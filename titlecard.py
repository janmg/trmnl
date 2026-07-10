import io
import os
from PIL import Image, ImageDraw, ImageFont, ImageOps

def generate_titlecard(text):
    # Target dimensions: 800 x 480
    width, height = 800, 480
    
    # Try to load the background image from content/titlecard.png
    bg_path = os.path.join(os.path.dirname(__file__), 'content', 'titlecard.png')
    if os.path.exists(bg_path):
        # Convert to 'L' (Grayscale) immediately so drawing white (255) works correctly
        img = Image.open(bg_path).convert('L')
        # If it's not the right size, resize it
        if img.size != (width, height):
            img = img.resize((width, height))
    else:
        # Create a black background if the image doesn't exist
        img = Image.new('L', (width, height), 0)

    draw = ImageDraw.Draw(img)
    
    # Border color (Grayscale white)
    border_color = 255 
    
    # Helper to keep draw_ornate_corner available but we're using the image now
    def draw_ornate_corner(cx, cy, quadrant):
        # cx, cy are the corner vertices (70, 20), (730, 20), etc.
        # quadrant: 1 (top-right), 2 (top-left), 3 (bottom-left), 4 (bottom-right)
        
        # Main scroll flourish
        angles = {1: (270, 360), 2: (180, 270), 3: (90, 180), 4: (0, 90)}
        start, end = angles[quadrant]
        
        # Large outer scroll
        draw.arc([cx-60, cy-60, cx+60, cy+60], start, end, fill=border_color, width=4)
        # Counter-sweep scroll
        draw.arc([cx-40, cy-40, cx+40, cy+40], start+20, end-20, fill=border_color, width=3)
        # Small inner tight curl
        draw.arc([cx-20, cy-20, cx+20, cy+20], start+40, end-40, fill=border_color, width=2)
        
        # Decorative dots (vignette style)
        dot_offset = 70
        if quadrant == 1: # TR
            draw.ellipse([cx-dot_offset-5, cy+5, cx-dot_offset+5, cy+15], fill=border_color)
            draw.ellipse([cx-5, cy+dot_offset-5, cx+5, cy+dot_offset+5], fill=border_color)
        elif quadrant == 2: # TL
            draw.ellipse([cx+dot_offset-5, cy+5, cx+dot_offset+5, cy+15], fill=border_color)
            draw.ellipse([cx-5, cy+dot_offset-5, cx+5, cy+dot_offset+5], fill=border_color)
        elif quadrant == 3: # BL
            draw.ellipse([cx+dot_offset-5, cy-15, cx+dot_offset+5, cy-5], fill=border_color)
            draw.ellipse([cx-5, cy-dot_offset-5, cx+5, cy-dot_offset+5], fill=border_color)
        elif quadrant == 4: # BR
            draw.ellipse([cx-dot_offset-5, cy-15, cx-dot_offset+5, cy-5], fill=border_color)
            draw.ellipse([cx-5, cy-dot_offset-5, cx+5, cy-dot_offset+5], fill=border_color)

    # Note: Corners and connecting lines were previously drawn here:
    # draw_ornate_corner(margin_x, margin_top, 2) # TL
    # etc.
    # We now skip them in favor of the background image provided by the user.

    # Text rendering
    try:
        # Much bigger font size as requested (64px)
        font_size = 34
        font = ImageFont.truetype("fonts/dejavuserif-bold.ttf", font_size)
    except:
        font = ImageFont.load_default()

    # Wrap text and center it
    lines = []
    # Maximum width for text (Visible area minus some padding)
    # Visible width is approx 700px (50px margins). 
    # Use 640px to leave space for the ornate border/frame.
    max_text_width = 640
    
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            lines.append("")
            continue
        
        words = paragraph.split(' ')
        current_line = []
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) <= max_text_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        if current_line:
            lines.append(' '.join(current_line))

    line_spacing = 15
    
    # Calculate visible area center
    # Visible horizontal center is still 400 (symmetrical 50px margins)
    # Visible vertical center: (0 to 450) -> middle is 225
    visible_v_center = 225
    
    processed_lines = []
    total_text_height = 0
    for line in lines:
        if not line: # Empty line for \n\n
            processed_lines.append(("", 0, 30)) # height of a blank line
            total_text_height += 30 + line_spacing
            continue

        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        processed_lines.append((line, line_w, line_h))
        total_text_height += line_h + line_spacing

    current_y = visible_v_center - (total_text_height // 2)
    for line_text, line_w, line_h in processed_lines:
        if line_text:
            x = (width - line_w) // 2
            # Use white (255) for text. 
            # We use 255 to ensure it is the brightest possible value.
            draw.text((x, current_y), line_text, font=font, fill=255)
        current_y += line_h + line_spacing

    # The TRMNL device handles its own dithering/quantization,
    # but sending a 255 white is the safest way to ensure "bright white".
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf
