"""Concurrent preview regressions using the production module and HA I/O stubs."""

from __future__ import annotations

import sys
from copy import deepcopy
from types import SimpleNamespace
import unittest

import test_calibration_api as api_fixtures
import test_color_temperature_bounds as color_fixtures
from test_calibration_preview import FakeHass, FakeState, FakeSwitch, immediate, load_preview_module


class Gate:
    """A controllable I/O suspension, without opening Windows loopback sockets."""

    def __await__(self):
        yield self


class CalibrationConcurrencyAudit(unittest.TestCase):
    def setUp(self):
        self.preview, self.saved_modules = load_preview_module()
        self.hass = FakeHass()
        self.hass.add_profile("one", ["light.one"])
        self.hass.add_profile("two", ["light.two"])

    def tearDown(self):
        for entry_id in list(self.hass.data.get(self.preview.PREVIEW_DATA, {})):
            immediate(self.preview.async_stop_calibration_preview(self.hass, entry_id))
        for name in set(sys.modules) - self.saved_modules.keys():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules.pop(name, None)
        for name, module in self.saved_modules.items():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules[name] = module

    def apply(self, entry_id="one", **draft):
        return self.preview.async_preview_calibration(self.hass, entry_id, draft, 4000)

    def test_physical_change_during_dispatch_is_preserved_on_stop(self):
        gate = Gate()
        original_call = self.hass.call

        async def delayed_call(*args, **kwargs):
            await original_call(*args, **kwargs)
            await gate

        self.hass.services.async_call = delayed_call
        applying = self.apply()
        self.assertIs(applying.send(None), gate)
        manual = FakeState(brightness=103, rgbww_color=(70, 20, 10, 30, 90))
        self.hass.state_map["light.one"] = manual
        self.hass.bus.emit({"entity_id": "light.one", "new_state": manual})
        immediate(applying)
        self.hass.services.async_call = original_call
        immediate(self.preview.async_stop_calibration_preview(self.hass, "one"))
        self.assertIs(self.hass.state_map["light.one"], manual)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)

    def test_physical_turn_off_during_dispatch_is_not_undone(self):
        gate = Gate()
        original_call = self.hass.call

        async def delayed_call(*args, **kwargs):
            await original_call(*args, **kwargs)
            await gate

        self.hass.services.async_call = delayed_call
        applying = self.apply()
        self.assertIs(applying.send(None), gate)
        # Off often retains brightness/color attributes, so the native channel
        # vector alone cannot distinguish this external action from a preview.
        manual = FakeState("off", **self.hass.state_map["light.one"].attributes)
        self.hass.state_map["light.one"] = manual
        self.hass.bus.emit({"entity_id": "light.one", "new_state": manual})
        immediate(applying)
        self.hass.services.async_call = original_call
        immediate(self.preview.async_stop_calibration_preview(self.hass, "one"))
        self.assertIs(self.hass.state_map["light.one"], manual)
        self.assertEqual(manual.state, "off")

    def test_external_change_to_a_waiting_light_prevents_its_pending_preview_command(self):
        gate = Gate()
        original_call = self.hass.call

        async def delayed_first_light(domain, service, payload, **kwargs):
            await original_call(domain, service, payload, **kwargs)
            if payload["entity_id"] == "light.one":
                await gate

        self.hass.services.async_call = delayed_first_light
        applying = self.apply(lights=["light.one", "light.two"])
        self.assertIs(applying.send(None), gate)
        manual = FakeState(brightness=68, rgbww_color=(3, 90, 45, 120, 30))
        self.hass.state_map["light.two"] = manual
        self.hass.bus.emit({"entity_id": "light.two", "new_state": manual})
        immediate(applying)
        self.hass.services.async_call = original_call
        self.assertTrue(all(payload["entity_id"] != "light.two" for _, payload in self.hass.calls))
        immediate(self.preview.async_stop_calibration_preview(self.hass, "one"))
        self.assertIs(self.hass.state_map["light.two"], manual)

    def test_new_lights_are_reserved_before_awaiting_removed_light_restoration(self):
        immediate(self.apply())
        gate = Gate()
        original_call = self.hass.call

        async def delayed_restore(domain, service, payload, **kwargs):
            if payload["entity_id"] == "light.one" and payload.get("rgbww_color", ())[0] == 10:
                await gate
            return await original_call(domain, service, payload, **kwargs)

        self.hass.services.async_call = delayed_restore
        changing = self.apply(lights=["light.two"])
        self.assertIs(changing.send(None), gate)
        try:
            with self.assertRaises(self.preview.CalibrationPreviewError):
                immediate(self.apply("two", lights=["light.two"]))
        finally:
            immediate(changing)
            self.hass.services.async_call = original_call
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.two"))
        self.assertNotIn("two", self.hass.data[self.preview.PREVIEW_DATA])

    def test_failed_restoration_while_changing_lights_releases_reservations(self):
        immediate(self.apply())
        original_call = self.hass.call

        async def failing_restore(domain, service, payload, **kwargs):
            if payload["entity_id"] == "light.one" and payload.get("rgbww_color", ())[0] == 10:
                raise ValueError("Controller unavailable during restoration")
            return await original_call(domain, service, payload, **kwargs)

        self.hass.services.async_call = failing_restore
        with self.assertLogs(self.preview._LOGGER, level="ERROR"):
            with self.assertRaisesRegex(ValueError, "Controller unavailable"):
                immediate(self.apply(lights=["light.two"]))
        self.hass.services.async_call = original_call
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertEqual(self.hass.bus.listeners, [])
        self.assertTrue(immediate(self.apply("two", lights=["light.two"])))

    def test_dual_rgb_cct_preview_uses_actual_calibrated_rgb_without_changing_normal_preference(self):
        namespace = color_fixtures.production_namespace()
        namespace["State"] = FakeState
        namespace["color_temperature_to_rgb"] = lambda kelvin: (255, 56, 0) if kelvin == 1000 else (255, 228, 206)
        # Only replace the final I/O packing; the actual switch preparation,
        # mode selection and calibrated-channel functions run unchanged.
        namespace["prepare_adaptation_data"] = lambda hass, light, context, transition, delay, payload, **kwargs: SimpleNamespace(payload=payload, context=context)

        class ActualPrepareSwitch(FakeSwitch):
            prepare_adaptation_data = namespace["prepare_switch_adaptation_data"]

            def _set_changeable_settings(self, data, defaults):
                super()._set_changeable_settings(data, defaults)
                self._prefer_rgb_color = False
                self._name = "audit"
                self._transition = self._send_split_delay = 0
                self._separate_turn_on_commands = False
                self._cal_rgb_neutral = [data.get("cal_rgb_neutral_r", 255), data.get("cal_rgb_neutral_g", 255), data.get("cal_rgb_neutral_b", 255)]
                self._cal_rgb_warm = [255, 56, 0]
                self._cal_white_intensity_warm = 0
                self._cal_white_intensity_neutral = 100
                self._white_w_kelvin = 3000
                self._white_cw_kelvin = self._cal_white_kelvin_neutral = None
                self._sun_light_settings = SimpleNamespace(sleep_rgb_or_color_temp="color_temp", get_settings=lambda *args: {
                    "brightness_pct": data["max_brightness"], "color_temp_kelvin": data["min_color_temp"],
                })

        manager = self.hass.data["circadian_pro"]["one"]["switch"].manager
        manager.get_adaption_control_attributes = lambda switch, light: namespace["LightControlAttributes"].ALL
        switch = ActualPrepareSwitch(self.hass, manager, ["light.one"])
        switch._prefer_rgb_color = False
        self.hass.data["circadian_pro"]["one"]["switch"] = switch
        self.hass.state_map["light.one"] = FakeState(supported_color_modes=["rgb", "color_temp"], color_mode="color_temp", color_temp_kelvin=3500)
        immediate(self.apply(cal_rgb_neutral_r=80, cal_rgb_neutral_g=90, cal_rgb_neutral_b=60))
        payload = self.hass.calls[-1][1]
        self.assertEqual(payload["rgb_color"], (80, 90, 60))
        self.assertNotIn("color_temp_kelvin", payload)
        self.assertFalse(switch._prefer_rgb_color)


