"""Config flow for Circadian Pro integration."""
# Modified for Circadian Pro. See NOTICE and LICENSE-APACHE-2.0.txt.

import logging
from copy import deepcopy
from typing import Any

import voluptuous as vol
try:
    from probatio import Invalid as SelectorInvalid
except ImportError:  # Home Assistant versions before the validator migration.
    SelectorInvalid = vol.Invalid
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import (  # pylint: disable=unused-import
    CONF_CAL_RGB_NEUTRAL_R,
    CONF_CAL_RGB_NEUTRAL_G,
    CONF_CAL_RGB_NEUTRAL_B,
    CONF_CAL_RGB_WARM_R,
    CONF_CAL_RGB_WARM_G,
    CONF_CAL_RGB_WARM_B,
    CONF_CAL_WHITE_INTENSITY_NEUTRAL,
    CONF_CAL_WHITE_INTENSITY_WARM,
    CONF_CAL_WHITE_KELVIN_NEUTRAL,
    DEFAULT_CAL_WHITE_KELVIN_NEUTRAL,
    CONF_WHITE_CW_KELVIN,
    DEFAULT_WHITE_CW_KELVIN,
    CONF_WHITE_W_KELVIN,
    DEFAULT_WHITE_W_KELVIN,
    CONF_LIGHTS,
    CONF_SLEEP_RGB_OR_COLOR_TEMP,
    CONF_SLEEP_ACTIVATION_MODE,
    DOMAIN,
    EXTRA_VALIDATION,
    NONE_STR,
    VALIDATION_TUPLES,
    box_int,
    replace_none_str,
    validate_cross_options,
)

_LOGGER = logging.getLogger(__name__)


def validate_entry(config_entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Validate and merge config_entry.data + config_entry.options.

    IMPORTANT: This helper intentionally does not import switch.py to avoid
    pulling in runtime-only dependencies when HA loads the config flow.
    """
    data: dict[str, Any] = {key: default for key, default, _ in VALIDATION_TUPLES}
    data.update(config_entry.options)
    data.update(config_entry.data)
    data = {key: replace_none_str(value) for key, value in data.items()}

    # Coerce / validate types for unserializable validators
    for key, (validate_value, _) in EXTRA_VALIDATION.items():
        value = data.get(key)
        if value is not None and value != NONE_STR:
            data[key] = validate_value(value)

    # Backward compatibility: infer sleep_activation_mode from legacy options
    # if the new option is not present in the entry yet.
    if (
        CONF_SLEEP_ACTIVATION_MODE not in config_entry.options
        and CONF_SLEEP_ACTIVATION_MODE not in config_entry.data
    ):
        if data.get("sleep_schedule_enabled") is True:
            data[CONF_SLEEP_ACTIVATION_MODE] = "schedule"
        elif data.get("transition_until_sleep") is True:
            data[CONF_SLEEP_ACTIVATION_MODE] = "sun"
        else:
            data[CONF_SLEEP_ACTIVATION_MODE] = "disabled"

    validate_cross_options(data)
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Circadian Pro."""

    VERSION = 1

    source_options: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        if user_input is None and self._async_current_entries():
            return await self.async_step_menu()
        return await self.async_step_wait_for_name(user_input)

    async def async_step_menu(self, user_input: dict[str, Any] | None = None):
        """Handle the menu step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["action"] == "new":
                self.source_options = None
                return await self.async_step_wait_for_name()
            else:
                entry_id = user_input["action"]
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry is not None:
                    self.source_options = deepcopy(validate_entry(entry))
                    self.source_options.pop(CONF_NAME, None)
                    for key, (_validate, serialize) in EXTRA_VALIDATION.items():
                        value = self.source_options.get(key)
                        if value is not None:
                            self.source_options[key] = serialize(value)
                    return await self.async_step_wait_for_name()
                self.source_options = None
                errors["base"] = "profile_missing"

        entries = self._async_current_entries()
        options = {"new": "Create new instance"}
        for entry in entries:
            options[entry.entry_id] = f"Duplicate '{entry.title}'"

        return self.async_show_form(
            step_id="menu",
            data_schema=vol.Schema(
                {vol.Required("action", default="new"): vol.In(options)},
            ),
            errors=errors,
        )

    async def async_step_wait_for_name(self, user_input: dict[str, Any] | None = None):
        """Handle the name step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            else:
                try:
                    await self.async_set_unique_id(name)
                    self._abort_if_unique_id_configured()
                except AbortFlow as err:
                    if err.reason not in {"already_configured", "already_in_progress"}:
                        raise
                    errors[CONF_NAME] = "unique_name"
                else:
                    return self.async_create_entry(
                        title=name,
                        data={CONF_NAME: name},
                        options=dict(self.source_options or {}),
                    )

        name_default = user_input.get(CONF_NAME, "") if user_input else ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default=name_default): str}),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any] | None = None):
        """Handle configuration by YAML file."""
        if user_input is None:
            return self.async_abort(reason="no_data")

        await self.async_set_unique_id(user_input[CONF_NAME])
        # Keep a list of switches that are configured via YAML
        data = self.hass.data.setdefault(DOMAIN, {})
        data.setdefault("__yaml__", set()).add(self.unique_id)

        for entry in self._async_current_entries():
            if entry.unique_id == self.unique_id:
                if self.hass.config_entries.async_update_entry(entry, data=user_input):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                self._abort_if_unique_id_configured()

        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> "OptionsFlowHandler":
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


