import io
import qrcode
from PIL import Image

def generate_qr_code(data):
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('L')
    
    # Create 800x480 white canvas (Grayscale)
    canvas = Image.new('L', (800, 480), 255)
    
    # Center the QR code on the canvas
    qr_width, qr_height = qr_img.size
    offset = ((800 - qr_width) // 2, (480 - qr_height) // 2)
    canvas.paste(qr_img, offset)
    
    # Save to buffer
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf
