"""Calibration options-flow regressions without Home Assistant or real lights.

Execute the production flow and cross-field validator with AST extraction.
Only HA form rendering and selectors are doubles. Preview sentinels detect any
unexpected hardware I/O from the standard options form.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import time, timedelta
from enum import Enum
import logging
import json
from math import isfinite
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


def immediate(coroutine):
    """Complete an async flow whose mocked I/O never yields background work."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("Unexpected background work in isolated config-flow test")


class Invalid(Exception):
    """Voluptuous's public validation exception."""


class Optional:
    """Expose a field and its default without replacing production control flow."""

    def __init__(self, name, default=None):
        self.name = name
        self.default = default


class Schema:
    def __init__(self, schema):
        self.schema = schema


def load_nodes(namespace, filename, names, class_name=None):
    """Load named production definitions, preserving their original bodies."""
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    if class_name is not None:
        tree = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    missing = names - {getattr(node, "name", None) for node in nodes}
    if missing:
        raise AssertionError(f"Production definitions not found: {sorted(missing)}")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(COMPONENT / filename), "exec"), namespace)


def production_namespace():
    """Extract real defaults and functions while leaving HA schema I/O stubbed."""
    namespace = {
        "__name__": "calibration_flow_tests.config_flow",
        "__package__": "calibration_flow_tests",
        "time": time,
        "timedelta": timedelta,
        "isfinite": isfinite,
        "Enum": Enum,
        "deepcopy": deepcopy,
        "_LOGGER": logging.getLogger("circadian_pro.flow_tests"),
        "vol": SimpleNamespace(Optional=Optional, Schema=Schema, Invalid=Invalid),
        "SelectorInvalid": Invalid,
        "cv": SimpleNamespace(time=lambda value: time.fromisoformat(value)),
        "config_entries": SimpleNamespace(SOURCE_IMPORT="import"),
        "EXTRA_VALIDATION": {},
    }
    tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            # Preserve constant names and real clock defaults in tuple assignments.
            if (
                len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)
                and isinstance(node.value, ast.Tuple)
            ):
                for target, part in zip(node.targets[0].elts, node.value.elts):
                    if not isinstance(target, ast.Name):
                        continue
                    try:
                        namespace[target.id] = ast.literal_eval(part)
                    except (ValueError, TypeError):
                        if isinstance(part, ast.Call) and isinstance(part.func, ast.Name) and part.func.id == "time":
                            namespace[target.id] = eval(compile(ast.Expression(part), "<real clock default>", "eval"), namespace)
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                namespace[target.id] = value
            elif isinstance(target, ast.Tuple) and all(isinstance(item, ast.Name) for item in target.elts):
                namespace.update({item.id: part for item, part in zip(target.elts, value)})

    # Keep the real defaults and field names. The schema does not do I/O here;
    # production validate_options/validate_cross_options enforce relationships.
    namespace["VALIDATION_TUPLES"] = [
        (value, namespace.get(name.replace("CONF_", "DEFAULT_", 1)), object())
        for name, value in list(namespace.items())
        if name.startswith("CONF_")
    ]
    namespace["selector"] = SimpleNamespace(
        NumberSelector=lambda config: config,
        NumberSelectorConfig=lambda **config: SimpleNamespace(**config),
        NumberSelectorMode=SimpleNamespace(BOX="box"),
        SelectSelector=lambda config: config,
        SelectSelectorConfig=lambda **config: SimpleNamespace(**config),
        SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
        TimeSelector=lambda config: config,
        TimeSelectorConfig=lambda **config: SimpleNamespace(**config),
    )
    namespace["EntitySelector"] = lambda config: config
    namespace["EntitySelectorConfig"] = lambda **config: SimpleNamespace(**config)
    load_nodes(namespace, "const.py", {"box_int", "replace_none_str", "_duration_seconds", "_time_value", "validate_cross_options"})
    load_nodes(namespace, "config_flow.py", {"options_from_form", "validate_entry", "validate_options"})

    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OptionsFlowHandler")
    handler.bases = []  # HA base provides form rendering, mocked by FakeFlow below.
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), handler],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(COMPONENT / "config_flow.py"), "exec"), namespace)
    return namespace


def form_fields(form):
    return {marker.name: (marker.default, selector) for marker, selector in form["data_schema"].schema.items()}


