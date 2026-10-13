"""Authorization and slow-handler regressions using production control logic."""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from inspect import isawaitable
import logging
from types import MethodType, SimpleNamespace
import unittest

from test_audit_interception import InterceptionHarness, ServiceCall
from test_color_temperature_bounds import COMPONENT, load_functions, production_namespace


def restricted_user(allowed):
    return SimpleNamespace(is_admin=False, permissions=SimpleNamespace(
        check_entity=lambda entity_id, permission: entity_id in allowed and permission == "control",
    ))


def actor(hass, user):
    reads = []
    async def read(user_id):
        reads.append(user_id)
        return user
    hass.auth = SimpleNamespace(async_get_user=read)
    return reads


def event_harness(modes=None):
    h = InterceptionHarness(modes or {"light.one": ["rgbww"]})
    h.ns.update(
        asyncio=asyncio, ATTR_DOMAIN="domain", ATTR_SERVICE="service", ATTR_SERVICE_DATA="service_data",
        ATTR_TRANSITION="transition", SERVICE_TURN_OFF="turn_off", SERVICE_TOGGLE="toggle",
    )
    load_functions(h.ns, "switch.py", {"_service_parameters", "_is_light_group"})
    load_functions(h.ns, "switch.py", {
        "turn_on_off_event_listener", "reset", "_separate_entity_ids",
        "_mark_manual_control_if_non_bare_turn_on", "update_manually_controlled_from_event",
    }, "CircadianProManager")
    for method in (
        "turn_on_off_event_listener", "reset", "_mark_manual_control_if_non_bare_turn_on",
        "update_manually_controlled_from_event",
    ):
        setattr(h.manager, method, MethodType(h.ns[method], h.manager))
    h.manager.lights = set(h.states)
    h.manager.turn_off_event = {}
    h.manager.toggle_event = {}
    h.manager.sleep_tasks = {}
    h.manager.cancel_ongoing_adaptation_calls = lambda _: None
    h.events = []
    h.hass.bus.async_fire = lambda name, data, **kwargs: h.events.append((name, data))
    h.ns["_switch_with_lights"] = lambda *_args, **_kwargs: h.profile
    h.profile.lights = list(h.states)
    h.profile._configured_lights = list(h.states)
    h.profile._intercept = True
    h.profile._adapt_only_on_bare_turn_on = False
    return h


def event(service, targets, context, **parameters):
    return SimpleNamespace(
        data={"domain": "light", "service": service, "service_data": {"entity_id": targets, **parameters}},
        context=context, time_fired=datetime.now(UTC),
    )


def generic_handler(h, name):
    tree = ast.parse((COMPONENT / "switch.py").read_text(encoding="utf-8"))
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    node.decorator_list = []
    h.ns.update(
        CONF_LIGHTS="lights", CONF_MANUAL_CONTROL="manual_control", CONF_TURN_ON_LIGHTS="turn_on_lights",
        CONF_TRANSITION="transition", CONF_PREFER_RGB_COLOR="prefer_rgb_color",
        ATTR_ADAPT_BRIGHTNESS="adapt_brightness", ATTR_ADAPT_COLOR="adapt_color", hass=h.hass,
    )
    load_functions(h.ns, "adaptation_utils.py", {"manual_control_event_attribute_to_flags"})
    load_functions(h.ns, "switch.py", {
        "_authorized_switch_targets", "_switches_from_service_call", "_expand_light_groups", "_is_light_group",
    })
    registry_entry = SimpleNamespace(config_entry_id="profile")
    h.ns["entity_registry"] = SimpleNamespace(async_get=lambda _: SimpleNamespace(async_get=lambda _: registry_entry))
    h.hass.data = {"circadian_pro": {"profile": {"switch": h.profile}}}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(COMPONENT / "switch.py"), "exec"), h.ns)
    return h.ns[name]


class AuthorizationHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_targets_check_all_before_returning_any(self):
        ns = production_namespace()
        hass = SimpleNamespace()
        reads = actor(hass, restricted_user({"light.allowed"}))
        context = SimpleNamespace(user_id="restricted")
        with self.assertRaises(ns["Unauthorized"]):
            await ns["async_check_entity_control"](hass, context, ["light.allowed", "light.denied"])
        self.assertEqual(reads, ["restricted"])

    async def test_all_target_filters_and_deduplicates(self):
        ns = production_namespace()
        hass = SimpleNamespace()
        actor(hass, restricted_user({"light.allowed"}))
        allowed = await ns["async_check_entity_control"](
            hass, SimpleNamespace(user_id="restricted"), ["light.allowed", "light.denied", "light.allowed"], filter_allowed=True,
        )
        self.assertEqual(allowed, ["light.allowed"])

    async def test_missing_user_is_rejected_even_when_all_is_filtered(self):
        ns = production_namespace()
        hass = SimpleNamespace()
        actor(hass, None)
        with self.assertRaises(ns["UnknownUser"]):
            await ns["async_check_entity_control"](hass, SimpleNamespace(user_id="deleted"), ["light.one"], filter_allowed=True)

    async def test_automation_and_admin_are_allowed(self):
        ns = production_namespace()
        self.assertEqual(await ns["async_check_entity_control"](SimpleNamespace(), SimpleNamespace(user_id=None), ["light.one"]), ["light.one"])
        hass = SimpleNamespace()
        actor(hass, SimpleNamespace(is_admin=True))
        self.assertEqual(await ns["async_check_entity_control"](hass, SimpleNamespace(user_id="admin"), ["light.one"]), ["light.one"])


class AuthorizationControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_off_does_not_clear_manual_control_or_baselines(self):
        h = event_harness()
        flags = h.ns["LightControlAttributes"]
        h.manager.manual_control["light.one"] = flags.ALL
        h.manager.last_service_data["light.one"] = {"brightness": 5}
        actor(h.hass, restricted_user(set()))
        await h.manager.turn_on_off_event_listener(event("turn_off", ["light.one"], h.original_context))
        self.assertEqual(h.manager.manual_control["light.one"], flags.ALL)
        self.assertEqual(h.manager.last_service_data["light.one"], {"brightness": 5})
        self.assertEqual(h.manager.turn_off_event, {})

    async def test_all_event_only_changes_permitted_lights(self):
        h = event_harness({"light.one": ["rgbww"], "light.two": ["rgbww"]})
        flags = h.ns["LightControlAttributes"]
        h.manager.manual_control.update({light: flags.ALL for light in h.states})
        h.manager._get_entity_list = lambda _: list(h.states)
        actor(h.hass, restricted_user({"light.one"}))
        await h.manager.turn_on_off_event_listener(event("turn_off", "all", h.original_context))
        self.assertEqual(h.manager.manual_control["light.one"], flags.NONE)
        self.assertEqual(h.manager.manual_control["light.two"], flags.ALL)
        self.assertEqual(set(h.manager.turn_off_event), {"light.one"})

    async def test_denied_manual_service_never_pauses_or_resumes(self):
        for manual in (True, False):
            with self.subTest(manual=manual):
                h = event_harness()
                handler = generic_handler(h, "handle_set_manual_control")
                flags = h.ns["LightControlAttributes"]
                h.manager.manual_control["light.one"] = flags.ALL
                actor(h.hass, restricted_user(set()))
                call = ServiceCall(h.hass, "circadian_pro", "set_manual_control", {
                    "entity_id": [h.profile.entity_id], "lights": [], "manual_control": manual,
                }, h.original_context)
                with self.assertRaises(h.ns["Unauthorized"]):
                    await handler(call)
                self.assertEqual(h.manager.manual_control["light.one"], flags.ALL)
                self.assertEqual(h.manager.last_service_data, {})
                self.assertEqual(h.events, [])

    async def test_manual_service_checks_every_light_before_first_mutation(self):
        h = event_harness({"light.one": ["rgbww"], "light.two": ["rgbww"]})
        handler = generic_handler(h, "handle_set_manual_control")
        actor(h.hass, restricted_user({h.profile.entity_id, "light.one"}))
        call = ServiceCall(h.hass, "circadian_pro", "set_manual_control", {
            "entity_id": [h.profile.entity_id], "lights": [], "manual_control": True,
        }, h.original_context)
        with self.assertRaises(h.ns["Unauthorized"]):
            await handler(call)
        self.assertEqual(h.manager.manual_control, {})
        self.assertEqual(h.events, [])

    async def test_apply_checks_profile_permission_even_if_light_is_allowed(self):
        h = event_harness()
        handler = generic_handler(h, "handle_apply")
        actor(h.hass, restricted_user({"light.one"}))
        call = ServiceCall(h.hass, "circadian_pro", "apply", {
            "entity_id": [h.profile.entity_id], "lights": [], "turn_on_lights": True,
            "transition": 0, "adapt_brightness": True, "adapt_color": True, "prefer_rgb_color": False,
        }, h.original_context)
        with self.assertRaises(h.ns["Unauthorized"]):
            await handler(call)
        self.assertEqual(h.calls, [])
        self.assertEqual(h.resets, [])

    async def test_denied_interception_does_not_prepare_or_reset(self):
        h = event_harness()
        actor(h.hass, restricted_user(set()))
        data = {"entity_id": ["light.one"], "params": {}}
        call = ServiceCall(h.hass, "light", "turn_on", deepcopy(data), h.original_context)
        with self.assertRaises(h.ns["Unauthorized"]):
            await h.manager._service_interceptor_turn_on_handler(call, data)
        self.assertEqual(h.prepared, [])
        self.assertEqual(h.manager.last_service_data, {})
        self.assertEqual(h.proactive, {})
        self.assertEqual(data, {"entity_id": ["light.one"], "params": {}})

    async def test_all_interception_only_dispatches_allowed_lights(self):
        h = event_harness({"light.one": ["rgbww"], "light.two": ["rgbww"]})
        h.manager._get_entity_list = lambda _: list(h.states)
        actor(h.hass, restricted_user({"light.one"}))
        data = {"entity_id": "all", "params": {}}
        call = ServiceCall(h.hass, "light", "turn_on", deepcopy(data), h.original_context)
        finish = await h.manager._service_interceptor_turn_on_handler(call, data)
        await h.native_call("light", "turn_on", {"entity_id": data["entity_id"], **data["params"]}, context=h.original_context)
        if finish:
            finish(True)
        self.assertEqual(data["entity_id"], ["light.one"])
        self.assertEqual(h.prepared, ["light.one"])
        self.assertEqual(h.calls_for("light.two"), [])

    async def test_eager_initial_nonbare_call_obeys_bare_only_setting(self):
        for bare_only in (False, True):
            with self.subTest(bare_only=bare_only):
                h = event_harness()
                h.profile._adapt_only_on_bare_turn_on = bare_only
                h.manager._separate_entity_ids = MethodType(h.ns["_separate_entity_ids"], h.manager)
                await h.manager.turn_on_off_event_listener(event("turn_on", ["light.one"], h.original_context, brightness=100))
                self.assertEqual(h.manager.manual_control, {})
                data, _ = await h.run(["light.one"], {"brightness": 100})
                if bare_only:
                    self.assertEqual(data["params"]["brightness"], 100)
                    self.assertTrue(h.manager.manual_control["light.one"].has_any())
                else:
                    self.assertEqual(data["params"]["brightness"], 204)
                    self.assertFalse(h.manager.manual_control.get("light.one"))

    async def test_already_on_call_still_marks_manual_control(self):
        h = event_harness()
        h.states["light.one"].state = "on"
        await h.manager.turn_on_off_event_listener(event("turn_on", ["light.one"], h.original_context, brightness=100))
        self.assertEqual(h.manager.manual_control["light.one"], h.ns["LightControlAttributes"].BRIGHTNESS)


