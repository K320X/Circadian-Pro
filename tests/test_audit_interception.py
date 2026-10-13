"""Per-light proactive interception using real payload preparation and splitting."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from types import MethodType, SimpleNamespace
import unittest

from test_color_temperature_bounds import ProtocolHarness, State, load_functions


class Profile(SimpleNamespace):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


class ServiceCall:
    def __init__(self, hass, domain, service, data, context, return_response=False):
        self.hass = hass
        self.domain = domain
        self.service = service
        self.data = data
        self.context = context
        self.return_response = return_response


class Context(SimpleNamespace):
    counter = 0

    def __init__(self, user_id=None, parent_id=None):
        Context.counter += 1
        super().__init__(id=f"external-forward-{Context.counter}", user_id=user_id, parent_id=parent_id, kind=None)


class InterceptionHarness:
    def __init__(self, modes, *, split=False, multi=True):
        protocol = ProtocolHarness()
        self.ns = protocol.ns
        self.ns.update(
            deepcopy=deepcopy, CONF_PARAMS="params", ServiceCall=ServiceCall,
            Context=Context, Mapping=Mapping,
            STATE_ON="on", STATE_OFF="off", STATE_UNAVAILABLE="unavailable", STATE_UNKNOWN="unknown",
            is_our_context=lambda context, which=None: context.kind is not None and (which is None or context.kind == which),
            preprocess_turn_on_alternatives=lambda *_: None,
        )
        load_functions(self.ns, "adaptation_utils.py", {"has_effect_attribute", "has_brightness_attribute", "has_color_attribute", "get_light_control_attributes"})
        load_functions(self.ns, "switch.py", {
            "_service_interceptor_turn_on_handler", "_service_interceptor_turn_on_single_light_handler",
            "_correct_for_multi_light_intercept",
            "update_manually_controlled_from_event",
        }, "CircadianProManager")
        self.hass = protocol.hass
        async def admin_user(_user_id):
            return SimpleNamespace(is_admin=True)
        self.hass.auth = SimpleNamespace(async_get_user=admin_user)
        self.manager = protocol.manager
        self.states = {}
        for light, colors in modes.items():
            state = State({
                "supported_color_modes": set(colors), "supported_features": 0,
                "color_mode": None, "brightness": None, "color_temp_kelvin": None,
                "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6535,
            })
            state.state = "off"
            self.states[light] = state
        self.hass.states = SimpleNamespace(get=self.states.get, is_state=lambda light, value: self.states.get(light) is not None and self.states[light].state == value)
        self.context_count = 0
        self.tasks = []
        self.calls = []
        self.continuations = []
        self.prepared = []
        self.sleeps = []
        self.fail_preparation = set()
        self.fail_service = set()
        self.proactive = {}
        self.resets = []
        self.first_baselines = {}
        self.manager.turn_on_event = {}
        self.manager.adaptation_tasks = set()

        def reset(*lights, reset_manual_control=True):
            self.resets.append((lights, reset_manual_control))
            for light in lights:
                for cache in (self.manager.last_service_data, self.manager.last_service_context, self.manager.last_service_acknowledgements):
                    cache.pop(light, None)

        self.manager.reset = reset
        self.manager.set_proactively_adapting = lambda context, light: self.proactive.__setitem__(context, light)
        self.manager.is_proactively_adapting = lambda context, light=None: context in self.proactive and (light is None or self.proactive[context] == light)

        def clear(light):
            for context in [context for context, entity in self.proactive.items() if entity == light]:
                self.proactive.pop(context)

        self.manager.clear_proactively_adapting = clear
        self.manager.create_context = self.new_context
        self.profile = Profile(**vars(protocol.switch))
        self.profile.initial_transition = 0
        self.profile._multi_light_intercept = multi
        self.profile._separate_turn_on_commands = split
        self.profile.create_context = self.new_context
        self.profile._sun_light_settings = SimpleNamespace(sleep_rgb_or_color_temp="color_temp", get_settings=lambda *_: {"brightness_pct": 80, "color_temp_kelvin": 4000})
        self.owners = {light: self.profile for light in modes}
        self.bind_profile(self.profile)
        self.manager._get_entity_list = lambda data: list(data["entity_id"])
        self.manager._separate_entity_ids = self.separate
        for method in ("_service_interceptor_turn_on_handler", "_service_interceptor_turn_on_single_light_handler", "_correct_for_multi_light_intercept"):
            setattr(self.manager, method, MethodType(self.ns[method], self.manager))

        async def sleep(seconds):
            self.sleeps.append(seconds)
            await asyncio.sleep(0)

        self.ns["asyncio"] = SimpleNamespace(sleep=sleep)
        self.ns["is_on"] = lambda hass, light: self.states[light].state == "on"
        self.hass.async_create_task = self.create_task
        self.hass.services.async_call = self.native_call
        self.original_context = SimpleNamespace(id="user-call", kind=None, user_id="human", parent_id=None)

    def new_context(self, kind, parent=None):
        self.context_count += 1
        return SimpleNamespace(id=f"own-{self.context_count}", kind=kind, parent_id=parent.id if parent else None, user_id=None)

    def bind_profile(self, profile):
        async def prepare(light, transition, **kwargs):
            self.prepared.append(light)
            if light in self.fail_preparation:
                raise ValueError("Simulated preparation failure")
            return await self.ns["prepare_switch_adaptation_data"](profile, light, transition, **kwargs)

        async def execute(data):
            self.continuations.append((data.entity_id, data.initial_sleep))
            await self.ns["_execute_adaptation_calls"](profile, data)

        profile.prepare_adaptation_data = prepare
        profile.execute_cancellable_adaptation_calls = execute

    def separate(self, targets, data):
        grouped, skipped = {}, []
        for light in targets:
            if light not in self.owners or self.states[light].state == "on":
                skipped.append(light)
            else:
                grouped.setdefault(self.owners[light], []).append(light)
        return grouped, skipped

    def create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def native_call(self, domain, service, payload, **kwargs):
        targets = payload["entity_id"]
        if isinstance(targets, str):
            targets = [targets]
        self.calls.append((service, deepcopy(payload), kwargs["context"]))
        for light in targets:
            self.first_baselines.setdefault(light, (
                deepcopy(self.manager.last_service_data.get(light)),
                self.manager.last_service_context.get(light),
            ))
            state = self.states[light]
            state.state = "off" if service == "toggle" and state.state == "on" else "on"
            state.attributes.update({key: value for key, value in payload.items() if key not in {"entity_id", "transition"}})
            for mode, attribute in (("rgbww", "rgbww_color"), ("rgbw", "rgbw_color"), ("color_temp", "color_temp_kelvin"), ("rgb", "rgb_color")):
                if attribute in payload:
                    state.attributes["color_mode"] = mode
            # Include an uncertain device write: it changed before reporting an
            # error, so retrying a toggle would visibly reverse the user's action.
            if light in self.fail_service:
                raise ValueError("Simulated service failure after device write")
        await asyncio.sleep(0)

    async def run(self, targets, params=None, *, service="turn_on", extra_targets=None):
        data = {"entity_id": list(targets), "params": deepcopy(params or {}), **(extra_targets or {})}
        original = deepcopy(data)
        call = ServiceCall(self.hass, "light", service, deepcopy(data), self.original_context)
        after_original = await self.manager._service_interceptor_turn_on_handler(call, data)
        # The proxy invokes the preserved original handler once after interception.
        try:
            await self.native_call("light", service, {"entity_id": data["entity_id"], **data["params"]}, context=self.original_context)
        except BaseException:
            if after_original is not None:
                after_original(False)
            raise
        if after_original is not None:
            after_original(True)
        if self.tasks:
            await asyncio.gather(*self.tasks)
        return data, original

    def calls_for(self, light):
        return [(service, payload, context) for service, payload, context in self.calls if light in ([payload["entity_id"]] if isinstance(payload["entity_id"], str) else payload["entity_id"])]


class InterceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_heterogeneous_lights_receive_their_native_payload_and_range(self):
        h = InterceptionHarness({"light.rgbww": ["rgbww", "color_temp"], "light.cct": ["color_temp"], "light.rgbw": ["rgbw"]})
        h.states["light.cct"].attributes.update(min_color_temp_kelvin=2700, max_color_temp_kelvin=3000)
        data, _ = await h.run(list(h.states))
        self.assertEqual(data["entity_id"], ["light.rgbww"])
        self.assertEqual(h.prepared, ["light.rgbww", "light.cct", "light.rgbw"])
        self.assertEqual(len(h.calls_for("light.rgbww")[0][1]["rgbww_color"]), 5)
        self.assertEqual(h.calls_for("light.cct")[0][1]["color_temp_kelvin"], 3000)
        self.assertNotIn("rgbww_color", h.calls_for("light.cct")[0][1])
        self.assertEqual(len(h.calls_for("light.rgbw")[0][1]["rgbw_color"]), 4)
        self.assertEqual(h.tasks, [])

    async def test_split_commands_finish_every_light_after_its_own_brightness_call(self):
        h = InterceptionHarness({"light.rgbww": ["rgbww"], "light.cct": ["color_temp"]}, split=True)
        await h.run(list(h.states), {"transition": 0})
        for light, color_attribute in (("light.rgbww", "rgbww_color"), ("light.cct", "color_temp_kelvin")):
            with self.subTest(light=light):
                calls = h.calls_for(light)
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0][1], {"entity_id": [light], "brightness": 204, "transition": 0})
                self.assertIn(color_attribute, calls[1][1])
                self.assertNotIn("brightness", calls[1][1])
                self.assertTrue(all(context.user_id == "human" for _, _, context in calls))
        self.assertEqual(set(h.continuations), {("light.rgbww", True), ("light.cct", True)})
        self.assertTrue(all(state.state == "on" for state in h.states.values()))

    async def test_original_nonadapted_color_is_kept_for_every_profile(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgb"]})
        h.profile.adapt_color_switch.is_on = False
        second = Profile(**vars(h.profile))
        second._cal_rgb_neutral = [1, 2, 3]
        h.bind_profile(second)
        h.owners["light.two"] = second
        await h.run(list(h.states), {"rgb_color": (11, 22, 33), "transition": 0})
        for light in h.states:
            payload = h.calls_for(light)[0][1]
            self.assertEqual(payload["rgb_color"], (11, 22, 33))
            self.assertEqual(payload["brightness"], 204)
            self.assertNotIn("rgbww_color", payload)

    async def test_multiple_profiles_keep_their_own_brightness_and_calibration(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbww"]})
        second = Profile(**vars(h.profile))
        second._cal_rgb_warm = [80, 20, 0]
        second._sun_light_settings = SimpleNamespace(sleep_rgb_or_color_temp="color_temp", get_settings=lambda *_: {"brightness_pct": 20, "color_temp_kelvin": 1000})
        h.bind_profile(second)
        h.owners["light.two"] = second
        await h.run(list(h.states))
        self.assertEqual(h.calls_for("light.one")[0][1]["brightness"], 204)
        self.assertEqual(h.calls_for("light.two")[0][1]["brightness"], 51)
        self.assertEqual(h.calls_for("light.two")[0][1]["rgbww_color"], (80, 20, 0, 0, 0))

    async def test_native_adapted_color_replaces_conflicting_original_color_fields(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]})
        await h.run(list(h.states), {"rgb_color": (11, 22, 33)})
        for light in h.states:
            self.assertNotIn("rgb_color", h.calls_for(light)[0][1])

    async def test_adapted_brightness_replaces_steps_but_disabled_brightness_keeps_them(self):
        for adapt in (False, True):
            with self.subTest(adapt=adapt):
                h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]})
                h.profile.adapt_brightness_switch.is_on = adapt
                await h.run(list(h.states), {"brightness_step": 10})
                for light in h.states:
                    payload = h.calls_for(light)[0][1]
                    if adapt:
                        self.assertEqual(payload["brightness"], 204)
                        self.assertNotIn("brightness_step", payload)
                    else:
                        self.assertEqual(payload["brightness_step"], 10)
                        self.assertNotIn("brightness", payload)

    async def test_targets_are_restricted_and_secondary_contexts_preserve_user_and_parent(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbww"]})
        data, _ = await h.run(list(h.states), extra_targets={"area_id": ["area"], "device_id": ["device"], "label_id": ["label"], "floor_id": ["floor"]})
        self.assertEqual(set(data), {"entity_id", "params"})
        self.assertEqual(data["entity_id"], ["light.one"])
        self.assertEqual(h.proactive[h.original_context.id], "light.one")
        second_context = h.calls_for("light.two")[0][2]
        self.assertNotEqual(second_context.id, h.original_context.id)
        self.assertEqual(second_context.user_id, "human")
        self.assertEqual(second_context.parent_id, h.original_context.id)
        self.assertEqual(h.proactive[second_context.id], "light.two")
        self.assertTrue(all(reset_manual is False for _, reset_manual in h.resets))

    async def test_missing_color_brightness_capabilities_preserve_a_bare_turn_on(self):
        h = InterceptionHarness({"light.onoff": ["onoff"], "light.rgbww": ["rgbww"]}, split=True)
        h.states["light.onoff"].attributes.pop("supported_color_modes")
        await h.run(list(h.states))
        self.assertEqual(h.calls_for("light.onoff")[0][1], {"entity_id": ["light.onoff"]})
        self.assertEqual(len(h.calls_for("light.onoff")), 1)
        self.assertEqual(len(h.calls_for("light.rgbww")), 2)
        self.assertNotIn(h.original_context.id, h.proactive)

    async def test_one_preparation_failure_keeps_that_original_command_and_other_plans(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]}, split=True)
        h.fail_preparation.add("light.two")
        with self.assertLogs(h.ns["_LOGGER"], level="ERROR"):
            await h.run(list(h.states), {"rgb_color": (11, 22, 33)})
        self.assertEqual(h.calls_for("light.two")[0][1], {"entity_id": ["light.two"], "rgb_color": (11, 22, 33)})
        self.assertEqual(len(h.calls_for("light.one")), 2)
        self.assertEqual(len(h.calls_for("light.two")), 1)
        self.assertNotIn("light.two", h.proactive.values())
        self.assertNotIn("light.two", h.manager.last_service_data)

    async def test_service_failure_does_not_repeat_a_toggle_or_cancel_other_lights(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"], "light.three": ["color_temp"]}, split=True)
        h.fail_service.add("light.two")
        with self.assertLogs(h.ns["_LOGGER"], level="ERROR"):
            await h.run(list(h.states), service="toggle")
        for light in h.states:
            self.assertEqual(sum(service == "toggle" for service, _, _ in h.calls_for(light)), 1)
            self.assertEqual(h.states[light].state, "on")
        self.assertEqual(len(h.calls_for("light.one")), 2)
        self.assertEqual(len(h.calls_for("light.two")), 1)
        self.assertEqual(len(h.calls_for("light.three")), 2)
        self.assertNotIn("light.two", h.manager.last_service_data)
        self.assertNotIn("light.two", h.manager.last_service_context)

    async def test_first_payload_baseline_and_context_are_recorded_for_split_and_unsplit(self):
        for split in (False, True):
            with self.subTest(split=split):
                h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]}, split=split)
                h.manager.last_service_acknowledgements["light.one"] = object()
                await h.run(list(h.states), {"transition": 0})
                for light in h.states:
                    service, payload, context = h.calls_for(light)[0]
                    baseline, baseline_context = h.first_baselines[light]
                    self.assertEqual(baseline, {"entity_id": light, **{key: value for key, value in payload.items() if key != "entity_id"}})
                    self.assertEqual(baseline_context, context.id)
                    self.assertEqual(baseline["brightness"], 204)
                    if split:
                        self.assertEqual(h.manager.last_service_data[light]["brightness"], 204)
                self.assertNotIn("light.one", h.manager.last_service_acknowledgements)

    async def test_mixed_target_already_on_light_still_marks_only_brightness_manual(self):
        h = InterceptionHarness({"light.off": ["rgbww"], "light.on": ["rgbw"]})
        h.states["light.on"].state = "on"
        await h.run(list(h.states), {"brightness": 100})
        forwarded_context = h.calls_for("light.on")[0][2]
        self.assertFalse(h.ns["is_our_context"](forwarded_context))
        self.assertEqual(forwarded_context.user_id, "human")
        self.assertEqual(forwarded_context.parent_id, h.original_context.id)
        for context in (h.original_context, forwarded_context):
            with self.subTest(context=context.id):
                h.manager.manual_control.clear()
                h.manager.turn_on_event["light.on"] = SimpleNamespace(data={"service_data": {"params": {"brightness": 100}}}, context=context)
                await h.ns["update_manually_controlled_from_event"](h.manager, h.profile, "light.on", False)
                self.assertEqual(h.manager.manual_control["light.on"], h.ns["LightControlAttributes"].BRIGHTNESS)

    async def test_profile_off_during_secondary_call_keeps_original_primary_payload(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]}, split=True)
        native = h.native_call

        async def disable_profile_after_secondary(*args, **kwargs):
            await native(*args, **kwargs)
            h.profile.is_on = False

        h.hass.services.async_call = disable_profile_after_secondary
        await h.run(list(h.states))
        self.assertEqual(h.calls_for("light.one")[0][1], {"entity_id": ["light.one"]})
        self.assertEqual(len(h.calls_for("light.one")), 1)
        self.assertNotIn("light.one", h.proactive.values())
        self.assertNotIn("light.one", h.manager.last_service_data)

    async def test_preview_started_during_dispatch_blocks_primary_and_pending_secondary_adaptation(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"], "light.three": ["color_temp"]}, split=True)
        reserved = set()
        h.ns["is_calibration_preview_active"] = lambda hass, light: light in reserved
        native = h.native_call

        async def reserve_pending_lights_after_secondary(*args, **kwargs):
            await native(*args, **kwargs)
            reserved.update(("light.one", "light.three"))

        h.hass.services.async_call = reserve_pending_lights_after_secondary
        await h.run(list(h.states))
        for light in ("light.one", "light.three"):
            self.assertEqual(h.calls_for(light)[0][1], {"entity_id": [light]})
            self.assertEqual(len(h.calls_for(light)), 1)
            self.assertNotIn(light, h.proactive.values())
            self.assertNotIn(light, h.manager.last_service_data)

    async def test_multi_intercept_disabled_keeps_the_original_request(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]}, multi=False)
        with self.assertLogs(h.ns["_LOGGER"], level="WARNING"):
            data, original = await h.run(list(h.states))
        self.assertEqual(data, original)
        self.assertEqual(h.prepared, [])
        self.assertEqual(len(h.calls), 1)

    async def test_effect_calls_and_skipped_lights_keep_the_original_parameters(self):
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]})
        data, original = await h.run(list(h.states), {"effect": "Solid"})
        self.assertEqual(data, original)
        self.assertEqual(h.prepared, [])
        self.assertEqual(len(h.calls), 1)
        h = InterceptionHarness({"light.one": ["rgbww"], "light.two": ["rgbw"]})
        h.owners.pop("light.two")
        await h.run(list(h.states), {"rgb_color": (11, 22, 33)})
        self.assertEqual(h.calls_for("light.two")[0][1], {"entity_id": ["light.two"], "rgb_color": (11, 22, 33)})


if __name__ == "__main__":
    unittest.main()
