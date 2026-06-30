import os
import json
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

COOKIES = {}
if COOKIE_NAME and COOKIE_VALUE:
    COOKIES[COOKIE_NAME] = COOKIE_VALUE

HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.driftfund.io/rules",
    "accept-language": "es-ES,es;q=0.9,en;q=0.8,ar;q=0.7",
}

SOURCE_TZ = pytz.utc
TARGET_TZ = pytz.timezone("Europe/Madrid")

CACHE_FILE = "news_cache.json"

MENTIONS = [
    "@almtaiwea76",
    "@xaxepro99",
]


# ========= HTTP =========

def fetch_html_safe() -> str | None:
    """GET /news. Si status 200, devuelve HTML; si no, avisa y devuelve None."""
    try:
        session = requests.Session()
        r = session.get(URL, headers=HEADERS, cookies=COOKIES, timeout=30)
        status = r.status_code
        print(f"[HTTP] GET {URL} status: {status}")
        if status == 200:
            return r.text
        else:
            msg = f"DRIFT ERROR: /news status {status}, no se actualiza cache."
            print("[HTTP]", msg)
            send_telegram_message(msg)
            return None
    except Exception as e:
        msg = f"DRIFT ERROR: /news exception: {e}, no se actualiza cache."
        print("[HTTP]", msg)
        send_telegram_message(msg)
        return None


# ========= TIEMPO =========

def parse_datetime_to_europe(date_str: str) -> str:
    dt_naive = datetime.strptime(date_str, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)
    return dt_target.strftime("%d/%m/%Y %H:%M")


def minutes_until_event(datetime_raw: str) -> float:
    """Minutos desde ahora (Madrid) hasta la hora del evento. Puede ser negativo."""
    dt_naive = datetime.strptime(datetime_raw, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)

    now_local = datetime.now(TARGET_TZ)
    delta = dt_target - now_local
    return delta.total_seconds() / 60.0  # [web:391][web:405][web:396][web:527]


# ========= PARSEO =========

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

    print(f"[PARSE] Eventos parseados: {len(events)}")
    return events


# ========= CACHE =========

def load_cache():
    """Devuelve dict con last_news_sent_at (ISO o None) y lista events."""
    if not os.path.exists(CACHE_FILE):
        print("[CACHE] Cache no existe, inicializando.")
        return {"last_news_sent_at": None, "events": []}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "events" in data and isinstance(data["events"], list):
            print(f"[CACHE] Cache cargada desde {CACHE_FILE} con {len(data['events'])} eventos.")
            return {
                "last_news_sent_at": data.get("last_news_sent_at"),
                "events": data["events"],
            }
        print("[CACHE] Cache sin formato esperado, reiniciando.")
        return {"last_news_sent_at": None, "events": []}
    except Exception as e:
        print("[CACHE] Error leyendo cache:", e)
        return {"last_news_sent_at": None, "events": []}


