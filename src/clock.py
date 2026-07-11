# -*- coding: utf-8 -*-
import io
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
import requests
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

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


def _current_local_datetime():
    return datetime.now(ZoneInfo("Europe/Helsinki"))


def _ensure_holiday_tables(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (date, name, source)
        )
    ''')


def _normalize_name(name):
    return re.sub(r'\s+', ' ', name).strip(' •-')


def _dedupe_entries(entries):
    seen = set()
    deduped = []
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)
    return deduped


def _fetch_timeanddate_holidays(year):
    url = f"https://www.timeanddate.com/calendar/print.html?year={year}&country=24&hol=268439833"
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    text = re.sub(r'\s+', ' ', response.text)
    marker = text.lower().find('calendar generated on')
    if marker != -1:
        segment = text[max(0, marker - 1800):marker]
    else:
        segment = text[-2500:]

    pair_pattern = re.compile(r'(\d{1,2}\s+[A-Z][a-z]{2})\s+(.+?)(?=\s+\d{1,2}\s+[A-Z][a-z]{2}|\s*$)')
    entries = []

    for date_label, raw_name in pair_pattern.findall(segment):
        parts = date_label.split()
        if len(parts) != 2:
            continue
        day_text, month_text = parts
        month = MONTH_LOOKUP.get(month_text)
        if not month:
            continue

        name = _normalize_name(re.split(r'\s+\|\s+|\s+Calendar generated on', raw_name)[0])
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
    for line in response.text.splitlines():
        line = _normalize_name(line)
        match = re.match(r'•\s*(\d{1,2})\.(\d{1,2})\.\s*(.+)', line)
        if not match:
            continue

        day_text, month_text, raw_name = match.groups()
        try:
            day = int(day_text)
            month = int(month_text)
        except ValueError:
            continue

        name = _normalize_name(re.split(r'\s+\|\s+|\s+a recommended flag day', raw_name)[0])
        if not name:
            continue

        entries.append((f"{year}-{month:02d}-{day:02d}", name, 'intermin'))

    return _dedupe_entries(entries)


def sync_holidays(db_path, today=None):
    if not db_path:
        return

    today = today or _current_local_datetime().date()
    year = today.year

    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_holiday_tables(conn)

            entries = []
            entries.extend(_fetch_timeanddate_holidays(year))
            entries.extend(_fetch_intermin_holidays(year))
            entries = _dedupe_entries(entries)

            conn.execute('DELETE FROM holidays WHERE substr(date, 1, 4) = ?', (str(year),))
            if entries:
                conn.executemany(
                    'INSERT OR REPLACE INTO holidays (date, name, source) VALUES (?, ?, ?)',
                    entries,
                )
    except Exception as e:
        print(f"Error syncing holidays: {e}")


def get_today_holidays(db_path, today=None):
    if not db_path:
        return []

    today = today or _current_local_datetime().date()
    today_str = today.isoformat()

    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_holiday_tables(conn)
            rows = conn.execute(
                'SELECT name FROM holidays WHERE date = ? ORDER BY source, name',
                (today_str,)
            ).fetchall()
        names = []
        for row in rows:
            name = row[0]
            if name and name not in names:
                names.append(name)
        return names
    except Exception as e:
        print(f"Error loading holidays for {today_str}: {e}")
        return []

def generate_clock(db_path=None):
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
    
    now = _current_local_datetime()
    
    current_time = now.strftime("%H:%M")
    # Get fully written out date (e.g., "Friday, October 10, 2026")
    current_date = now.strftime("%A, %B %d, %Y")
    holiday_names = get_today_holidays(db_path, now.date())
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
