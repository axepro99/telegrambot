import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

# ========= CONFIG =========

URL = "https://www.driftfund.io/news"


    
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

COOKIE_NAME = os.getenv("COOKIE_NAME")
COOKIE_VALUE = os.getenv("COOKIE_VALUE")

COOKIES = {
    COOKIE_NAME: COOKIE_VALUE,
}

HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/149.0.0.0 Safari/537.36",
    "referer": "https://www.driftfund.io/rules",
    "accept-language": "es-ES,es;q=0.9,en;q=0.8,ar;q=0.7",
}

SOURCE_TZ = pytz.utc
TARGET_TZ = pytz.timezone("Europe/Madrid")



# ========= DRIFT: HTTP =========

def fetch_html() -> str:
    session = requests.Session()
    r = session.get(URL, headers=HEADERS, cookies=COOKIES, timeout=30)
    r.raise_for_status()
    return r.text


# ========= DRIFT: TIEMPO =========

def parse_datetime_to_europe(date_str: str) -> str:
    # Ej: '6/30/2026, 1:30:00 AM'
    dt_naive = datetime.strptime(date_str, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)
    return dt_target.strftime("%d/%m/%Y %H:%M")


# ========= DRIFT: PARSEO =========

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


# ========= TELEGRAM =========

def send_telegram_message(text: str):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            r = requests.post(base_url, data=payload, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"Error enviando a {chat_id}: {e}")


# ========= MAIN =========

def main():
    html = fetch_html()
    events = parse_events(html)

    print(f"Total eventos: {len(events)}")

    lines = []
    for e in events:
        if e["impact"].lower() != "high":
            continue
        if "passed" in e["time_to"].lower():
            continue

        line = f"*{e['datetime_eu']}* – {e['name']} ({e['time_to']})"
        lines.append(line)

    if not lines:
        print("No hay eventos high pendientes.")
        return

    message = "DRIFT NEWS :\n\n" + "\n".join(lines)

    print("Enviando mensaje:\n", message)
    send_telegram_message(message)


if __name__ == "__main__":
    main()
