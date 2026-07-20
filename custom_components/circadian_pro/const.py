"""Constants for the Circadian Pro integration."""

from datetime import time, timedelta
from enum import Enum
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import VALID_TRANSITION
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.helpers import selector

ICON_MAIN = "mdi:theme-light-dark"
ICON_BRIGHTNESS = "mdi:brightness-4"
ICON_COLOR_TEMP = "mdi:sun-thermometer"
ICON_SLEEP = "mdi:sleep"

DOMAIN = "circadian_pro"

# Documentation strings (shown in services.yaml and used by UI helpers)
DOCS = {CONF_ENTITY_ID: "Entity ID of the switch. "}

# --- Light hardware/profile selection (instance-wide) ---
# These profiles are designed primarily for WLED-based lights.
# The integration uses two reference points to correct the Kelvin->channel mapping.
CAL_POINT_WARM = "warm"  # 1000K for RGB-capable profiles
CAL_POINT_NEUTRAL = "neutral"  # 4000K

# RGB calibration at 4000K and 1000K (0-255 each channel)
# We store them as separate channels so the UI can show 3 sliders per point.
CONF_CAL_RGB_NEUTRAL_R = "cal_rgb_neutral_r"
CONF_CAL_RGB_NEUTRAL_G = "cal_rgb_neutral_g"
CONF_CAL_RGB_NEUTRAL_B = "cal_rgb_neutral_b"

CONF_CAL_RGB_WARM_R = "cal_rgb_warm_r"
CONF_CAL_RGB_WARM_G = "cal_rgb_warm_g"
CONF_CAL_RGB_WARM_B = "cal_rgb_warm_b"

DEFAULT_CAL_RGB_NEUTRAL_R = 255
DEFAULT_CAL_RGB_NEUTRAL_G = 255
DEFAULT_CAL_RGB_NEUTRAL_B = 255

DEFAULT_CAL_RGB_WARM_R = 255
DEFAULT_CAL_RGB_WARM_G = 56
DEFAULT_CAL_RGB_WARM_B = 0

DOCS[CONF_CAL_RGB_NEUTRAL_R] = "Calibration at 4000K: Red channel (0-255)."
DOCS[CONF_CAL_RGB_NEUTRAL_G] = "Calibration at 4000K: Green channel (0-255)."
DOCS[CONF_CAL_RGB_NEUTRAL_B] = "Calibration at 4000K: Blue channel (0-255)."

DOCS[CONF_CAL_RGB_WARM_R] = "Calibration at 1000K: Red channel (0-255)."
DOCS[CONF_CAL_RGB_WARM_G] = "Calibration at 1000K: Green channel (0-255)."
DOCS[CONF_CAL_RGB_WARM_B] = "Calibration at 1000K: Blue channel (0-255)."

# White intensity (%) calibration at 1000K and 4000K (for RGBW / RGBCCT / CCT)
CONF_CAL_WHITE_INTENSITY_WARM = "cal_white_intensity_warm"
CONF_CAL_WHITE_INTENSITY_NEUTRAL = "cal_white_intensity_neutral"
DEFAULT_CAL_WHITE_INTENSITY_WARM = 0
DEFAULT_CAL_WHITE_INTENSITY_NEUTRAL = 100
DOCS[CONF_CAL_WHITE_INTENSITY_WARM] = (
    "White intensity at 1000K (0-100%). Lower values allow RGB to dominate for warm/orange."
)
DOCS[CONF_CAL_WHITE_INTENSITY_NEUTRAL] = "White intensity at 4000K (0-100%)."

# Fixed white-channel Kelvin (for RGBW strips with a single W LED temperature)
# Example: if the W channel is 2700K, we keep the W intensity at the neutral level
# until 2700K, and only start reducing it below that point (towards the 1000K calibration).
CONF_WHITE_W_KELVIN = "white_w_kelvin"
DEFAULT_WHITE_W_KELVIN = 3000
DOCS[CONF_WHITE_W_KELVIN] = (
    "Actual Kelvin of the W (white) channel for RGBW strips (e.g., 2700). "
    "Used to keep W at the neutral intensity until this Kelvin, then fade it down towards the warm point."
)


class TakeOverControlMode(Enum):
    """Modes for pausing adaptation when control of a light is taken over externally."""

    PAUSE_ALL = "pause_all"
    PAUSE_CHANGED = "pause_changed"

CONF_NAME, DEFAULT_NAME = "name", "default"
DOCS[CONF_NAME] = "Display name for this switch. "

CONF_LIGHTS, DEFAULT_LIGHTS = "lights", []
DOCS[CONF_LIGHTS] = "List of light entity_ids to be controlled (may be empty). "

