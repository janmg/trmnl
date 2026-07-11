import io
import qrcode
from PIL import Image, ImageDraw, ImageFont

def generate_testcard(mac_address=None, device_info=None):
    # 800x480 Grayscale TV Test Card
    width, height = 800, 480
    canvas = Image.new('L', (width, height), 255) # White background
    draw = ImageDraw.Draw(canvas)
    
    # Grid lines (Light Grey) for full screen alignment
    grid_size = 40
    for x in range(0, width, grid_size):
        draw.line([(x, 0), (x, height)], fill=230)
    for y in range(0, height, grid_size):
        draw.line([(0, y), (width, y)], fill=230)

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    # 4 tones: black, dark grey, light grey, white
    BLACK = 0
    DARK_GREY = 85
    LIGHT_GREY = 170
    WHITE = 255
    
    # Border
    draw.rectangle([0, 0, width-1, height-1], outline=BLACK)

    # Ruler / Grid ticks (Discrete instead of solid bar)
    # Every 10px a small line, every 50px a longer line with text
    
    # Top Ruler (Extended upwards/longer to handle bevel)
    for x in range(0, width, 10):
        is_major = (x % 50 == 0)
        length = 100 if is_major else 50 # Longer than bottom
        draw.line([(x, 0), (x, length)], fill=BLACK)
        if is_major:
            draw.text((x + 2, length + 2), str(x), font=font, fill=BLACK)

    # Bottom Ruler
    for x in range(0, width, 10):
        is_major = (x % 50 == 0)
        length = 60 if is_major else 30
        draw.line([(x, height - 1), (x, height - 1 - length)], fill=BLACK)
        if is_major:
            draw.text((x + 2, height - 1 - length - 15), str(x), font=font, fill=BLACK)

    # Left Ruler
    for y in range(0, height, 10):
        is_major = (y % 50 == 0)
        length = 60 if is_major else 30
        draw.line([(0, y), (length, y)], fill=BLACK)
        if is_major:
            draw.text((length + 5, y - 5), str(y), font=font, fill=BLACK)

    # Right Ruler
    for y in range(0, height, 10):
        is_major = (y % 50 == 0)
        length = 60 if is_major else 30
        draw.line([(width - 1, y), (width - 1 - length, y)], fill=BLACK)
        if is_major:
            draw.text((width - 1 - length - 30, y - 5), str(y), font=font, fill=BLACK)

    # Center Circle
    cx, cy = width // 2, height // 2
    radius = 150
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=BLACK, width=2)

    # If Mac Address provided, add a small QR code in the middle
    if mac_address and str(mac_address).strip():
        base_url = "https://trmnl.janmg.com" # Hardcoded for now, or could be passed
        reg_url = f"{base_url}/configure?mac_address={mac_address}"
        
        try:
            qr = qrcode.QRCode(version=1, box_size=4, border=2)
            qr.add_data(reg_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert('L')
            
            qr_w, qr_h = qr_img.size
            # Center inside the circle
            canvas.paste(qr_img, (cx - qr_w // 2, cy - qr_h // 2))
        except Exception as e:
            # Fallback text if QR fails
            draw.text((cx - 40, cy - 10), "QR Error", font=font, fill=BLACK)
    
    # Device Info Column (Left Side)
    if device_info:
        info_x = 110
        info_y = cy - 60
        spacing = 15
        draw.text((info_x, info_y), f"NAME: {device_info.get('name', '-')}", font=font, fill=BLACK)
        draw.text((info_x, info_y + spacing), f"MAC:  {device_info.get('mac', '-')}", font=font, fill=BLACK)
        draw.text((info_x, info_y + spacing*2), f"IP:   {device_info.get('ip', '-')}", font=font, fill=BLACK)
        draw.text((info_x, info_y + spacing*3), f"FW:   {device_info.get('fw', '-')}", font=font, fill=BLACK)
        draw.text((info_x, info_y + spacing*4), f"BAT:  {device_info.get('battery', '-')}V", font=font, fill=BLACK)
        draw.text((info_x, info_y + spacing*5), f"SSID: {device_info.get('ssid', '-')}", font=font, fill=BLACK)

    # Compass Headings
    draw.text((cx - 5, cy - radius + 10), "N", font=font, fill=BLACK)
    draw.text((cx - 5, cy + radius - 25), "S", font=font, fill=BLACK)
    draw.text((cx + radius - 20, cy - 5), "E", font=font, fill=BLACK)
    draw.text((cx - radius + 10, cy - 5), "W", font=font, fill=BLACK)

    # Right side: block with dithering from light to dark
    dither_w, dither_h = 60, 200
    dx0, dy0 = cx + radius + 15, cy - (dither_h // 2)
    bayer = [[0, 2], [3, 1]]
    for dy in range(dither_h):
        brightness = (dither_h - dy) / dither_h  # Top is white (1.0), bottom is black (0.0)
        val_scaled = brightness * 3
        base_level = int(val_scaled)
        fraction = val_scaled - base_level
        for dx in range(dither_w):
            threshold = bayer[dy % 2][dx % 2] / 4.0
            level_idx = min(3, base_level + 1) if fraction > threshold else base_level
            p_val = [0, 85, 170, 255][level_idx]
            canvas.putpixel((dx0 + dx, dy0 + dy), p_val)

    # Right side: 4 solid color blocks (moved next to dithering)
    block_w, block_h = 40, 50
    colors = [BLACK, DARK_GREY, LIGHT_GREY, WHITE]
    bx = dx0 + dither_w + 10
    start_y = cy - (len(colors) * block_h // 2)
    for i, color in enumerate(colors):
        by = start_y + (i * block_h)
        draw.rectangle([bx, by, bx + block_w, by + block_h], fill=color, outline=BLACK)

    # Save to buffer
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
