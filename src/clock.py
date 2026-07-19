# -*- coding: utf-8 -*-
import html
import io
import os
import re
from datetime import datetime, timezone, timedelta
import requests
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

from src.db import get_device_timezone, replace_holidays_for_year, get_holidays_for_date

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

MONTH_LOOKUP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _current_local_datetime(db_path=None, mac_address=None):
    tz_name = "Europe/Helsinki"
    if db_path and mac_address:
        try:
            tz_name = get_device_timezone(db_path, mac_address, default=tz_name)
        except Exception:
            pass
    # Add 20 seconds to account for refresh delay
    return datetime.now(ZoneInfo(tz_name)) + timedelta(seconds=20)


def _normalize_name(name):
    return re.sub(r'\s+', ' ', name).strip(' •-')


def _dedupe_entries(entries):
    """Deduplicate entries. When same day has entries from different sources
    with names starting with the same string, keep only the one with most characters."""
    # First pass: collect all entries by date
    by_date = {}
    for entry in entries:
        date_str, name, source = entry
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(entry)
    
    # Second pass: deduplicate within each date
    deduped = []
    for date_str, date_entries in by_date.items():
        if len(date_entries) == 1:
            deduped.append(date_entries[0])
            continue
        
        # Multiple entries for same date - check if names start with same string
        seen_prefixes = {}
        for entry in date_entries:
            date_str, name, source = entry
            # Find if any existing prefix matches the start of this name
            matched = False
            for existing_name in list(seen_prefixes.keys()):
                # Check if they share a common start (the shorter one)
                min_len = min(len(name), len(existing_name))
                if name[:min_len] == existing_name[:min_len]:
                    # Same prefix - keep the longer one
                    if len(name) > len(existing_name):
                        # Replace with this entry
                        del seen_prefixes[existing_name]
                        seen_prefixes[name] = entry
                    matched = True
                    break
            
            if not matched:
                seen_prefixes[name] = entry
        
        # Add the deduplicated entries for this date
        deduped.extend(seen_prefixes.values())
    
    return deduped


def _fetch_timeanddate_holidays(year):
    url = f"https://www.timeanddate.com/calendar/print.html?year={year}&country=24&hol=268439833"
    # The site's anti-scraping check rejects a bare "Mozilla/5.0" User-Agent
    # with a 403; a full browser-like header set (plus a Referer pointing at
    # the corresponding non-print holiday page) is accepted.
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.timeanddate.com/holidays/finland/{year}",
        "Sec-Fetch-Mode": "navigate",
    }
    response = requests.get(url, timeout=20, headers=headers)
    response.raise_for_status()

    text = response.text

    # Each holiday is rendered as a table row containing a date cell
    # ("<td><span class=...>1 Jan</span></td>" or plain "<td>1 Jan</td>")
    # immediately followed by a name cell ("<td>New Year's Day</td>").
    row_pattern = re.compile(
        r'<td[^>]*><span[^>]*>(\d{1,2})\s+([A-Za-z]{3})</span></td><td>(.*?)</td>'
        r'|<td>(\d{1,2})\s+([A-Za-z]{3})</td><td>(.*?)</td>'
    )
    entries = []

    for match in row_pattern.finditer(text):
        if match.group(1):
            day_text, month_text, raw_name = match.group(1), match.group(2), match.group(3)
        else:
            day_text, month_text, raw_name = match.group(4), match.group(5), match.group(6)

        month = MONTH_LOOKUP.get(month_text)
        if not month:
            continue

        name = _normalize_name(html.unescape(re.sub(r'<[^>]+>', '', raw_name)))
        if not name:
            continue

        try:
            day = int(day_text)
        except ValueError:
            continue

        entries.append((f"{year}-{month:02d}-{day:02d}", name, 'timeanddate'))

    return _dedupe_entries(entries)


def _fetch_intermin_holidays(year):
    url = f"https://intermin.fi/en/flag-days-{year}"
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    entries = []
    # The site currently renders each flag day as "<li>3.2. Name...</li>",
    # with no leading bullet character (older markup used a "•" prefix).
    # Strip HTML entities/tags from each <li> and match the leading date.
    for li_html in re.findall(r'<li[^>]*>(.*?)</li>', response.text, flags=re.DOTALL):
        text = re.sub(r'<[^>]+>', ' ', li_html)
        text = html.unescape(text)
        line = _normalize_name(text)

        match = re.match(r'(\d{1,2})\.(\d{1,2})\.\s*(.+)', line)
        if not match:
            continue

        day_text, month_text, raw_name = match.groups()
        try:
            day = int(day_text)
            month = int(month_text)
        except ValueError:
            continue

        name = _normalize_name(re.split(r'\s*\|\s*|\s+a recommended flag day', raw_name)[0])
        if not name:
            continue

        entries.append((f"{year}-{month:02d}-{day:02d}", name, 'intermin'))

    return _dedupe_entries(entries)


