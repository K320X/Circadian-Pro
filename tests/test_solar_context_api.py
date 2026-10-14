"""Real solar context for the panel, with no lights or profile writes."""

from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.location import Location

from test_audit_lighting import FixedAstral, load_module
import test_calibration_api as api_fixtures


class SolarContextApiTests(unittest.TestCase):
    # Reuse the admin WebSocket fixture without inheriting its unrelated tests.
    invoke = api_fixtures.ApiTests.invoke

    def setUp(self):
        api_fixtures.ApiTests.setUp(self)
        self.color = load_module("color_and_brightness.py")
        sys.modules["calibration_api_tests_package.color_and_brightness"] = self.color
        self.hass.config = SimpleNamespace(time_zone="Europe/London")
        self.sun = self.color.SunEvents(
            name="Desk", astral_location=FixedAstral(), sunrise_time=None,
            min_sunrise_time=None, max_sunrise_time=None, sunset_time=None,
            min_sunset_time=None, max_sunset_time=None,
            timezone=ZoneInfo(self.hass.config.time_zone),
        )
        self.hass.data["circadian_pro"]["loaded"]["switch"] = SimpleNamespace(
            _sun_light_settings=SimpleNamespace(sun=self.sun),
        )
        for name in self.api._SOLAR_TIME_OPTIONS:
            self.entry.options[name] = None
            self.const.EXTRA_VALIDATION[name] = (
                lambda value: value if isinstance(value, time) else time.fromisoformat(value), str,
            )
        self.const.VALIDATION_TUPLES = [(name, "None", None) for name in self.api._SOLAR_TIME_OPTIONS]
        # The production domain schema emits serialized seconds/time strings;
        # the endpoint rehydrates them using these same validators.
        for name in self.api._SOLAR_OFFSET_OPTIONS:
            self.entry.options[name] = timedelta()
            self.const.EXTRA_VALIDATION[name] = (
                lambda value: value if isinstance(value, timedelta) else timedelta(seconds=value),
                lambda value: value.total_seconds() if isinstance(value, timedelta) else value,
            )
        self.entry.options.update(sleep_activation_mode="disabled", sleep_schedule_start="23:00:00",
                                  sleep_schedule_end="05:00:00")
        for name in self.api._SLEEP_TIME_OPTIONS:
            self.const.EXTRA_VALIDATION[name] = (
                lambda value: value if isinstance(value, time) else time.fromisoformat(value), str,
            )

    def tearDown(self):
        sys.modules.pop(self.color.__name__, None)
        api_fixtures.ApiTests.tearDown(self)

    def solar(self, *, now=datetime(2026, 10, 14, 12, tzinfo=UTC), settings=None, entry_id="loaded"):
        with patch.object(self.api, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            self.invoke(self.api.websocket_solar, entry_id=entry_id, settings=settings or {})
        return self.results[-1][1] if self.results else None

    def test_uses_ha_local_day_and_timezone_instead_of_utc_date(self):
        result = self.solar(now=datetime(2026, 10, 14, 23, 30, tzinfo=UTC))
        self.assertEqual(result["timezone"], "Europe/London")
        self.assertEqual(result["local_date"], "2026-10-15")
        self.assertEqual(result["day_start"], datetime(2026, 10, 14, 23, tzinfo=UTC).timestamp())
        self.assertEqual(result["sunrise"]["local_time"], "07:00")
        self.assertEqual(result["sunset"]["local_time"], "19:00")
        self.assertEqual(result["refresh_at"], result["day_end"])
        self.assertEqual(result["refresh_after"], 23.5 * 3600)
        self.assertEqual(result["generated_at"], datetime(2026, 10, 14, 23, 30, tzinfo=UTC).timestamp())

    def test_dst_local_days_have_23_or_25_elapsed_hours(self):
        for day, hours in ((datetime(2026, 3, 29, 12, tzinfo=UTC), 23),
                           (datetime(2026, 10, 25, 12, tzinfo=UTC), 25)):
            with self.subTest(day=day):
                result = self.solar(now=day)
                self.assertEqual((result["day_end"] - result["day_start"]) / 3600, hours)
                self.assertEqual(result["local_date"], day.date().isoformat())

    def test_timezone_extremes_use_correct_local_dates_with_real_astral_events(self):
        for timezone_name, latitude, longitude, expected_date in (
            ("Pacific/Kiritimati", 1.8721, -157.4278, "2026-10-15"),
            ("Etc/GMT+12", 0.0, -180.0, "2026-10-14"),
        ):
            with self.subTest(timezone=timezone_name):
                self.hass.config.time_zone = timezone_name
                location = Location(LocationInfo("Location", "", timezone_name, latitude, longitude))
                self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun = self.api.replace(
                    self.sun, astral_location=location,
                )
                result = self.solar(now=datetime(2026, 10, 14, 12, tzinfo=UTC))
                self.assertEqual(result["local_date"], expected_date)
                self.assertEqual(result["timezone"], timezone_name)
                self.assertEqual((result["day_end"] - result["day_start"]) / 3600, 24)
                self.assertLessEqual(result["events"][0]["timestamp"], result["day_start"])
                self.assertGreater(result["events"][-1]["timestamp"], result["day_end"])
                for event in ("sunrise", "sunset"):
                    value = result[event]
                    self.assertGreaterEqual(value["timestamp"], result["day_start"])
                    self.assertLess(value["timestamp"], result["day_end"])
                    self.assertIn({"event": event, "timestamp": value["timestamp"]}, result["events"])
                    local_event = datetime.fromtimestamp(value["timestamp"], ZoneInfo(timezone_name))
                    self.assertEqual(local_event.date().isoformat(), result["local_date"])
                    self.assertEqual(local_event.strftime("%H:%M"), value["local_time"])

    def test_real_astral_events_include_noon_and_midnight_and_bracket_the_day(self):
        location = Location(LocationInfo("Test location", "", "Europe/London", 51.0, 0.0))
        self.sun = self.api.replace(self.sun, astral_location=location)
        self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun = self.sun
        result = self.solar()
        expected = sorted([
            {"event": event.value, "timestamp": timestamp}
            for days in range(-2, 3)
            for event, timestamp in self.sun.sun_events(datetime(2026, 10, 14).date() + timedelta(days=days))
        ], key=lambda item: item["timestamp"])
        first = max(index for index, event in enumerate(expected) if event["timestamp"] <= result["day_start"] - 7200)
        last = next(index for index, event in enumerate(expected) if event["timestamp"] > result["day_end"] + 7200)
        self.assertEqual(result["events"], expected[first:last + 1])
        self.assertLess(expected[0]["timestamp"], result["day_start"])
        self.assertGreater(expected[-1]["timestamp"], result["day_end"])
        self.assertEqual(result["sunrise"]["timestamp"], self.sun.sunrise(datetime(2026, 10, 14).date()).timestamp())
        self.assertEqual(result["sunset"]["timestamp"], self.sun.sunset(datetime(2026, 10, 14).date()).timestamp())

    def test_draft_fixed_times_offsets_and_bounds_match_runtime_without_saving(self):
        original = deepcopy(self.entry.options)
        draft = {"sunrise_time": "07:30:00", "sunset_time": "18:00:00",
                 "sunrise_offset_minutes": -60, "sunset_offset_minutes": 60,
                 "min_sunrise_time": "07:00:00", "max_sunset_time": "18:45:00"}
        result = self.solar(settings=draft)
        self.assertEqual(result["sunrise"]["local_time"], "07:00")
        self.assertEqual(result["sunset"]["local_time"], "18:45")
        self.assertEqual(self.entry.options, original)
        self.assertIs(self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun, self.sun)
        self.assertEqual(self.sun.sunrise_offset, timedelta())
        self.assertEqual(self.calibration.calls, [])
        self.assertEqual(self.calibration.stops, [])
        self.assertEqual(draft["sunset_offset_minutes"], 60)

    def test_sunset_minute_offset_is_applied_once_to_astronomical_events(self):
        original = deepcopy(self.entry.options)
        result = self.solar(settings={"sunset_offset_minutes": 60})
        self.assertEqual(result["sunset"]["local_time"], "20:00")
        self.assertEqual(result["sunrise"]["local_time"], "07:00")
        self.assertEqual(self.entry.options, original)

    def test_unedited_legacy_offset_seconds_are_not_rounded(self):
        self.entry.options["sunset_offset"] = timedelta(seconds=61)
        original = deepcopy(self.entry.options)
        result = self.solar(settings={"sunset_offset_minutes": 1})
        expected = datetime(2026, 10, 14, 18, 1, 1, tzinfo=UTC).timestamp()
        self.assertEqual(result["sunset"]["timestamp"], expected)
        self.assertEqual(self.entry.options, original)

    def test_unknown_or_non_solar_fields_are_rejected_without_executor_work(self):
        for draft in ({"lights": []}, {"max_brightness": 20}, {"unknown": 1}, {"preview_light": True}):
            self.solar(settings=draft)
            self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.executor_calls, [])
        self.assertEqual(self.calibration.calls, [])

    def test_invalid_draft_times_and_offset_minutes_are_rejected(self):
        for draft in ({"sunrise_time": "garbage"}, {"sunrise_offset_minutes": 1.5},
                      {"sunset_offset_minutes": float("inf")}, {"sunset_offset_minutes": True},
                      {"min_sunrise_time": "09:00", "max_sunrise_time": "08:00"}):
            with self.subTest(draft=draft):
                self.solar(settings=draft)
                self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.executor_calls, [])

    def test_admin_is_required_even_for_read_only_solar_context(self):
        self.connection.user.is_admin = False
        with self.assertRaises(PermissionError):
            self.solar()
        self.assertEqual(self.executor_calls, [])

    def test_missing_foreign_unloaded_or_switchless_profiles_cannot_be_read(self):
        self.entries["foreign"] = SimpleNamespace(domain="other", state=api_fixtures.EntryState.LOADED)
        for entry_id in ("missing", "foreign"):
            self.solar(entry_id=entry_id)
            self.assertEqual(self.errors[-1][1], "not_loaded")
        self.entry.state = api_fixtures.EntryState.NOT_LOADED
        self.solar()
        self.assertEqual(self.errors[-1][1], "not_loaded")
        self.entry.state = api_fixtures.EntryState.LOADED
        self.hass.data["circadian_pro"]["loaded"] = {}
        self.solar()
        self.assertEqual(self.errors[-1][1], "not_loaded")
        self.assertEqual(self.executor_calls, [])

    def test_astronomy_runs_in_executor_and_has_bounded_date_queries(self):
        result = self.solar()
        self.assertEqual(len(self.executor_calls), 1)
        self.assertIsNot(self.executor_calls[0][0], self.sun)
        self.assertGreaterEqual(len(result["events"]), 5)
        self.assertLessEqual(len(self.sun.astral_location.calls), 22)

    def test_polar_days_return_explicit_unavailable_instead_of_invented_times(self):
        location = Location(LocationInfo("Tromso", "NO", "Europe/Oslo", 69.6492, 18.9553))
        self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun = self.api.replace(
            self.sun, astral_location=location,
        )
        for month in (6, 12):
            self.solar(now=datetime(2026, month, 21, 12, tzinfo=UTC))
            self.assertEqual(self.errors[-1][1], "solar_unavailable")
        self.assertEqual(self.results, [])
        self.assertEqual(self.calibration.calls, [])

    def test_unavailable_unused_neighbor_does_not_hide_a_valid_local_day(self):
        class AbsentNeighbor(FixedAstral):
            def sunrise(self, day, local=False):
                if day == datetime(2026, 10, 12).date():
                    raise ValueError("Sun is always above the horizon on this day, at this location.")
                return super().sunrise(day, local=local)

        self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun = self.api.replace(
            self.sun, astral_location=AbsentNeighbor(),
        )
        result = self.solar()
        self.assertEqual(result["local_date"], "2026-10-14")
        self.assertEqual(self.errors, [])

    def test_date_overflow_is_reported_without_crashing_or_changing_options(self):
        original = deepcopy(self.entry.options)
        self.solar(settings={"sunrise_offset_minutes": 20000000000})
        self.assertEqual(self.errors[-1][1], "solar_unavailable")
        self.assertEqual(self.entry.options, original)

    def test_invalid_event_order_returns_unavailable_without_a_fallback_schedule(self):
        self.solar(settings={"sunrise_offset_minutes": 10 * 60})
        self.assertEqual(self.errors[-1][1], "solar_unavailable")
        self.assertEqual(self.results, [])

    def test_disabled_sleep_keeps_the_normal_graph_and_adds_only_read_only_metadata(self):
        original = deepcopy(self.entry.options)
        result = self.solar()
        self.assertEqual(result["sleep_mode"], "disabled")
        self.assertEqual(result["sleep_windows"], [])
        self.assertFalse(result["sleep_all_day"])
        self.assertEqual(self.entry.options, original)
        self.assertEqual(self.calibration.calls, [])
        self.assertEqual(self.calibration.stops, [])

    def test_solar_sleep_windows_follow_effective_offsets_and_include_previous_night(self):
        result = self.solar(settings={"sleep_activation_mode": "sun", "sunset_offset_minutes": 60})
        self.assertEqual(result["sleep_mode"], "sun")
        self.assertFalse(result["sleep_all_day"])
        previous = next(window for window in result["sleep_windows"]
                        if window["start"] < result["day_start"] < window["end"])
        self.assertEqual(previous["start"], datetime(2026, 10, 13, 19, tzinfo=UTC).timestamp())
        self.assertEqual(previous["end"], result["sunrise"]["timestamp"])
        tonight = next(window for window in result["sleep_windows"]
                       if window["start"] == result["sunset"]["timestamp"])
        self.assertEqual(tonight["end"], datetime(2026, 10, 15, 6, tzinfo=UTC).timestamp())

    def test_unsaved_cross_midnight_schedule_does_not_change_profile_or_switch(self):
        original = deepcopy(self.entry.options)
        result = self.solar(settings={"sleep_activation_mode": "schedule",
                                     "sleep_schedule_start": "23:30:00", "sleep_schedule_end": "07:15:00"})
        self.assertEqual(result["sleep_mode"], "schedule")
        relevant = [window for window in result["sleep_windows"]
                    if window["start"] < result["day_end"] and window["end"] > result["day_start"]]
        self.assertEqual(relevant, [
            {"start": datetime(2026, 10, 13, 22, 30, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 14, 6, 15, tzinfo=UTC).timestamp()},
            {"start": datetime(2026, 10, 14, 22, 30, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 15, 6, 15, tzinfo=UTC).timestamp()},
        ])
        self.assertEqual(self.entry.options, original)
        self.assertEqual(self.calibration.calls, [])
        self.assertEqual(self.calibration.stops, [])
        self.assertIs(self.hass.data["circadian_pro"]["loaded"]["switch"]._sun_light_settings.sun, self.sun)

    def test_curve_history_keeps_the_solar_pair_for_a_long_night_fade_across_midnight(self):
        result = self.solar(settings={"sunrise_time": "01:00", "sunset_time": "21:00",
                                     "sleep_activation_mode": "schedule", "sleep_schedule_start": "22:31",
                                     "sleep_schedule_end": "07:00"})
        window = next(value for value in result["sleep_windows"]
                      if value["start"] < result["day_start"] < value["end"])
        # At 00:00 a 6553-second transition starting yesterday at 22:31 is
        # still underway. Its normal source belongs to the 21:00→23:00 pair;
        # cropping to the day's immediate 23:00 bracket would lose that source.
        self.assertLess(result["day_start"] - window["start"], 6553)
        before = [event for event in result["events"] if event["timestamp"] <= window["start"]][-1]
        after = next(event for event in result["events"] if event["timestamp"] > window["start"])
        self.assertEqual(before["event"], "sunset")
        self.assertEqual(after["event"], "solar_midnight")
        self.assertEqual(before["timestamp"], datetime(2026, 10, 13, 20, tzinfo=UTC).timestamp())
        self.assertEqual(after["timestamp"], datetime(2026, 10, 13, 22, tzinfo=UTC).timestamp())
        self.assertLessEqual(result["events"][0]["timestamp"], result["day_start"] - 6553)
        self.assertGreater(result["events"][-1]["timestamp"], result["day_end"] + 6553)

    def test_same_day_schedule_has_exact_seconds_and_no_midnight_split(self):
        result = self.solar(settings={"sleep_activation_mode": "schedule",
                                     "sleep_schedule_start": "14:10:30", "sleep_schedule_end": "16:20:45"})
        relevant = [window for window in result["sleep_windows"]
                    if result["day_start"] <= window["start"] < result["day_end"]]
        self.assertEqual(relevant, [{"start": datetime(2026, 10, 14, 13, 10, 30, tzinfo=UTC).timestamp(),
                                    "end": datetime(2026, 10, 14, 15, 20, 45, tzinfo=UTC).timestamp()}])

    def test_equal_schedule_endpoints_match_runtime_all_day_membership(self):
        result = self.solar(settings={"sleep_activation_mode": "schedule",
                                     "sleep_schedule_start": "05:00", "sleep_schedule_end": "05:00"})
        self.assertTrue(result["sleep_all_day"])
        self.assertEqual(len(result["sleep_windows"]), 1)
        self.assertLess(result["sleep_windows"][0]["start"], result["day_start"] - 86400)
        self.assertGreater(result["sleep_windows"][0]["end"], result["day_end"] + 86400)

    def test_dst_fall_back_repeats_a_short_wall_clock_window_twice(self):
        result = self.solar(now=datetime(2026, 10, 25, 12, tzinfo=UTC), settings={
            "sleep_activation_mode": "schedule", "sleep_schedule_start": "01:15", "sleep_schedule_end": "01:45",
        })
        relevant = [window for window in result["sleep_windows"]
                    if result["day_start"] <= window["start"] < result["day_end"]]
        self.assertEqual(relevant, [
            {"start": datetime(2026, 10, 25, 0, 15, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 25, 0, 45, tzinfo=UTC).timestamp()},
            {"start": datetime(2026, 10, 25, 1, 15, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 25, 1, 45, tzinfo=UTC).timestamp()},
        ])

    def test_dst_fall_back_can_exit_a_window_at_the_clock_change(self):
        result = self.solar(now=datetime(2026, 10, 25, 12, tzinfo=UTC), settings={
            "sleep_activation_mode": "schedule", "sleep_schedule_start": "01:30", "sleep_schedule_end": "02:30",
        })
        relevant = [window for window in result["sleep_windows"]
                    if result["day_start"] <= window["start"] < result["day_end"]]
        self.assertEqual(relevant, [
            {"start": datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 25, 1, tzinfo=UTC).timestamp()},
            {"start": datetime(2026, 10, 25, 1, 30, tzinfo=UTC).timestamp(),
             "end": datetime(2026, 10, 25, 2, 30, tzinfo=UTC).timestamp()},
        ])

    def test_dst_spring_skips_an_absent_window_and_starts_at_jump_for_partial_gap(self):
        for end, expected in (("01:45", []), ("02:30", [{
            "start": datetime(2026, 3, 29, 1, tzinfo=UTC).timestamp(),
            "end": datetime(2026, 3, 29, 1, 30, tzinfo=UTC).timestamp(),
        }])):
            with self.subTest(end=end):
                result = self.solar(now=datetime(2026, 3, 29, 12, tzinfo=UTC), settings={
                    "sleep_activation_mode": "schedule", "sleep_schedule_start": "01:15", "sleep_schedule_end": end,
                })
                relevant = [window for window in result["sleep_windows"]
                            if result["day_start"] <= window["start"] < result["day_end"]]
                self.assertEqual(relevant, expected)

    def test_invalid_schedule_options_are_rejected_without_executor_or_light_calls(self):
        for draft in ({"sleep_activation_mode": "unknown"},
                      {"sleep_activation_mode": "schedule", "sleep_schedule_start": "garbage"},
                      {"sleep_activation_mode": "schedule", "sleep_schedule_end": None}):
            with self.subTest(draft=draft):
                self.solar(settings=draft)
                self.assertEqual(self.errors[-1][1], "invalid_options")
        self.assertEqual(self.executor_calls, [])
        self.assertEqual(self.calibration.calls, [])

    def test_midpoints_are_arithmetic_horizon_midpoints_not_astral_noon_or_midnight(self):
        result = self.solar(settings={"sunset_offset_minutes": 60})
        self.assertEqual(result["midpoints"], [
            {"kind": "night", "timestamp": datetime(2026, 10, 14, 0, 30, tzinfo=UTC).timestamp(),
             "local_time": "01:30"},
            {"kind": "day", "timestamp": datetime(2026, 10, 14, 12, 30, tzinfo=UTC).timestamp(),
             "local_time": "13:30"},
        ])
        noon = next(event["timestamp"] for event in result["events"] if event["event"] == "solar_noon")
        self.assertNotEqual(result["midpoints"][1]["timestamp"], noon)

    def test_midpoints_use_elapsed_time_through_dst_and_stay_in_plotted_local_day(self):
        result = self.solar(now=datetime(2026, 10, 25, 12, tzinfo=UTC), settings={
            "sunrise_time": "07:00", "sunset_time": "19:00",
        })
        night = next(value for value in result["midpoints"] if value["kind"] == "night")
        # Yesterday's 19:00 BST = 18:00 UTC, today's 07:00 GMT = 07:00 UTC.
        self.assertEqual(night["timestamp"], datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp())
        self.assertEqual(night["local_time"], "01:30")
        for value in result["midpoints"]:
            self.assertGreaterEqual(value["timestamp"], result["day_start"])
            self.assertLess(value["timestamp"], result["day_end"])


if __name__ == "__main__":
    unittest.main()
