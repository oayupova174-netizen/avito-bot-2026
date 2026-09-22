import os
import time
import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import anthropic
from dotenv import load_dotenv

load_dotenv()

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_CHAT_ID = os.getenv("VK_CHAT_ID")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Множество для хранения ID уже обработанных последних сообщений, чтобы не спамить повторно
processed_messages = set()

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is alive!')
    
    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"[SERVER] Веб-сервер запущен на порту {port}", flush=True)
    server.serve_forever()

def get_avito_token():
    url = "https://api.avito.ru/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": AVITO_CLIENT_ID,
        "client_secret": AVITO_CLIENT_SECRET
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            return response.json().get("access_token")
        print(f"[ОШИБКА АВИТО] Ошибка токена: {response.text}", flush=True)
    except Exception as e:
        print(f"[ОШИБКА АВИТО ТАЙМАУТ]: {e}", flush=True)
    return None

def get_avito_user_id(token):
    url = "https://api.avito.ru/core/v1/accounts/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("id")
    except Exception as e:
        print(f"[ОШИБКА ПОЛУЧЕНИЯ USER ID]: {e}", flush=True)
    return None

def evaluate_resume_with_claude(candidate_text):
    system_prompt = """
    Ты — HR-ассистент юридической компании. Твоя задача — анализировать отклик кандидата на вакансию.
    
    Требования к кандидатам:
    - Возраст от 25 до 40 лет.
    - Опыт работы в продажах от 1 года.
    - Студенты-очники и те, кто ищет подработку — отклоняются.

    Оцени текст кандидата и верни СТРОГО в формате JSON:
    {
        "status": "Подходит" или "Подумать" или "Не подходит",
        "reason": "Краткое обоснование решения (почему подходит или нет)"
    }
    """
    try:
        response = claude_client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Текст отклика кандидата:\n{candidate_text}"}]
        )
        content = response.content[0].text.strip()
        
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            content = content[start_idx:end_idx+1]
            
        return json.loads(content)
    except Exception as e:
        print(f"[ОШИБКА CLAUDE]: {e}", flush=True)
        return {"status": "Подумать", "reason": "Ошибка анализа ИИ"}

def send_vk_notification(status, text, reason, chat_id):
    if status == "Подходит":
        emoji = "✅"
    elif status == "Подумать":
        emoji = "🤔"
    else:
        emoji = "❌"

    message = (
        f"{emoji} Статус: {status}\n\n"
        f"Причина: {reason}\n"
        f"Отклик: {text[:300]}...\n\n"
        f"🔗 Чат Авито: https://avito.ru/profile/messenger/channel/{chat_id}"
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
        requests.post(url, data=params, timeout=10)
    except Exception as e:
        print(f"[ОШИБКА VK]: {e}", flush=True)

def send_avito_reply(token, user_id, chat_id, text):
    url = f"https://api.avito.ru/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": {"text": text}, "type": "text"}
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"[ОШИБКА ОТВЕТА АВИТО]: {e}", flush=True)

def check_and_process():
    print("[CHECK] Проверяем свежие сообщения Авито...", flush=True)
    token = get_avito_token()
    if not token:
        return

    user_id = get_avito_user_id(token)
    if not user_id:
        print("[ОШИБКА]: Не удалось получить user_id", flush=True)
        return

    # Запрашиваем до 100 чатов, отсортированных по свежести (-time)
    url = f"https://api.avito.ru/messenger/v2/accounts/{user_id}/chats?limit=100&sort=-time"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers, timeout=10)
    
    if res.status_code != 200:
        print(f"[ОШИБКА ЧАТОВ АВИТО]: {res.status_code} {res.text}", flush=True)
        return

    chats = res.json().get("chats", [])
    print(f"[INFO] Получено чатов для проверки: {len(chats)}", flush=True)
    
    for chat in chats:
        chat_id = chat.get("id")
        last_msg_obj = chat.get("last_message", {})
        
        msg_id = last_msg_obj.get("id")
        author_id = last_msg_obj.get("author_id")
        
        # Пропускаем, если сообщение уже обрабатывали
        if not msg_id or msg_id in processed_messages:
            continue
            
        # Если автор сообщения — это мы сами (user_id), значит отвечать не нужно
        if str(author_id) == str(user_id):
            continue
            
        last_msg_text = last_msg_obj.get("content", {}).get("text", "")
        if not last_msg_text:
            continue

        # Помечаем сообщение как обработанное
        processed_messages.add(msg_id)
        
        print(f"[PROCESSING] Новое сообщение от кандидата в чате {chat_id}: {last_msg_text[:50]}...", flush=True)
        
        result = evaluate_resume_with_claude(last_msg_text)
        status = result.get("status")
        reason = result.get("reason")

        if status == "Подходит":
            send_avito_reply(token, user_id, chat_id, "Здравствуйте! Ваше резюме нас заинтересовало. Наш менеджер по персоналу свяжется с вами в ближайшее время для короткого интервью.")
            send_vk_notification("Подходит", last_msg_text, reason, chat_id)
        elif status == "Подумать":
            send_avito_reply(token, user_id, chat_id, "Здравствуйте! Спасибо за отклик. Уточните, пожалуйста, ваш возраст и подробности об опыте работы.")
            send_vk_notification("Подумать", last_msg_text, reason, chat_id)
        else:
            send_avito_reply(token, user_id, chat_id, "Здравствуйте! К сожалению, на данную вакансию мы ищем специалиста с другим опытом. Спасибо за интерес и успехов в поисках!")
            send_vk_notification("Не подходит", last_msg_text, reason, chat_id)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("[INIT] Бот проверки по последним сообщениям запущен...", flush=True)
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"[ОШИБКА ЦИКЛА]: {e}", flush=True)
        time.sleep(60)
