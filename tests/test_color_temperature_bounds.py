"""Protocol regressions without requiring a Home Assistant installation.

The harness executes the integration's actual functions, extracted with AST.
Only Home Assistant I/O, entities, and HA's Kelvin-to-RGB helper are doubles.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum, IntFlag, auto
import logging
from pathlib import Path
import re
from types import MethodType, SimpleNamespace
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


class ColorMode(str, Enum):
    """Home Assistant's public color-mode values used by the tested protocol."""

    BRIGHTNESS = "brightness"
    COLOR_TEMP = "color_temp"
    RGB = "rgb"
    RGBW = "rgbw"
    RGBWW = "rgbww"
    XY = "xy"
    HS = "hs"


class State:
    def __init__(self, attributes: dict, context_id="test-report"):
        self.attributes = attributes
        self.context = SimpleNamespace(id=context_id)
        self.state = "on"


class Unauthorized(Exception):
    """HA authorization exception, with observable error context."""

    def __init__(self, **kwargs):
        self.details = kwargs
        super().__init__(str(kwargs))


class UnknownUser(Unauthorized):
    """The caller's user no longer exists."""


def complete_immediate(coroutine):
    """Run a coroutine whose HA I/O doubles complete immediately.

    No event loop is necessary for these deterministic protocol checks. Fail if
    a function starts awaiting real background work instead of hiding that work.
    """
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    else:
        coroutine.close()
        raise AssertionError("Unexpected background await in isolated protocol test")


