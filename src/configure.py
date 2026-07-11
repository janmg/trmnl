import sqlite3
import os
from urllib.parse import quote
from flask import request, render_template_string

def handle_register(db_path, content_dir):
    mac_address = request.args.get('mac_address') or request.form.get('mac_address')
    if not mac_address:
        return "MAC Address required", 400
    
    mac_address = mac_address.upper()
    message = ""
    
    # Options from static + content directory
    generated_options = ["testcard", "schedule", "xkcd", "fmi", "clock"]
    content_options = sorted([f.replace('.png', '') for f in os.listdir(content_dir) if f.endswith('.png')])
    content_options = [f for f in content_options if f not in generated_options]

    refresh_options = [1, 5, 15, 60, 1440]

    if request.method == 'POST':
        selected_items = request.form.getlist('playlist')
        name = request.form.get('name')
        refresh_min = int(request.form.get('refresh_rate', 15))
        refresh_sec = refresh_min * 60
        flip = 1 if request.form.get('flip') else 0
        custom_url = request.form.get('custom_url', '').strip()
        custom_text = request.form.get('custom_text', '').strip()
        playlist_str = ",".join(selected_items)
        
        with sqlite3.connect(db_path) as conn:
            conn.execute('''
                INSERT INTO devices (mac_address, name, playlist, refresh_rate, flip, custom_url, custom_text) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac_address) DO UPDATE SET 
                name=excluded.name, playlist=excluded.playlist, refresh_rate=excluded.refresh_rate, 
                flip=excluded.flip, custom_url=excluded.custom_url, custom_text=excluded.custom_text
            ''', (mac_address, name or f"Device {mac_address[-5:]}", playlist_str, refresh_sec, flip, custom_url, custom_text))
        
        message = f"Device {mac_address} updated!"

    # GET request or after POST: show form
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, playlist, refresh_rate, last_update, last_filename, battery_voltage, fw_version, wifi_ssid, flip, custom_url, custom_text FROM devices WHERE mac_address = ?', (mac_address,))
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
            current_flip = row[8] or 0
            current_custom_url = row[9] or ""
            current_custom_text = row[10] or ""
        else:
            device_name = f"Device {mac_address[-5:]}"
            current_playlist = []
            current_refresh_min = 15
            last_update = "Never"
            last_filename = "None"
            battery = "-"
            fw_version = "-"
            wifi_ssid = "-"
            current_flip = 0
            current_custom_url = ""
            current_custom_text = ""

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
        elif item == "clock":
            preview_url = "/clock"
        elif item == "custom_text":
            label = "custom_text"
            preview_url = f"/text?mac_address={mac_encoded}"
        elif item == "custom_url" and current_custom_url:
            label = "custom_url"
            preview_url = current_custom_url
        elif item in content_options:
            preview_url = f"/content/{item}.png"

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
                            <div class="status-item"><span class="status-label">Last File:</span> {{ last_filename }}</div>
                            <div class="status-item"><span class="status-label">Status:</span> {{ battery }}V | {{ fw_version }} | {{ wifi_ssid }}</div>
                        </div>

                        <div class="form-group">
                            <label for="name">Friendly Name</label>
                            <input type="text" id="name" name="name" value="{{ device_name }}" placeholder="My TRMNL Screen">
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
                        <label>Selected Thumbnails</label>
                        {% if preview_items %}
                        <div class="preview-list">
                            {% for preview in preview_items %}
                            <div class="preview-card">
                                <a class="preview-link" href="{{ preview.url }}" target="_blank" rel="noopener noreferrer">
                                    <img src="{{ preview.url }}" alt="{{ preview.name }} thumbnail" loading="lazy">
                                </a>
                                <div class="preview-name">{{ preview.name }}</div>
                            </div>
                            {% endfor %}
                        </div>
                        {% else %}
                        <div class="preview-empty">Select playlist items and save to see thumbnails.</div>
                        {% endif %}
                    </div>
                </div>
            </form>
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
                                  battery=battery, fw_version=fw_version, wifi_ssid=wifi_ssid,
                                  current_flip=current_flip, current_custom_url=current_custom_url,
                                  current_custom_text=current_custom_text)
