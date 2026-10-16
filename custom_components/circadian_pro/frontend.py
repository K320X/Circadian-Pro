"""Admin-only Circadian Pro panel and its temporary-preview WebSocket API."""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from itertools import pairwise
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import probatio as ws_schema
except ImportError:  # Older Home Assistant versions use voluptuous for WebSocket schemas.
    import voluptuous as ws_schema
import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from .calibration import PREVIEW_SECONDS, async_preview_calibration, async_stop_calibration_preview
from .const import DOMAIN, EXTRA_VALIDATION, NONE_STR, VALIDATION_TUPLES, _DOMAIN_SCHEMA, is_panel_entry, validate_cross_options

FRONTEND_DATA = "circadian_pro_frontend"
PANEL_PATH = "circadian-pro-calibration"
PANEL_URL = "/circadian_pro_static/calibration-panel.js"
_LEGACY_CALIBRATION_OPTIONS = {
    "rgb_calibration_r", "rgb_calibration_g", "rgb_calibration_b",
    "combine_rgbctt_or_rgbw", "combine_rgb_scale", "combine_white_scale",
    "rgbw_fixed_white_kelvin",
}
_LOGGER = logging.getLogger(__name__)
_SOLAR_TIME_OPTIONS = (
    "sunrise_time", "min_sunrise_time", "max_sunrise_time",
    "sunset_time", "min_sunset_time", "max_sunset_time",
)
_SOLAR_OFFSET_OPTIONS = ("sunrise_offset", "sunset_offset")
_SLEEP_TIME_OPTIONS = ("sleep_schedule_start", "sleep_schedule_end")
_SOLAR_DRAFT_OPTIONS = frozenset((*_SOLAR_TIME_OPTIONS, *_SOLAR_OFFSET_OPTIONS,
                                "sunrise_offset_minutes", "sunset_offset_minutes",
                                "sleep_activation_mode", *_SLEEP_TIME_OPTIONS))


def _panel_asset_registration(path: Path) -> tuple[str, str]:
    """Identify both the module and its element by the served JavaScript bytes."""
    fingerprint = sha256(path.read_bytes()).hexdigest()[:16]
    return (
        f"/circadian_pro_static/calibration-panel-{fingerprint}.js",
        f"{PANEL_PATH}-{fingerprint}",
    )


def _loaded_entry(hass: Any, entry_id: str) -> Any:
    entry = hass.config_entries.async_get_entry(entry_id)
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    if (
        entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED
        or not isinstance(data, dict) or data.get("switch") is None
    ):
        raise HomeAssistantError("This Circadian Pro profile is not loaded.")
    return entry


