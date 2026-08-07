import os
import io
import time
import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from PIL import Image
from flask import Flask, request, jsonify, make_response

from src.testcard import generate_testcard
from src.schedule import generate_schedule, get_events
from src.xkcd import generate_xkcd
from src.fmi import generate_fmi
from src.weather import generate_weather
from src.map import generate_map
from src.titlecard import generate_titlecard
from src.clock import generate_clock
from src.configure import handle_register
from src.logs import get_logs
from src.timing import align_refresh, MIN_REFRESH_SECONDS
from src.influx import write_battery_voltage
from src.db import (
    init_db,
    get_device,
    get_device_by_api_key,
    device_exists,
    register_device,
    update_device_last_seen,
    get_refresh_lead_seconds,
    get_device_location_timezone,
    add_log_entries,
    log_display_request,
    normalize_mac,
    update_low_battery_alert,
    cleanup_old_display_requests,
    cleanup_past_events,
)

app = Flask(__name__)

DB_PATH = 'trmnl.db'
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')
BASE_URL = 'https://trmnl.janmg.com'

# How long before the whole minute the device should wake up to fetch its next image (the ESP32 needs a moment to connect to wifi and refresh the e-paper panel before the target minute actually arrives).
REFRESH_LEAD_SECONDS = 5
# Upper bound for the "come back later" refresh rate handed out while the device is inside its configured sleep window, so it can deep sleep for a long stretch instead of polling every minute.
MAX_SLEEP_REFRESH_SECONDS = 6 * 3600
LOW_BATTERY_REFRESH_SECONDS = 60 * 60
LOW_BATTERY_MESSAGE = "time to recharge the battery"


def _cleanup_scheduler():
    """Run cleanup of old display requests and past events every midnight."""
    while True:
        # Calculate seconds until next midnight
        now = datetime.now()
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        seconds_until_midnight = (tomorrow - now).total_seconds()
        
        # Sleep until midnight
        time.sleep(seconds_until_midnight)
        
        # Run cleanup
        try:
            cleanup_old_display_requests(DB_PATH)
            cleanup_past_events(DB_PATH)
        except Exception as e:
            print(f"Error in cleanup scheduler: {e}")


# Start the cleanup scheduler in a background thread
_cleanup_thread = threading.Thread(target=_cleanup_scheduler, daemon=True)
_cleanup_thread.start()


def _observed_refresh_lead(device, now_epoch):
    """Estimate how late this poll arrived relative to the previous one."""
    if not device or not device.get('last_update'):
        return None
    try:
        previous = datetime.strptime(device['last_update'], '%Y-%m-%d %H:%M:%S').timestamp()
        expected = float(device.get('refresh_rate') or 0)
        if expected <= 0:
            return None
        return max(0.0, now_epoch - previous - expected)
    except (TypeError, ValueError, OverflowError):
        return None

@app.after_request
def _dedupe_response_headers(response):
    """nginx (and other reverse proxies) reject responses that contain a
    duplicated header such as 'Date' or 'Server'. The Flask dev server adds a
    'Date' header itself, and send_file/send_from_directory add another one,
    which produced 'upstream sent duplicate header line: "Date: ..."' and a
    502 Bad Gateway on the device. De-duplicate those headers so the proxy
    accepts the response."""
    for header in ('Date', 'Server'):
        values = response.headers.get_all(header)
        if values and len(values) > 1:
            # Keep only the first occurrence.
            response.headers.set(header, values[0])
    return response

def _get_mac_from_request():
    """Identify the device from a mac_address query param or the ID/Id header."""
    mac = request.args.get('mac_address') or request.headers.get('ID') or request.headers.get('Id')
    return mac.strip().upper() if mac else None

def _device_should_flip(mac_address):
    if not mac_address:
        return False
    dev = get_device(DB_PATH, mac_address)
    return bool(dev and dev.get('flip'))

def apply_rotation(buf, should_flip):
    """Rotate a PNG 180 degrees via Pillow into a new BytesIO buffer."""
    if not should_flip:
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

