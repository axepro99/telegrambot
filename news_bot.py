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
# Supabase parte el token de auth en varios trozos:
#   sb-<proyecto>-auth-token.0  y  sb-<proyecto>-auth-token.1
# Hay que mandar LOS DOS o la sesión no vale.
COOKIE_NAME = os.getenv("COOKIE_NAME")
COOKIE_VALUE = os.getenv("COOKIE_VALUE")
COOKIE_NAME_2 = os.getenv("COOKIE_NAME_2")
COOKIE_VALUE_2 = os.getenv("COOKIE_VALUE_2")

COOKIES = {}
for _name, _value in ((COOKIE_NAME, COOKIE_VALUE), (COOKIE_NAME_2, COOKIE_VALUE_2)):
    if _name and _value:
        COOKIES[_name] = _value

# Solo nombres, nunca valores (los valores son el token de sesión)
print(f"[CONFIG] Cookies cargadas: {len(COOKIES)} -> {list(COOKIES.keys())}")

# Copiadas tal cual del "Copy as cURL" de Chrome sobre /news.
# Sin "accept: text/html" el servidor puede devolver algo que no es la página entera.
HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "es-ES,es;q=0.9,en;q=0.8,ro;q=0.7,da;q=0.6,de;q=0.5",
    "cache-control": "max-age=0",
    "priority": "u=0, i",
    "referer": "https://www.driftfund.io/dashboard",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

# Drift da las fechas en UTC (SOURCE_TZ)
SOURCE_TZ = pytz.utc
# Ahora usamos horario de Brasil (São Paulo, UTC-3 sin DST) [web:672][web:675][web:667][web:674]
TARGET_TZ = pytz.timezone("America/Sao_Paulo")

CACHE_FILE = "news_cache.json"

# Menciones normales para resúmenes y alertas
MENTIONS = [
    "@all",
]

# Mención solo para avisos de cookie/estado raro
ERROR_MENTION = "@xaxepro99"


# ========= HTTP =========

def fetch_html_safe() -> str | None:
    """GET /news. Devuelve HTML solo si de verdad hemos llegado a /news."""
    try:
        session = requests.Session()
        r = session.get(URL, headers=HEADERS, cookies=COOKIES, timeout=30)
        status = r.status_code
        print(f"[HTTP] GET {URL} status: {status} url_final: {r.url}")

        # Sin sesión válida Drift contesta 307 y te manda a /login. requests sigue
        # el redirect solo, así que el status final es 200 aunque nunca vimos /news:
        # el bot acababa parseando la página de login y sacando 0 eventos.
        if r.history:
            saltos = " -> ".join(str(h.status_code) for h in r.history)
            msg = (
                f"DRIFT ERROR: /news redirigió ({saltos}) a {r.url}. "
                f"Sesión no válida, cookies cargadas: {len(COOKIES)} (deben ser 2). "
                "No se actualiza cache.\n"
                f"{ERROR_MENTION}"
            )
            print("[HTTP]", msg)
            send_telegram_message(msg)
            return None

        if status == 200:
            return r.text
        else:
            msg = f"DRIFT ERROR: /news status {status}, no se actualiza cache.\n{ERROR_MENTION}"
            print("[HTTP]", msg)
            send_telegram_message(msg)
            return None
    except Exception as e:
        msg = f"DRIFT ERROR: /news exception: {e}, no se actualiza cache.\n{ERROR_MENTION}"
        print("[HTTP]", msg)
        send_telegram_message(msg)
        return None


# ========= TIEMPO =========

def parse_datetime_to_target(date_str: str) -> str:
    """Convierte fecha de Drift (UTC) a horario de Brasil (São Paulo)."""
    dt_naive = datetime.strptime(date_str, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)  # [web:673][web:674][web:676]
    return dt_target.strftime("%d/%m/%Y %H:%M")


