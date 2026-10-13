"""Switch for the Circadian Pro integration."""
# Modified for Circadian Pro. See NOTICE and LICENSE-APACHE-2.0.txt.

from __future__ import annotations

import asyncio
import datetime
import logging
import zoneinfo
from collections.abc import Mapping
from copy import deepcopy
from datetime import timedelta
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntityFeature,
    is_on,
    preprocess_turn_on_alternatives,
)
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    ATTR_SUPPORTED_FEATURES,
    CONF_NAME,
    CONF_PARAMS,
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    HomeAssistant,
    ServiceCall,
    State,
    callback,
)
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import entity_platform, entity_registry, target as target_helpers
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.sun import get_astral_location
from homeassistant.util import slugify
from homeassistant.util.color import (
    color_temperature_to_rgb,
    color_xy_to_RGB,
)

from .adaptation_utils import (
    BRIGHTNESS_ATTRS,
    AdaptationData,
    COLOR_ATTRS,
    LightControlAttributes,
    ServiceData,
    get_light_control_attributes,
    has_effect_attribute,
    manual_control_event_attribute_to_flags,
    prepare_adaptation_data,
)
from .calibration import is_calibration_preview_active, is_calibration_preview_context
from .color_and_brightness import SunLightSettings
from .const import (
    ADAPT_BRIGHTNESS_SWITCH,
    ADAPT_COLOR_SWITCH,
    ATTR_ADAPT_BRIGHTNESS,
    ATTR_ADAPT_COLOR,
    ATTR_CIRCADIAN_PRO_MANAGER,
    CONF_ADAPT_DELAY,
    CONF_ADAPT_ONLY_ON_BARE_TURN_ON,
    CONF_AUTORESET_CONTROL,
    CONF_BRIGHTNESS_MODE,
    CONF_BRIGHTNESS_MODE_TIME_DARK,
    CONF_BRIGHTNESS_MODE_TIME_LIGHT,
    CONF_CAL_RGB_NEUTRAL_R,
    CONF_CAL_RGB_NEUTRAL_G,
    CONF_CAL_RGB_NEUTRAL_B,
    CONF_CAL_RGB_WARM_R,
    CONF_CAL_RGB_WARM_G,
    CONF_CAL_RGB_WARM_B,
    CONF_CAL_WHITE_INTENSITY_NEUTRAL,
    CONF_CAL_WHITE_INTENSITY_WARM,
    CONF_CAL_WHITE_KELVIN_NEUTRAL,
    CONF_WHITE_CW_KELVIN,
    CONF_WHITE_W_KELVIN,
    CONF_DETECT_NON_HA_CHANGES,
    CONF_INCLUDE_CONFIG_IN_ATTRIBUTES,
    CONF_INITIAL_TRANSITION,
    CONF_INTERCEPT,
    CONF_INTERVAL,
    CONF_LIGHTS,
    CONF_MANUAL_CONTROL,
    CONF_MAX_BRIGHTNESS,
    CONF_MAX_COLOR_TEMP,
    CONF_MAX_SUNRISE_TIME,
    CONF_MAX_SUNSET_TIME,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_COLOR_TEMP,
    CONF_MIN_SUNRISE_TIME,
    CONF_MIN_SUNSET_TIME,
    CONF_MULTI_LIGHT_INTERCEPT,
    CONF_ONLY_ONCE,
    CONF_PREFER_RGB_COLOR,
    CONF_SEND_SPLIT_DELAY,
    CONF_SEPARATE_TURN_ON_COMMANDS,
    CONF_SKIP_REDUNDANT_COMMANDS,
    CONF_SLEEP_SCHEDULE_START,
    CONF_SLEEP_SCHEDULE_END,
    CONF_SLEEP_BRIGHTNESS,
    CONF_SLEEP_COLOR_TEMP,
    CONF_SLEEP_RGB_COLOR,
    CONF_SLEEP_RGB_OR_COLOR_TEMP,
    CONF_SLEEP_ACTIVATION_MODE,
    DEFAULT_SLEEP_ACTIVATION_MODE,
    CONF_SLEEP_TRANSITION,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_TIME,
    CONF_TAKE_OVER_CONTROL,
    CONF_TAKE_OVER_CONTROL_MODE,
    CONF_TRANSITION,
    CONF_TURN_ON_LIGHTS,
    CONF_USE_DEFAULTS,
    DOMAIN,
    EXTRA_VALIDATION,
    ICON_BRIGHTNESS,
    ICON_COLOR_TEMP,
    ICON_MAIN,
    ICON_SLEEP,
    SERVICE_APPLY,
    SERVICE_CHANGE_SWITCH_SETTINGS,
    SERVICE_SET_MANUAL_CONTROL,
    SET_MANUAL_CONTROL_SCHEMA,
    SLEEP_MODE_SWITCH,
    TURNING_OFF_DELAY,
    VALIDATION_TUPLES,
    TakeOverControlMode,
    apply_service_schema,
    replace_none_str,
    validate_cross_options,
)
from .hass_utils import ServiceCallPostProcessor, async_check_entity_control, setup_service_call_interceptor
from .helpers import (
    clamp,
    color_difference_redmean,
    int_to_base36,
    remove_vowels,
    short_hash,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.typing import NoEventData, VolDictType


_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=10)

# Consider it a significant change when attribute changes more than
BRIGHTNESS_CHANGE = 25  # ≈10% of total range
COLOR_TEMP_CHANGE = 100  # ≈3% of total range (2000-6500)
RGB_REDMEAN_CHANGE = 80  # ≈10% of total range
MAX_STATE_HISTORY = 8  # Keep the first and most recent reports of one command.


# Keep a short domain version for the context instances (which can only be 36 chars)
_DOMAIN_SHORT = "cp"


def create_context(
    name: str,
    which: str,
    index: int,
    parent: Context | None = None,
) -> Context:
    """Create a context that can identify this integration."""
    # Use a hash for the name because otherwise the context might become
    # too long (max len == 26) to fit in the database.
    # Pack index with base85 to maximize the number of contexts we can create
    # before we exceed the 26-character limit and are forced to wrap.
    # Use a short, time-based stamp without external dependencies.
    # We use milliseconds since epoch encoded in base36 to keep it compact.
    time_ms = int(dt_util.utcnow().timestamp() * 1000)
    time_stamp = int_to_base36(time_ms).zfill(10)[-10:]
    name_hash = short_hash(name)
    which_short = remove_vowels(which)
    context_id_start = f"{time_stamp}:{_DOMAIN_SHORT}:{name_hash}:{which_short}:"
    chars_left = 26 - len(context_id_start)
    index_packed = int_to_base36(index).zfill(chars_left)[-chars_left:]
    context_id = context_id_start + index_packed
    parent_id = parent.id if parent else None
    return Context(id=context_id, parent_id=parent_id, user_id=parent.user_id if parent else None)


def is_our_context_id(context_id: str | None, which: str | None = None) -> bool:
    """Check whether this integration created 'context_id'."""
    if context_id is None:
        return False

    is_ours = f":{_DOMAIN_SHORT}:" in context_id
    if not is_ours:
        return False
    if which is None:
        return True
    return f":{remove_vowels(which)}:" in context_id


def is_our_context(context: Context | None, which: str | None = None) -> bool:
    """Check whether this integration created 'context'."""
    if context is None:
        return False
    return is_our_context_id(context.id, which)


def _switches_with_lights(
    hass: HomeAssistant,
    lights: list[str],
    expand_light_groups: bool = True,
    *,
    refresh_lights: bool = True,
) -> CircadianProSwitches:
    """Get all switches that control at least one of the lights passed."""
    config_entries = hass.config_entries.async_entries(DOMAIN)
    data = hass.data.get(DOMAIN, {})
    switches: CircadianProSwitches = []
    all_check_lights = set(
        _expand_light_groups(hass, lights) if expand_light_groups else set(lights)
    )
    for config in config_entries:
        entry = data.get(config.entry_id)
        if entry is None:  # entry might be disabled and therefore missing
            continue
        switch = entry.get(SWITCH_DOMAIN)
        if switch is None:  # The platform may still be loading or unloading.
            continue
        if refresh_lights:
            switch._expand_light_groups(hass=hass)
            candidate_lights = switch.lights
        else:
            # Permission checks need a current target snapshot without changing
            # shared timers/tasks before the actor has been authorized.
            candidate_lights = _expand_light_groups(
                hass, getattr(switch, "_configured_lights", switch.lights),
            )
        # Check if any of the lights are in the switch's lights
        if all_check_lights.intersection(candidate_lights):
            switches.append(switch)
    return switches


class NoSwitchFoundError(ValueError):
    """No switches found for lights."""


def _switch_with_lights(
    hass: HomeAssistant,
    lights: list[str],
    expand_light_groups: bool = True,
    *,
    refresh_lights: bool = True,
) -> CircadianProSwitch:
    """Find the switch that controls the lights in 'lights'."""
    switches = _switches_with_lights(
        hass, lights, expand_light_groups, refresh_lights=refresh_lights,
    )
    if len(switches) == 1:
        return switches[0]
    if len(switches) > 1:
        on_switches = [s for s in switches if s.is_on]
        if len(on_switches) == 1:
            # Of the multiple switches, only one is on
            return on_switches[0]
        msg = (
            f"_switch_with_lights: Light(s) {lights} found in multiple switch configs"
            f" ({[s.entity_id for s in switches]}). You must pass a switch under"
            " 'entity_id'."
        )
        raise NoSwitchFoundError(msg)
    msg = (
        f"_switch_with_lights: Light(s) {lights} not found in any switch's"
        " configuration. You must either include the light(s) that is/are"
        " in the integration config, or pass a switch under 'entity_id'."
    )
    raise NoSwitchFoundError(msg)


# For documentation on this function, see integration_entities() from HomeAssistant Core:
# https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/template.py#L1109
def _switches_from_service_call(
    hass: HomeAssistant,
    service_call: ServiceCall,
    *,
    refresh_lights: bool = True,
) -> CircadianProSwitches:
    data = service_call.data
    lights = data[CONF_LIGHTS]
    switch_entity_ids: list[str] | None = data.get("entity_id")

    if not lights and not switch_entity_ids:
        msg = (
            "circadian-pro: Neither a switch nor a light was provided in the service call."
            " If you intend to adapt all lights on all switches, please report your"
            " use case to the Circadian Pro maintainers. Currently, you must pass"
            " either a circadian-pro switch or the lights to a circadian_pro service call."
        )
        raise ValueError(msg)

    if switch_entity_ids is not None:
        if len(switch_entity_ids) > 1 and lights:
            msg = (
                "circadian-pro: Cannot pass multiple switches with lights argument."
                f" Invalid service data received: {service_call.data}"
            )
            raise ValueError(msg)
        switches: CircadianProSwitches = []
        ent_reg = entity_registry.async_get(hass)
        for entity_id in switch_entity_ids:
            ent_entry = ent_reg.async_get(entity_id)
            config_id = ent_entry.config_entry_id if ent_entry is not None else None
            switch = hass.data.get(DOMAIN, {}).get(config_id, {}).get(SWITCH_DOMAIN)
            if switch is None or entity_id != switch.entity_id:
                raise ValueError(f"{entity_id} is not a loaded Circadian Pro main switch.")
            switches.append(switch)
        return switches

    if lights:
        switch = _switch_with_lights(hass, lights, refresh_lights=refresh_lights)
        return [switch]

    msg = (
        "circadian-pro: Incorrect data provided in service call."
        f" Entities not found in the integration. Service data: {service_call.data}"
    )
    raise ValueError(msg)


async def _authorized_switch_targets(
    hass: HomeAssistant,
    service_call: ServiceCall,
) -> list[tuple[CircadianProSwitch, list[str]]]:
    """Resolve every profile/light and check all access before changing any."""
    switches = _switches_from_service_call(hass, service_call, refresh_lights=False)
    requested_lights = service_call.data[CONF_LIGHTS]
    plans = []
    entities = list(service_call.data.get(ATTR_ENTITY_ID, [])) + list(requested_lights)
    for switch in switches:
        lights = _expand_light_groups(
            hass,
            requested_lights or getattr(switch, "_configured_lights", switch.lights),
        )
        entities.append(switch.entity_id)
        entities.extend(lights)
        plans.append((switch, lights))
    await async_check_entity_control(hass, service_call.context, entities)
    return plans


async def handle_change_switch_settings(
    switch: CircadianProSwitch,
    service_call: ServiceCall,
) -> None:
    """Allows HASS to change config values via a service call."""
    if not isinstance(switch, CircadianProSwitch):
        raise ValueError("Select the main Circadian Pro switch to change profile settings.")
    await async_check_entity_control(
        switch.hass, service_call.context, [switch.entity_id, *switch.lights],
    )
    data = service_call.data
    which = data.get(CONF_USE_DEFAULTS, "current")
    if which == "current":  # use whatever we're already using.
        defaults = switch._current_settings  # pylint: disable=protected-access
    elif which == "factory":  # use actual defaults listed in the documentation
        defaults = None
    elif which == "configuration":
        # use whatever's in the config flow or configuration.yaml
        defaults = switch._config_backup
    else:
        raise ValueError(f"Unknown use_defaults value: {which}")

    # deep copy the defaults so we don't modify the original dicts
    switch._set_changeable_settings(data=data, defaults=deepcopy(defaults))
    if switch.is_on:
        switch._update_time_interval_listener()

    _LOGGER.debug(
        "Called 'circadian_pro.change_switch_settings' service with '%s'",
        data,
    )

    switch.manager.reset(*switch.lights, reset_manual_control=False)
    if switch.is_on:
        await switch._update_attrs_and_maybe_adapt_lights(  # pylint: disable=protected-access
            context=switch.create_context("service", parent=service_call.context),
            lights=switch.lights,
            transition=switch.initial_transition,
            force=True,
        )


