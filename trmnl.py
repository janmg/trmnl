import os
import io
import time
import hashlib
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response

from src.testcard import generate_testcard
from src.schedule import generate_schedule, get_events
from src.xkcd import generate_xkcd
from src.fmi import generate_fmi
from src.weather import generate_weather
from src.map import generate_map
from src.titlecard import generate_titlecard
from src.clock import generate_clock
from src.configure import handle_register
from src.db import (
    init_db,
    get_device,
    get_device_by_api_key,
    device_exists,
    register_device,
    update_device_last_seen,
    get_device_location_timezone,
    add_log_entries,
)

app = Flask(__name__)

DB_PATH = 'trmnl.db'
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')
BASE_URL = 'https://trmnl.janmg.com'

# How long before the whole minute the device should wake up to fetch its
# next image (the ESP32 needs a moment to connect to wifi and refresh the
# e-paper panel before the target minute actually arrives).
REFRESH_LEAD_SECONDS = 5
# Never tell the device to come back sooner than this.
MIN_REFRESH_SECONDS = 40
# Upper bound for the "come back later" refresh rate handed out while the
# device is inside its configured sleep window, so it can deep sleep for a
# long stretch instead of polling every minute.
MAX_SLEEP_REFRESH_SECONDS = 6 * 3600


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

    resp = make_response(send_file(buf, mimetype='image/png'))
    cache_control = 'no-store, no-cache, must-revalidate, max-age=0'
    if cache_private:
        cache_control += ', private'
    resp.headers['Cache-Control'] = cache_control
    return resp


