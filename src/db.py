import sqlite3
import time
import json
import secrets
import datetime

LEGACY_EVENTS_DEVICE_MAC = '44:1B:F6:81:9B:FC'

# Column order used whenever a full device row is read back out as a dict.
DEVICE_COLUMNS = [
    'mac_address', 'name', 'location', 'playlist', 'refresh_rate', 'last_update',
    'last_filename', 'battery_voltage', 'fw_version', 'wifi_ssid', 'rssi', 'flip',
    'sleep_start', 'sleep_end', 'sleep_weekend', 'timezone', 'custom_url',
    'custom_text', 'playlist_index', 'api_key'
    , 'refresh_lead_seconds', 'refresh_lead_samples', 'next_refresh', 'low_battery_alert'
]


def normalize_mac(mac):
    """Canonicalize a MAC address to uppercase, colon-separated form so the
    same device is always keyed identically regardless of whether the client
    sends 'AA:BB:CC:DD:EE:FF', 'aabbccddeeff', etc. Returns None for empty input."""
    if not mac:
        return None
    cleaned = ''.join(ch for ch in str(mac).strip().upper() if ch in '0123456789ABCDEF')
    if len(cleaned) != 12:
        # Not a standard 48-bit MAC; return as-is (uppercased) to avoid data loss.
        return str(mac).strip().upper()
    return ':'.join(cleaned[i:i + 2] for i in range(0, 12, 2))


