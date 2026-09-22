import os
import time
import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import anthropic
from dotenv import load_dotenv

# Загружаем ключи из нашего файла .env
load_dotenv()

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- Веб-сервер заглушка для обхода требований Render Web Service ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is alive!')

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()
# -------------------------------------------------------------------

def get_avito_token():
    url = "https://api.avito.ru/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": AVITO_CLIENT_ID,
        "client_secret": AVITO_CLIENT_SECRET
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        return response.json().get("access_token")
    print(f"[ОШИБКА АВИТО] Не удалось получить токен: {response.text}")
    return None

def evaluate_resume_with_claude(resume_text):
    system_prompt = """
    Ты — строгий HR-ассистент юридической компании. Твоя задача — жестко квалифицировать отклик кандидата по критериям.

    1. Категория "Подходит" (ВСЕ условия обязательны):
       - Есть опыт работы в ПРОДАЖАХ от 1 года (любое направление).
       - Возраст строго в диапазоне 25–40 лет.

    2. Категория "Не подходит" (Любой из стоп-факторов):
       - Вообще нет опыта работы.
       - Ищет подработку, частичную занятость или совмещение.
       - Школьник или студент (очного отделения).
       - Возраст младше 21 года или старше 50 лет.

    3. Категория "Подумать":
       - Все остальные случаи, не попавшие в "Подходит" и "Не подходит" (например, возраст 22–24 или 41–49 при наличии опыта).
       - Сообщение слишком короткое (например, "Привет", "Перезвоните"), нет данных о возрасте/опыте.

    Отвечай СТРОГО в формате JSON без дополнительного текста:
    {
        "status": "Подходит" или "Подумать" или "Не подходит",
        "reason": "Краткое пояснение причины на 1 предложение"
    }
    """
    try:
        response = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Текст отклика/сообщения кандидата:\n{resume_text}"}]
        )
        content = response.content[0].text.strip()
        if content.startswith("```"):
            content = content.replace("