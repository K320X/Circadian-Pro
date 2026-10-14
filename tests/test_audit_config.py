"""Configuration and lifecycle regressions using real production functions.

HA persistence, form rendering and individual schema validators are doubles;
the merging, migration, wizard and cross-field decisions run unchanged.
"""

from __future__ import annotations

import ast
import asyncio
from asyncio import CancelledError
from copy import deepcopy
from datetime import time, timedelta
import json
import logging
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_calibration_flow as flow_fixtures


COMPONENT = flow_fixtures.COMPONENT
immediate = flow_fixtures.immediate
load_nodes = flow_fixtures.load_nodes
Invalid = flow_fixtures.Invalid


class ConfigAuditTests(unittest.TestCase):
    def setUp(self):
        fixture = flow_fixtures.ConfigCreationTests(
            "test_new_profile_uses_empty_lights_default_until_options_select_lights"
        )
        fixture.setUp()
        self.namespace = fixture.namespace
        self.CreationFlow = fixture.Flow
        load_nodes(self.namespace, "const.py", {"normalize_sleep_activation", "timedelta_as_int"})
        self.namespace["cv"].time = lambda value: value if isinstance(value, time) else time.fromisoformat(value)
        self.namespace["cv"].time_period = lambda value: value if isinstance(value, timedelta) else timedelta(seconds=float(value))
        tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
        extra = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
                     and isinstance(node.target, ast.Name) and node.target.id == "EXTRA_VALIDATION")
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), extra], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "<real serialization rules>", "exec"), self.namespace)

    def options_flow(self, options, data=None):
        handler = self.namespace["OptionsFlowHandler"]

        class Flow(handler):
            def __init__(self):
                self.config_entry = SimpleNamespace(
                    source="user", data=data or {"name": "Bedroom"}, options=deepcopy(options)
                )
                self.hass = SimpleNamespace(states=SimpleNamespace(
                    async_entity_ids=lambda _: [], get=lambda _: None,
                ))

            def async_show_form(self, **kwargs):
                return {"type": "form", **kwargs}

            def async_abort(self, **kwargs):
                return {"type": "abort", **kwargs}

        return Flow()

    def test_duplicate_yaml_profile_copies_effective_settings_as_serializable_options(self):
        self.namespace["EXTRA_VALIDATION"]["interval"] = (
            lambda value: value if isinstance(value, timedelta) else timedelta(seconds=value),
            self.namespace["timedelta_as_int"],
        )
        self.namespace["EXTRA_VALIDATION"]["sleep_schedule_start"] = (
            lambda value: value if isinstance(value, time) else time.fromisoformat(value), str,
        )
        source = SimpleNamespace(
            entry_id="yaml", title="Original", unique_id="Original", source="import",
            data={"name": "Original", "lights": ["light.original"], "interval": 30,
                  "max_brightness": 80, "transition_until_sleep": True,
                  "sleep_schedule_start": "22:30:00"},
            options={"max_brightness": 90, "hidden_option": {"preserve": True}},
        )
        flow = self.CreationFlow([source])
        immediate(flow.async_step_menu({"action": "yaml"}))
        created = immediate(flow.async_step_wait_for_name({"name": "Copy"}))
        options = created["options"]
        self.assertEqual(options["lights"], ["light.original"])
        self.assertEqual(options["interval"], 30)
        self.assertEqual(options["max_brightness"], 80)  # YAML remains authoritative.
        self.assertEqual(options["sleep_activation_mode"], "sun")
        self.assertEqual(options["sleep_schedule_start"], "22:30:00")
        self.assertNotIn("name", options)
        self.assertEqual(created["data"], {"name": "Copy"})
        self.assertIsNot(options["lights"], source.data["lights"])
        options["hidden_option"]["preserve"] = False
        self.assertTrue(source.options["hidden_option"]["preserve"])
        json.dumps(options)  # No datetime/timedelta values reach ConfigEntry storage.

    def test_removed_duplicate_source_returns_to_menu_and_clears_old_selection(self):
        flow = self.CreationFlow()
        flow.source_options = {"lights": ["light.previous"]}
        result = immediate(flow.async_step_menu({"action": "removed"}))
        self.assertEqual(result["step_id"], "menu")
        self.assertEqual(result["errors"], {"base": "profile_missing"})
        self.assertIsNone(flow.source_options)
        self.assertEqual(flow.created_entries, [])

    def test_yaml_reimport_reloads_only_changed_settings_and_keeps_tracking(self):
        load_nodes(self.namespace, "config_flow.py", {"async_step_import"}, class_name="ConfigFlow")
        self.CreationFlow.async_step_import = self.namespace["async_step_import"]
        for changed in (False, True):
            with self.subTest(changed=changed):
                entry = SimpleNamespace(entry_id="yaml", title="Bedroom", unique_id="Bedroom",
                                        data={"name": "Bedroom"}, options={})
                flow = self.CreationFlow([entry])
                flow.hass.data = {}
                reloads = []
                writes = []

                def update(config_entry, **changes):
                    self.assertIs(config_entry, entry)
                    writes.append(changes)
                    return changed

                flow.hass.config_entries.async_update_entry = update
                flow.hass.config_entries.async_schedule_reload = reloads.append
                with self.assertRaises(self.namespace["AbortFlow"]) as caught:
                    immediate(flow.async_step_import({"name": "Bedroom", "lights": ["light.bedroom"]}))
                self.assertEqual(caught.exception.reason, "already_configured")
                self.assertEqual(len(writes), 1)
                self.assertEqual(reloads, ["yaml"] if changed else [])
                self.assertEqual(flow.hass.data["circadian_pro"]["__yaml__"], {"Bedroom"})

    def test_general_form_uses_inferred_legacy_mode_and_persisted_values(self):
        flow = self.options_flow({"sleep_schedule_enabled": True, "interval": 61})
        form = immediate(flow.async_step_init())
        fields = flow_fixtures.form_fields(form)
        self.assertEqual(fields["sleep_activation_mode"][0], "schedule")
        self.assertEqual(fields["sleep_schedule_start"][0], "23:00:00")
        self.assertEqual(fields["sleep_schedule_end"][0], "05:00:00")
        self.assertEqual(fields["interval"][0], 61)

    def test_yaml_options_flow_finishes_with_an_informative_abort_without_writing_settings(self):
        data = {"name": "YAML profile", "lights": ["light.yaml"], "max_brightness": 80}
        options = {"max_brightness": 90, "hidden_option": {"keep": True}}
        for submitted in (None, {}, {"lights": ["light.override"], "max_brightness": 10}):
            with self.subTest(submitted=submitted):
                flow = self.options_flow(options, deepcopy(data))
                flow.config_entry.source = "import"
                result = immediate(flow.async_step_init(submitted))
                self.assertEqual(result, {"type": "abort", "reason": "yaml_configuration"})
                self.assertIsNone(flow._base_options)
                self.assertIsNone(flow._cal_neutral)
                self.assertIsNone(flow._cal_warm)
                self.assertEqual(flow.config_entry.data, data)
                self.assertEqual(flow.config_entry.options, options)

    def test_yaml_readonly_options_does_not_validate_or_read_lights_before_showing_its_message(self):
        flow = self.options_flow({}, {"name": "Invalid legacy YAML", "min_brightness": 90, "max_brightness": 10})
        flow.config_entry.source = "import"

        def unexpected_call(*_args):
            self.fail("The readonly options result must not validate or load light state")

        self.namespace["validate_entry"] = unexpected_call
        flow.hass.states.async_entity_ids = unexpected_call
        flow.hass.states.get = unexpected_call
        self.assertEqual(immediate(flow.async_step_init()), {"type": "abort", "reason": "yaml_configuration"})

    def test_invalid_general_submit_keeps_edited_values_and_serializes_duration(self):
        self.namespace["EXTRA_VALIDATION"]["interval"] = (
            lambda value: value if isinstance(value, timedelta) else timedelta(seconds=value),
            self.namespace["timedelta_as_int"],
        )
        flow = self.options_flow({"interval": 90, "min_brightness": 10})
        form = immediate(flow.async_step_init({
            "interval": 60, "min_brightness": 90, "max_brightness": 20,
        }))
        self.assertEqual(form["errors"], {"base": "invalid_brightness_range"})
        fields = flow_fixtures.form_fields(form)
        self.assertEqual(fields["min_brightness"][0], 90)
        self.assertEqual(fields["interval"][0], 60)
        self.assertEqual(flow.config_entry.options["interval"], 90)

    def test_solar_offset_form_displays_minutes_without_changing_stored_seconds(self):
        for seconds in (0, 3600, -3600, 90, -61):
            with self.subTest(seconds=seconds):
                options = {"sunrise_offset": seconds, "sunset_offset": -seconds}
                flow = self.options_flow(options)
                fields = flow_fixtures.form_fields(immediate(flow.async_step_init()))
                self.assertEqual(fields["sunrise_offset_minutes"][0], seconds / 60)
                self.assertEqual(fields["sunset_offset_minutes"][0], -seconds / 60)
                self.assertEqual(fields["sunrise_offset_minutes"][1].step, "any")
                self.assertEqual(fields["sunrise_offset_minutes"][1].mode, "box")
                self.assertFalse(hasattr(fields["sunrise_offset_minutes"][1], "min"))
                self.assertFalse(hasattr(fields["sunset_offset_minutes"][1], "min"))
                self.assertNotIn("sunrise_offset", fields)
                self.assertNotIn("sunset_offset", fields)
                self.assertEqual(flow.config_entry.options, options)
                self.assertIsNone(flow._base_options)

    def test_solar_offset_form_roundtrips_seconds_in_fresh_flows_without_mutating_replay(self):
        for seconds in (0, 3600, -3600, 90, -61, 123):
            with self.subTest(seconds=seconds):
                options = {"sunrise_offset": seconds, "sunset_offset": -seconds}
                first = self.options_flow(options)
                fields = flow_fixtures.form_fields(immediate(first.async_step_init()))
                draft = {name: fields[name][0] for name in ("sunrise_offset_minutes", "sunset_offset_minutes")}
                original_draft = dict(draft)
                # The panel's Back action replays the same UI draft in fresh flows.
                for _ in range(3):
                    flow = self.options_flow(options)
                    result = immediate(flow.async_step_init(draft))
                    self.assertEqual(result["step_id"], "cal_neutral")
                    self.assertEqual(flow._base_options, options)
                    self.assertEqual(flow.config_entry.options, options)
                    self.assertEqual(draft, original_draft)

    def test_invalid_general_form_keeps_submitted_minutes_without_converting_twice(self):
        flow = self.options_flow({"sunrise_offset": 3600, "sunset_offset": -3600})
        draft = {"sunrise_offset_minutes": 20, "sunset_offset_minutes": -1.5,
                 "min_brightness": 90, "max_brightness": 20}
        form = immediate(flow.async_step_init(draft))
        self.assertEqual(form["errors"], {"base": "invalid_brightness_range"})
        fields = flow_fixtures.form_fields(form)
        self.assertEqual(fields["sunrise_offset_minutes"][0], 20)
        self.assertEqual(fields["sunset_offset_minutes"][0], -1.5)
        self.assertIsNone(flow._base_options)
        corrected = {**draft, "max_brightness": 100}
        self.assertEqual(immediate(flow.async_step_init(corrected))["step_id"], "cal_neutral")
        self.assertEqual(flow._base_options["sunrise_offset"], 1200)
        self.assertEqual(flow._base_options["sunset_offset"], -90)
        self.assertEqual(draft["sunrise_offset_minutes"], 20)

    def test_solar_offset_conversion_precedes_fixed_event_validation(self):
        flow = self.options_flow({})
        draft = {"sunrise_time": "08:00:00", "sunset_time": "09:00:00",
                 "sunrise_offset_minutes": 60, "sunset_offset_minutes": 0}
        form = immediate(flow.async_step_init(draft))
        self.assertEqual(form["errors"], {"base": "invalid_solar_times"})
        self.assertEqual(flow_fixtures.form_fields(form)["sunrise_offset_minutes"][0], 60)
        self.assertIsNone(flow._base_options)

    def test_invalid_minute_offsets_return_a_recoverable_error_without_saving(self):
        for value in (None, True, False, "60", "invalid", [], {}, float("inf"),
                      float("-inf"), float("nan"), 1e100, 10 ** 400):
            with self.subTest(value=value):
                flow = self.options_flow({"sunrise_offset": 3600})
                draft = {"sunrise_offset_minutes": value}
                form = immediate(flow.async_step_init(draft))
                self.assertEqual(form["errors"], {"base": "option_error"})
                self.assertIs(flow_fixtures.form_fields(form)["sunrise_offset_minutes"][0], value)
                self.assertEqual(flow.config_entry.options, {"sunrise_offset": 3600})
                self.assertIsNone(flow._base_options)

    def test_minutes_override_corresponding_seconds_but_leave_canonical_inputs_unchanged(self):
        convert = self.namespace["options_from_form"]
        canonical = {"sunrise_offset": 3600, "sunset_offset": -61, "interval": 60}
        self.assertEqual(convert(canonical), canonical)
        self.assertIsNot(convert(canonical), canonical)
        draft = {**canonical, "sunrise_offset_minutes": 20}
        self.assertEqual(convert(draft), {**canonical, "sunrise_offset": 1200})
        self.assertEqual(draft["sunrise_offset"], 3600)
        self.assertEqual(draft["sunrise_offset_minutes"], 20)

    def test_brightness_windows_display_minutes_and_roundtrip_legacy_seconds_on_back(self):
        for light, dark in ((60, 10000), (60, 60), (61, 123), (60.25, 10000.75), (0, 0), (1, 0.25), (14400, 14400)):
            with self.subTest(light=light, dark=dark):
                options = {"brightness_mode": "linear", "brightness_mode_time_light": light,
                           "brightness_mode_time_dark": dark}
                first = self.options_flow(options)
                fields = flow_fixtures.form_fields(immediate(first.async_step_init()))
                draft = {"brightness_mode": "linear"}
                for name, seconds in (("brightness_mode_time_light", light), ("brightness_mode_time_dark", dark)):
                    form_name = f"{name}_minutes"
                    self.assertNotIn(name, fields)
                    self.assertEqual(fields[form_name][0], seconds / 60)
                    self.assertEqual(fields[form_name][1].step, "any")
                    self.assertEqual(fields[form_name][1].mode, "box")
                    self.assertEqual(fields[form_name][1].min, 0)
                    self.assertEqual(fields[form_name][1].max, 240)
                    draft[form_name] = fields[form_name][0]
                original_draft = dict(draft)
                # Back and forward replay a UI draft through fresh options flows.
                for _ in range(3):
                    flow = self.options_flow(options)
                    self.assertEqual(immediate(flow.async_step_init(draft))["step_id"], "cal_neutral")
                    self.assertEqual(flow._base_options, options)
                    self.assertEqual(flow.config_entry.options, options)
                    self.assertEqual(draft, original_draft)

    def test_brightness_form_preserves_legacy_canonical_defaults(self):
        flow = self.options_flow({})
        fields = flow_fixtures.form_fields(immediate(flow.async_step_init()))
        self.assertEqual(self.namespace["DEFAULT_BRIGHTNESS_MODE_TIME_LIGHT"], 1)
        self.assertEqual(fields["brightness_mode_time_light_minutes"][0], 1 / 60)
        self.assertEqual(fields["brightness_mode_time_dark_minutes"][0], 10000 / 60)
        for name in ("brightness_mode_time_light_minutes", "brightness_mode_time_dark_minutes"):
            self.assertEqual(fields[name][1].min, 0)
            self.assertEqual(fields[name][1].max, 240)
            self.assertEqual(fields[name][1].step, "any")
        self.assertEqual(flow.config_entry.options, {})
        self.assertIsNone(flow._base_options)
        legacy_settings = self.namespace["validate_entry"](flow.config_entry)
        self.assertEqual(legacy_settings["brightness_mode_time_light"].total_seconds(), 1)
        convert = self.namespace["options_from_form"]
        for light, dark in ((0, 0), (0, 10000), (1, 30), (0.25, 10000.75), (18000, 18000.25)):
            with self.subTest(light=light, dark=dark):
                canonical = {"brightness_mode": "linear", "brightness_mode_time_light": light,
                             "brightness_mode_time_dark": dark}
                self.assertEqual(convert(canonical), canonical)
                self.namespace["validate_cross_options"](canonical)
                legacy_flow = self.options_flow(canonical)
                settings = self.namespace["validate_entry"](legacy_flow.config_entry)
                self.assertEqual(settings["brightness_mode_time_light"].total_seconds(), light)
                self.assertEqual(settings["brightness_mode_time_dark"].total_seconds(), dark)
                self.assertEqual(legacy_flow.config_entry.options, canonical)

    def test_brightness_form_preserves_legacy_subminute_display_and_persisted_values(self):
        for light, dark in ((1, 10000), (0, 3600), (60, 0.25), (59.99, 30)):
            with self.subTest(light=light, dark=dark):
                options = {"brightness_mode": "linear", "brightness_mode_time_light": light,
                           "brightness_mode_time_dark": dark}
                flow = self.options_flow(options)
                fields = flow_fixtures.form_fields(immediate(flow.async_step_init()))
                self.assertEqual(fields["brightness_mode_time_light_minutes"][0], light / 60)
                self.assertEqual(fields["brightness_mode_time_dark_minutes"][0], dark / 60)
                self.assertEqual(flow.config_entry.options, options)
                self.assertIsNone(flow._base_options)

    def test_invalid_brightness_minutes_preserve_draft_and_validate_in_seconds(self):
        for mode in ("default", "linear", "tanh"):
            for light, dark in ((-1, 20), (20, -1), (-0.001, 0), (0, -0.001),
                                (240.001, 0), (0, 240.001), (240.5, 20), (20, 240.5)):
                if mode == "default" and max(light, dark) > 240:
                    continue  # Natural preserves inactive legacy windows.
                with self.subTest(mode=mode, light=light, dark=dark):
                    flow = self.options_flow({"brightness_mode_time_light": 1, "brightness_mode_time_dark": 10000})
                    draft = {"brightness_mode": mode, "brightness_mode_time_light_minutes": light,
                             "brightness_mode_time_dark_minutes": dark}
                    form = immediate(flow.async_step_init(draft))
                    self.assertEqual(form["errors"], {"base": "option_error"})
                    fields = flow_fixtures.form_fields(form)
                    self.assertEqual(fields["brightness_mode_time_light_minutes"][0], light)
                    self.assertEqual(fields["brightness_mode_time_dark_minutes"][0], dark)
                    self.assertIsNone(flow._base_options)
                    corrected = {**draft, "brightness_mode_time_light_minutes": 20,
                                 "brightness_mode_time_dark_minutes": 30}
                    self.assertEqual(immediate(flow.async_step_init(corrected))["step_id"], "cal_neutral")
                    self.assertEqual(flow._base_options["brightness_mode_time_light"], 1200)
                    self.assertEqual(flow._base_options["brightness_mode_time_dark"], 1800)

    def test_legacy_windows_above_ui_limit_stay_visible_and_require_explicit_correction(self):
        options = {"brightness_mode": "linear", "brightness_mode_time_light": 18000,
                   "brightness_mode_time_dark": 18000.25}
        flow = self.options_flow(options)
        fields = flow_fixtures.form_fields(immediate(flow.async_step_init()))
        draft = {"brightness_mode": "linear"}
        for name in ("brightness_mode_time_light", "brightness_mode_time_dark"):
            form_name = f"{name}_minutes"
            self.assertEqual(fields[form_name][0], options[name] / 60)
            self.assertEqual(fields[form_name][1].max, 240)
            draft[form_name] = fields[form_name][0]
        self.assertEqual(flow.config_entry.options, options)
        self.assertIsNone(flow._base_options)

        rejected = immediate(flow.async_step_init(draft))
        self.assertEqual(rejected["errors"], {"base": "option_error"})
        self.assertEqual(flow.config_entry.options, options)
        self.assertIsNone(flow._base_options)
        for name, value in draft.items():
            self.assertEqual(flow_fixtures.form_fields(rejected)[name][0], value)

        corrected = {**draft, "brightness_mode_time_light_minutes": 240,
                     "brightness_mode_time_dark_minutes": 239.5}
        self.assertEqual(immediate(flow.async_step_init(corrected))["step_id"], "cal_neutral")
        self.assertEqual(flow._base_options["brightness_mode_time_light"], 14400)
        self.assertEqual(flow._base_options["brightness_mode_time_dark"], 14370)
        self.assertEqual(flow.config_entry.options, options)

    def test_natural_preserves_inactive_legacy_windows_but_active_modes_enforce_limit(self):
        options = {"brightness_mode": "default", "brightness_mode_time_light": 18000,
                   "brightness_mode_time_dark": 18000.25}
        draft = {"brightness_mode": "default", "brightness_mode_time_light_minutes": 300,
                 "brightness_mode_time_dark_minutes": 18000.25 / 60}
        convert = self.namespace["options_from_form"]
        self.assertEqual(convert(draft), options)
        flow = self.options_flow(options)
        self.assertEqual(immediate(flow.async_step_init(draft))["step_id"], "cal_neutral")
        self.assertEqual(flow._base_options, options)
        self.assertEqual(flow.config_entry.options, options)
        for mode in (None, "linear", "tanh"):
            active_draft = {**draft, "brightness_mode": mode}
            if mode is None:
                del active_draft["brightness_mode"]
            with self.subTest(mode=mode), self.assertRaisesRegex(Invalid, "option_error"):
                convert(active_draft)

    def test_invalid_brightness_minute_types_return_recoverable_form_errors(self):
        for name in ("brightness_mode_time_light_minutes", "brightness_mode_time_dark_minutes"):
            for mode in ("default", "linear", "tanh"):
                for value in (None, True, False, "60", [], {}, float("inf"), float("nan"), 1e100, 10 ** 400):
                    with self.subTest(name=name, mode=mode, value=value):
                        flow = self.options_flow({})
                        form = immediate(flow.async_step_init({name: value, "brightness_mode": mode}))
                        self.assertEqual(form["errors"], {"base": "option_error"})
                        self.assertIs(flow_fixtures.form_fields(form)[name][0], value)
                        self.assertIsNone(flow._base_options)

    def test_brightness_minute_aliases_override_only_corresponding_canonical_values(self):
        convert = self.namespace["options_from_form"]
        canonical = {"brightness_mode_time_light": 1.25, "brightness_mode_time_dark": 10000,
                     "transition": 45, "sunrise_offset": 3600}
        self.assertEqual(convert(canonical), canonical)
        draft = {**canonical, "brightness_mode_time_light_minutes": 20.125,
                 "brightness_mode_time_dark_minutes": 10000 / 60}
        self.assertEqual(convert(draft), {**canonical, "brightness_mode_time_light": 1207.5})
        self.assertEqual(draft["brightness_mode_time_light"], 1.25)
        self.assertEqual(draft["brightness_mode_time_light_minutes"], 20.125)

    def test_temperature_curve_is_opt_in_for_legacy_profiles_and_preserves_explicit_choice(self):
        for options, expected in (({}, False), ({"apply_temperature_curve": True}, True),
                                  ({"apply_temperature_curve": False}, False)):
            with self.subTest(options=options):
                flow = self.options_flow(options)
                fields = flow_fixtures.form_fields(immediate(flow.async_step_init()))
                self.assertIs(fields["apply_temperature_curve"][0], expected)
                self.assertIs(self.namespace["validate_entry"](flow.config_entry)["apply_temperature_curve"], expected)
                self.assertEqual(flow.config_entry.options, options)

    def test_canonical_brightness_windows_allow_zero_pair_and_reject_invalid_durations(self):
        validate = self.namespace["validate_cross_options"]
        for mode in ("default", "linear", "tanh"):
            for light, dark in ((0, 0), (0, 10000), (0.25, 0), (60, 9600), (18000, 18000.25)):
                data = {"brightness_mode": mode, "brightness_mode_time_light": light,
                        "brightness_mode_time_dark": dark, "apply_temperature_curve": True}
                with self.subTest(data=data):
                    self.assertIs(validate(data), data)
            for key in ("brightness_mode_time_light", "brightness_mode_time_dark"):
                for value in (-1, -0.001, float("nan"), float("inf"), None, []):
                    with self.subTest(mode=mode, key=key, value=value):
                        with self.assertRaisesRegex(Invalid, "invalid_brightness_window"):
                            validate({"brightness_mode": mode, key: value})

    def test_yaml_schema_infers_legacy_before_new_defaults_and_keeps_explicit_mode(self):
        undefined = object()

        class All:
            def __init__(self, *validators):
                self.validators = validators

            def __call__(self, value):
                for validate in self.validators:
                    value = validate(value)
                return value

        class DefaultSchema(flow_fixtures.Schema):
            def __call__(self, value):
                result = dict(value)
                for marker in self.schema:
                    if marker.default is not undefined:
                        result.setdefault(marker.name, marker.default)
                return result

        self.namespace["vol"].All = All
        self.namespace["vol"].Schema = DefaultSchema
        self.namespace["vol"].UNDEFINED = undefined
        self.namespace["_yaml_validation_tuples"] = self.namespace["VALIDATION_TUPLES"]
        tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
        schema = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "_DOMAIN_SCHEMA"
                              for target in node.targets))
        exec(compile(ast.Module(body=[schema], type_ignores=[]), "<real YAML schema>", "exec"), self.namespace)
        validate = self.namespace["_DOMAIN_SCHEMA"]
        for legacy, expected in (({"transition_until_sleep": True}, "sun"),
                                 ({"sleep_schedule_enabled": True}, "schedule"), ({}, "disabled")):
            with self.subTest(legacy=legacy):
                self.assertEqual(validate(legacy)["sleep_activation_mode"], expected)
                self.assertNotIn("sleep_activation_mode", legacy)
        self.assertEqual(validate({"sleep_activation_mode": "disabled", "transition_until_sleep": True})
                         ["sleep_activation_mode"], "disabled")
        for invalid in (None, "not a mapping", []):
            with self.subTest(invalid=invalid), self.assertRaises(Invalid):
                validate(invalid)

    def test_all_routes_reject_schedule_missing_a_boundary_and_allow_midnight_crossing(self):
        validate = self.namespace["validate_cross_options"]
        for start, end in ((None, None), (None, "05:00:00"), ("23:00:00", None), ("None", "05:00:00")):
            with self.subTest(start=start, end=end):
                data = {"sleep_activation_mode": "schedule", "sleep_schedule_start": start,
                        "sleep_schedule_end": end}
                with self.assertRaisesRegex(Invalid, "invalid_sleep_schedule"):
                    validate(data)
                errors = {}
                self.namespace["validate_options"](data, errors)
                self.assertEqual(errors, {"base": "invalid_sleep_schedule"})
        valid = {"sleep_activation_mode": "schedule", "sleep_schedule_start": "23:00:00",
                 "sleep_schedule_end": "05:00:00"}
        self.assertIs(validate(valid), valid)
        validate({"sleep_activation_mode": "disabled", "sleep_schedule_start": None,
                  "sleep_schedule_end": None})

    def test_ha_probatio_validation_error_is_a_recoverable_form_error(self):
        class SelectorInvalid(Exception):
            pass

        def invalid_time(_value):
            raise SelectorInvalid("Invalid time")

        self.namespace["SelectorInvalid"] = SelectorInvalid
        self.namespace["EXTRA_VALIDATION"]["sleep_schedule_start"] = (invalid_time, str)
        errors = {}
        with self.assertLogs("circadian_pro.flow_tests", level="ERROR"):
            self.namespace["validate_options"]({"sleep_schedule_start": "bad time"}, errors)
        self.assertEqual(errors, {"base": "option_error"})

    def test_fixed_solar_times_reject_coincidence_after_offsets_in_every_validation_route(self):
        validate = self.namespace["validate_cross_options"]
        cases = (
            {"sunrise_time": "08:00:00", "sunset_time": "08:00:00"},
            {"sunrise_time": "08:00:00", "sunset_time": "09:00:00", "sunrise_offset": 3600},
            {"sunrise_time": time(8), "sunset_time": time(9), "sunrise_offset": timedelta(hours=1)},
            {"sunrise_time": "08:00:00", "sunset_time": "08:00:00", "sunset_offset": 86400},
            {"sunrise_time": "23:00:00", "sunset_time": "00:00:00", "sunrise_offset": 3600},
        )
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(Invalid, "invalid_solar_times"):
                    validate(data)
                errors = {}
                self.namespace["validate_options"](deepcopy(data), errors)
                self.assertEqual(errors, {"base": "invalid_solar_times"})

    def test_fixed_solar_times_allow_distinct_adjusted_times_and_optional_events(self):
        validate = self.namespace["validate_cross_options"]
        cases = (
            {"sunrise_time": "08:00:00", "sunset_time": "08:00:00", "sunset_offset": 3600},
            {"sunrise_time": time(8), "sunset_time": time(8, microsecond=1)},
            {"sunrise_time": "20:00:00", "sunset_time": "08:00:00"},
            {"sunrise_time": None, "sunset_time": "08:00:00"},
            {"sunrise_time": "08:00:00", "sunset_time": "None"},
        )
        for data in cases:
            with self.subTest(data=data):
                self.assertIs(validate(data), data)

    def lifecycle(self):
        package = ModuleType("audit_config_package")
        package.__path__ = []
        const = ModuleType("audit_config_package.const")
        const.CONF_SLEEP_ACTIVATION_MODE = "sleep_activation_mode"
        flow = ModuleType("audit_config_package.config_flow")
        flow.validate_entry = self.namespace["validate_entry"]
        calibration = ModuleType("audit_config_package.calibration")

        async def stop_preview(_hass, _entry_id):
            pass

        calibration.async_stop_calibration_preview = stop_preview
        modules = {"audit_config_package": package, "audit_config_package.const": const,
                   "audit_config_package.config_flow": flow, "audit_config_package.calibration": calibration}
        namespace = {"__name__": "audit_config_package", "__package__": "audit_config_package",
                     "SOURCE_IMPORT": "import", "DOMAIN": "circadian_pro", "PLATFORMS": ["switch"],
                     "ATTR_CIRCADIAN_PRO_MANAGER": "manager"}
        load_nodes(namespace, "__init__.py", {"async_setup_entry", "async_unload_entry"})
        return namespace, modules

    def test_setup_persists_legacy_mode_before_platform_setup_only_once(self):
        namespace, modules = self.lifecycle()
        for source, legacy, expected in (("user", {"transition_until_sleep": True}, "sun"),
                                         ("import", {"sleep_schedule_enabled": True}, "schedule"),
                                         ("user", {}, "disabled")):
            with self.subTest(source=source, legacy=legacy):
                entry = SimpleNamespace(entry_id="profile", source=source,
                                        data={"name": "Bedroom", **(legacy if source == "import" else {})},
                                        options=dict(legacy if source == "user" else {}))
                updates = []

                def update(config_entry, **changes):
                    updates.append(deepcopy(changes))
                    for name, value in changes.items():
                        setattr(config_entry, name, value)

                async def forward(config_entry, platforms):
                    self.assertEqual(platforms, ["switch"])
                    self.assertEqual(self.namespace["validate_entry"](config_entry)["sleep_activation_mode"], expected)
                    saved = config_entry.data if source == "import" else config_entry.options
                    self.assertEqual(saved["sleep_activation_mode"], expected)
                    hass.data["circadian_pro"][config_entry.entry_id]["platform_setup_complete"] = True

                hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
                    async_update_entry=update, async_forward_entry_setups=forward,
                ))
                with patch.dict(sys.modules, modules):
                    self.assertTrue(immediate(namespace["async_setup_entry"](hass, entry)))
                    self.assertTrue(immediate(namespace["async_setup_entry"](hass, entry)))
                self.assertEqual(len(updates), 1)
                self.assertEqual(set(updates[0]), {"data" if source == "import" else "options"})
                self.assertEqual(entry.data["name"], "Bedroom")
                for key, value in legacy.items():
                    self.assertEqual((entry.data if source == "import" else entry.options)[key], value)

    def test_setup_keeps_an_explicit_sleep_mode_even_with_legacy_flags(self):
        namespace, modules = self.lifecycle()
        entry = SimpleNamespace(entry_id="profile", source="user", data={"name": "Bedroom"},
                                options={"sleep_activation_mode": "disabled", "transition_until_sleep": True})

        async def forward(config_entry, _platforms):
            hass.data["circadian_pro"][config_entry.entry_id]["platform_setup_complete"] = True

        def update(*_args, **_kwargs):
            self.fail("An explicit activation mode must not be migrated")

        hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
            async_update_entry=update, async_forward_entry_setups=forward,
        ))
        with patch.dict(sys.modules, modules):
            immediate(namespace["async_setup_entry"](hass, entry))
        self.assertEqual(entry.options["sleep_activation_mode"], "disabled")

    def setup_harness(self, forward, data=None):
        namespace, modules = self.lifecycle()
        actions = []
        manager = SimpleNamespace(
            disable=lambda: actions.append("disable"),
            unregister_switch=lambda switch: actions.append(("unregister", switch)),
        )
        switch = SimpleNamespace(manager=manager, _remove_listeners=lambda: actions.append("listeners"))
        entry = SimpleNamespace(entry_id="profile", source="user", data={"name": "Bedroom"},
                                options={"sleep_activation_mode": "disabled"})
        hass = SimpleNamespace(data={} if data is None else {"circadian_pro": data})

        async def forward_entry(config_entry, platforms):
            self.assertIs(config_entry, entry)
            self.assertEqual(platforms, ["switch"])
            forward(hass, entry, manager, switch)

        hass.config_entries = SimpleNamespace(async_forward_entry_setups=forward_entry)
        return SimpleNamespace(namespace=namespace, modules=modules, hass=hass,
                               entry=entry, manager=manager, switch=switch, actions=actions)

    def run_setup(self, fixture):
        with patch.dict(sys.modules, fixture.modules):
            return immediate(fixture.namespace["async_setup_entry"](fixture.hass, fixture.entry))

    def test_forward_errors_and_cancellation_preserve_original_exception_and_release_partial_resources(self):
        for failure in (ImportError("platform dependency missing"), CancelledError("shutdown")):
            for partial in (False, True):
                with self.subTest(failure=type(failure).__name__, partial=partial):
                    def forward(hass, entry, manager, switch):
                        if partial:
                            data = hass.data["circadian_pro"]
                            data["manager"] = manager
                            data[entry.entry_id]["switch"] = switch
                        raise failure

                    fixture = self.setup_harness(forward)
                    with self.assertRaises(type(failure)) as caught:
                        self.run_setup(fixture)
                    self.assertIs(caught.exception, failure)
                    self.assertNotIn("circadian_pro", fixture.hass.data)
                    self.assertEqual(fixture.actions, ["listeners", ("unregister", fixture.switch), "disable"]
                                     if partial else [])

    def test_failed_profile_does_not_disable_loaded_or_loading_sibling_or_erase_yaml(self):
        for sibling in ({"switch": object()}, {}):
            with self.subTest(sibling=sibling):
                failure = RuntimeError("failed profile")

                def forward(hass, entry, manager, switch):
                    data = hass.data["circadian_pro"]
                    data["manager"] = manager
                    data[entry.entry_id]["switch"] = switch
                    raise failure

                data = {"other": sibling, "__yaml__": {"Bedroom"}}
                fixture = self.setup_harness(forward, data)
                with self.assertRaises(RuntimeError):
                    self.run_setup(fixture)
                self.assertEqual(set(data), {"other", "__yaml__", "manager"})
                self.assertIs(data["other"], sibling)
                self.assertEqual(fixture.actions, ["listeners", ("unregister", fixture.switch)])

    def test_last_failed_yaml_profile_releases_manager_and_preserves_import_tracking(self):
        def forward(hass, entry, manager, switch):
            data = hass.data["circadian_pro"]
            data["manager"] = manager
            data[entry.entry_id]["switch"] = switch

        data = {"__yaml__": {"Bedroom"}}
        fixture = self.setup_harness(forward, data)
        self.assertFalse(self.run_setup(fixture))  # Platform returned without completing.
        self.assertEqual(data, {"__yaml__": {"Bedroom"}})
        self.assertEqual(fixture.actions, ["listeners", ("unregister", fixture.switch), "disable"])

    def test_old_setup_never_removes_a_reloaded_slot_or_replaced_domain(self):
        for replacement_scope in ("entry", "domain"):
            for failure in (None, ImportError("old load failed")):
                with self.subTest(scope=replacement_scope, failure=failure):
                    replacement = {"switch": object(), "platform_setup_complete": True}
                    new_domain = {"profile": replacement, "manager": object()}

                    def forward(hass, entry, _manager, _switch):
                        hass.data["circadian_pro"]["manager"] = _manager
                        hass.data["circadian_pro"][entry.entry_id]["switch"] = _switch
                        hass.data["circadian_pro"][entry.entry_id]["platform_setup_complete"] = True
                        if replacement_scope == "entry":
                            hass.data["circadian_pro"][entry.entry_id] = replacement
                        else:
                            hass.data["circadian_pro"] = new_domain
                        if failure is not None:
                            raise failure

                    fixture = self.setup_harness(forward)
                    if failure is None:
                        self.assertFalse(self.run_setup(fixture))
                    else:
                        with self.assertRaises(ImportError) as caught:
                            self.run_setup(fixture)
                        self.assertIs(caught.exception, failure)
                    self.assertIs(fixture.hass.data["circadian_pro"]["profile"], replacement)
                    if replacement_scope == "domain":
                        self.assertIs(fixture.hass.data["circadian_pro"], new_domain)
                    self.assertEqual(fixture.actions, [])

    def test_yaml_removal_during_forward_is_not_reported_as_loaded(self):
        def forward(hass, entry, _manager, _switch):
            hass.data["circadian_pro"].pop(entry.entry_id)

        data = {"__yaml__": set()}
        fixture = self.setup_harness(forward, data)
        self.assertFalse(self.run_setup(fixture))
        self.assertEqual(data, {"__yaml__": set()})
        self.assertEqual(fixture.actions, [])

    def test_cleanup_failure_does_not_replace_original_forward_error_or_cancellation(self):
        for failure in (RuntimeError("original"), CancelledError("original cancellation")):
            with self.subTest(failure=type(failure).__name__):
                def cleanup_error():
                    raise ValueError("listener cleanup error")

                def forward(hass, entry, manager, switch):
                    data = hass.data["circadian_pro"]
                    data["manager"] = manager
                    data[entry.entry_id]["switch"] = switch
                    switch._remove_listeners = cleanup_error
                    raise failure

                fixture = self.setup_harness(forward)
                with self.assertLogs("audit_config_package", level="ERROR"), self.assertRaises(type(failure)) as caught:
                    self.run_setup(fixture)
                self.assertIs(caught.exception, failure)
                self.assertNotIn("circadian_pro", fixture.hass.data)
                self.assertEqual(fixture.actions, [("unregister", fixture.switch), "disable"])

    def test_failed_listener_cleanup_still_unregisters_the_switch_without_disabling_a_sibling(self):
        original = ImportError("original setup failure")

        def forward(hass, entry, manager, switch):
            hass.data["circadian_pro"]["manager"] = manager
            hass.data["circadian_pro"][entry.entry_id]["switch"] = switch
            def broken_listener():
                raise ValueError("listener removal failed")
            switch._remove_listeners = broken_listener
            raise original

        sibling = {"switch": object()}
        data = {"other": sibling}
        fixture = self.setup_harness(forward, data)
        with self.assertLogs("audit_config_package", level="ERROR"), self.assertRaises(ImportError) as caught:
            self.run_setup(fixture)
        self.assertIs(caught.exception, original)
        self.assertEqual(set(data), {"other", "manager"})
        self.assertIs(data["other"], sibling)
        self.assertEqual(fixture.actions, [("unregister", fixture.switch)])

    def test_unregister_or_manager_disable_errors_do_not_leave_the_failed_slot_registered(self):
        for broken_step in ("unregister", "disable"):
            with self.subTest(step=broken_step):
                original = RuntimeError("original setup failure")

                def forward(hass, entry, manager, switch):
                    hass.data["circadian_pro"]["manager"] = manager
                    hass.data["circadian_pro"][entry.entry_id]["switch"] = switch
                    def cleanup_error(*_args):
                        fixture.actions.append(broken_step)
                        raise ValueError("cleanup failed")
                    setattr(manager, "unregister_switch" if broken_step == "unregister" else "disable", cleanup_error)
                    raise original

                fixture = self.setup_harness(forward)
                with self.assertLogs("audit_config_package", level="ERROR"), self.assertRaises(RuntimeError) as caught:
                    self.run_setup(fixture)
                self.assertIs(caught.exception, original)
                self.assertNotIn("circadian_pro", fixture.hass.data)
                self.assertEqual(fixture.actions[0], "listeners")
                self.assertEqual(fixture.actions[-1], "disable")

    def test_retry_after_failed_forward_can_complete_without_a_stale_slot(self):
        attempts = []

        def forward(hass, entry, manager, switch):
            slot = hass.data["circadian_pro"][entry.entry_id]
            attempts.append(slot)
            hass.data["circadian_pro"]["manager"] = manager
            slot["switch"] = switch
            if len(attempts) == 1:
                return  # HA swallowed a platform failure.
            slot["platform_setup_complete"] = True

        fixture = self.setup_harness(forward)
        self.assertFalse(self.run_setup(fixture))
        self.assertTrue(self.run_setup(fixture))
        self.assertIsNot(attempts[0], attempts[1])
        self.assertIs(fixture.hass.data["circadian_pro"]["profile"], attempts[1])

    def test_switch_platform_completion_requires_entity_and_service_registration(self):
        namespace = dict(self.namespace)
        source = (COMPONENT / "switch.py").read_text(encoding="utf-8")
        for name in set(re.findall(r"\b[A-Z][A-Z_]+\b", source)):
            namespace.setdefault(name, name.lower())
        namespace.update({"DOMAIN": "circadian_pro", "ATTR_CIRCADIAN_PRO_MANAGER": "manager",
                          "SWITCH_DOMAIN": "switch", "SOURCE_IMPORT": "import", "callback": lambda fn: fn,
                          "_LOGGER": logging.getLogger("audit_config_platform"),
                          "SimpleSwitch": lambda **_kwargs: object(), "handle_change_switch_settings": object(),
                          "apply_service_schema": lambda _transition: object(), "SET_MANUAL_CONTROL_SCHEMA": object()})
        namespace["vol"].In = lambda _choices: lambda value: value
        load_nodes(namespace, "switch.py", {"async_setup_entry"})
        for fail_at_last_service in (False, True):
            with self.subTest(fail=fail_at_last_service):
                registered = []
                added = []
                manager = object()
                switch = SimpleNamespace(initial_transition=0)
                namespace["CircadianProManager"] = lambda _hass: manager
                namespace["CircadianProSwitch"] = lambda *_args: switch

                def register_entity_service(*_args):
                    if fail_at_last_service:
                        raise RuntimeError("entity service registration failed")
                    registered.append("entity")

                platform = SimpleNamespace(async_register_entity_service=register_entity_service)
                namespace["entity_platform"] = SimpleNamespace(current_platform=SimpleNamespace(get=lambda: platform))
                hass = SimpleNamespace(data={"circadian_pro": {"profile": {}}}, services=SimpleNamespace(
                    async_register=lambda **_kwargs: registered.append("domain")))
                entry = SimpleNamespace(entry_id="profile", source="user")
                def add_entities(entities, **kwargs):
                    added.extend(entities)
                    self.assertTrue(kwargs["update_before_add"])

                if fail_at_last_service:
                    with self.assertRaisesRegex(RuntimeError, "entity service registration failed"):
                        immediate(namespace["async_setup_entry"](hass, entry, add_entities))
                    self.assertNotIn("platform_setup_complete", hass.data["circadian_pro"]["profile"])
                else:
                    immediate(namespace["async_setup_entry"](hass, entry, add_entities))
                    self.assertTrue(hass.data["circadian_pro"]["profile"]["platform_setup_complete"])
                self.assertEqual(len(added), 4)
                self.assertEqual(registered, ["domain", "domain"] + ([] if fail_at_last_service else ["entity"]))

    def test_unload_failure_preserves_the_entry_and_manager(self):
        namespace, modules = self.lifecycle()
        disabled = []
        manager = SimpleNamespace(rebuild_lights=lambda: None, disable=lambda: disabled.append(True))

        async def unload(*_args):
            return False

        data = {"profile": {"switch": object()}, "manager": manager}
        hass = SimpleNamespace(data={"circadian_pro": data}, config_entries=SimpleNamespace(async_unload_platforms=unload))
        with patch.dict(sys.modules, modules):
            self.assertFalse(immediate(namespace["async_unload_entry"](hass, SimpleNamespace(entry_id="profile"))))
        self.assertIn("profile", data)
        self.assertIs(data["manager"], manager)
        self.assertEqual(disabled, [])

    def test_last_yaml_entry_unload_disables_manager_without_erasing_yaml_tracking(self):
        namespace, modules = self.lifecycle()
        disabled = []
        manager = SimpleNamespace(rebuild_lights=lambda: None, disable=lambda: disabled.append(True))

        async def unload(*_args):
            return True

        data = {"profile": {"switch": object()}, "manager": manager, "__yaml__": {"Bedroom"}}
        hass = SimpleNamespace(data={"circadian_pro": data}, config_entries=SimpleNamespace(async_unload_platforms=unload))
        with patch.dict(sys.modules, modules):
            self.assertTrue(immediate(namespace["async_unload_entry"](hass, SimpleNamespace(entry_id="profile"))))
        self.assertEqual(disabled, [True])
        self.assertEqual(data, {"__yaml__": {"Bedroom"}})

    def test_translations_cover_new_errors_selectors_and_schedule_fields(self):
        translations = [json.loads((COMPONENT / name).read_text(encoding="utf-8"))
                        for name in ("strings.json", "translations/en.json", "translations/es.json")]
        self.assertEqual(translations[0], translations[1])
        for data in translations:
            self.assertIn("profile_missing", data["config"]["error"])
            self.assertIn("invalid_sleep_schedule", data["options"]["error"])
            self.assertIn("invalid_solar_times", data["options"]["error"])
            self.assertIn("yaml_configuration", data["options"]["abort"])
            self.assertTrue({"sleep_schedule_start", "sleep_schedule_end"}.issubset(data["options"]["step"]["init"]["data"]))
            self.assertEqual(set(data["selector"]["sleep_activation_mode"]["options"]), {"disabled", "sun", "schedule"})
        self.assertEqual(translations[1]["selector"]["brightness_mode"]["options"]["linear"], "Linear")
        self.assertEqual(translations[2]["selector"]["brightness_mode"]["options"]["linear"], "Lineal")

    def test_manual_control_action_exposes_every_supported_choice(self):
        services = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
        section = services.split("    manual_control:\n", 1)[1].split("\nchange_switch_settings:", 1)[0]
        choices = re.findall(r"^          - (.+)$", section, re.MULTILINE)
        self.assertEqual({choice.strip('"') for choice in choices}, {"true", "false", "brightness", "color"})
        self.assertIn('default: "true"', section)
        self.assertIn("translation_key: manual_control", section)

    def test_selective_manual_control_survives_ha_validator_migration(self):
        class ModernInvalid(Exception):
            pass

        class Any:
            def __init__(self, *validators):
                self.validators = validators

            def __call__(self, value):
                for validate in self.validators:
                    try:
                        return validate(value)
                    except Invalid:
                        pass
                raise Invalid("No valid alternative")

        def membership(choices):
            def validate(value):
                if value not in choices:
                    raise Invalid("Not an accepted choice")
                return value
            return validate

        def ha_boolean(value):
            if isinstance(value, bool):
                return value
            if value in {"true", "false"}:
                return value == "true"
            raise ModernInvalid("Not a boolean")

        self.namespace["vol"].Any = Any
        self.namespace["vol"].In = membership
        self.namespace["cv"].boolean = ha_boolean
        self.namespace["cv"].entity_ids = lambda value: value
        self.namespace["CONF_ENTITY_ID"] = "entity_id"
        tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
        schema = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "SET_MANUAL_CONTROL_SCHEMA"
                              for target in node.targets))
        exec(compile(ast.Module(body=[schema], type_ignores=[]), "<real manual-control schema>", "exec"), self.namespace)
        validator = next(validate for key, validate in self.namespace["SET_MANUAL_CONTROL_SCHEMA"].schema.items()
                         if key.name == "manual_control")
        for value, expected in ((True, True), (False, False), ("true", True), ("false", False),
                                ("brightness", "brightness"), ("color", "color")):
            with self.subTest(value=value):
                self.assertEqual(validator(value), expected)

    def test_documentation_handles_number_optional_and_new_ha_validator_types(self):
        class Group:
            def __init__(self, *validators):
                self.validators = validators

        class Coerce:
            def __init__(self, target):
                self.type = target

        class Range:
            def __init__(self, minimum, maximum):
                self.min, self.max = minimum, maximum

        legacy = SimpleNamespace(All=type("LegacyAll", (Group,), {}), Any=type("LegacyAny", (Group,), {}),
                                 In=type("LegacyIn", (), {}), Coerce=Coerce, Range=Range, Clamp=Range)
        modern = SimpleNamespace(All=type("ModernAll", (Group,), {}), Any=type("ModernAny", (Group,), {}),
                                 In=type("ModernIn", (), {}), Coerce=Coerce, Range=Range)

        class NumberSelector:
            def __init__(self, **config):
                self.config = config

        selectors = SimpleNamespace(NumberSelector=NumberSelector, SelectSelector=type("Select", (), {}),
                                    ColorRGBSelector=type("Color", (), {}))
        namespace = {"vol": legacy, "ha_schema": modern, "selector": selectors,
                     "cv": SimpleNamespace(entity_ids=object(), boolean=object(), string=object())}
        load_nodes(namespace, "_docs_helpers.py", {"_type_to_str", "_format_voluptuous_instance"})
        describe = namespace["_type_to_str"]
        self.assertEqual(describe(NumberSelector(min=0, max=255)), "number 0-255")
        self.assertEqual(describe(legacy.Any(None, int)), "None or int")
        self.assertEqual(describe(modern.All(Coerce(float), Range(0, 60))), "float 0-60")
        self.assertEqual(describe(namespace["cv"].string), "str")


class LifecycleCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = ConfigAuditTests("test_setup_keeps_an_explicit_sleep_mode_even_with_legacy_flags")
        self.fixture.setUp()

    async def test_real_task_cancellation_releases_partial_setup_and_preserves_cancellation(self):
        entered = asyncio.Event()
        blocked = asyncio.Event()
        fixture = self.fixture.setup_harness(lambda *_args: None)

        async def forward(entry, _platforms):
            data = fixture.hass.data["circadian_pro"]
            data["manager"] = fixture.manager
            data[entry.entry_id]["switch"] = fixture.switch
            entered.set()
            await blocked.wait()

        fixture.hass.config_entries.async_forward_entry_setups = forward
        with patch.dict(sys.modules, fixture.modules):
            task = asyncio.create_task(fixture.namespace["async_setup_entry"](fixture.hass, fixture.entry))
            await entered.wait()
            task.cancel()
            with self.assertRaises(CancelledError):
                await task
        self.assertTrue(task.cancelled())
        self.assertNotIn("circadian_pro", fixture.hass.data)
        self.assertEqual(fixture.actions, ["listeners", ("unregister", fixture.switch), "disable"])

    async def test_real_forward_failure_after_an_await_keeps_the_original_error(self):
        original = ImportError("dependency failed after an await")
        fixture = self.fixture.setup_harness(lambda *_args: None)

        async def forward(entry, _platforms):
            fixture.hass.data["circadian_pro"]["manager"] = fixture.manager
            fixture.hass.data["circadian_pro"][entry.entry_id]["switch"] = fixture.switch
            await asyncio.sleep(0)
            raise original

        fixture.hass.config_entries.async_forward_entry_setups = forward
        with patch.dict(sys.modules, fixture.modules), self.assertRaises(ImportError) as caught:
            await fixture.namespace["async_setup_entry"](fixture.hass, fixture.entry)
        self.assertIs(caught.exception, original)
        self.assertNotIn("circadian_pro", fixture.hass.data)
        self.assertEqual(fixture.actions, ["listeners", ("unregister", fixture.switch), "disable"])


if __name__ == "__main__":
    unittest.main()
