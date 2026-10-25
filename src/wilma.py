# -*- coding: utf-8 -*-
import os
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

def parse_ics_datetime(dt_str, tz_name='Europe/Helsinki'):
    local_tz = ZoneInfo(tz_name)
    dt_str = dt_str.strip()
    
    if 'T' in dt_str:
        clean_str = dt_str.split('T')[0] + 'T' + dt_str.split('T')[1].split('Z')[0]
        try:
            dt = datetime.strptime(clean_str, '%Y%m%dT%H%M%S')
            if dt_str.endswith('Z'):
                dt = dt.replace(tzinfo=timezone.utc).astimezone(local_tz)
            else:
                dt = dt.replace(tzinfo=local_tz)
            return dt
        except ValueError:
            pass
    else:
        try:
            dt = datetime.strptime(dt_str, '%Y%m%d')
            return datetime.combine(dt.date(), datetime.min.time(), tzinfo=local_tz)
        except ValueError:
            pass
    return None

def parse_until_date(rrule_str, tz_name='Europe/Helsinki'):
    """Extract UNTIL date from RRULE string if present."""
    for param in rrule_str.split(';'):
        if param.startswith('UNTIL='):
            val = param.split('=', 1)[1]
            return parse_ics_datetime(val, tz_name)
    return None

def get_relevant_school_date(now_dt):
    """
    Determines the relevant date for school schedule:
    - If current time is after 18:00 (6 PM) or during the weekend:
      - Saturday (5) or Sunday (6) -> Next Monday
      - Weekdays after 18:00 -> Next day (Friday after 18:00 -> Next Monday)
    - Otherwise -> Today
    """
    target = now_dt.date()
    
    # If after 18:00 on a weekday
    if now_dt.hour >= 18:
        target += timedelta(days=1)

    # If target falls on Saturday (5) or Sunday (6), roll forward to Monday
    if target.weekday() == 5: # Saturday
        target += timedelta(days=2)
    elif target.weekday() == 6: # Sunday
        target += timedelta(days=1)

    return target

def get_first_school_event(child_name, target_date=None, tz_name='Europe/Helsinki'):
    """Return the first event's start time, summary and room for a school date."""
    local_tz = ZoneInfo(tz_name)
    if target_date is None:
        target_date = datetime.now(local_tz).date()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    ics_path = f"/tmp/{child_name.lower()}.ics"
    if not os.path.exists(ics_path):
        return None, None, None

    first_start = None
    first_summary = None
    first_location = None

    try:
        with open(ics_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        events = content.split('BEGIN:VEVENT')
        for block in events[1:]:
            block = block.split('END:VEVENT')[0]
            
            dtstart = None
            rrule = None
            summary = None
            location = None
            for line in block.splitlines():
                line = line.strip()
                if line.startswith('DTSTART'):
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        dtstart = parse_ics_datetime(parts[1], tz_name)
                elif line.startswith('RRULE:'):
                    rrule = line.split(':', 1)[1]
                elif line.startswith('SUMMARY:'):
                    summary = line.split(':', 1)[1].replace('\\,', ',').replace('\\;', ';')
                elif line.startswith('LOCATION:'):
                    location = line.split(':', 1)[1].replace('\\,', ',').replace('\\;', ';')

            if not dtstart:
                continue

            event_start_time = dtstart.time()

            if not rrule:
                if dtstart.date() == target_date:
                    cur_start = datetime.combine(target_date, event_start_time, tzinfo=local_tz)
                    if first_start is None or cur_start < first_start:
                        first_start = cur_start
                        first_summary = summary
                        first_location = location
            else:
                # For weekly recurrences (or simple matching day-of-week recurrence within start and until dates)
                until_dt = parse_until_date(rrule, tz_name)
                dtstart_date = dtstart.date()

                # Check if target_date is within start and until bounds
                if target_date >= dtstart_date and (until_dt is None or target_date <= until_dt.date()):
                    # Check day of week match if FREQ=WEEKLY
                    if 'FREQ=WEEKLY' in rrule:
                        if target_date.weekday() == dtstart_date.weekday():
                            cur_start = datetime.combine(target_date, event_start_time, tzinfo=local_tz)
                            if first_start is None or cur_start < first_start:
                                first_start = cur_start
                                first_summary = summary
                                first_location = location

    except Exception as e:
        print(f"Error reading/parsing {ics_path}: {e}")

    if first_start:
        return first_start.strftime("%H:%M"), first_summary, first_location
    return None, None, None

def get_first_event_start(child_name, target_date=None, tz_name='Europe/Helsinki'):
    """Return the first school event's start time formatted as 'HH:MM'."""
    start, _, _ = get_first_school_event(child_name, target_date, tz_name)
    return start

def get_school_start_times(target_date=None, tz_name='Europe/Helsinki'):
    return {
        'niiles': get_first_event_start('niiles', target_date, tz_name),
        'lorens': get_first_event_start('lorens', target_date, tz_name)
    }
