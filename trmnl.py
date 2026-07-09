import sqlite3
import os
import hashlib
import time
import io
from datetime import datetime
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response, render_template_string
from testcard import generate_testcard
from qr_code import generate_qr_code
from schedule import generate_schedule, get_events
from xkcd import generate_xkcd
from fmi import generate_fmi
from titlecard import generate_titlecard
from register import handle_register

app = Flask(__name__)
DB_PATH = 'trmnl.db'
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')
BASE_URL = 'https://trmnl.janmg.com'

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                mac_address TEXT PRIMARY KEY,
                name TEXT,
                playlist TEXT,
                refresh_rate INTEGER DEFAULT 900,
                last_update TEXT,
                last_filename TEXT,
                battery_voltage TEXT,
                fw_version TEXT,
                wifi_ssid TEXT,
                flip INTEGER DEFAULT 0,
                custom_url TEXT,
                custom_text TEXT
            )
        ''')
        # Ensure 'flip' column exists (for existing databases)
        try:
            conn.execute('ALTER TABLE devices ADD COLUMN flip INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass # Already exists
        # Ensure 'custom_url' column exists
        try:
            conn.execute('ALTER TABLE devices ADD COLUMN custom_url TEXT')
        except sqlite3.OperationalError:
            pass # Already exists
        # Ensure 'custom_text' column exists
        try:
            conn.execute('ALTER TABLE devices ADD COLUMN custom_text TEXT')
        except sqlite3.OperationalError:
            pass # Already exists

@app.before_request
def log_request_info():
    if not app.debug:
        return
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
    if app.debug:
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
    else:
        # Concise logging for production
        ip = request.headers.get('X-Real-Ip', request.remote_addr)
        device_id = request.headers.get('Id') or request.headers.get('ID', '-')
        refresh_rate = request.headers.get('Refresh-Rate', '-')
        battery = request.headers.get('Battery-Voltage', '-')
        fw = request.headers.get('Fw-Version', '-')
        ssid = request.headers.get('Wifi-Ssid', '-')
        
        print(f"{request.method} {request.path}, {ip}, {device_id}, {refresh_rate}, {battery}, {fw}, {ssid}")
        
        image_url = "-"
        if response.mimetype == 'application/json':
            try:
                data = response.get_json(silent=True)
                if data and isinstance(data, dict):
                    image_url = data.get('image_url', '-')
            except Exception:
                pass
        
        print(f"{response.status}, {image_url}")
        
    return response

def apply_rotation(buf, rotate_param):
    if not rotate_param or rotate_param == '0':
        return buf
    
    try:
        buf.seek(0)
        img = Image.open(buf)
        img = img.transpose(Image.ROTATE_180)
        new_buf = io.BytesIO()
        img.save(new_buf, format='PNG')
        new_buf.seek(0)
        return new_buf
    except Exception as e:
        if app.debug:
            print(f"ERROR in apply_rotation: {str(e)}")
        buf.seek(0)
        return buf

@app.route('/api/setup', methods=['GET'])
def setup():
    # TRMNL API: Device minimal setup response
    device_id = request.headers.get('ID') or request.headers.get('Id')
    image_url = f"{BASE_URL}/testcard"
    flip = 0
    if device_id:
        device_id = device_id.strip().upper()
        image_url += f"?mac_address={device_id}"
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT flip FROM devices WHERE mac_address = ?', (device_id,))
                row = cursor.fetchone()
                if row:
                    flip = row[0] or 0
        except Exception:
            pass
            
    if flip:
        image_url += "&flip=1"
        
    response = jsonify({
        "image_url": image_url,
        "message": "Welcome to your TRMNL device!"
    })
    response.headers['Connection'] = 'close'
    response.headers['Content-Length'] = len(response.get_data())
    return response, 200

@app.route('/api/log', methods=['POST'])
def log():
    # TRMNL API: Fetch the log from the device
    data = request.get_json()
    if app.debug:
        print(f"DEVICE LOG: {data}")
    return '', 204

@app.route('/api/display', methods=['GET'])
def get_display():
    # TRMNL API: devices send the MAC address in the 'ID' (or 'Id') header
    device_id = request.headers.get('ID') or request.headers.get('Id')
    
    if not device_id:
        print(f"ERROR: No ID header found in request. Headers: {dict(request.headers)}")
        return jsonify({
            "status": 401,
            "title": "Unauthorized",
            "detail": "Device MAC address (ID header) is required."
        }), 401

    # Normalize MAC address for database lookup
    device_id = device_id.strip().upper()
    print(f"INFO: get_display for {device_id}")

    # Capture telemetry from headers early
    battery = request.headers.get('Battery-Voltage', '-')
    fw = request.headers.get('Fw-Version', '-')
    ssid = request.headers.get('Wifi-Ssid', '-')

    try:
        # Check database for playlist and configuration
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT playlist, refresh_rate, flip, custom_url, custom_text FROM devices WHERE mac_address = ?', (device_id,))
            row = cursor.fetchone()
            
        playlist_items = []
        refresh_rate = 900
        flip = 0
        custom_url = ""
        custom_text = ""
        if row:
            playlist_items = row[0].split(',') if row[0] else []
            refresh_rate = row[1] or 900
            flip = row[2] or 0
            custom_url = row[3] or ""
            custom_text = row[4] or ""

        content_state = ""

        if playlist_items:
            # Simple rotation based on current time
            idx = int(time.time() / refresh_rate) % len(playlist_items)
            item = playlist_items[idx]
            
            if item == "testcard":
                image_url = f"{BASE_URL}/testcard?mac_address={device_id}"
                filename = "testcard.png"
                refresh_rate = 60 # Faster refresh while registering
                # For testcard, content changes if hardware info changes
                content_state = f"{battery}-{fw}-{ssid}-{request.remote_addr}"
            elif item == "schedule":
                image_url = f"{BASE_URL}/schedule?mac_address={device_id}"
                filename = "schedule.png"
                # Schedule changes if events change, or if weather might have updated (every hour)
                events_hash = hashlib.md5(str(get_events()).encode('utf-8')).hexdigest()
                content_state = f"{events_hash}-{time.strftime('%Y-%m-%d-%H')}"
            elif item == "xkcd":
                image_url = f"{BASE_URL}/xkcd?mac_address={device_id}"
                filename = "xkcd.png"
                # XKCD changes once a day usually, content_state with hour is safe
                content_state = f"xkcd-{time.strftime('%Y-%m-%d-%H')}"
            elif item == "fmi":
                image_url = f"{BASE_URL}/fmi?mac_address={device_id}"
                filename = "fmi.png"
                # FMI updates every 15 mins, using 15-min bucket for content state
                minute_bucket = (datetime.now().minute // 15) * 15
                content_state = f"fmi-{datetime.now().strftime('%Y%m%d%H')}{minute_bucket}"
            elif item == "custom_url" and custom_url:
                image_url = custom_url
                filename = "custom_url.png"
                # For external URLs, we don't know when they change, so we refresh periodically
                content_state = f"custom-{time.strftime('%Y-%m-%d-%H')}"
            elif item == "custom_text" and custom_text:
                image_url = f"{BASE_URL}/text?mac_address={device_id}"
                filename = "titlecard.png"
                # Text changes if content changes
                text_hash = hashlib.md5(custom_text.encode('utf-8')).hexdigest()
                content_state = f"text-{text_hash}"
            else:
                image_url = f"{BASE_URL}/content/{item}.png"
                filename = f"{item}.png"
                # For static files, use mtime as content state
                try:
                    mtime = os.path.getmtime(os.path.join(CONTENT_DIR, f"{item}.png"))
                    content_state = f"{mtime}"
                except:
                    pass
                
            if flip:
                separator = "&" if "?" in image_url else "?"
                image_url += f"{separator}flip=1"

            if app.debug:
                print(f"PLAYLIST MATCH: Displaying {item} for {device_id} (flip: {flip})")
        else:
            # Fallback to registration/check logic if no playlist
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT name, flip FROM devices WHERE mac_address = ?', (device_id,))
                n_row = cursor.fetchone()
                row_name = n_row[0] if n_row else None
                flip = n_row[1] if n_row else 0

            if row_name:
                if app.debug:
                    print(f"MATCH FOUND: Displaying schedule.png for {device_id} ({row_name})")
                image_url = f"{BASE_URL}/content/schedule.png"
                if flip:
                    image_url += "?flip=1"
                filename = "schedule.png"
                # For fallback schedule, update if events change or every hour for weather
                events_hash = hashlib.md5(str(get_events()).encode('utf-8')).hexdigest()
                content_state = f"fallback-{events_hash}-{time.strftime('%Y-%m-%d-%H')}"
            else:
                raise Exception("Device not found/not configured")

    except Exception as e:
        if app.debug:
            print(f"ERROR: {str(e)}")
        # Catch-all: Display Testcard for unknown devices
        image_url = f"{BASE_URL}/testcard?mac_address={device_id}"
        filename = "testcard.png"
        refresh_rate = 60 # Faster refresh while registering
        content_state = "unknown-device"
        flip = 0

    response_data = {
        "filename": filename,
        "firmware_url": None,
        "firmware_version": None,
        "image_url": image_url,
        "image_url_timeout": 0,
        "maximum_compatibility": False,
        "refresh_rate": refresh_rate, 
        "reset_firmware": False,
        "special_function": "none",
        "temperature_profile": "default",
        "touchbar_mode": "tap",
        "update_firmware": False
    }

    # Generate ETag based on the response content and additional change factors
    # day_string ensures at least one refresh per day
    day_string = time.strftime('%Y-%m-%d')
    etag_content = f"{filename}-{image_url}-{day_string}-{content_state}-{flip}"
    etag = hashlib.md5(etag_content.encode('utf-8')).hexdigest()

    # Check if the device already has this version
    if request.headers.get('If-None-Match') == etag:
        if app.debug:
            print(f"NOT MODIFIED: Returning 304 for {device_id}")
        return '', 304

    if app.debug:
        print(f"SENDING DATA: Returning 200 for {device_id} (New Filename: {filename})")

    # Update device info in database
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            UPDATE devices 
            SET last_update = ?, last_filename = ?, battery_voltage = ?, fw_version = ?, wifi_ssid = ? 
            WHERE mac_address = ?
        ''', (time.strftime('%Y-%m-%d %H:%M:%S'), filename, battery, fw, ssid, device_id))

    resp = make_response(jsonify(response_data))
    resp.set_etag(etag)
    return resp