CONF_DETECT_NON_HA_CHANGES, DEFAULT_DETECT_NON_HA_CHANGES = (
    "detect_non_ha_changes",
    False,
)
DOCS[CONF_DETECT_NON_HA_CHANGES] = (
    "Detects and halts adaptations for non-light.turn_on state changes. "
    "Needs take_over_control enabled.  "
    "Caution:  Some lights might falsely indicate an 'on' state, which could result "
    "in lights turning on unexpectedly. "
    "Note that this calls homeassistant.update_entity every interval! "
    "Disable this feature if you encounter such issues."
)

CONF_INCLUDE_CONFIG_IN_ATTRIBUTES, DEFAULT_INCLUDE_CONFIG_IN_ATTRIBUTES = (
    "include_config_in_attributes",
    False,
)
DOCS[CONF_INCLUDE_CONFIG_IN_ATTRIBUTES] = (
    "Show all options as attributes on the switch in "
    "Home Assistant when set to true. "
)

CONF_INITIAL_TRANSITION, DEFAULT_INITIAL_TRANSITION = "initial_transition", 0
DOCS[CONF_INITIAL_TRANSITION] = (
    "Duration of the first transition when lights turn "
    "from off to on in seconds. "
)

CONF_SLEEP_TRANSITION, DEFAULT_SLEEP_TRANSITION = "sleep_transition", 90
DOCS[CONF_SLEEP_TRANSITION] = (
    'Duration of transition when "sleep mode" is toggled in seconds. '
)

CONF_INTERVAL, DEFAULT_INTERVAL = "interval", 90
DOCS[CONF_INTERVAL] = "Frequency to adapt the lights, in seconds. "

CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS = "max_brightness", 100
DOCS[CONF_MAX_BRIGHTNESS] = "Maximum brightness percentage. "

CONF_MAX_COLOR_TEMP, DEFAULT_MAX_COLOR_TEMP = "max_color_temp", 4000
DOCS[CONF_MAX_COLOR_TEMP] = "Coldest color temperature in Kelvin. "

CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS = "min_brightness", 50
DOCS[CONF_MIN_BRIGHTNESS] = "Minimum brightness percentage. "

CONF_MIN_COLOR_TEMP, DEFAULT_MIN_COLOR_TEMP = "min_color_temp", 1000
DOCS[CONF_MIN_COLOR_TEMP] = "Warmest color temperature in Kelvin. "

CONF_ONLY_ONCE, DEFAULT_ONLY_ONCE = "only_once", False
DOCS[CONF_ONLY_ONCE] = (
    "Adapt lights only when they are turned on (true) or keep adapting them "
    "(false). "
)

CONF_ADAPT_ONLY_ON_BARE_TURN_ON, DEFAULT_ADAPT_ONLY_ON_BARE_TURN_ON = (
    "adapt_only_on_bare_turn_on",
    False,
)
DOCS[CONF_ADAPT_ONLY_ON_BARE_TURN_ON] = (
    "When turning lights on initially. If set to true, Circadian Pro adapts only if light.turn_on is "
    "invoked without specifying color or brightness.  "
    "This e.g., prevents adaptation when activating a scene. "
    "If false, Circadian Pro adapts regardless of the presence of color or brightness in the initial service_data. "
    "Needs take_over_control enabled. "
)

CONF_PREFER_RGB_COLOR, DEFAULT_PREFER_RGB_COLOR = "prefer_rgb_color", False
DOCS[CONF_PREFER_RGB_COLOR] = (
    "Whether to prefer RGB color adjustment over "
    "light color temperature when possible. "
)

# --- Advanced color mixing for RGBW / RGBWW (RGBCCT) lights ---
# When enabled, Circadian Pro will try to set RGB *and* white channels at the same time
# (if the light supports it) by using rgbw_color or rgbww_color instead of choosing
# between color_temp or rgb_color.
CONF_COMBINE_RGBCTT_RGBW, DEFAULT_COMBINE_RGBCTT_RGBW = (
    "combine_rgbctt_or_rgbw",
    False,
)
DOCS[CONF_COMBINE_RGBCTT_RGBW] = (
    "Combine RGB + white channels simultaneously for RGBW / RGBWW (RGBCCT) lights. "
    "This enables stronger whites by mixing RGB with CW/WW (or W). "
)

# Scaling factors (percent). Values >100% allow 'boosting' channels (clamped to 255).
CONF_COMBINE_RGB_SCALE, DEFAULT_COMBINE_RGB_SCALE = "combine_rgb_scale", 100
DOCS[CONF_COMBINE_RGB_SCALE] = (
    "Scale (boost) the RGB contribution when using the combined RGB+white mode (0-200%). "
)

CONF_COMBINE_WHITE_SCALE, DEFAULT_COMBINE_WHITE_SCALE = "combine_white_scale", 100
DOCS[CONF_COMBINE_WHITE_SCALE] = (
    "Scale (boost) the white-channel contribution when using the combined RGB+white mode (0-200%). "
)

# For RGBW strips, the 'W' channel is usually a fixed phosphor white.
CONF_RGBW_FIXED_WHITE_KELVIN, DEFAULT_RGBW_FIXED_WHITE_KELVIN = (
    "rgbw_fixed_white_kelvin",
    4500,
)
DOCS[CONF_RGBW_FIXED_WHITE_KELVIN] = (
    "For RGBW strips: the fixed color temperature (Kelvin) of the W channel (e.g., 3000K or 4500K). "
    "Used to compute the RGB+W mix for a target color temperature. "
)

# Calibration of the RGB channels (neutral/white point). Defaults to uncalibrated (255,255,255).
CONF_RGB_CALIBRATION_R, DEFAULT_RGB_CALIBRATION_R = "rgb_calibration_r", 255
CONF_RGB_CALIBRATION_G, DEFAULT_RGB_CALIBRATION_G = "rgb_calibration_g", 255
CONF_RGB_CALIBRATION_B, DEFAULT_RGB_CALIBRATION_B = "rgb_calibration_b", 255
DOCS[CONF_RGB_CALIBRATION_R] = (
    "RGB calibration: Neutral/white point Red channel value (0-255). Used as the base for Kelvin->RGB mapping in combined mode. "
)
DOCS[CONF_RGB_CALIBRATION_G] = (
    "RGB calibration: Neutral/white point Green channel value (0-255). Used as the base for Kelvin->RGB mapping in combined mode. "
)
DOCS[CONF_RGB_CALIBRATION_B] = (
    "RGB calibration: Neutral/white point Blue channel value (0-255). Used as the base for Kelvin->RGB mapping in combined mode. "
)

CONF_SEPARATE_TURN_ON_COMMANDS, DEFAULT_SEPARATE_TURN_ON_COMMANDS = (
    "separate_turn_on_commands",
    False,
)
DOCS[CONF_SEPARATE_TURN_ON_COMMANDS] = (
    "Use separate light.turn_on calls for color and brightness, needed for "
    "some light types. "
)

CONF_SLEEP_BRIGHTNESS, DEFAULT_SLEEP_BRIGHTNESS = "sleep_brightness", 25
DOCS[CONF_SLEEP_BRIGHTNESS] = "Brightness percentage of lights in sleep mode. "

CONF_SLEEP_COLOR_TEMP, DEFAULT_SLEEP_COLOR_TEMP = "sleep_color_temp", 1000
DOCS[CONF_SLEEP_COLOR_TEMP] = (
    "Color temperature in sleep mode (used when sleep_rgb_or_color_temp is "
    "color_temp) in Kelvin. "
)

CONF_SLEEP_RGB_COLOR, DEFAULT_SLEEP_RGB_COLOR = "sleep_rgb_color", [255, 56, 0]
DOCS[CONF_SLEEP_RGB_COLOR] = (
    'RGB color in sleep mode (used when sleep_rgb_or_color_temp is "rgb_color"). '
)

CONF_SLEEP_RGB_OR_COLOR_TEMP, DEFAULT_SLEEP_RGB_OR_COLOR_TEMP = (
    "sleep_rgb_or_color_temp",
    "rgb_color",
)
DOCS[CONF_SLEEP_RGB_OR_COLOR_TEMP] = (
    'Use either "rgb_color" or "color_temp" in sleep mode. '
)

CONF_SUNRISE_OFFSET, DEFAULT_SUNRISE_OFFSET = "sunrise_offset", 0
DOCS[CONF_SUNRISE_OFFSET] = (
    "Adjust sunrise time with a positive or negative offset in seconds. "
)

CONF_SUNRISE_TIME = "sunrise_time"
DOCS[CONF_SUNRISE_TIME] = "Set a fixed time (HH:MM:SS) for sunrise. "

CONF_MIN_SUNRISE_TIME = "min_sunrise_time"
DOCS[CONF_MIN_SUNRISE_TIME] = (
    "Set the earliest virtual sunrise time (HH:MM:SS), allowing for later sunrises. "
)

CONF_MAX_SUNRISE_TIME = "max_sunrise_time"
DOCS[CONF_MAX_SUNRISE_TIME] = (
    "Set the latest virtual sunrise time (HH:MM:SS), allowing"
    " for earlier sunrises. "
)

CONF_SUNSET_OFFSET, DEFAULT_SUNSET_OFFSET = "sunset_offset", 0
DOCS[CONF_SUNSET_OFFSET] = (
    "Adjust sunset time with a positive or negative offset in seconds. "
)

