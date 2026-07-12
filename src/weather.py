import io
import os
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

TEMPEST_BASE_URL = "https://swd.weatherflow.com/swd/rest"
TEMPEST_COORDS = "60.1581787,24.8668912"
WEATHERAPI_BASE_URL = "https://api.weatherapi.com/v1"
UNITS_TEMP = "c"
UNITS_WIND = "kph"
UNITS_PRECIP = "mm"
HELSINKI_TZ = ZoneInfo("Europe/Helsinki")
BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def _load_fonts():
    regular_path = os.path.join(BASE_DIR, "fonts", "exo2-medium.ttf")
    bold_path = os.path.join(BASE_DIR, "fonts", "exo2-bold.ttf")
    try:
        return {
            "huge": ImageFont.truetype(bold_path, 64),
            "large": ImageFont.truetype(bold_path, 32),
            "medium": ImageFont.truetype(regular_path, 24),
            "small": ImageFont.truetype(regular_path, 18),
            "tiny": ImageFont.truetype(regular_path, 15),
        }
    except Exception:
        fallback = ImageFont.load_default()
        return {
            "huge": fallback,
            "large": fallback,
            "medium": fallback,
            "small": fallback,
            "tiny": fallback,
        }


def _to_local_time(ts, tz=HELSINKI_TZ):
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz).strftime("%H:%M")
    except Exception:
        return "-"


def _build_url(coords=TEMPEST_COORDS):
    api_key = os.getenv("TEMPEST_API_KEY", "").strip()
    if not api_key:
        return None
    lat, lon = [v.strip() for v in coords.split(",", 1)]
    return (
        f"{TEMPEST_BASE_URL}/better_forecast"
        f"?lat={lat}&lon={lon}"
        f"&units_temp={UNITS_TEMP}&units_wind={UNITS_WIND}&units_precip={UNITS_PRECIP}"
        f"&api_key={api_key}"
    )


def _build_weatherapi_url(coords=TEMPEST_COORDS):
    api_key = os.getenv("WEATHERAPI_KEY", "").strip() or os.getenv("WEATHER_API_KEY", "").strip()
    if not api_key:
        return None
    return (
        f"{WEATHERAPI_BASE_URL}/forecast.json"
        f"?key={api_key}&q={coords}&days=2&aqi=no&alerts=no"
    )


def _safe_number(value, digits=0):
    try:
        f = float(value)
        if digits == 0:
            return str(int(round(f)))
        return f"{f:.{digits}f}"
    except Exception:
        return "-"


def _max_uv_for_day(hourly, day_start_local):
    if not hourly or not day_start_local:
        return "-"

    try:
        start = datetime.fromtimestamp(int(day_start_local), tz=timezone.utc).astimezone(HELSINKI_TZ)
    except Exception:
        return "-"

    day = start.date()
    values = []
    for row in hourly:
        try:
            ts = datetime.fromtimestamp(int(row.get("time", 0)), tz=timezone.utc).astimezone(HELSINKI_TZ)
        except Exception:
            continue
        if ts.date() == day:
            uv = row.get("uv")
            if uv is not None:
                try:
                    values.append(float(uv))
                except Exception:
                    pass

    if not values:
        return "-"
    uv_max = max(values)
    if int(uv_max) == uv_max:
        return str(int(uv_max))
    return f"{uv_max:.1f}"


def _draw_error(draw, fonts, message):
    draw.text((40, 40), "Weather", font=fonts["large"], fill=0)
    draw.text((40, 100), message, font=fonts["medium"], fill=0)


