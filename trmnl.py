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
from src.spectra6 import is_spectra6_model, convert_to_spectra6, convert_to_grayscale
from src.configure import handle_register
from src.logs import get_logs
from src.timing import align_refresh, MIN_REFRESH_SECONDS
from src.influx import write_battery_voltage
from src.db import (
    init_db,
    get_device,
    get_device_by_api_key,
    get_latest_display_request,
    device_exists,
    register_device,
    update_device_last_seen,
    get_refresh_lead_seconds,
    get_device_location_timezone,
    get_device_field,
    add_log_entries,
    log_display_request,
    normalize_mac,
    update_low_battery_alert,
    has_battery,
    cleanup_old_display_requests,
    cleanup_past_events,
)

app = Flask(__name__)

DB_PATH = 'trmnl.db'
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')
CONTENT_IMAGE_EXTENSIONS = ('.png', '.bmp', '.jpg', '.jpeg', '.gif')
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

def _get_model_from_request():
    """Use the request's model, or infer it from the device's latest poll."""
    model = request.headers.get('Model')
    if model:
        return model
    # Allow specifying model via query param for preview mode
    model = request.args.get('model')
    if model:
        return model
    # Allow specifying model via query param for preview mode
    model = request.args.get('model')
    if model:
        return model
    # Allow specifying model via query param for preview mode
    model = request.args.get('model')
    if model:
        return model
    mac_address = _get_mac_from_request()
    latest_request = get_latest_display_request(DB_PATH, mac_address)
    if latest_request:
        return latest_request.get('request_headers', {}).get('Model')
    return None

def _device_should_flip(mac_address):
    if not mac_address:
        return False
    dev = get_device(DB_PATH, mac_address)
    return bool(dev and dev.get('flip'))


def _should_apply_image_rotation(mac_address, *, preview=False, device_flip=None):
    """Preview renders should stay upright even if the device is configured upside-down."""
    if preview:
        return False
    if device_flip is None:
        device_flip = _device_should_flip(mac_address)
    return bool(device_flip)


def _request_preview_mode():
    value = request.args.get('preview', '')
    return str(value).lower() in {'1', 'true', 'yes'}


def _find_content_image(item):
    """Find a static content image by playlist basename."""
    for extension in CONTENT_IMAGE_EXTENSIONS:
        filepath = os.path.join(CONTENT_DIR, f'{item}{extension}')
        if os.path.isfile(filepath):
            return filepath
    return None


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

def _serve_image(buf, mac_address, cache_private=False, preview=False, resize_width=None, resize_height=None):
    """Apply device rotation, apply color dithering, set no-cache headers,
    and send a PNG buffer.
    Falls back to the testcard if the generator failed to produce an image.
    When preview=True with resize dimensions, the image is resized before color conversion."""
    if buf is None:
        buf = generate_testcard(mac_address, device_info={})

    should_flip = _should_apply_image_rotation(mac_address, preview=preview)
    buf = apply_rotation(buf, should_flip)

    # Resize for preview mode
    if preview and resize_width and resize_height:
        try:
            buf.seek(0)
            img = Image.open(buf)
            img = img.resize((resize_width, resize_height), Image.LANCZOS)
            new_buf = io.BytesIO()
            img.save(new_buf, format='PNG')
            new_buf.seek(0)
            buf = new_buf
        except Exception as e:
            if app.debug:
                print(f"ERROR in preview resize: {str(e)}")

    # Color Spectra 6 panels (Waveshare Photo Painter / reTerminal E1002) report
    # their model in the "Model" header. For those, convert the rendered image
    # to a dithered 6-color Spectra 6 palette PNG so the firmware decodes it
    # into the 192 KB 4-bpp framebuffer. Monochrome devices get 4-level grayscale.
    model = _get_model_from_request()
    if is_spectra6_model(model):
        if preview:
            # For preview: convert to RGB with Spectra 6 colors (not palette) so browsers can display
            buf = _convert_to_spectra6_rgb(buf)
        else:
            buf = convert_to_spectra6(buf)
    else:
        if preview:
            # For preview: convert to RGB with 4-level grayscale (not palette) so browsers can display
            buf = _convert_to_grayscale_rgb(buf)
        else:
            buf = convert_to_grayscale(buf)

    # Read the PNG bytes and return a plain Response. Using send_file() would make Werkzeug add its own Date/Last-Modified/ETag headers on top of the ones the dev server's http.server handler writes, producing a duplicated "Date" header that nginx rejects with a 502 Bad Gateway.
    buf.seek(0)
    data = buf.read()
    resp = make_response(data, 200, {'Content-Type': 'image/png'})
    cache_control = 'no-store, no-cache, must-revalidate, max-age=0'
    if cache_private:
        cache_control += ', private'
    resp.headers['Cache-Control'] = cache_control
    return resp


