import os
import time
import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_CHAT_ID = os.getenv("VK_CHAT_ID")

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
    except Exception as e:
        print(f"[ОШИБКА АВИТО ТОКЕН]: {e}", flush=True)
    return None

def get_avito_user_id(token):
    url = "https://api.avito.ru/core/v1/accounts/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("id")
    except Exception as e:
        print(f"[ОШИБКА USER ID]: {e}", flush=True)
    return None

def generate_ai_reply(candidate_text):
    system_prompt = """
    Ты — реальный рекрутер юридической компании. Общаешься в чате Авито как живой человек: просто, вежливо, без роботоподобных фраз.
    
    ГЛАВНАЯ ЦЕЛЬ БОТА: 
    Быстро проверить кандидата, получить согласие на созвон и завершить диалог фразой о том, что HR-менеджер скоро позвонит. Никаких бесконечных переписок.

    Условия вакансии (менеджер по продажам):
    - График: 5/2.
    - Зарплата: оклад + процент за каждый договор (в среднем от 60 000 рублей, потолка нет).
    
    Требования к кандидатам:
    - Возраст: от 25 до 40 лет.
    - Опыт в продажах: от 1 года.
    - Студенты-очники и те, кто ищет подработку — не подходят.

    Инструкции для ответов:
    1. ОБРАБОТКА ОТКАЗОВ: Если кандидат пишет, что уже нашел работу, отказывается или ему неинтересно — ответь коротко и доброжелательно («Понял вас, спасибо за ответ! Успехов в поиске!») и поставь статус «Не подходит».
    2. ФИНАЛ ДЛЯ ПОДХОДЯЩИХ И «ПОДУМАТЬ»: 
       - Если кандидат подтверждает возраст, опыт и согласен на созвон (или задает вопросы вне сценария / просит рассказать детали), ты кратко отвечаешь на вопрос (если он был) и сразу ставишь точку: предлагаешь созвониться и говоришь, что HR свяжется.
       - Пример ответа: «Отлично, условия подходят! Передал ваш контакт HR-менеджеру, скоро вам позвонят для короткого созвона».
       - Статус при этом ставится «Подходит» (или «Подумать», если человек сомневается, но контакт оставил).
       - ВАЖНО: Это сообщение должно быть финальным от бота. Больше вопросов задавать не нужно, ждем звонка рекрутера.
    3. Если в сообщении кандидата нет информации о возрасте или опыте — мягко уточни это.
    
    Верни СТРОГО в формате JSON без лишнего текста:
    {
        "reply_text": "Финальный живой ответ с отсылкой на звонок HR",
        "status": "Подходит" или "Подумать" или "Не подходит",
        "reason": "Краткая суть ответа или статус кандидата"
    }
    """
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": f"Сообщение кандидата:\n{candidate_text}"}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code != 200:
            return {"reply_text": "Привет! Подскажите, сколько вам лет и есть ли опыт в продажах?", "status": "Подумать", "reason": "Ошибка API ИИ"}
            
        res_data = response.json()
        content = res_data.get("content", [{}])[0].get("text", "").strip()
        
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            content = content[start_idx:end_idx+1]
            
        return json.loads(content)
    except Exception as e:
        print(f"[ОШИБКА CLAUDE]: {e}", flush=True)
        return {"reply_text": "Привет! Расскажите немного о своем опыте работы.", "status": "Подумать", "reason": "Сбой генерации"}

def send_vk_notification(candidate_text, ai_reply, status, chat_id):
    if status == "Подходит":
        emoji = "✅"
    elif status == "Подумать":
        emoji = "🤔"
    else:
        emoji = "❌"

    message = (
        f"{emoji} Сообщение кандидата:\n"
        f"\"{candidate_text}\"\n\n"
        f"🤖 Ответ бота:\n\"{ai_reply}\"\n\n"
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
    token = get_avito_token()
    if not token:
        return

    user_id = get_avito_user_id(token)
    if not user_id:
        return

    url = f"https://api.avito.ru/messenger/v2/accounts/{user_id}/chats?limit=50&sort=-time"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers, timeout=10)
    
    if res.status_code != 200:
        return

    chats = res.json().get("chats", [])
    
    for chat in chats:
        chat_id = chat.get("id")
        last_msg_obj = chat.get("last_message")
        
        if not last_msg_obj:
            continue
            
        msg_id = last_msg_obj.get("id")
        author_id = last_msg_obj.get("author_id")
        
        if not msg_id or msg_id in processed_messages:
            continue
            
        if str(author_id) == str(user_id):
            continue
            
        content_obj = last_msg_obj.get("content")
        if not content_obj:
            continue
            
        last_msg_text = content_obj.get("text", "")
        if not last_msg_text:
            continue

        processed_messages.add(msg_id)
        
        print(f"[PROCESSING] Сообщение в чате {chat_id}: {last_msg_text[:50]}...", flush=True)
        
        ai_data = generate_ai_reply(last_msg_text)
        reply_text = ai_data.get("reply_text", "Привет!")
        status = ai_data.get("status", "Подумать")
        
        send_avito_reply(token, user_id, chat_id, reply_text)
        send_vk_notification(last_msg_text, reply_text, status, chat_id)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("[INIT] Финальный диалоговый бот запущен...", flush=True)
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"[ОШИБКА ЦИКЛА]: {e}", flush=True)
        time.sleep(60)