def _wind_dir_cardinal(deg):
    try:
        d = float(deg) % 360
    except Exception:
        return "-"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((d + 22.5) // 45) % 8
    return dirs[idx]


def _conditions_from_precip(prob):
    try:
        p = float(prob)
    except Exception:
        return "-"
    if p >= 60:
        return "Rain likely"
    if p >= 20:
        return "Possible showers"
    return "Dry"


def _fetch_tempest_data(coords=TEMPEST_COORDS, tz=HELSINKI_TZ):
    url = _build_url(coords)
    if not url:
        raise RuntimeError("Missing TEMPEST_API_KEY env var")

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()

    current = payload.get("current_conditions", {})
    forecast = payload.get("forecast", {})
    hourly = forecast.get("hourly", [])
    daily = forecast.get("daily", [])

    today = daily[0] if len(daily) > 0 else {}
    tomorrow = daily[1] if len(daily) > 1 else {}

    return {
        "source": "Tempest",
        "current_temp": _safe_number(current.get("air_temperature")),
        "current_conditions": str(current.get("conditions", "-")),
        "sunrise": _to_local_time(today.get("sunrise"), tz),
        "sunset": _to_local_time(today.get("sunset"), tz),
        "wind_gust": _safe_number(current.get("wind_gust"), 1),
        "wind_dir": str(current.get("wind_direction_cardinal", "-")).upper(),
        "feels_like": _safe_number(current.get("feels_like")),
        "humidity": _safe_number(current.get("relative_humidity")),
        "today": {
            "conditions": str(today.get("conditions", "-")),
            "uv": _max_uv_for_day(hourly, today.get("day_start_local")),
            "precip_prob": _safe_number(today.get("precip_probability")),
            "precip_amount": _safe_number(current.get("precip_accum_local_day"), 1),
            "low": _safe_number(today.get("air_temp_low")),
            "high": _safe_number(today.get("air_temp_high")),
        },
        "tomorrow": {
            "conditions": str(tomorrow.get("conditions", "-")),
            "uv": _max_uv_for_day(hourly, tomorrow.get("day_start_local")),
            "precip_prob": _safe_number(tomorrow.get("precip_probability")),
            "precip_amount": "-",
            "low": _safe_number(tomorrow.get("air_temp_low")),
            "high": _safe_number(tomorrow.get("air_temp_high")),
        },
    }


def _fetch_weatherapi_data(coords=TEMPEST_COORDS):
    url = _build_weatherapi_url(coords)
    if not url:
        raise RuntimeError("WeatherAPI not configured")

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()

    current = payload.get("current", {})
    forecast_days = payload.get("forecast", {}).get("forecastday", [])
    today = forecast_days[0] if len(forecast_days) > 0 else {}
    tomorrow = forecast_days[1] if len(forecast_days) > 1 else {}

    today_day = today.get("day", {})
    tomorrow_day = tomorrow.get("day", {})
    today_astro = today.get("astro", {})

    return {
        "source": "WeatherAPI",
        "current_temp": _safe_number(current.get("temp_c")),
        "current_conditions": str(current.get("condition", {}).get("text", "-")),
        "sunrise": str(today_astro.get("sunrise", "-")),
        "sunset": str(today_astro.get("sunset", "-")),
        "wind_gust": _safe_number(current.get("gust_kph"), 1),
        "wind_dir": str(current.get("wind_dir", "-")).upper(),
        "feels_like": _safe_number(current.get("feelslike_c")),
        "humidity": _safe_number(current.get("humidity")),
        "today": {
            "conditions": str(today_day.get("condition", {}).get("text", "-")),
            "uv": _safe_number(today_day.get("uv"), 1),
            "precip_prob": _safe_number(today_day.get("daily_chance_of_rain")),
            "precip_amount": _safe_number(today_day.get("totalprecip_mm"), 1),
            "low": _safe_number(today_day.get("mintemp_c")),
            "high": _safe_number(today_day.get("maxtemp_c")),
        },
        "tomorrow": {
            "conditions": str(tomorrow_day.get("condition", {}).get("text", "-")),
            "uv": _safe_number(tomorrow_day.get("uv"), 1),
            "precip_prob": _safe_number(tomorrow_day.get("daily_chance_of_rain")),
            "precip_amount": _safe_number(tomorrow_day.get("totalprecip_mm"), 1),
            "low": _safe_number(tomorrow_day.get("mintemp_c")),
            "high": _safe_number(tomorrow_day.get("maxtemp_c")),
        },
    }


def _fetch_fmi_data(coords=TEMPEST_COORDS, tz=HELSINKI_TZ):
    url = (
        "https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0"
        "&request=getFeature"
        "&storedquery_id=fmi::forecast::harmonie::surface::point::simple"
        f"&latlon={coords}&"
    )

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    ns = {
        "BsWfs": "http://xml.fmi.fi/schema/wfs/2.0",
    }

    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(hours=36)

    hourly = {}
    for element in root.findall(".//BsWfs:BsWfsElement", ns):
        time_node = element.find("BsWfs:Time", ns)
        pname_node = element.find("BsWfs:ParameterName", ns)
        pvalue_node = element.find("BsWfs:ParameterValue", ns)
        if time_node is None or pname_node is None or pvalue_node is None:
            continue

        try:
            t = datetime.strptime(time_node.text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            v = float(pvalue_node.text)
        except Exception:
            continue

        if t < now_utc - timedelta(hours=1) or t > end_utc:
            continue

        key = t.replace(minute=0, second=0, microsecond=0)
        row = hourly.setdefault(key, {})
        pname = pname_node.text
        if pname == "Temperature":
            row["temp"] = v
        elif pname == "Precipitation1h":
            row["rain"] = v
        elif pname in ("WindSpeedMS", "WindSpeedMS10min"):
            row["wind_ms"] = v
        elif pname == "WindDirection":
            row["wind_deg"] = v

    if not hourly:
        raise RuntimeError("FMI returned no forecast rows")

    rows_sorted = sorted(hourly.items(), key=lambda kv: kv[0])
    current_t, current_row = min(rows_sorted, key=lambda kv: abs((kv[0] - now_utc).total_seconds()))

    local_now = datetime.now(tz)
    today_date = local_now.date()
    tomorrow_date = (local_now + timedelta(days=1)).date()

    def build_day(target_date):
        day_rows = []
        for ts, row in rows_sorted:
            if ts.astimezone(tz).date() == target_date and "temp" in row:
                day_rows.append(row)

        if not day_rows:
            return {
                "conditions": "-",
                "uv": "-",
                "precip_prob": "-",
                "precip_amount": "-",
                "low": "-",
                "high": "-",
            }

        temps = [r.get("temp") for r in day_rows if r.get("temp") is not None]
        rains = [max(0.0, r.get("rain", 0.0)) for r in day_rows]
        rain_hours = [r for r in rains if r > 0.1]
        precip_prob = int(round((len(rain_hours) / max(1, len(rains))) * 100))

        return {
            "conditions": _conditions_from_precip(precip_prob),
            "uv": "-",
            "precip_prob": str(precip_prob),
            "precip_amount": _safe_number(sum(rains), 1),
            "low": _safe_number(min(temps)),
            "high": _safe_number(max(temps)),
        }

    wind_kph = _safe_number((current_row.get("wind_ms", 0.0) or 0.0) * 3.6, 1)
    current_rain = current_row.get("rain", 0.0) or 0.0
    current_cond = "Rain" if current_rain > 0.1 else "Dry"
    current_temp = _safe_number(current_row.get("temp"))

    return {
        "source": "FMI",
        "current_temp": current_temp,
        "current_conditions": current_cond,
        "sunrise": "-",
        "sunset": "-",
        "wind_gust": wind_kph,
        "wind_dir": _wind_dir_cardinal(current_row.get("wind_deg")),
        "feels_like": current_temp,
        "humidity": "-",
        "today": build_day(today_date),
        "tomorrow": build_day(tomorrow_date),
    }


def generate_weather(coords=TEMPEST_COORDS, device_tz_name="Europe/Helsinki"):
    width, height = 800, 480
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    fonts = _load_fonts()

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(device_tz_name)
        datasource = os.getenv("WEATHER_DATASOURCE", "fmi").strip().lower()
        if datasource == "weatherapi":
            weather = _fetch_weatherapi_data(coords)
        elif datasource == "fmi":
            weather = _fetch_fmi_data(coords, tz)
        else:
            weather = _fetch_tempest_data(coords, tz)
    except Exception as e:
        _draw_error(draw, fonts, f"Weather fetch failed: {str(e).replace('WEATHERAPI_KEY', 'provider').replace('WEATHER_API_KEY', 'provider')}")
        out = io.BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        return out

    # Layout inspired by TRMNL weather plugin full view.
    margin = 18
    left_bar_width = 5
    left_bar_color = 200
    top_y0, top_y1 = 18, 208
    divider_y = 226
    bottom_y0, bottom_y1 = 240, 430
    titlebar_y0, titlebar_y1 = 438, 478

    # Top grid: left icon card, middle current card, right stacked metrics.
    left_x0, left_x1 = margin, 198
    mid_x0, mid_x1 = 210, 560
    right_x0, right_x1 = 572, 782

    draw.rectangle([0, 0, left_bar_width - 1, height - 1], fill=left_bar_color)

    # Left "weather-image" style card
    draw.ellipse([left_x0 + 28, top_y0 + 24, left_x0 + 148, top_y0 + 144], outline=0, width=3)
    cond_short = weather["current_conditions"][:18]
    draw.text((left_x0 + 20, top_y0 + 156), cond_short, font=fonts["tiny"], fill=0)

    # Middle current card
    draw.text((mid_x0 + 16, top_y0 + 18), f"{weather['current_temp']}°", font=fonts["huge"], fill=0)
    draw.text((mid_x0 + 18, top_y0 + 108), f"Sunrise  {weather['sunrise']}", font=fonts["small"], fill=0)
    draw.text((mid_x0 + 18, top_y0 + 136), f"Sunset   {weather['sunset']}", font=fonts["small"], fill=0)
    draw.text((mid_x0 + 18, top_y0 + 164), f"Wind     {weather['wind_gust']} {UNITS_WIND} {weather['wind_dir']}", font=fonts["small"], fill=0)

    # Right stacked mini cards
    mini_h = 58
    mini_gap = 8
    mini_labels = [
        (f"{weather['feels_like']}°", "Feels Like"),
        (f"{weather['humidity']}%", "Humidity"),
        (weather["current_conditions"][:16], "Right Now"),
    ]
    for idx, (value, label) in enumerate(mini_labels):
        y0 = top_y0 + 8 + idx * (mini_h + mini_gap)
        draw.text((right_x0 + 18, y0 + 8), value, font=fonts["small"], fill=0)
        draw.text((right_x0 + 18, y0 + 32), label, font=fonts["tiny"], fill=0)

    # Divider
    draw.line([margin, divider_y, width - margin, divider_y], fill=0, width=2)

    # Forecast rows: today and tomorrow with three metric blocks each.
    def draw_day_row(y0, title, row, include_amount):
        # Left conditions block
        draw.text((margin + 18, y0 + 14), title, font=fonts["small"], fill=0)
        draw.text((margin + 18, y0 + 44), str(row.get("conditions", "-"))[:28], font=fonts["tiny"], fill=0)

        # Middle UV / precip block
        uv = row.get("uv", "-")
        pprob = row.get("precip_prob", "-")
        precip = f"{pprob}%"
        if include_amount:
            precip = f"{pprob}%  {row.get('precip_amount', '-')} {UNITS_PRECIP}"
        draw.text((322, y0 + 14), f"UV {uv}", font=fonts["small"], fill=0)
        draw.text((322, y0 + 44), f"Precip {precip}", font=fonts["tiny"], fill=0)

        # Right low/high block
        draw.text((570, y0 + 14), f"Low  {row.get('low', '-') }°", font=fonts["small"], fill=0)
        draw.text((570, y0 + 44), f"High {row.get('high', '-') }°", font=fonts["small"], fill=0)

    draw_day_row(bottom_y0, "Today", weather["today"], include_amount=True)
    draw_day_row(bottom_y0 + 96, "Tomorrow", weather["tomorrow"], include_amount=False)

    # Title bar similar to plugin footer.
    draw.text((margin + 12, titlebar_y0 + 9), "Weather", font=fonts["small"], fill=0)
    refreshed = datetime.now(tz).strftime("%H:%M")
    draw.text((margin + 142, titlebar_y0 + 11), f"{weather['source']} ({refreshed})", font=fonts["tiny"], fill=0)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out
