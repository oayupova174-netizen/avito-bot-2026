import os
import time
import requests
import json
import threading
import certifi
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

_max_ca_bundle_path = None

def get_max_ca_bundle():
    """Скачивает сертификаты Минцифры и объединяет их с обычным набором сертификатов,
    чтобы requests могли проверять HTTPS-соединение с MAX API."""
    global _max_ca_bundle_path
    if _max_ca_bundle_path and os.path.exists(_max_ca_bundle_path):
        return _max_ca_bundle_path

    bundle_path = "/tmp/max_ca_bundle.pem"
    try:
        root_ca = requests.get(
            "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
            verify=False, timeout=10
        ).text
        sub_ca = requests.get(
            "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
            verify=False, timeout=10
        ).text
        with open(certifi.where(), "r", encoding="utf-8") as f:
            base_bundle = f.read()
        with open(bundle_path, "w", encoding="utf-8") as f:
            f.write(base_bundle)
            f.write("\n")
            f.write(root_ca)
            f.write("\n")
            f.write(sub_ca)
        _max_ca_bundle_path = bundle_path
        return bundle_path
    except Exception as e:
        print(f"[ОШИБКА СЕРТИФИКАТА MAX]: {e}", flush=True)
        return certifi.where()

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")

processed_messages = set()

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/max_updates"):
            max_token = os.getenv("MAX_BOT_TOKEN", "")
            try:
                res = requests.get(
                    "https://platform-api2.max.ru/updates",
                    headers={"Authorization": max_token},
                    timeout=10,
                    verify=get_max_ca_bundle()
                )
                body = res.text
            except Exception as e:
                body = f"ERROR: {e}"
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return
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

def get_chat_history(token, user_id, chat_id):
    """Получает всю историю сообщений чата с Авито для анализа контекста"""
    url = f"https://api.avito.ru/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages?limit=20"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            messages_data = res.json().get("messages", [])
            messages_data = sorted(messages_data, key=lambda x: x.get("created", 0))
            formatted_history = []
            for m in messages_data:
                author_id = m.get("author_id")
                text = m.get("content", {}).get("text", "")
                if not text:
                    continue
                role = "assistant" if str(author_id) == str(user_id) else "user"
                formatted_history.append({"role": role, "content": text})
            return formatted_history
    except Exception as e:
        print(f"[ОШИБКА ИСТОРИИ ЧАТА]: {e}", flush=True)
    return []

