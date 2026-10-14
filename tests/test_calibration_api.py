"""Admin API and panel wiring tests without Home Assistant dependencies or lights."""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from functools import wraps
import importlib.util
from math import isfinite
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("Unexpected background wait in calibration API test")


class Invalid(Exception):
    pass


class Marker(str):
    def __new__(cls, value, default=None):
        obj = super().__new__(cls, value)
        obj.default = default
        return obj


class All:
    def __init__(self, *validators):
        self.validators = validators

    def __call__(self, value):
        for validator in self.validators:
            if isinstance(validator, type):
                if not isinstance(value, validator):
                    raise Invalid("Incorrect field type")
            else:
                value = validator(value)
        return value


class In:
    def __init__(self, values):
        self.values = values

    def __call__(self, value):
        if value not in self.values:
            raise Invalid("Unsupported calibration Kelvin")
        return value


class EntryState(Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


def load_api_module():
    saved_modules = dict(sys.modules)
    package = ModuleType("calibration_api_tests_package")
    package.__path__ = [str(COMPONENT)]
    const = ModuleType("calibration_api_tests_package.const")
    const.DOMAIN = "circadian_pro"
    const.NONE_STR = "None"
    const.EXTRA_VALIDATION = {
        "interval": (None, lambda value: int(value.total_seconds()) if isinstance(value, timedelta) else value),
        "sunrise_time": (None, str),
    }
    const.VALIDATION_TUPLES = [("sunrise_time", "None", None)]
    const.schema_calls = []

    def domain_schema(data):
        const.schema_calls.append(deepcopy(data))
        allowed = {"lights", "min_brightness", "max_brightness", "min_color_temp", "max_color_temp", "interval", "sunrise_time", "sunrise_offset", "sunset_offset", "min_sunrise_time", "max_sunrise_time", "sunset_time", "min_sunset_time", "max_sunset_time", "cal_rgb_neutral_r",
                   "brightness_mode", "apply_temperature_curve", "brightness_mode_time_light", "brightness_mode_time_dark",
                   "sleep_activation_mode", "sleep_schedule_start", "sleep_schedule_end"}
        if set(data) - allowed:
            raise Invalid("Unexpected option")
        if "cal_rgb_neutral_r" in data and (not isinstance(data["cal_rgb_neutral_r"], int) or not 0 <= data["cal_rgb_neutral_r"] <= 255):
            raise Invalid("Invalid RGB channel")
        if data.get("sleep_activation_mode", "disabled") not in ("disabled", "sun", "schedule"):
            raise Invalid("Invalid sleep mode")
        return deepcopy(data)

    const._DOMAIN_SCHEMA = domain_schema
    # Execute the real cross-field checks, rather than duplicating their rules.
    tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
    namespace = {"vol": SimpleNamespace(Invalid=Invalid), "timedelta": timedelta, "time": time, "isfinite": isfinite, "NONE_STR": "None", "cv": SimpleNamespace(time=lambda value: time.fromisoformat(value))}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for name, value in zip(target.elts, node.value.elts):
                        if isinstance(name, ast.Name) and isinstance(value, ast.Constant):
                            namespace[name.id] = value.value
                elif isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    namespace[target.id] = node.value.value
    functions = [node for node in tree.body if getattr(node, "name", None) in {"is_panel_entry", "_duration_seconds", "_time_value", "validate_cross_options"}]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<real option relationship validation>", "exec"), namespace)
    const.validate_cross_options = namespace["validate_cross_options"]
    const.is_panel_entry = namespace["is_panel_entry"]
    config_flow = ModuleType("calibration_api_tests_package.config_flow")
    config_flow.validate_entry = lambda entry: deepcopy(entry.options)
    flow_tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    converters = [node for node in flow_tree.body if getattr(node, "name", None) in {"minutes_for_form", "options_from_form"}]
    converter_namespace = {"vol": SimpleNamespace(Invalid=Invalid), "timedelta": timedelta, "isfinite": isfinite}
    converter_module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *converters], type_ignores=[])
    exec(compile(ast.fix_missing_locations(converter_module), "<real form unit conversion>", "exec"), converter_namespace)
    config_flow.options_from_form = converter_namespace["options_from_form"]
    calibration = ModuleType("calibration_api_tests_package.calibration")
    calibration.PREVIEW_SECONDS = 60
    calibration.calls = []
    calibration.stops = []
    calibration.preview_sessions = []
    calibration.stop_sessions = []

    async def preview(hass, entry_id, settings, kelvin, *, session_id=""):
        calibration.calls.append((entry_id, deepcopy(settings), kelvin))
        calibration.preview_sessions.append(session_id)
        return True

    async def stop(hass, entry_id, *, session_id=None):
        calibration.stops.append(entry_id)
        calibration.stop_sessions.append(session_id)

    calibration.async_preview_calibration = preview
    calibration.async_stop_calibration_preview = stop
    schema = ModuleType("probatio")
    schema.Required = Marker
    schema.Optional = Marker
    schema.Invalid = Invalid
    schema.All = All
    schema.In = In
    vol = ModuleType("voluptuous")
    vol.Invalid = Invalid
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    components = ModuleType("homeassistant.components")
    components.__path__ = []
    frontend = ModuleType("homeassistant.components.frontend")
    frontend.DATA_PANELS = "frontend_panels"
    panel_custom = ModuleType("homeassistant.components.panel_custom")
    panel_custom.calls = []

    async def register_panel(hass, **kwargs):
        panel_custom.calls.append(kwargs)
        hass.data.setdefault(frontend.DATA_PANELS, {})[kwargs["frontend_url_path"]] = kwargs

    panel_custom.async_register_panel = register_panel
    ws = ModuleType("homeassistant.components.websocket_api")
    ws.handlers = []

    def command(schema):
        def decorate(function):
            function._ws_schema = schema
            function._ws_command = schema["type"]
            return function
        return decorate

    def admin(function):
        @wraps(function)
        def guarded(hass, connection, message):
            if connection.user is None or not connection.user.is_admin:
                raise PermissionError("Administrator required")
            return function(hass, connection, message)
        return guarded

    def response(function):
        @wraps(function)
        def run(hass, connection, message):
            return immediate(function(hass, connection, message))
        return run

    ws.websocket_command = command
    ws.require_admin = admin
    ws.async_response = response
    ws.async_register_command = lambda hass, handler: ws.handlers.append(handler)
    http = ModuleType("homeassistant.components.http")
    http.StaticPathConfig = lambda url_path, path, cache_headers: SimpleNamespace(url_path=url_path, path=path, cache_headers=cache_headers)
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntryState = EntryState
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    components.frontend = frontend
    components.panel_custom = panel_custom
    components.websocket_api = ws
    sys.modules.update({
        "calibration_api_tests_package": package, "calibration_api_tests_package.const": const,
        "calibration_api_tests_package.config_flow": config_flow, "calibration_api_tests_package.calibration": calibration,
        "probatio": schema, "voluptuous": vol, "homeassistant": ha, "homeassistant.components": components,
        "homeassistant.components.frontend": frontend, "homeassistant.components.panel_custom": panel_custom,
        "homeassistant.components.websocket_api": ws, "homeassistant.components.http": http,
        "homeassistant.config_entries": config_entries, "homeassistant.exceptions": exceptions,
    })
    name = "calibration_api_tests_package.frontend"
    spec = importlib.util.spec_from_file_location(name, COMPONENT / "frontend.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules[name] = api
    spec.loader.exec_module(api)
    return api, saved_modules, calibration, const, ws, panel_custom


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.api, self.saved_modules, self.calibration, self.const, self.ws, self.panel_custom = load_api_module()
        self.entry = SimpleNamespace(entry_id="loaded", domain="circadian_pro", state=EntryState.LOADED, title="Profile A", data={"name": "Profile A"}, options={
            "lights": ["light.example_a"], "min_brightness": 50, "max_brightness": 100,
            "min_color_temp": 1200, "max_color_temp": 4000, "interval": timedelta(seconds=60), "sunrise_time": None,
        })
        self.entries = {"loaded": self.entry}
        self.static_paths = []
        self.executor_calls = []

        async def static(paths):
            self.static_paths.extend(paths)

        async def execute(function, *args):
            self.executor_calls.append(args)
            return function(*args)

        self.hass = SimpleNamespace(
            data={"circadian_pro": {"loaded": {"switch": object()}}},
            config_entries=SimpleNamespace(async_get_entry=self.entries.get, async_entries=lambda domain: [entry for entry in self.entries.values() if entry.domain == domain]),
            http=SimpleNamespace(async_register_static_paths=static), async_add_executor_job=execute,
        )
        self.results = []
        self.errors = []
        self.connection = SimpleNamespace(user=SimpleNamespace(is_admin=True), send_result=lambda *args: self.results.append(args), send_error=lambda *args: self.errors.append(args))

    def tearDown(self):
        prefixes = ("calibration_api_tests_package", "homeassistant", "probatio", "voluptuous")
        for name in set(sys.modules) - self.saved_modules.keys():
            if name.startswith(prefixes):
                sys.modules.pop(name, None)
        for name, module in self.saved_modules.items():
            if name.startswith(prefixes):
                sys.modules[name] = module

    def invoke(self, handler, **fields):
        message = {"id": 1, "type": handler._ws_command, **fields}
        for name, validator in handler._ws_schema.items():
            if name == "type":
                continue
            if name not in message:
                if isinstance(name, Marker) and name.default is not None:
                    message[name] = name.default
                else:
                    raise Invalid("Missing field")
            if isinstance(validator, type):
                if not isinstance(message[name], validator):
                    raise Invalid("Incorrect field type")
            else:
                validator(message[name])
        handler(self.hass, self.connection, message)

    def test_all_endpoints_require_an_administrator_before_accessing_lights(self):
        self.connection.user.is_admin = False
        for handler, fields in ((self.api.websocket_entries, {}), (self.api.websocket_entries, {"include_status": True}), (self.api.websocket_preview, {"entry_id": "loaded", "settings": {}, "kelvin": 4000}), (self.api.websocket_stop, {"entry_id": "loaded"}), (self.api.websocket_translations, {}), (self.api.websocket_solar, {"entry_id": "loaded", "settings": {}})):
            with self.assertRaises(PermissionError):
                self.invoke(handler, **fields)
        self.assertEqual(self.calibration.calls, [])
        self.assertEqual(self.calibration.stops, [])

    def test_entries_are_loaded_only_and_settings_are_serialized_for_the_form(self):
        self.entries["unloaded"] = SimpleNamespace(entry_id="unloaded", domain="circadian_pro", state=EntryState.NOT_LOADED, title="Dormitorio", data={"name": "Dormitorio"})
        self.entries["foreign"] = SimpleNamespace(entry_id="foreign", domain="other", state=EntryState.LOADED)
        self.entry.options["sunrise_time"] = time(7, 30)
        self.invoke(self.api.websocket_entries)
        entry = self.results[-1][1][0]
        self.assertEqual(entry["entry_id"], "loaded")
        self.assertEqual(entry["title"], "Profile A")
        self.assertEqual(entry["settings"]["interval"], 60)
        self.assertEqual(entry["settings"]["sunrise_time"], "07:30:00")
        self.assertEqual(len(self.results[-1][1]), 1)

    def test_panel_anchor_is_not_a_profile_and_empty_panel_can_read_catalog(self):
        self.entries["panel"] = SimpleNamespace(
            entry_id="panel", domain="circadian_pro", state=EntryState.LOADED,
            title="Circadian Pro", data={"entry_type": "panel"}, options={},
        )
        self.invoke(self.api.websocket_entries)
        self.assertEqual([entry["entry_id"] for entry in self.results[-1][1]], ["loaded"])
        self.entries.pop("loaded")
        self.hass.data.pop("circadian_pro")
        self.invoke(self.api.websocket_entries)
        self.assertEqual(self.results[-1][1], [])

    def test_catalog_status_reports_zero_with_only_panel_anchor(self):
        self.entries.clear()
        self.entries["panel"] = SimpleNamespace(
            entry_id="panel", domain="circadian_pro", state=EntryState.LOADED,
            data={"entry_type": "panel"},
        )
        self.invoke(self.api.websocket_entries, include_status=True)
        self.assertEqual(self.results[-1][1], {"entries": [], "configured_profiles": 0})

    def test_catalog_status_counts_disabled_profile_without_treating_it_as_available(self):
        self.entry.state = EntryState.NOT_LOADED
        self.entry.disabled_by = "user"
        self.hass.data.pop("circadian_pro")
        self.invoke(self.api.websocket_entries, include_status=True)
        self.assertEqual(self.results[-1][1], {"entries": [], "configured_profiles": 1})
        self.invoke(self.api.websocket_entries)
        self.assertEqual(self.results[-1][1], [])  # Old clients keep their list contract.

    def test_catalog_status_counts_loaded_disabled_and_failed_profiles_excluding_panel(self):
        for key, disabled_by in (("disabled", "user"), ("failed", None)):
            self.entries[key] = SimpleNamespace(
                entry_id=key, domain="circadian_pro", state=EntryState.NOT_LOADED,
                disabled_by=disabled_by, data={"name": key},
            )
        self.entries["panel"] = SimpleNamespace(
            entry_id="panel", domain="circadian_pro", state=EntryState.LOADED,
            data={"entry_type": "panel"},
        )
        self.entries["foreign"] = SimpleNamespace(domain="other")
        self.invoke(self.api.websocket_entries, include_status=True)
        result = self.results[-1][1]
        self.assertEqual(result["configured_profiles"], 3)
        self.assertEqual([entry["entry_id"] for entry in result["entries"]], ["loaded"])
        self.invoke(self.api.websocket_entries, include_status=False)
        self.assertEqual(self.results[-1][1], result["entries"])

    def test_catalog_status_flag_requires_boolean(self):
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.invoke(self.api.websocket_entries, include_status=value)

    def test_entries_expose_stored_dates_for_profile_sorting_without_changing_settings(self):
        self.entry.created_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
        self.entry.modified_at = datetime(2026, 10, 13, 21, 30, tzinfo=timezone.utc)
        original = deepcopy(self.entry.options)
        self.invoke(self.api.websocket_entries)
        entry = self.results[-1][1][0]
        self.assertEqual(entry["created_at"], "2026-01-10T00:00:00+00:00")
        self.assertEqual(entry["modified_at"], "2026-10-13T21:30:00+00:00")
        self.assertEqual(self.entry.options, original)

    def test_entries_without_dates_remain_available(self):
        self.invoke(self.api.websocket_entries)
        entry = self.results[-1][1][0]
        self.assertIsNone(entry["created_at"])
        self.assertIsNone(entry["modified_at"])

    def test_preview_validates_and_merges_the_draft_without_saving_options(self):
        original = deepcopy(self.entry.options)
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={"cal_rgb_neutral_r": 170, "preview_light": True}, kelvin=4000)
        entry_id, settings, kelvin = self.calibration.calls[-1]
        self.assertEqual((entry_id, kelvin), ("loaded", 4000))
        self.assertEqual(settings["cal_rgb_neutral_r"], 170)
        self.assertIsNone(settings["sunrise_time"])
        self.assertNotIn("sunrise_time", self.const.schema_calls[-1])
        self.assertNotIn("preview_light", settings)
        self.assertEqual(self.entry.options, original)
        self.assertEqual(self.results[-1][1], {"active": True, "expires_in": 60})

    def test_preview_converts_minute_drafts_once_and_keeps_stored_seconds_unchanged(self):
        self.entry.options.update(sunrise_offset=3600, sunset_offset=-61)
        original = deepcopy(self.entry.options)
        draft = {"sunrise_offset": 3600, "sunrise_offset_minutes": 20,
                 "sunset_offset_minutes": -1}
        original_draft = deepcopy(draft)
        for _ in range(2):
            self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=4000)
            settings = self.calibration.calls[-1][1]
            self.assertEqual(settings["sunrise_offset"], 1200)
            self.assertEqual(settings["sunset_offset"], -61)
            self.assertNotIn("sunrise_offset_minutes", settings)
            self.assertNotIn("sunset_offset_minutes", self.const.schema_calls[-1])
        self.assertEqual(self.entry.options, original)
        self.assertEqual(draft, original_draft)

    def test_preview_without_minute_aliases_preserves_second_units(self):
        self.entry.options.update(sunrise_offset=3600, sunset_offset=-61)
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=1000)
        self.assertEqual(self.calibration.calls[-1][1]["sunrise_offset"], 3600)
        self.assertEqual(self.calibration.calls[-1][1]["sunset_offset"], -61)

    def test_invalid_minute_offset_cannot_start_a_preview(self):
        for value in (True, None, "60", float("inf"), float("nan"), 1e100, 1.5, -1.5):
            with self.subTest(value=value):
                self.invoke(self.api.websocket_preview, entry_id="loaded",
                            settings={"sunrise_offset_minutes": value}, kelvin=1000)
                self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.calibration.calls, [])

    def test_preview_brightness_minute_drafts_roundtrip_without_mutating_stored_seconds(self):
        for light, dark, light_minutes, dark_minutes in (
            (60, 10000, 1, 167), (60, 60, 1, 1), (60.25, 10000.75, 1, 167),
            (1200, 1800, 20, 30), (0, 0, 0, 0), (1, 0.25, 0, 0), (14400, 14400, 240, 240),
        ):
            with self.subTest(light=light, dark=dark):
                self.entry.options.update(brightness_mode_time_light=light, brightness_mode_time_dark=dark)
                original = deepcopy(self.entry.options)
                draft = {"brightness_mode_time_light": 333, "brightness_mode_time_dark": 444,
                         "brightness_mode": "linear", "brightness_mode_time_light_minutes": light_minutes,
                         "brightness_mode_time_dark_minutes": dark_minutes}
                original_draft = deepcopy(draft)
                for _ in range(2):
                    self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=4000)
                    settings = self.calibration.calls[-1][1]
                    self.assertEqual(settings["brightness_mode_time_light"], light)
                    self.assertEqual(settings["brightness_mode_time_dark"], dark)
                    self.assertNotIn("brightness_mode_time_light_minutes", settings)
                    self.assertNotIn("brightness_mode_time_dark_minutes", self.const.schema_calls[-1])
                self.assertEqual(draft, original_draft)
                self.assertEqual(self.entry.options, original)
        self.entry.options.update(brightness_mode_time_light=1, brightness_mode_time_dark=10000)
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=1000)
        self.assertEqual(self.calibration.calls[-1][1]["brightness_mode_time_light"], 1)
        self.assertEqual(self.calibration.calls[-1][1]["brightness_mode_time_dark"], 10000)

    def test_preview_edited_integer_minutes_change_only_the_edited_legacy_window(self):
        self.entry.options.update(brightness_mode_time_light=1, brightness_mode_time_dark=10000)
        original = deepcopy(self.entry.options)
        draft = {"brightness_mode": "linear", "brightness_mode_time_light_minutes": 1,
                 "brightness_mode_time_dark_minutes": 167}
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=1000)
        settings = self.calibration.calls[-1][1]
        self.assertEqual(settings["brightness_mode_time_light"], 60)
        self.assertEqual(settings["brightness_mode_time_dark"], 10000)
        self.assertEqual(self.entry.options, original)

    def test_invalid_brightness_minute_draft_cannot_start_preview(self):
        for name in ("brightness_mode_time_light_minutes", "brightness_mode_time_dark_minutes"):
            for value in (True, None, "60", float("inf"), float("nan"), 1e100, -1, -0.001, 240.001, 240.5, 0.5, 166.5):
                with self.subTest(name=name, value=value):
                    self.invoke(self.api.websocket_preview, entry_id="loaded", settings={
                        "brightness_mode": "linear", "brightness_mode_time_light_minutes": 20,
                        "brightness_mode_time_dark_minutes": 30, name: value,
                    }, kelvin=1000)
                    self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.calibration.calls, [])

    def test_preview_preserves_legacy_canonical_windows_above_panel_maximum(self):
        self.entry.options.update(brightness_mode_time_light=18000, brightness_mode_time_dark=18000.25)
        original = deepcopy(self.entry.options)
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=1000)
        settings = self.calibration.calls[-1][1]
        self.assertEqual(settings["brightness_mode_time_light"], 18000)
        self.assertEqual(settings["brightness_mode_time_dark"], 18000.25)
        self.assertEqual(self.entry.options, original)

    def test_natural_preview_preserves_inactive_legacy_minute_windows(self):
        self.entry.options.update(brightness_mode="default", brightness_mode_time_light=18000,
                                  brightness_mode_time_dark=18000.25)
        original = deepcopy(self.entry.options)
        draft = {"brightness_mode": "default", "brightness_mode_time_light_minutes": 300,
                 "brightness_mode_time_dark_minutes": 300}
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=1000)
        settings = self.calibration.calls[-1][1]
        self.assertEqual(settings["brightness_mode_time_light"], 18000)
        self.assertEqual(settings["brightness_mode_time_dark"], 18000.25)
        self.assertEqual(self.entry.options, original)

    def test_preview_preserves_temperature_curve_flag_with_zero_windows_without_saving(self):
        original = deepcopy(self.entry.options)
        for mode in ("linear", "tanh", "default"):
            for enabled in (False, True):
                with self.subTest(mode=mode, enabled=enabled):
                    draft = {"brightness_mode": mode, "apply_temperature_curve": enabled,
                             "brightness_mode_time_light_minutes": 0,
                             "brightness_mode_time_dark_minutes": 0}
                    self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=4000)
                    settings = self.calibration.calls[-1][1]
                    self.assertIs(settings["apply_temperature_curve"], enabled)
                    self.assertEqual(settings["brightness_mode_time_light"], 0)
                    self.assertEqual(settings["brightness_mode_time_dark"], 0)
                    self.assertEqual(self.entry.options, original)

    def test_invalid_channel_or_unknown_option_never_starts_preview(self):
        for draft in ({"cal_rgb_neutral_r": 999}, {"cal_rgb_neutral_r": "bad"}, {"unknown_option": 1}):
            with self.subTest(draft=draft):
                self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=1000)
                self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.calibration.calls, [])

    def test_real_cross_field_validation_rejects_an_inverted_range(self):
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={"min_brightness": 90, "max_brightness": 10}, kelvin=1000)
        self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.calibration.calls, [])

    def test_kelvin_schema_accepts_only_the_two_calibration_points(self):
        for kelvin in (1200, 6500, "4000", 4000.0):
            with self.subTest(kelvin=kelvin), self.assertRaises(Invalid):
                self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=kelvin)
        self.assertEqual(self.calibration.calls, [])

    def test_unloaded_or_foreign_entries_cannot_be_previewed(self):
        self.entries["foreign"] = SimpleNamespace(entry_id="foreign", domain="other", state=EntryState.LOADED)
        for entry_id in ("missing", "foreign"):
            self.invoke(self.api.websocket_preview, entry_id=entry_id, settings={}, kelvin=1000)
            self.assertEqual(self.errors[-1][1], "not_loaded")
        self.entry.state = EntryState.NOT_LOADED
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=1000)
        self.assertEqual(self.errors[-1][1], "not_loaded")
        self.assertEqual(self.calibration.calls, [])

    def test_preview_failure_returns_a_friendly_error_without_saving(self):
        original = deepcopy(self.entry.options)

        async def unavailable(*args, **kwargs):
            raise self.api.HomeAssistantError("Another profile is previewing this light")

        self.api.async_preview_calibration = unavailable
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=4000)
        self.assertEqual(self.errors[-1][1], "preview_unavailable")
        self.assertEqual(self.entry.options, original)
        self.assertEqual(self.results, [])

    def test_session_id_is_forwarded_to_preview_and_stop(self):
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=4000, session_id="editor")
        self.invoke(self.api.websocket_stop, entry_id="loaded", session_id="editor")
        self.assertEqual(self.calibration.preview_sessions, ["editor"])
        self.assertEqual(self.calibration.stop_sessions, ["editor"])

    def test_legacy_websocket_requests_use_only_the_legacy_preview_session(self):
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings={}, kelvin=4000)
        self.invoke(self.api.websocket_stop, entry_id="loaded")
        self.assertEqual(self.calibration.preview_sessions, [""])
        self.assertEqual(self.calibration.stop_sessions, [""])

    def test_stop_is_idempotent_and_available_after_unload(self):
        self.entry.state = EntryState.NOT_LOADED
        self.invoke(self.api.websocket_stop, entry_id="loaded")
        self.invoke(self.api.websocket_stop, entry_id="loaded")
        self.assertEqual(self.calibration.stops, ["loaded", "loaded"])
        self.assertEqual(self.results[-1][1], {"active": False})

    def test_translations_are_local_cached_options_and_creation_data(self):
        self.invoke(self.api.websocket_translations, language="es-ES")
        self.assertIn("step", self.results[-1][1])
        self.assertEqual(self.results[-1][1]["config"]["step"]["user"]["data"]["name"], "Nombre")
        self.assertIn("unique_name", self.results[-1][1]["config"]["error"])
        self.assertIn("name_required", self.results[-1][1]["config"]["error"])
        self.invoke(self.api.websocket_translations, language="es")
        self.assertEqual(self.executor_calls, [("es",)])
        self.invoke(self.api.websocket_translations, language="../../other")
        self.assertEqual(self.executor_calls[-1], ("en",))

    def test_panel_and_api_registration_are_idempotent_and_keep_user_dashboard(self):
        dashboard = object()
        self.hass.data["frontend_panels"] = {"circadian-pro": dashboard}
        immediate(self.api.async_setup_frontend(self.hass))
        immediate(self.api.async_setup_frontend(self.hass))
        self.assertEqual(len(self.ws.handlers), 5)
        self.assertEqual(len(self.static_paths), 2)
        self.assertEqual(self.static_paths[0].url_path, "/circadian_pro_static/calibration-panel.js")
        self.assertTrue(all(not route.cache_headers for route in self.static_paths))
        self.assertEqual(len(self.panel_custom.calls), 1)
        panel = self.panel_custom.calls[0]
        self.assertEqual(panel["frontend_url_path"], "circadian-pro-calibration")
        self.assertEqual(panel["sidebar_title"], "Circadian Pro")
        self.assertTrue(panel["require_admin"])
        self.assertRegex(panel["module_url"], r"^/circadian_pro_static/calibration-panel-[0-9a-f]{16}\.js$")
        fingerprint = panel["module_url"].removeprefix("/circadian_pro_static/calibration-panel-").removesuffix(".js")
        self.assertEqual(panel["webcomponent_name"], f"circadian-pro-calibration-{fingerprint}")
        route = next(route for route in self.static_paths if route.url_path == panel["module_url"])
        self.assertEqual(route.path, self.static_paths[0].path)
        self.assertEqual(Path(route.path), COMPONENT / "frontend" / "calibration-panel.js")
        self.assertEqual(self.executor_calls, [(Path(route.path),)])
        self.assertIs(self.hass.data["frontend_panels"]["circadian-pro"], dashboard)
        self.assertEqual(set(self.hass.data["circadian_pro"]), {"loaded"})

    def test_changed_javascript_gets_a_new_url_and_element_without_a_manifest_change(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent, prefix="panel-asset-") as directory:
            path = Path(directory) / "calibration-panel.js"
            path.write_bytes(b"export const build = 1;")
            first = self.api._panel_asset_registration(path)
            self.assertEqual(self.api._panel_asset_registration(path), first)
            copy = Path(directory) / "same-content.js"
            copy.write_bytes(path.read_bytes())
            self.assertEqual(self.api._panel_asset_registration(copy), first)
            path.write_bytes(b"export const build = 2;")
            second = self.api._panel_asset_registration(path)
            self.assertNotEqual(first[0], second[0])
            self.assertNotEqual(first[1], second[1])

    def test_setup_reads_asset_bytes_only_inside_the_executor(self):
        asset = COMPONENT / "frontend" / "calibration-panel.js"
        read_bytes = Path.read_bytes
        executor = self.hass.async_add_executor_job
        inside_executor = False

        async def guarded_executor(function, *args):
            nonlocal inside_executor
            inside_executor = True
            try:
                return await executor(function, *args)
            finally:
                inside_executor = False

        def guarded_read(path):
            if path == asset:
                self.assertTrue(inside_executor, "Asset I/O must not block HA's event loop")
            return read_bytes(path)

        self.hass.async_add_executor_job = guarded_executor
        with patch.object(Path, "read_bytes", new=guarded_read):
            immediate(self.api.async_setup_frontend(self.hass))
        self.assertEqual(self.executor_calls, [(asset,)])
        self.assertEqual(len(self.panel_custom.calls), 1)

    def test_even_a_collision_at_the_calibration_path_is_not_overwritten(self):
        existing = object()
        self.hass.data["frontend_panels"] = {"circadian-pro-calibration": existing}
        immediate(self.api.async_setup_frontend(self.hass))
        self.assertIs(self.hass.data["frontend_panels"]["circadian-pro-calibration"], existing)
        self.assertEqual(self.panel_custom.calls, [])

    def test_real_integration_setup_hook_registers_api_and_panel(self):
        from test_calibration_flow import load_nodes

        source = ast.parse((COMPONENT / "__init__.py").read_text(encoding="utf-8"))
        function = next(node for node in source.body if getattr(node, "name", None) == "async_setup")
        namespace = {"__name__": "calibration_api_tests_package", "__package__": "calibration_api_tests_package", "DOMAIN": "circadian_pro",
                     "CONF_ENTRY_TYPE": "entry_type", "ENTRY_TYPE_PANEL": "panel"}
        load_nodes(namespace, "const.py", {"is_panel_entry"})
        self.entry.data = {"name": "Profile A"}
        self.entries["panel"] = SimpleNamespace(domain="circadian_pro", data={"entry_type": "panel"})
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "<real integration setup hook>", "exec"), namespace)
        self.assertTrue(immediate(namespace["async_setup"](self.hass, {})))
        self.assertEqual(len(self.ws.handlers), 5)
        self.assertEqual(len(self.panel_custom.calls), 1)


if __name__ == "__main__":
    unittest.main()
