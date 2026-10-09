import requests
import io
from PIL import Image, ImageOps

def generate_xkcd(width=800, height=480, left_margin=50, right_margin=50, top_margin=0, bottom_margin=30):
    try:
        # Get metadata for the latest comic
        response = requests.get("https://xkcd.com/info.0.json", timeout=30)
        response.raise_for_status()
        data = response.json()
        img_url = data.get('img')
        
        # Download the image
        img_response = requests.get(img_url, timeout=30)
        img_response.raise_for_status()
        img = Image.open(io.BytesIO(img_response.content))
        
        # Visible area is the canvas minus all margins
        visible_width = width - left_margin - right_margin
        visible_height = height - top_margin - bottom_margin
        
        # Create a white canvas
        canvas = Image.new('RGB', (width, height), (255, 255, 255))
        
        # Prefer filling vertical space without cropping.
        # If that would exceed visible width, fall back to width-constrained scaling.
        scale = visible_height / img.height
        target_w = int(img.width * scale)
        target_h = visible_height

        if target_w > visible_width:
            scale = visible_width / img.width
            target_w = visible_width
            target_h = int(img.height * scale)

        img = img.resize((max(1, target_w), max(1, target_h)), Image.Resampling.LANCZOS)
        
        # Calculate position to center the image in the visible area
        x_offset = left_margin + (visible_width - img.width) // 2
        y_offset = top_margin + (visible_height - img.height) // 2

        # Paste comic onto canvas
        canvas.paste(img, (x_offset, y_offset))
        
        # Transform to Grayscale
        grayscale = canvas.convert('L')
        
        # Quantize to 4-bit (16 levels of gray)
        # Note: TRMNL displays are usually 1-bit or 4-bit. Pillow's quantize or simple point map works.
        # Here we just keep it as L (8-bit grayscale) which the TRMNL device handles, 
        # but to be strict about "4-bit grayscale" we can posterize.
        four_bit = ImageOps.posterize(grayscale, 4)
        
        buf = io.BytesIO()
        four_bit.save(buf, format='PNG')
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Error generating xkcd: {e}")
        return None
