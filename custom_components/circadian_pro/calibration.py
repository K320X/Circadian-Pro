"""Temporary, reversible previews of unsaved calibration settings."""

from __future__ import annotations

import asyncio
from copy import copy, deepcopy
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED, STATE_OFF, STATE_ON
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

PREVIEW_DATA = "circadian_pro_calibration_preview"
PREVIEW_SECONDS = 60
_LOGGER = logging.getLogger(__name__)
_COLOR_ATTRIBUTES = (
    "color_temp_kelvin", "rgbww_color", "rgbw_color", "rgb_color", "xy_color", "hs_color",
)
_MODE_ATTRIBUTES = {
    "color_temp": "color_temp_kelvin", "rgbww": "rgbww_color", "rgbw": "rgbw_color",
    "rgb": "rgb_color", "xy": "xy_color", "hs": "hs_color",
}


class CalibrationPreviewError(HomeAssistantError):
    """A preview cannot safely be started for the requested lights."""


@dataclass
class _LightPreview:
    state: str
    attributes: dict[str, Any]
    observed_state: str | None = None
    observed_attributes: dict[str, Any] | None = None
    expected_attributes: dict[str, Any] | None = None
    externally_changed: bool = False


@dataclass
class _Preview:
    switch: Any
    lights: dict[str, _LightPreview] = field(default_factory=dict)
    contexts: set[str] = field(default_factory=set)
    dispatching: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    cancel_timeout: Any = None
    remove_listener: Any = None


class _PreviewServices:
    """Wait for the real service handler before taking its resulting snapshot."""

    def __init__(self, hass: Any, preview: _Preview) -> None:
        self._hass = hass
        self._preview = preview

    async def async_call(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["blocking"] = True
        domain, service, data = args
        payload = dict(data)
        light = payload.get("entity_id")
        if domain == "light" and service == "turn_on" and light in self._preview.lights:
            # prepare_adaptation_data omits zero transitions. Send zero explicitly
            # so the device's default fade cannot outlive our preview snapshot.
            payload["transition"] = 0
            state = self._hass.states.get(light)
            if state is not None and "Solid" in state.attributes.get("effect_list", []):
                payload["effect"] = "Solid"
            saved = self._preview.lights[light]
            expected = saved.expected_attributes or _observed_attributes(state)
            expected = deepcopy(expected)
            for name in ("brightness", "effect"):
                if name in payload:
                    expected[name] = payload[name]
            for mode, attribute in _MODE_ATTRIBUTES.items():
                if attribute in payload:
                    for color_attribute in _COLOR_ATTRIBUTES:
                        expected.pop(color_attribute, None)
                    expected["color_mode"] = mode
                    expected[attribute] = payload[attribute]
                    break
            saved.expected_attributes = expected
            self._preview.switch.manager.last_service_data[light] = payload
        return await self._hass.services.async_call(domain, service, payload, **kwargs)


class _PreviewHass:
    def __init__(self, hass: Any, preview: _Preview) -> None:
        self._hass = hass
        self.services = _PreviewServices(hass, preview)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hass, name)


def is_calibration_preview_active(hass: Any, light: str) -> bool:
    """Pause normal adaptation only for lights reserved by an active preview."""
    return any(light in preview.lights for preview in hass.data.get(PREVIEW_DATA, {}).values())


def _observed_attributes(state: Any) -> dict[str, Any]:
    attributes = {name: state.attributes.get(name) for name in ("brightness", "effect", "color_mode")}
    native_attribute = _MODE_ATTRIBUTES.get(attributes["color_mode"])
    if native_attribute:
        value = state.attributes.get(native_attribute)
        attributes[native_attribute] = tuple(value) if isinstance(value, list) else value
    return deepcopy(attributes)


def _same_attributes(first: dict[str, Any] | None, second: dict[str, Any] | None) -> bool:
    """Compare native color only, with the two-byte RGBW/RGBWW quantization margin."""
    if first is None or second is None or first.keys() != second.keys():
        return False
    for name, value in first.items():
        other = second[name]
        if name in {"rgb_color", "rgbw_color", "rgbww_color"} and isinstance(value, (tuple, list)) and isinstance(other, (tuple, list)):
            if len(value) != len(other) or any(abs(a - b) > 2 for a, b in zip(value, other, strict=True)):
                return False
        elif value != other:
            return False
    return True


def _is_preview_context(preview: _Preview, context: Any) -> bool:
    return getattr(context, "id", None) in preview.contexts or getattr(context, "parent_id", None) in preview.contexts


def _matches_expected(preview: _Preview, light: str, state: Any, saved: _LightPreview) -> bool:
    """Recognize WLED's full-white RGBWW report as its equivalent native CCT."""
    actual = _observed_attributes(state)
    expected = saved.expected_attributes
    if _same_attributes(actual, expected):
        return True
    if (
        expected is None or expected.get("color_mode") != "rgbww"
        or actual.get("color_mode") != "color_temp"
        or any(actual.get(name) != expected.get(name) for name in ("brightness", "effect"))
    ):
        return False
    rgbww = expected.get("rgbww_color")
    if (
        not isinstance(rgbww, (tuple, list)) or len(rgbww) != 5
        or any(rgbww[:3]) or abs(rgbww[3] + rgbww[4] - 255) > 2
    ):
        return False
    # This alias is specific to WLED's representation and linear CCT mapping.
    # Other devices retain exact mode comparisons. Blend=0 is required because
    # additive/exclusive blending can make these white levels non-equivalent.
    from homeassistant.helpers import entity_registry as er

    hass = preview.switch.hass
    entity = er.async_get(hass).async_get(light)
    if entity is None or entity.platform != "wled":
        return False
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    coordinator = getattr(entry, "runtime_data", None)
    device = getattr(coordinator, "data", None)
    led_config = getattr(device, "led_config", None)
    if getattr(led_config, "cct_blend", None) != 0:
        return False
    min_kelvin = state.attributes.get("min_color_temp_kelvin") or 2000
    max_kelvin = state.attributes.get("max_color_temp_kelvin") or 6535
    kelvin = actual.get("color_temp_kelvin")
    if not isinstance(kelvin, (int, float)) or max_kelvin <= min_kelvin:
        return False
    native_kelvin = min_kelvin + rgbww[3] / (rgbww[3] + rgbww[4]) * (max_kelvin - min_kelvin)
    # A white byte can shift by two counts during the RGBWW round trip, and HA
    # floors its Kelvin report. Keep the same two-byte tolerance used above.
    return abs(kelvin - native_kelvin) <= 2 * (max_kelvin - min_kelvin) / 255 + 1


def _snapshot(state: Any) -> _LightPreview:
    return _LightPreview(state.state, deepcopy(dict(state.attributes)))


def _service_listener(hass: Any, preview: _Preview, event: Any) -> None:
    """An explicit external light command wins even if its values match ours."""
    if event.data.get("domain") != "light" or event.data.get("service") not in {
        "turn_on", "turn_off", "toggle",
    }:
        return
    if _is_preview_context(preview, event.context):
        return
    from .switch import _expand_light_groups  # Imported only after platforms are loaded.

    targets = event.data.get("service_data", {}).get("entity_id", [])
    if isinstance(targets, str):
        targets = [target.strip() for target in targets.split(",")]
    if not targets or "all" in targets:
        # Area/device targets are resolved by HA later; do not undo an ambiguous
        # external command while its individual targets are still unknown.
        for saved in preview.lights.values():
            saved.externally_changed = True
        return
    for light in _expand_light_groups(hass, list(targets)):
        if light in preview.lights:
            preview.lights[light].externally_changed = True


def _can_restore(hass: Any, light: str, saved: _LightPreview, preview: _Preview | None = None) -> bool:
    current = hass.states.get(light)
    return (
        not saved.externally_changed
        and current is not None
        and saved.observed_state is not None
        and (
            (
                current.state == saved.observed_state
                and _same_attributes(_observed_attributes(current), saved.observed_attributes)
            )
            or (
                preview is not None
                and current.state == STATE_ON
                and _is_preview_context(preview, getattr(current, "context", None))
                and _matches_expected(preview, light, current, saved)
            )
        )
    )


