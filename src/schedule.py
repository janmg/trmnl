# -*- coding: utf-8 -*-
import io
import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

from src.db import get_device_field, get_device_location, get_device_timezone, get_device_name, get_events_raw
from src.clock import sync_holidays
from src.wilma import get_first_school_event, get_relevant_school_date

NTC_CALENDAR_URL = 'https://calendar.google.com/calendar/ical/c_imdloh50miqt4c4o3erqej499g%40group.calendar.google.com/public/basic.ics'
NTC_DEVICE_MAC = '44:1B:F6:81:9B:FC'

def get_next_ntc_events():
    """Fetch the next two upcoming events from the NTC Google Calendar."""
    try:
        res = requests.get(NTC_CALENDAR_URL, timeout=5)
        if res.status_code != 200:
            return []
        ics_data = res.text
        events = []
        current_event = {}
        for line in ics_data.splitlines():
            line = line.strip()
            if line == 'BEGIN:VEVENT':
                current_event = {}
            elif line == 'END:VEVENT':
                if current_event:
                    events.append(current_event)
                current_event = {}
            elif ':' in line:
                k, v = line.split(':', 1)
                k_base = k.split(';')[0]
                current_event[k_base] = v
                
        now = datetime.now(ZoneInfo('Europe/Helsinki'))
        future_events = []
        for e in events:
            summary = e.get('SUMMARY', '')
            dtstart_str = e.get('DTSTART', '')
            if not dtstart_str:
                continue
                
            dt = None
            if 'T' in dtstart_str:
                if dtstart_str.endswith('Z'):
                    dt = datetime.strptime(dtstart_str, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/Helsinki'))
                else:
                    dt = datetime.strptime(dtstart_str, '%Y%m%dT%H%M%S').replace(tzinfo=ZoneInfo('Europe/Helsinki'))
            else:
                dt = datetime.strptime(dtstart_str, '%Y%m%d').replace(tzinfo=ZoneInfo('Europe/Helsinki'))
                
            if dt.date() >= now.date():
                future_events.append((dt, summary))
                
        future_events.sort(key=lambda x: x[0])
        return [
            (event_dt, summary.replace('\\,', ',').replace('\\;', ';'))
            for event_dt, summary in future_events[:2]
        ]
    except Exception as e:
        print(f"Error fetching NTC calendar: {e}")
        return []


def class_from_summary(summary):
    if not summary:
        return "-"
    # Wilma omits the class suffix for this subject in SUMMARY, but this
    # timetable's SUK_ entry is the SUK_51 course.
    if re.search(r'(?<![A-Za-z0-9])SUK_(?:\s|$)', summary):
        return "SUK_51"
    match = re.search(
        r'(?<![A-Za-z0-9])y?([A-Z][A-Z0-9]*(?:\.\d+|_[A-Z0-9]+)?)(?![A-Za-z0-9_])',
        summary,
    )
    if match:
        return match.group(1)
    return "Suomen ki..." if summary.startswith("Suomen kieli ja kirjallisuus") else "-"

def room_from_location(location):
    if not location:
        return ""
    # Wilma often repeats the room code, e.g. "313 313"
    words = location.split()
    half = len(words) // 2
    if len(words) % 2 == 0 and half and words[:half] == words[half:]:
        return " ".join(words[:half])
    return location.strip()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'trmnl.db')

