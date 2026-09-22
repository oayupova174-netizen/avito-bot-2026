import os
import time
import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import anthropic
from dotenv import load_dotenv

# Загружаем ключи из переменных окружения
load_dotenv()

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_CHAT_ID = os.getenv("VK_CHAT_ID")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- Веб-сервер заглушка для Render Web Service ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is alive!')

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()
# --------------------------------------------------

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
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        return json.loads(content)
    except Exception as e:
        print(f"[ОШИБКА CLAUDE]: {e}")
        return {"status": "Подумать", "reason": "Ошибка авто-анализа, проверьте вручную."}

def send_vk_notification(status, text, reason, chat_id):
    if status == "Подходит":
        emoji = "✅"
    elif status == "Подумать":
        emoji = "🤔"
    else:
        emoji = "❌"

    message = (
        f"{emoji} Статус отклика: {status}\n\n"
        f"Причина: {reason}\n"
        f"Текст отклика: {text[:300]}...\n\n"
        f"Ссылка на чат Авито: https://avito.ru/profile/messenger/channel/{chat_id}"
    )
    
    url = "https://api.vk.com/method/messages.send"
    params = {
        "peer_id": VK_CHAT_ID,
        "message": message,
        "random_id": int(time.time() * 1000),
        "access_token": VK_GROUP_TOKEN,
        "v": "5.131"
    }
    try:
        res = requests.post(url, data=params).json()
        if "error" in res:
            print(f"[ОШИБКА VK]: {res['error']['error_msg']}")
    except Exception as e:
        print(f"[ОШИБКА ОТПРАВКИ VK]: {e}")

def send_avito_reply(token, chat_id, text):
    url = f"https://api.avito.ru/messenger/v1/accounts/self/chats/{chat_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": {"text": text}, "type": "text"}
    requests.post(url, headers=headers, json=payload)

def check_and_process():
    token = get_avito_token()
    if not token:
        return

    url = "https://api.avito.ru/messenger/v2/accounts/self/chats?unread_only=true"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers)
    
    if res.status_code != 200:
        return

    chats = res.json().get("chats", [])
    for chat in chats:
        chat_id = chat.get("id")
        last_msg = chat.get("last_message", {}).get("content", {}).get("text", "")
        
        if not last_msg:
            continue

        result = evaluate_resume_with_claude(last_msg)
        status = result.get("status")
        reason = result.get("reason")

        if status == "Подходит":
            send_avito_reply(token, chat_id, "Здравствуйте! Ваше резюме нас заинтересовало. Наш менеджер по персоналу свяжется с вами в ближайшее время для короткого интервью.")
            send_vk_notification("Подходит", last_msg, reason, chat_id)
        elif status == "Подумать":
            send_avito_reply(token, chat_id, "Здравствуйте! Спасибо за отклик. Уточните, пожалуйста, ваш возраст и подробности об опыте работы.")
            send_vk_notification("Подумать", last_msg, reason, chat_id)
        else:
            send_avito_reply(token, chat_id, "Здравствуйте! К сожалению, на данную вакансию мы ищем специалиста с другим опытом. Спасибо за интерес и успехов в поисках!")
            send_vk_notification("Не подходит", last_msg, reason, chat_id)

if __name__ == "__main__":
    print("Запускаем веб-сервер для поддержки Render Web Service...")
    threading.Thread(target=run_server, daemon=True).start()

    print("Бот запущен и проверяет отклики...")
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"Ошибка цикла: {e}")
        time.sleep(60)