def validate_options(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate the options in the OptionsFlow.

    This is an extra validation step because the validators
    in EXTRA_VALIDATION cannot be serialized to json.
    """
    for key, (_validate, _) in EXTRA_VALIDATION.items():
        # these are unserializable validators
        value = user_input.get(key)
        try:
            if value is not None and value != NONE_STR:
                _validate(value)
        except (vol.Invalid, SelectorInvalid):
            _LOGGER.exception("Configuration option %s=%s is incorrect", key, value)
            errors["base"] = "option_error"

    if not errors:
        try:
            validate_cross_options(user_input)
        except (vol.Invalid, SelectorInvalid) as err:
            error_key = str(err)
            if error_key not in {
                "invalid_brightness_range",
                "invalid_color_temp_range",
                "invalid_brightness_window",
                "invalid_sunrise_bounds",
                "invalid_sunset_bounds",
                "invalid_white_channel_range",
                "invalid_white_neutral_range",
                "invalid_sleep_schedule",
                "invalid_solar_times",
            }:
                error_key = "option_error"
            errors["base"] = error_key


class OptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle an options flow for Circadian Pro."""

    _base_options: dict[str, Any] | None = None
    _cal_neutral: dict[str, Any] | None = None
    _cal_warm: dict[str, Any] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Step 1: general settings."""
        conf = self.config_entry
        if conf.source == config_entries.SOURCE_IMPORT:
            return self.async_abort(reason="yaml_configuration")
        data = validate_entry(conf)

        errors: dict[str, str] = {}
        if user_input is not None:
            validate_options(user_input, errors)
            if not errors:
                self._base_options = dict(conf.options)
                self._base_options.update(user_input)
                if self._uses_rgbww():
                    return await self.async_step_white_channels()
                if self._uses_white():
                    return await self.async_step_white_w_kelvin()
                return await self.async_step_cal_neutral()

        # Validate that all configured lights still exist
        all_lights = set(self.hass.states.async_entity_ids("light"))
        for configured_light in data[CONF_LIGHTS]:
            if configured_light not in all_lights:
                errors = {CONF_LIGHTS: "entity_missing"}
                _LOGGER.error(
                    "%s: light entity %s is configured, but was not found",
                    data[CONF_NAME],
                    configured_light,
                )

        to_replace: dict[str, Any] = {
            CONF_LIGHTS: EntitySelector(
                EntitySelectorConfig(
                    domain="light",
                    multiple=True,
                ),
            ),
            CONF_SLEEP_RGB_OR_COLOR_TEMP: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["rgb_color", "color_temp"],
                    translation_key=CONF_SLEEP_RGB_OR_COLOR_TEMP,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            CONF_SLEEP_ACTIVATION_MODE: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["disabled", "sun", "schedule"],
                    translation_key=CONF_SLEEP_ACTIVATION_MODE,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            "sleep_schedule_start": selector.TimeSelector(selector.TimeSelectorConfig()),
            "sleep_schedule_end": selector.TimeSelector(selector.TimeSelectorConfig()),
        }

        # Hide calibration + white-range overrides from this step to keep it clean.
        calibration_keys = {
            # Calibración RGBW (dos puntos)
            CONF_CAL_RGB_NEUTRAL_R, CONF_CAL_RGB_NEUTRAL_G, CONF_CAL_RGB_NEUTRAL_B,
            CONF_CAL_RGB_WARM_R, CONF_CAL_RGB_WARM_G, CONF_CAL_RGB_WARM_B,
            CONF_CAL_WHITE_INTENSITY_NEUTRAL, CONF_CAL_WHITE_INTENSITY_WARM,
            CONF_WHITE_W_KELVIN,
            CONF_WHITE_CW_KELVIN, CONF_CAL_WHITE_KELVIN_NEUTRAL,
            # Legacy calibration options from Circadian Pro (not used in Circadian Pro)
            "rgb_calibration_r", "rgb_calibration_g", "rgb_calibration_b",
            "combine_rgb_scale", "combine_white_scale", "rgbw_fixed_white_kelvin",
        }

        # Keep the UI simple: show only the most-used general settings here.
        allowed_init_keys = {
            "lights",
            "interval",
            "transition",
            "initial_transition",
            "min_brightness",
            "max_brightness",
            "min_color_temp",
            "max_color_temp",
            "only_once",
            "detect_non_ha_changes",
            "intercept",
            "multi_light_intercept",
            "separate_turn_on_commands",
            "send_split_delay",
            "skip_redundant_commands",
            "take_over_control",
            "autoreset_control_seconds",
            "adapt_delay",
            "sunrise_time",
            "sunset_time",
            "sunrise_offset",
            "sunset_offset",
            "min_sunrise_time",
            "max_sunrise_time",
            "min_sunset_time",
            "max_sunset_time",
            "sleep_brightness",
            "sleep_rgb_or_color_temp",
            "sleep_color_temp",
            "sleep_rgb_color",
            "sleep_activation_mode",
            "sleep_schedule_start",
            "sleep_schedule_end",
            "brightness_mode",
            "brightness_mode_time_light",
            "brightness_mode_time_dark",
        }

        options_schema: dict[Any, Any] = {}
        for name, default, validation in VALIDATION_TUPLES:
            if name in calibration_keys or name not in allowed_init_keys:
                continue
            selected_default = (user_input or {}).get(name, data.get(name, default))
            if name in EXTRA_VALIDATION and selected_default not in (None, NONE_STR):
                validate_value, serialize = EXTRA_VALIDATION[name]
                try:
                    selected_default = serialize(validate_value(selected_default))
                except (vol.Invalid, SelectorInvalid, ValueError, TypeError):
                    pass  # Keep the submitted value visible alongside its error.
            key = vol.Optional(name, default=selected_default)
            value = to_replace.get(name, validation)
            options_schema[key] = value

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(options_schema),
            errors=errors,
            last_step=False,
        )


    def _uses_rgbww(self) -> bool:
        """Show dual-white controls only for selected RGBWW lights."""
        options = self._base_options
        if options is None:
            options = validate_entry(self.config_entry)
        return any(
            "rgbww" in (state.attributes.get("supported_color_modes") or [])
            for light in options.get(CONF_LIGHTS, [])
            if (state := self.hass.states.get(light)) is not None
        )

    def _uses_white(self) -> bool:
        """Show white controls only when selected lights expose white channels."""
        options = self._base_options
        if options is None:
            options = validate_entry(self.config_entry)
        return any(
            {"rgbw", "rgbww"}.intersection(
                state.attributes.get("supported_color_modes") or []
            )
            for light in options.get(CONF_LIGHTS, [])
            if (state := self.hass.states.get(light)) is not None
        )

    async def async_step_white_channels(self, user_input: dict[str, Any] | None = None):
        """Handle the physical warm/cold white temperatures."""
        return await self.async_step_white_w_kelvin(user_input)

    def _draft_options(self) -> dict[str, Any]:
        """Merge the current configuration with unsaved calibration values."""
        options = validate_entry(self.config_entry)
        options.update(self._base_options or {})
        options.update(self._cal_neutral or {})
        options.update(self._cal_warm or {})
        return options

    async def async_step_white_w_kelvin(self, user_input: dict[str, Any] | None = None):
        """Step 2: physical W, or warm/cold white, temperatures."""
        conf = self.config_entry
        options = self._base_options if self._base_options is not None else conf.options
        uses_rgbww = self._uses_rgbww()
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = dict(options)
            candidate.update(user_input)
            # Validate the mixture at its own step, after editing the endpoints.
            candidate.pop(CONF_CAL_WHITE_KELVIN_NEUTRAL, None)
            validate_options(candidate, errors)
            if not errors:
                # guardamos este paso como parte de la configuración base
                self._base_options = dict(self._base_options or {})
                self._base_options.update(user_input)
                return await self.async_step_cal_neutral()

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_WHITE_W_KELVIN,
                default=options.get(CONF_WHITE_W_KELVIN, DEFAULT_WHITE_W_KELVIN),
            ): validations[CONF_WHITE_W_KELVIN],
        }
        if uses_rgbww:
            schema[
                vol.Optional(
                    CONF_WHITE_CW_KELVIN,
                    default=options.get(CONF_WHITE_CW_KELVIN, DEFAULT_WHITE_CW_KELVIN) or 6500,
                )
            ] = box_int(1000, 10000, step=50)

        return self.async_show_form(
            step_id="white_channels" if uses_rgbww else "white_w_kelvin",
            data_schema=vol.Schema(schema),
            errors=errors,
            last_step=False,
        )

    async def async_step_cal_neutral(self, user_input: dict[str, Any] | None = None):
        """Step 2: calibration at 4000K (neutral white)."""
        options = self._draft_options()
        errors: dict[str, str] = {}
        if user_input is not None:
            submitted = dict(user_input)
            candidate = dict(options)
            candidate.update(submitted)
            validate_options(candidate, errors)
            self._cal_neutral = submitted
            options = candidate
            if not errors:
                return await self.async_step_cal_warm()

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {}

        def add_opt(name: str, default_fallback: Any) -> None:
            schema[vol.Optional(name, default=options.get(name, default_fallback))] = validations[name]

        # Calibración 4000K
        add_opt(CONF_CAL_RGB_NEUTRAL_R, 255)
        add_opt(CONF_CAL_RGB_NEUTRAL_G, 255)
        add_opt(CONF_CAL_RGB_NEUTRAL_B, 255)
        if self._uses_white():
            add_opt(CONF_CAL_WHITE_INTENSITY_NEUTRAL, 100)
        if self._uses_rgbww():
            warm_white = int(options.get(CONF_WHITE_W_KELVIN, DEFAULT_WHITE_W_KELVIN))
            cold_white = int(options.get(CONF_WHITE_CW_KELVIN, DEFAULT_WHITE_CW_KELVIN) or 6500)
            neutral_white = options.get(
                CONF_CAL_WHITE_KELVIN_NEUTRAL, DEFAULT_CAL_WHITE_KELVIN_NEUTRAL
            )
            neutral_default = min(cold_white, max(warm_white, neutral_white or 4000))
            schema[
                vol.Optional(CONF_CAL_WHITE_KELVIN_NEUTRAL, default=neutral_default)
            ] = box_int(warm_white, cold_white, step=50)
        return self.async_show_form(
            step_id="cal_neutral",
            data_schema=vol.Schema(schema),
            errors=errors,
            last_step=False,
        )

    async def async_step_cal_warm(self, user_input: dict[str, Any] | None = None):
        """Step 3: calibration at 1000K (or 'warm' for CCT-only profiles)."""
        options = self._draft_options()
        errors: dict[str, str] = {}
        if user_input is not None:
            submitted = dict(user_input)
            candidate = dict(options)
            candidate.update(submitted)
            validate_options(candidate, errors)
            self._cal_warm = submitted
            options = candidate
            if not errors:
                data_out = dict(self._base_options or {})
                data_out.update(self._cal_neutral or {})
                data_out.update(self._cal_warm or {})
                return self.async_create_entry(title="", data=data_out)

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {}

        def add_opt(name: str, default_fallback: Any) -> None:
            schema[vol.Optional(name, default=options.get(name, default_fallback))] = validations[name]

        # Calibración 1000K (cálido)
        add_opt(CONF_CAL_RGB_WARM_R, 255)
        add_opt(CONF_CAL_RGB_WARM_G, 56)
        add_opt(CONF_CAL_RGB_WARM_B, 0)
        if self._uses_white():
            add_opt(CONF_CAL_WHITE_INTENSITY_WARM, 0)
        return self.async_show_form(
            step_id="cal_warm",
            data_schema=vol.Schema(schema),
            errors=errors,
            last_step=True,
        )
