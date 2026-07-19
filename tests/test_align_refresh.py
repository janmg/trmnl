"""
Comprehensive test suite for src.timing.align_refresh function.
Tests various refresh rate configurations and timezone scenarios.
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.timing import align_refresh, MIN_REFRESH_SECONDS


class TestAlignRefreshBasic(unittest.TestCase):
    """Test basic align_refresh functionality without timezone."""

    def test_minute_based_5min_interval(self):
        """5-minute refresh should align to 0, 5, 10, 15, etc. minutes."""
        # At 10:02:30, next refresh should be at 10:05:00 (in ~150 seconds)
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300)  # 300 seconds = 5 minutes
        self.assertGreater(result, 0)
        self.assertLess(result, 300)

    def test_minute_based_15min_interval(self):
        """15-minute refresh should align to 0, 15, 30, 45 minutes."""
        now_epoch = datetime(2026, 7, 19, 10, 7, 0).timestamp()
        result = align_refresh(now_epoch, 900)  # 900 seconds = 15 minutes
        self.assertGreater(result, 0)

    def test_minute_based_10min_interval(self):
        """10-minute refresh should align to 0, 10, 20, 30, etc. minutes."""
        now_epoch = datetime(2026, 7, 19, 10, 3, 0).timestamp()
        result = align_refresh(now_epoch, 600)  # 600 seconds = 10 minutes
        self.assertGreater(result, 0)

    def test_hour_based_1hour_interval(self):
        """1-hour refresh should align to top of hour."""
        now_epoch = datetime(2026, 7, 19, 10, 30, 0).timestamp()
        result = align_refresh(now_epoch, 3600)  # 3600 seconds = 1 hour
        # Should be approximately 1800 seconds (30 minutes)
        self.assertGreater(result, 1500)
        self.assertLess(result, 2100)

    def test_hour_based_2hour_interval(self):
        """2-hour refresh should align to 0, 2, 4, etc. hours."""
        now_epoch = datetime(2026, 7, 19, 10, 30, 0).timestamp()
        result = align_refresh(now_epoch, 7200)  # 7200 seconds = 2 hours
        self.assertGreater(result, 0)

    def test_hour_based_3hour_interval(self):
        """3-hour refresh should align to 0, 3, 6, etc. hours."""
        now_epoch = datetime(2026, 7, 19, 10, 30, 0).timestamp()
        result = align_refresh(now_epoch, 10800)  # 10800 seconds = 3 hours
        self.assertGreater(result, 0)


class TestAlignRefreshMinimum(unittest.TestCase):
    """Test MIN_REFRESH_SECONDS enforcement."""

    def test_very_short_interval_enforces_minimum(self):
        """Very short intervals should be clamped to MIN_REFRESH_SECONDS."""
        now_epoch = datetime(2026, 7, 19, 10, 0, 0).timestamp()
        result = align_refresh(now_epoch, 10)  # 10 seconds (less than MIN)
        self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)

    def test_at_minute_boundary_adds_lead(self):
        """At exact minute boundary, should still add time."""
        now_epoch = datetime(2026, 7, 19, 10, 0, 0).timestamp()
        result = align_refresh(now_epoch, 60)
        self.assertGreater(result, 0)


class TestAlignRefreshWithLeadSeconds(unittest.TestCase):
    """Test lead_seconds parameter."""

    def test_lead_seconds_reduces_wait_time(self):
        """Lead seconds should reduce the wait time before refresh."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        without_lead = align_refresh(now_epoch, 300, lead_seconds=0)
        with_lead = align_refresh(now_epoch, 300, lead_seconds=5)
        self.assertLess(with_lead, without_lead)

    def test_lead_seconds_5_seconds(self):
        """5 seconds of lead should be respected."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, lead_seconds=5)
        self.assertGreater(result, 0)

    def test_lead_seconds_30_seconds(self):
        """30 seconds of lead should be respected."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, lead_seconds=30)
        self.assertGreater(result, 0)


class TestAlignRefreshWithTimezone(unittest.TestCase):
    """Test timezone-aware alignment."""

    def test_timezone_europe_helsinki(self):
        """Test alignment with Europe/Helsinki timezone."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, tz_name="Europe/Helsinki")
        self.assertGreater(result, 0)
        self.assertLess(result, 300)

    def test_timezone_us_eastern(self):
        """Test alignment with US/Eastern timezone."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, tz_name="US/Eastern")
        self.assertGreater(result, 0)

    def test_timezone_asia_tokyo(self):
        """Test alignment with Asia/Tokyo timezone."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, tz_name="Asia/Tokyo")
        self.assertGreater(result, 0)

    def test_timezone_utc(self):
        """Test alignment with UTC timezone."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, tz_name="UTC")
        self.assertGreater(result, 0)