def _convert_to_spectra6_rgb(buf):
    """Convert image to RGB with Spectra 6 colors quantized (not palette) for browser preview."""
    from src.spectra6 import _build_palette_image
    buf.seek(0)
    img = Image.open(buf)
    # Convert to RGB first so quantize sees actual pixel values (not palette
    # indices). Then quantize to the Spectra 6 palette, then convert back to
    # RGB so browsers can render the colors.
    img_rgb = img.convert('RGB')
    pal_img = img_rgb.quantize(palette=_build_palette_image())
    rgb_img = pal_img.convert('RGB')
    out = io.BytesIO()
    rgb_img.save(out, format='PNG')
    out.seek(0)
    return out


def _convert_to_grayscale_rgb(buf):
    """Convert image to RGB with 4-level grayscale (not palette) for browser preview."""
    from src.spectra6 import _build_gray4_palette_image
    buf.seek(0)
    img = Image.open(buf)
    # Convert to RGB first so quantize sees actual pixel values (not palette
    # indices). Then quantize to the 4-level grayscale palette, then convert
    # back to RGB so browsers can render the gray levels.
    img_rgb = img.convert('RGB')
    pal_img = img_rgb.quantize(palette=_build_gray4_palette_image())
    rgb_img = pal_img.convert('RGB')
    out = io.BytesIO()
    rgb_img.save(out, format='PNG')
    out.seek(0)
    return out

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
    if device:
        device['low_battery_alert'] = int(low_battery_alert)

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
            content_state = f"schedule-layout-2-{events_hash}-{corrected_dt.strftime('%Y-%m-%d-%H')}"
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
            # Static image stored in the content directory.
            content_path = _find_content_image(item)
            content_filename = os.path.basename(content_path) if content_path else f'{item}.png'
            image_url = f"{BASE_URL}/content/{content_filename}?mac_address={mac_q}"
            try:
                mtime = os.path.getmtime(content_path)
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
        'Model': request.headers.get('Model'),
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
    is_preview = request.args.get('preview', '').lower() == '1'
    should_flip = _device_should_flip(mac_address) and not is_preview

    with open(filepath, 'rb') as f:
        buf = io.BytesIO(f.read())

    try:
        if should_flip:
            buf = apply_rotation(buf, True)
        else:
            image = Image.open(buf)
            png_buf = io.BytesIO()
            image.save(png_buf, format='PNG')
            buf = png_buf
    except Exception:
        return jsonify({"status": 415, "title": "Unsupported Media Type", "detail": "Content is not a readable image"}), 415

    # Color Spectra 6 panels get a dithered 6-color palette PNG (see _serve_image).
    if is_spectra6_model(_get_model_from_request()):
        buf = convert_to_spectra6(buf)
    else:
        # Standard 4-gray panels: a full-color PNG of a photo can exceed the
        # firmware's MAX_IMAGE_SIZE (90 KB). Convert to a 4-level grayscale
        # (2-bit) PNG, which matches the panel's actual capability and stays
        # small. The firmware would reduce the bit depth anyway.
        buf = convert_to_grayscale(buf)

    # For preview mode, resize to 800x480 if not already
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=is_preview, resize_width=rw, resize_height=rh)

@app.route('/schedule')
def get_schedule():
    mac_address = _get_mac_from_request()
    is_generic = not (mac_address and device_exists(DB_PATH, mac_address))
    width = int(get_device_field(DB_PATH, mac_address, 'display_width')) if mac_address else 800
    height = int(get_device_field(DB_PATH, mac_address, 'display_height')) if mac_address else 480
    left_margin = int(get_device_field(DB_PATH, mac_address, 'left_margin')) if mac_address else 50
    right_margin = int(get_device_field(DB_PATH, mac_address, 'right_margin')) if mac_address else 50
    top_margin = int(get_device_field(DB_PATH, mac_address, 'top_margin')) if mac_address else 0
    bottom_margin = int(get_device_field(DB_PATH, mac_address, 'bottom_margin')) if mac_address else 30
    model = _get_model_from_request()
    buf = _safe_generate(generate_schedule, is_generic=is_generic, mac_address=mac_address, width=width, height=height, left_margin=left_margin, right_margin=right_margin, top_margin=top_margin, bottom_margin=bottom_margin, model=model)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

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
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/xkcd')
def get_xkcd():
    mac_address = _get_mac_from_request()
    width = int(get_device_field(DB_PATH, mac_address, 'display_width')) if mac_address else 800
    height = int(get_device_field(DB_PATH, mac_address, 'display_height')) if mac_address else 480
    left_margin = int(get_device_field(DB_PATH, mac_address, 'left_margin')) if mac_address else 50
    right_margin = int(get_device_field(DB_PATH, mac_address, 'right_margin')) if mac_address else 50
    top_margin = int(get_device_field(DB_PATH, mac_address, 'top_margin')) if mac_address else 0
    bottom_margin = int(get_device_field(DB_PATH, mac_address, 'bottom_margin')) if mac_address else 30
    buf = _safe_generate(generate_xkcd, width=width, height=height, left_margin=left_margin, right_margin=right_margin, top_margin=top_margin, bottom_margin=bottom_margin)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/fmi')
def get_fmi():
    mac_address = _get_mac_from_request()
    width = int(get_device_field(DB_PATH, mac_address, 'display_width')) if mac_address else 800
    height = int(get_device_field(DB_PATH, mac_address, 'display_height')) if mac_address else 480
    left_margin = int(get_device_field(DB_PATH, mac_address, 'left_margin')) if mac_address else 50
    right_margin = int(get_device_field(DB_PATH, mac_address, 'right_margin')) if mac_address else 50
    top_margin = int(get_device_field(DB_PATH, mac_address, 'top_margin')) if mac_address else 0
    bottom_margin = int(get_device_field(DB_PATH, mac_address, 'bottom_margin')) if mac_address else 30
    buf = _safe_generate(generate_fmi, width=width, height=height, left_margin=left_margin, right_margin=right_margin, top_margin=top_margin, bottom_margin=bottom_margin)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

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

    width = int(get_device_field(DB_PATH, mac_address, 'display_width')) if mac_address else 800
    height = int(get_device_field(DB_PATH, mac_address, 'display_height')) if mac_address else 480
    left_margin = int(get_device_field(DB_PATH, mac_address, 'left_margin')) if mac_address else 50
    right_margin = int(get_device_field(DB_PATH, mac_address, 'right_margin')) if mac_address else 50
    top_margin = int(get_device_field(DB_PATH, mac_address, 'top_margin')) if mac_address else 0
    bottom_margin = int(get_device_field(DB_PATH, mac_address, 'bottom_margin')) if mac_address else 30
    buf = _safe_generate(generate_weather, coords=coords, device_tz_name=tz_name, width=width, height=height, left_margin=left_margin, right_margin=right_margin, top_margin=top_margin, bottom_margin=bottom_margin)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/map')
def get_map():
    mac_address = _get_mac_from_request()
    width = int(get_device_field(DB_PATH, mac_address, 'display_width')) if mac_address else 800
    height = int(get_device_field(DB_PATH, mac_address, 'display_height')) if mac_address else 480
    left_margin = int(get_device_field(DB_PATH, mac_address, 'left_margin')) if mac_address else 50
    right_margin = int(get_device_field(DB_PATH, mac_address, 'right_margin')) if mac_address else 50
    top_margin = int(get_device_field(DB_PATH, mac_address, 'top_margin')) if mac_address else 0
    bottom_margin = int(get_device_field(DB_PATH, mac_address, 'bottom_margin')) if mac_address else 30
    buf = _safe_generate(generate_map, DB_PATH, mac_address, width=width, height=height, left_margin=left_margin, right_margin=right_margin, top_margin=top_margin, bottom_margin=bottom_margin)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/clock')
def get_clock():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_clock, DB_PATH, mac_address)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, cache_private=True, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/text')
def get_text():
    mac_address = _get_mac_from_request()
    text = "Silent Movie Title"

    if mac_address:
        dev = get_device(DB_PATH, mac_address)
        if dev and dev.get('custom_text'):
            text = dev.get('custom_text')

    buf = _safe_generate(generate_titlecard, text)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/low-battery')
def get_low_battery():
    mac_address = _get_mac_from_request()
    buf = _safe_generate(generate_titlecard, LOW_BATTERY_MESSAGE)
    preview = _request_preview_mode()
    rw = int(request.args.get('resize_width')) if request.args.get('resize_width') else None
    rh = int(request.args.get('resize_height')) if request.args.get('resize_height') else None
    return _serve_image(buf, mac_address, preview=preview, resize_width=rw, resize_height=rh)

@app.route('/logs')
def logs_endpoint():
    """Display the last display request/response and device log for a device in pretty HTML."""
    return get_logs(DB_PATH)

# consider WSGI server (e.g. gunicorn 'trmnl:app'), where __main__ is never executed and the schema would otherwise never be created/upgraded.
init_db(DB_PATH)

if __name__ == '__main__':
    # Run the app on all interfaces at port 5002
    app.run(host='0.0.0.0', port=5002, debug=True)