def align_refresh(now_epoch, interval_seconds, lead_seconds=0, tz_name=None):
    """
    Compute seconds until the next aligned refresh.

    - When `interval_seconds` is a whole number of minutes, align to the next
      multiple-of-interval minute boundary (in the device's local timezone),
      minus `lead_seconds` so the device wakes slightly early.
    - Otherwise, fall back to aligning to epoch multiples of `interval_seconds`.
    - The result is never allowed to be less than `MIN_REFRESH_SECONDS`.
    """
    interval = max(1, int(interval_seconds or 1))
    lead = max(0, int(lead_seconds or 0))

    try:
        now_dt = datetime.fromtimestamp(now_epoch, tz=ZoneInfo(tz_name)) if tz_name else datetime.fromtimestamp(now_epoch)
    except Exception:
        now_dt = datetime.fromtimestamp(now_epoch)

    if interval % 60 == 0:
        interval_min = interval // 60
        minutes_since_midnight = now_dt.hour * 60 + now_dt.minute
        next_minutes = ((minutes_since_midnight // interval_min) + 1) * interval_min

        midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        next_boundary_dt = midnight + timedelta(minutes=next_minutes)
        target_dt = next_boundary_dt - timedelta(seconds=lead)

        if target_dt.timestamp() <= now_epoch:
            next_boundary_dt += timedelta(minutes=interval_min)
            target_dt = next_boundary_dt - timedelta(seconds=lead)

        seconds = int(target_dt.timestamp() - now_epoch)
    else:
        next_boundary = ((int(now_epoch) // interval) + 1) * interval
        target = next_boundary - lead
        if target <= now_epoch:
            target += interval
        seconds = int(target - now_epoch)

    if seconds < MIN_REFRESH_SECONDS:
        seconds += 60
    return seconds


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

    key_alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    candidate_key = ''.join(secrets.choice(key_alphabet) for _ in range(22))
    api_key = register_device(DB_PATH, header_mac, candidate_key)

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
        mac_address = header_mac.strip().upper()
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

    # The api_key is the primary way we identify a provisioned device; the
    # MAC (ID header) is only used as a fallback to drive the registration
    # flow for devices that haven't completed /api/setup yet.
    device = get_device_by_api_key(DB_PATH, access_token) if access_token else None
    if device:
        mac_address = device.get('mac_address')
    elif header_mac:
        mac_address = header_mac.strip().upper()
    else:
        mac_address = None

    if not mac_address:
        return jsonify({
            "status": 401,
            "title": "Unauthorized",
            "detail": "Device identification (Access-Token or ID header) is required."
        }), 401

    now_epoch = time.time()
    forced_refresh = None
    next_playlist_index = None

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

        try:
            corrected_dt = datetime.fromtimestamp(now_epoch, tz=ZoneInfo(device_timezone))
        except Exception:
            corrected_dt = datetime.fromtimestamp(now_epoch)

        if not playlist_items:
            raise LookupError("device has no playlist configured")

        playlist_index = int(device.get('playlist_index') or 0)
        idx = playlist_index % len(playlist_items)
        item = playlist_items[idx].strip()
        next_playlist_index = playlist_index + 1
        selected_item = item

        content_state = ""

        if item == "schedule":
            image_url = f"{BASE_URL}/schedule?mac_address={mac_address}"
            filename = "schedule.png"
            events_hash = hashlib.md5(str(get_events()).encode('utf-8')).hexdigest()
            content_state = f"{events_hash}-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "clock":
            image_url = f"{BASE_URL}/clock?mac_address={mac_address}"
            filename = "clock.png"
            refresh_rate = 60
            next_minute = ((int(now_epoch) // 60) + 1) * 60
            content_state = f"clock-{next_minute}"
            forced_refresh = max(1, int(next_minute - now_epoch))
        elif item == "xkcd":
            image_url = f"{BASE_URL}/xkcd?mac_address={mac_address}"
            filename = "xkcd.png"
            content_state = f"xkcd-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "fmi":
            image_url = f"{BASE_URL}/fmi?mac_address={mac_address}"
            filename = "fmi.png"
            minute_bucket = (corrected_dt.minute // 15) * 15
            content_state = f"fmi-{corrected_dt.strftime('%Y%m%d%H')}{minute_bucket}"
        elif item == "weather":
            image_url = f"{BASE_URL}/weather?mac_address={mac_address}"
            filename = "weather.png"
            content_state = f"weather-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "map":
            image_url = f"{BASE_URL}/map?mac_address={mac_address}"
            filename = "map.png"
            content_state = f"map-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "custom_url" and custom_url:
            image_url = custom_url
            filename = "custom_url.png"
            content_state = f"custom-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        elif item == "custom_text" and custom_text:
            image_url = f"{BASE_URL}/text?mac_address={mac_address}"
            filename = "titlecard.png"
            content_state = f"text-{hashlib.md5(custom_text.encode('utf-8')).hexdigest()}"
        elif item == "testcard":
            image_url = f"{BASE_URL}/testcard?mac_address={mac_address}"
            filename = "testcard.png"
            content_state = f"testcard-{corrected_dt.strftime('%Y-%m-%d-%H')}"
        else:
            # Static PNG stored in the content directory.
            image_url = f"{BASE_URL}/content/{item}.png?mac_address={mac_address}"
            filename = f"{item}.png"
            try:
                mtime = os.path.getmtime(os.path.join(CONTENT_DIR, f"{item}.png"))
                content_state = f"{mtime}"
            except OSError:
                content_state = "missing"

    except Exception as e:
        if app.debug:
            print(f"DISPLAY ERROR for {mac_address}: {e}")
        # Unregistered device, no playlist, or any other error: fall back to
        # the testcard and poll again soon (this also drives new devices
        # towards the /configure registration flow).
        image_url = f"{BASE_URL}/testcard?mac_address={mac_address}"
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
        next_refresh = align_refresh(now_epoch, refresh_rate, REFRESH_LEAD_SECONDS, device_timezone)

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

    # The ETag lets the (modified) firmware decide whether the screen
    # actually needs to be redrawn without re-downloading the image.
    day_string = corrected_dt.strftime('%Y-%m-%d')
    etag_content = f"{filename}-{image_url}-{day_string}-{content_state}-{int(bool(should_flip))}"
    etag = hashlib.md5(etag_content.encode('utf-8')).hexdigest()

    if request.if_none_match and etag in request.if_none_match:
        return '', 304

    update_device_last_seen(DB_PATH, mac_address, filename, battery, fw, ssid, next_refresh, rssi, next_playlist_index)

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

    if should_flip:
        with open(filepath, 'rb') as f:
            buf = io.BytesIO(f.read())
        buf = apply_rotation(buf, True)
        return send_file(buf, mimetype='image/png')

    return send_from_directory(CONTENT_DIR, filename)


@app.route('/schedule')
def get_schedule():
    mac_address = _get_mac_from_request()
    is_generic = not (mac_address and device_exists(DB_PATH, mac_address))
    buf = generate_schedule(is_generic=is_generic, mac_address=mac_address)
    return _serve_image(buf, mac_address)


@app.route('/testcard')
def get_testcard():
    mac_address = _get_mac_from_request()
    device_info = {}
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

    buf = generate_testcard(mac_address, device_info=device_info)
    return _serve_image(buf, mac_address)


@app.route('/xkcd')
def get_xkcd():
    mac_address = _get_mac_from_request()
    buf = generate_xkcd()
    return _serve_image(buf, mac_address)


@app.route('/fmi')
def get_fmi():
    mac_address = _get_mac_from_request()
    buf = generate_fmi()
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

    buf = generate_weather(coords=coords, device_tz_name=tz_name)
    return _serve_image(buf, mac_address)


@app.route('/map')
def get_map():
    mac_address = _get_mac_from_request()
    buf = generate_map(DB_PATH, mac_address)
    return _serve_image(buf, mac_address)


@app.route('/clock')
def get_clock():
    mac_address = _get_mac_from_request()
    buf = generate_clock(DB_PATH, mac_address)
    return _serve_image(buf, mac_address, cache_private=True)


@app.route('/text')
def get_text():
    mac_address = _get_mac_from_request()
    text = "Silent Movie Title"

    if mac_address:
        dev = get_device(DB_PATH, mac_address)
        if dev and dev.get('custom_text'):
            text = dev.get('custom_text')

    buf = generate_titlecard(text)
    return _serve_image(buf, mac_address)


if __name__ == '__main__':
    init_db(DB_PATH)
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=True)
