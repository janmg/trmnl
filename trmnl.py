import sqlite3
import os
import hashlib
import time
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response, render_template_string
from testcard import generate_testcard
from qr_code import generate_qr_code

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
                wifi_ssid TEXT
            )
        ''')

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

@app.route('/api/setup', methods=['GET'])
def setup():
    # TRMNL API: Device minimal setup response
    response = jsonify({
        "image_url": f"{BASE_URL}/content/testcard.png",
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
        if app.debug:
            print("ERROR: No ID header found in request")
        return jsonify({
            "status": 401,
            "title": "Unauthorized",
            "detail": "Device MAC address (ID header) is required."
        }), 401

    # Normalize MAC address for database lookup
    device_id = device_id.strip().upper()

    try:
        # Check database for playlist
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT playlist, refresh_rate FROM devices WHERE mac_address = ?', (device_id,))
            row = cursor.fetchone()
            
        playlist_items = []
        refresh_rate = 900
        if row and row[0]:
            playlist_items = row[0].split(',')
            refresh_rate = row[1] or 900

        if playlist_items:
            # Simple rotation based on current time
            idx = int(time.time() / refresh_rate) % len(playlist_items)
            item = playlist_items[idx]
            
            if item == "qr-code":
                reg_url = f"{BASE_URL}/register?mac_address={device_id}"
                image_url = f"{BASE_URL}/qrcode?data={reg_url}"
                filename = "register.png"
                refresh_rate = 60 # Faster refresh while registering
            elif item == "testcard":
                image_url = f"{BASE_URL}/testcard"
                filename = "testcard.png"
            else:
                image_url = f"{BASE_URL}/content/{item}.png"
                filename = f"{item}.png"
                
            if app.debug:
                print(f"PLAYLIST MATCH: Displaying {item} for {device_id}")
        else:
            # Fallback to registration/check logic if no playlist
            row_name = row[0] if row else None # name is first col in old schema, but we changed it.
            # Let's re-fetch name explicitly if row is just playlist/refresh
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT name FROM devices WHERE mac_address = ?', (device_id,))
                n_row = cursor.fetchone()
                row_name = n_row[0] if n_row else None

            if row_name:
                if app.debug:
                    print(f"MATCH FOUND: Displaying schedule.png for {device_id} ({row_name})")
                image_url = f"{BASE_URL}/content/schedule.png"
                filename = "schedule.png"
            else:
                raise Exception("Device not found/not configured")

    except Exception as e:
        if app.debug:
            print(f"ERROR: {str(e)}")
        # Catch-all: Display Registration QR
        reg_url = f"{BASE_URL}/register?mac_address={device_id}"
        image_url = f"{BASE_URL}/qrcode?data={reg_url}"
        filename = "register.png"
        refresh_rate = 60 # Faster refresh while registering

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

    # Generate ETag based on the response content
    # This ensures that if filename and image_url are the same, we can return 304
    etag_content = f"{filename}-{image_url}"
    etag = hashlib.md5(etag_content.encode('utf-8')).hexdigest()

    # Check if the device already has this version
    if request.headers.get('If-None-Match') == etag:
        if app.debug:
            print(f"NOT MODIFIED: Returning 304 for {device_id}")
        return '', 304

    if app.debug:
        print(f"SENDING DATA: Returning 200 for {device_id} (New Filename: {filename})")

    # Update device info in database
    battery = request.headers.get('Battery-Voltage', '-')
    fw = request.headers.get('Fw-Version', '-')
    ssid = request.headers.get('Wifi-Ssid', '-')

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
    mac_address = request.args.get('mac_address') or request.form.get('mac_address')
    if not mac_address:
        return "MAC Address required", 400
    
    mac_address = mac_address.upper()
    message = ""
    
    # Options from static + content directory
    options = ["qr-code", "testcard"]
    content_files = [f.replace('.png', '') for f in os.listdir(CONTENT_DIR) if f.endswith('.png')]
    for opt in ["schedule", "eye", "robo"]:
        if opt in content_files and opt not in options:
            options.append(opt)
    
    for f in content_files:
        if f not in options:
            options.append(f)

    refresh_options = [1, 5, 15, 60, 1440]

    if request.method == 'POST':
        selected_items = request.form.getlist('playlist')
        name = request.form.get('name')
        refresh_min = int(request.form.get('refresh_rate', 15))
        refresh_sec = refresh_min * 60
        playlist_str = ",".join(selected_items)
        
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO devices (mac_address, name, playlist, refresh_rate) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(mac_address) DO UPDATE SET 
                name=excluded.name, playlist=excluded.playlist, refresh_rate=excluded.refresh_rate
            ''', (mac_address, name or f"Device {mac_address[-5:]}", playlist_str, refresh_sec))
        
        message = f"Device {mac_address} updated! Selected items: {', '.join(selected_items)}"

    # GET request or after POST: show form
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, playlist, refresh_rate, last_update, last_filename, battery_voltage, fw_version, wifi_ssid FROM devices WHERE mac_address = ?', (mac_address,))
        row = cursor.fetchone()
        if row:
            device_name = row[0]
            current_playlist = row[1].split(',') if row[1] else []
            current_refresh_min = (row[2] or 900) // 60
            last_update = row[3] or "Never"
            last_filename = row[4] or "None"
            battery = row[5] or "-"
            fw_version = row[6] or "-"
            wifi_ssid = row[7] or "-"
        else:
            device_name = f"Device {mac_address[-5:]}"
            current_playlist = []
            current_refresh_min = 15
            last_update = "Never"
            last_filename = "None"
            battery = "-"
            fw_version = "-"
            wifi_ssid = "-"

    html = '''
    <h1>Register / Configure Device</h1>
    <p><strong>Last Update:</strong> {{ last_update }} ({{ last_filename }})</p>
    <p><strong>Status:</strong> Battery: {{ battery }}V | Firmware: {{ fw_version }} | WiFi: {{ wifi_ssid }}</p>
    <form method="post">
        <input type="hidden" name="mac_address" value="{{ mac_address }}">
        <p>MAC Address: {{ mac_address }}</p>
        <p>Name: <input type="text" name="name" value="{{ device_name }}"></p>
        <p>Refresh Rate: 
            <select name="refresh_rate">
                {% for min in refresh_options %}
                <option value="{{ min }}" {% if min == current_refresh_min %}selected{% endif %}>{{ min }} minutes</option>
                {% endfor %}
            </select>
        </p>
        <p>Playlist items:</p>
        {% for option in options %}
        <div>
            <input type="checkbox" id="{{ option }}" name="playlist" value="{{ option }}"
                   {% if option in current_playlist %}checked{% endif %}>
            <label for="{{ option }}">{{ option }}</label>
        </div>
        {% endfor %}
        <br>
        <button type="submit">Save Configuration</button>
    </form>
    {% if message %}
    <hr>
    <p><strong>{{ message }}</strong></p>
    {% endif %}
    '''
    return render_template_string(html, mac_address=mac_address, device_name=device_name, 
                                  options=options, current_playlist=current_playlist,
                                  refresh_options=refresh_options, current_refresh_min=current_refresh_min,
                                  message=message, last_update=last_update, last_filename=last_filename,
                                  battery=battery, fw_version=fw_version, wifi_ssid=wifi_ssid)

@app.route('/content/<path:filename>')
def serve_content(filename):
    # Show the files in the content directory
    return send_from_directory(CONTENT_DIR, filename)

@app.route('/qrcode')
def get_qrcode():
    # Write a QR code to the screen to register the device in the sqlite database
    data = request.args.get('data')
    if not data:
        return "No data provided", 400
    
    buf = generate_qr_code(data)
    return send_file(buf, mimetype='image/png')

@app.route('/testcard')
def get_testcard():
    buf = generate_testcard()
    return send_file(buf, mimetype='image/png')

if __name__ == '__main__':
    init_db()
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=False)
