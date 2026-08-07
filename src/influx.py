"""
InfluxDB client for writing metrics (e.g., battery voltage) from TRMNL devices.
Non-blocking async writes to avoid impacting the main request/response loop.
"""
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import URLError
import json


INFLUX_HOST = 'localhost'
INFLUX_PORT = 8086
INFLUX_DB = 'trmnl'
INFLUX_URL = f'http://{INFLUX_HOST}:{INFLUX_PORT}/write'


def _build_line_protocol(measurement, tags, fields, timestamp=None):
    """
    Build InfluxDB line protocol format.
    Example: battery_voltage,mac_address=AA:BB:CC:DD:EE:FF voltage=4.2,rssi=-45 1234567890000
    
    Args:
        measurement (str): Measurement name
        tags (dict): Tags (indexed, string values)
        fields (dict): Fields (numeric values)
        timestamp (int): Nanosecond precision timestamp; if None, InfluxDB uses server time
    
    Returns:
        str: Line protocol string
    """
    # Build tag string
    tag_str = ','.join(f'{k}={v}' for k, v in sorted(tags.items())) if tags else ''
    
    # Build field string
    field_parts = []
    for k, v in fields.items():
        if isinstance(v, str):
            field_parts.append(f'{k}="{v}"')
        elif isinstance(v, (int, float)):
            field_parts.append(f'{k}={v}')
    field_str = ','.join(field_parts)
    
    # Build line protocol
    line = measurement
    if tag_str:
        line += f',{tag_str}'
    line += f' {field_str}'
    if timestamp is not None:
        line += f' {timestamp}'
    
    return line


def write_battery_voltage(mac_address, battery_voltage, fw_version=None, wifi_ssid=None, rssi=None):
    """
    Write battery voltage metric to InfluxDB asynchronously.
    
    Args:
        mac_address (str): MAC address of the device (used as tag)
        battery_voltage (str or float): Battery voltage value
        fw_version (str, optional): Firmware version
        wifi_ssid (str, optional): WiFi SSID
        rssi (str or int, optional): Signal strength
    """
    def _write():
        try:
            tags = {'mac_address': str(mac_address)}
            fields = {}
            
            # Parse battery voltage as float
            if battery_voltage and battery_voltage != '-':
                try:
                    fields['voltage'] = float(battery_voltage)
                except (ValueError, TypeError):
                    fields['voltage_raw'] = str(battery_voltage)
            
            # Add optional fields
            if fw_version and fw_version != '-':
                fields['fw_version'] = str(fw_version)
            if wifi_ssid and wifi_ssid != '-':
                fields['wifi_ssid'] = str(wifi_ssid)
            if rssi and rssi != '-':
                try:
                    fields['rssi'] = int(rssi)
                except (ValueError, TypeError):
                    fields['rssi_raw'] = str(rssi)
            
            if not fields:
                # Nothing to write
                return
            
            line = _build_line_protocol('battery_voltage', tags, fields)
            _write_to_influx(line)
        except Exception as e:
            print(f"ERROR writing battery voltage to InfluxDB: {e}")
    
    # Launch in background thread to avoid blocking request/response
    thread = threading.Thread(target=_write, daemon=True)
    thread.start()


def _write_to_influx(line_protocol):
    """
    Write line protocol to InfluxDB via HTTP.
    
    Args:
        line_protocol (str): InfluxDB line protocol string
    
    Raises:
        URLError: If the HTTP request fails
    """
    params = f'db={INFLUX_DB}'
    url = f'{INFLUX_URL}?{params}'
    
    data = line_protocol.encode('utf-8')
    req = Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/octet-stream')
    
    try:
        with urlopen(req, timeout=5) as response:
            status = response.status
            if status not in (200, 204):
                body = response.read().decode('utf-8', errors='ignore')
                print(f"InfluxDB write returned {status}: {body}")
    except URLError as e:
        print(f"ERROR connecting to InfluxDB at {INFLUX_URL}: {e}")
        raise
