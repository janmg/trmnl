"""Timing utilities for refresh alignment."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MIN_REFRESH_SECONDS = 40


def align_refresh(now_epoch, interval_seconds, lead_seconds=0, tz_name=None, for_clock=False):
    """
    Compute seconds until the next aligned refresh.

    - When `interval_seconds` is a whole number of minutes, align to the next
      multiple-of-interval minute boundary (in the device's local timezone),
      minus `lead_seconds` so the device wakes slightly early.
    - Otherwise, fall back to aligning to epoch multiples of `interval_seconds`.
    - The result is never allowed to be less than `MIN_REFRESH_SECONDS`.
    - When `for_clock=True`, returns a tuple of (seconds, next_boundary_dt) for clock alignment.
    """
    interval = max(1, int(interval_seconds or 1))
    lead = max(0, int(lead_seconds or 0))

    try:
        now_dt = datetime.fromtimestamp(now_epoch, tz=ZoneInfo(tz_name)) if tz_name else datetime.fromtimestamp(now_epoch)
    except Exception:
        now_dt = datetime.fromtimestamp(now_epoch)

    if interval % 60 == 0:
        interval_min = interval // 60
        minutes_since_midnight = now_dt.hour * 60 + now_dt.minute
        next_minutes = ((minutes_since_midnight // interval_min) + 1) * interval_min

        midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        next_boundary_dt = midnight + timedelta(minutes=next_minutes)
        target_dt = next_boundary_dt - timedelta(seconds=lead)

        if target_dt.timestamp() <= now_epoch:
            next_boundary_dt += timedelta(minutes=interval_min)
            target_dt = next_boundary_dt - timedelta(seconds=lead)

        seconds = int(target_dt.timestamp() - now_epoch)
    else:
        # For minute-based intervals, align to refresh_rate boundaries within the day
        interval_seconds_safe = max(60, interval)
        current_seconds = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second
        next_boundary_seconds = ((current_seconds // interval_seconds_safe) + 1) * interval_seconds_safe
        midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        next_boundary_dt = midnight + timedelta(seconds=next_boundary_seconds)
        target_dt = next_boundary_dt - timedelta(seconds=lead)

        if target_dt.timestamp() <= now_epoch:
            next_boundary_dt = midnight + timedelta(seconds=next_boundary_seconds + interval_seconds_safe)
            target_dt = next_boundary_dt - timedelta(seconds=lead)

        seconds = int(target_dt.timestamp() - now_epoch)

    if seconds < MIN_REFRESH_SECONDS:
        seconds += 60
        if not for_clock:
            return seconds
        # Recalculate next_boundary_dt if we added 60 seconds
        next_boundary_dt = datetime.fromtimestamp(now_epoch + seconds + lead, tz=ZoneInfo(tz_name) if tz_name else None)

    if for_clock:
        return seconds, next_boundary_dt
    return seconds