def _state_listener(preview: _Preview, event: Any) -> None:
    """Remember an external change even when it later returns to preview values."""
    light = event.data.get("entity_id")
    saved = preview.lights.get(light)
    state = event.data.get("new_state")
    if saved is None or saved.observed_state is None or light in preview.dispatching:
        return
    if (
        state is not None
        and state.state == STATE_ON
        and _is_preview_context(preview, event.context)
        and _matches_expected(preview, light, state, saved)
    ):
        saved.observed_state = state.state
        saved.observed_attributes = _observed_attributes(state)
        return
    if state is None or state.state != saved.observed_state or not _same_attributes(_observed_attributes(state), saved.observed_attributes):
        saved.externally_changed = True


def _restore_payload(light: str, saved: _LightPreview) -> dict[str, Any]:
    attributes = saved.attributes
    payload: dict[str, Any] = {"entity_id": light}
    if attributes.get("brightness") is not None:
        payload["brightness"] = attributes["brightness"]
    color_mode = attributes.get("color_mode")
    color_attribute = _MODE_ATTRIBUTES.get(color_mode)
    if color_attribute and attributes.get(color_attribute) is not None:
        payload[color_attribute] = attributes[color_attribute]
    if attributes.get("effect") is not None:
        payload["effect"] = attributes["effect"]
    return payload


async def _restore_light(hass: Any, preview: _Preview, light: str) -> None:
    saved = preview.lights[light]
    preview.switch.manager.reset(light, reset_manual_control=False)
    if not _can_restore(hass, light, saved, preview):
        return
    context = preview.switch.create_context("calibration")
    preview.contexts.add(context.id)
    if saved.state == STATE_OFF:
        await hass.services.async_call("light", "turn_off", {"entity_id": light, "transition": 0}, context=context, blocking=True)
    elif saved.state == STATE_ON:
        payload = _restore_payload(light, saved)
        payload["transition"] = 0
        await hass.services.async_call("light", "turn_on", payload, context=context, blocking=True)


async def _finish_preview(hass: Any, entry_id: str, preview: _Preview) -> None:
    """Restore while reservations remain active, then release every resource."""
    if preview.cancel_timeout is not None:
        preview.cancel_timeout()
        preview.cancel_timeout = None
    try:
        for light in list(preview.lights):
            try:
                await _restore_light(hass, preview, light)
            except Exception:  # One unavailable light must not retain other reservations.
                _LOGGER.exception("Could not restore calibration preview for %s", light)
    finally:
        preview.switch.manager.reset(*preview.lights, reset_manual_control=False)
        if preview.remove_listener is not None:
            preview.remove_listener()
        registry = hass.data.get(PREVIEW_DATA, {})
        if registry.get(entry_id) is preview:
            registry.pop(entry_id)
        if not registry:
            hass.data.pop(PREVIEW_DATA, None)


async def _stop_preview(hass: Any, entry_id: str, expected: _Preview | None = None, generation: int | None = None) -> None:
    preview = hass.data.get(PREVIEW_DATA, {}).get(entry_id)
    if preview is None or (expected is not None and preview is not expected):
        return
    async with preview.lock:
        if hass.data.get(PREVIEW_DATA, {}).get(entry_id) is not preview:
            return
        if generation is not None and preview.generation != generation:
            return
        await _finish_preview(hass, entry_id, preview)


async def async_stop_calibration_preview(hass: Any, entry_id: str) -> None:
    """Stop a preview on uncheck, continuation, expiry, or config-entry unload."""
    await _stop_preview(hass, entry_id)