def _serve_image(buf, mac_address, cache_private=False):
    """Apply device rotation, set no-cache headers, and send a PNG buffer.
    Falls back to the testcard if the generator failed to produce an image."""
    if buf is None:
        buf = generate_testcard(mac_address, device_info={})

    should_flip = _device_should_flip(mac_address)
    buf = apply_rotation(buf, should_flip)

    # Read the PNG bytes and return a plain Response. Using send_file() would make Werkzeug add its own Date/Last-Modified/ETag headers on top of the ones the dev server's http.server handler writes, producing a duplicated "Date" header that nginx rejects with a 502 Bad Gateway.
    buf.seek(0)
    data = buf.read()
    resp = make_response(data, 200, {'Content-Type': 'image/png'})
    cache_control = 'no-store, no-cache, must-revalidate, max-age=0'
    if cache_private:
        cache_control += ', private'
    resp.headers['Cache-Control'] = cache_control
    return resp

def _safe_generate(generator_fn, *args, **kwargs):
    """Call a content generator and never let it raise: a device fetching an
    image must always get back a valid PNG (falling back to the testcard via
    _serve_image) rather than an HTTP 500, which the firmware would log as a
    failed GET and never retry the same content again."""
    try:
        return generator_fn(*args, **kwargs)
    except Exception as e:
        if app.debug:
            print(f"GENERATOR ERROR in {generator_fn.__name__}: {e}")
        return None

def is_device_sleeping(dt, sleep_start, sleep_end, sleep_weekend):
    if sleep_weekend and dt.weekday() >= 5:
        return True

    if not sleep_start or not sleep_end:
        return False

    try:
        start_h, start_m = map(int, sleep_start.split(':'))
        end_h, end_m = map(int, sleep_end.split(':'))
        now_val = dt.hour * 60 + dt.minute
        start_val = start_h * 60 + start_m
        end_val = end_h * 60 + end_m

        if start_val < end_val:
            return start_val <= now_val < end_val
        else:  # overnight window
            return now_val >= start_val or now_val < end_val
    except Exception:
        return False

def seconds_until_awake(dt, sleep_start, sleep_end, sleep_weekend):
    """How long the device can stay in deep sleep, capped at MAX_SLEEP_REFRESH_SECONDS."""
    if sleep_weekend and dt.weekday() >= 5:
        return MAX_SLEEP_REFRESH_SECONDS

    try:
        end_h, end_m = map(int, sleep_end.split(':'))
    except Exception:
        return MAX_SLEEP_REFRESH_SECONDS

    target = dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if target <= dt:
        target += timedelta(days=1)

    seconds = int((target - dt).total_seconds())
    return min(max(seconds, MIN_REFRESH_SECONDS), MAX_SLEEP_REFRESH_SECONDS)

@app.route('/api/setup', methods=['GET'])
def setup():
    # Device provisioning: exchange the device's MAC address for an api_key.
    header_mac = request.headers.get('ID') or request.headers.get('Id') or ''
    normalized = ''.join(ch for ch in header_mac.strip().upper() if ch in '0123456789ABCDEF')

    if not normalized:
        return jsonify({
            "status": 401,
            "title": "Unauthorized",
            "detail": "Device MAC address (ID header) is required."
        }), 401

    friendly_id = normalized[-6:]
    mac_address = normalize_mac(header_mac)

    key_alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    candidate_key = ''.join(secrets.choice(key_alphabet) for _ in range(22))
    try:
        api_key = register_device(DB_PATH, mac_address, candidate_key)
    except Exception as e:
        if app.debug:
            print(f"SETUP ERROR for {mac_address}: {e}")
        return jsonify({
            "status": 500,
            "title": "Internal Server Error",
            "detail": "Failed to store device api_key."
        }), 500

    response = jsonify({
        "status": 200,
        "api_key": api_key,
        "friendly_id": friendly_id,
        "image_url": f"{BASE_URL}/testcard",
        "filename": "testcard",
        "message": "Device provisioned"
    })
    response.headers['Connection'] = 'close'
    response.headers['Content-Length'] = len(response.get_data())
    return response, 200


@app.route('/api/log', methods=['POST'])
def post_log():
    # Devices push their local log buffer here; store it against the device.
    access_token = request.headers.get('Access-Token')
    header_mac = request.headers.get('ID') or request.headers.get('Id')

    device = get_device_by_api_key(DB_PATH, access_token) if access_token else None
    if device:
        mac_address = device.get('mac_address')
    elif header_mac:
        mac_address = normalize_mac(header_mac)
    else:
        mac_address = 'UNKNOWN'

    payload = request.get_json(silent=True) or {}
    logs = payload.get('logs', []) if isinstance(payload, dict) else []
    add_log_entries(DB_PATH, mac_address, logs)
    return '', 204