def generate_ai_reply(chat_history):
    system_prompt = """
    Ты — реальный рекрутер юридической компании. Общаешься в чате Авито как живой человек: просто, вежливо, без роботоподобных фраз.

    ГЛАВНАЯ ЦЕЛЬ БОТА:
    Быстро проверить кандидата, получить согласие на созвон и завершить диалог фразой о том, что HR-менеджер скоро позвонит.

    ПЕРЕД ТЕМ КАК ОТВЕЧАТЬ, ОБЯЗАТЕЛЬНО СДЕЛАЙ ПРО СЕБЯ СЛЕДУЮЩЕЕ (не пиши это в ответе):
    1. Перечитай ВЕСЬ диалог с самого начала, а не только последнее сообщение.
    2. Выпиши для себя: что кандидат уже сообщил (возраст, опыт, отношение к графику и т.д.), какие вопросы ты уже задавал и получил ли на них ответ.
    3. Определи, о чём именно последнее сообщение кандидата — это ответ на твой вопрос, новый вопрос от него, отказ, или что-то не по теме вакансии.
    4. Не повторяй вопросы, на которые кандидат уже ответил ранее в этом же диалоге. Не проси данные повторно.
    5. Если сообщение кандидата непонятное, бессвязное или не по теме — не выдумывай факты и не отвечай наугад: вежливо переспроси именно то, что осталось непонятным.

    Условия вакансии (менеджер по продажам):
    - График: 5/2.
    - Зарплата: оклад + процент за каждый договор (в среднем от 60 000 рублей).

    Требования:
    - Возраст: от 25 до 40 лет.
    - Опыт в продажах: от 1 года.
    - Студенты-очники и те, кто ищет подработку — не подходят.

    Инструкции по ответу:
    1. Отвечай по существу на последнее сообщение кандидата, с учётом всего, что уже обсуждалось выше в диалоге.
    2. Если кандидат подтверждает возраст, условия и опыт — предлагай созвон и пиши, что HR свяжется («Отлично! Передал ваш контакт HR-менеджеру, скоро вам позвонят для короткого созвона»).
    3. Если ключевой информации (возраст, опыт в продажах) ещё не было нигде в диалоге — вежливо уточни именно её, и только её.
    4. Если кандидат отказывается или говорит, что нашёл работу — ответь доброжелательно («Понял вас, спасибо за ответ! Успехов в поиске!») и поставь статус «Не подходит».
    5. Если кандидат НЕ подходит по возрасту, опыту или другим формальным критериям — НИКОГДА не называй кандидату истинную причину отказа (нельзя писать "вам не подходит по возрасту", "нужен опыт от года" и т.п. в ответе кандидату). Вместо этого используй нейтральную вежливую формулировку без объяснения причины, например: «Спасибо за отклик! На данный момент, к сожалению, не сможем предложить вам эту позицию. Удачи в поиске работы!». Настоящую причину отказа указывай ТОЛЬКО в поле "reason" — оно кандидату не показывается.

    Верни СТРОГО в формате JSON без лишнего текста:
    {
        "reply_text": "Текст ответа соискателю",
        "status": "Подходит" или "Подумать" или "Не подходит",
        "reason": "Краткая суть ответа или статус"
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
        "max_tokens": 400,
        "system": system_prompt,
        "messages": chat_history
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code != 200:
            print(f"[ОШИБКА CLAUDE API]: Код {response.status_code} - {response.text}", flush=True)
            return {"reply_text": "", "status": "Подумать", "reason": "Ошибка API ИИ"}
            
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

def send_max_notification(candidate_name, candidate_city, status, chat_id):
    if not MAX_CHAT_ID:
        print("[ОШИБКА MAX]: не задана переменная MAX_CHAT_ID", flush=True)
        return

    if status == "Подходит":
        emoji = "✅"
    elif status == "Подумать":
        emoji = "🤔"
    else:
        emoji = "❌"

    message = (
        f"{emoji} {candidate_name}\n"
        f"📍 {candidate_city}\n"
        f"🔗 Чат Авито: https://avito.ru/profile/messenger/channel/{chat_id}"
    )

    url = f"https://platform-api2.max.ru/messages?chat_id={MAX_CHAT_ID}"
    headers = {
        "Authorization": MAX_BOT_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(
            url, headers=headers, json={"text": message},
            timeout=10, verify=get_max_ca_bundle()
        )
        if res.status_code != 200:
            print(f"[ОШИБКА MAX API]: Код {res.status_code} - {res.text}", flush=True)
        else:
            print("[MAX SUCCESS] Уведомление отправлено в чат MAX", flush=True)
    except Exception as e:
        print(f"[ОШИБКА MAX EXCEPTION]: {e}", flush=True)

def send_avito_reply(token, user_id, chat_id, text):
    if not text or text.strip() == "":
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
            
        last_msg_text = content_obj.get("content", {}).get("text", "") or content_obj.get("text", "")
        if not last_msg_text:
            continue

        processed_messages.add(msg_id)
        
        print(f"[PROCESSING] Обработка чата {chat_id}, загружаем историю...", flush=True)

        candidate_name = "Имя не указано"
        for u in chat.get("users", []):
            if str(u.get("id")) != str(user_id):
                candidate_name = u.get("name", candidate_name)
                break

        candidate_city = "Город не указан"
        context = chat.get("context") or {}
        if context.get("type") == "item":
            candidate_city = (
                context.get("value", {}).get("location", {}).get("title", candidate_city)
            )

        chat_history = get_chat_history(token, user_id, chat_id)
        if not chat_history:
            chat_history = [{"role": "user", "content": last_msg_text}]

        ai_data = generate_ai_reply(chat_history)
        reply_text = ai_data.get("reply_text", "")
        status = ai_data.get("status", "Подумать")
        
        if reply_text:
            send_avito_reply(token, user_id, chat_id, reply_text)
            send_max_notification(candidate_name, candidate_city, status, chat_id)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("[INIT] Бот запущен и готов отвечать на сообщения...", flush=True)
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"[ОШИБКА ЦИКЛА]: {e}", flush=True)
        time.sleep(60)