def load_functions(namespace, filename, names, class_name=None, rename=None):
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    if class_name is not None:
        tree = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
    nodes = []
    for node in tree.body:
        selected = getattr(node, "name", None) in names
        if isinstance(node, ast.Assign):
            selected = any(
                isinstance(target, ast.Name) and target.id in names
                for target in node.targets
            )
        if selected:
            if rename and getattr(node, "name", None) in rename:
                node.name = rename[node.name]
            nodes.append(node)
    missing = names - {
        getattr(node, "name", None)
        for node in tree.body
    } - {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    if rename:
        missing -= rename.keys()
    if missing:
        raise AssertionError(f"Production functions not found: {sorted(missing)}")
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    exec(
        compile(ast.fix_missing_locations(module), str(COMPONENT / filename), "exec"),
        namespace,
    )


def production_namespace():
    namespace = {
        "__name__": __name__,
        "_LOGGER": logging.getLogger("circadian_pro.protocol_tests"),
        "dataclass": dataclass,
        "IntFlag": IntFlag,
        "Enum": Enum,
        "auto": auto,
        "State": State,
        "ColorMode": ColorMode,
        "LightEntityFeature": SimpleNamespace(TRANSITION=1),
        "LIGHT_DOMAIN": "light",
        "SERVICE_TURN_ON": "turn_on",
        "DOMAIN": "circadian_pro",
        "SWITCH_DOMAIN": "switch",
        "STATE_UNAVAILABLE": "unavailable",
        "STATE_UNKNOWN": "unknown",
        "CONF_MANUAL_CONTROL": "manual_control",
        "is_calibration_preview_active": lambda hass, light: False,
        "is_calibration_preview_context": lambda *_: False,
        "POLICY_CONTROL": "control",
        "Unauthorized": Unauthorized,
        "UnknownUser": UnknownUser,
    }
    for filename in ("switch.py", "adaptation_utils.py"):
        source = (COMPONENT / filename).read_text(encoding="utf-8")
        for name in set(re.findall(r"\bATTR_[A-Z_]+\b", source)):
            namespace[name] = name.removeprefix("ATTR_").lower()

    load_functions(namespace, "helpers.py", {"clamp", "color_difference_redmean", "remove_vowels"})
    load_functions(namespace, "hass_utils.py", {"async_check_entity_control"})
    # helpers.color_difference_redmean uses math only for a real external RGB change.
    import math

    namespace["math"] = math
    load_functions(namespace, "const.py", {"TakeOverControlMode"})
    load_functions(
        namespace,
        "adaptation_utils.py",
        {
            "COLOR_ATTRS",
            "BRIGHTNESS_ATTRS",
            "LightControlAttributes",
            "AdaptationData",
            "NoColorOrBrightnessInServiceDataError",
            "_split_service_call_data",
            "_remove_redundant_attributes",
            "_has_relevant_service_data_attributes",
            "_create_service_call_data_iterator",
            "_identify_light_control_attributes",
            "prepare_adaptation_data",
        },
    )
    load_functions(
        namespace,
        "switch.py",
        {
            "BRIGHTNESS_CHANGE",
            "COLOR_TEMP_CHANGE",
            "RGB_REDMEAN_CHANGE",
            "_DOMAIN_SHORT",
            "is_our_context",
            "is_our_context_id",
            "_supported_features",
            "_clamp_int",
            "_interp_white_intensity_rgbw",
            "_calibrated_white_channels",
            "_calibrated_rgb_from_kelvin",
            "_has_color_mode_changed",
            "_attributes_have_changed",
            "_add_missing_attributes",
            "_convert_attributes",
        },
    )
    load_functions(
        namespace,
        "switch.py",
        {
            "prepare_adaptation_data",
            "_execute_adaptation_calls",
            "fire_manual_control_event",
        },
        "CircadianProSwitch",
        {"prepare_adaptation_data": "prepare_switch_adaptation_data"},
    )
    load_functions(
        namespace,
        "switch.py",
        {
            "significant_change",
            "update_manually_controlled_from_untracked_change",
            "get_adaption_control_attributes",
            "get_manual_control_attributes",
            "add_manual_control_attributes",
            "set_manual_control_attributes",
            "_handle_timer",
        },
        "CircadianProManager",
    )
    return namespace


class ProtocolHarness:
    light = "light.example_a"

    def __init__(self, modes=("color_temp", "rgbww"), bounds=(2000, 6535)):
        self.ns = production_namespace()
        self.calls = []
        self.events = []
        self.calibration_targets = []
        self.refreshed_lights = []
        self.sleeps = []
        self.context_counter = 0
        self.state = State(
            {
                "supported_color_modes": set(modes),
                "supported_features": 0,
                "color_mode": "color_temp",
                "brightness": 255,
                "color_temp_kelvin": 3500,
            }
        )
        if bounds is not None:
            self.state.attributes.update(
                min_color_temp_kelvin=bounds[0], max_color_temp_kelvin=bounds[1]
            )

        async def call(domain, service, data, **kwargs):
            self.calls.append((domain, service, dict(data)))

        async def refresh(hass, light):
            self.refreshed_lights.append(light)

        real_calibrate = self.ns["_calibrated_rgb_from_kelvin"]

        def calibrate(kelvin, **kwargs):
            self.calibration_targets.append(kelvin)
            return real_calibrate(kelvin, **kwargs)

        async def sleep(seconds):
            self.sleeps.append(seconds)

        def context(which, parent=None):
            self.context_counter += 1
            return SimpleNamespace(id=f"test-adaptation-{self.context_counter}", parent_id=parent.id if parent else None)

        # Dependency fixtures deliberately have an identical red value at the
        # calibration endpoints. This exercises the production flat-channel fix.
        rgb_fixtures = {
            1000: (255, 56, 0),
            1200: (255, 80, 0),
            3500: (255, 200, 180),
            4000: (255, 228, 206),
            10000: (205, 228, 255),
        }

        self.ns["async_update_entity"] = refresh
        self.ns["_calibrated_rgb_from_kelvin"] = calibrate
        self.ns["color_temperature_to_rgb"] = rgb_fixtures.__getitem__
        self.ns["asyncio"] = SimpleNamespace(sleep=sleep)
        self.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda light: self.state),
            services=SimpleNamespace(async_call=call),
            bus=SimpleNamespace(
                async_fire=lambda event, data, **kwargs: self.events.append((event, data))
            ),
        )
        self.manager = SimpleNamespace(
            hass=self.hass,
            last_service_data={},
            last_service_context={},
            last_service_acknowledgements={},
            our_last_state_on_change={},
            manual_control={},
            auto_reset_manual_control_times={},
            auto_reset_manual_control_timers={},
        )
        for name in (
            "significant_change",
            "update_manually_controlled_from_untracked_change",
            "get_adaption_control_attributes",
            "get_manual_control_attributes",
            "add_manual_control_attributes",
            "set_manual_control_attributes",
            "_handle_timer",
        ):
            setattr(self.manager, name, MethodType(self.ns[name], self.manager))
        self.switch = SimpleNamespace(
            hass=self.hass,
            manager=self.manager,
            _name="Profile A",
            is_on=True,
            entity_id="switch.circadian_pro_example_a",
            _transition=0,
            _prefer_rgb_color=False,
            _send_split_delay=0,
            _separate_turn_on_commands=False,
            _skip_redundant_commands=False,
            _cal_rgb_warm=[255, 56, 0],
            _cal_rgb_neutral=[255, 255, 255],
            _cal_white_intensity_warm=0,
            _cal_white_intensity_neutral=100,
            _white_w_kelvin=3000,
            _white_cw_kelvin=None,
            _cal_white_kelvin_neutral=None,
            _effective_sleep_state=lambda: False,
            create_context=context,
            _take_over_control=True,
            _detect_non_ha_changes=True,
            _take_over_control_mode=self.ns["TakeOverControlMode"].PAUSE_ALL,
            adapt_brightness_switch=SimpleNamespace(is_on=True),
            adapt_color_switch=SimpleNamespace(is_on=True),
        )
        self.switch.fire_manual_control_event = MethodType(
            self.ns["fire_manual_control_event"], self.switch
        )

    def dispatch(self, target_kelvin, *, sleep=False, force_rgb=False, rgb=(255, 56, 0)):
        """Prepare and execute the real light.turn_on sequence; inspect wire data."""
        previous_calls = len(self.calls)
        self.switch._effective_sleep_state = lambda: sleep
        self.switch._sun_light_settings = SimpleNamespace(
            sleep_rgb_or_color_temp="rgb_color" if sleep else "color_temp",
            get_settings=lambda *args: {
                "brightness_pct": 100,
                "color_temp_kelvin": target_kelvin,
                "force_rgb_color": force_rgb,
                "rgb_color": rgb,
            },
        )
        data = complete_immediate(
            self.ns["prepare_switch_adaptation_data"](
                self.switch, self.light, force=True
            )
        )
        complete_immediate(self.ns["_execute_adaptation_calls"](self.switch, data))
        return self.calls[-1][2] if len(self.calls) > previous_calls else None

    def report_native(self, color, *, mode="rgbww", brightness=255):
        self.state.attributes.update(
            color_mode=mode,
            rgbww_color=color if mode == "rgbww" else None,
            color_temp_kelvin=None,
            brightness=brightness,
        )

    def acknowledge_color_temperature(self, kelvin):
        self.state.attributes.update(
            color_mode="color_temp", color_temp_kelvin=kelvin,
            rgbww_color=None, brightness=255,
        )
        context_id = self.manager.last_service_context[self.light]
        self.manager.our_last_state_on_change[self.light] = [
            State(dict(self.state.attributes), context_id)
        ]

    def detect_manual_change(self):
        complete_immediate(
            self.manager.update_manually_controlled_from_untracked_change(
                self.switch,
                self.light,
                force=False,
                context=SimpleNamespace(id="test-periodic-update"),
            )
        )
        return self.manager.get_manual_control_attributes(self.light)


