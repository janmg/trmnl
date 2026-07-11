# -*- coding: utf-8 -*-
import io
import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

def get_weather():
    """Fetch weather data for Helsinki from FMI."""
    url = "https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature&storedquery_id=fmi::forecast::harmonie::surface::point::simple&place=helsinki&"
    
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
        # EET/EEST Offset (Helsinki). Currently UTC+3 for July.
        local_now = now + timedelta(hours=3)
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

def get_events(is_generic=False):
    """Load events from events.json or return generic fallback."""
    if is_generic:
        return [
            ["1 oct, 12:00", "Miina Sillanpää Day"],
            ["10 oct, 12:00", "Aleksis Kivi"],
            ["24 October", "United Nations Day"],
            ["6 Nov, 12:00", "Swedish Heritage Day"],
            ["20 Nov, 12:00", "Children's Rights"],
            ["8 Dec, 12:00", "Jean Sibelius"]
        ]

    json_path = os.path.join(BASE_DIR, 'events.json')
    try:
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading events.json: {e}")
    
    return [["No events", "found"]]

def generate_schedule(is_generic=False):
    # 800x480 Grayscale
    width, height = 800, 480
    # 4 tones: black, dark grey, light grey, white
    BLACK = 0
    DARK_GREY = 85
    LIGHT_GREY = 170
    WHITE = 255

    canvas = Image.new('L', (width, height), WHITE)
    draw = ImageDraw.Draw(canvas)
    
    # Margins: 50px left/right, 60px bottom (increased to leave more whitespace)
    margin_left = 50
    margin_right = 50
    margin_bottom = 60
    margin_top = 30
    
    avail_width = width - margin_left - margin_right
    avail_height = height - margin_top - margin_bottom
    
    col_spacing = 20
    col_width = (avail_width - (2 * col_spacing)) // 3
    
    # EXO2 font by Natanael Gama, through Google Fonts. SIL Open Font License, Version 1.1
    font_path = os.path.join(BASE_DIR, 'fonts', 'exo2-medium.ttf')
    font_bold_path = os.path.join(BASE_DIR, 'fonts', 'exo2-bold.ttf')
    try:
        font_main = ImageFont.truetype(font_path, 20)
        font_small = ImageFont.truetype(font_path, 16)
        font_large = ImageFont.truetype(font_path, 28)
        font_large_bold = ImageFont.truetype(font_bold_path, 28)
        font_medium_bold = ImageFont.truetype(font_bold_path, 22)
        font_huge_bold = ImageFont.truetype(font_bold_path, 48)
        font_xl = ImageFont.truetype(font_path, 42)
    except:
        font_main = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_large = ImageFont.load_default()
        font_large_bold = ImageFont.load_default()
        font_medium_bold = ImageFont.load_default()
        font_huge_bold = ImageFont.load_default()
        font_xl = ImageFont.load_default()

    weather = get_weather()
    
    # Column 1 - Light Grey background
    c1_x0 = margin_left
    c1_x1 = c1_x0 + col_width
    draw.rounded_rectangle([c1_x0, margin_top, c1_x1, height - margin_bottom], radius=15, fill=LIGHT_GREY, outline=BLACK, width=2)
    
    # Text in Column 1
    y = margin_top + 20
    draw.text((c1_x0 + 15, y), "Helsinki", font=font_large, fill=BLACK)
    
    # Weather sub-box
    weather_y0 = margin_top + 60
    weather_y1 = weather_y0 + 150
    draw.rounded_rectangle([c1_x0 + 10, weather_y0, c1_x1 - 10, weather_y1], radius=10, fill=WHITE, outline=DARK_GREY, width=2)
    
    y = weather_y0 + 10
    if weather:
        draw.text((c1_x0 + 20, y), f"{weather['current']:.0f}°", font=font_xl, fill=BLACK)
        y += 55
        draw.text((c1_x0 + 20, y), f"Min: {weather['min']:.1f}°", font=font_main, fill=BLACK)
        y += 25
        draw.text((c1_x0 + 20, y), f"Max: {weather['max']:.1f}°", font=font_main, fill=BLACK)
        
        # Weather Icon centered in the sub-box horizontally
        icon_x = c1_x0 + 155
        icon_y = weather_y0 + 45
        import math
        if weather['will_rain']:
            # Rain Cloud
            draw.ellipse([icon_x-20, icon_y-10, icon_x, icon_y+10], fill=DARK_GREY)
            draw.ellipse([icon_x-10, icon_y-20, icon_x+10, icon_y], fill=DARK_GREY)
            draw.ellipse([icon_x, icon_y-10, icon_x+20, icon_y+10], fill=DARK_GREY)
            for i in range(3):
                ix = icon_x - 10 + (i*10)
                draw.line([ix, icon_y+15, ix-5, icon_y+25], fill=BLACK, width=2)
        else:
            # Sun
            draw.ellipse([icon_x-18, icon_y-18, icon_x+18, icon_y+18], outline=BLACK, width=2)
            for i in range(8):
                angle = i * (math.pi / 4)
                x1 = icon_x + 20 * math.cos(angle)
                y1 = icon_y + 20 * math.sin(angle)
                x2 = icon_x + 30 * math.cos(angle)
                y2 = icon_y + 30 * math.sin(angle)
                draw.line([x1, y1, x2, y2], fill=BLACK, width=2)
    else:
        draw.text((c1_x0 + 20, y), "Weather N/A", font=font_main, fill=BLACK)

    # Appliances box in Column 1 - Colored
    app_y0 = height - margin_bottom - 160
    app_x0 = c1_x0 + 10
    app_x1 = c1_x1 - 10
    draw.rounded_rectangle([app_x0, app_y0, app_x1, height - margin_bottom - 10], radius=10, fill=WHITE, outline=DARK_GREY, width=2)
    
    draw.text((app_x0 + 10, app_y0 + 10), "dishwasher", font=font_main, fill=BLACK)
    draw.text((app_x0 + 15, app_y0 + 35), "clean", font=font_large, fill=BLACK)
    draw.text((app_x0 + 10, app_y0 + 80), "washingmachine", font=font_main, fill=BLACK)
    draw.text((app_x0 + 15, app_y0 + 105), "empty", font=font_large, fill=BLACK)

    # Column 2 - White background
    c2_x0 = c1_x1 + col_spacing
    c2_x1 = c2_x0 + col_width
    draw.rounded_rectangle([c2_x0, margin_top, c2_x1, height - margin_bottom], radius=15, fill=WHITE, outline=BLACK, width=2)
    
    events = get_events(is_generic)
    y = margin_top + 20
    for date, desc in events:
        draw.text((c2_x0 + 15, y), date, font=font_small, fill=DARK_GREY)
        draw.text((c2_x0 + 15, y + 20), desc, font=font_medium_bold, fill=BLACK)
        y += 60

    # Column 3 - Dark Grey background
    c3_x0 = c2_x1 + col_spacing
    c3_x1 = c3_x0 + col_width
    draw.rounded_rectangle([c3_x0, margin_top, c3_x1, height - margin_bottom], radius=15, fill=DARK_GREY, outline=BLACK, width=2)
    
    # Fixed text in Column 3 - White text on Dark Grey
    lines = ["HOME", "IS", "WHERE", "YOUR", "WIFI IS"]
    y = margin_top + 40
    for line in lines:
        # Simple horizontal centering
        try:
            bbox = draw.textbbox((0, 0), line, font=font_huge_bold)
            tw = bbox[2] - bbox[0]
            tx = c3_x0 + (col_width - tw) // 2
        except:
            tx = c3_x0 + 10
        draw.text((tx, y), line, font=font_huge_bold, fill=WHITE)
        y += 65

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
