import os
import io
import qrcode
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory, send_file

app = Flask(__name__)
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')
BASE_URL = 'https://trmnl.janmg.com'

@app.before_request
def log_request_info():
    print(f"\n--- REQUEST ---")
    print(f"Method: {request.method}")
    print(f"Path:   {request.path}")
    print(f"Headers: {dict(request.headers)}")
    if request.get_data():
        try:
            print(f"Body:    {request.get_data(as_text=True)}")
        except Exception:
            print(f"Body:    <binary data>")

@app.after_request
def log_response_info(response):
    print(f"--- RESPONSE ---")
    print(f"Status: {response.status}")
    if response.direct_passthrough:
        print(f"Body:    <direct passthrough>")
    else:
        data = response.get_data()
        if len(data) > 500:
            print(f"Body:    <{len(data)} bytes of data>")
        else:
            try:
                print(f"Body:    {data.decode('utf-8')}")
            except Exception:
                print(f"Body:    <binary data>")
    print(f"----------------\n")
    return response

@app.route('/api/display', methods=['GET'])
def get_display():
    # Simplest possible response to just display the eye.png
    response_data = {
        "filename": "eye.png",
        "firmware_url": None,
        "firmware_version": None,
        "image_url": f"{BASE_URL}/content/eye.png",
        "image_url_timeout": 0,
        "maximum_compatibility": False,
        "refresh_rate": 900,
        "reset_firmware": False,
        "special_function": "none",
        "temperature_profile": "default",
        "touchbar_mode": "tap",
        "update_firmware": False
    }
    return jsonify(response_data), 200

@app.route('/api/setup', methods=['GET'])
def setup():
    # Minimal setup response
    response = jsonify({
        "image_url": f"{BASE_URL}/content/eye.png",
        "message": "Welcome to your TRMNL device!"
    })
    response.headers['Connection'] = 'close'
    response.headers['Content-Length'] = len(response.get_data())
    return response, 200

@app.route('/api/log', methods=['POST'])
def log():
    data = request.get_json()
    print(f"DEVICE LOG: {data}")
    return '', 204

@app.route('/content/<path:filename>')
def serve_content(filename):
    return send_from_directory(CONTENT_DIR, filename)

@app.route('/api/qrcode')
def get_qrcode():
    data = request.args.get('data')
    if not data:
        return "No data provided", 400
    
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
    
    return send_file(buf, mimetype='image/png')

if __name__ == '__main__':
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=True)
