
<p align="center">
  <img src="custom_components/circadian_pro/logo.png" alt="Circadian Pro" width="220">
</p>

<h1 align="center">Circadian Pro</h1>

<p align="center">
  Advanced circadian lighting control for Home Assistant.
</p>

## Overview

Circadian Pro automatically adjusts the brightness and color temperature of your lights throughout the day according to sunrise, sunset and your configured preferences.

It supports multiple independent instances, manual-control detection, automatic sleep mode, calibrated RGB/RGBW/RGB+CCT lighting and several brightness-transition profiles.

**Current stable version:** `0.20.3`

**Local development version:** `0.21.0` (RGB+CCT correction and live calibration wizard; not yet released).

## Features

- Automatic brightness and color-temperature adaptation.
- Multiple independent Circadian Pro instances.
- Natural, Linear and Sigmoid brightness modes.
- Manual and automatic sleep mode.
- Sunrise and sunset offsets.
- Fixed or limited sunrise and sunset times.
- Manual-control detection with optional automatic reset.
- RGB, RGBW, RGB+CCT (`rgbww`) and color-temperature light support.
- Custom RGBW and RGB+CCT calibration at 4000 K and 1000 K.
- Configurable adaptation intervals and transitions.
- Interception of `light.turn_on` and `light.toggle`.
- Home Assistant services for applying settings and managing manual control.

## Installation

### Manual installation

1. Download `circadian_pro.zip` from the [latest release](../../releases/latest).
2. Extract the ZIP file.
3. Copy the resulting `circadian_pro` folder into:

```text
/config/custom_components/
```

The final structure must be:

```text
/config/custom_components/circadian_pro/
├── __init__.py
├── manifest.json
├── config_flow.py
├── switch.py
└── ...
```

4. Restart Home Assistant.
5. Open:

```text
Settings → Devices & services → Add integration
```

6. Search for **Circadian Pro** and complete the configuration.

## Updating

1. Download the ZIP from the new release.
2. Replace the existing folder:

```text
/config/custom_components/circadian_pro/
```

3. Restart Home Assistant.

Creating a backup before updating is recommended.

## RGBW and RGB+CCT calibration

The 4000 K and 1000 K calibration points are visual references. Adjust RGB and white intensity at each point to get the appearance you want. They are separate from the physical temperatures of the white LEDs.

For RGBW, `white_w_kelvin` remains the temperature of the single white channel, with a default of 3000 K. For RGB+CCT lights that announce `rgbww`, the same value describes the warm white (WW) LED. The extra `white_cw_kelvin` setting describes the cold white (CW) LED. If omitted, Circadian Pro uses the light's maximum color temperature, or 6500 K when unavailable.

At the 4000 K reference, RGB+CCT has three independent adjustments: RGB channels, total white intensity, and white mixture temperature (`cal_white_kelvin_neutral`). The mixture splits the white intensity between WW and CW and must stay between their physical temperatures. If omitted, it uses 4000 K clamped to that range. The 1000 K reference keeps its RGB and white-intensity controls; warm white fades so RGB can produce the warm/orange appearance below the physical WW temperature.

The extra cold-white and mixture controls appear only when a selected light supports `rgbww`. RGBW keeps one white channel; profiles containing only RGB lights skip physical-white settings and show only RGB calibration. Existing hidden white values are preserved. Pixel-addressable (IC) control is independent of whether an entity exposes RGB, RGBW, or RGBWW.

Open the **Calibración Circadian Pro** sidebar panel for immediate previews. The preview checkbox is disabled by default. Checking it applies the draft immediately; adjusting RGB or white controls while it is checked updates the lights without saving. Unchecking it stops the preview and restores the previous state.

The panel includes general settings, physical white channels, and both calibration points. The first step has **Next**; intermediate steps have **Back** and **Next**; the final step has only **Finish**, which saves the settings. Going back preserves your draft. Navigation stops the preview, so each calibration step starts with it disabled. Canceling or closing the panel also stops the preview; the 60-second expiry stops it and clears the checkbox automatically. A light changed manually during the preview keeps the external change. The standard integration options form remains available for editing settings without immediate previews.

## Brightness modes

### Natural

Gradual transition distributed over a long window before and after the solar event.

### Linear

Uniform transition distributed linearly within a fixed window around the solar event.

### Sigmoid

Fast transition concentrated within a shorter window around the solar event.

## Available services

Circadian Pro provides the following Home Assistant services:

```text
circadian_pro.apply
circadian_pro.set_manual_control
circadian_pro.change_switch_settings
```

These services can be used from automations, scripts and Developer Tools.

## Release 0.20.3

* Updated config-entry lifecycle handling.
* Fixed mixed `light.toggle` behaviour.
* Improved cleanup of listeners, tasks and timers.
* Fixed stale light references after reloads.
* Improved manual-control reset handling.
* Added configuration validation and decimal-transition support.
* Fixed automatic sleep-mode state reporting.
* General code and translation cleanup.

## Important

Circadian Pro is a custom integration and is not part of the official Home Assistant project.

Compatibility can vary depending on the capabilities and implementation of each light integration.