@app.route('/register', methods=['GET', 'POST'])
def register():
    return handle_register(DB_PATH, CONTENT_DIR)

@app.route('/content/<path:filename>')
def serve_content(filename):
    # Show the files in the content directory
    filepath = os.path.join(CONTENT_DIR, filename)
    if not os.path.exists(filepath):
        return "File not found", 404
    
    flip = request.args.get('flip')
    if flip == '1':
        with open(filepath, 'rb') as f:
            buf = io.BytesIO(f.read())
        buf = apply_rotation(buf, flip)
        return send_file(buf, mimetype='image/png')
        
    return send_from_directory(CONTENT_DIR, filename)

@app.route('/qrcode')
def get_qrcode():
    # Write a QR code to the screen to register the device in the sqlite database
    data = request.args.get('data')
    flip = request.args.get('flip')
    if not data:
        return "No data provided", 400
    
    buf = generate_qr_code(data)
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route('/schedule')
def get_schedule():
    # Generate schedule image with weather and events
    flip = request.args.get('flip')
    buf = generate_schedule()
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route('/testcard')
def get_testcard():
    mac_address = request.args.get('mac_address')
    flip = request.args.get('flip')
    # If not in args, try to get from headers (like in get_display)
    if not mac_address:
        mac_address = request.headers.get('ID') or request.headers.get('Id')
    
    device_info = {}
    if mac_address:
        mac_address = mac_address.strip().upper()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT name, battery_voltage, fw_version, wifi_ssid, flip FROM devices WHERE mac_address = ?', (mac_address,))
                row = cursor.fetchone()
                if row:
                    if flip is None:
                        flip = row[4]
                    device_info = {
                        "name": row[0],
                        "mac": mac_address,
                        "ip": request.headers.get('X-Real-Ip', request.remote_addr),
                        "fw": row[2],
                        "battery": row[1],
                        "ssid": row[3]
                    }
                else:
                    # New device or not in DB yet, still show some info
                    device_info = {
                        "name": "New Device",
                        "mac": mac_address,
                        "ip": request.headers.get('X-Real-Ip', request.remote_addr),
                        "fw": request.headers.get('Fw-Version', '-'),
                        "battery": request.headers.get('Battery-Voltage', '-'),
                        "ssid": request.headers.get('Wifi-Ssid', '-')
                    }
        except Exception:
            pass

    print(f"INFO: /testcard request - mac_address: {mac_address}")
        
    buf = generate_testcard(mac_address, device_info=device_info)
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route('/xkcd')
def get_xkcd():
    flip = request.args.get('flip')
    buf = generate_xkcd()
    if not buf:
        return "Failed to fetch xkcd", 500
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route('/fmi')
def get_fmi():
    flip = request.args.get('flip')
    buf = generate_fmi()
    if not buf:
        return "Failed to fetch FMI radar", 500
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route('/text')
def get_text():
    mac_address = request.args.get('mac_address') or request.headers.get('ID') or request.headers.get('Id')
    flip = request.args.get('flip')
    text = "Silent Movie Title"
    
    if mac_address:
        mac_address = mac_address.strip().upper()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT custom_text FROM devices WHERE mac_address = ?', (mac_address,))
            row = cursor.fetchone()
            if row and row[0]:
                text = row[0]
                if app.debug:
                    print(f"DEBUG: Found text for {mac_address}: {text}")
            elif app.debug:
                print(f"DEBUG: No text found for {mac_address}, using default")
    elif app.debug:
        print("DEBUG: No MAC address provided to /text")
                
    buf = generate_titlecard(text)
    buf = apply_rotation(buf, flip)
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

if __name__ == '__main__':
    init_db()
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=False)
