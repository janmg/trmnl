import requests
import io
from PIL import Image, ImageOps
from datetime import datetime, timedelta

def generate_fmi():
    try:
        # Finland is typically EEST (UTC+3) in July
        # The URL uses UTC. We need to find the latest 15-minute mark in UTC.
        # Images are refreshed every 15 minutes (00, 15, 30, 45)
        now_utc = datetime.utcnow()
        
        # Round down to the nearest 15 minutes
        minute = (now_utc.minute // 15) * 15
        latest_time = now_utc.replace(minute=minute, second=0, microsecond=0)
        
        # Try the most recent, then fallback a few steps if content isn't ready yet
        # Weather radar processing can have a few minutes delay
        base_url = "https://cdn.fmi.fi/weather-radar/observations/flash/etela-suomi_500x500/HAV_{time}_DBZ.png"
        
        img = None
        for i in range(5):  # Try current and 4 previous slots if needed (1h 15m window)
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

        # Source is 500x500. User wants to zoom on the "left bottom 60%".
        # 60% of 500 is 300 pixels.
        # To make 300 pixels fill our 700px visible width, we need a scale of 700/300 = 2.333
        scale = 700 / 300
        new_size = int(500 * scale) # ~1166
        zoomed_img = img.resize((new_size, new_size), Image.Resampling.LANCZOS)
        
        # Crop the bottom-left 700x450 from this zoomed image, but move 300px higher
        # Bottom was: new_size. New bottom: new_size - 300
        left = 0
        bottom = new_size - 300
        right = 700
        top = bottom - 450
        cropped = zoomed_img.crop((left, top, right, bottom))
        
        # Create the full 800x480 canvas with white margins
        canvas = Image.new('RGB', (800, 480), (255, 255, 255))
        
        # Paste visible area (50px left margin, 0px top margin)
        canvas.paste(cropped, (50, 0))
        
        # Convert to grayscale
        grayscale = canvas.convert('L')
        
        # User asked for "4 grey scales"
        # We can achieve this by quantizing to 4 colors (2-bit)
        # Using posterize(2) gives 2^2 = 4 levels.
        four_scales = ImageOps.posterize(grayscale, 2)
        
        buf = io.BytesIO()
        four_scales.save(buf, format='PNG')
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Error generating FMI radar: {e}")
        return None