def init_db(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                mac_address TEXT PRIMARY KEY,
                name TEXT,
                location TEXT,
                playlist TEXT,
                refresh_rate INTEGER DEFAULT 900,
                last_update TEXT,
                last_filename TEXT,
                battery_voltage TEXT,
                fw_version TEXT,
                wifi_ssid TEXT,
                flip INTEGER DEFAULT 0,
                sleep_start TEXT,
                sleep_end TEXT,
                sleep_weekend INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'Europe/Helsinki',
                custom_url TEXT,
                custom_text TEXT
            )
        ''')
        # Additive, backward-compatible schema changes.
        for statement in (
            'ALTER TABLE devices ADD COLUMN playlist_index INTEGER DEFAULT 0',
            'ALTER TABLE devices ADD COLUMN api_key TEXT',
            'ALTER TABLE devices ADD COLUMN rssi TEXT',
            'ALTER TABLE devices ADD COLUMN refresh_lead_seconds REAL DEFAULT 5',
            'ALTER TABLE devices ADD COLUMN refresh_lead_samples INTEGER DEFAULT 0',
            'ALTER TABLE devices ADD COLUMN next_refresh INTEGER',
            'ALTER TABLE devices ADD COLUMN low_battery_alert INTEGER DEFAULT 0',
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass

        conn.execute('''
            CREATE TABLE IF NOT EXISTS holidays (
                date TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (date, name, source)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS events (
                event_time DATETIME NOT NULL,
                title TEXT NOT NULL,
                mac_address TEXT NOT NULL
            )
        ''')
        try:
            conn.execute('ALTER TABLE events ADD COLUMN mac_address TEXT')
        except sqlite3.OperationalError:
            pass
        conn.execute(
            'UPDATE events SET mac_address = ? WHERE mac_address IS NULL',
            (LEGACY_EVENTS_DEVICE_MAC,)
        )

        conn.execute('''
            CREATE TABLE IF NOT EXISTS device_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_address TEXT,
                message TEXT,
                created_at TEXT
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS display_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_address TEXT,
                request_headers TEXT,
                response_body TEXT,
                status_code INTEGER,
                created_at TEXT
            )
        ''')


def _row_to_device(row):
    if not row:
        return None
    return dict(zip(DEVICE_COLUMNS, row))


def get_device(db_path, mac_address):
    if not mac_address:
        return None
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {", ".join(DEVICE_COLUMNS)} FROM devices WHERE mac_address = ?',
            (mac_address,)
        )
        row = cursor.fetchone()
    return _row_to_device(row)


def get_device_by_api_key(db_path, api_key):
    if not api_key:
        return None
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {", ".join(DEVICE_COLUMNS)} FROM devices WHERE api_key = ?',
            (api_key,)
        )
        row = cursor.fetchone()
    return _row_to_device(row)


def device_exists(db_path, mac_address):
    if not mac_address:
        return False
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM devices WHERE mac_address = ?', (mac_address,))
        return cursor.fetchone() is not None


def register_device(db_path, mac_address, generated_api_key):
    """
    Ensure a device row exists for `mac_address`. If the device is brand new,
    it is inserted with `generated_api_key`. If it already exists but has no
    api_key yet, `generated_api_key` is stored. Otherwise the existing
    api_key is left untouched. Returns the effective api_key.
    """
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT api_key FROM devices WHERE mac_address = ?', (mac_address,))
        row = cursor.fetchone()
        if row is None:
            conn.execute(
                'INSERT INTO devices (mac_address, name, api_key) VALUES (?, ?, ?)',
                (mac_address, f"Device {mac_address[-5:]}", generated_api_key)
            )
            return generated_api_key
        if row[0]:
            return row[0]
        conn.execute('UPDATE devices SET api_key = ? WHERE mac_address = ?', (generated_api_key, mac_address))
        return generated_api_key


def update_device_last_seen(db_path, mac_address, last_filename, battery, fw, ssid, refresh_rate, rssi=None, playlist_index=None, observed_lead_seconds=None, next_refresh=None):
    if not mac_address:
        return
    mac_address = normalize_mac(mac_address)
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(db_path) as conn:
        if observed_lead_seconds is not None:
            observed = min(30.0, max(1.0, float(observed_lead_seconds)))
            row = conn.execute(
                'SELECT refresh_lead_seconds, refresh_lead_samples FROM devices WHERE mac_address = ?',
                (mac_address,)
            ).fetchone()
            old_lead = float(row[0]) if row and row[0] is not None else 5.0
            samples = int(row[1]) if row and row[1] is not None else 0
            # EWMA: recent observations matter more, while retaining history.
            alpha = 0.25 if samples else 1.0
            lead = old_lead + alpha * (observed - old_lead)
            samples = min(samples + 1, 1000000)
            conn.execute(
                'UPDATE devices SET refresh_lead_seconds = ?, refresh_lead_samples = ? WHERE mac_address = ?',
                (lead, samples, mac_address)
            )

        if playlist_index is not None:
            conn.execute('''
                UPDATE devices
                SET last_update = ?, last_filename = ?, battery_voltage = ?, fw_version = ?, wifi_ssid = ?, rssi = ?, refresh_rate = ?, playlist_index = ?, next_refresh = ?
                WHERE mac_address = ?
            ''', (now_str, last_filename, battery, fw, ssid, rssi, refresh_rate, playlist_index, next_refresh, mac_address))
        else:
            conn.execute('''
                UPDATE devices
                SET last_update = ?, last_filename = ?, battery_voltage = ?, fw_version = ?, wifi_ssid = ?, rssi = ?, refresh_rate = ?, next_refresh = ?
                WHERE mac_address = ?
            ''', (now_str, last_filename, battery, fw, ssid, rssi, refresh_rate, next_refresh, mac_address))


def get_refresh_lead_seconds(db_path, mac_address, default=5.0):
    value = get_device_field(db_path, mac_address, 'refresh_lead_seconds')
    try:
        return min(30.0, max(1.0, float(value))) if value is not None else default
    except (TypeError, ValueError):
        return default


def get_device_location_timezone(db_path, mac_address):
    dev = get_device(db_path, mac_address)
    if not dev:
        return None, None
    return dev.get('location'), dev.get('timezone')


def add_log_entries(db_path, mac_address, logs):
    if not logs:
        return
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(db_path) as conn:
        for entry in logs:
            message = json.dumps(entry) if isinstance(entry, (dict, list)) else str(entry)
            conn.execute(
                'INSERT INTO device_logs (mac_address, message, created_at) VALUES (?, ?, ?)',
                (mac_address, message, now_str)
            )


def log_display_request(db_path, mac_address, request_headers, response_body, status_code):
    """Persist the headers/params received from a device on /api/display and
    the JSON we sent back, for troubleshooting device-side fetch failures."""
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'INSERT INTO display_requests (mac_address, request_headers, response_body, status_code, created_at) VALUES (?, ?, ?, ?, ?)',
            (mac_address, json.dumps(request_headers), json.dumps(response_body), status_code, now_str)
        )


def list_devices(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT mac_address, name FROM devices ORDER BY name')
        rows = cursor.fetchall()
    return [{'mac_address': r[0], 'name': r[1] or r[0]} for r in rows]


def get_device_field(db_path, mac_address, field):
    """Fetch a single column for a device, e.g. 'timezone', 'location', 'name'.
    Returns None if the device or the value doesn't exist."""
    if not mac_address:
        return None
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT {field} FROM devices WHERE mac_address = ?', (mac_address,))
        row = cursor.fetchone()
    return row[0] if row else None


def get_device_timezone(db_path, mac_address, default="Europe/Helsinki"):
    return get_device_field(db_path, mac_address, 'timezone') or default


def get_device_location(db_path, mac_address):
    return get_device_field(db_path, mac_address, 'location')


def get_device_name(db_path, mac_address):
    return get_device_field(db_path, mac_address, 'name')


def update_low_battery_alert(db_path, mac_address, battery_voltage):
    """Update and return a device's low-battery alert state.

    The alert activates below 2.9V and remains active until a reading above 3.0V.
    Invalid readings leave the existing state unchanged.
    """
    mac_address = normalize_mac(mac_address)
    if not mac_address:
        return False

    try:
        voltage = float(battery_voltage)
    except (TypeError, ValueError):
        voltage = None

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            'SELECT low_battery_alert FROM devices WHERE mac_address = ?',
            (mac_address,)
        ).fetchone()
        if not row:
            return False

        alert_active = bool(row[0])
        if voltage is not None:
            if voltage < 2.9:
                alert_active = True
            elif voltage > 3.0:
                alert_active = False

        conn.execute(
            'UPDATE devices SET low_battery_alert = ? WHERE mac_address = ?',
            (int(alert_active), mac_address),
        )
    return alert_active


def upsert_device_config(db_path, mac_address, name, location, playlist, refresh_rate,
                          flip, sleep_start, sleep_end, sleep_weekend, timezone,
                          custom_url, custom_text):
    """Insert or update a device's full configuration, as submitted from the
    /configure form."""
    mac_address = normalize_mac(mac_address)
    generated_api_key = secrets.token_urlsafe(16)
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            INSERT INTO devices (mac_address, name, location, playlist, refresh_rate, flip, sleep_start, sleep_end, sleep_weekend, timezone, custom_url, custom_text, api_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mac_address) DO UPDATE SET
            name=excluded.name, location=excluded.location, playlist=excluded.playlist, refresh_rate=excluded.refresh_rate,
            flip=excluded.flip, sleep_start=excluded.sleep_start, sleep_end=excluded.sleep_end, sleep_weekend=excluded.sleep_weekend,
            timezone=excluded.timezone, custom_url=excluded.custom_url, custom_text=excluded.custom_text,
            api_key=COALESCE(devices.api_key, excluded.api_key)
        ''', (mac_address, name, location, playlist, refresh_rate, flip, sleep_start, sleep_end, sleep_weekend, timezone, custom_url, custom_text, generated_api_key))