async def async_preview_calibration(hass: Any, entry_id: str, settings_dict: dict[str, Any], kelvin: int) -> bool:
    """Apply an unsaved calibration draft for sixty seconds, without editing its entry."""
    from .switch import _expand_light_groups

    switch = hass.data.get(DOMAIN, {}).get(entry_id, {}).get("switch")
    if switch is None:
        raise CalibrationPreviewError("The Circadian Pro profile must be loaded to preview calibration.")
    settings = deepcopy(switch._current_settings)
    settings.update(deepcopy(settings_dict))
    settings.pop("preview_light", None)
    requested = settings.get("lights", switch.lights)
    if isinstance(requested, str):
        requested = [item.strip() for item in requested.split(",")]
    lights = _expand_light_groups(hass, list(requested))
    if not lights or any(hass.states.get(light) is None or hass.states.get(light).state not in {STATE_ON, STATE_OFF} for light in lights):
        raise CalibrationPreviewError("Select available lights before previewing calibration.")

    registry = hass.data.setdefault(PREVIEW_DATA, {})
    for other_id, other in registry.items():
        if other_id != entry_id and set(lights).intersection(other.lights):
            raise CalibrationPreviewError("Another Circadian Pro profile is already previewing one of these lights.")

    preview = registry.get(entry_id)
    if preview is None:
        preview = _Preview(switch)
        registry[entry_id] = preview

        @callback
        def on_service(event: Any) -> None:
            _service_listener(hass, preview, event)

        @callback
        def on_state(event: Any) -> None:
            _state_listener(preview, event)

        remove_service = hass.bus.async_listen(
            EVENT_CALL_SERVICE, on_service,
        )
        remove_state = hass.bus.async_listen(
            EVENT_STATE_CHANGED, on_state,
        )

        def remove_listeners() -> None:
            remove_service()
            remove_state()

        preview.remove_listener = remove_listeners
    async with preview.lock:
        if hass.data.get(PREVIEW_DATA, {}).get(entry_id) is not preview:
            raise CalibrationPreviewError("This calibration preview has ended; submit it again to start a new preview.")
        for other_id, other in registry.items():
            if other_id != entry_id and set(lights).intersection(other.lights):
                raise CalibrationPreviewError("Another Circadian Pro profile is already previewing one of these lights.")
        for light in list(preview.lights):
            if light not in lights:
                await _restore_light(hass, preview, light)
                preview.lights.pop(light)
        for light in lights:
            saved = preview.lights.get(light)
            if saved is None or saved.externally_changed or (saved.observed_state is not None and not _can_restore(hass, light, saved, preview)):
                # A newly submitted preview uses an intervening user's state as its baseline.
                preview.lights[light] = _snapshot(hass.states.get(light))
        switch.manager.reset(*lights, reset_manual_control=False)
        clone = copy(switch)
        clone.hass = _PreviewHass(hass, preview)
        clone._configured_lights = list(lights)
        clone.lights = list(lights)
        settings.update({
            "min_color_temp": kelvin, "max_color_temp": kelvin,
            "min_brightness": settings["max_brightness"], "sleep_activation_mode": "disabled",
            "transition_until_sleep": False, "sleep_schedule_enabled": False,
        })
        try:
            clone._set_changeable_settings(data=settings, defaults=deepcopy(switch._current_settings))
            clone._effective_sleep_state = lambda: False
            clone._skip_redundant_commands = False
            for light in lights:
                preview.lights[light].expected_attributes = None
                context = switch.create_context("calibration")
                preview.contexts.add(context.id)
                data = await clone.prepare_adaptation_data(
                    light, transition=0, adapt_color=True, adapt_brightness=True, force=True, context=context,
                )
                if data is None:
                    raise CalibrationPreviewError(f"The light {light} cannot preview brightness or color.")
                preview.dispatching.add(light)
                try:
                    await clone.execute_cancellable_adaptation_calls(data)
                finally:
                    state = hass.states.get(light)
                    if state is not None:
                        saved = preview.lights[light]
                        saved.observed_state = state.state
                        saved.observed_attributes = _observed_attributes(state)
                    preview.dispatching.discard(light)
        except BaseException:
            await _finish_preview(hass, entry_id, preview)
            raise
        preview.generation += 1
        if preview.cancel_timeout is not None:
            preview.cancel_timeout()
        generation = preview.generation

        @callback
        def expire(_now: Any) -> None:
            hass.async_create_task(_stop_preview(hass, entry_id, preview, generation))

        preview.cancel_timeout = async_call_later(hass, PREVIEW_SECONDS, expire)
    return True
