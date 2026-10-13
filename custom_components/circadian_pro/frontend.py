"""Admin-only Circadian Pro panel and its temporary-preview WebSocket API."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any

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
from .const import DOMAIN, EXTRA_VALIDATION, NONE_STR, VALIDATION_TUPLES, _DOMAIN_SCHEMA, validate_cross_options

FRONTEND_DATA = "circadian_pro_frontend"
PANEL_PATH = "circadian-pro-calibration"
PANEL_URL = "/circadian_pro_static/calibration-panel.js"
_LOGGER = logging.getLogger(__name__)


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
    from .config_flow import validate_entry

    merged = _serializable_settings(validate_entry(entry))
    merged.update(deepcopy(draft))
    merged.pop("preview_light", None)
    # Optional time fields have no YAML default and cv.time cannot accept null.
    # Omit only fields which explicitly allow being unset, then put null back.
    optional = {name for name, default, _ in VALIDATION_TUPLES if default is None or default == NONE_STR}
    unset = {name for name in optional if merged.get(name) in (None, NONE_STR)}
    checked = _DOMAIN_SCHEMA({name: value for name, value in merged.items() if name not in unset})
    checked.update({name: None for name in unset})
    return validate_cross_options(checked)


@websocket_api.websocket_command({"type": "circadian_pro/calibration/entries"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_entries(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    from .config_flow import validate_entry

    entries = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        try:
            _loaded_entry(hass, entry.entry_id)
        except HomeAssistantError:
            continue
        entries.append({
            "entry_id": entry.entry_id, "title": entry.title,
            "settings": _serializable_settings(validate_entry(entry)),
        })
    connection.send_result(msg["id"], entries)


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/preview",
    ws_schema.Required("entry_id"): str,
    ws_schema.Required("settings"): dict,
    ws_schema.Required("kelvin"): ws_schema.All(int, ws_schema.In((1000, 4000))),
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
        await async_preview_calibration(hass, entry.entry_id, settings, msg["kelvin"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "preview_unavailable", str(err))
        return
    connection.send_result(msg["id"], {"active": True, "expires_in": PREVIEW_SECONDS})


@websocket_api.websocket_command({
    "type": "circadian_pro/calibration/stop", ws_schema.Required("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_stop(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "This Circadian Pro profile does not exist.")
        return
    # Idempotent cleanup remains available even if the profile was unloaded.
    await async_stop_calibration_preview(hass, entry.entry_id)
    connection.send_result(msg["id"], {"active": False})


def _read_translations(language: str) -> dict[str, Any]:
    path = Path(__file__).parent / "translations" / f"{language}.json"
    translations = json.loads(path.read_text(encoding="utf-8"))
    return {**translations.get("options", {}), "config": translations.get("config", {})}


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
            for handler in (websocket_entries, websocket_preview, websocket_stop, websocket_translations):
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
                )
            data["panel_registered"] = True