class ColorTemperatureBoundsTests(unittest.TestCase):
    def test_cct_only_wire_payload_respects_device_range(self):
        for target, expected in ((1200, 2000), (3500, 3500), (10000, 6535)):
            with self.subTest(target=target):
                harness = ProtocolHarness(modes=("color_temp",))
                payload = harness.dispatch(target)
                self.assertEqual(harness.calls[-1][:2], ("light", "turn_on"))
                self.assertEqual(payload["color_temp_kelvin"], expected)
                self.assertEqual(payload["brightness"], 255)
                self.assertFalse(
                    {"rgb_color", "rgbw_color", "rgbww_color"} & payload.keys()
                )
                self.assertEqual(
                    harness.manager.last_service_data[harness.light], payload
                )

    def test_rgbw_calibration_keeps_requested_kelvin(self):
        harness = ProtocolHarness(modes=("rgbw",))
        payload = harness.dispatch(1200)
        self.assertEqual(harness.calibration_targets, [1200])
        self.assertIn("rgbw_color", payload)
        self.assertNotIn("color_temp_kelvin", payload)

    def test_missing_device_bounds_preserves_existing_fallback(self):
        harness = ProtocolHarness(modes=("color_temp",), bounds=None)
        payload = harness.dispatch(1200)
        self.assertEqual(payload["color_temp_kelvin"], 1200)

    def test_brightness_only_light_receives_no_color_command(self):
        harness = ProtocolHarness(modes=("brightness",))
        payload = harness.dispatch(1200)
        self.assertEqual(payload, {"entity_id": harness.light, "brightness": 255})

    def test_limited_payload_does_not_pause_on_its_own_reported_state(self):
        harness = ProtocolHarness(modes=("color_temp",))
        payload = harness.dispatch(1200)
        # This is the device's actual minimum, rather than echoing the request.
        harness.state.attributes.update(color_temp_kelvin=2000, brightness=255)
        manual = harness.detect_manual_change()
        flags = harness.ns["LightControlAttributes"]
        self.assertEqual(payload["color_temp_kelvin"], 2000)
        self.assertEqual(manual, flags.NONE)
        self.assertEqual(
            harness.manager.get_adaption_control_attributes(harness.switch, harness.light),
            flags.ALL,
        )
        self.assertEqual(harness.refreshed_lights, [harness.light])
        self.assertEqual(harness.events, [])
        self.assertEqual(harness.manager.auto_reset_manual_control_timers, {})

    def test_real_external_color_change_still_pauses_adaptation(self):
        harness = ProtocolHarness(modes=("color_temp",))
        harness.dispatch(1200)
        harness.state.attributes.update(color_temp_kelvin=3000, brightness=255)
        manual = harness.detect_manual_change()
        flags = harness.ns["LightControlAttributes"]
        self.assertEqual(manual, flags.COLOR)
        self.assertEqual(
            harness.manager.get_adaption_control_attributes(harness.switch, harness.light),
            flags.NONE,
        )
        self.assertEqual(harness.events[0][0], "circadian_pro.manual_control")
        self.assertEqual(harness.events[0][1]["manual_control"], flags.COLOR)

    def test_real_external_brightness_change_still_pauses_adaptation(self):
        harness = ProtocolHarness(modes=("color_temp",))
        harness.dispatch(1200)
        harness.state.attributes.update(color_temp_kelvin=2000, brightness=128)
        manual = harness.detect_manual_change()
        flags = harness.ns["LightControlAttributes"]
        self.assertEqual(manual, flags.BRIGHTNESS)
        self.assertEqual(
            harness.manager.get_adaption_control_attributes(harness.switch, harness.light),
            flags.NONE,
        )


class RGBWWProtocolTests(unittest.TestCase):
    def test_1200k_keeps_warm_rgb_and_fades_3000k_white(self):
        harness = ProtocolHarness()
        payload = harness.dispatch(1200)
        self.assertEqual(harness.calibration_targets, [1200])
        self.assertNotIn("color_temp_kelvin", payload)
        red, green, blue, cold, warm = payload["rgbww_color"]
        self.assertGreater(red, 0)
        self.assertGreater(green, 0)
        self.assertEqual(cold, 0)
        self.assertGreater(warm, 0)
        self.assertLess(warm, 255)
        self.assertEqual(harness.manager.last_service_data[harness.light]["rgbww_color"], payload["rgbww_color"])
        self.assertEqual(harness.manager.last_service_data[harness.light]["brightness"], harness.calls[0][2]["brightness"])

    def test_calibration_endpoints_preserve_all_rgb_channels(self):
        for target, expected in ((1000, (180, 40, 10)), (4000, (100, 220, 240))):
            with self.subTest(target=target):
                harness = ProtocolHarness()
                harness.switch._cal_rgb_warm = [180, 40, 10]
                harness.switch._cal_rgb_neutral = [100, 220, 240]
                payload = harness.dispatch(target)
                self.assertEqual(payload["rgbww_color"][:3], expected)
                if target == 1000:
                    self.assertEqual(payload["rgbww_color"][3:], (0, 0))
                else:
                    self.assertGreater(payload["rgbww_color"][3], 0)
                    self.assertGreater(payload["rgbww_color"][4], 0)
                    self.assertEqual(sum(payload["rgbww_color"][3:]), 255)

    def test_white_mix_override_does_not_change_rgb_calibration(self):
        reports = []
        for override in (3000, 6500):
            harness = ProtocolHarness()
            harness.switch._white_cw_kelvin = 6500
            harness.switch._cal_white_kelvin_neutral = override
            reports.append(harness.dispatch(4000)["rgbww_color"])
        self.assertEqual(reports[0][:3], reports[1][:3])
        self.assertEqual(reports[0][3:], (0, 255))
        self.assertEqual(reports[1][3:], (255, 0))

    def test_sleep_and_forced_rgb_disable_both_white_channels(self):
        for kwargs in ({"sleep": True}, {"force_rgb": True}):
            with self.subTest(kwargs=kwargs):
                harness = ProtocolHarness()
                payload = harness.dispatch(1200, rgb=(80, 5, 0), **kwargs)
                self.assertEqual(payload["rgbww_color"], (80, 5, 0, 0, 0))
                self.assertNotIn("rgb_color", payload)
                self.assertNotIn("color_temp_kelvin", payload)

    def test_separate_calls_keep_the_full_native_color_vector(self):
        harness = ProtocolHarness()
        harness.switch._separate_turn_on_commands = True
        payload = harness.dispatch(1200)
        self.assertEqual(len(harness.calls), 2)
        self.assertEqual(
            harness.calls[0][2], {"entity_id": harness.light, "brightness": 255}
        )
        self.assertEqual(set(payload), {"entity_id", "rgbww_color"})
        self.assertEqual(len(payload["rgbww_color"]), 5)
        self.assertEqual(harness.sleeps, [0])
        self.assertEqual(
            harness.manager.last_service_data[harness.light],
            {**payload, "brightness": 255},
        )

    def test_own_vector_report_ignores_derived_rgb_and_temperature(self):
        for representation in (tuple, list):
            with self.subTest(representation=representation):
                harness = ProtocolHarness()
                payload = harness.dispatch(1200)
                requested = payload["rgbww_color"]
                # Include a small transport quantization in every native channel.
                observed = [min(255, channel + 2) for channel in requested]
                harness.report_native(representation(observed))
                harness.state.attributes.update(
                    rgb_color=(0, 0, 255), color_temp_kelvin=5000
                )
                self.assertEqual(
                    harness.detect_manual_change(), harness.ns["LightControlAttributes"].NONE
                )
                self.assertEqual(harness.events, [])
                self.assertEqual(
                    harness.manager.last_service_data[harness.light], payload
                )

    def test_external_rgb_white_brightness_and_mode_changes_are_detected(self):
        for change in ("rgb", "white", "brightness", "mode"):
            with self.subTest(change=change):
                harness = ProtocolHarness()
                payload = harness.dispatch(1200)
                vector = list(payload["rgbww_color"])
                if change == "rgb":
                    vector[0] = 0
                elif change == "white":
                    vector[3:] = [255, 0]
                harness.report_native(vector, brightness=128 if change == "brightness" else 255)
                if change == "mode":
                    harness.state.attributes.update(
                        color_mode="color_temp", color_temp_kelvin=3500, rgbww_color=None
                    )
                flags = harness.ns["LightControlAttributes"]
                expected = flags.BRIGHTNESS if change == "brightness" else flags.COLOR
                self.assertEqual(harness.detect_manual_change(), expected)
                self.assertEqual(
                    harness.manager.get_adaption_control_attributes(
                        harness.switch, harness.light
                    ),
                    flags.NONE,
                )

    def test_own_pure_white_normalization_uses_acknowledged_temperature(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        payload = harness.dispatch(4000)
        self.assertEqual(payload["rgbww_color"][:3], (0, 0, 0))
        self.assertGreater(payload["rgbww_color"][3], 0)
        self.assertGreater(payload["rgbww_color"][4], 0)
        # The device's own middle-temperature report is a protocol fixture, not
        # an assumed mathematical conversion of the requested white mixture.
        harness.acknowledge_color_temperature(3956)
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].NONE
        )
        harness.state.attributes["color_temp_kelvin"] = 4500
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].COLOR
        )

    def test_old_pure_white_acknowledgement_cannot_describe_a_new_command(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        harness.dispatch(4000)
        harness.acknowledge_color_temperature(3956)
        harness.detect_manual_change()
        self.assertIn(harness.light, harness.manager.last_service_acknowledgements)
        old_context = harness.manager.our_last_state_on_change[harness.light][0].context.id
        harness.dispatch(3500)
        self.assertNotIn(harness.light, harness.manager.last_service_acknowledgements)
        self.assertNotEqual(harness.manager.last_service_context[harness.light], old_context)
        # A new command without its own acknowledgement cannot be compared with
        # the preceding command's report and misclassified as a manual change.
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].NONE
        )
        harness.acknowledge_color_temperature(3200)
        harness.state.attributes["color_temp_kelvin"] = 4500
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].COLOR
        )

    def test_identical_white_resends_preserve_confirmed_normalization(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        harness.dispatch(4000)
        harness.acknowledge_color_temperature(3956)
        original_context = harness.manager.last_service_context[harness.light]
        # A transition change cannot alter a command's target light values and
        # Home Assistant need not emit another event for those same values.
        harness.switch._transition = 5
        for _ in range(3):
            harness.dispatch(4000)
            self.assertNotEqual(
                harness.manager.last_service_context[harness.light], original_context
            )
            self.assertEqual(
                harness.detect_manual_change(), harness.ns["LightControlAttributes"].NONE
            )
        harness.state.attributes["color_temp_kelvin"] = 4500
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].COLOR
        )

    def test_unacknowledged_white_command_still_detects_brightness(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        harness.dispatch(4000)
        harness.state.attributes.update(
            color_mode="color_temp", color_temp_kelvin=3956, brightness=128
        )
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].BRIGHTNESS
        )

    def test_unacknowledged_white_command_still_detects_external_rgb(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        harness.dispatch(4000)
        harness.report_native((255, 0, 0, 0, 0))
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].COLOR
        )

    def test_white_neutral_override_outside_physical_4000k_range(self):
        for warm, cold, override, expected in (
            (5000, 6500, 6500, (255, 0)),
            (2000, 3500, 3500, (255, 0)),
        ):
            with self.subTest(warm=warm, cold=cold, override=override):
                harness = ProtocolHarness()
                harness.switch._white_w_kelvin = warm
                harness.switch._white_cw_kelvin = cold
                harness.switch._cal_white_kelvin_neutral = override
                harness.switch._cal_white_intensity_warm = 100
                payload = harness.dispatch(4000)
                self.assertEqual(payload["rgbww_color"][3:], expected)

    def test_external_report_cannot_replace_context_bound_baseline(self):
        harness = ProtocolHarness()
        harness.switch._cal_rgb_warm = [0, 0, 0]
        harness.switch._cal_rgb_neutral = [0, 0, 0]
        harness.dispatch(4000)
        harness.acknowledge_color_temperature(3956)
        harness.manager.our_last_state_on_change[harness.light].append(
            State({"color_mode": "color_temp", "color_temp_kelvin": 4500, "brightness": 255}, "external")
        )
        harness.state.attributes["color_temp_kelvin"] = 4500
        self.assertEqual(
            harness.detect_manual_change(), harness.ns["LightControlAttributes"].COLOR
        )

    def test_calibration_preview_pauses_adaptation_without_marking_manual(self):
        harness = ProtocolHarness()
        harness.dispatch(1200)
        harness.ns["is_calibration_preview_active"] = lambda hass, light: True
        harness.report_native((0, 0, 255, 255, 0))
        flags = harness.ns["LightControlAttributes"]
        self.assertEqual(harness.detect_manual_change(), flags.NONE)
        self.assertEqual(
            harness.manager.get_adaption_control_attributes(harness.switch, harness.light),
            flags.NONE,
        )
        self.assertEqual(harness.refreshed_lights, [])
        self.assertEqual(harness.events, [])


