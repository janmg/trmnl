import requests
import io
from PIL import Image, ImageOps
from datetime import datetime, timedelta

def generate_fmi(width=800, height=480, left_margin=0, right_margin=0, top_margin=0, bottom_margin=0):
    try:
        # Finland is typically EEST (UTC+3) in July
        # The URL uses UTC. We need to find the latest 15-minute mark in UTC.
        # Images are refreshed every 15 minutes (00, 15, 30, 45)
        now_utc = datetime.utcnow()
        
        # Round down to the nearest 15 minutes
        minute = (now_utc.minute // 15) * 15
        latest_time = now_utc.replace(minute=minute, second=0, microsecond=0)

        # https://en.ilmatieteenlaitos.fi/rainmap        
        # Try the most recent, then fallback a few steps if content isn't ready yet
        # Weather radar processing can have a few minutes delay
        base_url = "https://cdn.fmi.fi/weather-radar/observations/flash/etela-suomi_500x500/HAV_{time}_DBZ.png"
        
        img = None
        for i in range(4):  # Try current and 3 previous slots if needed (1h window)
            test_time = latest_time - timedelta(minutes=i*15)
            time_str = test_time.strftime("%Y%m%d%H%M")
            url = base_url.format(time=time_str)
            
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content))
                    print(f"INFO: Successfully fetched FMI radar for {time_str}")
                    break
                else:
                    print(f"DEBUG: FMI radar {time_str} not found (Status {response.status_code}), trying 15m earlier...")
            except Exception as e:
                print(f"DEBUG: FMI fetch error for {time_str}: {e}")
                continue

        if not img:
            return None

        # Source is 500x500. User wants the bottom 300px (500x300 region),
        # scaled to fit the visible area (canvas minus margins).
        # Visible area is the canvas minus all margins
        visible_width = width - left_margin - right_margin
        visible_height = height - top_margin - bottom_margin
        
        # Crop the bottom 300px from the original 500x500 image
        cropped = img.crop((0, 200, 500, 500))
        
        # Scale the 500x300 crop to fit the visible area, preserving aspect ratio
        scale_w = visible_width / 500
        scale_h = visible_height / 300
        scale = min(scale_w, scale_h)
        new_width = int(500 * scale)
        new_height = int(300 * scale)
        cropped = cropped.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Create the full canvas with white margins
        canvas = Image.new('RGB', (width, height), (255, 255, 255))
        
        # Paste visible area at left_margin, top_margin
        canvas.paste(cropped, (left_margin, top_margin))
        
        buf = io.BytesIO()
        canvas.save(buf, format='PNG')
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Error generating FMI radar: {e}")
        return None
