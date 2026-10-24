"""Native entity translations and action errors without changing stable IDs.

Production entity constructors, ID/device properties and validation functions
are executed with AST. The small HA doubles model its public translation
contract; these tests do not replace verification on a running HA installation.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


COMPONENT = Path(__file__).parents[1] / "custom_components" / "circadian_pro"


class ServiceValidationError(Exception):
    """HA's public translated validation-error payload."""

    def __init__(self, *args, translation_domain=None, translation_key=None,
                 translation_placeholders=None):
        super().__init__(*args or (translation_key,))
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class SwitchEntity:
    """Minimal name resolver: production entities must inherit the HA property."""

    @property
    def name(self):
        # Equivalent to HA's native name lookup using platform translations.
        return self.hass.entity_translations[self._attr_translation_key]["name"]

    def async_on_remove(self, _callback):
        pass


class RestoreEntity:
    pass


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def entity_namespace():
    tree = ast.parse((COMPONENT / "switch.py").read_text(encoding="utf-8"))
    methods = {"__init__", "name", "unique_id", "device_info", "is_on"}
    classes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in {"CircadianProSwitch", "SimpleSwitch"}:
            node.body = [item for item in node.body if isinstance(item, ast.Assign)
                         or getattr(item, "name", None) in methods]
            classes.append(node)
    namespace = {
        "SwitchEntity": SwitchEntity, "RestoreEntity": RestoreEntity,
        "DOMAIN": "circadian_pro", "CONF_NAME": "name", "CONF_INTERVAL": "interval",
        "CONF_LIGHTS": "lights", "ICON_MAIN": "mdi:theme-light-dark", "deepcopy": deepcopy,
        "slugify": slugify, "validate": lambda entry: deepcopy(entry.data),
        "async_generate_entity_id": lambda template, value, **_kwargs: template.format(value),
        "DeviceInfo": dict, "DeviceEntryType": SimpleNamespace(SERVICE="service"),
        "_LOGGER": logging.getLogger(__name__),
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *classes], type_ignores=[])),
                 str(COMPONENT / "switch.py"), "exec"), namespace)
    namespace["CircadianProSwitch"]._set_changeable_settings = lambda *_args, **_kwargs: None
    namespace["CircadianProSwitch"]._remove_listeners = lambda _self: None
    return namespace


def action_namespace():
    tree = ast.parse((COMPONENT / "switch.py").read_text(encoding="utf-8"))
    names = {"NoSwitchFoundError", "_switch_with_lights", "_switches_from_service_call",
             "handle_change_switch_settings"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    namespace = {
        "ServiceValidationError": ServiceValidationError,
        "DOMAIN": "circadian_pro", "CONF_LIGHTS": "lights", "SWITCH_DOMAIN": "switch",
        "CONF_USE_DEFAULTS": "use_defaults", "CircadianProSwitch": type("Main", (), {}),
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])),
                 str(COMPONENT / "switch.py"), "exec"), namespace)
    return namespace