class ConfigCreationTests(unittest.TestCase):
    """Exercise the real user/menu/name steps without any light services."""

    def setUp(self):
        self.namespace = production_namespace()

        class AbortFlow(Exception):
            def __init__(self, reason):
                self.reason = reason

        self.namespace["AbortFlow"] = AbortFlow
        self.namespace["vol"].Required = Optional
        self.namespace["vol"].In = lambda values: values
        names = {"async_step_user", "async_step_menu", "async_step_wait_for_name"}
        load_nodes(self.namespace, "config_flow.py", names, class_name="ConfigFlow")

        class FakeConfigFlow:
            def __init__(self, entries=(), in_progress=()):
                self.entries = list(entries)
                self.in_progress = set(in_progress)
                self.source_options = None
                self.unique_id = None
                self.created_entries = []
                self.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda entry_id: next((entry for entry in self.entries if entry.entry_id == entry_id), None)))

            def _async_current_entries(self):
                return self.entries

            async def async_set_unique_id(self, name):
                if name in self.in_progress:
                    raise AbortFlow("already_in_progress")
                self.unique_id = name

            def _abort_if_unique_id_configured(self):
                if any(entry.unique_id == self.unique_id for entry in self.entries):
                    raise AbortFlow("already_configured")

            def async_show_form(self, **kwargs):
                return {"type": "form", **kwargs}

            def async_create_entry(self, **kwargs):
                self.created_entries.append(kwargs)
                return {"type": "create_entry", **kwargs}

        for name in names:
            setattr(FakeConfigFlow, name, self.namespace[name])
        self.Flow = FakeConfigFlow

    def test_new_profile_uses_empty_lights_default_until_options_select_lights(self):
        flow = self.Flow()
        form = immediate(flow.async_step_user())
        self.assertEqual(form["step_id"], "user")
        result = immediate(flow.async_step_user({"name": " Bedroom "}))
        self.assertEqual(result, {"type": "create_entry", "title": "Bedroom", "data": {"name": "Bedroom"}, "options": {}})
        entry = SimpleNamespace(data=result["data"], options=result["options"])
        self.assertEqual(self.namespace["validate_entry"](entry)["lights"], [])
        # Store lights in options, not creation data, so later selection wins.
        entry.options = {"lights": ["light.bedroom"]}
        self.assertEqual(self.namespace["validate_entry"](entry)["lights"], ["light.bedroom"])

    def test_existing_and_concurrent_names_return_recoverable_field_errors(self):
        entry = SimpleNamespace(entry_id="existing", title="Bedroom", unique_id="Bedroom", data={"name": "Bedroom"}, options={})
        for flow in (self.Flow([entry]), self.Flow(in_progress=["Bedroom"])):
            with self.subTest(flow=flow):
                form = immediate(flow.async_step_wait_for_name({"name": "Bedroom"}))
                self.assertEqual(form["type"], "form")
                self.assertEqual(form["step_id"], "user")
                self.assertEqual(form["errors"], {"name": "unique_name"})
                self.assertEqual(form_fields(form)["name"][0], "Bedroom")
                self.assertEqual(flow.created_entries, [])
                result = immediate(flow.async_step_user({"name": "Desk"}))
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(result["title"], "Desk")

    def test_blank_name_does_not_create_an_entry(self):
        flow = self.Flow()
        for name in ("", "  "):
            form = immediate(flow.async_step_user({"name": name}))
            self.assertEqual(form["errors"], {"name": "name_required"})
        self.assertIsNone(flow.unique_id)
        self.assertEqual(flow.created_entries, [])

    def test_existing_profile_menu_new_selection_clears_duplicate_options(self):
        entry = SimpleNamespace(entry_id="existing", title="Bedroom", unique_id="Bedroom", data={"name": "Bedroom"}, options={"lights": ["light.existing"]})
        flow = self.Flow([entry])
        self.assertEqual(immediate(flow.async_step_user())["step_id"], "menu")
        immediate(flow.async_step_menu({"action": "existing"}))
        self.assertEqual(flow.source_options["lights"], entry.options["lights"])
        self.assertNotIn("name", flow.source_options)
        form = immediate(flow.async_step_menu({"action": "new"}))
        self.assertEqual(form["step_id"], "user")
        result = immediate(flow.async_step_user({"name": "Desk"}))
        self.assertEqual(result["options"], {})
        self.assertEqual(entry.options, {"lights": ["light.existing"]})

    def test_creation_errors_have_labels_in_both_languages_and_base_strings(self):
        for filename in ("strings.json", "translations/en.json", "translations/es.json"):
            strings = json.loads((COMPONENT / filename).read_text(encoding="utf-8"))
            self.assertTrue({"unique_name", "name_required", "profile_missing"}.issubset(strings["config"]["error"]))
            self.assertIn("already_configured", strings["config"]["abort"])
            self.assertIn("name", strings["config"]["step"]["user"]["data"])


