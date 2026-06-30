# Drift News Bot

Bot en Python que:
- Extrae eventos de driftfund.io/news
- Filtra eventos de alto impacto no pasados
- Convierte horas a Europe/Madrid
- Envía avisos a Telegram

## Requisitos
- Python 3.12
- requests
- beautifulsoup4
- lxml
- pytz

## Ejecutar
```bash
python news_bot.py
```

## Secrets
Configurar en GitHub Actions:
- TELEGRAM_TOKEN
- CHAT_ID
- COOKIE_NAME
- COOKIE_VALUE
