import json
from flask import request, make_response
from src.db import normalize_mac, get_latest_display_request, get_latest_device_log


def get_logs(db_path):
    """Display the last display request/response and device log for a device in pretty HTML."""
    mac = request.args.get('mac')
    if not mac:
        return 'Missing mac parameter', 400
    
    mac = normalize_mac(mac)
    display_request = get_latest_display_request(db_path, mac)
    device_log = get_latest_device_log(db_path, mac)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Logs for {mac}</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 2rem 1rem;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            h1 {{
                color: white;
                text-align: center;
                margin-bottom: 2rem;
                font-size: 2rem;
                text-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            }}
            .logs-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
                gap: 1.5rem;
            }}
            .box {{
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                overflow: hidden;
                transition: transform 0.3s ease, box-shadow 0.3s ease;
            }}
            .box:hover {{
                transform: translateY(-5px);
                box-shadow: 0 15px 40px rgba(0, 0, 0, 0.3);
            }}
            .box-header {{
                padding: 1.5rem;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .box-title {{
                font-size: 1.3rem;
                font-weight: 600;
            }}
            .timestamp {{
                font-size: 0.85rem;
                opacity: 0.9;
                background: rgba(255, 255, 255, 0.2);
                padding: 0.4rem 0.8rem;
                border-radius: 20px;
                white-space: nowrap;
            }}
            .box-content {{
                padding: 1.5rem;
                max-height: 800px;
                overflow-y: auto;
            }}
            .box-content pre {{
                background: #f5f5f5;
                padding: 1rem;
                border-radius: 8px;
                font-size: 0.9rem;
                overflow-x: auto;
                border-left: 4px solid #667eea;
            }}
            .status-code {{
                display: inline-block;
                padding: 0.3rem 0.8rem;
                border-radius: 6px;
                font-weight: bold;
                margin: 0.5rem 0;
            }}
            .status-200 {{
                background: #d4edda;
                color: #155724;
            }}
            .status-304 {{
                background: #cfe2ff;
                color: #084298;
            }}
            .status-error {{
                background: #f8d7da;
                color: #721c24;
            }}
            .empty-state {{
                text-align: center;
                padding: 2rem;
                color: #999;
            }}
            .empty-state-icon {{
                font-size: 3rem;
                margin-bottom: 1rem;
            }}
            @media (max-width: 768px) {{
                h1 {{
                    font-size: 1.5rem;
                }}
                .logs-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Device Logs: {mac}</h1>
            <div class="logs-grid">
    """
    
    # Display Request Box
    if display_request:
        status_code = display_request.get('status_code', 'N/A')
        status_class = f'status-{status_code}' if status_code in [200, 304] else 'status-error'
        request_headers = display_request.get('request_headers', {})
        response_body = display_request.get('response_body', {})
        
        html += f"""
                <div class="box">
                    <div class="box-header">
                        <span class="box-title">📤 Display Request</span>
                        <span class="timestamp">{display_request.get('created_at', 'N/A')}</span>
                    </div>
                    <div class="box-content">
                        <div class="status-code {status_class}">HTTP {status_code}</div>
                        <h4 style="margin-top: 1rem; margin-bottom: 0.5rem;">Request Headers:</h4>
                        <pre>{_format_json_for_display(request_headers)}</pre>
                        <h4 style="margin-top: 1rem; margin-bottom: 0.5rem;">Response Body:</h4>
                        <pre>{_format_json_for_display(response_body)}</pre>
                    </div>
                </div>
        """
    else:
        html += """
                <div class="box">
                    <div class="box-header">
                        <span class="box-title">📤 Display Request</span>
                    </div>
                    <div class="box-content">
                        <div class="empty-state">
                            <div class="empty-state-icon">📭</div>
                            <p>No display requests found for this device.</p>
                        </div>
                    </div>
                </div>
        """
    
    # Device Log Box
    if device_log:
        message = device_log.get('message', {})
        html += f"""
                <div class="box">
                    <div class="box-header">
                        <span class="box-title">📝 Device Errors</span>
                        <span class="timestamp">{device_log.get('created_at', 'N/A')}</span>
                    </div>
                    <div class="box-content">
                        <pre>{_format_json_for_display(message)}</pre>
                    </div>
                </div>
        """
    else:
        html += """
                <div class="box">
                    <div class="box-header">
                        <span class="box-title">📝 Device Log</span>
                    </div>
                    <div class="box-content">
                        <div class="empty-state">
                            <div class="empty-state-icon">📭</div>
                            <p>No device logs found for this device.</p>
                        </div>
                    </div>
                </div>
        """
    
    html += """
            </div>
        </div>
    </body>
    </html>
    """
    
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response


def _format_json_for_display(obj):
    """Format JSON object for display in HTML, with proper escaping."""
    import html
    json_str = json.dumps(obj, indent=2, default=str)
    return html.escape(json_str)