CONF_SUNSET_TIME = "sunset_time"
DOCS[CONF_SUNSET_TIME] = "Set a fixed time (HH:MM:SS) for sunset. "

CONF_MIN_SUNSET_TIME = "min_sunset_time"
DOCS[CONF_MIN_SUNSET_TIME] = (
    "Set the earliest virtual sunset time (HH:MM:SS), allowing for later sunsets. "
)

CONF_MAX_SUNSET_TIME = "max_sunset_time"
DOCS[CONF_MAX_SUNSET_TIME] = (
    "Set the latest virtual sunset time (HH:MM:SS), allowing for earlier sunsets. "
)

CONF_BRIGHTNESS_MODE, DEFAULT_BRIGHTNESS_MODE = "brightness_mode", "default"
DOCS[CONF_BRIGHTNESS_MODE] = (
    "Brightness mode to use. Possible values are default, linear, and tanh "
    "(uses brightness_mode_time_dark and brightness_mode_time_light). "
)
CONF_BRIGHTNESS_MODE_TIME_DARK, DEFAULT_BRIGHTNESS_MODE_TIME_DARK = (
    "brightness_mode_time_dark",
    10000,
)
DOCS[CONF_BRIGHTNESS_MODE_TIME_DARK] = (
    "(Ignored if brightness_mode='default') The duration in seconds to ramp up/down "
    "the brightness before/after sunrise/sunset. "
)
CONF_BRIGHTNESS_MODE_TIME_LIGHT, DEFAULT_BRIGHTNESS_MODE_TIME_LIGHT = (
    "brightness_mode_time_light",
    1,
)
DOCS[CONF_BRIGHTNESS_MODE_TIME_LIGHT] = (
    "(Ignored if brightness_mode='default') The duration in seconds to ramp up/down "
    "the brightness after/before sunrise/sunset. ."
)

CONF_TAKE_OVER_CONTROL, DEFAULT_TAKE_OVER_CONTROL = "take_over_control", False
DOCS[CONF_TAKE_OVER_CONTROL] = (
    "Pause adaptation of individual lights and hand over (manual) control to other sources that "
    "issue light.turn_on calls for lights that are on. "
)

CONF_TAKE_OVER_CONTROL_MODE, DEFAULT_TAKE_OVER_CONTROL_MODE = (
    "take_over_control_mode",
    TakeOverControlMode.PAUSE_ALL.value,
)
DOCS[CONF_TAKE_OVER_CONTROL_MODE] = (
    "The adaptation pausing mode when other sources change brightness and/or color of lights. "
    "pause_all always pauses both brightness and color adaptation. "
    "pause_changed pauses the adaptation of only the changed attributes and continues adapting "
    "unchanged attributes, e.g., continues color adaptation when only brightness was changed."
)

CONF_TRANSITION, DEFAULT_TRANSITION = "transition", 45
DOCS[CONF_TRANSITION] = "Duration of transition when lights change, in seconds. "

CONF_ADAPT_UNTIL_SLEEP, DEFAULT_ADAPT_UNTIL_SLEEP = (
    "transition_until_sleep",
    False,
)
DOCS[CONF_ADAPT_UNTIL_SLEEP] = (
    "Activa el modo noche automático: cuando el sol se pone (o dentro del horario si lo activas),"
    " las luces pasarán a los valores de modo noche y volverán al modo día al amanecer."
)


# --- Sleep mode activation ---
# Controls *automatic* enabling of sleep mode.
# Manual sleep mode is still available via the sleep_mode switch entity.
CONF_SLEEP_ACTIVATION_MODE, DEFAULT_SLEEP_ACTIVATION_MODE = (
    "sleep_activation_mode",
    "disabled",
)
SLEEP_ACTIVATION_MODES = ("disabled", "sun", "schedule")
DOCS[CONF_SLEEP_ACTIVATION_MODE] = (
    "Cómo se activa automáticamente el modo noche: desactivado, por amanecer/atardecer o por una franja horaria fija."
)

CONF_ADAPT_DELAY, DEFAULT_ADAPT_DELAY = "adapt_delay", 0
DOCS[CONF_ADAPT_DELAY] = (
    "Wait time (seconds) between light turn on and Circadian Pro applying "
    "changes. Might help to avoid flickering. "
)

CONF_SEND_SPLIT_DELAY, DEFAULT_SEND_SPLIT_DELAY = "send_split_delay", 0
DOCS[CONF_SEND_SPLIT_DELAY] = (
    "Delay (ms) between separate_turn_on_commands for lights that don't support "
    "simultaneous brightness and color setting. "
)

