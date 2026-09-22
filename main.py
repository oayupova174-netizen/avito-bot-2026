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
        else:
            print(f"[ОШИБКА АВИТО ТОКЕН]: Код {response.status_code} - {response.text}", flush=True)
    except Exception as e:
        print(f"[ОШИБКА АВИТО ТОКЕН EXCEPTION]: {e}", flush=True)
    return None

def get_avito_user_id(token):
    url = "https://api.avito.ru/core/v1/accounts/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("id")
        else:
            print(f"[ОШИБКА USER ID]: Код {res.status_code} - {res.text}", flush=True)
    except Exception as e:
        print(f"[ОШИБКА USER ID EXCEPTION]: {e}", flush=True)
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
    1. ИГНОРИРОВАНИЕ ПРОЩАНИЙ И БЛАГОДАРНОСТЕЙ (СТРОГО!): Если кандидат пишет слова благодарности, прощается или ставит точку (например: «Спасибо», «Благодарю», «Понял», «Хорошо», «До свидания», «ок»), то НЕ ОТВЕЧАЙ НИЧЕГО. Верни пустую строку в `reply_text`, чтобы бот сохранял молчание и не донимал человека.
    2. ОБРАБОТКА ОТКАЗОВ: Если кандидат пишет, что уже нашел работу, отказывается или ему неинтересно — ответь коротко и доброжелательно («Понял вас, спасибо за ответ! Успехов в поиске!») и поставь статус «Не подходит».
    3. ФИЛЬТРАЦИЯ ПО ОПЫТУ И ВОЗРАСТУ (ВАЖНО!): 
       - Если по возрасту подходит (25–40 лет), но нет опыта в продажах или опыт минимальный/другой — НИ В КОЕМ СЛУЧАЕ не ставь статус «Не подходит». Ставь статус «Подумать», мягко принимай информацию и говори, что HR-менеджер свяжется.
    4. ФИНАЛ ДЛЯ ПОДХОДЯЩИХ И «ПОДУМАТЬ»: 
       - Если кандидат подтверждает возраст и условия / согласен на созвон — ты кратко отвечаешь и сразу ставишь точку: предлагаешь созвониться и говоришь, что HR свяжется. («Отлично! Передал ваш контакт HR-менеджеру, скоро вам позвонят для короткого созвона»). Больше вопросов задавать не нужно.
    5. Если в сообщении кандидата нет информации о возрасте или опыте — мягко уточни это.
    
    Верни СТРОГО в формате JSON без лишнего текста:
    {
        "reply_text": "Текст ответа или пустая строка "", если отвечать не нужно",
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
            print(f"[ОШИБКА CLAUDE API]: Код {response.status_code} - {response.text}", flush=True)
            return {"reply_text": "Привет! Подскажите, сколько вам лет и есть ли опыт в продажах?", "status": "Подумать", "reason": "Ошибка API ИИ"}
            
        res_data = response.json()
        content = res_data.get("content", [{}])[0].get("text", "").strip()
        
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            content = content[start_idx:end_idx+1]
            
        return json.loads(content)
    except Exception as e:
        print(f"[ОШИБКА CLAUDE EXCEPTION]: {e}", flush=True)
        return {"reply_text": "", "status": "Подумать", "reason": "Сбой генерации"}

def send_vk_notification(candidate_text, ai_reply, status, chat_id):
    # Если бот решил промолчать (пустой ответ), то и в ВК спамить не нужно
    if not ai_reply or ai_reply.strip() == "":
        print(f"[INFO] Бот проигнорировал сообщение (прощание/благодарность), в ВК не отправляем.", flush=True)
        return

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
        res = requests.post(url, data=params, timeout=10)
        res_json = res.json()
        if "error" in res_json:
            print(f"[ОШИБКА VK API]: {res_json['error']}", flush=True)
        else:
            print(f"[VK SUCCESS] Уведомление успешно улетело в ВК (peer_id: {VK_CHAT_ID})", flush=True)
    except Exception as e:
        print(f"[ОШИБКА VK EXCEPTION]: {e}", flush=True)

def send_avito_reply(token, user_id, chat_id, text):
    if not text or text.strip() == "":
        print(f"[INFO] Текст ответа пустой, в Авито ничего не отправляем.", flush=True)
        return

    url = f"https://api.avito.ru/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": {"text": text}, "type": "text"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[ОШИБКА ОТВЕТА АВИТО]: Код {response.status_code} - {response.text}", flush=True)
    except Exception as e:
        print(f"[ОШИБКА ОТВЕТА АВИТО EXCEPTION]: {e}", flush=True)

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
        print(f"[ОШИБКА ЗАПРОСА ЧАТОВ АВИТО]: Код {res.status_code}", flush=True)
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
        reply_text = ai_data.get("reply_text", "")
        status = ai_data.get("status", "Подумать")
        
        send_avito_reply(token, user_id, chat_id, reply_text)
        send_vk_notification(last_msg_text, reply_text, status, chat_id)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("[INIT] Бот с защитой от ответов на благодарности запущен...", flush=True)
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"[ОШИБКА ЦИКЛА]: {e}", flush=True)
        time.sleep(60)
