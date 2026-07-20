"""Config flow for Circadian Pro integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
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
    CONF_WHITE_W_KELVIN,
    DEFAULT_WHITE_W_KELVIN,
    CONF_LIGHTS,
    CONF_SLEEP_RGB_OR_COLOR_TEMP,
    CONF_SLEEP_ACTIVATION_MODE,
    DOMAIN,
    EXTRA_VALIDATION,
    NONE_STR,
    VALIDATION_TUPLES,
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

    validate_cross_options(data)

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
        if user_input is not None:
            if user_input["action"] != "new":
                entry_id = user_input["action"]
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry:
                    self.source_options = dict(entry.options)
            return await self.async_step_wait_for_name()

        entries = self._async_current_entries()
        options = {"new": "Create new instance"}
        for entry in entries:
            options[entry.entry_id] = f"Duplicate '{entry.title}'"

        return self.async_show_form(
            step_id="menu",
            data_schema=vol.Schema(
                {vol.Required("action", default="new"): vol.In(options)},
            ),
        )

    async def async_step_wait_for_name(self, user_input: dict[str, Any] | None = None):
        """Handle the name step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_NAME])
            self._abort_if_unique_id_configured()
            options = self.source_options
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=user_input,
                options=options,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME): str}),
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
                self.hass.config_entries.async_update_entry(entry, data=user_input)
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
        except vol.Invalid:
            _LOGGER.exception("Configuration option %s=%s is incorrect", key, value)
            errors["base"] = "option_error"

    if not errors:
        try:
            validate_cross_options(user_input)
        except vol.Invalid as err:
            error_key = str(err)
            if error_key not in {
                "invalid_brightness_range",
                "invalid_color_temp_range",
                "invalid_brightness_window",
                "invalid_sunrise_bounds",
                "invalid_sunset_bounds",
            }:
                error_key = "option_error"
            errors["base"] = error_key

    # If the user selected fixed schedule, ensure start/end are present
    # (they can cross midnight, e.g. 23:00 -> 05:00).
    mode = user_input.get(CONF_SLEEP_ACTIVATION_MODE, "disabled")
    if mode == "schedule":
        start = user_input.get("sleep_schedule_start")
        end = user_input.get("sleep_schedule_end")
        if not start or start == NONE_STR or not end or end == NONE_STR:
            errors["base"] = "option_error"



class OptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle an options flow for Circadian Pro."""

    _base_options: dict[str, Any] | None = None
    _cal_neutral: dict[str, Any] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Step 1: general settings."""
        conf = self.config_entry
        data = validate_entry(conf)
        if conf.source == config_entries.SOURCE_IMPORT:
            return self.async_show_form(step_id="init", data_schema=None)

        errors: dict[str, str] = {}
        if user_input is not None:
            validate_options(user_input, errors)
            if not errors:
                self._base_options = dict(user_input)
                return await self.async_step_white_w_kelvin()

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
                    options=[
                        {"value": "rgb_color", "label": "Definir con RGB"},
                        {"value": "color_temp", "label": "Definir con temperatura de color"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            CONF_SLEEP_ACTIVATION_MODE: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "disabled", "label": "Desactivado"},
                        {"value": "sun", "label": "Por amanecer y atardecer"},
                        {"value": "schedule", "label": "Por franja horaria"},
                    ],
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
            key = vol.Optional(name, default=conf.options.get(name, default))
            value = to_replace.get(name, validation)
            options_schema[key] = value

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(options_schema),
            errors=errors,
        )


    async def async_step_white_w_kelvin(self, user_input: dict[str, Any] | None = None):
        """Step 2: Kelvin real del canal blanco (W) para RGBW."""
        conf = self.config_entry
        errors: dict[str, str] = {}

        if user_input is not None:
            validate_options(user_input, errors)
            if not errors:
                # guardamos este paso como parte de la configuración base
                self._base_options = dict(self._base_options or {})
                self._base_options.update(user_input)
                return await self.async_step_cal_neutral()

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_WHITE_W_KELVIN,
                default=conf.options.get(CONF_WHITE_W_KELVIN, DEFAULT_WHITE_W_KELVIN),
            ): validations[CONF_WHITE_W_KELVIN],
        }

        return self.async_show_form(
            step_id="white_w_kelvin",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_cal_neutral(self, user_input: dict[str, Any] | None = None):
        """Step 2: calibration at 4000K (neutral white)."""
        conf = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            validate_options(user_input, errors)
            if not errors:
                self._cal_neutral = dict(user_input)
                return await self.async_step_cal_warm()

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {}

        def add_opt(name: str, default_fallback: Any) -> None:
            schema[vol.Optional(name, default=conf.options.get(name, default_fallback))] = validations[name]

        # Calibración 4000K
        add_opt(CONF_CAL_RGB_NEUTRAL_R, 255)
        add_opt(CONF_CAL_RGB_NEUTRAL_G, 255)
        add_opt(CONF_CAL_RGB_NEUTRAL_B, 255)
        add_opt(CONF_CAL_WHITE_INTENSITY_NEUTRAL, 100)

        return self.async_show_form(
            step_id="cal_neutral",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_cal_warm(self, user_input: dict[str, Any] | None = None):
        """Step 3: calibration at 1000K (or 'warm' for CCT-only profiles)."""
        conf = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            validate_options(user_input, errors)
            if not errors:
                data_out = dict(self._base_options or {})
                data_out.update(self._cal_neutral or {})
                data_out.update(user_input)
                return self.async_create_entry(title="", data=data_out)

        validations = {name: validation for name, _default, validation in VALIDATION_TUPLES}
        schema: dict[Any, Any] = {}

        def add_opt(name: str, default_fallback: Any) -> None:
            schema[vol.Optional(name, default=conf.options.get(name, default_fallback))] = validations[name]

        # Calibración 1000K (cálido)
        add_opt(CONF_CAL_RGB_WARM_R, 255)
        add_opt(CONF_CAL_RGB_WARM_G, 56)
        add_opt(CONF_CAL_RGB_WARM_B, 0)
        add_opt(CONF_CAL_WHITE_INTENSITY_WARM, 0)

        return self.async_show_form(
            step_id="cal_warm",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
