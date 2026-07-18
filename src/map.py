import math
import io
import os
import requests
from PIL import Image

from src.db import get_device_location

MAPTILER_API_KEY = "KQgAXC6ARiLV7SshYzas"
# Using backdrop-v4 as it's cleaner for E-ink
MAP_STYLE = "backdrop-v4"
WIDTH = 800
HEIGHT = 480
CACHE_DIR = "content/cache/maps"
DEFAULT_ZOOM = 13
MAP_RENDER_VERSION = 2

def generate_map(db_path=None, mac_address=None):
    # Ensure cache directory exists
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)

    # Default coordinates (e.g. Helsinki center)
    lat, lon = 60.1699, 24.9384
    zoom = DEFAULT_ZOOM
    
    if db_path and mac_address:
        try:
            location = get_device_location(db_path, mac_address)
            if location:
                parts = location.split(',')
                if len(parts) == 2:
                    lat = float(parts[0])
                    lon = float(parts[1])
        except Exception as e:
            print(f"DEBUG: Error reading location for map: {e}")

    # Check cache first
    # Include the render version so existing oversized cache files are not
    # reused after changing the e-paper palette/compression settings.
    cache_filename = f"map_v{MAP_RENDER_VERSION}_{lat}_{lon}_{zoom}.png"
    cache_path = os.path.join(CACHE_DIR, cache_filename)

    if os.path.exists(cache_path):
        print(f"DEBUG: Serving map from cache: {cache_path}")
        with open(cache_path, 'rb') as f:
            return io.BytesIO(f.read())

    # Calculate world coordinates in tiles
    n = 2.0 ** zoom
    world_x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    world_y = (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n

    # Calculate tile range based on standard 512px tiles mapping
    min_tx = int((world_x - (WIDTH / 2 / 512)) // 1)
    max_tx = int((world_x + (WIDTH / 2 / 512)) // 1)
    min_ty = int((world_y - (HEIGHT / 2 / 512)) // 1)
    max_ty = int((world_y + (HEIGHT / 2 / 512)) // 1)

    # Create canvas (each tile is 512x512)
    canvas_w = (max_tx - min_tx + 1) * 512
    canvas_h = (max_ty - min_ty + 1) * 512
    canvas = Image.new('RGB', (canvas_w, canvas_h), color=(255, 255, 255))

    # Download and stitch tiles
    for tx in range(min_tx, max_tx + 1):
        for ty in range(min_ty, max_ty + 1):
            url = f"https://api.maptiler.com/maps/{MAP_STYLE}/{zoom}/{tx}/{ty}.png?key={MAPTILER_API_KEY}"
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    tile_img = Image.open(io.BytesIO(resp.content)).convert('RGB')
                    canvas.paste(tile_img, ((tx - min_tx) * 512, (ty - min_ty) * 512))
            except Exception as e:
                print(f"DEBUG: Map tile error {tx}/{ty}: {e}")

    # Crop the window from the stitched tiles
    crop_x = int((world_x - min_tx) * 512 - (WIDTH // 2))
    crop_y = int((world_y - min_ty) * 512 - (HEIGHT // 2))
    img = canvas.crop((crop_x, crop_y, crop_x + WIDTH, crop_y + HEIGHT))
    
    # The non-PSRAM firmware rejects images over 90,000 bytes. Four grayscale
    # levels are sufficient for the e-paper map and produce a substantially
    # smaller PNG than the previous 16-level output.
    img = img.convert('L')
    img = img.quantize(colors=4, method=Image.Quantize.MEDIANCUT)

    # Save to cache
    try:
        img.save(cache_path, format='PNG', optimize=True)
        print(f"DEBUG: Saved map to cache: {cache_path}")
    except Exception as e:
        print(f"DEBUG: Failed to save map to cache: {e}")

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf
