"""Panel-only activation and profile lifecycle without Home Assistant I/O."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_calibration_flow as flow_fixtures
import test_audit_config as audit_fixtures


def panel_entry(unique_id="circadian_pro:panel"):
    return SimpleNamespace(
        entry_id="panel", unique_id=unique_id, title="Circadian Pro",
        data={"entry_type": "panel"}, options={}, source="system",
    )


def profile_entry(name="Bedroom"):
    return SimpleNamespace(
        entry_id=f"profile-{name}", unique_id=name, title=name,
        data={"name": name}, options={"lights": ["light.bedroom"]}, source="user",
    )


class PanelBootstrapFlowTests(unittest.TestCase):
    def setUp(self):
        fixture = flow_fixtures.ConfigCreationTests()
        fixture.setUp()
        self.namespace = fixture.namespace
        self.Flow = fixture.Flow
        self.immediate = flow_fixtures.immediate

    def test_first_activation_creates_only_panel_without_a_name_form(self):
        flow = self.Flow()
        result = self.immediate(flow.async_step_user())
        self.assertEqual(result, {
            "type": "create_entry", "title": "Circadian Pro",
            "data": {"entry_type": "panel"},
        })
        self.assertEqual(flow.unique_id, "circadian_pro:panel")
        self.assertNotIn("lights", result["data"])

    def test_only_panel_opens_name_form_for_actual_first_profile(self):
        flow = self.Flow([panel_entry()])
        result = self.immediate(flow.async_step_user())
        self.assertEqual(result["step_id"], "user")
        created = self.immediate(flow.async_step_user({"name": "Desk"}))
        self.assertEqual(created["data"], {"name": "Desk"})

    def test_system_bootstrap_detects_marked_panel_even_with_different_id(self):
        entry = panel_entry("old-id")
        flow = self.Flow([entry, profile_entry()])
        self.assertEqual(self.immediate(flow.async_step_system()), {
            "type": "abort", "reason": "panel_already_configured",
        })
        self.assertEqual(flow.created_entries, [])

    def test_panel_identity_preserves_legacy_profiles_named_like_internal_id(self):
        entries = [profile_entry("circadian_pro:panel"), profile_entry("circadian_pro:panel:panel")]
        before = deepcopy(entries)
        flow = self.Flow(entries)
        result = self.immediate(flow.async_step_system())
        self.assertEqual(flow.unique_id, "circadian_pro:panel:panel:panel")
        self.assertEqual(result["data"], {"entry_type": "panel"})
        self.assertEqual(entries, before)

    def test_concurrent_creation_is_rejected_by_shared_unique_id_guard(self):
        flow = self.Flow(in_progress=["circadian_pro:panel"])
        with self.assertRaises(self.namespace["AbortFlow"]) as caught:
            self.immediate(flow.async_step_panel())
        self.assertEqual(caught.exception.reason, "already_in_progress")
        self.assertEqual(flow.created_entries, [])

    def test_duplicate_menu_hides_panel_and_rejects_forged_targets(self):
        panel = panel_entry()
        profile = profile_entry()
        flow = self.Flow([panel, profile])
        form = self.immediate(flow.async_step_user())
        choices = next(iter(form["data_schema"].schema.values()))
        self.assertEqual({option["value"] for option in choices.options}, {"new", profile.entry_id})
        self.assertEqual(choices.translation_key, "profile_action")
        self.assertEqual(choices.options[-1]["label"], profile.title)
        for target in (panel, profile_entry("foreign")):
            flow.hass.config_entries.async_get_entry = lambda _entry_id, target=target: target
            result = self.immediate(flow.async_step_menu({"action": target.entry_id}))
            self.assertEqual(result["errors"], {"base": "profile_missing"})
            self.assertIsNone(flow.source_options)

    def test_panel_options_direct_to_panel_before_any_profile_validation(self):
        handler = self.namespace["OptionsFlowHandler"]

        class Flow(handler):
            config_entry = panel_entry()

            def async_abort(self, **kwargs):
                return {"type": "abort", **kwargs}

        self.namespace["validate_entry"] = lambda _: self.fail("Panel is not a profile")
        self.assertEqual(self.immediate(Flow().async_step_init()), {
            "type": "abort", "reason": "panel_configuration",
        })

    def test_yaml_name_collision_cannot_overwrite_panel_entry(self):
        flow_fixtures.load_nodes(self.namespace, "config_flow.py", {"async_step_import"}, class_name="ConfigFlow")
        self.Flow.async_step_import = self.namespace["async_step_import"]
        panel = panel_entry()
        flow = self.Flow([panel])
        flow.hass.data = {}
        flow.hass.config_entries.async_update_entry = lambda *args, **kwargs: self.fail("Panel must not be overwritten")
        result = self.immediate(flow.async_step_import({"name": panel.unique_id, "lights": ["light.bedroom"]}))
        self.assertEqual(result, {"type": "abort", "reason": "panel_name_reserved"})
        self.assertEqual(panel.data, {"entry_type": "panel"})

    def test_activation_and_options_messages_link_directly_to_panel(self):
        for filename in ("strings.json", "translations/en.json", "translations/es.json"):
            data = json.loads((flow_fixtures.COMPONENT / filename).read_text(encoding="utf-8"))
            self.assertIn("/circadian-pro-calibration", data["config"]["create_entry"]["default"])
            self.assertIn("/circadian-pro-calibration", data["options"]["abort"]["panel_configuration"])


class PanelBootstrapLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixture = audit_fixtures.ConfigAuditTests()
        fixture.setUp()
        self.namespace, self.modules = fixture.lifecycle()
        self.namespace.update({
            "SOURCE_SYSTEM": "system", "CONF_SOURCE": "source",
            "_LOGGER": logging.getLogger("circadian_pro.bootstrap_tests"),
        })
        flow_fixtures.load_nodes(self.namespace, "__init__.py", {"async_setup", "_async_ensure_panel_entry"})
        self.frontend_calls = []

        async def frontend(hass):
            self.frontend_calls.append(hass)

        frontend_module = ModuleType("audit_config_package.frontend")
        frontend_module.async_setup_frontend = frontend
        self.modules["audit_config_package.frontend"] = frontend_module
        self.immediate = flow_fixtures.immediate

    def test_legacy_setup_schedules_anchor_preserves_profiles_and_does_not_wait(self):
        profile = profile_entry()
        before = deepcopy(profile)
        tasks = []
        calls = []

        async def init(domain, **kwargs):
            calls.append((domain, kwargs))

        hass = SimpleNamespace(
            async_create_task=tasks.append,
            config_entries=SimpleNamespace(async_entries=lambda _: [profile], flow=SimpleNamespace(async_init=init)),
        )
        with patch.dict(sys.modules, self.modules):
            self.assertTrue(self.immediate(self.namespace["async_setup"](hass, {})))
        self.assertEqual(self.frontend_calls, [hass])
        self.assertEqual(calls, [])  # No nested await of entry setup.
        self.assertEqual(len(tasks), 1)
        self.immediate(tasks.pop())
        self.assertEqual(calls, [("circadian_pro", {"context": {"source": "system"}})])
        self.assertEqual(profile, before)

    def test_restart_with_only_panel_registers_sidebar_without_creating_another_entry(self):
        hass = SimpleNamespace(
            async_create_task=lambda _: self.fail("Existing panel must not be recreated"),
            config_entries=SimpleNamespace(async_entries=lambda _: [panel_entry()]),
        )
        with patch.dict(sys.modules, self.modules):
            self.assertTrue(self.immediate(self.namespace["async_setup"](hass, {})))
        self.assertEqual(self.frontend_calls, [hass])

    def test_anchor_setup_and_unload_do_not_touch_profile_runtime_or_configuration(self):
        panel = panel_entry()
        before = deepcopy(panel)
        # No attributes at all: any runtime/configuration access is a failure.
        hass = SimpleNamespace()
        self.assertTrue(self.immediate(self.namespace["async_setup_entry"](hass, panel)))
        self.assertTrue(self.immediate(self.namespace["async_unload_entry"](hass, panel)))
        self.assertEqual(panel, before)

    def test_last_profile_unload_releases_manager_even_while_panel_is_configured(self):
        calls = []

        async def unload(*args):
            return True

        manager = SimpleNamespace(
            rebuild_lights=lambda: calls.append("rebuild"), disable=lambda: calls.append("disable"),
        )
        hass = SimpleNamespace(
            data={"circadian_pro": {"profile-Bedroom": {"switch": object()}, "manager": manager}},
            config_entries=SimpleNamespace(async_entries=lambda _: [panel_entry(), profile_entry()], async_unload_platforms=unload),
        )
        with patch.dict(sys.modules, self.modules):
            self.assertTrue(self.immediate(self.namespace["async_unload_entry"](hass, profile_entry())))
        self.assertEqual(calls, ["rebuild", "disable"])
        self.assertNotIn("circadian_pro", hass.data)

    def test_background_bootstrap_failure_is_logged_without_failing_loaded_profiles(self):
        async def init(*args, **kwargs):
            raise RuntimeError("storage failed")

        hass = SimpleNamespace(config_entries=SimpleNamespace(flow=SimpleNamespace(async_init=init)))
        with self.assertLogs("circadian_pro.bootstrap_tests", level="ERROR") as captured:
            self.immediate(self.namespace["_async_ensure_panel_entry"](hass))
        self.assertIn("Could not create the Circadian Pro panel entry", captured.output[0])
        self.assertIn("storage failed", captured.output[0])

    def test_background_bootstrap_propagates_shutdown_cancellation(self):
        async def init(*args, **kwargs):
            raise asyncio.CancelledError()

        hass = SimpleNamespace(config_entries=SimpleNamespace(flow=SimpleNamespace(async_init=init)))
        with self.assertRaises(asyncio.CancelledError):
            self.immediate(self.namespace["_async_ensure_panel_entry"](hass))


if __name__ == "__main__":
    unittest.main()
