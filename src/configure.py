import os
import requests
import json
import time
from urllib.parse import quote
from flask import request, render_template_string

from src.db import list_devices, upsert_device_config, get_device_config_row, get_latest_display_request, add_event
from src.timing import align_refresh

def lookup_coordinates_and_timezone(location_str):
    if not location_str:
        return None, None

    # Defaults
    coords = None
    tz_name = "Europe/Helsinki"

    # Check if it already looks like coordinates
    parts = location_str.split(',')
    if len(parts) == 2:
        try:
            float(parts[0])
            float(parts[1])
            coords = location_str.strip()
        except ValueError:
            pass

    # For TZ, we'll try to find an API that gives both.
    # The 'Hacker Way' is often using a public geocoder that includes TZ.
    # nominatim doesn't have it. Let's try the 'Geocode.maps.co' or similar if no key,
    # but for stability we can use the 'lat'/'lon' with a TZ-only API.
    
    try:
        # Step 1: Geocode if not already coordinates
        if not coords:
            url = f"https://nominatim.openstreetmap.org/search?q={quote(location_str)}&format=json&limit=1"
            headers = {'User-Agent': 'TRMNL-Config/1.0'}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data:
                    coords = f"{data[0]['lat']},{data[0]['lon']}"
                    print(f"DEBUG: Geocoded '{location_str}' to {coords}")

        # Step 2: Timezone lookup from coordinates using offline library
        if coords:
            lat, lon = [v.strip() for v in coords.split(',', 1)]
            try:
                from timezonefinder import TimezoneFinder
                tf = TimezoneFinder()
                found = tf.timezone_at(lat=float(lat), lng=float(lon))
                if found:
                    tz_name = found
                    print(f"DEBUG: timezonefinder resolved {coords} -> {tz_name}")
            except Exception as tf_err:
                print(f"DEBUG: timezonefinder error: {tf_err}")
    except Exception as e:
        print(f"Location/TZ lookup error: {e}")

    return coords, tz_name