class CalibrationFlowTests(unittest.TestCase):
    def setUp(self):
        self.namespace = production_namespace()
        self.preview_calls = []
        self.stop_calls = []

        async def preview(hass, entry_id, settings, kelvin):
            self.preview_calls.append((entry_id, deepcopy(settings), kelvin))
            raise AssertionError("Standard form must not apply a light preview")

        async def stop(hass, entry_id):
            self.stop_calls.append(entry_id)
            raise AssertionError("Standard form must not control panel previews")

        package = ModuleType("calibration_flow_tests")
        package.__path__ = []
        calibration = ModuleType("calibration_flow_tests.calibration")
        calibration.async_preview_calibration = preview
        calibration.async_stop_calibration_preview = stop
        self.modules_patch = patch.dict(sys.modules, {"calibration_flow_tests": package, "calibration_flow_tests.calibration": calibration})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)

        handler = self.namespace["OptionsFlowHandler"]

        class FakeFlow(handler):
            def __init__(self, states, options):
                self.config_entry = SimpleNamespace(source="user", entry_id="entry-1", data={"name": "Living room"}, options=deepcopy(options))
                self.hass = SimpleNamespace(states=SimpleNamespace(get=states.get, async_entity_ids=lambda domain: list(states)))
                self.created_entries = []

            def async_show_form(self, **kwargs):
                return {"type": "form", **kwargs}

            def async_create_entry(self, **kwargs):
                self.created_entries.append(kwargs)
                return {"type": "create_entry", **kwargs}

        self.Flow = FakeFlow
        self.rgbww = SimpleNamespace(attributes={"supported_color_modes": ["rgbww", "color_temp"]})
        self.rgbw = SimpleNamespace(attributes={"supported_color_modes": ["rgbw"]})
        self.rgb = SimpleNamespace(attributes={"supported_color_modes": ["rgb"]})
        self.cct = SimpleNamespace(attributes={"supported_color_modes": ["color_temp"]})

    def flow(self, mode="rgbww", **overrides):
        options = {
            "lights": ["light.strip"],
            "white_w_kelvin": 3000,
            "white_cw_kelvin": None,
            "cal_white_kelvin_neutral": None,
            "cal_rgb_neutral_r": 111,
            "cal_rgb_warm_r": 222,
            "sleep_transition": 7,
            "hidden_option": {"keep": "this value"},
            **overrides,
        }
        state = {"rgbww": self.rgbww, "rgbw": self.rgbw, "rgb": self.rgb, "color_temp": self.cct}[mode]
        return self.Flow({"light.strip": state}, options)

    def start_calibration(self, flow):
        first = immediate(flow.async_step_init({"lights": ["light.strip"]}))
        if first["step_id"] == "cal_neutral":
            return first
        physical = {"white_w_kelvin": 3000}
        if flow._uses_rgbww():
            physical["white_cw_kelvin"] = 6500
        return immediate(flow.async_step_white_w_kelvin(physical))

    @staticmethod
    def neutral(**overrides):
        return {"cal_rgb_neutral_r": 101, "cal_rgb_neutral_g": 102, "cal_rgb_neutral_b": 103, "cal_white_intensity_neutral": 85, "cal_white_kelvin_neutral": 4500, **overrides}

    @staticmethod
    def warm(**overrides):
        return {"cal_rgb_warm_r": 244, "cal_rgb_warm_g": 40, "cal_rgb_warm_b": 0, "cal_white_intensity_warm": 4, **overrides}

    def test_standard_form_advances_and_finishes_without_preview_fields_or_io(self):
        flow = self.flow()
        neutral_form = self.start_calibration(flow)
        self.assertNotIn("preview_light", form_fields(neutral_form))
        self.assertFalse(neutral_form["last_step"])
        self.assertEqual(self.preview_calls, [])
        warm_form = immediate(flow.async_step_cal_neutral(self.neutral()))
        self.assertEqual(warm_form["step_id"], "cal_warm")
        self.assertNotIn("preview_light", form_fields(warm_form))
        self.assertTrue(warm_form["last_step"])
        result = immediate(flow.async_step_cal_warm(self.warm()))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.preview_calls, [])
        self.assertEqual(self.stop_calls, [])

    def test_neutral_submit_preserves_unsaved_draft_and_advances(self):
        flow = self.flow()
        original = deepcopy(flow.config_entry.options)
        self.start_calibration(flow)
        result = immediate(flow.async_step_cal_neutral(self.neutral()))
        self.assertEqual(result["step_id"], "cal_warm")
        self.assertEqual(result["errors"], {})
        draft = flow._draft_options()
        self.assertEqual(draft["cal_rgb_neutral_r"], 101)
        self.assertEqual(draft["cal_white_kelvin_neutral"], 4500)
        self.assertEqual(draft["hidden_option"], original["hidden_option"])
        self.assertNotIn("preview_light", draft)
        self.assertEqual(flow.config_entry.options, original)
        self.assertEqual(flow.created_entries, [])

    def test_final_submit_saves_both_calibration_drafts(self):
        flow = self.flow()
        self.start_calibration(flow)
        immediate(flow.async_step_cal_neutral(self.neutral()))
        result = immediate(flow.async_step_cal_warm(self.warm()))
        self.assertEqual(result["type"], "create_entry")
        draft = result["data"]
        self.assertEqual(draft["cal_rgb_neutral_r"], 101)
        self.assertEqual(draft["cal_rgb_warm_r"], 244)
        self.assertEqual(len(flow.created_entries), 1)
        self.assertNotIn("preview_light", draft)

    def test_final_save_preserves_hidden_options_and_physical_whites(self):
        flow = self.flow()
        self.start_calibration(flow)
        form = immediate(flow.async_step_cal_neutral(self.neutral()))
        self.assertEqual(form["step_id"], "cal_warm")
        result = immediate(flow.async_step_cal_warm(self.warm()))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["white_w_kelvin"], 3000)
        self.assertEqual(result["data"]["white_cw_kelvin"], 6500)
        self.assertEqual(result["data"]["sleep_transition"], 7)
        self.assertEqual(result["data"]["hidden_option"], {"keep": "this value"})
        self.assertEqual(result["data"]["cal_white_kelvin_neutral"], 4500)
        self.assertEqual(result["data"]["cal_rgb_warm_r"], 244)
        self.assertNotIn("preview_light", result["data"])

    def test_final_save_keeps_solar_offsets_in_seconds_without_form_aliases(self):
        flow = self.flow(mode="rgb", sunrise_offset=3600, sunset_offset=-3600)
        original = deepcopy(flow.config_entry.options)
        draft = {"lights": ["light.strip"], "sunrise_offset_minutes": 20,
                 "sunset_offset_minutes": -61 / 60}
        self.assertEqual(immediate(flow.async_step_init(draft))["step_id"], "cal_neutral")
        immediate(flow.async_step_cal_neutral(self.neutral()))
        result = immediate(flow.async_step_cal_warm(self.warm()))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["sunrise_offset"], 1200)
        self.assertEqual(result["data"]["sunset_offset"], -61)
        self.assertNotIn("sunrise_offset_minutes", result["data"])
        self.assertNotIn("sunset_offset_minutes", result["data"])
        self.assertEqual(flow.config_entry.options, original)
        self.assertEqual(draft["sunrise_offset_minutes"], 20)

    def test_standard_form_does_not_require_preview_module(self):
        flow = self.flow()
        self.start_calibration(flow)
        with patch.dict(sys.modules, {"calibration_flow_tests.calibration": None}):
            form = immediate(flow.async_step_cal_neutral(self.neutral()))
            self.assertEqual(form["step_id"], "cal_warm")
            result = immediate(flow.async_step_cal_warm(self.warm()))
        self.assertEqual(result["type"], "create_entry")

    def test_rgbww_has_physical_and_mixture_fields_rgbw_keeps_legacy_fields(self):
        for mode, physical_step, expected_physical in (
            ("rgbww", "white_channels", {"white_w_kelvin", "white_cw_kelvin"}),
            ("rgbw", "white_w_kelvin", {"white_w_kelvin"}),
        ):
            with self.subTest(mode=mode):
                flow = self.flow(mode)
                physical = immediate(flow.async_step_init({"lights": ["light.strip"]}))
                self.assertEqual(physical["step_id"], physical_step)
                self.assertFalse(physical["last_step"])
                self.assertEqual(set(form_fields(physical)), expected_physical)
                if mode == "rgbww":
                    self.assertEqual(form_fields(physical)["white_cw_kelvin"][0], 6500)
                neutral = self.start_calibration(flow)
                self.assertEqual("cal_white_kelvin_neutral" in form_fields(neutral), mode == "rgbww")
                self.assertEqual(form_fields(neutral)["cal_rgb_neutral_r"][0], 111)
                self.assertIn("cal_white_intensity_neutral", form_fields(neutral))
                warm = immediate(flow.async_step_cal_neutral(self.neutral()))
                self.assertIn("cal_white_intensity_warm", form_fields(warm))

    def test_rgb_only_skips_physical_whites_and_has_only_rgb_calibration(self):
        flow = self.flow("rgb")
        original = deepcopy(flow.config_entry.options)
        neutral = self.start_calibration(flow)
        self.assertEqual(neutral["step_id"], "cal_neutral")
        self.assertEqual(set(form_fields(neutral)), {
            "cal_rgb_neutral_r", "cal_rgb_neutral_g", "cal_rgb_neutral_b",
        })
        warm = immediate(flow.async_step_cal_neutral({
            "cal_rgb_neutral_r": 101, "cal_rgb_neutral_g": 102, "cal_rgb_neutral_b": 103,
        }))
        self.assertEqual(set(form_fields(warm)), {
            "cal_rgb_warm_r", "cal_rgb_warm_g", "cal_rgb_warm_b",
        })
        result = immediate(flow.async_step_cal_warm({
            "cal_rgb_warm_r": 244, "cal_rgb_warm_g": 40, "cal_rgb_warm_b": 0,
        }))
        self.assertEqual(result["type"], "create_entry")
        for name in ("white_w_kelvin", "white_cw_kelvin", "cal_white_kelvin_neutral"):
            self.assertEqual(result["data"][name], original[name])
        self.assertEqual(result["data"]["cal_rgb_neutral_r"], 101)
        self.assertEqual(result["data"]["hidden_option"], original["hidden_option"])
        self.assertEqual(self.preview_calls, [])

    def test_hiding_white_controls_preserves_existing_white_calibration(self):
        previous = {
            "white_w_kelvin": 2800, "white_cw_kelvin": 6200,
            "cal_white_kelvin_neutral": 4200,
            "cal_white_intensity_neutral": 73, "cal_white_intensity_warm": 9,
        }
        flow = self.flow("rgb", **previous)
        self.start_calibration(flow)
        immediate(flow.async_step_cal_neutral({"cal_rgb_neutral_r": 201}))
        result = immediate(flow.async_step_cal_warm({"cal_rgb_warm_r": 251}))
        for name, value in previous.items():
            self.assertEqual(result["data"][name], value)

    def test_mixed_selected_lights_show_only_their_available_white_channels(self):
        for white_state, physical_step, dual_white in (
            (self.rgbw, "white_w_kelvin", False),
            (self.rgbww, "white_channels", True),
        ):
            with self.subTest(physical_step=physical_step):
                flow = self.flow("rgb")
                flow.hass.states.get = {"light.rgb": self.rgb, "light.white": white_state}.get
                form = immediate(flow.async_step_init({"lights": ["light.rgb", "light.white"]}))
                self.assertEqual(form["step_id"], physical_step)
                self.assertEqual("white_cw_kelvin" in form_fields(form), dual_white)

    def test_cct_only_does_not_advertise_nonexistent_white_channel_controls(self):
        flow = self.flow("color_temp")
        neutral = self.start_calibration(flow)
        self.assertEqual(neutral["step_id"], "cal_neutral")
        self.assertNotIn("cal_white_intensity_neutral", form_fields(neutral))
        self.assertNotIn("cal_white_kelvin_neutral", form_fields(neutral))

    def test_conditional_fields_follow_newly_selected_lights(self):
        flow = self.flow("rgbw")
        flow.hass.states.get = {"light.old": self.rgbw, "light.new": self.rgbww}.get
        form = immediate(flow.async_step_init({"lights": ["light.new"]}))
        self.assertEqual(form["step_id"], "white_channels")
        self.assertEqual(flow._base_options["lights"], ["light.new"])
        self.assertEqual(flow._base_options["sleep_transition"], 7)

    def test_optional_defaults_remain_auto_without_overwriting_existing_w(self):
        self.assertIsNone(self.namespace["DEFAULT_WHITE_CW_KELVIN"])
        self.assertIsNone(self.namespace["DEFAULT_CAL_WHITE_KELVIN_NEUTRAL"])
        self.assertEqual(self.namespace["DEFAULT_WHITE_W_KELVIN"], 3000)
        flow = self.flow()
        self.start_calibration(flow)
        self.assertEqual(flow._base_options["white_w_kelvin"], 3000)

    def test_physical_white_range_rejects_equal_or_reversed_endpoints(self):
        for warm, cold in ((3000, 3000), (6500, 3000)):
            with self.subTest(warm=warm, cold=cold):
                flow = self.flow()
                immediate(flow.async_step_init({"lights": ["light.strip"]}))
                form = immediate(flow.async_step_white_channels({"white_w_kelvin": warm, "white_cw_kelvin": cold}))
                self.assertEqual(form["step_id"], "white_channels")
                self.assertEqual(form["errors"], {"base": "invalid_white_channel_range"})
                self.assertEqual(self.preview_calls, [])

    def test_neutral_mixture_rejects_values_outside_physical_range(self):
        for mixture in (2500, 7000):
            with self.subTest(mixture=mixture):
                flow = self.flow()
                self.start_calibration(flow)
                form = immediate(flow.async_step_cal_neutral(self.neutral(cal_white_kelvin_neutral=mixture)))
                self.assertEqual(form["errors"], {"base": "invalid_white_neutral_range"})
                self.assertEqual(form_fields(form)["cal_rgb_neutral_r"][0], 101)
                self.assertEqual(self.preview_calls, [])

    def test_changed_endpoints_allow_correcting_neutral_on_next_step(self):
        flow = self.flow(cal_white_kelvin_neutral=4000)
        immediate(flow.async_step_init({"lights": ["light.strip"]}))
        form = immediate(flow.async_step_white_channels({"white_w_kelvin": 4500, "white_cw_kelvin": 6500}))
        self.assertEqual(form["step_id"], "cal_neutral")
        default, selector = form_fields(form)["cal_white_kelvin_neutral"]
        self.assertEqual((default, selector.min, selector.max), (4500, 4500, 6500))
        result = immediate(flow.async_step_cal_neutral(self.neutral(cal_white_kelvin_neutral=4500)))
        self.assertEqual(result["step_id"], "cal_warm")

    def test_white_mixture_selector_supports_exact_kelvin_values_and_endpoints(self):
        flow = self.flow(cal_white_kelvin_neutral=4123)
        immediate(flow.async_step_init({"lights": ["light.strip"]}))
        form = immediate(flow.async_step_white_channels({"white_w_kelvin": 2723, "white_cw_kelvin": 6537}))
        default, selector = form_fields(form)["cal_white_kelvin_neutral"]
        self.assertEqual((default, selector.min, selector.max, selector.step), (4123, 2723, 6537, 1))
        for mixture in (2723, 4123, 6537):
            result = immediate(flow.async_step_cal_neutral(self.neutral(cal_white_kelvin_neutral=mixture)))
            self.assertEqual(result["step_id"], "cal_warm")

    def test_first_step_is_not_last_and_translations_use_next_and_finish(self):
        form = immediate(self.flow().async_step_init())
        self.assertEqual(form["step_id"], "init")
        self.assertFalse(form["last_step"])
        self.assertNotIn("preview_light", form_fields(form))
        for filename, next_label, finish_label in (
            ("strings.json", "Next", "Finish"),
            ("translations/en.json", "Next", "Finish"),
            ("translations/es.json", "Siguiente", "Terminar"),
        ):
            with self.subTest(filename=filename):
                strings = json.loads((COMPONENT / filename).read_text(encoding="utf-8"))
                steps = strings["options"]["step"]
                for name in ("init", "white_w_kelvin", "white_channels", "cal_neutral"):
                    self.assertEqual(steps[name]["submit"], next_label)
                self.assertEqual(steps["cal_warm"]["submit"], finish_label)
                self.assertNotIn("preview_light", steps["cal_neutral"]["data"])
                self.assertNotIn("preview_light", steps["cal_warm"]["data"])


if __name__ == "__main__":
    unittest.main()
