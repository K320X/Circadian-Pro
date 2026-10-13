"""Circadian Pro integration in Home-Assistant."""
# Modified for Circadian Pro. See NOTICE and LICENSE-APACHE-2.0.txt.

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
    from .frontend import async_setup_frontend

    await async_setup_frontend(hass)
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
    from asyncio import CancelledError

    from .config_flow import validate_entry
    from .const import CONF_SLEEP_ACTIVATION_MODE

    if (
        CONF_SLEEP_ACTIVATION_MODE not in config_entry.data
        and CONF_SLEEP_ACTIVATION_MODE not in config_entry.options
    ):
        mode = validate_entry(config_entry)[CONF_SLEEP_ACTIVATION_MODE]
        if config_entry.source == SOURCE_IMPORT:
            settings = dict(config_entry.data)
            settings[CONF_SLEEP_ACTIVATION_MODE] = mode
            hass.config_entries.async_update_entry(config_entry, data=settings)
        else:
            settings = dict(config_entry.options)
            settings[CONF_SLEEP_ACTIVATION_MODE] = mode
            hass.config_entries.async_update_entry(config_entry, options=settings)

    data = hass.data.setdefault(DOMAIN, {})
    entry_data: dict[str, Any] = {}
    data[config_entry.entry_id] = entry_data

    def cleanup_failed_setup() -> None:
        if hass.data.get(DOMAIN) is not data or data.get(config_entry.entry_id) is not entry_data:
            return  # A later load/unload owns the replacement slot.
        data.pop(config_entry.entry_id)
        switch = entry_data.get("switch")
        try:
            if switch is not None:
                switch._remove_listeners()
        finally:
            try:
                if switch is not None:
                    switch.manager.unregister_switch(switch)
            finally:
                manager = data.get(ATTR_CIRCADIAN_PRO_MANAGER)
                release_manager = manager is not None and not any(
                    isinstance(value, dict)
                    for key, value in data.items() if key != ATTR_CIRCADIAN_PRO_MANAGER
                )
                try:
                    if release_manager:
                        manager.disable()
                finally:
                    if release_manager:
                        if data.get(ATTR_CIRCADIAN_PRO_MANAGER) is manager:
                            data.pop(ATTR_CIRCADIAN_PRO_MANAGER)
                    if not data and hass.data.get(DOMAIN) is data:
                        hass.data.pop(DOMAIN)

    try:
        await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
        if hass.data.get(DOMAIN) is not data or data.get(config_entry.entry_id) is not entry_data:
            return False  # YAML removal or a later reload already owns cleanup.
        if not entry_data.get("platform_setup_complete"):
            cleanup_failed_setup()
            return False  # HA may log a platform error without propagating it.
    except (Exception, CancelledError):
        try:
            cleanup_failed_setup()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Could not completely clean up failed profile setup")
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from .calibration import async_stop_calibration_preview

    await async_stop_calibration_preview(hass, config_entry.entry_id)
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
