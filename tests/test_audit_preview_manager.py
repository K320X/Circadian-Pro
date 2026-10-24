"""Preview regressions across the real preview, manager and split executor."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import sys
from types import MethodType, SimpleNamespace
import unittest

from test_audit_switch import namespace
from test_calibration_preview import FakeHass, FakeState, FakeSwitch, load_preview_module
from test_color_temperature_bounds import load_functions


class PreviewManagerAudit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.preview, self.saved_modules = load_preview_module(real_io_timeout=True)
        self.ns = namespace()
        self.ns.update(
            asyncio=asyncio,
            is_calibration_preview_context=self.preview.is_calibration_preview_context,
            is_calibration_preview_active=self.preview.is_calibration_preview_active,
            async_check_entity_control=self.preview.async_check_entity_control,
        )
        load_functions(self.ns, "switch.py", {"execute_cancellable_adaptation_calls"}, "CircadianProSwitch")
        load_functions(self.ns, "switch.py", {
            "cancel_ongoing_adaptation_calls", "reset", "state_changed_event_listener",
            "is_proactively_adapting", "turn_on_off_event_listener",
            "clear_proactively_adapting",
        }, "CircadianProManager")
        ns = self.ns

        class ActualExecuteSwitch(FakeSwitch):
            _execute_adaptation_calls = ns["_execute_adaptation_calls"]
            execute_cancellable_adaptation_calls = ns["execute_cancellable_adaptation_calls"]
            is_on = True
            _name = "audit"

            def create_context(self, kind, parent=None):
                self.context_count += 1
                return SimpleNamespace(
                    id=f"0000000000:cp:AAAA:{ns['remove_vowels'](kind)}:{self.context_count}",
                    parent_id=parent.id if parent else None, user_id=None,
                )

            async def prepare_adaptation_data(self, light, **kwargs):
                data = await super().prepare_adaptation_data(light, **kwargs)
                return ns["prepare_adaptation_data"](
                    self.hass, light, data.context, 0, 0, data.payload,
                    split=True, filter_by_state=False, force=True,
                )

        self.hass = FakeHass()
        self.hass.add_profile("one", ["light.one"])
        self.manager = self.hass.data["circadian_pro"]["one"]["switch"].manager
        for key in (
            "last_service_context", "last_service_acknowledgements", "our_last_state_on_change",
            "adaptation_tasks_brightness", "adaptation_tasks_color", "auto_reset_manual_control_timers",
            "on_to_off_event", "off_to_on_event", "turn_off_locks", "_proactively_adapting_contexts",
            "turn_on_event", "turn_off_event", "toggle_event", "sleep_tasks",
        ):
            setattr(self.manager, key, {})
        self.manager.lights = {"light.one"}
        self.manager.manual_control = {"light.one": ns["LightControlAttributes"].COLOR}
        self.manager.start_transition_timer = lambda *_: None
        for name in (
            "cancel_ongoing_adaptation_calls", "reset", "state_changed_event_listener",
            "is_proactively_adapting", "turn_on_off_event_listener",
            "clear_proactively_adapting",
        ):
            setattr(self.manager, name, MethodType(ns[name], self.manager))
        self.switch = ActualExecuteSwitch(self.hass, self.manager, ["light.one"])
        self.hass.data["circadian_pro"]["one"]["switch"] = self.switch
        self.hass.state_map["light.one"] = FakeState("off")
        self.hass.async_create_task = asyncio.create_task
        native_call = self.hass.call

        async def call(domain, service, payload, **kwargs):
            old = self.hass.states.get(payload["entity_id"])
            await self.manager.turn_on_off_event_listener(SimpleNamespace(
                data={"domain": domain, "service": service, "service_data": payload},
                context=kwargs["context"],
            ))
            await native_call(domain, service, payload, **kwargs)
            new = self.hass.states.get(payload["entity_id"])
            await self.emit_state(old, new)
            await asyncio.sleep(0)

        self.hass.services.async_call = call

    async def asyncTearDown(self):
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        for name in set(sys.modules) - self.saved_modules.keys():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules.pop(name, None)
        for name, module in self.saved_modules.items():
            if name.startswith(("preview_tests_package", "homeassistant")):
                sys.modules[name] = module

    async def emit_state(self, old, new):
        event = SimpleNamespace(
            data={"entity_id": "light.one", "old_state": old, "new_state": new},
            context=new.context,
        )
        self.hass.bus.emit(event.data, event.context)
        await self.manager.state_changed_event_listener(event)

    async def apply(self, session_id=""):
        return await self.preview.async_preview_calibration(self.hass, "one", {}, 4000, session_id=session_id)

    async def test_off_light_split_preview_sends_both_commands_and_preserves_manual_flags(self):
        self.assertTrue(await self.apply())
        self.assertEqual(len(self.hass.calls), 2)
        self.assertIn("brightness", self.hass.calls[0][1])
        self.assertIn("rgbww_color", self.hass.calls[1][1])
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        self.assertEqual(self.hass.states.get("light.one").state, "off")
        self.assertEqual(self.manager.manual_control["light.one"], self.ns["LightControlAttributes"].COLOR)
        self.assertFalse(self.manager.adaptation_tasks_color)
        self.assertFalse(self.manager.adaptation_tasks_brightness)

    async def test_external_physical_off_cancels_remaining_preview_and_is_not_undone(self):
        original = self.hass.services.async_call

        async def physical_off_after_brightness(domain, service, payload, **kwargs):
            await original(domain, service, payload, **kwargs)
            if service == "turn_on" and "brightness" in payload:
                old = self.hass.states.get("light.one")
                off = FakeState("off", **old.attributes)
                self.hass.state_map["light.one"] = off
                await self.emit_state(old, off)
                await asyncio.sleep(0)

        self.hass.services.async_call = physical_off_after_brightness
        await self.apply()
        self.assertEqual(len(self.hass.calls), 1)
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        self.assertEqual(len(self.hass.calls), 1)
        self.assertEqual(self.hass.states.get("light.one").state, "off")

    async def test_preview_context_recognition_is_bound_to_the_reserved_light_and_lifetime(self):
        await self.apply()
        preview = self.hass.data[self.preview.PREVIEW_DATA]["one"]
        root = next(iter(preview.contexts))
        child = SimpleNamespace(id="split", parent_id=root)
        self.assertTrue(self.preview.is_calibration_preview_context(self.hass, "light.one", child))
        self.assertFalse(self.preview.is_calibration_preview_context(self.hass, "light.two", child))
        self.assertFalse(self.preview.is_calibration_preview_context(self.hass, "light.one", SimpleNamespace(id="external")))
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        self.assertFalse(self.preview.is_calibration_preview_context(self.hass, "light.one", child))

    async def test_denied_service_event_does_not_prevent_preview_restoration(self):
        self.hass.state_map["light.one"] = FakeState("on")
        original = deepcopy(self.hass.states.get("light.one").attributes)
        await self.apply()

        async def get_user(_):
            return SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=lambda *_: False))

        self.hass.auth = SimpleNamespace(async_get_user=get_user)
        event = SimpleNamespace(
            data={"domain": "light", "service": "turn_off", "service_data": {"entity_id": "light.one"}},
            context=SimpleNamespace(id="denied", parent_id=None, user_id="restricted"),
        )
        await self.preview._service_listener(self.hass, self.hass.data[self.preview.PREVIEW_DATA]["one"], event)
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        self.assertEqual(self.hass.states.get("light.one").attributes, original)

    async def test_all_target_only_takes_over_lights_the_user_can_control(self):
        self.hass.state_map["light.one"] = FakeState("on")
        await self.apply()

        async def get_user(_):
            return SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=lambda entity, _: entity == "light.two"))

        self.hass.auth = SimpleNamespace(async_get_user=get_user)
        preview = self.hass.data[self.preview.PREVIEW_DATA]["one"]
        await self.preview._service_listener(self.hass, preview, SimpleNamespace(
            data={"domain": "light", "service": "turn_off", "service_data": {"entity_id": "all"}},
            context=SimpleNamespace(id="all", parent_id=None, user_id="restricted"),
        ))
        self.assertFalse(preview.lights["light.one"].externally_changed)

    async def test_second_editor_cannot_replace_or_stop_an_active_preview(self):
        await self.apply("first")
        count = len(self.hass.calls)
        with self.assertRaisesRegex(self.preview.CalibrationPreviewError, "Another editor") as caught:
            await self.apply("second")
        self.assertEqual(caught.exception.code, "preview_editor_busy")
        await self.preview.async_stop_calibration_preview(self.hass, "one", session_id="second")
        self.assertEqual(len(self.hass.calls), count)
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        await self.preview.async_stop_calibration_preview(self.hass, "one", session_id="first")
        self.assertFalse(self.preview.is_calibration_preview_active(self.hass, "light.one"))

    async def test_late_stop_from_closed_editor_does_not_end_new_session(self):
        await self.apply("closed")
        await self.preview.async_stop_calibration_preview(self.hass, "one", session_id="closed")
        await self.apply("new")
        count = len(self.hass.calls)
        await self.preview.async_stop_calibration_preview(self.hass, "one", session_id="closed")
        self.assertEqual(len(self.hass.calls), count)
        self.assertEqual(self.hass.data[self.preview.PREVIEW_DATA]["one"].session_id, "new")

    async def test_unload_can_force_cleanup_but_legacy_editor_cannot_stop_owned_session(self):
        await self.apply("owned")
        await self.preview.async_stop_calibration_preview(self.hass, "one", session_id="")
        self.assertTrue(self.preview.is_calibration_preview_active(self.hass, "light.one"))
        await self.preview.async_stop_calibration_preview(self.hass, "one")
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)

    async def test_stalled_dispatch_times_out_and_releases_a_waiting_stop(self):
        self.preview.PREVIEW_SERVICE_TIMEOUT = 0.02
        entered = asyncio.Event()
        original = self.hass.services.async_call

        async def stalled(domain, service, payload, **kwargs):
            await original(domain, service, payload, **kwargs)
            if service == "turn_on" and "brightness" in payload:
                entered.set()
                await asyncio.Event().wait()

        self.hass.services.async_call = stalled
        applying = asyncio.create_task(self.apply())
        await asyncio.wait_for(entered.wait(), 1)
        stopping = asyncio.create_task(self.preview.async_stop_calibration_preview(self.hass, "one"))
        with self.assertRaisesRegex(self.preview.CalibrationPreviewError, "command in time") as caught:
            await asyncio.wait_for(applying, 1)
        self.assertEqual(caught.exception.code, "preview_timeout")
        await asyncio.wait_for(stopping, 1)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertFalse(self.manager.adaptation_tasks_color)
        self.assertFalse(self.manager.adaptation_tasks_brightness)

    async def test_total_dispatch_deadline_is_not_swallowed_by_the_child_executor(self):
        self.preview.PREVIEW_SECONDS = 0.02
        self.preview.PREVIEW_SERVICE_TIMEOUT = 1
        original = self.hass.services.async_call

        async def stalled(domain, service, payload, **kwargs):
            await original(domain, service, payload, **kwargs)
            if service == "turn_on" and "brightness" in payload:
                await asyncio.Event().wait()

        self.hass.services.async_call = stalled
        with self.assertRaisesRegex(self.preview.CalibrationPreviewError, "time budget") as caught:
            await asyncio.wait_for(self.apply(), 1)
        self.assertEqual(caught.exception.code, "preview_timeout")
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertEqual(self.hass.states.get("light.one").state, "off")

    async def test_stalled_restoration_releases_reservations_and_logs_the_failure(self):
        await self.apply()
        self.preview.PREVIEW_SERVICE_TIMEOUT = 0.02
        original = self.hass.services.async_call

        async def stalled(domain, service, payload, **kwargs):
            await original(domain, service, payload, **kwargs)
            if service == "turn_off":
                await asyncio.Event().wait()

        self.hass.services.async_call = stalled
        with self.assertLogs(self.preview._LOGGER, level="ERROR"):
            await asyncio.wait_for(self.preview.async_stop_calibration_preview(self.hass, "one"), 1)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertEqual(self.hass.bus.listeners, [])

    async def test_cancelling_the_request_propagates_and_cleans_up_the_preview(self):
        entered = asyncio.Event()
        original = self.hass.services.async_call

        async def stalled(domain, service, payload, **kwargs):
            await original(domain, service, payload, **kwargs)
            if service == "turn_on" and "brightness" in payload:
                entered.set()
                await asyncio.Event().wait()

        self.hass.services.async_call = stalled
        applying = asyncio.create_task(self.apply())
        await asyncio.wait_for(entered.wait(), 1)
        applying.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(applying, 1)
        self.assertNotIn(self.preview.PREVIEW_DATA, self.hass.data)
        self.assertFalse(self.manager.adaptation_tasks_color)
        self.assertFalse(self.manager.adaptation_tasks_brightness)


if __name__ == "__main__":
    unittest.main()
