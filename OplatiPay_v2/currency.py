# currency.py — получаем курс доллара с ЦБ РФ

import requests

def get_usd_rate():
    try:
        response = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=5)
        response.raise_for_status()
        return response.json()["Valute"]["USD"]["Value"]
    except Exception as e:
        print(f"Ошибка получения курса: {e}")
        return None