CONF_AUTORESET_CONTROL, DEFAULT_AUTORESET_CONTROL = "autoreset_control_seconds", 0
DOCS[CONF_AUTORESET_CONTROL] = (
    "Automatically reset the manual control after a number of seconds. "
    "Set to 0 to disable. "
)

CONF_SKIP_REDUNDANT_COMMANDS, DEFAULT_SKIP_REDUNDANT_COMMANDS = (
    "skip_redundant_commands",
    True,
)
DOCS[CONF_SKIP_REDUNDANT_COMMANDS] = (
    "Skip sending adaptation commands whose target state already "
    "equals the light's known state. Minimizes network traffic and improves the "
    "adaptation responsivity in some situations. "
    "Disable if physical light states get out of sync with HA's recorded state."
)

# --- Optional fixed sleep window (for "transition_until_sleep") ---
# When enabled, "Transicionar hasta modo noche" will use this time window instead
# of the real sunset/sunrise.
CONF_SLEEP_SCHEDULE_ENABLED, DEFAULT_SLEEP_SCHEDULE_ENABLED = (
    "sleep_schedule_enabled",
    False,
)
CONF_SLEEP_SCHEDULE_START, DEFAULT_SLEEP_SCHEDULE_START = (
    "sleep_schedule_start",
    time(23, 0, 0),
)
CONF_SLEEP_SCHEDULE_END, DEFAULT_SLEEP_SCHEDULE_END = (
    "sleep_schedule_end",
    time(5, 0, 0),
)

DOCS[CONF_SLEEP_SCHEDULE_ENABLED] = (
    "Si lo activas, el modo noche se aplicará según una franja horaria fija en lugar de depender del atardecer/amanecer."
)
DOCS[CONF_SLEEP_SCHEDULE_START] = "Sleep window start time (HH:MM[:SS])."
DOCS[CONF_SLEEP_SCHEDULE_END] = "Sleep window end time (HH:MM[:SS])."

CONF_INTERCEPT, DEFAULT_INTERCEPT = "intercept", True
DOCS[CONF_INTERCEPT] = (
    "Intercept and adapt light.turn_on calls to enabling instantaneous color "
    "and brightness adaptation.  Disable for lights that do not "
    "support light.turn_on with color and brightness."
)

CONF_MULTI_LIGHT_INTERCEPT, DEFAULT_MULTI_LIGHT_INTERCEPT = (
    "multi_light_intercept",
    True,
)
DOCS[CONF_MULTI_LIGHT_INTERCEPT] = (
    "Intercept and adapt light.turn_on calls that target multiple lights. "
    " This might result in splitting up a single light.turn_on call "
    "into multiple calls, e.g., when lights are in different switches. "
    "Requires intercept to be enabled."
)

SLEEP_MODE_SWITCH = "sleep_mode_switch"
ADAPT_COLOR_SWITCH = "adapt_color_switch"
ADAPT_BRIGHTNESS_SWITCH = "adapt_brightness_switch"
ATTR_CIRCADIAN_PRO_MANAGER = "manager"
NONE_STR = "None"
ATTR_ADAPT_COLOR = "adapt_color"
DOCS[ATTR_ADAPT_COLOR] = "Whether to adapt the color on supporting lights. "
ATTR_ADAPT_BRIGHTNESS = "adapt_brightness"
DOCS[ATTR_ADAPT_BRIGHTNESS] = "Whether to adapt the brightness of the light. "

SERVICE_SET_MANUAL_CONTROL = "set_manual_control"
CONF_MANUAL_CONTROL = "manual_control"
DOCS[CONF_MANUAL_CONTROL] = "Whether to manually control the lights. "
SERVICE_APPLY = "apply"
CONF_TURN_ON_LIGHTS = "turn_on_lights"
DOCS[CONF_TURN_ON_LIGHTS] = "Whether to turn on lights that are currently off. "
SERVICE_CHANGE_SWITCH_SETTINGS = "change_switch_settings"
CONF_USE_DEFAULTS = "use_defaults"
DOCS[CONF_USE_DEFAULTS] = (
    "Sets the default values not specified in this service call. Options: "
    '"current" (default, retains current values), "factory" (resets to '
    'documented defaults), or "configuration" (reverts to switch config defaults). '
)

TURNING_OFF_DELAY = 5

DOCS_MANUAL_CONTROL = {
    CONF_ENTITY_ID: "The entity_id of the switch in which to (un)mark the "
    "light as being manually controlled. ",
    CONF_LIGHTS: "entity_id(s) of lights, if not specified, all lights in the "
    "switch are selected. ",
    CONF_MANUAL_CONTROL: 'Whether to add ("true") or remove ("false") all '
    'adapted attributes of the light from the "manual_control" list, or the '
    "name of an attribute for selective addition. ",
}