def minutes_until_event(datetime_raw: str) -> float:
    """Minutos desde ahora (Brasil) hasta la hora del evento. Puede ser negativo."""
    dt_naive = datetime.strptime(datetime_raw, "%m/%d/%Y, %I:%M:%S %p")
    dt_source = SOURCE_TZ.localize(dt_naive)
    dt_target = dt_source.astimezone(TARGET_TZ)

    now_local = datetime.now(TARGET_TZ)
    delta = dt_target - now_local
    return delta.total_seconds() / 60.0  # la diferencia es la misma en cualquier huso [web:663]


def format_time_to(minutes: float) -> str:
    """Minutos restantes -> texto tipo 'in 2d 14h' / 'in 3h 19m' / 'in 42m'."""
    total = int(minutes)
    days, rest = divmod(total, 60 * 24)
    hours, mins = divmod(rest, 60)
    if days > 0:
        return f"in {days}d {hours}h"
    if hours > 0:
        return f"in {hours}h {mins}m"
    return f"in {mins}m"


def is_pending(event) -> bool:
    """True si el evento aún no ha ocurrido.

    Calculado en vivo desde datetime_raw. NO usar event["time_to"]: ese texto
    es el que había en la web cuando se hizo el scrape y no se actualiza solo.
    """
    try:
        return minutes_until_event(event["datetime_raw"]) > 0
    except Exception as ex:
        print(f"[TIME] No se pudo calcular minutos para {event.get('name')}: {ex}")
        return True  # ante la duda, no lo borramos


# ========= PARSEO =========

def parse_events(html: str):
    # Si la sesión no vale, Drift devuelve 200 con un HTML mucho más corto y sin tabla
    print(f"[PARSE] HTML recibido: {len(html)} chars")
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
            datetime_target = parse_datetime_to_target(datetime_str_raw)
        except Exception:
            datetime_target = datetime_str_raw

        events.append({
            "name": name,
            "datetime_raw": datetime_str_raw,   # siempre en formato Drift (UTC string)
            "datetime_local": datetime_target,  # ahora en horario de Brasil
            "impact": impact,
            "time_to": time_to,
        })

    print(f"[PARSE] Eventos parseados: {len(events)}")
    return events


# ========= CACHE =========

def load_cache():
    """Devuelve dict con last_news_sent_at, cache_created_at y lista events."""
    if not os.path.exists(CACHE_FILE):
        print("[CACHE] Cache no existe, inicializando.")
        return {
            "last_news_sent_at": None,
            "cache_created_at": None,
            "events": [],
        }

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("events", [])
        last_news_sent_at = data.get("last_news_sent_at")
        cache_created_at = data.get("cache_created_at")
        print(
            f"[CACHE] Cache cargada desde {CACHE_FILE} "
            f"con {len(events)} eventos, last_news_sent_at={last_news_sent_at}, "
            f"cache_created_at={cache_created_at}"
        )
        return {
            "last_news_sent_at": last_news_sent_at,
            "cache_created_at": cache_created_at,
            "events": events,
        }
    except Exception as e:
        print("[CACHE] Error leyendo cache:", e)
        return {
            "last_news_sent_at": None,
            "cache_created_at": None,
            "events": [],
        }