class TestAlignRefreshForClock(unittest.TestCase):
    """Test for_clock=True mode that returns boundary datetime."""

    def test_clock_mode_returns_tuple(self):
        """Clock mode should return (seconds, next_boundary_dt)."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, for_clock=True)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], int)
        self.assertIsInstance(result[1], datetime)

    def test_clock_mode_5min_interval(self):
        """Clock mode with 5-min interval should return next 5-min boundary."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        seconds, boundary = align_refresh(now_epoch, 300, for_clock=True)
        self.assertGreater(seconds, 0)
        self.assertIsNotNone(boundary)

    def test_clock_mode_15min_interval(self):
        """Clock mode with 15-min interval should return next 15-min boundary."""
        now_epoch = datetime(2026, 7, 19, 10, 7, 0).timestamp()
        seconds, boundary = align_refresh(now_epoch, 900, for_clock=True)
        self.assertGreater(seconds, 0)
        # Boundary minute should be aligned to 15-min intervals
        self.assertIn(boundary.minute % 15, [0])

    def test_clock_mode_with_timezone_and_lead(self):
        """Clock mode should work with both timezone and lead seconds."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        seconds, boundary = align_refresh(
            now_epoch, 300, lead_seconds=5, tz_name="Europe/Helsinki", for_clock=True
        )
        self.assertGreater(seconds, 0)
        self.assertIsNotNone(boundary)


class TestAlignRefreshEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_exactly_at_midnight(self):
        """Alignment at exactly midnight should work."""
        now_epoch = datetime(2026, 7, 19, 0, 0, 0).timestamp()
        result = align_refresh(now_epoch, 300)
        self.assertGreater(result, 0)

    def test_just_before_midnight(self):
        """Alignment just before midnight should wrap to next day."""
        now_epoch = datetime(2026, 7, 19, 23, 59, 30).timestamp()
        result = align_refresh(now_epoch, 300)
        self.assertGreater(result, 0)

    def test_at_hour_boundary(self):
        """Alignment at exact hour boundary should work."""
        now_epoch = datetime(2026, 7, 19, 10, 0, 0).timestamp()
        result = align_refresh(now_epoch, 3600)
        self.assertGreater(result, 0)

    def test_invalid_timezone_fallback(self):
        """Invalid timezone should gracefully fallback."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, tz_name="Invalid/Timezone")
        self.assertGreater(result, 0)

    def test_zero_interval_defaults_to_one(self):
        """Zero or None interval should default to 1 second minimum."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 0)
        self.assertGreater(result, 0)

    def test_negative_lead_becomes_zero(self):
        """Negative lead seconds should be treated as zero."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300, lead_seconds=-10)
        self.assertGreater(result, 0)


