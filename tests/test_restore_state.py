"""Switch restoration across simulated HA starts, stops and config reloads.

Production lifecycle methods are extracted with AST so these tests need no HA
installation. The I/O doubles model HA 2026.10.1's internal RestoreEntity hooks:
https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/restore_state.py#L265-L304
They do not model filesystem failures or prove persistence on a running HA host.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, field
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).parents[1] / "custom_components" / "circadian_pro" / "switch.py"


def complete(coroutine):
    """Execute a coroutine whose test I/O completes without an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    else:
        coroutine.close()
        raise AssertionError("Unexpected asynchronous I/O in restore-state test")


@dataclass
class State:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)


class RestoreStore:
    def __init__(self, saved):
        self.entities = {}
        self.last_states = deepcopy(saved)

    def dump(self, states):
        # The storage boundary saves registered current states. On reload,
        # RestoreEntity captures the last state before HA publishes unavailable.
        current = {key: value for key, value in states.items() if not value.attributes.get("restored")}
        saved = {key: deepcopy(current[key]) for key in self.entities if key in current}
        saved.update({key: deepcopy(value) for key, value in self.last_states.items() if key not in current})
        return saved


class Entity:
    async def async_internal_added_to_hass(self):
        pass

    async def async_internal_will_remove_from_hass(self):
        pass

    async def add_to_platform_finish(self):
        # HA Entity.add_to_platform_finish invokes both hooks independently.
        await self.async_internal_added_to_hass()
        await self.async_added_to_hass()
        self.async_write_ha_state()

    def async_write_ha_state(self):
        self.hass.states[self.entity_id] = State(self.entity_id, self.state)

    def async_on_remove(self, callback):
        self.remove_callbacks.append(callback)

    async def remove(self):
        for callback in reversed(self.remove_callbacks):
            callback()
        await self.async_internal_will_remove_from_hass()
        self.hass.states[self.entity_id] = State(self.entity_id, "unavailable", {"restored": True})


class ToggleEntity(Entity):
    @property
    def state(self):
        return "on" if self.is_on else "off"


class SwitchEntity(ToggleEntity):
    pass


class RestoreEntity(Entity):
    async def async_internal_added_to_hass(self):
        await super().async_internal_added_to_hass()
        self.hass.restore.entities[self.entity_id] = self

    async def async_internal_will_remove_from_hass(self):
        state = self.hass.states.get(self.entity_id)
        if state is not None:
            self.hass.restore.last_states[self.entity_id] = deepcopy(state)
        del self.hass.restore.entities[self.entity_id]
        await super().async_internal_will_remove_from_hass()

    async def async_get_last_state(self):
        if self.hass.restore.entities.get(self.entity_id) is not self:
            raise AssertionError("Restoration was read before internal registration")
        return self.hass.restore.last_states.get(self.entity_id)


def load_switches():
    methods = {
        "CircadianProSwitch": {
            "is_on", "async_added_to_hass", "async_turn_on", "async_turn_off",
            "_remove_listeners", "_remove_interval_listener", "_update_attrs_and_maybe_adapt_lights",
        },
        "SimpleSwitch": {"is_on", "async_added_to_hass", "async_turn_on", "async_turn_off"},
    }
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    classes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in methods:
            node.body = [method for method in node.body if getattr(method, "name", None) in methods[node.name]]
            classes.append(node)
    namespace = {
        "SwitchEntity": SwitchEntity, "RestoreEntity": RestoreEntity,
        "STATE_ON": "on", "EVENT_HOMEASSISTANT_STARTED": "started",
        "_LOGGER": logging.getLogger(__name__),
        "is_on": lambda hass, entity_id: hass.states.get(entity_id, State(entity_id, "off")).state == "on",
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *classes], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["CircadianProSwitch"], namespace["SimpleSwitch"]


MAIN, SIMPLE = load_switches()


class RestoreStateTests(unittest.TestCase):
    def boot(self, kind="main", saved=None, initial=True, running=False):
        hass = SimpleNamespace(
            states={}, restore=RestoreStore(saved or {}), is_running=running,
            bus=SimpleNamespace(async_listen_once=lambda *_: lambda: None),
        )
        entity = object.__new__(MAIN if kind == "main" else SIMPLE)
        entity.hass = hass
        entity.entity_id = f"switch.circadian_pro_{kind}"
        entity.remove_callbacks = []
        entity._state = None if kind == "main" else initial
        entity._initial_state = initial
        entity._name = kind
        entity._only_once = False
        entity.lights = []
        entity.remove_listeners = []
        entity.remove_interval = lambda: None
        entity._settings = {}
        entity.initial_transition = 0
        entity._effective_sleep_state = lambda: False
        entity._sun_light_settings = SimpleNamespace(get_settings=lambda *_: {})
        entity.create_context = lambda *_: SimpleNamespace(id="test-context")
        entity.manager = SimpleNamespace(
            register_switch=lambda _: None, unregister_switch=lambda _: None,
            reset=lambda *_: None,
        )

        async def setup_listeners(*_):
            pass

        entity._setup_listeners = setup_listeners
        if kind == "main":
            entity.async_on_remove(entity._remove_listeners)
        complete(entity.add_to_platform_finish())
        return entity, hass

    def service(self, entity, enabled):
        complete(entity.async_turn_on() if enabled else entity.async_turn_off())
        # HA's entity-service handler publishes the state after the method.
        entity.async_write_ha_state()

    def test_internal_hook_registers_both_switch_classes_before_public_restore(self):
        for kind in ("main", "sleep", "color", "brightness"):
            with self.subTest(kind=kind):
                entity, hass = self.boot(kind)
                self.assertIs(hass.restore.entities[entity.entity_id], entity)
                self.assertEqual(hass.restore.dump(hass.states)[entity.entity_id].state, "on")

    def test_new_profile_defaults_remain_main_on_sleep_off_adaptation_on(self):
        for kind, initial in (("main", True), ("sleep", False), ("color", True), ("brightness", True)):
            with self.subTest(kind=kind):
                entity, hass = self.boot(kind, initial=initial)
                self.assertEqual(entity.is_on, initial)
                self.assertEqual(hass.states[entity.entity_id].state, "on" if initial else "off")

    def test_real_service_changes_survive_three_restarts_with_old_off_cache(self):
        for kind in ("main", "sleep", "color", "brightness"):
            with self.subTest(kind=kind):
                entity_id = f"switch.circadian_pro_{kind}"
                saved = {entity_id: State(entity_id, "off")}
                for enabled in (True, False, True):
                    entity, hass = self.boot(kind, saved=saved)
                    self.service(entity, enabled)
                    saved = hass.restore.dump(hass.states)
                    restored, _ = self.boot(kind, saved=saved)
                    self.assertEqual(restored.is_on, enabled)

    def test_unload_cleanup_and_unavailable_placeholder_preserve_last_on_state(self):
        for kind in ("main", "sleep", "color", "brightness"):
            with self.subTest(kind=kind):
                entity, hass = self.boot(kind, initial=False)
                self.service(entity, True)
                complete(entity.remove())
                self.assertTrue(entity.is_on)
                self.assertNotIn(entity.entity_id, hass.restore.entities)
                self.assertEqual(hass.states[entity.entity_id].state, "unavailable")
                saved = hass.restore.dump(hass.states)
                self.assertEqual(saved[entity.entity_id].state, "on")
                restored, _ = self.boot(kind, saved=saved, running=True)
                self.assertTrue(restored.is_on)


if __name__ == "__main__":
    unittest.main()