def get_weather(mac_address=None):
    """Fetch weather data using device's location or fallback to Helsinki."""
    coords = "60.1695,24.9355" # Default Helsinki
    if mac_address:
        try:
            location = get_device_location(DB_PATH, mac_address)
            if location:
                coords = location
        except Exception as e:
            print(f"DEBUG: Error reading location for weather: {e}")

    lat, lon = [v.strip() for v in coords.split(",", 1)]
    url = f"https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature&storedquery_id=fmi::forecast::harmonie::surface::point::simple&latlon={lat},{lon}&"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Weather API error: {response.status_code}")
            return None
        
        root = ET.fromstring(response.content)
        ns = {
            'wfs': 'http://www.opengis.net/wfs/2.0',
            'BsWfs': 'http://xml.fmi.fi/schema/wfs/2.0',
            'gml': 'http://www.opengis.net/gml/3.2'
        }
        
        temps = []
        rains = []
        current_temp = None
        
        now = datetime.now(timezone.utc)
        # We want data for "today" in local time
        # Let's just look at the next 18 hours of forecast to represent "today"
        end_period = now + timedelta(hours=18)
        
        for element in root.findall('.//BsWfs:BsWfsElement', ns):
            time_str = element.find('BsWfs:Time', ns).text
            time_obj = datetime.strptime(time_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            
            if time_obj < now - timedelta(hours=1):
                continue
            if time_obj > end_period:
                continue
                
            param_name = element.find('BsWfs:ParameterName', ns).text
            param_value = element.find('BsWfs:ParameterValue', ns).text
            
            try:
                val = float(param_value)
            except ValueError:
                continue

            if param_name == 'Temperature':
                temps.append(val)
                if current_temp is None and time_obj >= now - timedelta(minutes=30):
                    current_temp = val
            elif param_name == 'Precipitation1h':
                rains.append(val)
        
        if not temps:
            return None
            
        return {
            'current': current_temp if current_temp is not None else temps[0],
            'min': min(temps),
            'max': max(temps),
            'will_rain': any(r > 0.1 for r in rains) # 0.1mm threshold
        }
    except Exception as e:
        print(f"Weather error: {e}")
        return None

def get_events(is_generic=False, mac_address=None):
    """Load device events plus shared holidays, with NTC events on its dedicated device."""
    if is_generic:
        return [
            ["1 oct, 12:00", "Miina Sillanpää Day"],
            ["10 oct, 12:00", "Aleksis Kivi"],
            ["24 October", "United Nations Day"],
            ["6 Nov, 12:00", "Swedish Heritage Day"],
            ["20 Nov, 12:00", "Children's Rights"],
            ["8 Dec, 12:00", "Jean Sibelius"]
        ]

    db_events = []
    try:
        rows = get_events_raw(DB_PATH, mac_address)
        if rows:
            today_start = datetime.now().strftime('%Y-%m-%d 00:00:00')
            for event_time, title in rows:
                str_time = str(event_time)
                if str_time < today_start:
                    continue
                dt = None
                has_time = True
                try:
                    dt = datetime.strptime(str(event_time), '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        dt = datetime.strptime(str(event_time), '%Y-%m-%d %H:%M')
                    except ValueError:
                        try:
                            dt = datetime.strptime(str(event_time), '%Y-%m-%d')
                            has_time = False
                        except ValueError:
                            db_events.append((None, str_time, title, False))
                            continue

                db_events.append((dt, None, title, has_time))
    except Exception as e:
        print(f"Error loading events from database: {e}")

    # Sync holidays following clock rules
    try:
        sync_holidays(DB_PATH, mac_address=mac_address)
    except Exception as e:
        print(f"Error syncing holidays in schedule: {e}")

    # Query next holiday from database
    try:
        import sqlite3
        today_date_str = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(DB_PATH) as conn:
            h_rows = conn.execute(
                'SELECT date, name FROM holidays WHERE date >= ? ORDER BY date ASC, source ASC',
                (today_date_str,)
            ).fetchall()
        if h_rows:
            h_date_str, h_name = h_rows[0]
            h_dt = datetime.strptime(h_date_str, '%Y-%m-%d')
            # Format holiday entry (all-day event or date without time)
            db_events.append((h_dt, None, f"Flag: {h_name}", False))
    except Exception as e:
        print(f"Error loading next holiday: {e}")

    # NTC lesson events belong only to the dedicated NTC device.
    if mac_address and mac_address.upper() == NTC_DEVICE_MAC:
        ntc_events = get_next_ntc_events()
        ntc_event = ntc_events[0] if ntc_events else None
        if ntc_event:
            ntc_dt, ntc_title = ntc_event
            db_events.append((ntc_dt.replace(tzinfo=None), None, f"NTC: {ntc_title}", True))

    # Sort all events chronologically
    def sort_key(item):
        dt, raw_str, _, _ = item
        if dt:
            return dt
        return datetime.max

    db_events.sort(key=sort_key)

    formatted = []
    for dt, raw_str, title, has_time in db_events[:7]:
        if dt:
            day_abbr = dt.strftime('%a')[:2].lower()
            if not has_time or title.startswith("Flag: "):
                display_time = f"{day_abbr} {dt.day} {dt.strftime('%b').lower()}"
            else:
                display_time = f"{day_abbr} {dt.day} {dt.strftime('%b').lower()}, {dt.strftime('%H:%M')}"
        else:
            display_time = raw_str

        # If event name contains ":", move prefix to behind the date
        if ":" in title:
            prefix, event_name = title.split(":", 1)
            display_time = f"{display_time} {prefix.strip()}"
            title = event_name.strip()

        # Wilma sometimes repeats a course's base code after its detailed code.
        title = re.sub(r'\b([a-z]+\d+)(\.\d+)(?:\s*[,:;/-]?\s*\1\b)+', r'\1\2', title, flags=re.IGNORECASE)

        formatted.append([display_time.lower(), title.lower()])

    return formatted[:7] if formatted else [["No events", "found"]]

def generate_schedule(is_generic=False, mac_address=None, width=800, height=480, left_margin=50, right_margin=50, top_margin=0, bottom_margin=30, model=None):
    from src.spectra6 import is_spectra6_model
    
    # Detect Spectra 6 color display
    is_color = is_spectra6_model(model)
    
    # Spectra 6 color palette (RGB tuples)
    # black (0,0,0)=0, white (192,192,192)=1, yellow (192,192,0)=2, 
    # red (192,0,0)=3, blue (0,0,192)=4, green (0,192,0)=5
    SPECTRA6_BLACK = (0, 0, 0)
    SPECTRA6_WHITE = (192, 192, 192)
    SPECTRA6_YELLOW = (192, 192, 0)
    SPECTRA6_DARK_BLUE = (0, 0, 192)  # Dark blue for left column background
    
    # Grayscale palette for monochrome displays
    GRAY_BLACK = 0
    GRAY_DARK_GREY = 85
    GRAY_LIGHT_GREY = 170
    GRAY_WHITE = 255
    
    # Create canvas based on display type
    if is_color:
        canvas = Image.new('RGB', (width, height), SPECTRA6_WHITE)
        draw = ImageDraw.Draw(canvas)
    else:
        canvas = Image.new('L', (width, height), GRAY_WHITE)
        draw = ImageDraw.Draw(canvas)
    
    # Timezone handling
    device_tz = ZoneInfo("Europe/Helsinki")
    if mac_address:
        try:
            device_tz = ZoneInfo(get_device_timezone(DB_PATH, mac_address))
        except Exception as e:
            print(f"DEBUG: Error reading timezone for schedule: {e}")

    now = datetime.now(device_tz)
    
    # Margins from device configuration
    margin_left = left_margin
    margin_right = right_margin
    margin_bottom = bottom_margin
    margin_top = top_margin
    
    avail_width = width - margin_left - margin_right
    avail_height = height - margin_top - margin_bottom
    font_scale = max(0.5, min(2.0, avail_width / 700, avail_height / 450))
    
    col_spacing = 20
    col_width = (avail_width - (2 * col_spacing)) // 3
    
    # EXO2 font by Natanael Gama, through Google Fonts. SIL Open Font License, Version 1.1
    font_path = os.path.join(BASE_DIR, 'fonts', 'exo2-medium.ttf')
    font_bold_path = os.path.join(BASE_DIR, 'fonts', 'exo2-bold.ttf')
    try:
        font_main = ImageFont.truetype(font_path, round(20 * font_scale))
        font_main_bold = ImageFont.truetype(font_bold_path, round(20 * font_scale))
        font_small = ImageFont.truetype(font_path, round(16 * font_scale))
        font_large = ImageFont.truetype(font_path, round(28 * font_scale))
        font_large_bold = ImageFont.truetype(font_bold_path, round(28 * font_scale))
        font_medium_bold = ImageFont.truetype(font_bold_path, round(22 * font_scale))
        font_huge_bold = ImageFont.truetype(font_bold_path, round(40 * font_scale))
        font_xl = ImageFont.truetype(font_path, round(42 * font_scale))
    except:
        font_main = ImageFont.load_default()
        font_main_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_large = ImageFont.load_default()
        font_large_bold = ImageFont.load_default()
        font_medium_bold = ImageFont.load_default()
        font_huge_bold = ImageFont.load_default()
        font_xl = ImageFont.load_default()

    weather = get_weather(mac_address=mac_address)
    
    # Column 1 - Dark Blue background for Spectra 6, Light Grey for monochrome
    c1_x0 = margin_left
    c1_x1 = c1_x0 + col_width
    c1_fill = SPECTRA6_DARK_BLUE if is_color else GRAY_LIGHT_GREY
    outline_color = SPECTRA6_BLACK if is_color else GRAY_BLACK
    draw.rounded_rectangle([c1_x0, margin_top, c1_x1, height - margin_bottom], radius=15, fill=c1_fill, outline=outline_color, width=2)
    
    # Text in Column 1
    y = margin_top + 20
    
    # Use device name if available
    display_title = "TRMNL"
    if mac_address:
        try:
            name = get_device_name(DB_PATH, mac_address)
            if name:
                display_title = name
        except Exception:
            pass
            
    title_fill = SPECTRA6_WHITE if is_color else GRAY_WHITE
    draw.text((c1_x0 + 15, y), display_title, font=font_large, fill=title_fill)
    
    # Weather sub-box
    weather_y0 = margin_top + 60
    weather_y1 = weather_y0 + 120
    box_fill = SPECTRA6_WHITE if is_color else GRAY_WHITE
    box_outline = GRAY_DARK_GREY if not is_color else SPECTRA6_BLACK
    draw.rounded_rectangle([c1_x0 + 10, weather_y0, c1_x1 - 10, weather_y1], radius=10, fill=box_fill, outline=box_outline, width=2)
    
    y = weather_y0 + 10
    if weather:
        draw.text((c1_x0 + 20, y), f"{weather['current']:.0f}°", font=font_xl, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
        y += 55
        draw.text((c1_x0 + 20, y), f"Min: {weather['min']:.1f}°", font=font_main, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
        y += 25
        draw.text((c1_x0 + 20, y), f"Max: {weather['max']:.1f}°", font=font_main, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
        
        # Weather Icon centered in the sub-box horizontally
        icon_x = c1_x0 + 155
        icon_y = weather_y0 + 45
        import math
        if weather['will_rain']:
            # Rain Cloud - Dark Grey for both
            cloud_fill = GRAY_DARK_GREY if not is_color else SPECTRA6_WHITE  # White clouds on blue
            draw.ellipse([icon_x-20, icon_y-10, icon_x, icon_y+10], fill=cloud_fill)
            draw.ellipse([icon_x-10, icon_y-20, icon_x+10, icon_y], fill=cloud_fill)
            draw.ellipse([icon_x, icon_y-10, icon_x+20, icon_y+10], fill=cloud_fill)
            for i in range(3):
                ix = icon_x - 10 + (i*10)
                draw.line([ix, icon_y+15, ix-5, icon_y+25], fill=SPECTRA6_BLACK if is_color else GRAY_BLACK, width=2)
        else:
            # Sun - Yellow for Spectra 6, Dark Grey for monochrome
            sun_fill = SPECTRA6_YELLOW if is_color else GRAY_DARK_GREY
            draw.ellipse([icon_x-18, icon_y-18, icon_x+18, icon_y+18], fill=sun_fill, outline=SPECTRA6_BLACK if is_color else GRAY_BLACK, width=2)
            for i in range(8):
                angle = i * (math.pi / 4)
                x1 = icon_x + 20 * math.cos(angle)
                y1 = icon_y + 20 * math.sin(angle)
                x2 = icon_x + 30 * math.cos(angle)
                y2 = icon_y + 30 * math.sin(angle)
                draw.line([x1, y1, x2, y2], fill=SPECTRA6_BLACK if is_color else GRAY_BLACK, width=2)
    else:
        draw.text((c1_x0 + 20, y), "Weather N/A", font=font_main, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)

    # Boys' school start times box in Column 1
    app_y0 = height - margin_bottom - 160
    app_x0 = c1_x0 + 10
    app_x1 = c1_x1 - 10
    draw.rounded_rectangle([app_x0, app_y0, app_x1, height - margin_bottom - 10], radius=10, fill=box_fill, outline=box_outline, width=2)
    
    school_date = get_relevant_school_date(now)
    school_day_abbr = school_date.strftime('%a')[:2].lower()
    niiles_start, niiles_summary, niiles_location = get_first_school_event('niiles', target_date=school_date, tz_name=device_tz.key)
    lorens_start, lorens_summary, _ = get_first_school_event('lorens', target_date=school_date, tz_name=device_tz.key)

    niiles_display = f"{school_day_abbr} {niiles_start}" if niiles_start else "-"
    lorens_display = f"{school_day_abbr} {lorens_start}" if lorens_start else "-"
    
    # If both niiles and lorens are empty, try NTC calendar as fallback
    ntc_events = get_next_ntc_events()
    if niiles_display == "-" and lorens_display == "" and ntc_event:
        ntc_dt, ntc_title = ntc_event
        ntc_day_abbr = ntc_dt.strftime('%a')[:2].lower()
        ntc_date_str = ntc_dt.strftime('%d %b').lower()
        niiles_display = f"{ntc_day_abbr} {ntc_date_str}"
        lorens_display = f"{ntc_title.lower()}"
    
    niiles_room = room_from_location(niiles_location)
    niiles_header = " ".join(part for part in ["niiles", class_from_summary(niiles_summary), niiles_room] if part)
    draw.text((app_x0 + 10, app_y0 + 10), niiles_header, font=font_main, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
    draw.text((app_x0 + 15, app_y0 + 35), niiles_display, font=font_large, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
    draw.text((app_x0 + 10, app_y0 + 80), f"lorens {class_from_summary(lorens_summary)}", font=font_main, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
    draw.text((app_x0 + 15, app_y0 + 105), lorens_display, font=font_large, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)

    # NTC school box in Column 1 - between weather and boys' school
    ntc_y0 = weather_y1 + 12
    ntc_y1 = app_y0 - 10
    ntc_x0 = c1_x0 + 10
    ntc_x1 = c1_x1 - 10
    draw.rounded_rectangle([ntc_x0, ntc_y0, ntc_x1, ntc_y1], radius=10, fill=box_fill, outline=box_outline, width=2)
    
    ntc_dates = [
        ntc_dt.strftime('%d %b').lower()
        for ntc_dt, _ in ntc_events
    ]
    ntc_fill = SPECTRA6_BLACK if is_color else GRAY_BLACK
    dates_display = " / ".join(ntc_dates) if ntc_dates else "-"
    ntc_inner_width = ntc_x1 - ntc_x0 - 16
    date_font_size = max(12, font_large.size)
    date_font = ImageFont.truetype(font_path, date_font_size)
    date_bbox = draw.textbbox((0, 0), dates_display, font=date_font)
    while date_bbox[2] - date_bbox[0] > ntc_inner_width and date_font_size > 12:
        date_font_size -= 1
        date_font = ImageFont.truetype(font_path, date_font_size)
        date_bbox = draw.textbbox((0, 0), dates_display, font=date_font)
    date_height = date_bbox[3] - date_bbox[1]
    content_y = ntc_y0 + max(4, (ntc_y1 - ntc_y0 - date_height) // 2)
    draw.text(
        (ntc_x0 + (ntc_x1 - ntc_x0 - (date_bbox[2] - date_bbox[0])) // 2, content_y),
        dates_display,
        font=date_font,
        fill=ntc_fill,
    )

    # Column 2 - White background, using the space formerly occupied by the slogan panel
    c2_x0 = c1_x1 + col_spacing
    c2_x1 = width - margin_right
    c2_y0 = margin_top
    c2_y1 = height - margin_bottom
    draw.rounded_rectangle([c2_x0, c2_y0, c2_x1, c2_y1], radius=15, fill=box_fill, outline=SPECTRA6_BLACK if is_color else GRAY_BLACK, width=2)
    
    # Clip text rendering to inner panel boundaries with a WHITE background
    c2_inner = (c2_x0 + 4, c2_y0 + 4, c2_x1 - 4, c2_y1 - 4)
    if is_color:
        c2_layer = Image.new('RGB', (width, height), SPECTRA6_WHITE)
        c2_draw = ImageDraw.Draw(c2_layer)
    else:
        c2_layer = Image.new('L', (width, height), GRAY_WHITE)
        c2_draw = ImageDraw.Draw(c2_layer)

    events = get_events(is_generic, mac_address=mac_address)
    header_y = margin_top + 12
    date_time = now.strftime('%a %d %b %H:%M')
    c2_fill = SPECTRA6_BLACK if is_color else GRAY_BLACK
    c2_draw.text((c2_x0 + 15, header_y), date_time, font=font_main_bold, fill=c2_fill)

    battery_percent = None
    if mac_address:
        try:
            voltage = float(get_device_field(DB_PATH, mac_address, 'battery_voltage'))
            if voltage > 0:
                battery_percent = round(max(0, min(100, (voltage - 3.0) / 1.2 * 100)))
        except (TypeError, ValueError):
            pass
    if battery_percent is not None:
        battery_display = f"Battery {battery_percent}%"
        battery_width = c2_draw.textbbox((0, 0), battery_display, font=font_main)[2]
        c2_draw.text((c2_x1 - 15 - battery_width, header_y), battery_display, font=font_main, fill=c2_fill)

    y = margin_top + 60
    for date, desc in events:
        c2_draw.text((c2_x0 + 15, y), date, font=font_small, fill=GRAY_DARK_GREY if not is_color else SPECTRA6_BLACK)
        c2_draw.text((c2_x0 + 15, y + 20), desc, font=font_medium_bold, fill=SPECTRA6_BLACK if is_color else GRAY_BLACK)
        y += 60

    # Composite clipped white box back onto canvas
    c2_crop = c2_layer.crop(c2_inner)
    canvas.paste(c2_crop, c2_inner)

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
