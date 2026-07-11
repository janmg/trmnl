import requests
import io
from PIL import Image, ImageOps

def generate_xkcd():
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
        
        # Target dimensions: 800 x 480
        # Visible area: (50, 0) to (750, 450) -> Size: 700 x 450
        # Wait, user said: "50px from the left and 50px from the right are not visible and 30px from the bottom"
        # Total width: 800. Visible width: 800 - 50 - 50 = 700.
        # Total height: 480. Visible height: 480 - 30 = 450.
        
        visible_width = 700
        visible_height = 450
        
        # Create a white canvas (800x480)
        canvas = Image.new('RGB', (800, 480), (255, 255, 255))
        
        # Prefer filling vertical space (450px) without cropping.
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
        # Visible area centers are: width=(50 to 750), height=(0 to 450)
        x_offset = 50 + (visible_width - img.width) // 2
        y_offset = (visible_height - img.height) // 2

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