@app.route('/api/display', methods=['GET'])
def get_display():
    access_token = request.headers.get('Access-Token')
    header_mac = request.headers.get('ID') or request.headers.get('Id')

    battery = request.headers.get('Battery-Voltage', '-')
    fw = request.headers.get('Fw-Version', '-')
    ssid = request.headers.get('Wifi-Ssid', '-')
    rssi = request.headers.get('Rssi') or request.headers.get('RSSI') or '-'

    # The api_key is the primary way we identify a provisioned device, but some firmware/proxy combinations send an empty or missing Access-Token header (see README capture: 'Access-Token': ''). Fall back to looking the device up by its MAC (ID header) so a configured playlist is still honored instead of silently dropping to the unregistered/testcard path.
    device = get_device_by_api_key(DB_PATH, access_token) if access_token else None
    if not device and header_mac:
        device = get_device(DB_PATH, header_mac)
    if device:
        mac_address = device.get('mac_address')
    elif header_mac:
        mac_address = normalize_mac(header_mac)
    else:
        mac_address = None

    if not mac_address:
        return jsonify({
            "status": 401,
            "title": "Unauthorized",
            "detail": "Device identification (Access-Token or ID header) is required."
        }), 401

    # Write battery voltage and related metrics to InfluxDB asynchronously
    write_battery_voltage(mac_address, battery, fw, ssid, rssi)
    low_battery_alert = update_low_battery_alert(DB_PATH, mac_address, battery) if device else False

    now_epoch = time.time()
    forced_refresh = None
    next_playlist_index = None
    observed_refresh_lead = None
    refresh_lead_seconds = REFRESH_LEAD_SECONDS
    # Percent-encode the MAC so it's a safe query-string value: raw colons in a URL can trip up minimal embedded HTTP/TLS clients (this was found to be the cause of persistent "[HTTPS] GET ... failed" errors on-device).
    mac_q = quote(mac_address, safe='')

    try:
        if not device:
            raise LookupError("device not registered")

        playlist_items = [i for i in (device.get('playlist') or '').split(',') if i]
        refresh_rate = device.get('refresh_rate') or 900
        should_flip = bool(device.get('flip'))
        custom_url = device.get('custom_url') or ""
        custom_text = device.get('custom_text') or ""
        sleep_start = device.get('sleep_start') or ""
        sleep_end = device.get('sleep_end') or ""
        sleep_weekend = device.get('sleep_weekend') or 0
        device_timezone = device.get('timezone') or "Europe/Helsinki"
        refresh_lead_seconds = get_refresh_lead_seconds(DB_PATH, mac_address, REFRESH_LEAD_SECONDS)
        observed_refresh_lead = _observed_refresh_lead(device, now_epoch)

        try:
            corrected_dt = datetime.fromtimestamp(now_epoch, tz=ZoneInfo(device_timezone))
        except Exception:
            corrected_dt = datetime.fromtimestamp(now_epoch)

        if low_battery_alert:
            playlist_items = ["low_battery"]
            refresh_rate = LOW_BATTERY_REFRESH_SECONDS

        if not playlist_items:
            raise LookupError("device has no playlist configured")

        playlist_index = int(device.get('playlist_index') or 0)
        idx = playlist_index % len(playlist_items)
        item = playlist_items[idx].strip()
        next_playlist_index = playlist_index + 1
        selected_item = item

        content_state = ""

        if item == "low_battery":
            image_url = f"{BASE_URL}/low-battery?mac_address={mac_q}"
            content_state = "low-battery"
            next_playlist_index = None
            forced_refresh = LOW_BATTERY_REFRESH_SECONDS
        elif item == "schedule":
            image_url = f"{BASE_URL}/schedule?mac_address={mac_q}"
            events_hash = hashlib.md5(str(get_events()).encode('utf-8')).hexdigest()
            content_state = f"{events_hash}-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "clock":
            image_url = f"{BASE_URL}/clock?mac_address={mac_q}"
            # Clock refreshes at the configured refresh_rate, aligned to period boundaries
            # (e.g., with 15-minute refresh_rate, clock updates at :00, :15, :30, :45)
            forced_refresh, next_boundary = align_refresh(now_epoch, refresh_rate, refresh_lead_seconds, device_timezone, for_clock=True)
            content_state = f"clock-{next_boundary.strftime('%Y-%m-%d-%H%M')}"
        elif item == "xkcd":
            image_url = f"{BASE_URL}/xkcd?mac_address={mac_q}"
            content_state = f"xkcd-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "fmi":
            image_url = f"{BASE_URL}/fmi?mac_address={mac_q}"
            minute_bucket = (corrected_dt.minute // 15) * 15
            content_state = f"fmi-{corrected_dt.strftime('%Y%m%d%H')}{minute_bucket}"
        elif item == "weather":
            image_url = f"{BASE_URL}/weather?mac_address={mac_q}"
            content_state = f"weather-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "map":
            image_url = f"{BASE_URL}/map?mac_address={mac_q}"
            content_state = f"map-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "custom_url" and custom_url:
            image_url = custom_url
            content_state = f"custom-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "custom_text" and custom_text:
            image_url = f"{BASE_URL}/text?mac_address={mac_q}"
            content_state = f"text-{hashlib.md5(custom_text.encode('utf-8')).hexdigest()}"
        elif item == "testcard":
            image_url = f"{BASE_URL}/testcard?mac_address={mac_q}"
            content_state = f"testcard-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        else:
            # Static PNG stored in the content directory.
            image_url = f"{BASE_URL}/content/{item}.png?mac_address={mac_q}"
            try:
                mtime = os.path.getmtime(os.path.join(CONTENT_DIR, f"{item}.png"))
                content_state = f"{item}-{int(mtime)}"
            except OSError:
                content_state = f"{item}-missing"

        # The TRMNL firmware decides whether to re-download and redraw the e-paper display based purely on this "filename" string:
        # if a file with that exact name already exists in its local flash cache, it skips the download and redraw
        # (see checkCurrentFileName / DisplayedImage::matches in the firmware).
        # Deriving the filename from content_state guarantees the filename changes content actually changes.
        # Using a hash keeps the name short and safe for the firmware's 31-character SPIFFS filename limit.
        state_hash = hashlib.md5(content_state.encode('utf-8')).hexdigest()[:10]
        filename = f"{item}-{state_hash}.png"

    except Exception as e:
        if app.debug:
            print(f"DISPLAY ERROR for {mac_address}: {e}")
        # Unregistered device, no playlist, or any other error: fall back to
        # the testcard and poll again soon (this also drives new devices
        # towards the /configure registration flow).
        image_url = f"{BASE_URL}/testcard?mac_address={mac_q}"
        filename = "testcard.png"
        refresh_rate = 60
        content_state = "unregistered"
        should_flip = False
        selected_item = "testcard"
        device_timezone = "Europe/Helsinki"
        sleep_start = sleep_end = ""
        sleep_weekend = 0
        corrected_dt = datetime.fromtimestamp(now_epoch)

    # Work out how long the device should sleep before it comes back.
    if forced_refresh is not None:
        next_refresh = forced_refresh
    elif is_device_sleeping(corrected_dt, sleep_start, sleep_end, sleep_weekend):
        next_refresh = seconds_until_awake(corrected_dt, sleep_start, sleep_end, sleep_weekend)
    else:
        next_refresh = align_refresh(now_epoch, refresh_rate, refresh_lead_seconds, device_timezone)

    response_data = {
        "filename": filename,
        "firmware_url": None,
        "firmware_version": None,
        "image_url": image_url,
        "image_url_timeout": 0,
        "maximum_compatibility": False,
        "refresh_rate": next_refresh,
        "reset_firmware": False,
        "special_function": "none",
        "temperature_profile": "default",
        "touchbar_mode": "tap",
        "update_firmware": False
    }

    # The ETag lets the (modified) firmware decide whether the screen actually needs to be redrawn without re-downloading the image.
    day_string = corrected_dt.strftime('%Y-%m-%d')
    etag_content = f"{filename}-{image_url}-{day_string}-{content_state}-{int(bool(should_flip))}"
    etag = hashlib.md5(etag_content.encode('utf-8')).hexdigest()

    # Record everything the device sent us and everything we sent back, so device-reported fetch failures can be correlated with what the server actually told it to do.
    request_headers = {
        'ID': header_mac,
        'Access-Token': access_token,
        'Battery-Voltage': battery,
        'Fw-Version': fw,
        'Wifi-Ssid': ssid,
        'Rssi': rssi,
        'If-None-Match': request.headers.get('If-None-Match'),
    }

    if request.if_none_match and etag in request.if_none_match:
        log_display_request(DB_PATH, mac_address, request_headers, {"status": 304}, 304)
        return '', 304

    update_device_last_seen(
        DB_PATH, mac_address, filename, battery, fw, ssid, refresh_rate, rssi,
        next_playlist_index, observed_refresh_lead, next_refresh
    )
    log_display_request(DB_PATH, mac_address, request_headers, response_data, 200)

    resp = make_response(jsonify(response_data))
    resp.set_etag(etag)
    return resp

@app.route('/configure', methods=['GET', 'POST'])
def configure():
    return handle_register(DB_PATH, CONTENT_DIR)

@app.route('/content/<path:filename>')
def serve_content(filename):
    filepath = os.path.join(CONTENT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"status": 404, "title": "Not Found", "detail": "File not found"}), 404

    mac_address = _get_mac_from_request()
    should_flip = _device_should_flip(mac_address)

    with open(filepath, 'rb') as f:
        buf = io.BytesIO(f.read())

    if should_flip:
        buf = apply_rotation(buf, True)

    # Serve raw bytes via a plain Response (not send_file/send_from_directory)
    # so Werkzeug does not add a second Date/Last-Modified/ETag header that
    # would duplicate the dev server's own Date and trigger a 502 at nginx.
    buf.seek(0)
    return make_response(buf.read(), 200, {'Content-Type': 'image/png'})