class CalibrationApiCompatibilityAudit(unittest.TestCase):
    def setUp(self):
        api_fixtures.ApiTests.setUp(self)

    def tearDown(self):
        api_fixtures.ApiTests.tearDown(self)

    def invoke(self, handler, **fields):
        return api_fixtures.ApiTests.invoke(self, handler, **fields)

    def test_stored_inactive_legacy_options_do_not_block_preview_or_modify_the_profile(self):
        entry = self.entries["loaded"]
        entry.options.update({"rgb_calibration_r": 130, "combine_rgb_scale": 120, "rgbw_fixed_white_kelvin": 4500})
        original = deepcopy(entry.options)
        self.invoke(self.api.websocket_preview, entry_id="loaded", settings=deepcopy(entry.options), kelvin=4000)
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.calibration.calls), 1)
        validated = self.calibration.calls[0][1]
        self.assertNotIn("rgb_calibration_r", validated)
        self.assertNotIn("combine_rgb_scale", validated)
        self.assertNotIn("rgbw_fixed_white_kelvin", validated)
        self.assertEqual(entry.options, original)

    def test_new_unknown_fields_still_fail_without_sending_light_commands(self):
        for draft in ({"unknown_field": 1}, {"rgb_calibration_r": 130}):
            with self.subTest(draft=draft):
                self.invoke(self.api.websocket_preview, entry_id="loaded", settings=draft, kelvin=4000)
                self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.calibration.calls, [])


if __name__ == "__main__":
    unittest.main()
