import io
from PIL import Image, ImageDraw, ImageFont

def generate_testcard():
    # 800x480 Grayscale TV Test Card
    width, height = 800, 480
    canvas = Image.new('L', (width, height), 255) # White background
    draw = ImageDraw.Draw(canvas)
    
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    # 4 tones: black, dark grey, light grey, white
    BLACK = 0
    DARK_GREY = 85
    LIGHT_GREY = 170
    WHITE = 255
    
    # Ruler on all 4 sides (left, right, top, bottom)
    # Every pixel 40px line, every 10 pixels 80px line with counter
    
    # Top and Bottom Rulers
    for x in range(width):
        is_major = (x % 10 == 0)
        length = 80 if is_major else 40
        draw.line([(x, 0), (x, length)], fill=BLACK)
        draw.line([(x, height - 1), (x, height - 1 - length)], fill=BLACK)
        if is_major:
            draw.text((x + 2, 85), str(x), font=font, fill=BLACK)
            draw.text((x + 2, height - 100), str(x), font=font, fill=BLACK)

    # Left and Right Rulers
    for y in range(height):
        is_major = (y % 10 == 0)
        length = 80 if is_major else 40
        draw.line([(0, y), (length, y)], fill=BLACK)
        draw.line([(width - 1, y), (width - 1 - length, y)], fill=BLACK)
        if is_major:
            draw.text((85, y + 2), str(y), font=font, fill=BLACK)
            draw.text((width - 120, y + 2), str(y), font=font, fill=BLACK)

    # Center Circle
    cx, cy = width // 2, height // 2
    radius = 150
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=BLACK, width=2)

    # Compass Headings
    draw.text((cx - 5, cy - radius + 10), "N", font=font, fill=BLACK)
    draw.text((cx - 5, cy + radius - 25), "S", font=font, fill=BLACK)
    draw.text((cx + radius - 20, cy - 5), "E", font=font, fill=BLACK)
    draw.text((cx - radius + 10, cy - 5), "W", font=font, fill=BLACK)

    # Left side: 4 blocks with different grey scales
    block_w, block_h = 50, 40
    colors = [BLACK, DARK_GREY, LIGHT_GREY, WHITE]
    start_y = cy - (len(colors) * block_h // 2)
    for i, color in enumerate(colors):
        bx = cx - radius - 70
        by = start_y + (i * block_h)
        draw.rectangle([bx, by, bx + block_w, by + block_h], fill=color, outline=BLACK)

    # Right side: block with dithering from light to dark
    dither_w, dither_h = 60, 200
    dx0, dy0 = cx + radius + 20, cy - (dither_h // 2)
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

    # Save to buffer
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