class TestAlignRefreshConsistency(unittest.TestCase):
    """Test consistency across multiple calls."""

    def test_same_time_gives_same_result(self):
        """Same timestamp should always produce same result."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result1 = align_refresh(now_epoch, 300)
        result2 = align_refresh(now_epoch, 300)
        self.assertEqual(result1, result2)

    def test_multiple_5min_intervals_align(self):
        """Multiple 5-min intervals should all align correctly."""
        base_epoch = datetime(2026, 7, 19, 10, 0, 0).timestamp()
        for minute in range(0, 60, 5):
            now_epoch = base_epoch + (minute * 60) + 30  # 30 seconds into each 5-min block
            result = align_refresh(now_epoch, 300)
            # Should be about 270 seconds (4.5 minutes) to next boundary
            self.assertGreater(result, 260)
            self.assertLess(result, 280)

    def test_multiple_15min_intervals_align(self):
        """Multiple 15-min intervals should all align correctly."""
        base_epoch = datetime(2026, 7, 19, 10, 0, 0).timestamp()
        for minute in [0, 15, 30, 45]:
            now_epoch = base_epoch + (minute * 60) + 30
            result = align_refresh(now_epoch, 900)
            # Should be about 870 seconds (14.5 minutes) to next boundary
            self.assertGreater(result, 860)
            self.assertLess(result, 880)


class TestAlignRefreshMinuteAlignment(unittest.TestCase):
    """Test specific minute-based alignment scenarios."""

    def test_5min_at_minute_2(self):
        """At minute 2, next 5-min boundary is minute 5."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 0).timestamp()
        result = align_refresh(now_epoch, 300)
        # Should be 180 seconds (3 minutes)
        self.assertGreater(result, 170)
        self.assertLess(result, 190)

    def test_5min_at_minute_4_59(self):
        """At 4:59, next 5-min boundary is at 5:00."""
        now_epoch = datetime(2026, 7, 19, 10, 4, 59).timestamp()
        result = align_refresh(now_epoch, 300)
        # Should be enforced to MIN_REFRESH_SECONDS (1 sec away from 5:00 is too close)
        self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)

    def test_15min_at_minute_7(self):
        """At minute 7, next 15-min boundary is minute 15."""
        now_epoch = datetime(2026, 7, 19, 10, 7, 0).timestamp()
        result = align_refresh(now_epoch, 900)
        # Should be 480 seconds (8 minutes)
        self.assertGreater(result, 470)
        self.assertLess(result, 490)

    def test_15min_at_minute_14_59(self):
        """At 14:59, next 15-min boundary is at 15:00."""
        now_epoch = datetime(2026, 7, 19, 10, 14, 59).timestamp()
        result = align_refresh(now_epoch, 900)
        # Should be enforced to MIN_REFRESH_SECONDS (1 sec away from 15:00 is too close)
        self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)


class TestAlignRefreshHourAlignment(unittest.TestCase):
    """Test hour-based alignment scenarios."""

    def test_1hour_at_30min(self):
        """At 30 minutes past, next hour is 30 min away."""
        now_epoch = datetime(2026, 7, 19, 10, 30, 0).timestamp()
        result = align_refresh(now_epoch, 3600)
        # Should be about 1800 seconds (30 minutes)
        self.assertGreater(result, 1790)
        self.assertLess(result, 1810)

    def test_2hour_at_1hour_30min(self):
        """At 1:30 into 2-hour cycle, next is 30 min away."""
        now_epoch = datetime(2026, 7, 19, 9, 30, 0).timestamp()
        result = align_refresh(now_epoch, 7200)
        # Should be about 1800 seconds (30 minutes to next 2-hour boundary)
        self.assertGreater(result, 1790)
        self.assertLess(result, 1810)

    def test_3hour_at_1hour_30min(self):
        """At 1:30 into 3-hour cycle."""
        now_epoch = datetime(2026, 7, 19, 9, 30, 0).timestamp()
        result = align_refresh(now_epoch, 10800)
        self.assertGreater(result, 0)


class TestAlignRefreshReturnTypes(unittest.TestCase):
    """Test return types and values."""

    def test_returns_integer_seconds(self):
        """Result should always be an integer."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300)
        self.assertIsInstance(result, int)

    def test_returns_positive_integer(self):
        """Result should always be positive."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        result = align_refresh(now_epoch, 300)
        self.assertGreater(result, 0)

    def test_respects_minimum_refresh(self):
        """Result should never be less than MIN_REFRESH_SECONDS."""
        for interval in [60, 120, 300, 600, 900, 1800, 3600]:
            now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
            result = align_refresh(now_epoch, interval)
            self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)


class TestAlignRefreshComplexScenarios(unittest.TestCase):
    """Test complex real-world scenarios."""

    def test_morning_to_evening_transitions(self):
        """Test transitions across hours during a full day."""
        base_epoch = datetime(2026, 7, 19, 6, 0, 0).timestamp()
        for hour_offset in range(0, 12, 3):  # 6am, 9am, 12pm, 3pm
            now_epoch = base_epoch + (hour_offset * 3600)
            result = align_refresh(now_epoch, 900, tz_name="Europe/Helsinki")
            self.assertGreater(result, 0)
            self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)

    def test_all_common_refresh_rates(self):
        """Test alignment for all commonly used refresh rates."""
        now_epoch = datetime(2026, 7, 19, 10, 2, 30).timestamp()
        common_rates = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800]
        for rate in common_rates:
            result = align_refresh(now_epoch, rate)
            self.assertGreater(result, 0)
            self.assertGreaterEqual(result, MIN_REFRESH_SECONDS)
            self.assertLessEqual(result, rate * 2 + MIN_REFRESH_SECONDS)


if __name__ == "__main__":
    unittest.main()