def save_cache(last_news_sent_at, events):
    """Chafa siempre la lista de eventos con los nuevos."""
    try:
        payload = {
            "last_news_sent_at": last_news_sent_at,
            "events": events,
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[CACHE] Cache guardada en {CACHE_FILE} con {len(events)} eventos.")
    except Exception as e:
        print("[CACHE] Error guardando cache:", e)


# ========= TELEGRAM =========

def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN o CHAT_ID, no se manda mensaje.")
        return

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        r = requests.post(base_url, data=payload, timeout=10)
        print(f"[TELEGRAM] Status: {r.status_code}")
        r.raise_for_status()
    except Exception as e:
        print("[TELEGRAM] Error enviando mensaje:", e)


def build_mentions_line() -> str:
    if not MENTIONS:
        return ""
    return " ".join(MENTIONS)


# ========= ALERTAS < 1 HORA =========

def build_alert_text(minutes: int, event_name: str) -> str:
    """Texto de alerta con sirenas, minutos, nombre y menciones."""
    mention_line = build_mentions_line()
    base = f"NEWS ALERT 🚨🚨 in {minutes} minutes — {event_name}"
    if mention_line:
        return base + "\n" + mention_line
    return base


def send_alerts_for_upcoming_events(events):
    """Envía alertas para eventos high que empiezan en <= 60 minutos."""
    alerts_sent = 0

    for e in events:
        if e["impact"].lower() != "high":
            continue

        try:
            minutes = minutes_until_event(e["datetime_raw"])
        except Exception as ex:
            print(f"[ALERT] No se pudo calcular minutos para {e['name']}: {ex}")
            continue

        print(f"[ALERT] {e['name']} empieza en {minutes:.1f} minutos.")

        if minutes <= 0:
            continue  # ya han pasado o están empezando
        if minutes > 60:
            continue  # falta más de una hora

        mins_int = int(round(minutes))
        alert_text = build_alert_text(mins_int, e["name"])
        print(f"[ALERT] Enviando alerta para {e['name']}: {alert_text}")
        send_telegram_message(alert_text)
        alerts_sent += 1

    if alerts_sent == 0:
        print("[ALERT] No hay eventos high con menos de 1h para alerta.")


# ========= MAIN =========

def main():
    print("========== NEWS BOT START ==========")

    # 1. Cargar cache actual
    cache = load_cache()
    events = cache["events"]
    last_news_sent_at = cache["last_news_sent_at"]

    print(f"[MAIN] last_news_sent_at en cache: {last_news_sent_at}")

    # 2. Intentar refrescar eventos desde /news (solo si 200)
    html = fetch_html_safe()
    if html is not None:
        print("[MAIN] Respuesta 200 OK, actualizando eventos desde /news.")
        new_events = parse_events(html)
        print(f"[MAIN] Nuevos eventos descargados: {len(new_events)}")
        events = new_events
        save_cache(last_news_sent_at, events)
    else:
        print("[MAIN] No se actualiza cache; se mantienen eventos anteriores.")

    # 3. Decidir si toca enviar resumen de noticias (cada 30 minutos)
    now_local = datetime.now(TARGET_TZ)
    print(f"[MAIN] Ahora (TARGET_TZ): {now_local.isoformat()}")

    should_send_news = False

    if not events:
        print("[MAIN] No hay eventos en cache, no hay resumen que enviar.")
    else:
        if last_news_sent_at is None:
            print("[MAIN] Nunca se ha enviado resumen, enviando ahora.")
            should_send_news = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_news_sent_at)
                delta = now_local - last_dt
                minutes_since = delta.total_seconds() / 60.0
                print(f"[MAIN] Han pasado {minutes_since:.1f} minutos desde el último resumen.")
                if minutes_since >= 30.0:
                    should_send_news = True
                else:
                    print("[MAIN] Aún no han pasado 30 minutos; no enviamos resumen.")
            except Exception as e:
                print("[MAIN] Error parseando last_news_sent_at, enviamos resumen por seguridad:", e)
                should_send_news = True

    # 4. Enviar resumen si toca (primero resumen, luego alertas)
    if should_send_news and events:
        print("[MAIN] Toca enviar resumen de noticias.")
        # Encontrar el evento high no passed más cercano
        nearest_event = None
        nearest_minutes = None
        high_pending = 0

        for e in events:
            if e["impact"].lower() != "high":
                continue
            if "passed" in e["time_to"].lower():
                continue

            high_pending += 1

            try:
                m = minutes_until_event(e["datetime_raw"])
            except Exception as ex:
                print(f"[MAIN] Error calculando minutos para {e['name']}: {ex}")
                continue

            print(f"[MAIN] {e['name']} pendiente, empieza en {m:.1f} minutos.")

            if m <= 0:
                continue

            if nearest_minutes is None or m < nearest_minutes:
                nearest_minutes = m
                nearest_event = e

        print(f"[MAIN] Eventos high pendientes: {high_pending}")
        if nearest_event:
            print(f"[MAIN] Evento más cercano: {nearest_event['name']} en {nearest_minutes:.1f} minutos.")

        lines = []
        for e in events:
            if e["impact"].lower() != "high":
                continue
            if "passed" in e["time_to"].lower():
                continue

            if nearest_event is not None and e is nearest_event:
                line = f"🔥 *{e['datetime_eu']}* - *{e['name']}* ({e['time_to']})"
            else:
                line = f"*{e['datetime_eu']}* - {e['name']} ({e['time_to']})"

            lines.append(line)

        if not lines:
            print("[MAIN] No hay eventos high pendientes para resumen.")
        else:
            header = f"DRIFT NEWS ({len(lines)} eventos high):\n\n"
            message = header + "\n".join(lines)
            mentions_line = build_mentions_line()
            if mentions_line:
                message += "\n\n" + mentions_line
            print("[MAIN] Mandando resumen de noticias:")
            print(message)
            send_telegram_message(message)
            last_news_sent_at = now_local.isoformat()
            save_cache(last_news_sent_at, events)
    else:
        print("[MAIN] No toca enviar resumen de noticias (menos de 30 min o sin eventos).")

    # 5. Enviar alertas para eventos high con menos de 1h (cada run)
    if events:
        send_alerts_for_upcoming_events(events)
    else:
        print("[MAIN] Sin eventos, no hay alertas que enviar.")

    print("========== NEWS BOT END ==========")


if __name__ == "__main__":
    main()