async def async_setup_entry(  # noqa: PLR0915
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the CircadianPro switch."""
    assert hass is not None
    data = hass.data[DOMAIN]
    assert config_entry.entry_id in data
    _LOGGER.debug(
        "Setting up CircadianPro with data: %s and config_entry %s",
        data,
        config_entry,
    )
    if (  # Skip deleted YAML config entries or first time YAML config entries
        config_entry.source == SOURCE_IMPORT
        and config_entry.unique_id not in data.get("__yaml__", set())
    ):
        _LOGGER.warning(
            "Deleting CircadianPro switch '%s' because YAML"
            " defined switch has been removed from YAML configuration",
            config_entry.unique_id,
        )
        await hass.config_entries.async_remove(config_entry.entry_id)
        return

    if (manager := data.get(ATTR_CIRCADIAN_PRO_MANAGER)) is None:
        manager = CircadianProManager(hass)
        data[ATTR_CIRCADIAN_PRO_MANAGER] = manager

    sleep_mode_switch = SimpleSwitch(
        which="Sleep Mode",
        initial_state=False,
        hass=hass,
        config_entry=config_entry,
        icon=ICON_SLEEP,
    )
    adapt_color_switch = SimpleSwitch(
        which="Adapt Color",
        initial_state=True,
        hass=hass,
        config_entry=config_entry,
        icon=ICON_COLOR_TEMP,
    )
    adapt_brightness_switch = SimpleSwitch(
        which="Adapt Brightness",
        initial_state=True,
        hass=hass,
        config_entry=config_entry,
        icon=ICON_BRIGHTNESS,
    )
    switch = CircadianProSwitch(
        hass,
        config_entry,
        manager,
        sleep_mode_switch,
        adapt_color_switch,
        adapt_brightness_switch,
    )

    data[config_entry.entry_id][SLEEP_MODE_SWITCH] = sleep_mode_switch
    data[config_entry.entry_id][ADAPT_COLOR_SWITCH] = adapt_color_switch
    data[config_entry.entry_id][ADAPT_BRIGHTNESS_SWITCH] = adapt_brightness_switch
    data[config_entry.entry_id][SWITCH_DOMAIN] = switch

    async_add_entities(
        [sleep_mode_switch, adapt_color_switch, adapt_brightness_switch, switch],
        update_before_add=True,
    )

    @callback
    async def handle_apply(service_call: ServiceCall) -> None:
        """Handle the entity service apply."""
        data = service_call.data
        _LOGGER.debug(
            "Called 'circadian_pro.apply' service with '%s'",
            data,
        )
        plans = await _authorized_switch_targets(hass, service_call)
        for switch, all_lights in plans:
            for light in all_lights:
                if data[CONF_TURN_ON_LIGHTS] or is_on(hass, light):
                    context = switch.create_context(
                        "service",
                        parent=service_call.context,
                    )
                    await switch._adapt_light(  # pylint: disable=protected-access
                        light,
                        context=context,
                        transition=data[CONF_TRANSITION],
                        adapt_brightness=data[ATTR_ADAPT_BRIGHTNESS],
                        adapt_color=data[ATTR_ADAPT_COLOR],
                        prefer_rgb_color=data[CONF_PREFER_RGB_COLOR],
                        force=True,
                    )

    @callback
    async def handle_set_manual_control(service_call: ServiceCall) -> None:
        """Set or unset lights as 'manually controlled'."""
        data = service_call.data
        _LOGGER.debug(
            "Called 'circadian_pro.set_manual_control' service with '%s'",
            data,
        )
        plans = await _authorized_switch_targets(hass, service_call)
        for switch, all_lights in plans:

            manual_attributes = manual_control_event_attribute_to_flags(
                service_call.data[CONF_MANUAL_CONTROL],
            )

            if manual_attributes:
                for light in all_lights:
                    switch.manager.set_manual_control_attributes(
                        light,
                        manual_attributes,
                    )
                    switch.fire_manual_control_event(
                        light,
                        service_call.context,
                    )
            else:
                switch.manager.reset(*all_lights)
                if switch.is_on:
                    context = switch.create_context(
                        "service",
                        parent=service_call.context,
                    )
                    # pylint: disable=protected-access
                    await switch._update_attrs_and_maybe_adapt_lights(
                        context=context,
                        lights=all_lights,
                        transition=switch.initial_transition,
                        force=True,
                    )

    # Register apply service
    hass.services.async_register(
        domain=DOMAIN,
        service=SERVICE_APPLY,
        service_func=handle_apply,
        schema=apply_service_schema(switch.initial_transition),
    )

    # Register set_manual_control service
    hass.services.async_register(
        domain=DOMAIN,
        service=SERVICE_SET_MANUAL_CONTROL,
        service_func=handle_set_manual_control,
        schema=SET_MANUAL_CONTROL_SCHEMA,
    )

    args: VolDictType = {
        vol.Optional(CONF_USE_DEFAULTS, default="current"): vol.In(
            ("current", "factory", "configuration"),
        ),
    }
    # Modifying these after init isn't possible
    skip = (CONF_INTERVAL, CONF_NAME, CONF_LIGHTS)
    for k, _, valid in VALIDATION_TUPLES:
        if k not in skip:
            args[vol.Optional(k)] = valid
    platform = entity_platform.current_platform.get()
    assert platform is not None
    platform.async_register_entity_service(
        SERVICE_CHANGE_SWITCH_SETTINGS,
        args,
        handle_change_switch_settings,
    )
    data[config_entry.entry_id]["platform_setup_complete"] = True


def validate(
    config_entry: ConfigEntry | None,
    service_data: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Get the options and data from the config_entry and add defaults."""
    if defaults is None:
        data = {key: default for key, default, _ in VALIDATION_TUPLES}
    else:
        data = deepcopy(defaults)

    if config_entry is not None:
        assert service_data is None
        assert defaults is None
        data.update(config_entry.options)  # come from options flow
        data.update(config_entry.data)  # all yaml settings come from data
    else:
        assert service_data is not None
        changed_settings = {
            key: value
            for key, value in service_data.items()
            if key not in (CONF_USE_DEFAULTS, ATTR_ENTITY_ID)
        }
        data.update(changed_settings)
    data = {key: replace_none_str(value) for key, value in data.items()}
    for key, (validate_value, _) in EXTRA_VALIDATION.items():
        value = data.get(key)
        if value is not None:
            data[key] = validate_value(value)  # Fix the types of the inputs

    validate_cross_options(data)

    # Circadian Pro: use a single transition value for both normal adaptation
    # and sleep-mode transitions to avoid confusing behavior across different
    # light integrations.
    try:
        data[CONF_SLEEP_TRANSITION] = float(data.get(CONF_TRANSITION, 0) or 0)
    except Exception:  # noqa: BLE001
        data[CONF_SLEEP_TRANSITION] = 0.0
    return data


def _is_state_event(
    event: Event[EventStateChangedData],
    from_or_to_state: Iterable[str],
) -> bool:
    """Match state event when either 'from_state' or 'to_state' matches."""
    return (
        (old_state := event.data.get("old_state")) is not None
        and old_state.state in from_or_to_state
    ) or (
        (new_state := event.data.get("new_state")) is not None
        and new_state.state in from_or_to_state
    )


def _expand_light_groups(
    hass: HomeAssistant,
    lights: list[str],
) -> list[str]:
    """Resolve nested groups once per entity, keeping native Hue groups intact."""
    all_lights: set[str] = set()
    visited: set[str] = set()
    pending = list(lights)
    while pending:
        light = pending.pop()
        if light in visited:
            continue
        visited.add(light)
        state = hass.states.get(light)
        if state is None:
            _LOGGER.debug("State of %s is None", light)
            all_lights.add(light)
        elif _is_light_group(state):
            group = state.attributes["entity_id"]
            pending.extend(group)
            _LOGGER.debug("Expanded %s to %s", light, group)
        else:
            all_lights.add(light)
    return sorted(all_lights)


def _service_parameters(service_data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read normalized HA light parameters while accepting older flat events."""
    parameters = service_data.get(CONF_PARAMS)
    return parameters if isinstance(parameters, Mapping) else service_data


def _is_light_group(state: State) -> bool:
    return "entity_id" in state.attributes and not state.attributes.get(
        "is_hue_group",
        False,
    )


def _supported_features(hass: HomeAssistant, light: str) -> set[str]:
    state = hass.states.get(light)
    assert state is not None
    supported_features = int(
        state.attributes.get(ATTR_SUPPORTED_FEATURES, 0),
    )  # type: ignore[arg-type]
    assert isinstance(supported_features, int)

    supported: set[str] = set()

    if supported_features & LightEntityFeature.TRANSITION:
        supported.add("transition")

    supported_color_modes = state.attributes.get(
        ATTR_SUPPORTED_COLOR_MODES,
        set(),
    )  # type: ignore[arg-type]
    color_modes = {
        ColorMode.RGB,
        ColorMode.RGBW,
        ColorMode.RGBWW,
        ColorMode.XY,
        ColorMode.HS,
    }

    # Adding brightness when color mode is supported, see
    # (reference removed)

    for mode in color_modes:
        if mode in supported_color_modes:
            supported.update({"color", "brightness"})
            break

    if ColorMode.COLOR_TEMP in supported_color_modes:
        supported.update({"color_temp", "brightness"})

    if ColorMode.BRIGHTNESS in supported_color_modes:
        supported.add("brightness")

    return supported


# --- RGBW / RGBWW combined mode helpers -------------------------------------

def _clamp_int(value: float | int, min_v: int, max_v: int) -> int:
    """Clamp and coerce a value to an int."""
    return max(min_v, min(max_v, int(round(value))))


def _scale_byte(value: int, scale_percent: int) -> int:
    """Scale a channel value by a percentage and clamp to 0-255."""
    return _clamp_int(value * (scale_percent / 100.0), 0, 255)


def _scale_rgb(rgb: tuple[int, int, int], scale_percent: int) -> tuple[int, int, int]:
    return tuple(_scale_byte(c, scale_percent) for c in rgb)  # type: ignore[return-value]


def _kelvin_to_rgb_with_neutral(
    kelvin: int,
    neutral_kelvin: int,
    neutral_rgb: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Convert Kelvin to RGB, using a user-defined neutral RGB as the base.

    This is designed for strips where full RGB white is not truly neutral. The user
    can tune neutral_rgb so that (neutral_r, neutral_g, neutral_b) looks neutral.

    We then compute per-channel ratios relative to neutral_kelvin and apply those
    ratios to neutral_rgb.
    """
    k = _clamp_int(kelvin, 1000, 10000)
    nk = _clamp_int(neutral_kelvin, 1000, 10000)
    tr, tg, tb = color_temperature_to_rgb(k)
    nr, ng, nb = color_temperature_to_rgb(nk)

    def ratio(t: float, n: float) -> float:
        if n <= 0.0:
            return 0.0
        return max(0.0, min(4.0, t / n))

    r = _clamp_int(neutral_rgb[0] * ratio(tr, nr), 0, 255)
    g = _clamp_int(neutral_rgb[1] * ratio(tg, ng), 0, 255)
    b = _clamp_int(neutral_rgb[2] * ratio(tb, nb), 0, 255)
    return (r, g, b)


def _rgbww_from_kelvin(
    kelvin: int,
    min_kelvin: int,
    max_kelvin: int,
    neutral_kelvin: int,
    rgb_scale: int,
    white_scale: int,
    neutral_r: int,
    neutral_g: int,
    neutral_b: int,
) -> tuple[int, int, int, int, int]:
    """Compute an rgbww_color tuple (r,g,b,cw,ww) for a target Kelvin.

    - CW/WW are mixed linearly between min_kelvin (warm) and max_kelvin (cold).
    - RGB is computed from Kelvin as well and can be scaled independently.
    """
    min_k = max(1, min(min_kelvin, max_kelvin))
    max_k = max(min_k + 1, max(min_kelvin, max_kelvin))
    k = _clamp_int(kelvin, min_k, max_k)

    # White channels (0-255) before scaling
    frac_cold = (k - min_k) / (max_k - min_k)
    frac_cold = max(0.0, min(1.0, frac_cold))
    white_level = _scale_byte(255, white_scale)
    cw = _clamp_int(white_level * frac_cold, 0, 255)
    ww = _clamp_int(white_level * (1.0 - frac_cold), 0, 255)

    rgb = _kelvin_to_rgb_with_neutral(
        k,
        neutral_kelvin=neutral_kelvin,
        neutral_rgb=(neutral_r, neutral_g, neutral_b),
    )
    rgb = _scale_rgb(rgb, rgb_scale)
    return (rgb[0], rgb[1], rgb[2], cw, ww)


def _rgbw_from_kelvin(
    kelvin: int,
    fixed_white_kelvin: int,
    rgb_scale: int,
    white_scale: int,
    neutral_r: int,
    neutral_g: int,
    neutral_b: int,
) -> tuple[int, int, int, int]:
    """Compute an rgbw_color tuple (r,g,b,w) for a target Kelvin.

    Strategy:
    1) Convert target Kelvin -> RGB (calibrated)
    2) Convert fixed W Kelvin -> RGB (calibrated)
    3) Use the W channel as a 'base' in the direction of the fixed white and let RGB
       provide the residual. This keeps colors reasonable while still using both paths.
    """
    k = _clamp_int(kelvin, 1000, 10000)
    w_k = _clamp_int(fixed_white_kelvin, 1000, 10000)

    # RGB path: compute relative to the fixed W Kelvin, using the user-defined
    # neutral RGB as the base.
    rgb = _kelvin_to_rgb_with_neutral(
        k,
        neutral_kelvin=w_k,
        neutral_rgb=(neutral_r, neutral_g, neutral_b),
    )
    rgb = _scale_rgb(rgb, rgb_scale)

    # W path: strongest at the fixed W Kelvin, gradually weaker towards the ends.
    # This matches the intended "use W as the base and tint via RGB" behavior.
    dist = abs(k - w_k)
    span = max(abs(1000 - w_k), abs(10000 - w_k), 1)
    w_strength = max(0.0, min(1.0, 1.0 - (dist / span)))
    w_val = _scale_byte(int(round(255 * w_strength)), white_scale)

    return (rgb[0], rgb[1], rgb[2], w_val)


# --- Circadian Pro calibration helpers ---

def _interp_two_point_pct(
    kelvin: int,
    warm_pct: int,
    neutral_pct: int,
    warm_kelvin: int = 1000,
    neutral_kelvin: int = 4000,
) -> int:
    """Interpolate (or extrapolate) a percentage between two Kelvin reference points.

    Values are clamped to 0..100.
    """
    if neutral_kelvin == warm_kelvin:
        return int(_clamp_int(neutral_pct, 0, 100))
    t = (kelvin - warm_kelvin) / (neutral_kelvin - warm_kelvin)
    val = warm_pct + t * (neutral_pct - warm_pct)
    return int(_clamp_int(round(val), 0, 100))


def _interp_white_intensity_rgbw(
    kelvin: int,
    warm_pct: int,
    neutral_pct: int,
    white_w_kelvin: int,
    warm_kelvin: int = 1000,
    neutral_kelvin: int = 4000,
) -> int:
    """Interpolate white intensity for RGBW strips with a fixed W LED temperature.

    Behavior:
    - From neutral_kelvin down to white_w_kelvin: keep neutral_pct (plateau).
    - From white_w_kelvin down to warm_kelvin: fade linearly to warm_pct.
    - Below warm_kelvin / above neutral_kelvin: extrapolate by clamping to endpoints.

    Output is clamped to 0..100.
    """
    # Sanitize pivot so it sits between warm and neutral.
    pivot = int(_clamp_int(white_w_kelvin, min(warm_kelvin, neutral_kelvin), max(warm_kelvin, neutral_kelvin)))

    if kelvin >= pivot:
        return int(_clamp_int(neutral_pct, 0, 100))
    if kelvin <= warm_kelvin:
        return int(_clamp_int(warm_pct, 0, 100))

    # Avoid division by zero if pivot == warm_kelvin.
    if pivot == warm_kelvin:
        return int(_clamp_int(neutral_pct, 0, 100))

    t = (kelvin - warm_kelvin) / (pivot - warm_kelvin)
    val = warm_pct + t * (neutral_pct - warm_pct)
    return int(_clamp_int(round(val), 0, 100))


def _calibrated_white_channels(
    kelvin: int,
    white_intensity_pct: int,
    warm_kelvin: int,
    cold_kelvin: int,
    neutral_white_kelvin: int | None = None,
) -> tuple[int, int]:
    """Mix CW/WW in mired, keeping RGB warmth independent of physical whites."""
    warm = max(1, int(warm_kelvin))
    cold = max(warm + 1, int(cold_kelvin))
    white_kelvin = float(clamp(kelvin, warm, cold))
    if neutral_white_kelvin is not None:
        neutral = clamp(neutral_white_kelvin, warm, cold)
        # The calibration reference can lie outside the LEDs' physical range.
        # It must still reproduce the chosen mixture exactly at 4000 K.
        warm_anchor = warm if warm < 4000 else 1000
        cold_anchor = cold if cold > 4000 else 10000
        if kelvin <= 4000:
            fraction = clamp((kelvin - warm_anchor) / (4000 - warm_anchor), 0, 1)
            white_kelvin = warm + fraction * (neutral - warm)
        else:
            fraction = clamp((kelvin - 4000) / (cold_anchor - 4000), 0, 1)
            white_kelvin = neutral + fraction * (cold - neutral)
    cold_fraction = (1 / warm - 1 / white_kelvin) / (1 / warm - 1 / cold)
    white_level = _clamp_int(255 * white_intensity_pct / 100, 0, 255)
    cold_level = _clamp_int(white_level * cold_fraction, 0, white_level)
    return cold_level, white_level - cold_level


def _calibrated_rgb_from_kelvin(
    kelvin: int,
    cal_warm: tuple[int, int, int],
    cal_neutral: tuple[int, int, int],
    warm_kelvin: int = 1000,
    neutral_kelvin: int = 4000,
) -> tuple[int, int, int]:
    """Convert Kelvin -> RGB and correct it so it matches two user calibrations.

    The correction is an affine transform per channel: out = a*base + b, computed
    so that:
      - out(warm_kelvin)    == cal_warm
      - out(neutral_kelvin) == cal_neutral
    """
    k = _clamp_int(kelvin, 1000, 10000)
    base = color_temperature_to_rgb(k)
    base_warm = color_temperature_to_rgb(_clamp_int(warm_kelvin, 1000, 10000))
    base_neutral = color_temperature_to_rgb(_clamp_int(neutral_kelvin, 1000, 10000))

    out: list[int] = [0, 0, 0]
    for i, (bw, bn, cw, cn, b) in enumerate(
        zip(base_warm, base_neutral, cal_warm, cal_neutral, base, strict=False)
    ):
        # Avoid division by zero if the base points are identical for a channel.
        if bn == bw:
            fraction = (
                clamp((k - warm_kelvin) / (neutral_kelvin - warm_kelvin), 0, 1)
                if neutral_kelvin != warm_kelvin else 1
            )
            corrected = cw + fraction * (cn - cw)
        else:
            a = (cn - cw) / (bn - bw)
            b0 = cw - a * bw
            corrected = a * b + b0
        out[i] = int(_clamp_int(round(corrected), 0, 255))
    return (out[0], out[1], out[2])


# Legacy color representations are compared through RGB; native channel vectors
# are compared directly so their independent white channels remain observable.
def _convert_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    # Keep the last command intact; derived RGB must not overwrite native channels.
    attributes = dict(attributes)
    if attributes.get(ATTR_RGB_COLOR) is not None:
        return attributes

    rgb = None
    if (color := attributes.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
        rgb = color_temperature_to_rgb(color)
    elif (color := attributes.get(ATTR_XY_COLOR)) is not None:
        rgb = color_xy_to_RGB(*color)

    if rgb is not None:
        attributes[ATTR_RGB_COLOR] = rgb
        _LOGGER.debug("Converted attributes %s to rgb %s", attributes, rgb)
    else:
        _LOGGER.debug("No suitable color conversion found for %s", attributes)

    return attributes


def _add_missing_attributes(
    old_attributes: dict[str, Any],
    new_attributes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not any(
        attr in old_attributes and attr in new_attributes
        for attr in [ATTR_COLOR_TEMP_KELVIN, ATTR_RGB_COLOR]
    ):
        old_attributes = _convert_attributes(old_attributes)
        new_attributes = _convert_attributes(new_attributes)

    return old_attributes, new_attributes


def _has_color_mode_changed(
    light: str,
    old_attributes: dict[str, Any],
    new_attributes: dict[str, Any],
    context: Context,
) -> bool:
    """Check if the light's color mode changed (e.g., color_temp to RGB or vice versa).

    This must be called BEFORE _add_missing_attributes() to detect mode changes
    using the original attributes. See issue #1275.
    """

    def color_mode(attributes: dict[str, Any]) -> str | None:
        # States may expose RGB/temperature derived from their native vector.
        # Service data has no color_mode, so infer it from the requested attribute.
        if (mode := attributes.get("color_mode")) is not None:
            return mode
        for attribute, mode in (
            (ATTR_RGBWW_COLOR, ColorMode.RGBWW),
            (ATTR_RGBW_COLOR, ColorMode.RGBW),
            (ATTR_COLOR_TEMP_KELVIN, ColorMode.COLOR_TEMP),
            (ATTR_RGB_COLOR, ColorMode.RGB),
            (ATTR_XY_COLOR, ColorMode.XY),
        ):
            if attributes.get(attribute) is not None:
                return mode
        return None

    old_mode = color_mode(old_attributes)
    new_mode = color_mode(new_attributes)

    # Check if mode changed
    if old_mode is not None and new_mode is not None and old_mode != new_mode:
        _LOGGER.debug(
            "Light mode of %s changed from %s to %s with context.id='%s'",
            light,
            old_mode,
            new_mode,
            context.id,
        )
        return True
    return False


def _attributes_have_changed(
    light: str,
    old_attributes: dict[str, Any],
    new_attributes: dict[str, Any],
    context: Context,
) -> LightControlAttributes:
    # 2023-11-19: HA core no longer removes light domain attributes when off
    # so we must protect for None here
    # see https://github.com/home-assistant/core/pull/101946

    changed_attributes = LightControlAttributes.NONE
    native_compared = False

    # Check for color mode changes BEFORE attribute conversion
    # This detects external changes like Hue scenes switching from color_temp to RGB
    # (reference removed)
    if _has_color_mode_changed(
        light,
        old_attributes,
        new_attributes,
        context,
    ):
        changed_attributes |= LightControlAttributes.COLOR

    if LightControlAttributes.COLOR not in changed_attributes:
        for attribute, channel_count in ((ATTR_RGBWW_COLOR, 5), (ATTR_RGBW_COLOR, 4)):
            old_color = old_attributes.get(attribute)
            new_color = new_attributes.get(attribute)
            if (
                isinstance(old_color, (tuple, list))
                and isinstance(new_color, (tuple, list))
                and len(old_color) == len(new_color) == channel_count
            ):
                native_compared = True
                # WLED quantizes the white split during its transport round trip.
                if any(abs(new - old) > 2 for old, new in zip(old_color, new_color, strict=True)):
                    changed_attributes |= LightControlAttributes.COLOR
                break

    if LightControlAttributes.COLOR not in changed_attributes and not native_compared:
        old_attributes, new_attributes = _add_missing_attributes(
            old_attributes,
            new_attributes,
        )

    if old_attributes.get(ATTR_BRIGHTNESS) and new_attributes.get(ATTR_BRIGHTNESS):
        last_brightness = old_attributes[ATTR_BRIGHTNESS]
        current_brightness = new_attributes[ATTR_BRIGHTNESS]
        if abs(current_brightness - last_brightness) > BRIGHTNESS_CHANGE:
            _LOGGER.debug(
                "Brightness of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_brightness,
                current_brightness,
                context.id,
            )
            changed_attributes |= LightControlAttributes.BRIGHTNESS

    if (
        LightControlAttributes.COLOR not in changed_attributes
        and not native_compared
        and old_attributes.get(ATTR_COLOR_TEMP_KELVIN)
        and new_attributes.get(ATTR_COLOR_TEMP_KELVIN)
    ):
        last_color_temp = old_attributes[ATTR_COLOR_TEMP_KELVIN]
        current_color_temp = new_attributes[ATTR_COLOR_TEMP_KELVIN]
        if abs(current_color_temp - last_color_temp) > COLOR_TEMP_CHANGE:
            _LOGGER.debug(
                "Color temperature of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_color_temp,
                current_color_temp,
                context.id,
            )
            changed_attributes |= LightControlAttributes.COLOR

    if (
        LightControlAttributes.COLOR not in changed_attributes
        and not native_compared
        and old_attributes.get(ATTR_RGB_COLOR)
        and new_attributes.get(ATTR_RGB_COLOR)
    ):
        last_rgb_color = old_attributes[ATTR_RGB_COLOR]
        current_rgb_color = new_attributes[ATTR_RGB_COLOR]
        redmean_change = color_difference_redmean(last_rgb_color, current_rgb_color)
        if redmean_change > RGB_REDMEAN_CHANGE:
            _LOGGER.debug(
                "color RGB of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_rgb_color,
                current_rgb_color,
                context.id,
            )
            changed_attributes |= LightControlAttributes.COLOR

    return changed_attributes


class CircadianProSwitch(SwitchEntity, RestoreEntity):
    """Representation of a Circadian Pro switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        manager: CircadianProManager,
        sleep_mode_switch: SimpleSwitch,
        adapt_color_switch: SimpleSwitch,
        adapt_brightness_switch: SimpleSwitch,
    ) -> None:
        """Initialize the Circadian Pro switch."""
        # Set attributes that can't be modified during runtime
        assert hass is not None
        self.hass = hass
        self.manager = manager
        self.sleep_mode_switch = sleep_mode_switch
        self.adapt_color_switch = adapt_color_switch
        self.adapt_brightness_switch = adapt_brightness_switch

        data = validate(config_entry)

        self._name = data[CONF_NAME]
        self._interval: timedelta = data[CONF_INTERVAL]
        self._configured_lights: list[str] = list(data[CONF_LIGHTS])
        self.lights: list[str] = list(self._configured_lights)

        # backup data for use in change_switch_settings "configuration" CONF_USE_DEFAULTS
        self._config_backup = deepcopy(data)
        self._set_changeable_settings(data=data, defaults=None)

        # Set other attributes
        self._icon = ICON_MAIN
        self._state: bool | None = None

        # To count the number of Context instances
        self._context_cnt: int = 0

        # Suggest a stable entity_id like `switch.circadian_pro_<name>`.
        object_id = f"{DOMAIN}_{slugify(self._name)}"
        self.entity_id = async_generate_entity_id("switch.{}", object_id, hass=hass)

        # Set in self._update_attrs_and_maybe_adapt_lights
        self._settings: dict[str, Any] = {}

        # Set and unset tracker in async_turn_on and async_turn_off
        self.remove_listeners: list[CALLBACK_TYPE] = []
        self.remove_interval: CALLBACK_TYPE = lambda: None
        self.async_on_remove(self._remove_listeners)
        _LOGGER.debug(
            "%s: Setting up with '%s',"
            " config_entry.data: '%s',"
            " config_entry.options: '%s', converted to '%s'.",
            self._name,
            self.lights,
            config_entry.data,
            config_entry.options,
            data,
        )

    def _set_changeable_settings(
        self,
        data: dict[str, Any],
        defaults: dict[str, Any] | None = None,
    ) -> None:
        # Only pass settings users can change during runtime
        data = validate(
            config_entry=None,
            service_data=data,
            defaults=defaults,
        )

        # backup data for use in change_switch_settings "current" CONF_USE_DEFAULTS
        self._current_settings = data

        self._detect_non_ha_changes = data[CONF_DETECT_NON_HA_CHANGES]
        self._include_config_in_attributes = data[CONF_INCLUDE_CONFIG_IN_ATTRIBUTES]
        self._config: dict[str, Any] = {}
        if self._include_config_in_attributes:
            attrdata = deepcopy(data)
            for k, v in attrdata.items():
                if isinstance(v, datetime.date | datetime.datetime):
                    attrdata[k] = v.isoformat()
                elif isinstance(v, datetime.timedelta):
                    attrdata[k] = v.total_seconds()
            self._config.update(attrdata)

        self.initial_transition = data[CONF_INITIAL_TRANSITION]
        self._sleep_transition = data[CONF_SLEEP_TRANSITION]
        self._only_once = data[CONF_ONLY_ONCE]
        self._prefer_rgb_color = data[CONF_PREFER_RGB_COLOR]
        # Circadian Pro: perfil fijo RGBW (auto-detect de CCT si la entidad soporta color_temp)
        self._cal_rgb_neutral: list[int] = [
            data[CONF_CAL_RGB_NEUTRAL_R],
            data[CONF_CAL_RGB_NEUTRAL_G],
            data[CONF_CAL_RGB_NEUTRAL_B],
        ]
        self._cal_rgb_warm: list[int] = [
            data[CONF_CAL_RGB_WARM_R],
            data[CONF_CAL_RGB_WARM_G],
            data[CONF_CAL_RGB_WARM_B],
        ]
        self._cal_white_intensity_neutral: int = data[CONF_CAL_WHITE_INTENSITY_NEUTRAL]
        self._cal_white_intensity_warm: int = data[CONF_CAL_WHITE_INTENSITY_WARM]
        self._white_w_kelvin: int = data[CONF_WHITE_W_KELVIN]
        self._white_cw_kelvin: int | None = data.get(CONF_WHITE_CW_KELVIN)
        self._cal_white_kelvin_neutral: int | None = data.get(CONF_CAL_WHITE_KELVIN_NEUTRAL)
        self._separate_turn_on_commands = data[CONF_SEPARATE_TURN_ON_COMMANDS]
        self._transition: float = data[CONF_TRANSITION]
        self._adapt_delay = data[CONF_ADAPT_DELAY]
        self._send_split_delay = data[CONF_SEND_SPLIT_DELAY]
        self._take_over_control = data[CONF_TAKE_OVER_CONTROL]
        if not data[CONF_TAKE_OVER_CONTROL] and (
            data[CONF_DETECT_NON_HA_CHANGES] or data[CONF_ADAPT_ONLY_ON_BARE_TURN_ON]
        ):
            _LOGGER.warning(
                "%s: Config mismatch: detect_non_ha_changes or adapt_only_on_bare_turn_on "
                "set to true requires take_over_control to be enabled. Adjusting config "
                "and continuing setup with take_over_control: true.",
                self._name,
            )
            self._take_over_control = True
        self._take_over_control_mode = TakeOverControlMode(
            data[CONF_TAKE_OVER_CONTROL_MODE],
        )
        self._detect_non_ha_changes = data[CONF_DETECT_NON_HA_CHANGES]
        self._adapt_only_on_bare_turn_on = data[CONF_ADAPT_ONLY_ON_BARE_TURN_ON]
        self._auto_reset_manual_control_time = data[CONF_AUTORESET_CONTROL]
        self._skip_redundant_commands = data[CONF_SKIP_REDUNDANT_COMMANDS]
        self._intercept = data[CONF_INTERCEPT]
        self._multi_light_intercept = data[CONF_MULTI_LIGHT_INTERCEPT]
        if not data[CONF_INTERCEPT] and data[CONF_MULTI_LIGHT_INTERCEPT]:
            _LOGGER.warning(
                "%s: Config mismatch: multi_light_intercept set to true requires intercept"
                " to be enabled. Adjusting config and continuing setup with"
                " multi_light_intercept: false.",
                self._name,
            )
            self._multi_light_intercept = False
        self._expand_light_groups(rebuild=True)  # updates manual control timers
        location, _ = get_astral_location(self.hass)

        self._sleep_activation_mode = str(
            data.get(CONF_SLEEP_ACTIVATION_MODE, DEFAULT_SLEEP_ACTIVATION_MODE)
        )

        self._sun_light_settings = SunLightSettings(
            name=self._name,
            astral_location=location,
            adapt_until_sleep=(self._sleep_activation_mode == "sun"),
            max_brightness=data[CONF_MAX_BRIGHTNESS],
            max_color_temp=data[CONF_MAX_COLOR_TEMP],
            min_brightness=data[CONF_MIN_BRIGHTNESS],
            min_color_temp=data[CONF_MIN_COLOR_TEMP],
            sleep_brightness=data[CONF_SLEEP_BRIGHTNESS],
            sleep_color_temp=data[CONF_SLEEP_COLOR_TEMP],
            sleep_rgb_color=data[CONF_SLEEP_RGB_COLOR],
            sleep_rgb_or_color_temp=data[CONF_SLEEP_RGB_OR_COLOR_TEMP],
            sunrise_offset=data[CONF_SUNRISE_OFFSET],
            sunrise_time=data[CONF_SUNRISE_TIME],
            min_sunrise_time=data[CONF_MIN_SUNRISE_TIME],
            max_sunrise_time=data[CONF_MAX_SUNRISE_TIME],
            sunset_offset=data[CONF_SUNSET_OFFSET],
            sunset_time=data[CONF_SUNSET_TIME],
            min_sunset_time=data[CONF_MIN_SUNSET_TIME],
            max_sunset_time=data[CONF_MAX_SUNSET_TIME],
            brightness_mode=data[CONF_BRIGHTNESS_MODE],
            brightness_mode_time_dark=data[CONF_BRIGHTNESS_MODE_TIME_DARK],
            brightness_mode_time_light=data[CONF_BRIGHTNESS_MODE_TIME_LIGHT],
            timezone=zoneinfo.ZoneInfo(self.hass.config.time_zone),
        )

        # Fixed sleep window (only used when sleep_activation_mode == "schedule").
        self._sleep_schedule_start = data.get(CONF_SLEEP_SCHEDULE_START)
        self._sleep_schedule_end = data.get(CONF_SLEEP_SCHEDULE_END)
        _LOGGER.debug(
            "%s: Set switch settings for lights '%s'. now using data: '%s'",
            self._name,
            self.lights,
            data,
        )

    @property
    def name(self) -> str:
        """Return the name of the device if any."""
        return f"Circadian Pro: {self._name}"

    @property
    def unique_id(self) -> str:
        """Return the unique ID of entity."""
        return self._name

    @property
    def is_on(self) -> bool | None:
        """Return true if circadian pro is on."""
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info, used to group this and adjacent entities in the UI."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self._name),
            },
            name=self._name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        self.manager.register_switch(self)
        self.async_on_remove(lambda: self.manager.unregister_switch(self))
        if self.hass.is_running:
            await self._setup_listeners()
        else:
            remove_start_listener = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._setup_listeners,
            )
            self.async_on_remove(remove_start_listener)
        last_state: State | None = await self.async_get_last_state()
        is_new_entry = last_state is None  # newly added to HA
        if is_new_entry or last_state.state == STATE_ON:  # type: ignore[union-attr]
            await self.async_turn_on(adapt_lights=not self._only_once)
        else:
            self._state = False
            assert not self.remove_listeners

    def _expand_light_groups(
        self, hass: HomeAssistant | None = None, *, rebuild: bool = False,
    ) -> None:
        hass = hass or self.hass
        all_lights = _expand_light_groups(hass, self._configured_lights)
        changed = self.lights != all_lights
        self.lights = all_lights
        if (changed or rebuild) and self in self.manager.switches:
            self.manager.rebuild_lights()

    async def _setup_listeners(self, _: Event[NoEventData] | None = None) -> None:
        _LOGGER.debug("%s: Called '_setup_listeners'", self._name)
        if not self.is_on or not self.hass.is_running:
            _LOGGER.debug("%s: Cancelled '_setup_listeners'", self._name)
            return

        if self.remove_listeners:
            _LOGGER.debug("%s: Listeners already active", self._name)
            return

        self._update_time_interval_listener()

        remove_sleep = async_track_state_change_event(
            self.hass,
            entity_ids=self.sleep_mode_switch.entity_id,
            action=self._sleep_mode_switch_state_event_action,
        )

        self.remove_listeners.append(remove_sleep)
        self._expand_light_groups()

    def _update_time_interval_listener(self) -> None:
        """Create or recreate the adaptation interval listener.

        Recreation is necessary when the configuration has changed (e.g., send_split_delay).
        """
        self._remove_interval_listener()

        # An adaptation takes a little longer than its nominal duration due processing overhead,
        # so we factor this in to avoid overlapping adaptations. Since this is a constant value,
        # it might not cover all cases, but if large enough, it covers most.
        # Ideally, the interval and adaptation are a coupled process where a finished adaptation
        # triggers the next, but that requires a larger architectural change.
        processing_overhead_time = 0.5

        adaptation_interval = (
            self._interval
            + timedelta(milliseconds=self._send_split_delay)
            + timedelta(seconds=processing_overhead_time)
        )

        self.remove_interval = async_track_time_interval(
            self.hass,
            action=self._async_update_at_interval_action,
            interval=adaptation_interval,
        )

    def _remove_interval_listener(self) -> None:
        self.remove_interval()
        self.remove_interval = lambda: None

    def _remove_listeners(self) -> None:
        self._remove_interval_listener()

        while self.remove_listeners:
            remove_listener = self.remove_listeners.pop()
            remove_listener()

    @property
    def icon(self) -> str:
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes of the switch."""
        extra_state_attributes: dict[str, Any] = {"configuration": self._config}
        if not self.is_on:
            for key in self._settings:
                extra_state_attributes[key] = None
            return extra_state_attributes
        extra_state_attributes["manual_control"] = [
            light for light in self.lights if self.manager.manual_control.get(light)
        ]
        extra_state_attributes.update(self._settings)
        timers = self.manager.auto_reset_manual_control_timers
        extra_state_attributes["autoreset_time_remaining"] = {
            light: time
            for light in self.lights
            if (timer := timers.get(light)) and (time := timer.remaining_time()) > 0
        }
        return extra_state_attributes

    def create_context(
        self,
        which: str = "default",
        parent: Context | None = None,
    ) -> Context:
        """Create a context that identifies this Circadian Pro instance."""
        context = create_context(self._name, which, self._context_cnt, parent=parent)
        self._context_cnt += 1
        return context

    async def async_turn_on(  # type: ignore[override]
        self,
        adapt_lights: bool = True,
    ) -> None:
        """Turn on circadian pro."""
        _LOGGER.debug(
            "%s: Called 'async_turn_on', current state is '%s'",
            self._name,
            self._state,
        )
        if self.is_on:
            return
        self._state = True
        self.manager.reset(*self.lights)
        await self._setup_listeners()
        if adapt_lights:
            await self._update_attrs_and_maybe_adapt_lights(
                context=self.create_context("turn_on"),
                transition=self.initial_transition,
                force=True,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn off circadian pro."""
        if not self.is_on:
            return
        self._state = False
        self._remove_listeners()
        self.manager.reset(*self.lights)

    async def _async_update_at_interval_action(
        self,
        now: Any = None,  # noqa: ARG002
    ) -> None:
        """Update the attributes and maybe adapt the lights."""
        await self._update_attrs_and_maybe_adapt_lights(
            context=self.create_context("interval"),
            transition=self._transition,
            force=False,
        )

    def _within_sleep_window(self) -> bool:
        """True if local time is within the configured sleep window.

        Supports windows that cross midnight (e.g. 23:30 -> 07:15).
        Returns False if the window is not configured.
        """
        start = getattr(self, "_sleep_schedule_start", None)
        end = getattr(self, "_sleep_schedule_end", None)
        if not start or not end:
            return False
        now_local = dt_util.now(zoneinfo.ZoneInfo(self.hass.config.time_zone)).time()
        if start < end:
            return start <= now_local < end
        # Cross-midnight window
        return now_local >= start or now_local < end

    def _effective_sleep_state(self) -> bool:
        """Effective sleep state.

        Priority:
        1) Manual sleep switch
        2) Automatic sleep activation (selected in options)

        Automatic modes:
        - disabled: never auto-enable sleep mode
        - sun: sleep mode when the sun is below the horizon
        - schedule: sleep mode only within the configured time window

        This behavior is intentionally "hard sleep" (sleep values are applied as targets).
        """
        # 1) Manual
        if self.sleep_mode_switch.is_on:
            return True

        mode = getattr(self, "_sleep_activation_mode", DEFAULT_SLEEP_ACTIVATION_MODE)

        if mode == "schedule":
            return self._within_sleep_window()

        if mode == "sun":
            now = dt_util.now(zoneinfo.ZoneInfo(self.hass.config.time_zone))
            try:
                return self._sun_light_settings.sun.sun_position(now) < 0
            except Exception:  # pragma: no cover
                return False

        return False


    async def prepare_adaptation_data(
        self,
        light: str,
        transition: float | None = None,
        adapt_brightness: bool | None = None,
        adapt_color: bool | None = None,
        prefer_rgb_color: bool | None = None,
        force: bool = False,
        context: Context | None = None,
        *,
        settings: dict[str, Any] | None = None,
        effective_sleep: bool | None = None,
    ) -> AdaptationData | None:
        """Prepare AdaptationData for adapting a light."""
        state = self.hass.states.get(light)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        adaptation_attributes = self.manager.get_adaption_control_attributes(
            self,
            light,
        )

        if transition is None:
            transition = self._transition
        if adapt_brightness is None:
            adapt_brightness = (
                LightControlAttributes.BRIGHTNESS in adaptation_attributes
            )
        if adapt_color is None:
            adapt_color = LightControlAttributes.COLOR in adaptation_attributes
        if prefer_rgb_color is None:
            prefer_rgb_color = self._prefer_rgb_color

        if not adapt_color and not adapt_brightness:
            _LOGGER.debug(
                "%s: Skipping adaptation of %s because both adapt_brightness and"
                " adapt_color are False",
                self._name,
                light,
            )
            return None

        # The switch might be off and not have _settings set.
        if effective_sleep is None:
            effective_sleep = self._effective_sleep_state()
        if settings is None:
            settings = self._sun_light_settings.get_settings(effective_sleep, transition)
        self._settings = dict(settings)

        # Build service data.
        service_data: dict[str, Any] = {ATTR_ENTITY_ID: light}
        features = _supported_features(self.hass, light)

        # Check transition == 0 to fix #378
        # Always send transition if the user configured it. Some integrations
        # (notably WLED) don't reliably report transition support flags, but
        # they still respect the transition parameter when sent.
        use_transition = transition > 0
        if use_transition:
            service_data[ATTR_TRANSITION] = transition

        if "brightness" in features and adapt_brightness:
            brightness = round(255 * self._settings["brightness_pct"] / 100)
            service_data[ATTR_BRIGHTNESS] = brightness

        sleep_rgb = (
            effective_sleep
            and self._sun_light_settings.sleep_rgb_or_color_temp == "rgb_color"
        )

        # Circadian Pro: mapeo RGBW calibrado (4000K/1000K) + canal W con Kelvin real
        #
        # - Si 'Modo noche' está en 'Definir con RGB' (o estamos transicionando hacia él),
        #   se fuerza W=0 y se usa el RGB configurado.
        # - En el resto de casos, calculamos RGB calibrado (dos puntos) + W según intensidad
        #   y con 'meseta' hasta el Kelvin real del canal W.
        if adapt_color:
            supported_color_modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES, set())
            min_kelvin = state.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN) or 1000
            max_kelvin = state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN) or 10000

            force_rgb = bool(self._settings.get("force_rgb_color", False))

            if sleep_rgb or force_rgb:
                # RGB puro en modo noche (o transición hacia él): W siempre a 0
                rgb = tuple(int(x) for x in self._settings.get("rgb_color", [255, 56, 0]))  # type: ignore[arg-type]
                if ColorMode.RGBWW in supported_color_modes:
                    service_data[ATTR_RGBWW_COLOR] = (rgb[0], rgb[1], rgb[2], 0, 0)
                elif ColorMode.RGBW in supported_color_modes:
                    service_data[ATTR_RGBW_COLOR] = (rgb[0], rgb[1], rgb[2], 0)
                elif ColorMode.RGB in supported_color_modes or "color" in features:
                    service_data[ATTR_RGB_COLOR] = rgb
                elif "color_temp" in features:
                    # A white-only light cannot display RGB; keep its sleep
                    # temperature inside the native range instead of dropping color.
                    service_data[ATTR_COLOR_TEMP_KELVIN] = _clamp_int(
                        self._settings["color_temp_kelvin"], min_kelvin, max_kelvin,
                    )

            else:
                target_kelvin = int(self._settings.get("color_temp_kelvin", 4000))
                target_kelvin = clamp(target_kelvin, 1000, 10000)

                # RGB calibrado (0-255 cada canal) usando 2 puntos: 1000K y 4000K
                cal_rgb = _calibrated_rgb_from_kelvin(
                    target_kelvin,
                    cal_warm=tuple(self._cal_rgb_warm),
                    cal_neutral=tuple(self._cal_rgb_neutral),
                )

                # Intensidad de blanco (0-100) con 'meseta' hasta el Kelvin real del canal W
                white_intensity_pct = _interp_white_intensity_rgbw(
                    target_kelvin,
                    warm_pct=self._cal_white_intensity_warm,
                    neutral_pct=self._cal_white_intensity_neutral,
                    white_w_kelvin=self._white_w_kelvin,
                )

                if ColorMode.RGBWW in supported_color_modes:
                    cold_kelvin = (
                        self._white_cw_kelvin
                        or state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN)
                        or 6500
                    )
                    cw, ww = _calibrated_white_channels(
                        target_kelvin,
                        white_intensity_pct,
                        self._white_w_kelvin,
                        cold_kelvin,
                        self._cal_white_kelvin_neutral,
                    )
                    service_data[ATTR_RGBWW_COLOR] = (*cal_rgb, cw, ww)
                elif ColorMode.RGBW in supported_color_modes:
                    w = round(255 * (white_intensity_pct / 100.0))
                    service_data[ATTR_RGBW_COLOR] = (cal_rgb[0], cal_rgb[1], cal_rgb[2], w)
                elif "color_temp" in features and (
                    "color" not in features
                    or (not prefer_rgb_color and min_kelvin <= target_kelvin <= max_kelvin)
                ):
                    # Keep the sent target within the light's native range so
                    # rejected commands cannot look like external manual changes.
                    service_data[ATTR_COLOR_TEMP_KELVIN] = _clamp_int(
                        target_kelvin, min_kelvin, max_kelvin,
                    )
                elif "color" in features:
                    service_data[ATTR_RGB_COLOR] = cal_rgb

        required_attrs = [
            ATTR_RGB_COLOR,
            ATTR_COLOR_TEMP_KELVIN,
            ATTR_RGBW_COLOR,
            ATTR_RGBWW_COLOR,
            ATTR_BRIGHTNESS,
        ]
        if not any(attr in service_data for attr in required_attrs):
            _LOGGER.debug(
                "%s: Skipping adaptation of %s because no relevant attributes"
                " are set in service_data: %s",
                self._name,
                light,
                service_data,
            )
            return None

        context = context or self.create_context("adapt_lights")

        return prepare_adaptation_data(
            self.hass,
            light,
            context,
            transition if use_transition else 0,
            self._send_split_delay / 1000.0,
            service_data,
            split=self._separate_turn_on_commands,
            filter_by_state=self._skip_redundant_commands,
            force=force,
        )

    async def _adapt_light(
        self,
        light: str,
        context: Context,
        transition: float | None = None,
        adapt_brightness: bool | None = None,
        adapt_color: bool | None = None,
        prefer_rgb_color: bool | None = None,
        force: bool = False,
        *,
        settings: dict[str, Any] | None = None,
        effective_sleep: bool | None = None,
    ) -> None:
        if (lock := self.manager.turn_off_locks.get(light)) and lock.locked():
            _LOGGER.debug("%s: '%s' is locked", self._name, light)
            return

        data = await self.prepare_adaptation_data(
            light,
            transition,
            adapt_brightness,
            adapt_color,
            prefer_rgb_color,
            force,
            context,
            settings=settings,
            effective_sleep=effective_sleep,
        )
        if data is None:
            return  # nothing to adapt

        await self.execute_cancellable_adaptation_calls(data)

    async def _execute_adaptation_calls(self, data: AdaptationData) -> None:
        """Executes a sequence of adaptation service calls for the given service datas."""
        previous_context = self.manager.last_service_context.get(data.entity_id)
        seed_contexts = {data.context.id, getattr(data.context, "parent_id", None)}
        expected_data = dict(self.manager.last_service_data.get(data.entity_id, {})) if previous_context is not None and previous_context in seed_contexts else {}
        for index in range(data.max_length):
            is_first_call = index == 0

            # Sleep between multiple service calls.
            if not is_first_call or data.initial_sleep:
                await asyncio.sleep(data.sleep_time)

            calibration = is_our_context(data.context, "calibration")
            explicit_apply = is_our_context(data.context, "service")
            if getattr(self.manager, "_disabled", False):
                return
            registered = getattr(self.manager, "switches", None)
            if registered is not None and self not in registered and not calibration:
                return

            if not self.is_on and not (explicit_apply or calibration):
                return  # Only an explicit apply/preview may control an off profile.

            # A preview may reserve the light while this sequence is sleeping.
            # Calibration itself uses the same executor and must remain allowed.
            if is_calibration_preview_active(self.hass, data.entity_id) and not calibration:
                return

            # Instead of directly iterating the generator in the while-loop, we get
            # the next item here after the sleep to make sure it incorporates state
            # changes which happened during the sleep.
            service_data = await data.next_service_call_data()

            if not service_data:
                # All service datas processed
                break

            await async_check_entity_control(self.hass, data.context, [data.entity_id])

            if not (explicit_apply or calibration):
                allowed = self.manager.get_adaption_control_attributes(self, data.entity_id)
                service_data = {
                    key: value for key, value in service_data.items()
                    if (key not in COLOR_ATTRS or LightControlAttributes.COLOR in allowed)
                    and (key not in BRIGHTNESS_ATTRS or LightControlAttributes.BRIGHTNESS in allowed)
                }
                if not any(key in COLOR_ATTRS or key in BRIGHTNESS_ATTRS for key in service_data):
                    return

            if (
                not data.force
                and not is_on(self.hass, data.entity_id)
                # if proactively adapting, we are sure that it came from a light.turn_on
                and not self.manager.is_proactively_adapting(data.context.id, data.entity_id)
            ):
                # Do a last-minute check if the entity is still on.
                _LOGGER.debug(
                    "%s: Skipping adaptation of %s because it is now off",
                    self._name,
                    data.entity_id,
                )
                return

            _LOGGER.debug(
                "%s: Scheduling 'light.turn_on' with the following 'service_data': %s"
                " with context.id='%s'",
                self._name,
                service_data,
                data.context.id,
            )
            light = service_data[ATTR_ENTITY_ID]
            if any(key in COLOR_ATTRS for key in service_data):
                for key in COLOR_ATTRS:
                    expected_data.pop(key, None)
            expected_data.update(service_data)
            previous_data = self.manager.last_service_data.get(light)
            previous_context = self.manager.last_service_context.get(light)
            acknowledgement = self.manager.last_service_acknowledgements.get(light)
            previous_rgbww = (previous_data or {}).get(ATTR_RGBWW_COLOR)
            if (
                isinstance(previous_rgbww, (tuple, list))
                and len(previous_rgbww) == 5
                and not any(previous_rgbww[:3])
                and any(previous_rgbww[3:])
            ):
                own_reports = [
                    state
                    for state in self.manager.our_last_state_on_change.get(light, [])
                    if state.context.id == previous_context
                    and state.attributes.get("color_mode") == ColorMode.COLOR_TEMP
                    and state.attributes.get(ATTR_COLOR_TEMP_KELVIN) is not None
                ]
                if own_reports:
                    previous_values = {
                        key: tuple(value)
                        if key in (ATTR_RGBW_COLOR, ATTR_RGBWW_COLOR)
                        else value
                        for key, value in previous_data.items()
                        if key not in (ATTR_ENTITY_ID, ATTR_TRANSITION)
                    }
                    acknowledgement = (previous_values, own_reports[-1])
            requested_values = {
                key: tuple(value)
                if key in (ATTR_RGBW_COLOR, ATTR_RGBWW_COLOR)
                else value
                for key, value in expected_data.items()
                if key not in (ATTR_ENTITY_ID, ATTR_TRANSITION)
            }
            color_values = {key: value for key, value in requested_values.items() if key in COLOR_ATTRS}
            acknowledged_color = {key: value for key, value in (acknowledgement[0] if acknowledgement else {}).items() if key in COLOR_ATTRS}
            if acknowledgement is not None and color_values and acknowledged_color == color_values:
                # Identical commands may produce no state_changed event. Keep
                # their confirmed native-white normalization, even with a new
                # context or transition, until the requested light values change.
                self.manager.last_service_acknowledgements[light] = (requested_values, acknowledgement[1])
            elif color_values:
                self.manager.last_service_acknowledgements.pop(light, None)
            elif acknowledgement is not None:
                self.manager.last_service_acknowledgements[light] = acknowledgement
            context = self.create_context("split", parent=data.context) if not is_first_call or data.initial_sleep else data.context
            if context is not data.context:
                context.user_id = getattr(data.context, "user_id", None)
            self.manager.last_service_data[light] = dict(expected_data)
            self.manager.last_service_context[light] = context.id
            try:
                await self.hass.services.async_call(
                    LIGHT_DOMAIN,
                    SERVICE_TURN_ON,
                    service_data,
                    context=context,
                    blocking=True,
                )
            except BaseException:
                if self.manager.last_service_context.get(light) == context.id:
                    # The device may have rejected or only partly applied this
                    # command. It is not a reliable manual-change baseline.
                    self.manager.last_service_data.pop(light, None)
                    self.manager.last_service_context.pop(light, None)
                    self.manager.last_service_acknowledgements.pop(light, None)
                    self.manager.our_last_state_on_change.pop(light, None)
                    self.manager.clear_proactively_adapting(light)
                raise

    async def execute_cancellable_adaptation_calls(
        self,
        data: AdaptationData,
    ) -> None:
        """Executes a cancellable sequence of adaptation service calls for the given service datas.

        Wraps the sequence of service calls in a task that can be cancelled from elsewhere, e.g.,
        to cancel an ongoing adaptation when a light is turned off.
        """
        # Prevent overlap of multiple adaptation sequences
        self.manager.cancel_ongoing_adaptation_calls(data.entity_id)
        _LOGGER.debug(
            "%s: execute_cancellable_adaptation_calls with data: %s",
            self._name,
            data,
        )
        # Execute adaptation calls within a task
        task: asyncio.Task[None] | None = None
        try:
            task = self.hass.async_create_task(self._execute_adaptation_calls(data))
            if LightControlAttributes.BRIGHTNESS in data.attributes:
                self.manager.adaptation_tasks_brightness[data.entity_id] = task
            if LightControlAttributes.COLOR in data.attributes:
                self.manager.adaptation_tasks_color[data.entity_id] = task
            await task
        except asyncio.CancelledError:
            if (caller := asyncio.current_task()) is not None and caller.cancelling():
                raise  # Request/timeout cancellation must not report a partial sequence as success.
            _LOGGER.debug(
                "%s: Ongoing adaptation of %s cancelled, with AdaptationData: %s",
                self._name,
                data.entity_id,
                data,
            )
        finally:
            if task is not None:
                if self.manager.adaptation_tasks_brightness.get(data.entity_id) is task:
                    self.manager.adaptation_tasks_brightness.pop(data.entity_id, None)
                if self.manager.adaptation_tasks_color.get(data.entity_id) is task:
                    self.manager.adaptation_tasks_color.pop(data.entity_id, None)

    async def _update_attrs_and_maybe_adapt_lights(
        self,
        *,
        context: Context,
        lights: list[str] | None = None,
        transition: float | None = None,
        force: bool = False,
    ) -> None:
        assert context is not None
        _LOGGER.debug(
            "%s: '_update_attrs_and_maybe_adapt_lights' called with context.id='%s'"
            " lights: '%s', transition: '%s', force: '%s'",
            self._name,
            context.id,
            lights,
            transition,
            force,
        )
        if not self.is_on:
            return  # An interval/state callback may already be queued when disabled.
        if transition is None:
            transition = self._transition
        effective_sleep = self._effective_sleep_state()
        settings = self._sun_light_settings.get_settings(effective_sleep, transition)
        self._settings.update(settings)
        self.async_write_ha_state()

        if not force and self._only_once:
            return

        if lights is None:
            lights = self.lights

        on_lights = [light for light in lights if is_on(self.hass, light)]

        if force:
            filtered_lights = on_lights
        else:
            filtered_lights: list[str] = []
            for light in on_lights:
                # Don't adapt lights that haven't finished prior transitions.
                timer = self.manager.transition_timers.get(light)
                if timer is not None and timer.is_running():
                    _LOGGER.debug(
                        "%s: Light '%s' is still transitioning, context.id='%s'",
                        self._name,
                        light,
                        context.id,
                    )
                elif (
                    # This is to prevent lights immediately turning on after
                    # being turned off in 'interval' update, see #726
                    not self._detect_non_ha_changes
                    and is_our_context(context, "interval")
                    and (turn_on := self.manager.turn_on_event.get(light))
                    and (turn_off := self.manager.turn_off_event.get(light))
                    and turn_off.time_fired > turn_on.time_fired
                ):
                    _LOGGER.debug(
                        "%s: Light '%s' was turned just turned off, context.id='%s'",
                        self._name,
                        light,
                        context.id,
                    )
                else:
                    filtered_lights.append(light)

        _LOGGER.debug("%s: filtered_lights: '%s'", self._name, filtered_lights)
        if not filtered_lights:
            return

        tasks: list[asyncio.Task[None]] = []
        for light in filtered_lights:
            await self.manager.update_manually_controlled_from_untracked_change(
                self,
                light,
                force,
                context,
            )
            if not self.is_on:
                break  # A light refresh can yield to a profile-off/config reload.

            # Performance optimization: Skip adaptation task if all attributes are
            # manually controlled and the task wouldn't actually do anything.
            if self.manager.get_adaption_control_attributes(self, light).has_none():
                _LOGGER.debug(
                    "%s: '%s' is being manually controlled, skip adaptation, context.id=%s.",
                    self._name,
                    light,
                    context.id,
                )
                continue

            _LOGGER.debug(
                "%s: Calling _adapt_light from _update_attrs_and_maybe_adapt_lights:"
                " '%s' with transition %s and context.id=%s",
                self._name,
                light,
                transition,
                context.id,
            )
            coro = self._adapt_light(
                light, context, transition, force=force,
                settings=settings, effective_sleep=effective_sleep,
            )
            task = self.hass.async_create_task(
                coro,
            )
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)

    async def _respond_to_off_to_on_event(
        self,
        entity_id: str,
        event: Event[EventStateChangedData],
    ) -> None:
        assert not self.manager.is_proactively_adapting(event.context.id, entity_id)
        from_turn_on = self.manager._off_to_on_state_event_is_from_turn_on(
            entity_id,
            event,
        )
        if (
            self._take_over_control
            and not self._detect_non_ha_changes
            and not from_turn_on
        ):
            # There is an edge case where 2 switches control the same light, e.g.,
            # one for brightness and one for color. Now we will mark both switches
            # as manually controlled, which is not 100% correct.
            _LOGGER.debug(
                "%s: Ignoring 'off' → 'on' event for '%s' with context.id='%s'"
                " because 'light.turn_on' was not called by HA and"
                " 'detect_non_ha_changes' is False",
                self._name,
                entity_id,
                event.context.id,
            )
            self.manager.set_manual_control_attributes(entity_id)
            return

        if (
            self._take_over_control
            and self._adapt_only_on_bare_turn_on
            and from_turn_on
            # circadian_pro.apply can turn on light, so check this is not our context
            and not is_our_context(event.context)
        ):
            service_data = self.manager.turn_on_event[entity_id].data[ATTR_SERVICE_DATA]
            if self.manager._mark_manual_control_if_non_bare_turn_on(
                entity_id,
                service_data,
            ):
                _LOGGER.debug(
                    "Skipping responding to 'off' → 'on' event for '%s' with context.id='%s' because"
                    " we only adapt on bare light.turn_on events and not on service_data: '%s'",
                    entity_id,
                    event.context.id,
                    service_data,
                )
                return

        if self._adapt_delay > 0:
            await asyncio.sleep(self._adapt_delay)

        await self._update_attrs_and_maybe_adapt_lights(
            context=self.create_context("light_event", parent=event.context),
            lights=[entity_id],
            transition=self.initial_transition,
            force=True,
        )

    async def _sleep_mode_switch_state_event_action(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        if not _is_state_event(event, (STATE_ON, STATE_OFF)):
            _LOGGER.debug("%s: Ignoring sleep event %s", self._name, event)
            return
        _LOGGER.debug(
            "%s: _sleep_mode_switch_state_event_action, event: '%s'",
            self._name,
            event,
        )
        # Reset the manually controlled status when the "sleep mode" changes
        self.manager.reset(*self.lights)
        await self._update_attrs_and_maybe_adapt_lights(
            context=self.create_context("sleep", parent=event.context),
            transition=self._sleep_transition,
            force=True,
        )

    def fire_manual_control_event(
        self,
        light: str,
        context: Context,
    ) -> None:
        """Fire an event that 'light' is marked as manual_control."""
        _LOGGER.debug(
            "'circadian_pro.manual_control' event fired for %s for light %s",
            self.entity_id,
            light,
        )
        manual_attributes = self.manager.get_manual_control_attributes(light)
        self.hass.bus.async_fire(
            f"{DOMAIN}.manual_control",
            {
                ATTR_ENTITY_ID: light,
                SWITCH_DOMAIN: self.entity_id,
                CONF_MANUAL_CONTROL: manual_attributes,
            },
            context=context,
        )


class SimpleSwitch(SwitchEntity, RestoreEntity):
    """Representation of a Circadian Pro switch."""

    def __init__(
        self,
        which: str,
        initial_state: bool,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        icon: str,
    ) -> None:
        """Initialize the Circadian Pro switch."""
        self.hass = hass
        data = validate(config_entry)
        self._icon = icon
        self._state: bool = initial_state
        self._which = which
        self._config_name = data[CONF_NAME]
        self._unique_id = f"{self._config_name}_{slugify(self._which)}"
        self._name = f"Circadian Pro {which}: {self._config_name}"
        self._initial_state = initial_state

        # Suggest a stable entity_id that matches the integration name.
        # Note: Home Assistant may keep an existing entity_id from the entity registry.
        object_id = f"{DOMAIN}_{slugify(self._which)}_{slugify(self._config_name)}"
        self.entity_id = async_generate_entity_id("switch.{}", object_id, hass=hass)

    @property
    def name(self) -> str:
        """Return the name of the device if any."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of entity."""
        return self._unique_id

    @property
    def icon(self) -> str:
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def is_on(self) -> bool | None:
        """Return true if circadian pro is on."""
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info, used to group this and adjacent entities in the UI."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self._config_name),
            },
            name=f"Circadian Pro: {self._config_name}",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        last_state = await self.async_get_last_state()
        _LOGGER.debug("%s: last state is %s", self._name, last_state)
        if (last_state is None and self._initial_state) or (
            last_state is not None and last_state.state == STATE_ON
        ):
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn on circadian pro sleep mode."""
        _LOGGER.debug("%s: Turning on", self._name)
        self._state = True

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn off circadian pro sleep mode."""
        _LOGGER.debug("%s: Turning off", self._name)
        self._state = False


CircadianProSwitches = list[CircadianProSwitch]
CircadianProSwitchMap = dict[CircadianProSwitch, list[str]]


class CircadianProManager:
    """Track 'light.turn_off' and 'light.turn_on' service calls."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the CircadianProManager that is shared among all switches."""
        assert hass is not None
        self.hass = hass
        self.switches: list[CircadianProSwitch] = []
        self.lights: set[str] = set()
        self._disabled = False

        # Tracks 'light.turn_off' service calls
        self.turn_off_event: dict[str, Event] = {}
        # Tracks 'light.turn_on' service calls
        self.turn_on_event: dict[str, Event] = {}
        # Tracks 'light.toggle' service calls
        self.toggle_event: dict[str, Event] = {}
        # Tracks 'on' → 'off' state changes
        self.on_to_off_event: dict[str, Event[EventStateChangedData]] = {}
        # Tracks 'off' → 'on' state changes
        self.off_to_on_event: dict[str, Event[EventStateChangedData]] = {}
        # Keep 'asyncio.sleep' tasks that can be cancelled by 'light.turn_on' events
        self.sleep_tasks: dict[str, asyncio.Task[None]] = {}
        # Locks that prevent light adjusting when waiting for a light to 'turn_off'
        self.turn_off_locks: dict[str, asyncio.Lock] = {}
        # Tracks which lights are manually controlled
        self.manual_control: dict[str, LightControlAttributes] = {}
        # Track 'state_changed' events of self.lights resulting from this integration
        self.our_last_state_on_change: dict[str, list[State]] = {}
        # Track last 'service_data' to 'light.turn_on' resulting from this integration
        self.last_service_data: dict[str, dict[str, Any]] = {}
        self.last_service_context: dict[str, str] = {}
        self.last_service_acknowledgements: dict[str, tuple[dict[str, Any], State]] = {}
        # Track ongoing split adaptations to be able to cancel them
        self.adaptation_tasks_brightness: dict[str, asyncio.Task[None]] = {}
        self.adaptation_tasks_color: dict[str, asyncio.Task[None]] = {}

        # Track auto reset of manual_control
        self.auto_reset_manual_control_timers: dict[str, _AsyncSingleShotTimer] = {}
        self.auto_reset_manual_control_times: dict[str, float] = {}

        # Track light transitions
        self.transition_timers: dict[str, _AsyncSingleShotTimer] = {}

        # Track _execute_cancellable_adaptation_calls tasks
        self.adaptation_tasks: set[asyncio.Task[None]] = set()

        # Setup listeners and its callbacks to remove them later
        self.listener_removers = [
            self.hass.bus.async_listen(
                EVENT_CALL_SERVICE,
                self.turn_on_off_event_listener,
            ),
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                self.state_changed_event_listener,
            ),
        ]

        self._proactively_adapting_contexts: dict[str, set[str]] = {}
        self._context_cnt: int = 0

        try:
            self.listener_removers.append(
                setup_service_call_interceptor(
                    hass,
                    LIGHT_DOMAIN,
                    SERVICE_TURN_ON,
                    self._service_interceptor_turn_on_handler,
                ),
            )

            self.listener_removers.append(
                setup_service_call_interceptor(
                    hass,
                    LIGHT_DOMAIN,
                    SERVICE_TOGGLE,
                    self._service_interceptor_turn_on_handler,
                ),
            )
        except RuntimeError:
            _LOGGER.warning(
                "Failed to set up service call interceptors, "
                "falling back to event-reactive mode",
                exc_info=True,
            )

    def disable(self) -> None:
        """Disable listeners, timers, and pending adaptation work."""
        if self._disabled:
            return
        self._disabled = True

        while self.listener_removers:
            remove = self.listener_removers.pop()
            try:
                remove()
            except Exception:  # pragma: no cover - defensive cleanup
                _LOGGER.exception("Failed to remove a Circadian Pro listener")

        for task in list(self.adaptation_tasks):
            task.cancel()
        self.adaptation_tasks.clear()

        for task_dict in (
            self.sleep_tasks,
            self.adaptation_tasks_brightness,
            self.adaptation_tasks_color,
        ):
            for task in set(task_dict.values()):
                task.cancel()
            task_dict.clear()

        for timer_dict in (
            self.auto_reset_manual_control_timers,
            self.transition_timers,
        ):
            for timer in timer_dict.values():
                timer.cancel()
            timer_dict.clear()

        self.switches.clear()
        self.lights.clear()
        self.auto_reset_manual_control_times.clear()
        self.turn_off_event.clear()
        self.turn_on_event.clear()
        self.toggle_event.clear()
        self.on_to_off_event.clear()
        self.off_to_on_event.clear()
        self.turn_off_locks.clear()
        self.manual_control.clear()
        self.our_last_state_on_change.clear()
        self.last_service_data.clear()
        self.last_service_context.clear()
        self.last_service_acknowledgements.clear()
        self._proactively_adapting_contexts.clear()

    def register_switch(self, switch: CircadianProSwitch) -> None:
        """Register an active switch and rebuild shared light state."""
        if switch not in self.switches:
            self.switches.append(switch)
        self.rebuild_lights()

    def unregister_switch(self, switch: CircadianProSwitch) -> None:
        """Unregister a switch and remove state for lights no longer in use."""
        if switch in self.switches:
            self.switches.remove(switch)
        self.rebuild_lights()

    def _cleanup_light(self, light: str) -> None:
        """Remove transient state associated with an unmanaged light."""
        for task_dict in (
            self.sleep_tasks,
            self.adaptation_tasks_brightness,
            self.adaptation_tasks_color,
        ):
            if task := task_dict.pop(light, None):
                task.cancel()

        for timer_dict in (
            self.auto_reset_manual_control_timers,
            self.transition_timers,
        ):
            if timer := timer_dict.pop(light, None):
                timer.cancel()

        self.auto_reset_manual_control_times.pop(light, None)
        self.turn_off_locks.pop(light, None)
        self.turn_off_event.pop(light, None)
        self.turn_on_event.pop(light, None)
        self.toggle_event.pop(light, None)
        self.on_to_off_event.pop(light, None)
        self.off_to_on_event.pop(light, None)
        self.manual_control.pop(light, None)
        self.our_last_state_on_change.pop(light, None)
        self.last_service_data.pop(light, None)
        self.last_service_context.pop(light, None)
        self.last_service_acknowledgements.pop(light, None)
        self.clear_proactively_adapting(light)

    def rebuild_lights(self) -> None:
        """Rebuild managed lights and per-light auto-reset settings."""
        new_lights = {
            light
            for switch in self.switches
            for light in switch.lights
        }
        removed_lights = self.lights - new_lights
        self.lights = new_lights

        for light in removed_lights:
            self._cleanup_light(light)

        new_reset_times: dict[str, float] = {}
        for switch in self.switches:
            delay = float(switch._auto_reset_manual_control_time)
            if delay <= 0:
                continue
            for light in switch.lights:
                # Preserve the previous load-order behavior for lights shared
                # by multiple Circadian Pro instances: the latest instance wins.
                new_reset_times[light] = delay

        for light in set(self.auto_reset_manual_control_times) - set(new_reset_times):
            self.auto_reset_manual_control_times.pop(light, None)
            if timer := self.auto_reset_manual_control_timers.pop(light, None):
                timer.cancel()

        for light, delay in new_reset_times.items():
            previous_delay = self.auto_reset_manual_control_times.get(light)
            self.auto_reset_manual_control_times[light] = delay
            if (
                previous_delay is not None
                and previous_delay != delay
                and (timer := self.auto_reset_manual_control_timers.get(light))
            ):
                timer.delay = delay
                timer.start()

    def set_proactively_adapting(self, context_id: str, entity_id: str) -> None:
        """Declare the adaptation with context_id as proactively adapting,
        and associate it to an entity_id.
        """  # noqa: D205
        self._proactively_adapting_contexts.setdefault(context_id, set()).add(entity_id)

    def is_proactively_adapting(self, context_id: str, entity_id: str | None = None) -> bool:
        """Determine whether an adaptation with the given context_id is proactive."""
        is_proactively_adapting_context = (
            context_id in self._proactively_adapting_contexts
            and (entity_id is None or entity_id in self._proactively_adapting_contexts[context_id])
        )

        _LOGGER.debug(
            "is_proactively_adapting_context='%s', context_id='%s'",
            is_proactively_adapting_context,
            context_id,
        )

        return is_proactively_adapting_context

    def clear_proactively_adapting(self, entity_id: str) -> None:
        """Clear all context IDs associated with the given entity ID.

        Call this method to clear past context IDs and avoid a memory leak.
        """
        for context_id, lights in list(self._proactively_adapting_contexts.items()):
            lights.discard(entity_id)
            if not lights:
                self._proactively_adapting_contexts.pop(context_id)

    def create_context(
        self,
        which: str = "default",
        parent: Context | None = None,
    ) -> Context:
        """Create a context that identifies this integration."""
        context = create_context("manager", which, self._context_cnt, parent=parent)
        self._context_cnt += 1
        return context

    def _separate_entity_ids(
        self,
        entity_ids: list[str],
        data: ServiceData,
    ) -> tuple[CircadianProSwitchMap, list[str]]:
        # Create a mapping from switch to entity IDs
        # CircadianProSwitch → entity_ids mapping
        switch_to_eids: CircadianProSwitchMap = {}
        skipped: list[str] = []
        for entity_id in entity_ids:
            try:
                switch = _switch_with_lights(
                    self.hass,
                    [entity_id],
                    # Do not expand light groups, because HA will make a separate light.turn_on
                    # call where the lights are expanded, and that call will be intercepted.
                    expand_light_groups=False,
                )
            except NoSwitchFoundError:
                # Needs to make the original call but without adaptation
                skipped.append(entity_id)
                _LOGGER.debug(
                    "No switch found for entity_id='%s', skipped='%s'",
                    entity_id,
                    skipped,
                )
            else:
                if (
                    not switch.is_on
                    or not switch._intercept
                    # Never adapt on light groups, because HA will make a separate light.turn_on
                    or ((e := self.hass.states.get(entity_id)) and _is_light_group(e))
                    # Prevent adaptation of TURN_ON calls when light is already on,
                    # and of TOGGLE calls when toggling off.
                    or self.hass.states.is_state(entity_id, STATE_ON)
                    or self.manual_control.get(entity_id, False)
                    or (
                        switch._take_over_control
                        and switch._adapt_only_on_bare_turn_on
                        and self._mark_manual_control_if_non_bare_turn_on(
                            entity_id,
                            data[CONF_PARAMS],
                        )
                    )
                ):
                    _LOGGER.debug(
                        "Switch is off or light is already on for entity_id='%s', skipped='%s'"
                        " (is_on='%s', is_state='%s', manual_control='%s', switch._intercept='%s')",
                        entity_id,
                        skipped,
                        switch.is_on,
                        self.hass.states.is_state(entity_id, STATE_ON),
                        self.manual_control.get(entity_id, False),
                        switch._intercept,
                    )
                    skipped.append(entity_id)
                else:
                    switch_to_eids.setdefault(switch, []).append(entity_id)
        return switch_to_eids, skipped

    def _correct_for_multi_light_intercept(
        self,
        entity_ids: list[str],
        switch_to_eids: CircadianProSwitchMap,
        skipped: list[str],
    ):
        # Check for multi_light_intercept: true/false
        mli = [sw._multi_light_intercept for sw in switch_to_eids]
        more_than_one_switch = len(switch_to_eids) > 1
        single_switch_with_multiple_lights = (
            len(switch_to_eids) == 1 and len(next(iter(switch_to_eids.values()))) > 1
        )
        switch_without_multi_light_intercept = not all(mli)
        if more_than_one_switch and switch_without_multi_light_intercept:
            _LOGGER.warning(
                "Multiple switches (%s) targeted, but not all have"
                " multi_light_intercept: true, so skipping intercept"
                " for all lights.",
                switch_to_eids,
            )
            skipped = entity_ids
            switch_to_eids = {}
        elif (
            single_switch_with_multiple_lights and switch_without_multi_light_intercept
        ):
            _LOGGER.warning(
                "Single switch with multiple lights targeted (%s), but"
                " multi_light_intercept: true is not set, so skipping intercept"
                " for all lights.",
                switch_to_eids,
            )
            skipped = entity_ids
            switch_to_eids = {}
        return switch_to_eids, skipped

    async def _service_interceptor_turn_on_handler(
        self,
        call: ServiceCall,
        service_data: ServiceData,
    ) -> ServiceCallPostProcessor | None:
        """Prepare each light separately, then retain one original service call.

        Other lights keep their original parameters and receive their own native
        color/brightness payload and optional split-command continuation.
        """
        is_skipped_hash = is_our_context(call.context, "skipped")
        _LOGGER.debug(
            "(0) _service_interceptor_turn_on_handler: call.context.id='%s', is_skipped_hash='%s'",
            call.context.id,
            is_skipped_hash,
        )
        if is_our_context(call.context) and not is_skipped_hash:
            # Don't adapt our own service calls, but do re-adapt calls that
            # were skipped by us
            return

        if has_effect_attribute(service_data[CONF_PARAMS]):
            return

        _LOGGER.debug(
            "(1) _service_interceptor_turn_on_handler: call='%s', service_data='%s'",
            call,
            service_data,
        )

        # Because _service_interceptor_turn_on_single_light_handler modifies the
        # original service data, we need to make a copy of it to use in the skipped call
        service_data_copy = deepcopy(service_data)

        entity_ids = await async_check_entity_control(
            self.hass, call.context, self._get_entity_list(service_data),
            filter_allowed=service_data.get(ATTR_ENTITY_ID) == "all",
        )
        # Note: we do not expand light groups anywhere in this method, instead
        # we skip them and rely on the followup call that HA will make
        # with the expanded entity IDs.

        switch_to_eids, skipped = self._separate_entity_ids(
            entity_ids,
            service_data,
        )

        (
            switch_to_eids,
            skipped,
        ) = self._correct_for_multi_light_intercept(
            entity_ids,
            switch_to_eids,
            skipped,
        )
        _LOGGER.debug(
            "(2) _service_interceptor_turn_on_handler: switch_to_eids='%s', skipped='%s'",
            switch_to_eids,
            skipped,
        )

        def modify_service_data(
            service_data: ServiceData,
            entity_ids: list[str],
        ) -> dict[str, Any]:
            """Modify the service data to contain the entity IDs."""
            for target in (ATTR_ENTITY_ID, ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID):
                service_data.pop(target, None)
            service_data[ATTR_ENTITY_ID] = entity_ids
            return service_data

        targets = [(switch, eid) for switch, eids in switch_to_eids.items() for eid in eids]
        if not targets:
            return  # Keep the original call intact when interception is disabled.

        planned = []
        for index, (switch, eid) in enumerate(targets):
            data = modify_service_data(deepcopy(service_data_copy), [eid])
            context = call.context
            individual_call = call
            if index:
                context = switch.create_context("intercept", parent=call.context)
                context.user_id = call.context.user_id
                individual_call = ServiceCall(
                    self.hass, call.domain, call.service, data, context,
                    return_response=call.return_response,
                )
            try:
                adaptation_data = await self._service_interceptor_turn_on_single_light_handler(
                    entity_ids=[eid], switch=switch,
                    transition=service_data_copy[CONF_PARAMS].get(ATTR_TRANSITION, switch.initial_transition),
                    call=individual_call, data=data,
                )
            except Exception:
                # Preparation has not sent a command yet. Retain this light's
                # original parameters without undoing other lights' plans.
                _LOGGER.exception("Could not prepare intercepted light %s; keeping its original parameters", eid)
                self.clear_proactively_adapting(eid)
                self.reset(eid, reset_manual_control=False)
                data = modify_service_data(deepcopy(service_data_copy), [eid])
                adaptation_data = None
            planned.append((switch, eid, context, data, adaptation_data))

        def schedule_remainder(switch: CircadianProSwitch, data: AdaptationData | None) -> None:
            if data is None or data.max_length <= 1:
                return
            data.initial_sleep = True
            task = self.hass.async_create_task(switch.execute_cancellable_adaptation_calls(data))
            self.adaptation_tasks.add(task)
            task.add_done_callback(self.adaptation_tasks.discard)

        def current_payload(
            switch: CircadianProSwitch,
            eid: str,
            data: ServiceData,
            adaptation_data: AdaptationData | None,
        ) -> tuple[ServiceData, AdaptationData | None]:
            if not switch.is_on or is_calibration_preview_active(self.hass, eid):
                # Another service handler can yield to profile-off or preview.
                # Honor the pending human action with its original parameters.
                self.clear_proactively_adapting(eid)
                self.reset(eid, reset_manual_control=False)
                return modify_service_data(deepcopy(service_data_copy), [eid]), None
            return data, adaptation_data

        # Restrict the original handler before secondary calls: a later failure
        # must never cause an already completed toggle to be sent a second time.
        service_data.clear()
        service_data.update(planned[0][3])
        for switch, eid, context, data, adaptation_data in planned[1:]:
            data, adaptation_data = current_payload(switch, eid, data, adaptation_data)
            try:
                await self.hass.services.async_call(
                    LIGHT_DOMAIN, call.service,
                    {ATTR_ENTITY_ID: [eid], **data[CONF_PARAMS]},
                    blocking=True, context=context,
                )
            except Exception:
                _LOGGER.exception("Could not execute intercepted light %s", eid)
                self.clear_proactively_adapting(eid)
                self.reset(eid, reset_manual_control=False)
            else:
                # Only start its continuation after its first service handler
                # completed, even when the split delay and transition are zero.
                schedule_remainder(switch, adaptation_data)

        # Call the original light service for skipped entities.
        if skipped:
            # This forwards a user's command unchanged. Keep it external so
            # already-on lights still register the user's manual adjustments.
            context = Context(user_id=call.context.user_id, parent_id=call.context.id)
            _LOGGER.debug(
                "(5) _service_interceptor_turn_on_handler: calling light.%s with skipped='%s', service_data: '%s', context='%s'",
                call.service,
                skipped,
                service_data_copy,  # This is the original service data
                context.id,
            )
            try:
                await self.hass.services.async_call(
                    LIGHT_DOMAIN, call.service,
                    {ATTR_ENTITY_ID: skipped, **service_data_copy[CONF_PARAMS]},
                    blocking=True, context=context,
                )
            except Exception:
                _LOGGER.exception("Could not execute original light parameters for %s", skipped)

        # The proxy owns execution of the original handler. Its continuation
        # must wait for that handler to finish, including slow device I/O.
        switch, eid, _context, data, adaptation_data = planned[0]
        data, adaptation_data = current_payload(switch, eid, data, adaptation_data)
        service_data.clear()
        service_data.update(data)
        def after_original(success: bool) -> None:
            if self.last_service_context.get(eid) != _context.id:
                return  # A newer command or a turn-off has already taken over.
            if not success:
                self.clear_proactively_adapting(eid)
                self.reset(eid, reset_manual_control=False)
                return
            schedule_remainder(switch, adaptation_data)

        return after_original

    async def _service_interceptor_turn_on_single_light_handler(
        self,
        entity_ids: list[str],
        switch: CircadianProSwitch,
        transition: float,
        call: ServiceCall,
        data: ServiceData,
    ) -> AdaptationData | None:
        """Prepare exactly one light without scheduling its remaining calls."""
        if len(entity_ids) != 1:
            raise ValueError("An intercepted adaptation must target exactly one light")
        _LOGGER.debug(
            "Intercepted TURN_ON call with data %s (%s)",
            data,
            call.context.id,
        )

        # Reset because turning on the light, this also happens in
        # state_changed_event_listener, however, this function is called
        # before that one.
        self.reset(*entity_ids, reset_manual_control=False)
        for eid in entity_ids:
            self.clear_proactively_adapting(eid)

        context = switch.create_context("intercept", parent=call.context)
        context.user_id = call.context.user_id
        adaptation_data = await switch.prepare_adaptation_data(entity_ids[0], transition, context=context)
        if adaptation_data is None:
            return

        # Take first adaptation item to apply it to this service call
        first_service_data = await adaptation_data.next_service_call_data()

        if not first_service_data:
            return

        # Update/adapt service call data
        first_service_data.pop(ATTR_ENTITY_ID, None)
        # This is called as a preprocessing step by the schema validation of the original
        # service call and needs to be repeated here to also process the added adaptation data.
        # (A more generic alternative would be re-executing the validation, but that is more
        # complicated and unstable because it requires transformation of the data object back
        # into its original service call structure which cannot be reliably done due to the
        # lack of a bijective mapping.)
        preprocess_turn_on_alternatives(self.hass, first_service_data)
        if any(attribute in first_service_data for attribute in COLOR_ATTRS):
            for attribute in COLOR_ATTRS:
                data[CONF_PARAMS].pop(attribute, None)
        if ATTR_BRIGHTNESS in first_service_data:
            # Relative steps otherwise overwrite the adapted absolute target
            # later in HA's per-entity preprocessing, invalidating its baseline.
            for attribute in BRIGHTNESS_ATTRS:
                data[CONF_PARAMS].pop(attribute, None)
        data[CONF_PARAMS].update(first_service_data)

        entity_id = entity_ids[0]
        self.last_service_data[entity_id] = {ATTR_ENTITY_ID: entity_id, **deepcopy(data[CONF_PARAMS])}
        self.last_service_context[entity_id] = call.context.id
        self.last_service_acknowledgements.pop(entity_id, None)

        # Associate both contexts with this light before its first handler can
        # report state. The caller owns this light's independent continuation.
        self.set_proactively_adapting(call.context.id, entity_id)
        self.set_proactively_adapting(adaptation_data.context.id, entity_id)
        return adaptation_data

    def _handle_timer(
        self,
        light: str,
        timers_dict: dict[str, _AsyncSingleShotTimer],
        delay: float | None,
        reset_coroutine: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        if delay is not None and delay <= 0:
            delay = None

        async def reset_and_remove() -> None:
            timers_dict.pop(light, None)
            await reset_coroutine()

        timer = timers_dict.get(light)
        if timer is not None:
            if delay is None:  # Timer object exists, but should not anymore
                timer.cancel()
                timers_dict.pop(light, None)
            else:  # Timer object already exists, just update the delay and restart it
                timer.delay = delay
                timer.callback = reset_and_remove
                timer.start()
        elif delay is not None:  # Timer object does not exist, create it
            timer = _AsyncSingleShotTimer(self.hass, delay, reset_and_remove)
            timers_dict[light] = timer
            timer.start()

    def start_transition_timer(self, light: str) -> None:
        """Mark a light as manually controlled."""
        last_service_data = self.last_service_data.get(light)
        if last_service_data is None:
            _LOGGER.debug(
                "No last service data for light %s, not starting timer.",
                light,
            )
            return

        last_transition = last_service_data.get(ATTR_TRANSITION)
        if not last_transition:
            _LOGGER.debug(
                "No transition in last adapt for light %s, not starting timer.",
                light,
            )
            return

        _LOGGER.debug(
            "Start transition timer of %s seconds for light %s",
            last_transition,
            light,
        )

        async def reset() -> None:
            # Called when the timer expires, doesn't need to do anything
            _LOGGER.debug(
                "Transition finished for light %s",
                light,
            )

        self._handle_timer(light, self.transition_timers, last_transition, reset)

    def set_auto_reset_manual_control_times(
        self,
        lights: list[str],
        time: float,
    ) -> None:
        """Set the time after which the lights are automatically reset."""
        for light in lights:
            if time <= 0:
                self.auto_reset_manual_control_times.pop(light, None)
                if timer := self.auto_reset_manual_control_timers.pop(light, None):
                    timer.cancel()
                continue
            old_time = self.auto_reset_manual_control_times.get(light)
            if (old_time is not None) and (old_time != time):
                _LOGGER.info(
                    "Setting auto_reset_manual_control for '%s' from %s seconds to %s seconds."
                    " This might happen because the light is in multiple swiches"
                    " or because of a config change.",
                    light,
                    old_time,
                    time,
                )
            self.auto_reset_manual_control_times[light] = time

    def get_manual_control_attributes(
        self,
        light: str,
    ) -> LightControlAttributes:
        """Get the attributes for a light that are manually controlled."""
        return self.manual_control.get(light, LightControlAttributes.NONE)

    def set_manual_control_attributes(
        self,
        light: str,
        attributes: LightControlAttributes = LightControlAttributes.ALL,
    ) -> None:
        """Mark attributes of a light as manually controlled."""
        _LOGGER.debug(
            "Light %s: Setting manual control attributes to %s (from %s).",
            light,
            attributes,
            self.manual_control.get(light, LightControlAttributes.NONE),
        )
        self.manual_control[light] = attributes
        delay = self.auto_reset_manual_control_times.get(light)

        async def reset() -> None:
            _LOGGER.debug(
                "Auto resetting 'manual_control' status of '%s' because"
                " it was not manually controlled for %s seconds.",
                light,
                delay,
            )
            self.reset(light)
            switches = _switches_with_lights(self.hass, [light])
            for switch in switches:
                if not switch.is_on:
                    continue
                await switch._update_attrs_and_maybe_adapt_lights(
                    context=switch.create_context("autoreset"),
                    lights=[light],
                    transition=switch.initial_transition,
                    force=True,
                )
            if self.get_manual_control_attributes(light).has_any():
                _LOGGER.debug(
                    "Manual-control state for %s was set again while it was being reset",
                    light,
                )

        self._handle_timer(light, self.auto_reset_manual_control_timers, delay, reset)

    def add_manual_control_attributes(
        self,
        light: str,
        attributes: LightControlAttributes,
    ) -> None:
        """Add attributes to the manual control status of a light."""
        current = self.get_manual_control_attributes(light)
        _LOGGER.debug(
            "Light %s: Adding manual control attributes %s (current: %s).",
            light,
            attributes,
            current,
        )
        new = current | attributes
        self.set_manual_control_attributes(light, new)

    def get_adaption_control_attributes(
        self,
        switch: CircadianProSwitch,
        light: str,
    ) -> LightControlAttributes:
        """Get the attributes that should be adapted for a light.

        Determines the attributes that should actually be adapted from the attributes
        marked as manually controlled, the state of adaptation switches, and the adaptation
        configuration.

        Example 1: When no attributes are marked as manually controlled and all adaptation
        switches are on, all attributes are returned.

        Example 2: When no attributes are marked as manually controlled and the brightness
        adaptation switch is off, only the color attribute is returned.

        Example 3: When only brightness is marked as manually controlled, but the configuration
        specifies to pause all adaptations on manual change, no attributes are returned so that
        color is also not adapted.
        """
        if is_calibration_preview_active(self.hass, light):
            return LightControlAttributes.NONE

        denied_adaptation_attributes = self.get_manual_control_attributes(light)

        if (
            denied_adaptation_attributes.has_any()
            and switch._take_over_control_mode == TakeOverControlMode.PAUSE_ALL
        ):
            # Extend to pausing all only if there is at least one manually controlled attribute
            denied_adaptation_attributes = LightControlAttributes.ALL

        enabled_adaptation_attributes = (
            LightControlAttributes.BRIGHTNESS
            if switch.adapt_brightness_switch.is_on
            else LightControlAttributes.NONE
        ) | (
            LightControlAttributes.COLOR
            if switch.adapt_color_switch.is_on
            else LightControlAttributes.NONE
        )

        return (
            LightControlAttributes.ALL
            & ~denied_adaptation_attributes
            & enabled_adaptation_attributes
        )

    def cancel_ongoing_adaptation_calls(
        self,
        light_id: str,
    ) -> None:
        """Cancel ongoing adaptation service calls for a specific light entity."""
        brightness_task = self.adaptation_tasks_brightness.get(light_id)
        color_task = self.adaptation_tasks_color.get(light_id)
        if brightness_task is not None and not brightness_task.done():
            _LOGGER.debug(
                "Cancelled ongoing brightness adaptation calls (%s) for '%s'",
                brightness_task,
                light_id,
            )
            brightness_task.cancel()
        if color_task is not None and not color_task.done():
            _LOGGER.debug(
                "Cancelled ongoing color adaptation calls (%s) for '%s'",
                color_task,
                light_id,
            )
            # color_task might be the same as brightness_task
            color_task.cancel()

    def reset(self, *lights: str, reset_manual_control: bool = True) -> None:
        """Reset the 'manual_control' status of the lights."""
        for light in lights:
            if reset_manual_control:
                _LOGGER.debug(
                    "Light %s: Clearing manual control attributes.",
                    light,
                )
                self.manual_control[light] = LightControlAttributes.NONE
                if timer := self.auto_reset_manual_control_timers.pop(light, None):
                    timer.cancel()
            self.our_last_state_on_change.pop(light, None)
            self.last_service_data.pop(light, None)
            self.last_service_context.pop(light, None)
            self.last_service_acknowledgements.pop(light, None)
            self.cancel_ongoing_adaptation_calls(light)

    def _get_entity_list(self, service_data: ServiceData) -> list[str]:
        """Use HA's target resolution so areas/devices/floors/labels can be combined."""
        if service_data.get(ATTR_ENTITY_ID) == "all":
            return sorted(self.hass.states.async_entity_ids(LIGHT_DOMAIN))
        referenced = target_helpers.async_extract_referenced_entity_ids(
            self.hass, target_helpers.TargetSelection(service_data), expand_group=False,
        )
        return sorted(
            entity_id for entity_id in referenced.referenced | referenced.indirectly_referenced
            if entity_id.startswith(f"{LIGHT_DOMAIN}.")
        )

    async def turn_on_off_event_listener(self, event: Event) -> None:
        """Track 'light.turn_off' and 'light.turn_on' service calls."""
        domain = event.data.get(ATTR_DOMAIN)
        if domain != LIGHT_DOMAIN:
            return

        service = event.data[ATTR_SERVICE]
        if service not in (SERVICE_TURN_ON, SERVICE_TURN_OFF, SERVICE_TOGGLE):
            return
        service_data = event.data[ATTR_SERVICE_DATA]
        try:
            permitted = await async_check_entity_control(
                self.hass, event.context, self._get_entity_list(service_data),
                filter_allowed=service_data.get(ATTR_ENTITY_ID) == "all",
            )
        except (Unauthorized, UnknownUser):
            return  # HA has not checked access yet when this event is fired.
        entity_ids = [
            eid for eid in permitted
            if eid in self.lights and not is_calibration_preview_context(self.hass, eid, event.context)
        ]
        if not entity_ids:
            return

        def off(eid: str, event: Event) -> None:
            self.turn_off_event[eid] = event
            self.reset(eid)

        async def on(eid: str, event: Event) -> None:
            task = self.sleep_tasks.get(eid)
            if task is not None:
                task.cancel()
            self.turn_on_event[eid] = event

            try:
                switch = _switch_with_lights(
                    self.hass,
                    [eid],
                    expand_light_groups=False,
                )
                await self.update_manually_controlled_from_event(
                    switch,
                    eid,
                    force=False,
                )
            except NoSwitchFoundError:
                _LOGGER.debug(
                    "No switch found for entity_id='%s' in 'on' event listener",
                    eid,
                )

            timer = self.auto_reset_manual_control_timers.get(eid)
            if (
                timer is not None
                and timer.is_running()
                and event.time_fired > timer.start_time  # type: ignore[operator]
            ):
                # Restart the auto reset timer
                timer.start()

        if service == SERVICE_TURN_OFF:
            transition = _service_parameters(service_data).get(ATTR_TRANSITION)
            _LOGGER.debug(
                "Detected an 'light.turn_off('%s', transition=%s)' event with context.id='%s'",
                entity_ids,
                transition,
                event.context.id,
            )
            for eid in entity_ids:
                off(eid, event)

        elif service == SERVICE_TURN_ON:
            _LOGGER.debug(
                "Detected an 'light.turn_on('%s')' event with context.id='%s'",
                entity_ids,
                event.context.id,
            )
            for eid in entity_ids:
                await on(eid, event)

        elif service == SERVICE_TOGGLE:
            _LOGGER.debug(
                "Detected an 'light.toggle('%s')' event with context.id='%s'",
                entity_ids,
                event.context.id,
            )
            for eid in entity_ids:
                state = self.hass.states.get(eid)
                if state is None:
                    continue
                self.toggle_event[eid] = event
                if state.state == STATE_ON:  # is turning off
                    off(eid, event)
                elif state.state == STATE_OFF:  # is turning on
                    await on(eid, event)

    async def state_changed_event_listener(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Track 'state_changed' events."""
        entity_id = event.data.get(ATTR_ENTITY_ID, "")
        if entity_id not in self.lights:
            return
        if is_calibration_preview_context(self.hass, entity_id, event.context):
            # Preview has its own snapshots and restoration listener. Normal
            # off/on resets would cancel its split task or clear manual flags.
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        new_on = (
            new_state if new_state is not None and new_state.state == STATE_ON else None
        )
        new_off = (
            new_state
            if new_state is not None and new_state.state == STATE_OFF
            else None
        )
        old_on = (
            old_state if old_state is not None and old_state.state == STATE_ON else None
        )
        old_off = (
            old_state
            if old_state is not None and old_state.state == STATE_OFF
            else None
        )

        if new_on:
            _LOGGER.debug(
                "Detected a '%s' 'state_changed' event: '%s' with context.id='%s'",
                entity_id,
                new_on.attributes,
                new_on.context.id,
            )
            # It is possible to have multiple state change events with the same context.
            # This can happen because a turn_on.light(brightness_pct=100, transition=30)
            # event leads to an instant state change of
            # new_state=dict(brightness=100, ...). However, after polling the light
            # could still only be new_state=dict(brightness=50, ...).
            # Keep the first and recent own reports because the first event might indicate at what
            # settings the light will be later *or* the second event might indicate a
            # final state. The latter case happens for example when a light was
            # called with a color_temp outside of its range (and HA reports the
            # incorrect 'min_kelvin' and 'max_kelvin', which happens e.g., for
            # Philips Hue White GU10 Bluetooth lights).
            last_state: list[State] | None = self.our_last_state_on_change.get(
                entity_id,
            )
            if is_our_context(new_on.context) or self.is_proactively_adapting(new_on.context.id, entity_id):
                if (
                    last_state is not None
                    and last_state[0].context.id == new_on.context.id
                ):
                    _LOGGER.debug(
                        "CircadianProManager: State change event of '%s' is already"
                        " in 'self.our_last_state_on_change' (%s)"
                        " adding this state also",
                        entity_id,
                        new_on.context.id,
                    )
                    self.our_last_state_on_change[entity_id].append(new_on)
                    if len(last_state) > MAX_STATE_HISTORY:
                        del last_state[1:-MAX_STATE_HISTORY + 1]
                else:
                    _LOGGER.debug(
                        "CircadianProManager: New adapt '%s' found for %s",
                        new_on,
                        entity_id,
                    )
                    self.our_last_state_on_change[entity_id] = [new_on]
                    self.start_transition_timer(entity_id)
            # External updates are compared with last_service_data/current state;
            # retaining them here would grow forever while manual control is paused.

        if old_on and new_off:
            # Tracks 'on' → 'off' state changes
            self.on_to_off_event[entity_id] = event
            self.reset(entity_id)
            _LOGGER.debug(
                "Detected an 'on' → 'off' event for '%s' with context.id='%s'",
                entity_id,
                event.context.id,
            )
        elif old_off and new_on:
            # Tracks 'off' → 'on' state changes
            self.off_to_on_event[entity_id] = event
            _LOGGER.debug(
                "Detected an 'off' → 'on' event for '%s' with context.id='%s'",
                entity_id,
                event.context.id,
            )

            if self.is_proactively_adapting(event.context.id, entity_id):
                _LOGGER.debug(
                    "Skipping responding to 'off' → 'on' event for '%s' with context.id='%s' because"
                    " we are already proactively adapting",
                    entity_id,
                    event.context.id,
                )
                # Note: the reset below already happened in _service_interceptor_turn_on_handler
                return

            self.reset(entity_id, reset_manual_control=False)
            lock = self.turn_off_locks.setdefault(entity_id, asyncio.Lock())
            async with lock:
                if await self.just_turned_off(entity_id):
                    # Stop if a rapid 'off' → 'on' → 'off' happens.
                    _LOGGER.debug(
                        "Cancelling adjusting lights for %s",
                        entity_id,
                    )
                    return

            switches = _switches_with_lights(self.hass, [entity_id])
            for switch in switches:
                if switch.is_on:
                    await switch._respond_to_off_to_on_event(
                        entity_id,
                        event,
                    )

    async def update_manually_controlled_from_event(
        self,
        switch: CircadianProSwitch,
        light: str,
        force: bool,
    ) -> None:
        """Check if the light has been manually controlled by the latest turn on event."""
        if not switch._take_over_control or not is_on(self.hass, light):
            # call_service listeners run eagerly, before the light turns on.
            # An explicit initial brightness/color is only a manual takeover
            # when the user enabled adapt_only_on_bare_turn_on; the initial
            # adaptation routes enforce that option separately.
            return

        turn_on_event = self.turn_on_event.get(light)

        if (
            turn_on_event is None
            or self.is_proactively_adapting(turn_on_event.context.id, light)
            or is_our_context(turn_on_event.context)
            or force
        ):
            return

        turn_on_attributes = get_light_control_attributes(
            turn_on_event.data[ATTR_SERVICE_DATA],
        )

        if not turn_on_attributes:
            return

        # Light was already on and 'light.turn_on' was not called by
        # the circadian_pro integration.
        self.add_manual_control_attributes(light, turn_on_attributes)
        switch.fire_manual_control_event(light, turn_on_event.context)
        _LOGGER.debug(
            "'%s' was already on and 'light.turn_on' was not called by the"
            " circadian_pro integration (context.id='%s'), Circadian Pro"
            " will stop adapting %s of the light until the switch or the"
            " light turns off and then on again.",
            light,
            turn_on_event.context.id,
            turn_on_attributes,
        )

    async def update_manually_controlled_from_untracked_change(
        self,
        switch: CircadianProSwitch,
        light: str,
        force: bool,
        context: Context,
    ) -> None:
        """Check if the light has been manually controlled from an untracked change.

        An untracked change is a change that has been made outsideof HA and is
        therefore not visible through events.
        """
        if (
            not switch._take_over_control
            or not switch._detect_non_ha_changes
            or force
            or is_calibration_preview_active(self.hass, light)
        ):
            return

        # Note: This call updates the state of the light
        # so it might suddenly be off.
        significantly_changed_attributes = await self.significant_change(
            switch,
            light,
            context,
        )

        if not significantly_changed_attributes:
            return

        self.add_manual_control_attributes(
            light,
            significantly_changed_attributes,
        )
        switch.fire_manual_control_event(light, context)

    async def significant_change(
        self,
        switch: CircadianProSwitch,
        light: str,
        context: Context,  # just for logging
    ) -> LightControlAttributes:
        """Has the light made a significant change since last update.

        This method will detect changes that were made to the light without
        calling 'light.turn_on', so outside of Home Assistant.
        """
        assert switch._detect_non_ha_changes

        last_service_data = self.last_service_data.get(light)
        if last_service_data is None:
            return LightControlAttributes.NONE
        # Update state and check for a manual change not done in HA.
        # Ensure HASS is correctly updating your light's state with
        # light.turn_on calls if any problems arise. This
        # can happen e.g. using zigbee2mqtt with 'report: false' in device settings.
        await async_update_entity(self.hass, light)
        refreshed_state = self.hass.states.get(light)
        if refreshed_state is None or refreshed_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return LightControlAttributes.NONE

        rgbww = last_service_data.get(ATTR_RGBWW_COLOR)
        if (
            isinstance(rgbww, (tuple, list))
            and len(rgbww) == 5
            and not any(rgbww[:3])
            and any(rgbww[3:])
            and refreshed_state.attributes.get("color_mode") == ColorMode.COLOR_TEMP
        ):
            # Full native white can be reported as COLOR_TEMP instead of RGBWW.
            # Compare with this command's acknowledged state, rather than guessing
            # the integration's white-channel conversion or reusing an old report.
            context_id = self.last_service_context.get(light)
            own_reports = [
                state
                for state in self.our_last_state_on_change.get(light, [])
                if state.context.id == context_id
                and state.attributes.get("color_mode") == ColorMode.COLOR_TEMP
                and state.attributes.get(ATTR_COLOR_TEMP_KELVIN) is not None
            ]
            requested_values = {
                key: tuple(value)
                if key in (ATTR_RGBW_COLOR, ATTR_RGBWW_COLOR)
                else value
                for key, value in last_service_data.items()
                if key not in (ATTR_ENTITY_ID, ATTR_TRANSITION)
            }
            if own_reports:
                acknowledgement = (requested_values, own_reports[-1])
                self.last_service_acknowledgements[light] = acknowledgement
            else:
                acknowledgement = self.last_service_acknowledgements.get(light)
            if acknowledgement is not None and acknowledgement[0] == requested_values:
                last_service_data = dict(acknowledgement[1].attributes)
                if ATTR_BRIGHTNESS in requested_values:
                    last_service_data[ATTR_BRIGHTNESS] = requested_values[ATTR_BRIGHTNESS]
            else:
                # Color is inconclusive until the changed command is confirmed.
                # Brightness remains observable and must not be hidden with it.
                return _attributes_have_changed(
                    light=light,
                    old_attributes={
                        ATTR_BRIGHTNESS: last_service_data.get(ATTR_BRIGHTNESS),
                    },
                    new_attributes={
                        ATTR_BRIGHTNESS: refreshed_state.attributes.get(ATTR_BRIGHTNESS),
                    },
                    context=context,
                )

        changed_attributes = _attributes_have_changed(
            old_attributes=last_service_data,
            new_attributes=refreshed_state.attributes,
            light=light,
            context=context,
        )
        if changed_attributes:
            _LOGGER.debug(
                "%s: State attributes %s of '%s' changed (%s) wrt 'last_service_data' (%s) (context.id=%s)",
                switch._name,
                changed_attributes,
                light,
                refreshed_state.attributes,
                last_service_data,
                context.id,
            )
        else:
            _LOGGER.debug(
                "%s: State attributes of '%s' did not change (%s) wrt 'last_service_data' (%s) (context.id=%s)",
                switch._name,
                light,
                refreshed_state.attributes,
                last_service_data,
                context.id,
            )
        return changed_attributes

    def _off_to_on_state_event_is_from_turn_on(
        self,
        entity_id: str,
        off_to_on_event: Event[EventStateChangedData],
    ) -> bool:
        # Circadian Pro should never turn on lights itself
        if is_our_context(off_to_on_event.context) and not is_our_context(
            off_to_on_event.context,
            "service",  # circadian_pro.apply is allowed to turn on lights
        ):
            _LOGGER.warning(
                "Detected an 'off' → 'on' event for '%s' with context.id='%s' and"
                " event='%s', triggered by the circadian_pro integration itself,"
                " which *should* not happen. If you see this please submit an issue with"
                " your full logs",
                entity_id,
                off_to_on_event.context.id,
                off_to_on_event,
            )
        turn_on_event: Event | None = self.turn_on_event.get(entity_id)
        id_off_to_on = off_to_on_event.context.id
        return turn_on_event is not None and id_off_to_on == turn_on_event.context.id

    async def just_turned_off(  # noqa: PLR0911
        self,
        entity_id: str,
    ) -> bool:
        """Cancel the adjusting of a light if it has just been turned off.

        Possibly the lights just got a 'turn_off' call, however, the light
        is actually still turning off (e.g., because of a 'transition') and
        HA polls the light before the light is 100% off. This might trigger
        a rapid switch 'off' → 'on' → 'off'. To prevent this component
        from interfering on the 'on' state, we make sure to wait at least
        TURNING_OFF_DELAY (or the 'turn_off' transition time) between a
        'off' → 'on' event and then check whether the light is still 'on' or
        if the brightness is still decreasing. Only if it is the case we
        adjust the lights.
        """
        off_to_on_event = self.off_to_on_event[entity_id]
        on_to_off_event = self.on_to_off_event.get(entity_id)

        if on_to_off_event is None:
            _LOGGER.debug(
                "just_turned_off: No 'on' → 'off' state change has been registered before for '%s'."
                " It's possible that the light was already on when Home Assistant was turned on.",
                entity_id,
            )
            return False

        if off_to_on_event.context.id == on_to_off_event.context.id:
            _LOGGER.debug(
                "just_turned_off: 'on' → 'off' state change has the same context.id as the"
                " 'off' → 'on' state change for '%s'. This is probably a false positive.",
                entity_id,
            )
            return True

        id_on_to_off = on_to_off_event.context.id

        turn_off_event = self.turn_off_event.get(entity_id)
        if turn_off_event is not None:
            transition = _service_parameters(turn_off_event.data[ATTR_SERVICE_DATA]).get(ATTR_TRANSITION)
        else:
            transition = None

        if self._off_to_on_state_event_is_from_turn_on(entity_id, off_to_on_event):
            is_toggle = off_to_on_event == self.toggle_event.get(entity_id)
            from_service = "light.toggle" if is_toggle else "light.turn_on"
            _LOGGER.debug(
                "just_turned_off: State change 'off' → 'on' triggered by '%s'",
                from_service,
            )
            return False

        if (
            turn_off_event is not None
            and id_on_to_off == turn_off_event.context.id
            and transition is not None  # 'turn_off' is called with transition=...
        ):
            # State change 'on' → 'off' and 'light.turn_off(..., transition=...)' come
            # from the same event, so wait at least the 'turn_off' transition time.
            delay = max(transition, TURNING_OFF_DELAY)
        else:
            # State change 'off' → 'on' happened because the light state was set.
            # Possibly because of polling.
            delay = TURNING_OFF_DELAY

        delta_time = (dt_util.utcnow() - on_to_off_event.time_fired).total_seconds()
        if delta_time > delay:
            _LOGGER.debug(
                "just_turned_off: delta_time='%s' > delay='%s'",
                delta_time,
                delay,
            )
            return False

        # Here we could just return True but because we want to prevent any updates
        # from happening to this light (through async_track_time_interval or
        # sleep_state) for some time, we wait below until the light
        # is 'off' or the time has passed.

        delay -= delta_time  # delta_time has passed since the 'off' → 'on' event
        _LOGGER.debug(
            "just_turned_off: Waiting with adjusting '%s' for %s",
            entity_id,
            delay,
        )
        total_sleep = 0
        for _ in range(3):
            # It can happen that the actual transition time is longer than the
            # specified time in the 'turn_off' service.
            total_sleep += delay
            task = self.hass.async_create_task(asyncio.sleep(delay))
            self.sleep_tasks[entity_id] = task
            try:
                await task
            except asyncio.CancelledError:  # 'light.turn_on' has been called
                _LOGGER.debug(
                    "just_turned_off: Sleep task is cancelled due to 'light.turn_on('%s')' call",
                    entity_id,
                )
                return False
            finally:
                if self.sleep_tasks.get(entity_id) is task:
                    self.sleep_tasks.pop(entity_id, None)

            if not is_on(self.hass, entity_id):
                _LOGGER.debug(
                    "just_turned_off: '%s' is off after %s seconds, cancelling adaptation",
                    entity_id,
                    total_sleep,
                )
                return True
            delay = TURNING_OFF_DELAY  # next time only wait this long

        if transition is not None:
            # Always ignore when there's a 'turn_off' transition.
            # Because it seems like HA cannot detect whether a light is
            # transitioning into 'off'. Maybe needs some discussion/input?
            return True

        # Now we assume that the lights are still on and they were intended
        # to be on.
        _LOGGER.debug(
            "just_turned_off: '%s' is still on after %s seconds, assuming it was intended to be on",
            entity_id,
            total_sleep,
        )
        return False

    def _mark_manual_control_if_non_bare_turn_on(
        self,
        entity_id: str,
        service_data: ServiceData,
    ) -> bool:
        _LOGGER.debug(
            "_mark_manual_control_if_non_bare_turn_on: entity_id='%s', service_data='%s'",
            entity_id,
            service_data,
        )
        manual_control_attributes = get_light_control_attributes(service_data)

        if manual_control_attributes:
            self.set_manual_control_attributes(entity_id, manual_control_attributes)
            return True

        return False


class _AsyncSingleShotTimer:
    def __init__(
        self,
        hass: HomeAssistant,
        delay: float,
        callback: Callable[[], None | Any],
    ) -> None:
        """Initialize the timer."""
        self.hass = hass
        self.delay = delay
        self.callback = callback
        self.task: asyncio.Task[None] | None = None
        self.start_time: datetime.datetime | None = None

    async def _run(self) -> None:
        """Run the timer. Don't call this directly, use start() instead."""
        await asyncio.sleep(self.delay)
        if self.callback:
            result = self.callback()
            if isawaitable(result):
                await result

    def is_running(self) -> bool:
        """Return whether the timer is running."""
        return self.task is not None and not self.task.done()

    def start(self) -> None:
        """Start the timer."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
        # Set start_time before creating task to avoid race condition
        # where is_running() returns True but start_time is still None
        # (reference removed)
        self.start_time = dt_util.utcnow()
        self.task = self.hass.async_create_task(self._run())

    def cancel(self) -> None:
        """Cancel the timer."""
        if self.task:
            self.task.cancel()
        self.start_time = None

    def remaining_time(self) -> float:
        """Return the remaining time before the timer expires."""
        if self.start_time is not None:
            elapsed_time = (dt_util.utcnow() - self.start_time).total_seconds()
            return max(0, self.delay - elapsed_time)
        return 0