def proxy_for(h, original):
    jobs = SimpleNamespace(Coroutinefunction=object(), Callback=object())
    ns = h.ns
    ns.update(HassJobType=jobs, ReadOnlyDict=dict, isawaitable=isawaitable)
    load_functions(ns, "hass_utils.py", {"setup_service_call_interceptor"})
    original_entry = SimpleNamespace(job=SimpleNamespace(target=original, job_type=jobs.Coroutinefunction),
                                     schema=None, supports_response="optional", description_placeholders={"name": "light"})
    registry = {"light": {"turn_on": original_entry}}
    def register(domain, service, target, schema, **kwargs):
        registry[domain][service] = SimpleNamespace(job=SimpleNamespace(target=target, job_type=kwargs.get("job_type", jobs.Coroutinefunction)),
                                                   schema=schema, **{key: value for key, value in kwargs.items() if key != "job_type"})
    h.hass.services._services = registry
    h.hass.services.async_register = register
    ns["setup_service_call_interceptor"](h.hass, "light", "turn_on", h.manager._service_interceptor_turn_on_handler)
    return registry["light"]["turn_on"].job.target


class OriginalHandlerBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_original_completes_before_any_split_continuation(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        entered, release = asyncio.Event(), asyncio.Event()
        log = []
        native = h.native_call
        async def color_call(*args, **kwargs):
            log.append("color")
            return await native(*args, **kwargs)
        h.hass.services.async_call = color_call
        async def original(call):
            log.append("brightness entered")
            entered.set()
            await release.wait()
            await native("light", "turn_on", {"entity_id": call.data["entity_id"], **call.data["params"]}, context=call.context)
            log.append("brightness completed")
            return {"result": "ok"}
        proxy = proxy_for(h, original)
        call = ServiceCall(h.hass, "light", "turn_on", {"entity_id": ["light.one"], "params": {"transition": 0}}, h.original_context)
        task = asyncio.create_task(proxy(call))
        await entered.wait()
        for _ in range(4):
            await asyncio.sleep(0)
        self.assertEqual(log, ["brightness entered"])
        self.assertEqual(h.tasks, [])
        release.set()
        self.assertEqual(await task, {"result": "ok"})
        await asyncio.gather(*h.tasks)
        self.assertEqual(log, ["brightness entered", "brightness completed", "color"])

    async def test_original_error_is_preserved_and_no_continuation_or_baseline_survives(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        failure = ValueError("Device failed")
        async def original(_):
            raise failure
        proxy = proxy_for(h, original)
        call = ServiceCall(h.hass, "light", "turn_on", {"entity_id": ["light.one"], "params": {}}, h.original_context)
        with self.assertRaises(ValueError) as raised:
            await proxy(call)
        self.assertIs(raised.exception, failure)
        self.assertEqual(h.tasks, [])
        self.assertEqual(h.manager.last_service_data, {})
        self.assertEqual(h.manager.last_service_context, {})
        self.assertEqual(h.proactive, {})

    async def test_original_cancellation_cleans_up_and_propagates(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        entered = asyncio.Event()
        async def original(_):
            entered.set()
            await asyncio.Future()
        proxy = proxy_for(h, original)
        call = ServiceCall(h.hass, "light", "turn_on", {"entity_id": ["light.one"], "params": {}}, h.original_context)
        task = asyncio.create_task(proxy(call))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(h.tasks, [])
        self.assertEqual(h.manager.last_service_data, {})
        self.assertEqual(h.proactive, {})

    async def test_original_error_cleanup_does_not_erase_a_newer_baseline(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        async def original(_):
            h.manager.last_service_data["light.one"] = {"brightness": 10}
            h.manager.last_service_context["light.one"] = "newer"
            h.manager.set_proactively_adapting("newer", "light.one")
            raise ValueError("Old device command failed")
        proxy = proxy_for(h, original)
        call = ServiceCall(h.hass, "light", "turn_on", {"entity_id": ["light.one"], "params": {}}, h.original_context)
        with self.assertRaises(ValueError):
            await proxy(call)
        self.assertEqual(h.manager.last_service_data["light.one"], {"brightness": 10})
        self.assertEqual(h.manager.last_service_context["light.one"], "newer")
        self.assertEqual(h.proactive["newer"], "light.one")

    async def test_original_success_does_not_resume_an_obsolete_sequence(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        async def original(_):
            h.manager.last_service_data["light.one"] = {"brightness": 10}
            h.manager.last_service_context["light.one"] = "newer"
        proxy = proxy_for(h, original)
        call = ServiceCall(h.hass, "light", "turn_on", {"entity_id": ["light.one"], "params": {}}, h.original_context)
        await proxy(call)
        self.assertEqual(h.tasks, [])
        self.assertEqual(h.manager.last_service_data["light.one"], {"brightness": 10})

    async def test_regular_split_dispatch_waits_for_first_device_handler(self):
        h = InterceptionHarness({"light.one": ["rgbww"]}, split=True)
        log = []
        native = h.native_call
        async def dispatch(domain, service, payload, **kwargs):
            self.assertTrue(kwargs.get("blocking"))
            label = "brightness" if "brightness" in payload else "color"
            log.append(label + " entered")
            await asyncio.sleep(0)
            await native(domain, service, payload, **kwargs)
            log.append(label + " completed")
        h.hass.services.async_call = dispatch
        h.states["light.one"].state = "on"
        data = await h.profile.prepare_adaptation_data("light.one", 0, context=h.new_context("interval"))
        await h.ns["_execute_adaptation_calls"](h.profile, data)
        self.assertEqual(log, ["brightness entered", "brightness completed", "color entered", "color completed"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
