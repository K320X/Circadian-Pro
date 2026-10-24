"""Switch solar and sleep calculations reuse Home Assistant's loaded timezone."""

from copy import deepcopy
import datetime as dt
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from test_audit_lighting import FixedAstral, load_module
from test_calibration_flow import production_namespace
from test_color_temperature_bounds import load_functions


class SwitchTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/London")
        self.instant = dt.datetime(2026, 7, 1, 22, 30, tzinfo=dt.UTC)
        self.clock = SimpleNamespace(
            # Mirrors HA's public datetime helpers: the timezone is already loaded.
            get_default_time_zone=Mock(side_effect=lambda: self.zone),
            now=Mock(side_effect=lambda zone=None: self.instant.astimezone(zone or self.zone)),
        )
        self.ns = production_namespace()
        self.color = load_module("color_and_brightness.py")
        self.ns.update(
            datetime=dt,
            dt_util=self.clock,
            zoneinfo=SimpleNamespace(ZoneInfo=Mock(side_effect=AssertionError(
                "Switch callbacks must not load timezone files"
            ))),
            get_astral_location=lambda hass: (FixedAstral(), 0),
            SunLightSettings=self.color.SunLightSettings,
            validate=lambda *, config_entry, service_data, defaults: deepcopy(service_data),
        )
        load_functions(self.ns, "const.py", {"TakeOverControlMode"})
        load_functions(self.ns, "switch.py", {
            "_set_changeable_settings", "_within_sleep_window", "_effective_sleep_state",
        }, "CircadianProSwitch")
        self.switch = SimpleNamespace(
            hass=SimpleNamespace(config=SimpleNamespace(time_zone=self.zone.key)),
            _name="Bedroom", lights=[], _expand_light_groups=lambda **kwargs: None,
            sleep_mode_switch=SimpleNamespace(is_on=False),
        )
        self.switch._within_sleep_window = MethodType(self.ns["_within_sleep_window"], self.switch)
        self.settings = {
            key: deepcopy(value) for key, value, _ in self.ns["VALIDATION_TUPLES"]
        }
        self.settings.update(
            take_over_control_mode="pause_all", sunrise_time=dt.time(7),
            sunset_time=dt.time(19), sunrise_offset=dt.timedelta(),
            sunset_offset=dt.timedelta(), sleep_activation_mode="schedule",
            sleep_schedule_start=dt.time(23), sleep_schedule_end=dt.time(7),
        )

    def configure(self):
        self.ns["_set_changeable_settings"](self.switch, self.settings)

    def effective_sleep(self, instant):
        self.instant = instant
        return self.ns["_effective_sleep_state"](self.switch)

    def test_solar_configuration_preserves_fixed_local_hours_across_dst(self):
        self.configure()
        sun = self.switch._sun_light_settings.sun
        self.assertIs(sun.timezone, self.zone)
        for day, utc_hour in ((dt.date(2026, 3, 28), 7), (dt.date(2026, 3, 29), 6),
                              (dt.date(2026, 10, 24), 6), (dt.date(2026, 10, 25), 7)):
            with self.subTest(day=day):
                self.assertEqual(sun.sunrise(day), dt.datetime.combine(day, dt.time(utc_hour), dt.UTC))

    def test_reconfiguration_reuses_current_home_assistant_timezone(self):
        self.configure()
        original = self.switch._sun_light_settings.sun
        self.zone = ZoneInfo("America/New_York")
        self.switch.hass.config.time_zone = self.zone.key
        self.configure()
        current = self.switch._sun_light_settings.sun
        day = dt.date(2026, 7, 1)
        self.assertEqual(original.sunrise(day), dt.datetime(2026, 7, 1, 6, tzinfo=dt.UTC))
        self.assertIs(current.timezone, self.zone)
        self.assertEqual(current.sunrise(day), dt.datetime(2026, 7, 1, 11, tzinfo=dt.UTC))

    def test_cross_midnight_sleep_window_uses_local_time_and_exact_boundaries(self):
        self.configure()
        for instant, expected in (
            (dt.datetime(2026, 7, 1, 21, 59, tzinfo=dt.UTC), False),
            (dt.datetime(2026, 7, 1, 22, tzinfo=dt.UTC), True),
            (dt.datetime(2026, 7, 2, 5, 59, tzinfo=dt.UTC), True),
            (dt.datetime(2026, 7, 2, 6, tzinfo=dt.UTC), False),
        ):
            with self.subTest(instant=instant):
                self.assertEqual(self.effective_sleep(instant), expected)

    def test_daytime_sleep_window_keeps_local_boundaries(self):
        self.settings.update(sleep_schedule_start=dt.time(13), sleep_schedule_end=dt.time(15))
        self.configure()
        for utc_hour, expected in ((11, False), (12, True), (13, True), (14, False)):
            with self.subTest(utc_hour=utc_hour):
                self.assertEqual(self.effective_sleep(dt.datetime(2026, 7, 1, utc_hour, tzinfo=dt.UTC)), expected)

    def test_sleep_window_covers_both_occurrences_of_repeated_dst_hour(self):
        self.settings.update(sleep_schedule_start=dt.time(1), sleep_schedule_end=dt.time(2))
        self.configure()
        for utc_hour, expected in ((0, True), (1, True), (2, False)):
            with self.subTest(utc_hour=utc_hour):
                self.assertEqual(self.effective_sleep(dt.datetime(2026, 10, 25, utc_hour, 30, tzinfo=dt.UTC)), expected)

    def test_sun_sleep_mode_passes_current_local_datetime_without_timezone_loading(self):
        self.settings["sleep_activation_mode"] = "sun"
        self.configure()
        sun_position = Mock(return_value=-0.5)
        self.switch._sun_light_settings = SimpleNamespace(sun=SimpleNamespace(sun_position=sun_position))
        self.assertTrue(self.effective_sleep(self.instant))
        sample = sun_position.call_args.args[0]
        self.assertIs(sample.tzinfo, self.zone)
        self.assertEqual((sample.hour, sample.minute), (23, 30))
        self.zone = ZoneInfo("America/New_York")
        self.switch.hass.config.time_zone = self.zone.key
        sun_position.return_value = 0.5
        self.assertFalse(self.effective_sleep(self.instant))
        self.assertIs(sun_position.call_args.args[0].tzinfo, self.zone)
        self.assertEqual(sun_position.call_args.args[0].hour, 18)


if __name__ == "__main__":
    unittest.main()