def _serializable_settings(settings: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(settings)
    data.pop("preview_light", None)
    for name, (_validate, serialize) in EXTRA_VALIDATION.items():
        if data.get(name) is not None:
            data[name] = serialize(data[name])
    return data


def _validated_draft(entry: Any, draft: dict[str, Any]) -> dict[str, Any]:
    from .config_flow import options_from_form, validate_entry

    merged = _serializable_settings(validate_entry(entry))
    ignored_legacy = _LEGACY_CALIBRATION_OPTIONS.intersection(merged)
    merged.update(options_from_form(deepcopy(draft), merged))
    merged.pop("preview_light", None)
    # Old profiles retain these inactive options for compatibility. They must
    # not make a temporary preview fail the current strict schema; newly
    # supplied unknown fields are still rejected below.
    for name in ignored_legacy:
        merged.pop(name, None)
    # Optional time fields have no YAML default and cv.time cannot accept null.
    # Omit only fields which explicitly allow being unset, then put null back.
    optional = {name for name, default, _ in VALIDATION_TUPLES if default is None or default == NONE_STR}
    unset = {name for name in optional if merged.get(name) in (None, NONE_STR)}
    checked = _DOMAIN_SCHEMA({name: value for name, value in merged.items() if name not in unset})
    checked.update({name: None for name in unset})
    return validate_cross_options(checked)


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/entries",
    ws_schema.Optional("include_status", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_entries(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    from .config_flow import validate_entry

    entries = []
    configured_profiles = 0
    for entry in hass.config_entries.async_entries(DOMAIN):
        if is_panel_entry(entry):
            continue
        configured_profiles += 1
        try:
            _loaded_entry(hass, entry.entry_id)
        except HomeAssistantError:
            continue
        created_at = getattr(entry, "created_at", None)
        modified_at = getattr(entry, "modified_at", None)
        entries.append({
            "entry_id": entry.entry_id, "title": entry.title,
            "created_at": created_at.isoformat() if created_at is not None else None,
            "modified_at": modified_at.isoformat() if modified_at is not None else None,
            "settings": _serializable_settings(validate_entry(entry)),
        })
    result = (
        {"entries": entries, "configured_profiles": configured_profiles}
        if msg.get("include_status", False) else entries
    )
    connection.send_result(msg["id"], result)


def _schedule_sleep_windows(
    start: float, end: float, zone: ZoneInfo, sleep_start: time, sleep_end: time,
) -> list[dict[str, float]]:
    """Resolve wall-clock membership, including both folds and skipped DST times.

    A local start/end can occur twice or not at all on a DST change. Offset
    changes are also boundaries: rolling the clock back can leave and re-enter
    a window even when neither configured endpoint occurs at the change itself.
    """
    if sleep_start == sleep_end:
        return [{"start": start, "end": end}]
    boundaries = {start, end}
    first_day = datetime.fromtimestamp(start, zone).date()
    last_day = datetime.fromtimestamp(end, zone).date()
    for days in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=days)
        for wall_time in (sleep_start, sleep_end):
            for fold in (0, 1):
                candidate = datetime.combine(day, wall_time, zone).replace(fold=fold)
                timestamp = candidate.timestamp()
                # Nonexistent local times must not be silently moved by the
                # time-zone conversion; the real offset change handles them.
                if (start < timestamp < end
                        and datetime.fromtimestamp(timestamp, zone).replace(tzinfo=None)
                        == candidate.replace(tzinfo=None)):
                    boundaries.add(timestamp)

    # This fixed five-local-day scan is bounded and runs in the executor. Locate
    # actual offset-change seconds instead of assuming DST changes by one hour.
    cursor = start
    while cursor < end:
        following = min(cursor + 3600, end)
        offset = datetime.fromtimestamp(cursor, zone).utcoffset()
        if offset != datetime.fromtimestamp(following, zone).utcoffset():
            low, high = int(cursor), int(following)
            while high - low > 1:
                middle = (low + high) // 2
                if datetime.fromtimestamp(middle, zone).utcoffset() == offset:
                    low = middle
                else:
                    high = middle
            boundaries.add(float(high))
        cursor = following

    windows: list[dict[str, float]] = []
    for left, right in pairwise(sorted(boundaries)):
        local_time = datetime.fromtimestamp((left + right) / 2, zone).time()
        active = (sleep_start <= local_time < sleep_end if sleep_start < sleep_end
                  else local_time >= sleep_start or local_time < sleep_end)
        if active:
            if windows and windows[-1]["end"] == left:
                windows[-1]["end"] = right
            else:
                windows.append({"start": left, "end": right})
    return windows


def _solar_context(
    sun: Any, now: datetime, timezone_name: str, sleep_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate the profile's real local day without changing its runtime state."""
    from .color_and_brightness import SunEventUnavailable

    today = now.astimezone(sun.timezone).date()
    start = datetime.combine(today, time(), sun.timezone).timestamp()
    end = datetime.combine(today + timedelta(days=1), time(), sun.timezone).timestamp()
    # Adjacent local dates bracket the full plot, including DST and the solar
    # midnight/noon boundaries used by the real brightness and color formulas.
    events = []
    for days in range(-2, 3):
        try:
            events.extend((event.value, timestamp) for event, timestamp
                          in sun.sun_events(today + timedelta(days=days)))
        except SunEventUnavailable:
            # A polar condition on an unused adjacent date does not invalidate
            # today if the remaining real events still bracket every sample.
            continue
    events.sort(key=lambda item: item[1])
    # Retain surrounding horizons for overnight windows and arithmetic midpoint
    # labels. The brightness formulas receive a cropped solar-noon/midnight
    # event sequence with enough surrounding history for a fade across midnight.
    horizons = [(event, timestamp) for event, timestamp in events if event in ("sunrise", "sunset")]
    midpoints = []
    solar_sleep_windows = []
    for left, right in pairwise(horizons):
        if left[0] == right[0] or left[1] >= right[1]:
            continue
        timestamp = (left[1] + right[1]) / 2
        if start <= timestamp < end:
            midpoints.append({
                "kind": "day" if left[0] == "sunrise" else "night", "timestamp": timestamp,
                "local_time": datetime.fromtimestamp(timestamp, sun.timezone).strftime("%H:%M"),
            })
        if left[0] == "sunset":
            solar_sleep_windows.append({"start": left[1], "end": right[1]})

    timestamps = [timestamp for _event, timestamp in events]
    # A night transition can begin before the plotted day. Its initial normal
    # target must use that earlier solar pair, not the first in-day value. Four
    # hours cover two consecutive maximum HA light transitions (6553 seconds
    # each), including an unfinished night entry interrupted by the exit.
    curve_start, curve_end = start - 4 * 3600, end + 4 * 3600
    first = max(0, bisect_right(timestamps, curve_start) - 1)
    last = bisect_right(timestamps, curve_end) + 1
    events = events[first:last]
    next_event = {"sunrise": "solar_noon", "solar_noon": "sunset",
                  "sunset": "solar_midnight", "solar_midnight": "sunrise"}
    if (
        not events or events[0][1] > curve_start or events[-1][1] <= curve_end
        or any(right[1] <= left[1] or next_event[left[0]] != right[0]
               for left, right in pairwise(events))
    ):
        raise ValueError("The solar events cannot represent this local day.")

    def label(event_name: str) -> dict[str, Any]:
        # Astral's local=False calculations may place the date argument's
        # event on the following local date near the international date line.
        # Select from the actual plotted day rather than calling Astral again.
        matches = [timestamp for event, timestamp in events
                   if event == event_name and start <= timestamp < end]
        if len(matches) != 1:
            raise ValueError("The solar events cannot identify this local day's sunrise and sunset.")
        timestamp = matches[0]
        return {"timestamp": timestamp,
                "local_time": datetime.fromtimestamp(timestamp, sun.timezone).strftime("%H:%M")}

    settings = sleep_settings or {}
    sleep_mode = settings.get("sleep_activation_mode", "disabled")
    sleep_all_day = False
    sleep_windows = []
    if sleep_mode == "sun":
        sleep_windows = solar_sleep_windows
    elif sleep_mode == "schedule":
        sleep_start, sleep_end = (settings[name] for name in _SLEEP_TIME_OPTIONS)
        sleep_all_day = sleep_start == sleep_end
        # Keep preceding windows intact so a transition that starts just before
        # midnight can carry into the plot. Nothing here changes runtime state.
        window_start = datetime.combine(today - timedelta(days=2), time(), sun.timezone).timestamp()
        window_end = datetime.combine(today + timedelta(days=3), time(), sun.timezone).timestamp()
        sleep_windows = _schedule_sleep_windows(window_start, window_end, sun.timezone, sleep_start, sleep_end)

    return {
        "local_date": today.isoformat(), "timezone": timezone_name,
        "day_start": start, "day_end": end, "refresh_at": end,
        "generated_at": now.timestamp(), "refresh_after": max(1, end - now.timestamp()),
        "events": [{"event": event, "timestamp": timestamp} for event, timestamp in events],
        "sunrise": label("sunrise"), "sunset": label("sunset"),
        "midpoints": midpoints, "sleep_mode": sleep_mode,
        "sleep_windows": sleep_windows, "sleep_all_day": sleep_all_day,
    }


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/solar",
    ws_schema.Required("entry_id"): str,
    ws_schema.Required("settings"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_solar(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    """Read solar and automatic-night schedules without saving or controlling lights."""
    try:
        entry = _loaded_entry(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_loaded", str(err))
        return
    try:
        if msg["settings"].keys() - _SOLAR_DRAFT_OPTIONS:
            raise vol.Invalid("Only solar and automatic-night schedule settings are accepted.")
        settings = _validated_draft(entry, msg["settings"])
        overrides = {}
        for name in (*_SOLAR_TIME_OPTIONS, *_SOLAR_OFFSET_OPTIONS):
            value = settings.get(name, 0 if name in _SOLAR_OFFSET_OPTIONS else None)
            overrides[name] = EXTRA_VALIDATION[name][0](value) if value is not None else None
        timezone_name = hass.config.time_zone
        overrides["timezone"] = ZoneInfo(timezone_name)
        sleep_settings = {"sleep_activation_mode": settings.get("sleep_activation_mode", "disabled")}
        if sleep_settings["sleep_activation_mode"] == "schedule":
            for name in _SLEEP_TIME_OPTIONS:
                sleep_settings[name] = EXTRA_VALIDATION[name][0](settings[name])
    except (vol.Invalid, ws_schema.Invalid, ValueError, TypeError, OverflowError) as err:
        connection.send_error(msg["id"], "invalid_options", str(err))
        return
    switch = hass.data[DOMAIN][entry.entry_id]["switch"]
    # Replace the frozen settings instead of mutating the live profile or its
    # cached SunEvents. Its Astral location already comes from HA configuration.
    sun = replace(switch._sun_light_settings.sun, **overrides)
    try:
        result = await hass.async_add_executor_job(
            _solar_context, sun, datetime.now(UTC), timezone_name, sleep_settings,
        )
    except (ValueError, OverflowError) as err:
        connection.send_error(msg["id"], "solar_unavailable", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/preview",
    ws_schema.Required("entry_id"): str,
    ws_schema.Required("settings"): dict,
    ws_schema.Required("kelvin"): ws_schema.All(int, ws_schema.In((1000, 4000))),
    ws_schema.Optional("session_id", default=""): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_preview(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    try:
        entry = _loaded_entry(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_loaded", str(err))
        return
    try:
        settings = _validated_draft(entry, msg["settings"])
    except (vol.Invalid, ws_schema.Invalid, ValueError, TypeError) as err:
        connection.send_error(msg["id"], "invalid_options", str(err))
        return
    try:
        await async_preview_calibration(
            hass, entry.entry_id, settings, msg["kelvin"], session_id=msg.get("session_id", ""),
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "preview_unavailable", str(err))
        return
    connection.send_result(msg["id"], {"active": True, "expires_in": PREVIEW_SECONDS})


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/stop", ws_schema.Required("entry_id"): str,
    ws_schema.Optional("session_id", default=""): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_stop(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "This Circadian Pro profile does not exist.")
        return
    # Idempotent cleanup remains available even if the profile was unloaded.
    await async_stop_calibration_preview(hass, entry.entry_id, session_id=msg.get("session_id", ""))
    connection.send_result(msg["id"], {"active": False})


def _read_translations(language: str) -> dict[str, Any]:
    path = Path(__file__).parent / "translations" / f"{language}.json"
    translations = json.loads(path.read_text(encoding="utf-8"))
    return {
        **translations.get("options", {}), "config": translations.get("config", {}),
        "selector": translations.get("selector", {}),
    }


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/translations",
    ws_schema.Optional("language", default="en"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_translations(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    language = "es" if msg.get("language", "en").lower().startswith("es") else "en"
    cache = hass.data.setdefault(FRONTEND_DATA, {}).setdefault("translations", {})
    if language not in cache:
        cache[language] = await hass.async_add_executor_job(_read_translations, language)
    connection.send_result(msg["id"], cache[language])


async def async_setup_frontend(hass: Any) -> None:
    """Register once, without replacing any user-owned dashboard or panel."""
    data = hass.data.setdefault(FRONTEND_DATA, {})
    lock = data.setdefault("lock", asyncio.Lock())
    async with lock:
        if not data.get("api_registered"):
            for handler in (websocket_entries, websocket_preview, websocket_stop, websocket_translations, websocket_solar):
                websocket_api.async_register_command(hass, handler)
            data["api_registered"] = True
        if not data.get("static_registered"):
            asset_path = Path(__file__).parent / "frontend" / "calibration-panel.js"
            module_url, element_name = await hass.async_add_executor_job(
                _panel_asset_registration, asset_path,
            )
            await hass.http.async_register_static_paths([
                StaticPathConfig(PANEL_URL, str(asset_path), False),
                StaticPathConfig(module_url, str(asset_path), False),
            ])
            data["module_url"] = module_url
            data["element_name"] = element_name
            data["static_registered"] = True
        if not data.get("panel_registered"):
            if PANEL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
                _LOGGER.warning("Keeping the existing %s panel; calibration API remains available", PANEL_PATH)
            else:
                await panel_custom.async_register_panel(
                    hass, frontend_url_path=PANEL_PATH, webcomponent_name=data["element_name"],
                    sidebar_title="Circadian Pro", sidebar_icon="mdi:theme-light-dark",
                    module_url=data["module_url"], require_admin=True,
                    config_panel_domain=DOMAIN,
                )
            data["panel_registered"] = True