DOCS_APPLY = {
    CONF_ENTITY_ID: "The entity_id of the switch with the settings to apply. ",
    CONF_LIGHTS: "A light (or list of lights) to apply the settings to. ",
}


def int_between(min_int: int, max_int: int) -> vol.All:
    """Return an integer between 'min_int' and 'max_int'."""
    return vol.All(vol.Coerce(int), vol.Range(min=min_int, max=max_int))

def slider_int(min_int: int, max_int: int, step: int = 1) -> selector.NumberSelector:
    """Return a UI slider selector for integers."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_int,
            max=max_int,
            step=step,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def box_int(min_int: int, max_int: int, step: int = 1) -> selector.NumberSelector:
    """Return a UI number selector (box) for integers."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_int,
            max=max_int,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


VALIDATION_TUPLES: list[tuple[str, Any, Any]] = [
    (CONF_LIGHTS, DEFAULT_LIGHTS, cv.entity_ids),  # type: ignore[arg-type]
    (
        CONF_WHITE_W_KELVIN,
        DEFAULT_WHITE_W_KELVIN,
        box_int(1000, 10000, step=50),
    ),
    (
        CONF_CAL_RGB_NEUTRAL_R,
        DEFAULT_CAL_RGB_NEUTRAL_R,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_RGB_NEUTRAL_G,
        DEFAULT_CAL_RGB_NEUTRAL_G,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_RGB_NEUTRAL_B,
        DEFAULT_CAL_RGB_NEUTRAL_B,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_WHITE_INTENSITY_NEUTRAL,
        DEFAULT_CAL_WHITE_INTENSITY_NEUTRAL,
        slider_int(0, 100),
    ),
    (
        CONF_CAL_RGB_WARM_R,
        DEFAULT_CAL_RGB_WARM_R,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_RGB_WARM_G,
        DEFAULT_CAL_RGB_WARM_G,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_RGB_WARM_B,
        DEFAULT_CAL_RGB_WARM_B,
        slider_int(0, 255),
    ),
    (
        CONF_CAL_WHITE_INTENSITY_WARM,
        DEFAULT_CAL_WHITE_INTENSITY_WARM,
        slider_int(0, 100),
    ),
    (CONF_INTERVAL, DEFAULT_INTERVAL, cv.positive_int),
    (CONF_TRANSITION, DEFAULT_TRANSITION, VALID_TRANSITION),
    (CONF_INITIAL_TRANSITION, DEFAULT_INITIAL_TRANSITION, VALID_TRANSITION),
    (CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS, int_between(1, 100)),
    (CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS, int_between(1, 100)),
    (CONF_MIN_COLOR_TEMP, DEFAULT_MIN_COLOR_TEMP, int_between(1000, 10000)),
    (CONF_MAX_COLOR_TEMP, DEFAULT_MAX_COLOR_TEMP, int_between(1000, 10000)),
    (CONF_PREFER_RGB_COLOR, DEFAULT_PREFER_RGB_COLOR, bool),
    (CONF_SLEEP_BRIGHTNESS, DEFAULT_SLEEP_BRIGHTNESS, int_between(1, 100)),
    (
        CONF_SLEEP_RGB_OR_COLOR_TEMP,
        DEFAULT_SLEEP_RGB_OR_COLOR_TEMP,
        selector.SelectSelector(  # type: ignore[arg-type]
            selector.SelectSelectorConfig(
                options=["color_temp", "rgb_color"],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
    ),
    (CONF_SLEEP_COLOR_TEMP, DEFAULT_SLEEP_COLOR_TEMP, int_between(1000, 10000)),
    (
        CONF_SLEEP_RGB_COLOR,
        DEFAULT_SLEEP_RGB_COLOR,
        selector.ColorRGBSelector(selector.ColorRGBSelectorConfig()),  # type: ignore[arg-type]
    ),
    (CONF_SLEEP_TRANSITION, DEFAULT_SLEEP_TRANSITION, VALID_TRANSITION),
    (CONF_SLEEP_ACTIVATION_MODE, DEFAULT_SLEEP_ACTIVATION_MODE, vol.In(SLEEP_ACTIVATION_MODES)),

    (CONF_ADAPT_UNTIL_SLEEP, DEFAULT_ADAPT_UNTIL_SLEEP, bool),
    (CONF_SLEEP_SCHEDULE_ENABLED, DEFAULT_SLEEP_SCHEDULE_ENABLED, bool),
    (
        CONF_SLEEP_SCHEDULE_START,
        DEFAULT_SLEEP_SCHEDULE_START,
        vol.Any(None, cv.string),
    ),
    (
        CONF_SLEEP_SCHEDULE_END,
        DEFAULT_SLEEP_SCHEDULE_END,
        vol.Any(None, cv.string),
    ),
    (CONF_SUNRISE_TIME, NONE_STR, str),
    (CONF_MIN_SUNRISE_TIME, NONE_STR, str),
    (CONF_MAX_SUNRISE_TIME, NONE_STR, str),
    (CONF_SUNRISE_OFFSET, DEFAULT_SUNRISE_OFFSET, int),
    (CONF_SUNSET_TIME, NONE_STR, str),
    (CONF_MIN_SUNSET_TIME, NONE_STR, str),
    (CONF_MAX_SUNSET_TIME, NONE_STR, str),
    (CONF_SUNSET_OFFSET, DEFAULT_SUNSET_OFFSET, int),
    (
        CONF_BRIGHTNESS_MODE,
        DEFAULT_BRIGHTNESS_MODE,
        selector.SelectSelector(  # type: ignore[arg-type]
            selector.SelectSelectorConfig(
                options=[
                    {"value": "default", "label": "Natural"},
                    {"value": "linear", "label": "Lineal"},
                    {"value": "tanh", "label": "Sigmoide"},
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
    ),
    (CONF_BRIGHTNESS_MODE_TIME_DARK, DEFAULT_BRIGHTNESS_MODE_TIME_DARK, int),
    (CONF_BRIGHTNESS_MODE_TIME_LIGHT, DEFAULT_BRIGHTNESS_MODE_TIME_LIGHT, int),
    (CONF_TAKE_OVER_CONTROL, DEFAULT_TAKE_OVER_CONTROL, bool),
    (
        CONF_TAKE_OVER_CONTROL_MODE,
        DEFAULT_TAKE_OVER_CONTROL_MODE,
        selector.SelectSelector(  # type: ignore[arg-type]
            selector.SelectSelectorConfig(
                options=[
                    TakeOverControlMode.PAUSE_ALL.value,
                    TakeOverControlMode.PAUSE_CHANGED.value,
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
    ),
    (CONF_DETECT_NON_HA_CHANGES, DEFAULT_DETECT_NON_HA_CHANGES, bool),
    (
        CONF_AUTORESET_CONTROL,
        DEFAULT_AUTORESET_CONTROL,
        int_between(0, 365 * 24 * 60 * 60),  # 1 year max
    ),
    (CONF_ONLY_ONCE, DEFAULT_ONLY_ONCE, bool),
    (CONF_ADAPT_ONLY_ON_BARE_TURN_ON, DEFAULT_ADAPT_ONLY_ON_BARE_TURN_ON, bool),
    (CONF_SEPARATE_TURN_ON_COMMANDS, DEFAULT_SEPARATE_TURN_ON_COMMANDS, bool),
    (CONF_SEND_SPLIT_DELAY, DEFAULT_SEND_SPLIT_DELAY, int_between(0, 10000)),
    (CONF_ADAPT_DELAY, DEFAULT_ADAPT_DELAY, cv.positive_float),
    (
        CONF_SKIP_REDUNDANT_COMMANDS,
        DEFAULT_SKIP_REDUNDANT_COMMANDS,
        bool,
    ),
    (CONF_INTERCEPT, DEFAULT_INTERCEPT, bool),
    (CONF_MULTI_LIGHT_INTERCEPT, DEFAULT_MULTI_LIGHT_INTERCEPT, bool),
    (CONF_INCLUDE_CONFIG_IN_ATTRIBUTES, DEFAULT_INCLUDE_CONFIG_IN_ATTRIBUTES, bool),
]


def timedelta_as_int(value: timedelta) -> float:
    """Convert a datetime.timedelta object to an integer.

    This integer can be serialized to json but a timedelta cannot.
    """
    return value.total_seconds()


# conf_option: (validator, coerce) tuples
# these validators cannot be serialized but can be serialized when coerced by coerce.
EXTRA_VALIDATION: dict[str, tuple[Any, Any]] = {
    CONF_INTERVAL: (cv.time_period, timedelta_as_int),
    CONF_SUNRISE_OFFSET: (cv.time_period, timedelta_as_int),
    CONF_SUNRISE_TIME: (cv.time, str),
    CONF_MIN_SUNRISE_TIME: (cv.time, str),
    CONF_MAX_SUNRISE_TIME: (cv.time, str),
    CONF_SUNSET_OFFSET: (cv.time_period, timedelta_as_int),
    CONF_SUNSET_TIME: (cv.time, str),
    CONF_MIN_SUNSET_TIME: (cv.time, str),
    CONF_MAX_SUNSET_TIME: (cv.time, str),
    CONF_BRIGHTNESS_MODE_TIME_LIGHT: (cv.time_period, timedelta_as_int),
    CONF_BRIGHTNESS_MODE_TIME_DARK: (cv.time_period, timedelta_as_int),
    CONF_SLEEP_SCHEDULE_START: (cv.time, str),
    CONF_SLEEP_SCHEDULE_END: (cv.time, str),
}


def maybe_coerce(key: str, validation: Any) -> vol.All | Any:
    """Coerce the validation into a json serializable type."""
    validation, coerce = EXTRA_VALIDATION.get(key, (validation, None))
    if coerce is not None:
        return vol.All(validation, vol.Coerce(coerce))
    return validation


def replace_none_str(value: Any, replace_with: Any | None = None) -> Any:
    """Replace "None" -> replace_with."""
    return value if value != NONE_STR else replace_with


def _duration_seconds(value: Any) -> float:
    """Return a duration as seconds for cross-field validation."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


def _time_value(value: Any) -> time | None:
    """Return a comparable time value, preserving optional fields."""
    if value is None or value == NONE_STR:
        return None
    if isinstance(value, time):
        return value
    return cv.time(value)


def validate_cross_options(data: dict[str, Any]) -> dict[str, Any]:
    """Validate relationships between individually valid configuration values."""
    min_brightness = data.get(CONF_MIN_BRIGHTNESS)
    max_brightness = data.get(CONF_MAX_BRIGHTNESS)
    if (
        min_brightness is not None
        and max_brightness is not None
        and float(min_brightness) > float(max_brightness)
    ):
        raise vol.Invalid("invalid_brightness_range")

    min_color_temp = data.get(CONF_MIN_COLOR_TEMP)
    max_color_temp = data.get(CONF_MAX_COLOR_TEMP)
    if (
        min_color_temp is not None
        and max_color_temp is not None
        and float(min_color_temp) > float(max_color_temp)
    ):
        raise vol.Invalid("invalid_color_temp_range")

    brightness_mode = data.get(CONF_BRIGHTNESS_MODE, DEFAULT_BRIGHTNESS_MODE)
    if brightness_mode in ("linear", "tanh"):
        dark = _duration_seconds(data.get(CONF_BRIGHTNESS_MODE_TIME_DARK, 0))
        light = _duration_seconds(data.get(CONF_BRIGHTNESS_MODE_TIME_LIGHT, 0))
        if dark < 0 or light < 0 or dark + light <= 0:
            raise vol.Invalid("invalid_brightness_window")

    min_sunrise = _time_value(data.get(CONF_MIN_SUNRISE_TIME))
    max_sunrise = _time_value(data.get(CONF_MAX_SUNRISE_TIME))
    if min_sunrise is not None and max_sunrise is not None and min_sunrise > max_sunrise:
        raise vol.Invalid("invalid_sunrise_bounds")

    min_sunset = _time_value(data.get(CONF_MIN_SUNSET_TIME))
    max_sunset = _time_value(data.get(CONF_MAX_SUNSET_TIME))
    if min_sunset is not None and max_sunset is not None and min_sunset > max_sunset:
        raise vol.Invalid("invalid_sunset_bounds")

    return data


_yaml_validation_tuples = [
    (key, default, maybe_coerce(key, validation))
    for key, default, validation in VALIDATION_TUPLES
] + [(CONF_NAME, DEFAULT_NAME, cv.string)]

_DOMAIN_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(key, default=replace_none_str(default, vol.UNDEFINED)): validation
            for key, default, validation in _yaml_validation_tuples
        },
    ),
    validate_cross_options,
)


def apply_service_schema(initial_transition: float = 1) -> vol.Schema:
    """Return the schema for the apply service."""
    return vol.Schema(
        {
            vol.Optional(CONF_ENTITY_ID): cv.entity_ids,  # type: ignore[arg-type]
            vol.Optional(CONF_LIGHTS, default=[]): cv.entity_ids,  # type: ignore[arg-type]
            vol.Optional(
                CONF_TRANSITION,
                default=initial_transition,
            ): VALID_TRANSITION,
            vol.Optional(ATTR_ADAPT_BRIGHTNESS, default=True): cv.boolean,
            vol.Optional(ATTR_ADAPT_COLOR, default=True): cv.boolean,
            vol.Optional(CONF_PREFER_RGB_COLOR, default=False): cv.boolean,
            vol.Optional(CONF_TURN_ON_LIGHTS, default=False): cv.boolean,
        },
    )


SET_MANUAL_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENTITY_ID): cv.entity_ids,  # type: ignore[arg-type]
        vol.Optional(CONF_LIGHTS, default=[]): cv.entity_ids,  # type: ignore[arg-type]
        vol.Optional(CONF_MANUAL_CONTROL, default=True): vol.Any(
            cv.boolean,
            vol.In(["brightness", "color"]),
        ),
    },
)