def sync_holidays(db_path, today=None, mac_address=None):
    if not db_path:
        return

    today = today or _current_local_datetime(db_path, mac_address).date()
    year = today.year

    # Fetch each source independently so one source failing (e.g. a 403 from
    # an anti-bot block) doesn't prevent the others from being stored.
    entries = []
    for fetcher in (_fetch_timeanddate_holidays, _fetch_intermin_holidays):
        try:
            entries.extend(fetcher(year))
        except Exception as e:
            print(f"Error fetching holidays via {fetcher.__name__}: {e}")

    entries = _dedupe_entries(entries)

    try:
        replace_holidays_for_year(db_path, year, entries)
    except Exception as e:
        print(f"Error syncing holidays: {e}")


def get_today_holidays(db_path, today=None, mac_address=None):
    if not db_path:
        return []

    today = today or _current_local_datetime(db_path, mac_address).date()
    today_str = today.isoformat()

    try:
        return get_holidays_for_date(db_path, today_str)
    except Exception as e:
        print(f"Error loading holidays for {today_str}: {e}")
        return []

def generate_clock(db_path=None, mac_address=None):
    # Target dimensions: 800 x 480
    width, height = 800, 480
    
    # White background
    img = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(img)
    
    # Load the same font as titlecard.py
    font_path = os.path.join(BASE_DIR, "fonts", "exo2-medium.ttf")
    try:
        # Using a very large size for the clock
        font_size = 180
        font_date_size = 40
        font = ImageFont.truetype(font_path, font_size)
        font_date = ImageFont.truetype(font_path, font_date_size)
    except:
        font = ImageFont.load_default()
        font_date = ImageFont.load_default()
    
    now = _current_local_datetime(db_path, mac_address)
    
    current_time = now.strftime("%H:%M")
    # Get fully written out date (e.g., "Friday, October 10, 2026")
    current_date = now.strftime("%A, %B %d, %Y")
    holiday_names = get_today_holidays(db_path, now.date(), mac_address)
    holiday_text = " · ".join(holiday_names) if holiday_names else ""

    try:
        font_holiday_size = 24
        font_holiday = ImageFont.truetype(font_path, font_holiday_size)
        while holiday_text:
            holiday_bbox = draw.textbbox((0, 0), holiday_text, font=font_holiday)
            if (holiday_bbox[2] - holiday_bbox[0]) <= 720 or font_holiday_size <= 18:
                break
            font_holiday_size -= 2
            font_holiday = ImageFont.truetype(font_path, font_holiday_size)
    except Exception:
        font_holiday = ImageFont.load_default()
    
    # Calculate text position for time (centered)
    bbox_time = draw.textbbox((0, 0), current_time, font=font)
    time_w = bbox_time[2] - bbox_time[0]
    time_h = bbox_time[3] - bbox_time[1]
    
    # Calculate text position for date (centered)
    bbox_date = draw.textbbox((0, 0), current_date, font=font_date)
    date_w = bbox_date[2] - bbox_date[0]
    date_h = bbox_date[3] - bbox_date[1]

    holiday_h = 0
    holiday_w = 0
    if holiday_text:
        holiday_bbox = draw.textbbox((0, 0), holiday_text, font=font_holiday)
        holiday_w = holiday_bbox[2] - holiday_bbox[0]
        holiday_h = holiday_bbox[3] - holiday_bbox[1]
    
    # Combined vertical space
    # Increase time/date separation and lift the time block slightly.
    time_date_gap = 34
    extra_gap = 10 if holiday_text else 0
    total_h = time_h + date_h + time_date_gap + holiday_h + extra_gap
    
    # Center vertically and horizontally
    visible_v_center = 225
    
    time_x = (width - time_w) // 2
    time_y = visible_v_center - (total_h // 2) - 42
    
    date_x = (width - date_w) // 2
    date_y = time_y + time_h + time_date_gap + 20

    holiday_x = (width - holiday_w) // 2 if holiday_text else 0
    holiday_y = date_y + date_h + (10 if holiday_text else 0)
    
    # Draw text in black
    draw.text((time_x, time_y), current_time, font=font, fill=0)
    draw.text((date_x, date_y), current_date, font=font_date, fill=0)
    if holiday_text:
        draw.text((holiday_x, holiday_y), holiday_text, font=font_holiday, fill=0)
    
    # Convert to buffer
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf
