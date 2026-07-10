# -*- coding: utf-8 -*-
import io
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont

def generate_clock():
    # Target dimensions: 800 x 480
    width, height = 800, 480
    
    # White background
    img = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(img)
    
    # Load the same font as titlecard.py
    try:
        # Using a very large size for the clock
        font_size = 180
        font_date_size = 40
        font = ImageFont.truetype("fonts/dejavuserif-bold.ttf", font_size)
        font_date = ImageFont.truetype("fonts/dejavuserif-bold.ttf", font_date_size)
    except:
        font = ImageFont.load_default()
        font_date = ImageFont.load_default()
    
    # Use EET timezone (UTC+2 or UTC+3 for EEST)
    # Since we don't have pytz, we'll use a standard Helsinki offset (UTC+3 for July)
    # In winter this would be UTC+2.
    offset = timedelta(hours=3)
    now = datetime.now(timezone.utc) + offset
    
    current_time = now.strftime("%H:%M")
    # Get fully written out date (e.g., "Friday, October 10, 2026")
    current_date = now.strftime("%A, %B %d, %Y")
    
    # Calculate text position for time (centered)
    bbox_time = draw.textbbox((0, 0), current_time, font=font)
    time_w = bbox_time[2] - bbox_time[0]
    time_h = bbox_time[3] - bbox_time[1]
    
    # Calculate text position for date (centered)
    bbox_date = draw.textbbox((0, 0), current_date, font=font_date)
    date_w = bbox_date[2] - bbox_date[0]
    date_h = bbox_date[3] - bbox_date[1]
    
    # Combined vertical space
    padding = 20
    total_h = time_h + date_h + padding
    
    # Center vertically and horizontally
    visible_v_center = 225
    
    time_x = (width - time_w) // 2
    time_y = visible_v_center - (total_h // 2) - 20
    
    date_x = (width - date_w) // 2
    date_y = time_y + time_h + padding + 30 # Adjusting for baseline
    
    # Draw text in black
    draw.text((time_x, time_y), current_time, font=font, fill=0)
    draw.text((date_x, date_y), current_date, font=font_date, fill=0)
    
    # Convert to buffer
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf
