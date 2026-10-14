"""Timed brightness/temperature curves, including instantaneous target changes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
import unittest

import test_audit_lighting as lighting_fixtures


class SharedTemperatureCurveTests(unittest.TestCase):
    def setUp(self):
        self.color = lighting_fixtures.load_module("color_and_brightness.py")
        self.day = datetime(2026, 10, 14, tzinfo=UTC)

    def tearDown(self):
        sys.modules.pop(self.color.__name__, None)

    def settings(self, **overrides):
        values = dict(
            name="Shared curve", astral_location=lighting_fixtures.FixedAstral(),
            adapt_until_sleep=False, max_brightness=100, min_brightness=10,
            max_color_temp=4000, min_color_temp=1200, sleep_brightness=3,
            sleep_rgb_or_color_temp="color_temp", sleep_color_temp=1000,
            sleep_rgb_color=(255, 56, 0), sunrise_time=None, min_sunrise_time=None,
            max_sunrise_time=None, sunset_time=None, min_sunset_time=None,
            max_sunset_time=None, brightness_mode="linear",
            brightness_mode_time_dark=timedelta(minutes=166),
            brightness_mode_time_light=timedelta(minutes=1),
            apply_temperature_curve=True,
        )
        values.update(overrides)
        return self.color.SunLightSettings(**values)

    def sample(self, settings, hour, seconds=0, sleeping=False):
        return settings.brightness_and_color(self.day + timedelta(hours=hour, seconds=seconds), sleeping)

    def assert_progress(self, result, progress, min_kelvin=1200, max_kelvin=4000):
        self.assertAlmostEqual(result["brightness_pct"], 10 + 90 * progress)
        expected = 5 * round((min_kelvin + (max_kelvin - min_kelvin) * progress) / 5)
        self.assertEqual(result["color_temp_kelvin"], max(min_kelvin, min(max_kelvin, expected)))
        self.assertFalse(result["force_rgb_color"])

    def test_zero_windows_step_at_both_events_and_stay_at_day_night_endpoints(self):
        for mode in ("linear", "tanh"):
            for sync in (False, True):
                settings = self.settings(
                    brightness_mode=mode, apply_temperature_curve=sync,
                    brightness_mode_time_dark=timedelta(), brightness_mode_time_light=timedelta(),
                )
                cases = ((0, 0, 0), (6, -0.001, 0), (6, 0, 1), (6, 0.001, 1),
                         (12, 0, 1), (18, -0.001, 1), (18, 0, 0), (18, 0.001, 0), (23, 0, 0))
                for hour, seconds, progress in cases:
                    with self.subTest(mode=mode, sync=sync, hour=hour, seconds=seconds):
                        result = self.sample(settings, hour, seconds)
                        self.assertEqual(result["brightness_pct"], 100 if progress else 10)
                        if sync:
                            self.assertEqual(result["color_temp_kelvin"], 4000 if progress else 1200)
                        else:
                            self.assertEqual(result["color_temp_kelvin"], settings.color_temp_kelvin(result["sun_position"]))
                        moment = self.day + timedelta(hours=hour, seconds=seconds)
                        self.assertEqual(settings.brightness_pct(moment, False), result["brightness_pct"])

    def test_linear_166_plus_one_minutes_reaches_the_configured_limits_and_midpoints(self):
        settings = self.settings()
        for hour, seconds, progress in (
            (6, -9960, 0), (6, -4950, 0.5), (6, 60, 1),
            (18, -60, 1), (18, 4950, 0.5), (18, 9960, 0),
        ):
            with self.subTest(hour=hour, seconds=seconds):
                self.assert_progress(self.sample(settings, hour, seconds), progress)

    def test_sigmoid_166_plus_one_minutes_keeps_five_ninetyfive_percent_reference_points(self):
        settings = self.settings(brightness_mode="tanh")
        for hour, seconds, progress in (
            (6, -9960, 0.05), (6, -4950, 0.5), (6, 60, 0.95),
            (18, -60, 0.95), (18, 4950, 0.5), (18, 9960, 0.05),
        ):
            with self.subTest(hour=hour, seconds=seconds):
                self.assert_progress(self.sample(settings, hour, seconds), progress)

    def test_one_zero_window_keeps_each_normal_curve_finite_and_correct(self):
        for mode, low, high in (("linear", 0, 1), ("tanh", 0.05, 0.95)):
            for dark, light in ((0, 120), (120, 0)):
                settings = self.settings(
                    brightness_mode=mode,
                    brightness_mode_time_dark=timedelta(minutes=dark),
                    brightness_mode_time_light=timedelta(minutes=light),
                )
                for hour, minutes, progress in (
                    (6, -dark, low), (6, light, high),
                    (18, -light, high), (18, dark, low),
                ):
                    with self.subTest(mode=mode, dark=dark, light=light, hour=hour, minutes=minutes):
                        self.assert_progress(self.sample(settings, hour, minutes * 60), progress)

    def test_constant_brightness_does_not_prevent_temperature_progression(self):
        for mode in ("linear", "tanh"):
            settings = self.settings(brightness_mode=mode, min_brightness=100, max_brightness=100)
            samples = [self.sample(settings, 6, seconds) for seconds in (-9960, -4950, 60)]
            self.assertEqual([sample["brightness_pct"] for sample in samples], [100, 100, 100])
            self.assertLess(samples[0]["color_temp_kelvin"], samples[1]["color_temp_kelvin"])
            self.assertLess(samples[1]["color_temp_kelvin"], samples[2]["color_temp_kelvin"])

    def test_constant_kelvin_does_not_prevent_brightness_progression(self):
        settings = self.settings(min_color_temp=3001, max_color_temp=3001)
        samples = [self.sample(settings, 6, seconds) for seconds in (-9960, -4950, 60)]
        self.assertEqual([sample["color_temp_kelvin"] for sample in samples], [3001, 3001, 3001])
        self.assertEqual([sample["brightness_pct"] for sample in samples], [10, 55, 100])

    def test_sync_kelvin_preserves_exact_endpoints_and_never_rounds_outside_them(self):
        for minimum, maximum in ((1201, 4003), (1201, 1202)):
            for mode in ("linear", "tanh"):
                settings = self.settings(brightness_mode=mode, min_color_temp=minimum, max_color_temp=maximum)
                for minute in range(0, 1440, 7):
                    result = self.sample(settings, 0, minute * 60)
                    self.assertGreaterEqual(result["color_temp_kelvin"], minimum)
                    self.assertLessEqual(result["color_temp_kelvin"], maximum)
                if mode == "linear":
                    self.assertEqual(self.sample(settings, 6, -9960)["color_temp_kelvin"], minimum)
                    self.assertEqual(self.sample(settings, 6, 60)["color_temp_kelvin"], maximum)

    def test_natural_ignores_sync_including_the_independent_night_rgb_blend(self):
        for until_sleep in (False, True):
            for sleep_color in ("color_temp", "rgb_color"):
                values = dict(brightness_mode="default", adapt_until_sleep=until_sleep, sleep_rgb_or_color_temp=sleep_color)
                independent = self.settings(**values, apply_temperature_curve=False)
                enabled = self.settings(**values, apply_temperature_curve=True)
                for hour in (0, 4, 6, 9, 12, 15, 18, 21):
                    with self.subTest(until_sleep=until_sleep, sleep_color=sleep_color, hour=hour):
                        self.assertEqual(self.sample(enabled, hour), self.sample(independent, hour))

    def test_optout_keeps_independent_kelvin_and_rgb_night_blend_for_each_timed_mode(self):
        for mode in ("linear", "tanh"):
            settings = self.settings(brightness_mode=mode, apply_temperature_curve=False,
                                     adapt_until_sleep=True, sleep_rgb_or_color_temp="rgb_color")
            night = self.sample(settings, 0)
            self.assertEqual(night["color_temp_kelvin"], 1000)
            self.assertEqual(night["rgb_color"], (255, 56, 0))
            self.assertTrue(night["force_rgb_color"])
            self.assertEqual(self.sample(settings, 6)["color_temp_kelvin"], 1200)
            self.assertEqual(self.sample(settings, 9)["color_temp_kelvin"], 3300)

    def test_sync_replaces_night_blend_but_explicit_sleep_always_wins(self):
        for mode in ("linear", "tanh"):
            for sleep_color in ("color_temp", "rgb_color"):
                settings = self.settings(brightness_mode=mode, adapt_until_sleep=True,
                                         sleep_rgb_or_color_temp=sleep_color)
                awake = self.sample(settings, 0)
                self.assertEqual(awake["color_temp_kelvin"], 1200)
                self.assertEqual(awake["rgb_color"], (255, 128, 64))
                self.assertFalse(awake["force_rgb_color"])
                for hour in (0, 6, 12, 18):
                    asleep = self.sample(settings, hour, sleeping=True)
                    self.assertEqual(asleep["brightness_pct"], 3)
                    self.assertEqual(asleep["color_temp_kelvin"], 1000)
                    self.assertEqual(asleep["rgb_color"], (255, 56, 0))

    def test_sync_reuses_one_solar_sample_for_both_curves(self):
        settings = self.settings()
        self.sample(settings, 9)
        self.assertEqual(len(settings.astral_location.calls), 12)

    def test_step_uses_adjusted_solar_events_without_transition_lookahead(self):
        for mode in ("linear", "tanh"):
            settings = self.settings(
                brightness_mode=mode, sunrise_offset=timedelta(minutes=30),
                sunset_offset=timedelta(minutes=60), brightness_mode_time_dark=timedelta(),
                brightness_mode_time_light=timedelta(),
            )
            for hour, seconds, progress in ((6, 1799, 0), (6, 1800, 1), (19, -1, 1), (19, 0, 0)):
                self.assert_progress(self.sample(settings, hour, seconds), progress)
            for hour, seconds, expected in ((6, 1799, 1200), (6, 1800, 4000), (6, 1801, 4000),
                                           (19, -1, 4000), (19, 0, 1200), (19, 1, 1200)):
                moment = self.day + timedelta(hours=hour, seconds=seconds)
                self.color.utcnow = lambda moment=moment: moment
                for transition in (0, 5, 60):
                    with self.subTest(mode=mode, hour=hour, seconds=seconds, transition=transition):
                        self.assertEqual(settings.get_settings(False, transition)["color_temp_kelvin"], expected)

    def test_positive_windows_natural_and_sleep_keep_transition_lookahead(self):
        moment = self.day + timedelta(hours=5, minutes=59, seconds=55)
        self.color.utcnow = lambda: moment
        for mode, dark, light, sleeping in (("linear", 0, 1, False), ("tanh", 1, 0, False),
                                           ("default", 0, 0, False), ("linear", 0, 0, True)):
            settings = self.settings(brightness_mode=mode,
                                     brightness_mode_time_dark=timedelta(minutes=dark),
                                     brightness_mode_time_light=timedelta(minutes=light))
            self.assertEqual(settings.get_settings(sleeping, 60),
                             settings.brightness_and_color(moment + timedelta(seconds=60), sleeping))


if __name__ == "__main__":
    unittest.main()
