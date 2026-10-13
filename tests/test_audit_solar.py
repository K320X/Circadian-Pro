"""Solar regressions with HA's actual Astral 2.2 dependency.

The production module runs unchanged except for HA color-conversion imports.
Real Tromso sunrise/sunset failures exercise both polar conditions; unexpected
errors remain visible and fixed schedules continue to override astronomy.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import sys
import unittest

from astral import LocationInfo
from astral.location import Location

import test_audit_lighting as lighting_fixtures


class SolarAuditTests(unittest.TestCase):
    def setUp(self):
        self.color = lighting_fixtures.load_module("color_and_brightness.py")
        self.tromso = Location(LocationInfo("Tromso", "Norway", "Europe/Oslo", 69.6492, 18.9553))

    def tearDown(self):
        sys.modules.pop(self.color.__name__, None)

    def settings(self, **overrides):
        values = dict(
            name="Polar regression", astral_location=self.tromso, adapt_until_sleep=False,
            max_brightness=91, max_color_temp=6503, min_brightness=7,
            min_color_temp=2001, sleep_brightness=3, sleep_rgb_or_color_temp="color_temp",
            sleep_color_temp=1100, sleep_rgb_color=(255, 56, 0), sunrise_time=None,
            min_sunrise_time=None, max_sunrise_time=None, sunset_time=None,
            min_sunset_time=None, max_sunset_time=None,
            brightness_mode_time_dark=timedelta(hours=1),
            brightness_mode_time_light=timedelta(hours=1),
        )
        values.update(overrides)
        return self.color.SunLightSettings(**values)

    def test_real_polar_sunrise_and_sunset_failures_use_exact_bounded_endpoints_in_every_mode(self):
        for month, expected_position, brightness, kelvin in ((6, 1, 91, 6503), (12, -1, 7, 2001)):
            for hour in (0, 12):
                sample = datetime(2026, month, 21, hour, tzinfo=UTC)
                for event in (self.tromso.sunrise, self.tromso.sunset):
                    with self.assertRaisesRegex(ValueError, "Sun is always (above|below) the horizon"):
                        event(sample, local=False)
                self.assertEqual(self.tromso.solar_elevation(sample) > 0, expected_position > 0)
                for mode in ("default", "linear", "tanh"):
                    for adapt_until_sleep in (False, True):
                        with self.subTest(month=month, hour=hour, mode=mode, until_sleep=adapt_until_sleep):
                            settings = self.settings(brightness_mode=mode, adapt_until_sleep=adapt_until_sleep)
                            result = settings.brightness_and_color(sample, False)
                            self.assertEqual(result["sun_position"], expected_position)
                            self.assertEqual(result["brightness_pct"], brightness)
                            self.assertEqual(result["color_temp_kelvin"], kelvin)
                            self.assertLessEqual(settings.min_brightness, result["brightness_pct"])
                            self.assertLessEqual(result["brightness_pct"], settings.max_brightness)
                            self.assertLessEqual(settings.min_color_temp, result["color_temp_kelvin"])
                            self.assertLessEqual(result["color_temp_kelvin"], settings.max_color_temp)
                            self.assertFalse(result["force_rgb_color"])

    def test_manual_sleep_targets_work_during_real_polar_day_and_night(self):
        for month in (6, 12):
            for mode in ("default", "linear", "tanh"):
                for sleep_color in ("color_temp", "rgb_color"):
                    with self.subTest(month=month, mode=mode, sleep_color=sleep_color):
                        settings = self.settings(brightness_mode=mode, sleep_rgb_or_color_temp=sleep_color)
                        result = settings.brightness_and_color(datetime(2026, month, 21, 12, tzinfo=UTC), True)
                        self.assertEqual(result["brightness_pct"], 3)
                        self.assertEqual(result["color_temp_kelvin"], 1100)
                        self.assertEqual(result["rgb_color"], (255, 56, 0))

    def test_polar_position_is_available_to_the_automatic_sleep_decision(self):
        settings = self.settings(adapt_until_sleep=True)
        self.assertEqual(settings.sun.sun_position(datetime(2026, 6, 21, 12, tzinfo=UTC)), 1)
        self.assertEqual(settings.sun.sun_position(datetime(2026, 12, 21, 12, tzinfo=UTC)), -1)

    def test_public_get_settings_keeps_transition_sampling_during_polar_days(self):
        sample = datetime(2026, 6, 21, 12, tzinfo=UTC)
        self.color.utcnow = lambda: sample
        settings = self.settings(brightness_mode="linear")
        self.assertEqual(settings.get_settings(False, 5), settings.brightness_and_color(sample + timedelta(seconds=5), False))
        self.assertEqual(settings.get_settings(True, 5)["brightness_pct"], 3)

    def test_fixed_schedule_overrides_polar_astronomy_and_keeps_the_normal_curve(self):
        settings = self.settings(sunrise_time=time(6), sunset_time=time(18), max_color_temp=6500)
        result = settings.brightness_and_color(datetime(2026, 6, 21, 9, tzinfo=UTC), False)
        self.assertAlmostEqual(result["sun_position"], 0.75)
        self.assertEqual(result["color_temp_kelvin"], 5375)
        self.assertEqual(result["brightness_pct"], 91)

    def test_one_absent_adjacent_day_does_not_replace_a_valid_bracket_with_a_polar_endpoint(self):
        sample = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)
        absent_day = (sample - timedelta(days=1)).date()
        real = self.tromso

        class AdjacentDayMissing:
            def __getattr__(self, name):
                return getattr(real, name)

            def sunrise(self, day, local=False):
                if day.date() == absent_day:
                    raise ValueError("Sun is always below the horizon on this day, at this location.")
                return real.sunrise(day, local=local)

        expected = self.settings().brightness_and_color(sample, False)
        result = self.settings(astral_location=AdjacentDayMissing()).brightness_and_color(sample, False)
        self.assertEqual(result, expected)
        self.assertNotEqual(result["sun_position"], 1)

    def test_unexpected_astral_and_elevation_failures_are_never_hidden(self):
        for fail_at_elevation in (False, True):
            failure = ValueError("unexpected astronomy error")

            class BrokenAstral:
                def sunrise(self, _day, local=False):
                    if fail_at_elevation:
                        raise ValueError("Sun is always below the horizon on this day, at this location.")
                    raise failure

                def solar_elevation(self, _sample):
                    raise failure

            for sleeping in (False, True):
                with self.subTest(elevation=fail_at_elevation, sleeping=sleeping):
                    settings = self.settings(astral_location=BrokenAstral())
                    with self.assertRaises(ValueError) as caught:
                        settings.brightness_and_color(datetime(2026, 12, 21, 12, tzinfo=UTC), sleeping)
                    self.assertIs(caught.exception, failure)

    def test_invalid_fixed_event_order_remains_an_error_instead_of_entering_polar_fallback(self):
        settings = self.settings(sunrise_time=time(8), sunset_time=time(8))
        with self.assertLogs(self.color.__name__, level="ERROR"), self.assertRaisesRegex(ValueError, "not in the expected order"):
            settings.brightness_and_color(datetime(2026, 6, 21, 12, tzinfo=UTC), False)


if __name__ == "__main__":
    unittest.main()
