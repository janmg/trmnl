import io
import os
import qrcode
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

def generate_testcard(mac_address=None, device_info=None):
    # 800x480 Grayscale TV Test Card
    width, height = 800, 480
    canvas = Image.new('L', (width, height), 255) # White background
    draw = ImageDraw.Draw(canvas)
    
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    try:
        info_font = ImageFont.truetype(os.path.join(BASE_DIR, "fonts", "exo2-medium.ttf"), 16)
    except Exception:
        info_font = font

    try:
        compass_font = ImageFont.truetype(os.path.join(BASE_DIR, "fonts", "exo2-bold.ttf"), 30)
    except Exception:
        compass_font = font

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

    # Circle shifted right with explicit vertical bounds: top=120px, bottom=390px.
    cx, cy = (width // 2) + 170, 255
    radius = 135
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=BLACK, width=2)

    # If Mac Address provided, add a small QR code in the middle
    if mac_address and str(mac_address).strip():
        base_url = "https://trmnl.janmg.com" # Hardcoded for now, or could be passed
        reg_url = f"{base_url}/configure?mac_address={mac_address}"
        
        try:
            qr = qrcode.QRCode(version=1, box_size=4, border=0)
            qr.add_data(reg_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert('L')

            qr_w, qr_h = qr_img.size
            qr_img = qr_img.resize((qr_w + 30, qr_h + 30), Image.Resampling.NEAREST)
            qr_w, qr_h = qr_img.size
            # Center inside the circle
            canvas.paste(qr_img, (cx - qr_w // 2, cy - qr_h // 2))
        except Exception as e:
            # Fallback text if QR fails
            draw.text((cx - 40, cy - 10), "QR Error", font=font, fill=BLACK)
    
    # Device Info Column (Left Side)
    if device_info:
        info_x = 110
        info_y = cy - 110
        spacing = 15
        draw.text((info_x, info_y), f"NAME: {device_info.get('name', '-')}" , font=info_font, fill=BLACK)
        draw.text((info_x, info_y + spacing), f"MAC:  {device_info.get('mac', '-')}" , font=info_font, fill=BLACK)
        draw.text((info_x, info_y + spacing*2), f"IP:   {device_info.get('ip', '-')}" , font=info_font, fill=BLACK)
        draw.text((info_x, info_y + spacing*3), f"FW:   {device_info.get('fw', '-')}" , font=info_font, fill=BLACK)
        draw.text((info_x, info_y + spacing*4), f"BAT:  {device_info.get('battery', '-')}V" , font=info_font, fill=BLACK)
        draw.text((info_x, info_y + spacing*5), f"SSID: {device_info.get('ssid', '-')}" , font=info_font, fill=BLACK)

    # Compass Headings
    draw.text((cx - 10, cy - radius + 8), "N", font=compass_font, fill=BLACK)
    draw.text((cx - 10, cy + radius - 32), "S", font=compass_font, fill=BLACK)
    draw.text((cx + radius - 28, cy - 12), "E", font=compass_font, fill=BLACK)
    draw.text((cx - radius + 8, cy - 12), "W", font=compass_font, fill=BLACK)

    # Horizontal gray bars under the information box
    bars_x = 110
    bars_y = (cy - 60) + (15 * 6) + 15
    grad_w, grad_h = 260, 34

    # Floyd-Steinberg dithered gradient box (white -> black), above the 4-color bars
    fs_x = bars_x
    fs_y = bars_y - grad_h - 12
    fs_img = Image.new('L', (grad_w, grad_h), WHITE)

    # Start with smooth grayscale gradient
    for fy in range(grad_h):
        for fx in range(grad_w):
            v = int(255 - (fx / max(1, grad_w - 1)) * 255)
            fs_img.putpixel((fx, fy), v)

    # Floyd-Steinberg dithering to black/white
    for fy in range(grad_h):
        for fx in range(grad_w):
            old = fs_img.getpixel((fx, fy))
            new = WHITE if old >= 128 else BLACK
            fs_img.putpixel((fx, fy), new)
            err = old - new

            if fx + 1 < grad_w:
                n = fs_img.getpixel((fx + 1, fy)) + int(err * 7 / 16)
                fs_img.putpixel((fx + 1, fy), max(0, min(255, n)))
            if fy + 1 < grad_h:
                if fx > 0:
                    n = fs_img.getpixel((fx - 1, fy + 1)) + int(err * 3 / 16)
                    fs_img.putpixel((fx - 1, fy + 1), max(0, min(255, n)))
                n = fs_img.getpixel((fx, fy + 1)) + int(err * 5 / 16)
                fs_img.putpixel((fx, fy + 1), max(0, min(255, n)))
                if fx + 1 < grad_w:
                    n = fs_img.getpixel((fx + 1, fy + 1)) + int(err * 1 / 16)
                    fs_img.putpixel((fx + 1, fy + 1), max(0, min(255, n)))

    canvas.paste(fs_img, (fs_x, fs_y))
    draw.rectangle([fs_x, fs_y, fs_x + grad_w, fs_y + grad_h], outline=BLACK)

    block_w, block_h = grad_w // 4, grad_h
    colors = [BLACK, DARK_GREY, LIGHT_GREY, WHITE]
    for i, color in enumerate(colors):
        bx = bars_x + i * block_w
        draw.rectangle([bx, bars_y, bx + block_w, bars_y + block_h], fill=color, outline=BLACK)

    # Fully dithered horizontal gradient bar: black (left) to white (right)
    grad_x = bars_x
    grad_y = bars_y + block_h + 14
    bayer4 = [
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5],
    ]
    for gy in range(grad_h):
        for gx in range(grad_w):
            desired = gx / max(1, grad_w - 1)
            threshold = (bayer4[gy % 4][gx % 4] + 0.5) / 16.0
            p_val = WHITE if desired > threshold else BLACK
            canvas.putpixel((grad_x + gx, grad_y + gy), p_val)
    draw.rectangle([grad_x, grad_y, grad_x + grad_w, grad_y + grad_h], outline=BLACK)

    # Save to buffer
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
