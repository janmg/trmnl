import sqlite3
import time
import json

# Column order used whenever a full device row is read back out as a dict.
DEVICE_COLUMNS = [
    'mac_address', 'name', 'location', 'playlist', 'refresh_rate', 'last_update',
    'last_filename', 'battery_voltage', 'fw_version', 'wifi_ssid', 'rssi', 'flip',
    'sleep_start', 'sleep_end', 'sleep_weekend', 'timezone', 'custom_url',
    'custom_text', 'playlist_index', 'api_key'
]


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
                title TEXT NOT NULL
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS device_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_address TEXT,
                message TEXT,
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
    mac_address = mac_address.strip().upper()
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
    mac_address = mac_address.strip().upper()
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
    mac_address = mac_address.strip().upper()
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


def update_device_last_seen(db_path, mac_address, last_filename, battery, fw, ssid, refresh_rate, rssi=None, playlist_index=None):
    if not mac_address:
        return
    mac_address = mac_address.strip().upper()
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(db_path) as conn:
        if playlist_index is not None:
            conn.execute('''
                UPDATE devices
                SET last_update = ?, last_filename = ?, battery_voltage = ?, fw_version = ?, wifi_ssid = ?, rssi = ?, refresh_rate = ?, playlist_index = ?
                WHERE mac_address = ?
            ''', (now_str, last_filename, battery, fw, ssid, rssi, refresh_rate, playlist_index, mac_address))
        else:
            conn.execute('''
                UPDATE devices
                SET last_update = ?, last_filename = ?, battery_voltage = ?, fw_version = ?, wifi_ssid = ?, rssi = ?, refresh_rate = ?
                WHERE mac_address = ?
            ''', (now_str, last_filename, battery, fw, ssid, rssi, refresh_rate, mac_address))


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


def list_devices(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT mac_address, name FROM devices ORDER BY name')
        rows = cursor.fetchall()
    return [{'mac_address': r[0], 'name': r[1] or r[0]} for r in rows]