def get_device_config_row(db_path, mac_address):
    """Fetch the subset of device columns used to render the /configure form.
    Returns a dict (with the same keys used previously as positional indices
    0..15) or None if the device doesn't exist."""
    if not mac_address:
        return None
    mac_address = normalize_mac(mac_address)
    columns = [
        'name', 'location', 'playlist', 'refresh_rate', 'last_update', 'last_filename',
        'battery_voltage', 'fw_version', 'wifi_ssid', 'flip', 'sleep_start', 'sleep_end',
        'sleep_weekend', 'timezone', 'custom_url', 'custom_text',
    ]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {", ".join(columns)} FROM devices WHERE mac_address = ?',
            (mac_address,)
        )
        row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(columns, row))


def ensure_holiday_tables(conn):
    """Create the holidays table on an already-open connection if missing.
    Exposed for callers that batch multiple statements on one connection."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (date, name, source)
        )
    ''')


def replace_holidays_for_year(db_path, year, entries):
    """Delete existing holiday rows for `year` and insert `entries`
    (a list of (date, name, source) tuples)."""
    with sqlite3.connect(db_path) as conn:
        ensure_holiday_tables(conn)
        conn.execute('DELETE FROM holidays WHERE substr(date, 1, 4) = ?', (str(year),))
        if entries:
            conn.executemany(
                'INSERT OR REPLACE INTO holidays (date, name, source) VALUES (?, ?, ?)',
                entries,
            )


def get_holidays_for_date(db_path, date_str):
    """Return a de-duplicated, ordered list of holiday names for an ISO date string."""
    with sqlite3.connect(db_path) as conn:
        ensure_holiday_tables(conn)
        rows = conn.execute(
            'SELECT name FROM holidays WHERE date = ? ORDER BY source, name',
            (date_str,)
        ).fetchall()
    names = []
    for row in rows:
        name = row[0]
        if name and name not in names:
            names.append(name)
    return names


def add_event(db_path, mac_address, event_time, title):
    """Insert a new event for a device into the events table."""
    mac_address = normalize_mac(mac_address)
    if not mac_address or not event_time or not title:
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                'INSERT INTO events (event_time, title, mac_address) VALUES (?, ?, ?)',
                (event_time, title, mac_address)
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"Error adding event: {e}")
        return False


def get_events_raw(db_path, mac_address):
    """Return a device's raw (event_time, title) rows, ordered."""
    mac_address = normalize_mac(mac_address)
    if not mac_address:
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            'SELECT event_time, title FROM events WHERE mac_address = ? '
            'ORDER BY event_time ASC, title ASC',
            (mac_address,)
        ).fetchall()
    return rows


