"""Preview lifecycle regressions without Home Assistant or a running controller."""

from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import inspect
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("Unexpected background work in preview lifecycle test")


def load_preview_module(*, real_io_timeout=False):
    """Load the complete production module; replace only HA and platform I/O."""
    saved = dict(sys.modules)
    package = ModuleType("preview_tests_package")
    package.__path__ = [str(COMPONENT)]
    const = ModuleType("preview_tests_package.const")
    const.DOMAIN = "circadian_pro"
    switch = ModuleType("preview_tests_package.switch")
    source = ast.parse((COMPONENT / "switch.py").read_text(encoding="utf-8"))
    functions = [node for node in source.body if getattr(node, "name", None) in {"_expand_light_groups", "_is_light_group"}]
    import logging

    switch.__dict__["_LOGGER"] = logging.getLogger("preview_tests")
    ast_module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(ast_module), "<real light-group helpers>", "exec"), switch.__dict__)
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    ha_const = ModuleType("homeassistant.const")
    ha_const.EVENT_CALL_SERVICE = "call_service"
    ha_const.EVENT_STATE_CHANGED = "state_changed"
    ha_const.STATE_ON = "on"
    ha_const.STATE_OFF = "off"
    core = ModuleType("homeassistant.core")

    def callback(function):
        # Match HA's callback marker, which controls HassJob's executor dispatch.
        function._hass_callback = True
        return function

    core.callback = callback
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    class PermissionErrorStub(exceptions.HomeAssistantError):
        def __init__(self, **kwargs):
            super().__init__(str(kwargs))

    permission_helpers = ModuleType("preview_tests_package.hass_utils")
    permission_helpers.__dict__.update(
        POLICY_CONTROL="control", UnknownUser=PermissionErrorStub, Unauthorized=PermissionErrorStub,
    )
    permission_tree = ast.parse((COMPONENT / "hass_utils.py").read_text(encoding="utf-8"))
    permission_functions = [node for node in permission_tree.body if getattr(node, "name", None) == "async_check_entity_control"]
    permission_module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *permission_functions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(permission_module), "<real permission helper>", "exec"), permission_helpers.__dict__)
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    event = ModuleType("homeassistant.helpers.event")
    event.async_call_later = lambda hass, seconds, callback: hass.add_timer(seconds, callback)
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: SimpleNamespace(async_get=hass.registry.get)
    helpers.entity_registry = entity_registry
    sys.modules.update({
        "preview_tests_package": package, "preview_tests_package.const": const,
        "preview_tests_package.switch": switch, "homeassistant": ha,
        "preview_tests_package.hass_utils": permission_helpers,
        "homeassistant.const": ha_const, "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers, "homeassistant.helpers.event": event,
        "homeassistant.helpers.entity_registry": entity_registry,
    })
    name = "preview_tests_package.calibration"
    spec = importlib.util.spec_from_file_location(name, COMPONENT / "calibration.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not real_io_timeout:
        class ImmediateDeadline:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        # These synchronous coroutine tests replace HA/controller I/O. The
        # integrated manager tests retain real asyncio deadlines and scheduling.
        module.asyncio = SimpleNamespace(Lock=asyncio.Lock, timeout=lambda _: ImmediateDeadline())
    # Keep the lazy platform import available for the duration of each test.
    return module, saved


class FakeState:
    def __init__(self, state="on", context=None, **attributes):
        self.state = state
        self.context = context or SimpleNamespace(id="external", parent_id=None, user_id=None)
        self.attributes = {
            "supported_color_modes": ["rgbww", "color_temp"],
            "color_mode": "rgbww", "brightness": 177,
            "rgbww_color": (10, 20, 30, 40, 50), "effect": "Solid",
            **attributes,
        }


class FakeBus:
    def __init__(self, hass):
        self.hass = hass
        self.listeners = []

    def async_listen(self, event_type, callback):
        self.listeners.append(callback)

        def remove():
            self.listeners.remove(callback)

        return remove

    def emit(self, data, context=None):
        event = SimpleNamespace(data=data, context=context or SimpleNamespace(id="external", parent_id=None, user_id=None))
        for callback in list(self.listeners):
            self.hass.run_job(callback, event)


class FakeManager:
    def __init__(self):
        self.last_service_data = {}
        self.manual_control = {"light.one": "color"}
        self.resets = []

    def _get_entity_list(self, data):
        targets = data.get("entity_id", [])
        if targets == "all":
            return list(self.hass.state_map)
        return [target.strip() for target in targets.split(",")] if isinstance(targets, str) else list(targets)

    def reset(self, *lights, reset_manual_control=True):
        self.resets.append((lights, reset_manual_control))
        if reset_manual_control:
            raise AssertionError("A calibration preview must preserve manual-control state")
        for light in lights:
            self.last_service_data.pop(light, None)


class FakeSwitch:
    def __init__(self, hass, manager, lights):
        self.hass = hass
        self.manager = manager
        self.lights = list(lights)
        self._configured_lights = list(lights)
        self._current_settings = {
            "lights": list(lights), "max_brightness": 80, "min_brightness": 20,
            "min_color_temp": 1200, "max_color_temp": 4000,
            "sleep_activation_mode": "sun", "transition_until_sleep": True,
            "sleep_schedule_enabled": True, "cal_rgb_neutral_r": 255,
        }
        self.context_count = 0
        self.fail_light = None

    def create_context(self, kind):
        self.context_count += 1
        return SimpleNamespace(id=f"calibration-{self.context_count}", parent_id=None, user_id=None)

    def _set_changeable_settings(self, data, defaults):
        self._current_settings = deepcopy(data)

    async def prepare_adaptation_data(self, light, **kwargs):
        assert kwargs["adapt_color"] and kwargs["adapt_brightness"] and kwargs["force"]
        assert kwargs["transition"] == 0
        assert self._effective_sleep_state() is False
        assert self._current_settings["sleep_activation_mode"] == "disabled"
        assert self._current_settings["transition_until_sleep"] is False
        assert self._current_settings["sleep_schedule_enabled"] is False
        assert self._current_settings["min_brightness"] == self._current_settings["max_brightness"]
        assert self._current_settings["min_color_temp"] == self._current_settings["max_color_temp"]
        if light == self.fail_light:
            raise ValueError("Test preparation failure")
        kelvin = self._current_settings["min_color_temp"]
        payload = {
            "entity_id": light, "brightness": round(255 * self._current_settings["max_brightness"] / 100),
            "rgbww_color": (self._current_settings["cal_rgb_neutral_r"], 100, 50, kelvin // 100, 10),
        }
        return SimpleNamespace(payload=payload, context=kwargs["context"])

    async def execute_cancellable_adaptation_calls(self, data):
        self.manager.last_service_data[data.payload["entity_id"]] = data.payload
        await self.hass.services.async_call("light", "turn_on", data.payload, context=data.context)


class FakeHass:
    def __init__(self):
        self.data = {}
        self.loop_thread_id = threading.get_ident()
        self.job_routes = []
        self.state_map = {"light.one": FakeState(), "light.two": FakeState()}
        self.states = SimpleNamespace(get=self.state_map.get)
        self.bus = FakeBus(self)
        self.calls = []
        self.timers = []
        self.services = SimpleNamespace(async_call=self.call)
        self.registry = {"light.one": SimpleNamespace(platform="wled", config_entry_id="wled-device")}
        self.wled_config = SimpleNamespace(cct_blend=0)
        self.config_entries = SimpleNamespace(async_get_entry=lambda entry_id: SimpleNamespace(runtime_data=SimpleNamespace(data=SimpleNamespace(led_config=self.wled_config))))

    async def call(self, domain, service, payload, **kwargs):
        assert kwargs.get("blocking") is True
        self.bus.emit({"domain": domain, "service": service, "service_data": payload}, kwargs["context"])
        self.calls.append((service, deepcopy(payload)))
        light = payload["entity_id"]
        previous = self.state_map[light]
        attributes = deepcopy(previous.attributes)
        attributes.update({key: value for key, value in payload.items() if key not in {"entity_id", "transition"}})
        for mode, attribute in (("rgbww", "rgbww_color"), ("rgbw", "rgbw_color"), ("color_temp", "color_temp_kelvin")):
            if attribute in payload:
                attributes["color_mode"] = mode
                break
        self.state_map[light] = FakeState("off" if service == "turn_off" else "on", context=kwargs["context"], **attributes)

    def add_timer(self, seconds, callback):
        timer = SimpleNamespace(seconds=seconds, callback=callback, cancelled=False)
        self.timers.append(timer)

        def cancel():
            timer.cancelled = True

        return cancel

    def async_create_task(self, coroutine):
        if threading.get_ident() != self.loop_thread_id:
            coroutine.close()
            raise RuntimeError("async_create_task called outside the event loop")
        return immediate(coroutine)

    def run_job(self, callback, *args):
        """Model HA's Callback/Executor routing with an actual executor thread."""
        if inspect.iscoroutinefunction(callback):
            self.job_routes.append("coroutine")
            return immediate(callback(*args))
        if getattr(callback, "_hass_callback", None) is True:
            self.job_routes.append("callback")
            return callback(*args)
        self.job_routes.append("executor")
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(callback, *args).result()

    def add_profile(self, entry_id, lights):
        manager = FakeManager()
        manager.hass = self
        switch = FakeSwitch(self, manager, lights)
        self.data.setdefault("circadian_pro", {})[entry_id] = {"switch": switch}
        return switch


class CalibrationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.preview, self.saved_modules = load_preview_module()
        self.hass = FakeHass()
        self.switch = self.hass.add_profile("one", ["light.one"])

    def tearDown(self):
        for name in set(sys.modules) - self.saved_modules.keys():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules.pop(name, None)
        for name, module in self.saved_modules.items():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules[name] = module

    def apply(self, kelvin=4000, **draft):
        return immediate(self.preview.async_preview_calibration(self.hass, "one", draft, kelvin))

    def stop(self):
        return immediate(self.preview.async_stop_calibration_preview(self.hass, "one"))

    def test_preview_is_explicit_and_does_not_save_or_modify_loaded_settings(self):
        before = deepcopy(self.switch._current_settings)
        self.assertFalse(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        self.assertEqual(self.hass.calls, [])
        self.assertTrue(self.apply(cal_rgb_neutral_r=170, preview_light=True))
        self.assertEqual(self.hass.calls[-1][1]["rgbww_color"][0], 170)
        self.assertEqual(self.switch._current_settings, before)
        self.assertEqual(self.switch.lights, ["light.one"])
        self.assertEqual(self.switch.manager.manual_control, {"light.one": "color"})
        self.assertEqual(self.hass.timers[-1].seconds, 60)
        self.assertEqual(self.hass.calls[-1][1]["transition"], 0)

    def test_repeated_preview_restores_initial_color_brightness_and_effect(self):
        before = deepcopy(self.hass.state_map["light.one"].attributes)
        self.apply(4000)
        self.apply(1000, cal_rgb_neutral_r=100)
        self.assertTrue(self.hass.timers[0].cancelled)
        self.stop()
        self.assertEqual(self.hass.state_map["light.one"].attributes, before)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertEqual(self.hass.bus.listeners, [])
        self.assertEqual(self.switch.manager.last_service_data, {})

    def test_originally_off_light_is_turned_on_then_restored_off(self):
        self.hass.state_map["light.one"].state = "off"
        self.apply()
        self.assertEqual(self.hass.state_map["light.one"].state, "on")
        self.stop()
        self.assertEqual(self.hass.calls[-1], ("turn_off", {"entity_id": "light.one", "transition": 0}))
        self.assertEqual(self.hass.state_map["light.one"].state, "off")

    def test_external_command_wins_even_when_it_matches_preview_values(self):
        self.apply()
        calls = len(self.hass.calls)
        self.hass.bus.emit({"domain": "light", "service": "turn_on", "service_data": {"entity_id": "light.one", "brightness": 204}})
        self.stop()
        self.assertEqual(len(self.hass.calls), calls)

    def test_bus_listeners_are_loop_callbacks_and_preserve_external_changes(self):
        self.apply()
        self.assertEqual(len(self.hass.bus.listeners), 2)
        self.assertTrue(all(inspect.iscoroutinefunction(listener) or getattr(listener, "_hass_callback", None) is True for listener in self.hass.bus.listeners))
        self.hass.job_routes.clear()
        self.hass.bus.emit({"entity_id": "light.one", "new_state": FakeState(brightness=50)})
        self.assertEqual(self.hass.job_routes, ["coroutine", "callback"])
        calls = len(self.hass.calls)
        self.stop()
        self.assertEqual(len(self.hass.calls), calls)

    def test_external_state_change_without_a_service_is_not_overwritten(self):
        self.apply()
        self.hass.state_map["light.one"].attributes["rgbww_color"] = (1, 2, 3, 4, 5)
        calls = len(self.hass.calls)
        self.stop()
        self.assertEqual(len(self.hass.calls), calls)
        self.assertEqual(self.hass.state_map["light.one"].attributes["rgbww_color"], (1, 2, 3, 4, 5))

    def test_external_state_round_trip_does_not_reenable_restoration(self):
        self.apply()
        preview_state = self.hass.state_map["light.one"]
        changed = FakeState(brightness=50)
        self.hass.bus.emit({"entity_id": "light.one", "new_state": changed})
        self.hass.bus.emit({"entity_id": "light.one", "new_state": preview_state})
        calls = len(self.hass.calls)
        self.stop()
        self.assertEqual(len(self.hass.calls), calls)

    def test_supported_solid_effect_is_previewed_and_original_effect_is_restored(self):
        self.hass.state_map["light.one"].attributes.update(effect="Rainbow", effect_list=["Solid", "Rainbow"])
        self.apply()
        self.assertEqual(self.hass.calls[-1][1]["effect"], "Solid")
        self.assertEqual(self.hass.calls[-1][1]["transition"], 0)
        self.stop()
        self.assertEqual(self.hass.calls[-1][1]["effect"], "Rainbow")
        self.assertEqual(self.hass.calls[-1][1]["transition"], 0)

    def test_solid_effect_is_not_forced_when_it_is_not_supported(self):
        self.hass.state_map["light.one"].attributes.update(effect="Other", effect_list=["Other"])
        self.apply()
        self.assertNotIn("effect", self.hass.calls[-1][1])
        self.assertEqual(self.hass.state_map["light.one"].attributes["effect"], "Other")

    def test_quantized_rgbww_and_derived_attributes_still_allow_restoration(self):
        original_color = self.hass.state_map["light.one"].attributes["rgbww_color"]
        self.apply()
        state = self.hass.state_map["light.one"]
        state.attributes["rgbww_color"] = [*state.attributes["rgbww_color"]]
        state.attributes["rgbww_color"][3] += 1
        state.attributes.update(hs_color=(9.2, 80), rgb_color=(200, 100, 20), xy_color=(0.6, 0.4))
        self.hass.bus.emit({"entity_id": "light.one", "new_state": state})
        self.stop()
        self.assertEqual(self.hass.state_map["light.one"].attributes["rgbww_color"], original_color)

    def test_late_own_report_can_complete_an_initially_stale_readback(self):
        original = deepcopy(self.hass.state_map["light.one"])
        self.apply()
        current = self.hass.state_map["light.one"]
        session = self.hass.data[self.preview.PREVIEW_DATA]["one"]
        saved = session.lights["light.one"]
        # The service handler finished before the final coordinator event.
        saved.observed_state = original.state
        saved.observed_attributes = self.preview._observed_attributes(original)
        self.hass.bus.emit({"entity_id": "light.one", "new_state": current}, current.context)
        self.stop()
        self.assertEqual(self.hass.state_map["light.one"].attributes["rgbww_color"], original.attributes["rgbww_color"])

    def test_large_native_change_or_brightness_change_is_not_hidden_by_own_context(self):
        for attribute in ("rgbww_color", "brightness", "effect", "color_mode"):
            with self.subTest(attribute=attribute):
                self.apply()
                state = self.hass.state_map["light.one"]
                if attribute == "rgbww_color":
                    value = list(state.attributes[attribute])
                    value[3] += 30
                    state.attributes[attribute] = value
                elif attribute == "brightness":
                    state.attributes[attribute] -= 1
                elif attribute == "effect":
                    state.attributes[attribute] = "Rainbow"
                else:
                    state.attributes[attribute] = "color_temp"
                    state.attributes["color_temp_kelvin"] = 3000
                self.hass.bus.emit({"entity_id": "light.one", "new_state": state}, state.context)
                calls = len(self.hass.calls)
                self.stop()
                self.assertEqual(len(self.hass.calls), calls)

    def test_late_wled_full_white_can_report_as_equivalent_color_temperature(self):
        original = deepcopy(self.hass.state_map["light.one"].attributes)
        self.apply()
        session = self.hass.data[self.preview.PREVIEW_DATA]["one"]
        saved = session.lights["light.one"]
        saved.expected_attributes.update(color_mode="rgbww", rgbww_color=(0, 0, 0, 184, 71))
        own_context = self.hass.state_map["light.one"].context
        reported = FakeState(context=own_context, color_mode="color_temp", color_temp_kelvin=5272, brightness=204, rgbww_color=None)
        self.hass.state_map["light.one"] = reported
        self.hass.bus.emit({"entity_id": "light.one", "new_state": reported}, own_context)
        self.stop()
        self.assertEqual(self.hass.state_map["light.one"].attributes["rgbww_color"], original["rgbww_color"])

    def test_cct_alias_is_limited_to_own_wled_reports_with_the_known_white_blend(self):
        for cause in ("external_context", "external_command", "non_wled", "different_blend", "different_temperature", "different_brightness", "different_effect"):
            with self.subTest(cause=cause):
                self.hass.registry["light.one"].platform = "wled"
                self.hass.wled_config.cct_blend = 0
                self.apply()
                session = self.hass.data[self.preview.PREVIEW_DATA]["one"]
                session.lights["light.one"].expected_attributes.update(color_mode="rgbww", rgbww_color=(0, 0, 0, 184, 71))
                context = self.hass.state_map["light.one"].context
                reported = FakeState(context=context, color_mode="color_temp", color_temp_kelvin=5272, brightness=204, rgbww_color=None)
                if cause == "external_context":
                    context = SimpleNamespace(id="external", parent_id=None)
                    reported.context = context
                elif cause == "external_command":
                    self.hass.bus.emit({"domain": "light", "service": "turn_on", "service_data": {"entity_id": "light.one"}})
                elif cause == "non_wled":
                    self.hass.registry["light.one"].platform = "other"
                elif cause == "different_blend":
                    self.hass.wled_config.cct_blend = 100
                elif cause == "different_temperature":
                    reported.attributes["color_temp_kelvin"] = 4500
                elif cause == "different_brightness":
                    reported.attributes["brightness"] = 128
                else:
                    reported.attributes["effect"] = "Rainbow"
                self.hass.state_map["light.one"] = reported
                self.hass.bus.emit({"entity_id": "light.one", "new_state": reported}, context)
                calls = len(self.hass.calls)
                self.stop()
                self.assertEqual(len(self.hass.calls), calls)

    def test_groups_are_resolved_and_only_previewed_lights_are_reserved(self):
        self.hass.state_map["light.group"] = FakeState(entity_id=["light.two"])
        self.apply(lights=["light.group"])
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.two"))
        self.assertFalse(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        self.assertEqual(self.hass.calls[-1][1]["entity_id"], "light.two")

    def test_conflicting_profile_is_rejected_before_changing_lights(self):
        self.hass.add_profile("two", ["light.one"])
        self.apply()
        calls = len(self.hass.calls)
        with self.assertRaises(self.preview.CalibrationPreviewError):
            immediate(self.preview.async_preview_calibration(self.hass, "two", {}, 1000))
        self.assertEqual(len(self.hass.calls), calls)
        self.assertEqual(set(self.hass.data[self.preview.PREVIEW_DATA]), {"one"})

    def test_changing_preview_lights_restores_and_releases_previous_light(self):
        original = deepcopy(self.hass.state_map["light.one"].attributes)
        self.apply()
        self.apply(lights=["light.two"])
        self.assertEqual(self.hass.state_map["light.one"].attributes, original)
        self.assertFalse(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.two"))

    def test_expiry_restores_and_stale_timeout_cannot_end_a_newer_preview(self):
        original = deepcopy(self.hass.state_map["light.one"].attributes)
        self.apply(4000)
        first = self.hass.timers[-1]
        self.apply(1000)
        self.hass.run_job(first.callback, None)
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        self.hass.run_job(self.hass.timers[-1].callback, None)
        self.assertNotIn("executor", self.hass.job_routes)
        self.assertEqual(self.hass.state_map["light.one"].attributes, original)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)

    def test_failed_preview_restores_completed_lights_and_releases_resources(self):
        original = deepcopy(self.hass.state_map["light.one"].attributes)
        self.switch.fail_light = "light.two"
        with self.assertRaises(ValueError):
            self.apply(lights=["light.one", "light.two"])
        self.assertEqual(self.hass.state_map["light.one"].attributes, original)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertEqual(self.hass.bus.listeners, [])
        self.assertEqual(self.switch.manager.last_service_data, {})

    def test_stop_is_idempotent_without_starting_a_preview(self):
        self.stop()
        self.stop()
        self.assertEqual(self.hass.calls, [])

    def test_real_unload_hook_stops_preview_before_platforms_are_removed(self):
        original = deepcopy(self.hass.state_map["light.one"].attributes)
        self.apply()

        async def unload_platforms(entry, platforms):
            self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
            self.assertEqual(self.hass.state_map["light.one"].attributes, original)
            self.assertEqual(platforms, ["switch"])
            return True

        self.hass.config_entries = SimpleNamespace(async_unload_platforms=unload_platforms)
        source = ast.parse((COMPONENT / "__init__.py").read_text(encoding="utf-8"))
        fn = next(node for node in source.body if getattr(node, "name", None) == "async_unload_entry")
        namespace = {
            "__name__": "preview_tests_package", "__package__": "preview_tests_package",
            "DOMAIN": "circadian_pro", "PLATFORMS": ["switch"], "ATTR_CIRCADIAN_PRO_MANAGER": "manager",
        }
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "<real integration unload>", "exec"), namespace)
        result = immediate(namespace["async_unload_entry"](self.hass, SimpleNamespace(entry_id="one")))
        self.assertTrue(result)
        self.assertNotIn("circadian_pro", self.hass.data)
        self.assertTrue(self.hass.timers[-1].cancelled)


if __name__ == "__main__":
    unittest.main()
