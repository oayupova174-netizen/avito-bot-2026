import os
import time
import requests
import json
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
    Ты — ассистент рекрутера. Оцени отклик кандидата.
    Верни СТРОГО JSON формат:
    {
        "status": "Подходит" или "Подумать" или "Не подходит",
        "reason": "Краткая причина отбора на 1 предложение"
    }
    """
    try:
        response = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Отклик кандидата:\n{resume_text}"}]
        )
        content = response.content[0].text
        return json.loads(content)
    except Exception as e:
        print(f"[ОШИБКА CLAUDE]: {e}")
        return {"status": "Подумать", "reason": "Ошибка авто-анализа, проверьте вручную."}

def send_telegram_notification(status, text, reason, chat_id):
    emoji = "✅" if status == "Подходит" else "🤔"
    message = (
        f"{emoji} <b>Статус отклика: {status}</b>\n\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Текст отклика:</b> {text[:300]}...\n\n"
        f"🔗 <a href='https://avito.ru/profile/messenger/channel/{chat_id}'>Открыть чат в Авито</a>"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    requests.post(url, json=payload)

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
            send_avito_reply(token, chat_id, "Здравствуйте! Ваше резюме нас заинтересовало, скоро свяжемся.")
            send_telegram_notification("Подходит", last_msg, reason, chat_id)
        elif status == "Подумать":
            send_avito_reply(token, chat_id, "Здравствуйте! Ваше резюме на рассмотрении у рекрутера.")
            send_telegram_notification("Подумать", last_msg, reason, chat_id)
        else:
            send_avito_reply(token, chat_id, "Здравствуйте! К сожалению, сейчас мы не готовы предложить вам данную вакансию.")

if __name__ == "__main__":
    print("Бот запущен и проверяет отклики...")
    while True:
        try:
            check_and_process()
        except Exception as e:
            print(f"Ошибка цикла: {e}")
        time.sleep(60)