@app.route('/schedule')
def get_schedule():
    mac_address = _get_mac_from_request()
    is_generic = not (mac_address and device_exists(DB_PATH, mac_address))
    buf = _safe_generate(generate_schedule, is_generic=is_generic, mac_address=mac_address)
    return _serve_image(buf, mac_address)

@app.route('/testcard')
def get_testcard():
    device_info = {}
    mac_address = _get_mac_from_request()
    if mac_address:
        dev = get_device(DB_PATH, mac_address)
        if dev:
            device_info = {
                "name": dev.get('name'),
                "mac": mac_address,
                "ip": request.headers.get('X-Real-Ip', request.remote_addr),
                "fw": dev.get('fw_version'),
                "battery": dev.get('battery_voltage'),
                "ssid": dev.get('wifi_ssid')
            }
        else:
            device_info = {
                "name": "New Device",
                "mac": mac_address,
                "ip": request.headers.get('X-Real-Ip', request.remote_addr),
                "fw": request.headers.get('Fw-Version', '-'),
                "battery": request.headers.get('Battery-Voltage', '-'),
                "ssid": request.headers.get('Wifi-Ssid', '-')
            }

    buf = _safe_generate(generate_testcard, mac_address, device_info=device_info)
    return _serve_image(buf, mac_address)