def get_latest_display_request(db_path, mac_address):
    """Retrieve the most recent display request and response for a device."""
    if not mac_address:
        return None
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            'SELECT request_headers, response_body, status_code, created_at FROM display_requests WHERE mac_address = ? ORDER BY created_at DESC LIMIT 1',
            (mac_address,)
        ).fetchone()
    if not row:
        return None
    return {
        'request_headers': json.loads(row[0]) if row[0] else {},
        'response_body': json.loads(row[1]) if row[1] else {},
        'status_code': row[2],
        'created_at': row[3]
    }


def get_latest_device_log(db_path, mac_address):
    """Retrieve the most recent device log message for a device."""
    if not mac_address:
        return None
    mac_address = normalize_mac(mac_address)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            'SELECT message, created_at FROM device_logs WHERE mac_address = ? ORDER BY created_at DESC LIMIT 1',
            (mac_address,)
        ).fetchone()
    if not row:
        return None
    return {
        'message': json.loads(row[0]) if row[0] else {},
        'created_at': row[1]
    }


def delete_device(db_path, mac_address):
    """Delete a device and all its related logs from the database."""
    if not mac_address:
        return False
    mac_address = normalize_mac(mac_address)
    try:
        with sqlite3.connect(db_path) as conn:
            # Delete from devices table
            cursor = conn.execute('DELETE FROM devices WHERE mac_address = ?', (mac_address,))
            deleted_count = cursor.rowcount
            # Also delete related logs
            conn.execute('DELETE FROM device_logs WHERE mac_address = ?', (mac_address,))
            conn.execute('DELETE FROM display_requests WHERE mac_address = ?', (mac_address,))
            conn.commit()
        return deleted_count > 0
    except Exception as e:
        print(f"Error deleting device {mac_address}: {e}")
        return False


def cleanup_old_display_requests(db_path):
    """Delete display_requests older than 1 day, except the latest entry for each mac_address."""
    try:
        with sqlite3.connect(db_path) as conn:
            # Get the current time minus 1 day
            now = time.time()
            one_day_ago = now - (24 * 3600)
            one_day_ago_str = datetime.datetime.fromtimestamp(one_day_ago).isoformat()
            
            # Find all display_requests older than 1 day, grouped by mac_address
            # and exclude the latest one for each mac_address
            cursor = conn.execute('''
                DELETE FROM display_requests
                WHERE id IN (
                    SELECT id FROM display_requests
                    WHERE created_at < ?
                    AND id NOT IN (
                        SELECT MAX(id) FROM display_requests
                        GROUP BY mac_address
                    )
                )
            ''', (one_day_ago_str,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old display requests")
            
            return deleted_count
    except Exception as e:
        print(f"Error cleaning up display requests: {e}")
        return 0


def cleanup_past_events(db_path):
    """Delete events from the events table that have already passed (disappear day after event)."""
    try:
        with sqlite3.connect(db_path) as conn:
            # Cutoff is the start of today (00:00:00) so events remain visible all through the day they happen,
            # and disappear starting the day after.
            today_start = datetime.datetime.now().strftime('%Y-%m-%d 00:00:00')
            
            # Delete all events with event_time before today's start
            cursor = conn.execute(
                'DELETE FROM events WHERE event_time < ?',
                (today_start,)
            )
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} past events")
            
            return deleted_count
    except Exception as e:
        print(f"Error cleaning up past events: {e}")
        return 0