def complete(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected I/O in translation test")


class EntityTranslationTests(unittest.TestCase):
    def test_native_names_and_legacy_identifiers_in_both_languages(self):
        namespace = entity_namespace()
        main, simple = namespace["CircadianProSwitch"], namespace["SimpleSwitch"]
        entry = SimpleNamespace(
            data={"name": "Reading corner", "lights": ["light.reading"], "interval": timedelta(seconds=90)},
            options={},
        )
        originals = deepcopy(entry.data)
        expected_names = {
            "en": ("Circadian Pro", "Adapt brightness", "Adapt color", "Sleep mode"),
            "es": ("Circadian Pro", "Adaptar brillo", "Adaptar color", "Modo noche"),
        }
        for language, names in expected_names.items():
            with self.subTest(language=language):
                catalog = json.loads((COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8"))
                hass = SimpleNamespace(entity_translations=catalog["entity"]["switch"])
                main_entity = main(hass, entry, object(), object(), object(), object())
                simple_entities = [simple(role, state, hass, entry, "mdi:lightbulb")
                                   for role, state in (("Adapt Brightness", True), ("Adapt Color", True), ("Sleep Mode", False))]
                entities = [main_entity, *simple_entities]
                self.assertEqual([item.name for item in entities], list(names))
                self.assertTrue(all(item._attr_has_entity_name for item in entities))
                self.assertIs(main.name, SwitchEntity.name)
                self.assertIs(simple.name, SwitchEntity.name)
                self.assertEqual([item.entity_id for item in entities], [
                    "switch.circadian_pro_reading_corner",
                    "switch.circadian_pro_adapt_brightness_reading_corner",
                    "switch.circadian_pro_adapt_color_reading_corner",
                    "switch.circadian_pro_sleep_mode_reading_corner",
                ])
                self.assertEqual([item.unique_id for item in entities], [
                    "Reading corner", "Reading corner_adapt_brightness",
                    "Reading corner_adapt_color", "Reading corner_sleep_mode",
                ])
                self.assertTrue(all(item.device_info["identifiers"] == {("circadian_pro", "Reading corner")}
                                    for item in entities))
                self.assertEqual([item.is_on for item in entities], [None, True, True, False])
                self.assertEqual(entry.data, originals)


class ActionTranslationTests(unittest.TestCase):
    def setUp(self):
        self.ns = action_namespace()
        self.hass = SimpleNamespace(data={})
        self.ns["entity_registry"] = SimpleNamespace(async_get=lambda _hass: SimpleNamespace(async_get=lambda _id: None))

    def assert_error(self, callback, key, placeholders=None):
        with self.assertRaises(ServiceValidationError) as caught:
            callback()
        self.assertEqual(caught.exception.translation_domain, "circadian_pro")
        self.assertEqual(caught.exception.translation_key, key)
        self.assertEqual(caught.exception.translation_placeholders, placeholders)
        for language in ("en", "es"):
            catalog = json.loads((COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8"))
            rendered = catalog["exceptions"][key]["message"].format(**(placeholders or {}))
            self.assertTrue(rendered)

    def test_missing_ambiguous_or_unloaded_service_targets_have_translated_errors(self):
        handler = self.ns["_switches_from_service_call"]
        for data, key, placeholders in (
            ({"lights": []}, "service_target_required", None),
            ({"lights": ["light.reading"], "entity_id": ["switch.one", "switch.two"]},
             "service_multiple_profiles_with_lights", None),
            ({"lights": [], "entity_id": ["switch.unloaded"]}, "service_profile_not_loaded",
             {"entity_id": "switch.unloaded"}),
        ):
            with self.subTest(key=key):
                self.assert_error(lambda data=data: handler(self.hass, SimpleNamespace(data=data)), key, placeholders)

    def test_implicit_light_target_failures_keep_the_internal_catchable_error_type(self):
        exception = self.ns["NoSwitchFoundError"]
        self.assertTrue(issubclass(exception, ServiceValidationError))
        for switches, key in (
            ([], "service_lights_not_configured"),
            ([SimpleNamespace(is_on=True), SimpleNamespace(is_on=True)], "service_lights_multiple_profiles"),
        ):
            with self.subTest(key=key):
                self.ns["_switches_with_lights"] = lambda *_args, switches=switches, **_kwargs: switches
                self.assert_error(lambda: self.ns["_switch_with_lights"](self.hass, ["light.reading"]), key)

    def test_settings_action_rejects_nonmain_switch_before_changing_configuration(self):
        self.assert_error(lambda: complete(self.ns["handle_change_switch_settings"](object(), object())),
                          "service_main_switch_required")

    def test_unknown_defaults_source_has_translated_error_with_exact_value(self):
        async def authorize(*_args):
            pass
        self.ns["async_check_entity_control"] = authorize
        switch = self.ns["CircadianProSwitch"]()
        switch.hass, switch.entity_id, switch.lights = self.hass, "switch.reading", []
        call = SimpleNamespace(data={"use_defaults": "invalid"}, context=None)
        self.assert_error(lambda: complete(self.ns["handle_change_switch_settings"](switch, call)),
                          "service_unknown_defaults", {"value": "invalid"})


if __name__ == "__main__":
    unittest.main()
