import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

URL = "https://www.driftfund.io/news"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
COOKIE_NAME = os.getenv("COOKIE_NAME")
COOKIE_VALUE = os.getenv("COOKIE_VALUE")

COOKIES = {}
if COOKIE_NAME and COOKIE_VALUE:
    COOKIES[COOKIE_NAME] = COOKIE_VALUE

HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/149.0.0.0 Safari/537.36",
    "referer": "https://www.driftfund.io/rules",
    "accept-language": "es-ES,es;q=0.9,en;q=0.8,ar;q=0.7",
}

SOURCE_TZ = pytz.utc
TARGET_TZ = pytz.timezone("Europe/Madrid")

CACHE_FILE = "news_cache.json"


def fetch_html() -> str:
    session = requests.Session()
    r = session.get(URL, headers=HEADERS, cookies=COOKIES, timeout=30)
    r.raise_for_status()
    return r.text


def parse_datetime_to_europe(date_str: str) -> str:
    dt_naive = datetime.strptime(date_str, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)
    return dt_target.strftime("%d/%m/%Y %H:%M")


def parse_events(html: str):
    soup = BeautifulSoup(html, "lxml")
    events = []

    for block in soup.find_all("div"):
        children = block.find_all("div", recursive=False)
        if len(children) != 2:
            continue

        left, right = children
        name_divs = left.find_all("div", recursive=False)
        if len(name_divs) != 2:
            continue

        name = name_divs[0].get_text(strip=True)
        datetime_str_raw = name_divs[1].get_text(strip=True)

        spans = right.find_all("span", recursive=False)
        if len(spans) != 2:
            continue

        impact = spans[0].get_text(strip=True)
        time_to = spans[1].get_text(strip=True)

        if not name or not datetime_str_raw or not impact or not time_to:
            continue

        try:
            datetime_eu = parse_datetime_to_europe(datetime_str_raw)
        except Exception:
            datetime_eu = datetime_str_raw

        events.append({
            "name": name,
            "datetime_raw": datetime_str_raw,
            "datetime_eu": datetime_eu,
            "impact": impact,
            "time_to": time_to,
        })

    return events


def save_cached_events(events):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        print(f"Cache guardada en {CACHE_FILE} con {len(events)} eventos.")
    except Exception as e:
        print("Error guardando cache:", e)


def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("Faltan TELEGRAM_TOKEN o CHAT_ID en los secrets.")

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    r = requests.post(base_url, data=payload, timeout=10)
    r.raise_for_status()


def main():
    html = fetch_html()
    events = parse_events(html)
    print(f"Eventos descargados: {len(events)}")

    # Guardar todos los eventos en el archivo de cache
    save_cached_events(events)

    # Filtrar eventos high pendientes para enviar
    lines = []
    for e in events:
        if e["impact"].lower() != "high":
            continue
        if "passed" in e["time_to"].lower():
            continue

        line = f"*{e['datetime_eu']}* - {e['name']} ({e['time_to']})"
        lines.append(line)

    if not lines:
        print("No hay eventos high pendientes.")
        return

    message = "DRIFT NEWS:\n\n" + "\n".join(lines)
    print(message)
    send_telegram_message(message)


if __name__ == "__main__":
    main()