class RedundantNativeColorTests(unittest.TestCase):
    def test_same_vector_as_list_or_tuple_is_redundant(self):
        namespace = production_namespace()
        for attribute, mode, vector in (
            ("rgbw_color", "rgbw", (120, 80, 40, 128)),
            ("rgbww_color", "rgbww", (120, 80, 40, 60, 68)),
        ):
            for representation in (tuple, list):
                with self.subTest(attribute=attribute, representation=representation):
                    filtered = namespace["_remove_redundant_attributes"](
                        {"entity_id": "light.test", attribute: vector},
                        State({"color_mode": mode, attribute: representation(vector)}),
                    )
                    self.assertEqual(filtered, {"entity_id": "light.test"})

    def test_matching_derived_vector_cannot_hide_a_different_mode(self):
        namespace = production_namespace()
        vector = (120, 80, 40, 60, 68)
        filtered = namespace["_remove_redundant_attributes"](
            {"entity_id": "light.test", "rgbww_color": vector},
            State({"color_mode": "color_temp", "rgbww_color": list(vector)}),
        )
        self.assertEqual(filtered["rgbww_color"], vector)

    def test_identical_native_report_avoids_another_service_call(self):
        harness = ProtocolHarness()
        payload = harness.dispatch(1200)
        harness.report_native(list(payload["rgbww_color"]))
        harness.switch._skip_redundant_commands = True
        self.assertIsNone(harness.dispatch(1200))
        self.assertEqual(len(harness.calls), 1)


if __name__ == "__main__":
    unittest.main()