def save_cache(last_news_sent_at, events, cache_created_at=None):
    """Guarda cache con timestamp de creación."""
    if cache_created_at is None:
        cache_created_at = datetime.now(TARGET_TZ).isoformat()
    try:
        payload = {
            "last_news_sent_at": last_news_sent_at,
            "cache_created_at": cache_created_at,
            "events": events,
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(
            f"[CACHE] Cache guardada en {CACHE_FILE} con {len(events)} eventos, "
            f"cache_created_at={cache_created_at}"
        )
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

        print(f"[ALERT] {e['name']} empieza en {minutes:.1f} minutos (hora Brasil).")

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
    cache_created_at = cache["cache_created_at"]

    print(f"[MAIN] last_news_sent_at en cache: {last_news_sent_at}")
    print(f"[MAIN] cache_created_at en cache: {cache_created_at}")

    # 2. Intentar refrescar eventos desde /news (solo si 200)
    html = fetch_html_safe()
    if html is not None:
        print("[MAIN] Respuesta 200 OK, intentando actualizar eventos desde /news.")
        new_events = parse_events(html)
        print(f"[MAIN] Nuevos eventos parseados: {len(new_events)}")

        if new_events:
            # Solo si hay eventos, chafamos la cache
            events = new_events
            save_cache(last_news_sent_at, events, cache_created_at)
            print("[MAIN] Cache actualizada con eventos nuevos.")
        else:
            # 200 pero 0 eventos = sesión inválida o HTML cambiado. Esto antes
            # pasaba en silencio y la cache se quedaba vieja durante días.
            msg = (
                "DRIFT ERROR: /news responde 200 pero 0 eventos parseados. "
                f"Cookies cargadas: {len(COOKIES)} (deben ser 2). "
                "Sesión caducada o web cambiada.\n"
                f"{ERROR_MENTION}"
            )
            print("[MAIN]", msg)
            send_telegram_message(msg)
    else:
        print("[MAIN] No se actualiza cache; se mantienen eventos anteriores.")

    # 2.a. Tirar los eventos que ya han ocurrido, calculado en vivo.
    before = len(events)
    events = [e for e in events if is_pending(e)]
    dropped = before - len(events)
    if dropped:
        print(f"[MAIN] Descartados {dropped} eventos ya pasados.")
        save_cache(last_news_sent_at, events, cache_created_at)

    # 2.b. Contar eventos high pendientes
    high_pending = [e for e in events if e.get("impact", "").lower() == "high"]
    num_high_pending = len(high_pending)
    print(f"[MAIN] Eventos high pendientes: {num_high_pending}")

    # Solo si quedan 3 o menos eventos high no passed, mandamos aviso solo a @xaxepro99
    if 0 < num_high_pending <= 3:
        warning = (
            f"DRIFT WARNING: el bot ve {num_high_pending} eventos high pendientes "
            "en horario de Brasil. Si ves más en la web, posible problema de cookies.\n"
            f"{ERROR_MENTION}"
        )
        print("[MAIN]", warning)
        send_telegram_message(warning)

    # 3. Decidir si toca enviar resumen de noticias (cada 30 minutos)
    now_local = datetime.now(TARGET_TZ)
    print(f"[MAIN] Ahora (Brasil, TARGET_TZ): {now_local.isoformat()}")

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

        # Eventos high pendientes, con los minutos calculados en vivo
        pending = []
        for e in events:
            if e["impact"].lower() != "high":
                continue

            try:
                m = minutes_until_event(e["datetime_raw"])
            except Exception as ex:
                print(f"[MAIN] Error calculando minutos para {e['name']}: {ex}")
                continue

            if m <= 0:
                print(f"[MAIN] {e['name']} ya ha pasado, fuera del resumen.")
                continue

            print(f"[MAIN] {e['name']} pendiente, empieza en {m:.1f} minutos (hora Brasil).")
            pending.append((m, e))

        pending.sort(key=lambda par: par[0])  # el más cercano primero
        print(f"[MAIN] Eventos high pendientes (resumen): {len(pending)}")

        if not pending:
            print("[MAIN] No hay eventos high pendientes para resumen.")
        else:
            nearest_minutes, nearest_event = pending[0]
            print(f"[MAIN] Evento más cercano: {nearest_event['name']} en {nearest_minutes:.1f} minutos.")

            lines = []
            for m, e in pending:
                # time_to recalculado, no el texto congelado que venía en la cache
                time_to = format_time_to(m)
                if e is nearest_event:
                    line = f"🔥 *{e['datetime_local']}* - *{e['name']}* ({time_to})"
                else:
                    line = f"*{e['datetime_local']}* - {e['name']} ({time_to})"
                lines.append(line)

            header = f"DRIFT NEWS (hora Brasil, {len(lines)} eventos high):\n\n"
            message = header + "\n".join(lines)
            mentions_line = build_mentions_line()
            if mentions_line:
                message += "\n\n" + mentions_line
            print("[MAIN] Mandando resumen de noticias:")
            print(message)
            send_telegram_message(message)
            last_news_sent_at = now_local.isoformat()
            save_cache(last_news_sent_at, events, cache_created_at)
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