def render_device_list(db_path):
    devices = list_devices(db_path)

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>TRMNL | Devices</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; margin:0; padding:2rem 1rem; }
            .container { max-width: 640px; margin: 0 auto; background:#fff; border-radius:12px; padding:2rem; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
            h1 { margin-top:0; font-size:1.5rem; border-bottom:2px solid #e2e8f0; padding-bottom:0.5rem; }
            ul { list-style:none; padding:0; margin:0; }
            li { border-bottom:1px solid #e2e8f0; padding:0.75rem 0; display:flex; justify-content:space-between; align-items:center; }
            a { color:#2563eb; text-decoration:none; font-weight:600; }
            .mac { font-family: monospace; color:#64748b; }
            .empty { color:#64748b; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Registered Devices</h1>
            {% if devices %}
            <ul>
                {% for d in devices %}
                <li>
                    <a href="/configure?mac_address={{ d.mac_address }}">{{ d.name }}</a>
                    <span class="mac">{{ d.mac_address }}</span>
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <p class="empty">No devices registered yet.</p>
            {% endif %}
        </div>
    </body>
    </html>
    '''
    return render_template_string(html, devices=devices)


def handle_register(db_path, content_dir):
    mac_address = request.args.get('mac_address') or request.form.get('mac_address')
    if not mac_address:
        return render_device_list(db_path)
    
    mac_address = mac_address.upper()
    message = ""
    
    # Options from static + content directory
    generated_options = ["testcard", "schedule", "xkcd", "fmi", "weather", "clock", "map"]
    content_extensions = ('.png', '.bmp', '.jpg', '.jpeg', '.gif')
    content_files = {}
    for filename in os.listdir(content_dir):
        basename, extension = os.path.splitext(filename)
        if extension.lower() in content_extensions and basename not in content_files:
            content_files[basename] = filename
    content_options = sorted(content_files)
    content_options = [f for f in content_options if f not in generated_options]

    refresh_options = [1, 5, 15, 60, 1440]
    row = get_device_config_row(db_path, mac_address)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_event':
            event_date = request.form.get('event_date', '').strip()
            event_time = request.form.get('event_time', '').strip()
            event_title = request.form.get('event_title', '').strip()
            if event_date and event_time and event_title:
                try:
                    # Parse dd/mm
                    day_str, month_str = [p.strip() for p in event_date.split('/')]
                    day = int(day_str)
                    month = int(month_str)
                    from datetime import datetime
                    try:
                        parsed_time = datetime.strptime(event_time, '%H:%M')
                    except ValueError:
                        parsed_time = datetime.strptime(event_time, '%H:%M:%S')

                    # Get local timezone / current year
                    from zoneinfo import ZoneInfo
                    tz_name = 'Europe/Helsinki'
                    if row and row['timezone']:
                        tz_name = row['timezone']
                    now_local = datetime.now(ZoneInfo(tz_name))
                    current_year = now_local.year
                    
                    event_dt_candidate = datetime(current_year, month, day)
                    if event_dt_candidate.date() < now_local.date():
                        target_year = current_year + 1
                    else:
                        target_year = current_year
                    
                    formatted_dt = (
                        f"{target_year:04d}-{month:02d}-{day:02d} "
                        f"{parsed_time.strftime('%H:%M:%S')}"
                    )
                    if add_event(db_path, mac_address, formatted_dt, event_title):
                        message = f"Event '{event_title}' added!"
                    else:
                        message = "Failed to add event."
                except Exception as e:
                    message = f"Invalid date or time format. Please use dd/mm and HH:MM."
            else:
                message = "Please provide date (dd/mm), time (HH:MM), and event title."
        else:
            selected_items = request.form.getlist('playlist')
            name = request.form.get('name')
            location_raw = request.form.get('location', '').strip()
            location, tz_name = lookup_coordinates_and_timezone(location_raw)
            refresh_min = int(request.form.get('refresh_rate', 1))
            refresh_sec = refresh_min * 60
            flip = 1 if request.form.get('flip') else 0
            sleep_start = request.form.get('sleep_start', '').strip()
            sleep_end = request.form.get('sleep_end', '').strip()
            sleep_weekend = 1 if request.form.get('sleep_weekend') else 0
            custom_url = request.form.get('custom_url', '').strip()
            custom_text = request.form.get('custom_text', '').strip()
            playlist_str = ",".join(selected_items)

            upsert_device_config(
                db_path, mac_address, name or f"Device {mac_address[-5:]}", location, playlist_str,
                refresh_sec, flip, sleep_start, sleep_end, sleep_weekend, tz_name, custom_url, custom_text
            )

            message = f"Device {mac_address} updated!"
            current_timezone = tz_name
            current_location = location

    # GET request or after POST: show form
    row = get_device_config_row(db_path, mac_address)
    if row:
        device_name = row['name']
        current_location = row['location'] or ""
        current_playlist = row['playlist'].split(',') if row['playlist'] else []
        current_refresh_min = (row['refresh_rate'] or 900) // 60
        last_update = row['last_update'] or "Never"
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            tz = row['timezone'] or 'Europe/Helsinki'

            if last_update and last_update != "Never":
                # Get the actual refresh_rate that was communicated to the device
                # in the most recent /api/display response
                latest_req = get_latest_display_request(db_path, mac_address)
                actual_refresh_secs = None
                
                if latest_req and latest_req.get('response_body'):
                    actual_refresh_secs = latest_req['response_body'].get('refresh_rate')
                
                # If no actual refresh rate found, fall back to configured value
                if actual_refresh_secs is None:
                    actual_refresh_secs = int(row['refresh_rate']) if row['refresh_rate'] else 900

                lu_dt = datetime.strptime(last_update, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('UTC'))
                device_tz = ZoneInfo(tz)
                last_update = lu_dt.astimezone(device_tz).strftime('%Y-%m-%d %H:%M:%S')

                # Calculate next_update based on the actual refresh_rate sent to device
                last_update_epoch = lu_dt.timestamp()
                next_refresh_secs = actual_refresh_secs
                next_dt = datetime.fromtimestamp(last_update_epoch + next_refresh_secs, tz=device_tz)
                next_update = next_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                next_update = "Unknown"
        except Exception:
            next_update = "Unknown"
        last_filename = row['last_filename'] or "None"
        battery = row['battery_voltage'] or "-"
        fw_version = row['fw_version'] or "-"
        wifi_ssid = row['wifi_ssid'] or "-"
        current_flip = row['flip'] or 0
        current_sleep_start = row['sleep_start'] or ""
        current_sleep_end = row['sleep_end'] or ""
        current_sleep_weekend = row['sleep_weekend'] or 0
        current_timezone = row['timezone'] or "Europe/Helsinki"
        current_custom_url = row['custom_url'] or ""
        current_custom_text = row['custom_text'] or ""
    else:
        device_name = f"Device {mac_address[-5:]}"
        current_location = ""
        current_playlist = []
        current_refresh_min = 15
        last_update = "Never"
        last_filename = "None"
        battery = "-"
        fw_version = "-"
        wifi_ssid = "-"
        current_flip = 0
        current_sleep_start = ""
        current_sleep_end = ""
        current_sleep_weekend = 0
        current_timezone = "Europe/Helsinki"
        current_custom_url = ""
        current_custom_text = ""
        next_update = "Unknown"

    mac_encoded = quote(mac_address, safe='')
    preview_items = []
    for item in current_playlist:

        label = item
        preview_url = None

        if item == "testcard":
            preview_url = f"/testcard?mac_address={mac_encoded}"
        elif item == "schedule":
            preview_url = f"/schedule?mac_address={mac_encoded}"
        elif item == "xkcd":
            preview_url = "/xkcd"
        elif item == "fmi":
            preview_url = "/fmi"
        elif item == "weather":
            preview_url = "/weather"
        elif item == "clock":
            preview_url = f"/clock?mac_address={mac_encoded}"
        elif item == "map":
            preview_url = f"/map?mac_address={mac_encoded}"
        elif item == "custom_text":
            label = "custom_text"
            preview_url = f"/text?mac_address={mac_encoded}"
        elif item == "custom_url" and current_custom_url:
            label = "custom_url"
            preview_url = current_custom_url
        elif item in content_options:
            preview_url = f"/content/{content_files[item]}"

        if not preview_url:
            continue

        preview_items.append({"name": label, "url": preview_url})

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>TRMNL | Device Configuration</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root {
                --primary-color: #2563eb;
                --bg-color: #f8fafc;
                --card-bg: #ffffff;
                --text-main: #1e293b;
                --text-muted: #64748b;
                --border-color: #e2e8f0;
            }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
                background-color: var(--bg-color);
                color: var(--text-main);
                margin: 0;
                display: flex;
                justify-content: center;
                padding: 2rem 1rem;
            }
            .container {
                background: var(--card-bg);
                padding: 2rem;
                border-radius: 12px;
                box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
                width: 100%;
                max-width: 980px;
            }
            .form-layout {
                display: block;
            }
            .config-column,
            .playlist-column,
            .preview-column {
                min-width: 0;
            }
            h1 { margin-top: 0; font-size: 1.5rem; border-bottom: 2px solid var(--border-color); padding-bottom: 0.5rem; }
            .status-box {
                background: #f1f5f9;
                padding: 1rem;
                border-radius: 8px;
                font-size: 0.875rem;
                margin-bottom: 1.5rem;
            }
            .status-item { margin-bottom: 0.25rem; }
            .status-label { font-weight: 600; color: var(--text-muted); min-width: 100px; display: inline-block; }
            label { font-weight: 500; display: block; margin-bottom: 0.25rem; }
            .form-group { margin-bottom: 1.25rem; }
            input[type="text"], select, textarea {
                width: 100%;
                padding: 0.625rem;
                border: 1px solid var(--border-color);
                border-radius: 6px;
                box-sizing: border-box;
                font-size: 1rem;
            }
            .checkbox-group {
                background: #fff;
                border: 1px solid var(--border-color);
                border-radius: 6px;
                padding: 0.5rem;
            }
            .category-header {
                font-size: 0.75rem;
                font-weight: 700;
                text-transform: uppercase;
                color: var(--text-muted);
                padding: 0.5rem 0.5rem 0.25rem;
                border-bottom: 1px solid var(--border-color);
                margin-bottom: 0.25rem;
                background: #f8fafc;
            }
            .checkbox-item {
                display: flex;
                align-items: center;
                padding: 0.375rem 0.5rem;
                cursor: pointer;
            }
            .checkbox-item:hover { background: #f1f5f9; border-radius: 4px; }
            .checkbox-item input { margin-right: 0.75rem; }
            .checkbox-item label { margin-bottom: 0; cursor: pointer; display: inline; font-weight: 400; }
            button {
                background-color: var(--primary-color);
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 6px;
                font-weight: 600;
                width: 100%;
                cursor: pointer;
                transition: background 0.2s;
                font-size: 1rem;
            }
            button:hover { background-color: #1d4ed8; }
            .message {
                margin-top: 1.5rem;
                padding: 1rem;
                background: #dcfce7;
                color: #166534;
                border-radius: 6px;
                font-weight: 500;
                text-align: center;
            }
            .mac-badge {
                font-family: monospace;
                background: #eee;
                padding: 0.2rem 0.4rem;
                border-radius: 4px;
            }
            .preview-list {
                display: grid;
                gap: 0.75rem;
            }
            .preview-card {
                border: 1px solid var(--border-color);
                border-radius: 8px;
                padding: 0.5rem;
                background: #fff;
            }
            .preview-card img {
                width: 100%;
                aspect-ratio: 5 / 3;
                object-fit: cover;
                border-radius: 6px;
                border: 1px solid var(--border-color);
                background: #f8fafc;
                display: block;
            }
            .preview-link {
                display: block;
                text-decoration: none;
            }
            .preview-name {
                font-size: 0.8rem;
                color: var(--text-muted);
                margin-top: 0.4rem;
                text-align: center;
                word-break: break-word;
            }
            .preview-empty {
                border: 1px dashed var(--border-color);
                border-radius: 8px;
                padding: 1rem;
                color: var(--text-muted);
                background: #f8fafc;
                text-align: center;
                font-size: 0.9rem;
            }
            @media (min-width: 768px) {
                .form-layout {
                    display: grid;
                    grid-template-columns: minmax(280px, 1fr) minmax(380px, 1.25fr);
                    gap: 1.5rem;
                    align-items: start;
                }
                .playlist-column {
                    grid-column: 2;
                }
                .preview-column {
                    grid-column: 1 / -1;
                }
            }
            @media (min-width: 1180px) {
                .container {
                    max-width: 1280px;
                }
                .form-layout {
                    grid-template-columns: minmax(260px, 0.9fr) minmax(360px, 1.15fr) minmax(240px, 0.9fr);
                }
                .playlist-column {
                    grid-column: 2;
                }
                .preview-column {
                    grid-column: 3;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Device Configuration</h1>

            <form method="post">
                <input type="hidden" name="mac_address" value="{{ mac_address }}">

                <div class="form-layout">
                    <div class="config-column">
                        <div class="status-box">
                            <div class="status-item"><span class="status-label">MAC Address:</span> <span class="mac-badge">{{ mac_address }}</span></div>
                            <div class="status-item"><span class="status-label">Last Update:</span> {{ last_update }}</div>
                            <div class="status-item"><span class="status-label">Next Update:</span> {{ next_update }}</div>
                            <div class="status-item"><span class="status-label">Last File:</span> {{ last_filename }}</div>
                            <div class="status-item"><span class="status-label">Status:</span> {{ battery }}V | {{ fw_version }} | {{ wifi_ssid }}</div>
                        </div>

                        <div class="form-group">
                            <label for="name">Friendly Name</label>
                            <input type="text" id="name" name="name" value="{{ device_name }}" placeholder="My TRMNL Screen">
                        </div>

                        <div class="form-group">
                            <label for="location">Location (City or GPS Coordinates)</label>
                            <input type="text" id="location" name="location" value="{{ current_location }}" placeholder="e.g. Helsinki or 60.1699,24.9384">
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">
                                Timezone: {{ current_timezone }}
                            </div>
                        </div>

                        <div class="form-group">
                            <label for="refresh_rate">Refresh Interval</label>
                            <select id="refresh_rate" name="refresh_rate">
                                {% for min in refresh_options %}
                                <option value="{{ min }}" {% if min == current_refresh_min %}selected{% endif %}>{{ min }} minutes</option>
                                {% endfor %}
                            </select>
                        </div>

                        <div class="form-group">
                            <label>Prolonged Sleeping (Stop Refreshing)</label>
                            <div style="display: flex; gap: 0.5rem; margin-bottom: 0.5rem;">
                                <input type="text" name="sleep_start" value="{{ current_sleep_start }}" placeholder="Start (e.g. 22:00)" style="flex: 1;">
                                <input type="text" name="sleep_end" value="{{ current_sleep_end }}" placeholder="End (e.g. 07:00)" style="flex: 1;">
                            </div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="sleep_weekend" name="sleep_weekend" value="1" {% if current_sleep_weekend %}checked{% endif %}>
                                <label for="sleep_weekend">Sleep during weekends</label>
                            </div>
                        </div>

                        <div class="form-group">
                            <div class="checkbox-item">
                                <input type="checkbox" id="flip" name="flip" value="1" {% if current_flip %}checked{% endif %}>
                                <label for="flip">Upside Down (Rotate 180°)</label>
                            </div>
                        </div>

                        <button type="submit">Save Configuration</button>

                        {% if message %}
                        <div class="message">{{ message }}</div>
                        {% endif %}
                    </div>

                    <div class="form-group playlist-column">
                        <label>Playlist Items</label>
                        <div class="checkbox-group">
                            <div class="category-header">Generated</div>
                            {% for option in generated_options %}
                            <div class="checkbox-item">
                                <input type="checkbox" id="{{ option }}" name="playlist" value="{{ option }}"
                                       {% if option in current_playlist %}checked{% endif %}>
                                <label for="{{ option }}">{{ option }}</label>
                            </div>
                            {% endfor %}

                            <div class="category-header">Content</div>
                            {% for option in content_options %}
                            <div class="checkbox-item">
                                <input type="checkbox" id="{{ option }}" name="playlist" value="{{ option }}"
                                       {% if option in current_playlist %}checked{% endif %}>
                                <label for="{{ option }}">{{ option }}</label>
                            </div>
                            {% endfor %}

                            <div class="category-header">Custom URL</div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="use_custom" name="playlist" value="custom_url"
                                       {% if 'custom_url' in current_playlist %}checked{% endif %}>
                                <label for="use_custom">Include Custom URL</label>
                            </div>
                            <div style="padding: 0 0.5rem 0.5rem;">
                                <input type="text" name="custom_url" value="{{ current_custom_url }}" 
                                       placeholder="https://example.com/image.png" 
                                       style="font-size: 0.875rem; padding: 0.5rem;">
                            </div>

                            <div class="category-header">Custom Text</div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="use_text" name="playlist" value="custom_text"
                                       {% if 'custom_text' in current_playlist %}checked{% endif %}>
                                <label for="use_text">Include Silent Movie Card</label>
                            </div>
                            <div style="padding: 0 0.5rem 0.5rem;">
                                <textarea name="custom_text" placeholder="Enter text for movie card..." 
                                          style="height: 80px; font-size: 0.875rem; padding: 0.5rem; border-radius: 4px;">{{ current_custom_text }}</textarea>
                            </div>
                        </div>
                    </div>

                    <div class="form-group preview-column">
                        <label>items</label>
                        {% if preview_items %}
                        <div class="preview-list">
                            {% for preview in preview_items %}
                            <div class="preview-card">
                                <a class="preview-link" href="{{ preview.url }}" target="_blank" rel="noopener noreferrer">
                                    <img src="{{ preview.url }}" alt="{{ preview.name }} thumbnail" loading="lazy">
                                </a>
                                <div class="preview-name">{{ preview.name }}</div>
                                {% if preview.name == 'schedule' %}
                                <div style="margin-top: 0.5rem; padding: 0.5rem; background: #f8fafc; border: 1px solid var(--border-color); border-radius: 6px;">
                                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                        <input form="add-event-form" type="hidden" name="mac_address" value="{{ mac_address }}">
                                        <input form="add-event-form" type="hidden" name="action" value="add_event">
                                        <div style="display: flex; gap: 0.4rem;">
                                            <input form="add-event-form" type="text" name="event_date" placeholder="dd/mm" required style="font-size: 0.8rem; padding: 0.3rem; border: 1px solid var(--border-color); border-radius: 4px; width: 50%;">
                                            <input form="add-event-form" type="time" name="event_time" step="60" required style="font-size: 0.8rem; padding: 0.3rem; border: 1px solid var(--border-color); border-radius: 4px; width: 50%;">
                                        </div>
                                        <input form="add-event-form" type="text" name="event_title" placeholder="Event name" required style="font-size: 0.8rem; padding: 0.3rem; border: 1px solid var(--border-color); border-radius: 4px;">
                                        <button form="add-event-form" type="submit" style="font-size: 0.8rem; padding: 0.35rem 0.5rem; background-color: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer;">add event</button>
                                    </div>
                                </div>
                                {% endif %}
                            </div>
                            {% endfor %}
                        </div>
                        {% else %}
                        <div class="preview-empty">Select playlist items and save to see thumbnails.</div>
                        {% endif %}
                    </div>
                </div>
            </form>
            <form id="add-event-form" method="post"></form>
        </div>
    </body>
    </html>
    '''

    return render_template_string(html, mac_address=mac_address, device_name=device_name, 
                                  generated_options=generated_options, content_options=content_options,
                                  current_playlist=current_playlist,
                                  preview_items=preview_items,
                                  refresh_options=refresh_options, current_refresh_min=current_refresh_min,
                                  message=message, last_update=last_update, last_filename=last_filename,
                                  next_update=next_update,
                                  battery=battery, fw_version=fw_version, wifi_ssid=wifi_ssid,
                                  current_location=current_location,
                                  current_flip=current_flip, 
                                  current_sleep_start=current_sleep_start, current_sleep_end=current_sleep_end,
                                  current_sleep_weekend=current_sleep_weekend,
                                  current_timezone=current_timezone,
                                  current_custom_url=current_custom_url,
                                  current_custom_text=current_custom_text)
