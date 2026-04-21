import requests
import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

def send_notification(telegram_id: str, message: str) -> bool:
    if not BOT_TOKEN or BOT_TOKEN == "7797778162:AAG1NEEuozOnx3MM6Mw2j5JpxY8qxFgivyM":
        print(f"[BOT] {telegram_id}: {message}")
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": telegram_id,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        print(f"[BOT] Failed: {e}")
        return False