@app.route('/xkcd')
def get_xkcd():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_xkcd)
    return _serve_image(buf, mac_address)

@app.route('/fmi')
def get_fmi():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_fmi)
    return _serve_image(buf, mac_address)

@app.route('/weather')
def get_weather():
    mac_address = _get_mac_from_request()
    coords = "60.1581787,24.8668912"  # Default
    tz_name = "Europe/Helsinki"

    if mac_address:
        loc, tz = get_device_location_timezone(DB_PATH, mac_address)
        if loc:
            coords = loc
        if tz:
            tz_name = tz

    buf = _safe_generate(generate_weather, coords=coords, device_tz_name=tz_name)
    return _serve_image(buf, mac_address)

@app.route('/map')
def get_map():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_map, DB_PATH, mac_address)
    return _serve_image(buf, mac_address)

@app.route('/clock')
def get_clock():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_clock, DB_PATH, mac_address)
    return _serve_image(buf, mac_address, cache_private=True)

@app.route('/text')
def get_text():
    mac_address = _get_mac_from_request()
    text = "Silent Movie Title"

    if mac_address:
        dev = get_device(DB_PATH, mac_address)
        if dev and dev.get('custom_text'):
            text = dev.get('custom_text')

    buf = _safe_generate(generate_titlecard, text)
    return _serve_image(buf, mac_address)

@app.route('/low-battery')
def get_low_battery():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_titlecard, LOW_BATTERY_MESSAGE)
    return _serve_image(buf, mac_address)

@app.route('/logs')
def logs_endpoint():
    """Display the last display request/response and device log for a device in pretty HTML."""
    return get_logs(DB_PATH)

# consider WSGI server (e.g. gunicorn 'trmnl:app'), where __main__ is never executed and the schema would otherwise never be created/upgraded.
init_db(DB_PATH)

if __name__ == '__main__':
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=True)
