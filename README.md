
<p align="center">
  <img src="custom_components/circadian_pro/logo.png" alt="Circadian Pro" width="220">
</p>

<h1 align="center">Circadian Pro</h1>

<p align="center">
  Advanced circadian lighting control for Home Assistant.
</p>

## Overview

Circadian Pro automatically adjusts the brightness and color temperature of your lights throughout the day according to sunrise, sunset and your configured preferences.

It supports multiple independent instances, manual-control detection, automatic sleep mode, calibrated RGB/RGBW lighting and several brightness-transition profiles.

**Current stable version:** `0.20.3`

## Features

- Automatic brightness and color-temperature adaptation.
- Multiple independent Circadian Pro instances.
- Natural, Linear and Sigmoid brightness modes.
- Manual and automatic sleep mode.
- Sunrise and sunset offsets.
- Fixed or limited sunrise and sunset times.
- Manual-control detection with optional automatic reset.
- RGB, RGBW and color-temperature light support.
- Custom RGBW calibration at 4000 K and 1000 K.
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
````

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

