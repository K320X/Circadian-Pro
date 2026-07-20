"""Circadian Pro integration in Home-Assistant."""

from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_SOURCE
from homeassistant.core import HomeAssistant

from .const import (
    _DOMAIN_SCHEMA,  # pyright: ignore[reportPrivateUsage]
    ATTR_CIRCADIAN_PRO_MANAGER,
    CONF_NAME,
    DOMAIN,
)

PLATFORMS = ["switch"]


def _all_unique_names(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate that all entities have a unique profile name."""
    hosts = [device[CONF_NAME] for device in value]
    schema = vol.Schema(vol.Unique())
    schema(hosts)
    return value


CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(cv.ensure_list, [_DOMAIN_SCHEMA], _all_unique_names)},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Import integration from config."""
    if DOMAIN in config:
        for entry in config[DOMAIN]:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={CONF_SOURCE: SOURCE_IMPORT},
                    data=entry,
                ),
            )
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the component."""
    data = hass.data.setdefault(DOMAIN, {})
    data[config_entry.entry_id] = {}
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry,
        PLATFORMS,
    )
    data = hass.data.get(DOMAIN, {})
    if unload_ok:
        data.pop(config_entry.entry_id, None)

    manager = data.get(ATTR_CIRCADIAN_PRO_MANAGER)
    if manager is not None:
        manager.rebuild_lights()

    has_loaded_entries = any(
        isinstance(value, dict)
        for key, value in data.items()
        if key != ATTR_CIRCADIAN_PRO_MANAGER
    )
    if not has_loaded_entries and manager is not None:
        data.pop(ATTR_CIRCADIAN_PRO_MANAGER, None)
        manager.disable()

    if not data:
        hass.data.pop(DOMAIN, None)

    return unload_ok
