"""Lighting-engine regressions without Home Assistant or controller I/O.

Load complete production modules, replacing only HA imports. Service-job doubles
use actual executor threads and HA's public job-type and response conventions.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta, timezone
from enum import Enum
import inspect
from pathlib import Path
import re
import sys
import threading
from types import MappingProxyType, ModuleType, SimpleNamespace
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("Unexpected background wait in isolated lighting test")


class HassJobType(Enum):
    Coroutinefunction = 1
    Callback = 2
    Executor = 3


class SupportsResponse(Enum):
    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


def job_type(target):
    if inspect.iscoroutinefunction(target):
        return HassJobType.Coroutinefunction
    if getattr(target, "_hass_callback", None) is True:
        return HassJobType.Callback
    return HassJobType.Executor


def load_module(filename):
    source = (COMPONENT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Import all stdlib dependencies and retain every production definition.
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom)
        and (node.level or (node.module or "").startswith("homeassistant"))
    )]
    if not any(isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in tree.body):
        tree.body.insert(1, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    name = f"lighting_audit_tests_{Path(filename).stem}"
    module = ModuleType(name)
    module.__dict__.update({
        "HassJobType": HassJobType, "ReadOnlyDict": MappingProxyType,
        "color_temperature_to_rgb": lambda kelvin: (255, 128, 64),
        "color_RGB_to_xy": lambda *rgb: (0.4, 0.3),
        "color_xy_to_hs": lambda *xy: (30, 80),
    })
    for attribute in set(re.findall(r"\bATTR_[A-Z_]+\b", source)):
        module.__dict__[attribute] = attribute.removeprefix("ATTR_").lower()
    sys.modules[name] = module
    exec(compile(ast.fix_missing_locations(tree), str(COMPONENT / filename), "exec"), module.__dict__)
    return module


class FixedAstral:
    """Deterministic solar endpoints, with call counts for repeated work."""

    def __init__(self):
        self.calls = []

    def event(self, name, day, hour):
        self.calls.append((name, day))
        if isinstance(day, datetime):
            day = day.date()
        return datetime.combine(day, time(hour), UTC)

    def sunrise(self, day, local=False):
        return self.event("sunrise", day, 6)

    def sunset(self, day, local=False):
        return self.event("sunset", day, 18)

    def noon(self, day, local=False):
        return self.event("noon", day, 12)

    def midnight(self, day, local=False):
        return self.event("midnight", day, 0)


class LightingTests(unittest.TestCase):
    def setUp(self):
        self.color = load_module("color_and_brightness.py")
        self.adaptation = load_module("adaptation_utils.py")
        self.helpers = load_module("helpers.py")

    def tearDown(self):
        for name in list(sys.modules):
            if name.startswith("lighting_audit_tests_"):
                sys.modules.pop(name)

    def settings(self, **overrides):
        values = dict(
            name="Test", astral_location=FixedAstral(), adapt_until_sleep=False,
            max_brightness=100, max_color_temp=6500, min_brightness=10,
            min_color_temp=2000, sleep_brightness=5,
            sleep_rgb_or_color_temp="color_temp", sleep_color_temp=1000,
            sleep_rgb_color=(255, 56, 0), sunrise_time=None, min_sunrise_time=None,
            max_sunrise_time=None, sunset_time=None, min_sunset_time=None,
            max_sunset_time=None, brightness_mode_time_dark=timedelta(hours=1),
            brightness_mode_time_light=timedelta(hours=1),
        )
        values.update(overrides)
        return self.color.SunLightSettings(**values)

    def test_each_settings_sample_calculates_the_solar_events_once_for_every_mode(self):
        for mode in ("default", "linear", "tanh"):
            with self.subTest(mode=mode):
                settings = self.settings(brightness_mode=mode)
                settings.brightness_and_color(datetime(2026, 10, 13, 7, 30, tzinfo=UTC), False)
                self.assertEqual(len(settings.astral_location.calls), 12)

    def test_day_night_endpoints_and_existing_brightness_curves_are_preserved(self):
        for mode in ("default", "linear", "tanh"):
            settings = self.settings(brightness_mode=mode)
            for hour, position, kelvin in ((0, -1, 2000), (6, 0, 2000), (9, 0.75, 5375), (12, 1, 6500), (15, 0.75, 5375), (18, 0, 2000), (21, -0.75, 2000)):
                with self.subTest(mode=mode, hour=hour):
                    result = settings.brightness_and_color(datetime(2026, 10, 13, hour, tzinfo=UTC), False)
                    self.assertAlmostEqual(result["sun_position"], position)
                    self.assertEqual(result["color_temp_kelvin"], kelvin)
                    if mode == "default":
                        expected = 100 if position > 0 else 10 + 90 * (1 + position)
                        self.assertEqual(result["brightness_pct"], expected)
                    elif hour in (6, 18):
                        self.assertAlmostEqual(result["brightness_pct"], 55)
                    elif mode == "linear":
                        self.assertEqual(result["brightness_pct"], 100 if 6 < hour < 18 else 10)
                    else:
                        self.assertAlmostEqual(result["brightness_pct"], 100 if 6 < hour < 18 else 10, delta=0.02)

    def test_linear_and_tanh_keep_their_transition_reference_points(self):
        for mode, low, high in (("linear", 10, 100), ("tanh", 14.5, 95.5)):
            settings = self.settings(brightness_mode=mode)
            for hour, brightness in ((5, low), (7, high), (17, high), (19, low)):
                result = settings.brightness_and_color(datetime(2026, 10, 13, hour, tzinfo=UTC), False)
                self.assertAlmostEqual(result["brightness_pct"], brightness)

    def test_twilight_temperature_interpolates_both_directions_without_a_sunrise_jump(self):
        for min_kelvin, sleep_kelvin in ((1500, 2500), (2500, 1500)):
            settings = self.settings(adapt_until_sleep=True)
            object.__setattr__(settings, "min_color_temp", min_kelvin)
            object.__setattr__(settings, "sleep_color_temp", sleep_kelvin)
            self.assertEqual(settings.color_temp_kelvin(0), min_kelvin)
            self.assertEqual(settings.color_temp_kelvin(-0.5), 2000)
            self.assertEqual(settings.color_temp_kelvin(-1), sleep_kelvin)
            self.assertLessEqual(abs(settings.color_temp_kelvin(-0.001) - min_kelvin), 5)

    def test_sleep_values_and_rgb_transition_behavior_are_preserved(self):
        settings = self.settings(adapt_until_sleep=True)
        object.__setattr__(settings, "sleep_rgb_or_color_temp", "rgb_color")
        moment = datetime(2026, 10, 13, 0, tzinfo=UTC)
        awake = settings.brightness_and_color(moment, False)
        asleep = settings.brightness_and_color(moment, True)
        self.assertTrue(awake["force_rgb_color"])
        self.assertEqual(awake["rgb_color"], (255, 56, 0))
        self.assertEqual(asleep["brightness_pct"], 5)
        self.assertEqual(asleep["color_temp_kelvin"], 1000)
        self.assertEqual(asleep["rgb_color"], (255, 56, 0))

    def test_fixed_local_times_offsets_and_bounds_are_preserved(self):
        settings = self.settings()
        object.__setattr__(settings, "timezone", timezone(timedelta(hours=9)))
        object.__setattr__(settings, "sunrise_time", time(7))
        object.__setattr__(settings, "sunset_time", time(19))
        object.__setattr__(settings, "sunrise_offset", timedelta(hours=2))
        object.__setattr__(settings, "max_sunrise_time", time(8))
        day = datetime(2026, 10, 13, tzinfo=UTC)
        self.assertEqual(settings.sun.sunrise(day), datetime(2026, 10, 12, 23, tzinfo=UTC))
        self.assertEqual(settings.sun.sunset(day), datetime(2026, 10, 13, 10, tzinfo=UTC))
        settings.sun.sun_events(day)  # Fixed times must still satisfy circular event order.

    def test_split_commands_preserve_zero_brightness_and_relative_steps(self):
        for attribute in ("brightness", "brightness_pct", "brightness_step", "brightness_step_pct"):
            with self.subTest(attribute=attribute):
                payload = {"entity_id": "light.test", attribute: 0, "rgb_color": (0, 0, 0), "transition": 10}
                split = self.adaptation._split_service_call_data(payload)
                self.assertEqual(split, [{"entity_id": "light.test", attribute: 0, "transition": 5}, {"entity_id": "light.test", "rgb_color": (0, 0, 0), "transition": 5}])
                self.assertIn(self.adaptation.LightControlAttributes.BRIGHTNESS, self.adaptation._identify_light_control_attributes(payload))
                self.assertEqual(payload["transition"], 10)

    def test_manual_attribute_detection_accepts_flat_and_ha_preprocessed_params(self):
        flags = self.adaptation.LightControlAttributes
        for params, expected in (({"brightness": 0}, flags.BRIGHTNESS), ({"brightness_pct": 20}, flags.BRIGHTNESS), ({"rgbww_color": (0, 0, 0, 30, 80)}, flags.COLOR), ({"color_temp_kelvin": 2200}, flags.COLOR), ({"effect": "Solid"}, flags.ALL), ({"flash": "short"}, flags.ALL), ({}, flags.NONE)):
            for nested in (False, True):
                with self.subTest(params=params, nested=nested):
                    payload = {"entity_id": ["light.test"], "params": MappingProxyType(params)} if nested else params
                    self.assertEqual(self.adaptation.get_light_control_attributes(payload), expected)

    def test_derived_color_does_not_hide_a_requested_supported_mode_change(self):
        values = {"rgb_color": (50, 60, 70), "rgbw_color": (50, 60, 70, 80), "rgbww_color": (50, 60, 70, 80, 90), "xy_color": (0.4, 0.3), "hs_color": (30, 80), "color_temp_kelvin": 3000}
        for attribute, value in values.items():
            requested_mode = "color_temp" if attribute == "color_temp_kelvin" else attribute.removesuffix("_color")
            mode = "rgb" if requested_mode == "color_temp" else "color_temp"
            state = SimpleNamespace(attributes={attribute: value, "color_mode": mode, "supported_color_modes": [mode, requested_mode]})
            payload = {"entity_id": "light.test", attribute: value}
            with self.subTest(attribute=attribute):
                self.assertEqual(self.adaptation._remove_redundant_attributes(payload, state), payload)
                state.attributes["color_mode"] = requested_mode
                state.attributes[attribute] = list(value) if isinstance(value, tuple) else value
                self.assertEqual(self.adaptation._remove_redundant_attributes(payload, state), {"entity_id": "light.test"})

    def test_converted_rgb_request_keeps_existing_redundancy_for_hs_only_lights(self):
        payload = {"entity_id": "light.test", "rgb_color": (50, 60, 70)}
        state = SimpleNamespace(attributes={"color_mode": "hs", "supported_color_modes": ["hs"], "rgb_color": (50, 60, 70)})
        self.assertEqual(self.adaptation._remove_redundant_attributes(payload, state), {"entity_id": "light.test"})

    def test_profile_context_tags_use_the_entire_name(self):
        self.assertNotEqual(self.helpers.short_hash("Profilé A"), self.helpers.short_hash("Profilé B"))
        self.assertNotEqual(self.helpers.short_hash("Bedroom"), self.helpers.short_hash("Bedside"))
        tag = self.helpers.short_hash("Profilé A")
        self.assertEqual(tag, self.helpers.short_hash("Profilé A"))
        self.assertEqual(len(tag), 4)
        self.assertRegex(tag, "^[A-Z2-7]{4}$")


class FakeRegistry:
    def __init__(self):
        self._services = {}
        self.registrations = []

    def async_register(self, domain, service, target, schema=None, supports_response=SupportsResponse.NONE, job_type=None, description_placeholders=None):
        self.registrations.append(target)
        self._services.setdefault(domain, {})[service] = SimpleNamespace(job=SimpleNamespace(target=target, job_type=job_type or globals()["job_type"](target)), schema=schema, supports_response=supports_response, description_placeholders=description_placeholders)


class InterceptorTests(unittest.TestCase):
    def setUp(self):
        self.utils = load_module("hass_utils.py")
        self.registry = FakeRegistry()
        self.executor_calls = []
        self.original_calls = []
        self.interceptions = []
        self.loop_thread = threading.get_ident()

        async def executor(target, *args):
            self.executor_calls.append(target)
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(target, *args).result()

        self.hass = SimpleNamespace(services=self.registry, async_add_executor_job=executor)

    def tearDown(self):
        sys.modules.pop("lighting_audit_tests_hass_utils", None)

    def call(self):
        return SimpleNamespace(data=MappingProxyType({"entity_id": ["light.test"], "params": {"brightness": 17}}), context=object(), return_response=True)

    def register(self, target):
        self.schema = object()
        self.placeholders = {"name": "Original service"}
        self.registry.async_register("light", "turn_on", target, self.schema, SupportsResponse.OPTIONAL, description_placeholders=self.placeholders)

        def intercept(call, data):
            self.interceptions.append(threading.get_ident())
            data["params"]["brightness"] = 42

        remove = self.utils.setup_service_call_interceptor(self.hass, "light", "turn_on", intercept)
        return self.registry._services["light"]["turn_on"], remove

    def test_interceptor_keeps_schema_response_metadata_and_original_result(self):
        async def original(call):
            return {"brightness": call.data["params"]["brightness"]}

        service, remove = self.register(original)
        self.assertIs(service.schema, self.schema)
        self.assertEqual(service.supports_response, SupportsResponse.OPTIONAL)
        self.assertEqual(service.description_placeholders, self.placeholders)
        self.assertEqual(immediate(service.job.target(self.call())), {"brightness": 42})
        remove()
        restored = self.registry._services["light"]["turn_on"]
        self.assertIs(restored.job.target, original)
        self.assertIs(restored.schema, self.schema)
        self.assertEqual(restored.supports_response, SupportsResponse.OPTIONAL)
        self.assertEqual(restored.description_placeholders, self.placeholders)

    def test_executor_handlers_use_a_worker_and_callbacks_stay_on_the_loop(self):
        for callback in (False, True):
            with self.subTest(callback=callback):
                threads = []

                def original(call):
                    threads.append(threading.get_ident())
                    return {"ok": True}

                if callback:
                    original._hass_callback = True
                service, remove = self.register(original)
                self.assertEqual(immediate(service.job.target(self.call())), {"ok": True})
                self.assertEqual(threads[0] == self.loop_thread, callback)
                self.assertEqual(self.interceptions[-1], self.loop_thread)
                self.assertEqual(len(self.executor_calls), 1)
                remove()
                self.assertEqual(self.registry._services["light"]["turn_on"].job.job_type, job_type(original))

    def test_failed_interception_does_not_apply_partial_nested_edits(self):
        async def original(call):
            self.original_calls.append(call.data["params"]["brightness"])
            return {"brightness": call.data["params"]["brightness"]}

        self.registry.async_register("light", "turn_on", original)

        def failed(call, data):
            data["params"]["brightness"] = 42
            raise ValueError("Simulated interception failure")

        self.utils.setup_service_call_interceptor(self.hass, "light", "turn_on", failed)
        target = self.registry._services["light"]["turn_on"].job.target
        with self.assertLogs(self.utils._LOGGER, level="ERROR"):
            result = immediate(target(self.call()))
        self.assertEqual(self.original_calls, [17])
        self.assertEqual(result, {"brightness": 17})

    def test_removal_keeps_later_handlers_and_disables_the_old_proxy(self):
        async def original(call):
            return {"brightness": call.data["params"]["brightness"]}

        service, remove = self.register(original)

        async def newer(call):
            return await service.job.target(call)

        self.registry.async_register("light", "turn_on", newer)
        count = len(self.registry.registrations)
        remove()
        remove()
        self.assertEqual(len(self.registry.registrations), count)
        self.assertIs(self.registry._services["light"]["turn_on"].job.target, newer)
        self.assertEqual(immediate(newer(self.call())), {"brightness": 17})
        self.assertEqual(self.interceptions, [])

    def test_area_selection_keeps_entity_overrides_over_device_areas(self):
        explicit = SimpleNamespace(entity_id="light.direct", area_id="kitchen")
        inherited = SimpleNamespace(entity_id="light.inherited", area_id=None)
        overridden = SimpleNamespace(entity_id="light.other", area_id="bedroom")
        self.utils.entity_registry = SimpleNamespace(async_get=lambda hass: object(), async_entries_for_area=lambda registry, area: [explicit], async_entries_for_device=lambda registry, device: [inherited, overridden])
        self.utils.device_registry = SimpleNamespace(async_get=lambda hass: object(), async_entries_for_area=lambda registry, area: [SimpleNamespace(id="device")])
        self.assertEqual(self.utils.area_entities(self.hass, "kitchen"), ["light.direct", "light.inherited"])


if __name__ == "__main__":
    unittest.main()
