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
initiated_chats = set()

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

def generate_ai_reply(chat_history, vacancy_title="Менеджер по продажам", vacancy_city="не указан",
                       vacancy_address="не указан", vacancy_salary="не указана", vacancy_schedule="не указан"):
    system_prompt = f"""
    Ты — реальный рекрутер юридической компании. Общаешься в чате Авито как живой человек: просто, вежливо, без роботоподобных фраз.

    ГЛАВНАЯ ЦЕЛЬ БОТА:
    Быстро проверить кандидата, получить согласие на созвон и завершить диалог фразой о том, что HR-менеджер скоро позвонит.

    ЭТО КОНКРЕТНАЯ ВАКАНСИЯ, ПО КОТОРОЙ ИДЁТ ДИАЛОГ (бери данные о городе/адресе/зарплате/графике ТОЛЬКО отсюда, это реальные данные из карточки объявления на Авито):
    - Название вакансии: {vacancy_title}
    - Город/регион вакансии: {vacancy_city}
    - Адрес офиса: {vacancy_address}
    - Зарплата по этой вакансии: {vacancy_salary}
    - График по этой вакансии: {vacancy_schedule}

    У компании много вакансий в разных городах, с разными условиями. НИКОГДА не называй кандидату другой город, адрес, зарплату или график, кроме указанных выше — даже если тебе кажется, что "обычно" бывает иначе. Если какое-то поле выше указано как "не указан(а)", а кандидат про это спрашивает — не придумывай значение, а напиши, что уточнишь это у HR-менеджера при созвоне.

    ПЕРЕД ТЕМ КАК ОТВЕЧАТЬ, ОБЯЗАТЕЛЬНО СДЕЛАЙ ПРО СЕБЯ СЛЕДУЮЩЕЕ (не пиши это в ответе):
    1. Перечитай ВЕСЬ диалог с самого начала, а не только последнее сообщение.
    2. Выпиши для себя: что кандидат уже сообщил (возраст, опыт, отношение к графику и т.д.), какие вопросы ты уже задавал и получил ли на них ответ.
    3. Определи, о чём именно последнее сообщение кандидата — это ответ на твой вопрос, новый вопрос от него, отказ, или что-то не по теме вакансии.
    4. Не повторяй вопросы, на которые кандидат уже ответил ранее в этом же диалоге. Не проси данные повторно.
    5. Если сообщение кандидата непонятное, бессвязное или не по теме — не выдумывай факты и не отвечай наугад: вежливо переспроси именно то, что осталось непонятным.

    Требования:
    - Возраст: от 25 до 45 лет.
    - Опыт в продажах: от 1 года.
    - Студенты-очники и те, кто ищет подработку — не подходят.

    Инструкции по ответу:
    1. Отвечай по существу на последнее сообщение кандидата, с учётом всего, что уже обсуждалось выше в диалоге.
    2. Если кандидат подтверждает возраст, условия и опыт — предлагай созвон и пиши, что HR свяжется («Отлично! Передал ваш контакт HR-менеджеру, скоро вам позвонят для короткого созвона»).
    3. Если ключевой информации (возраст, опыт в продажах) ещё не было нигде в диалоге — вежливо уточни именно её, и только её.
    4. Если кандидат отказывается или говорит, что нашёл работу — ответь доброжелательно («Понял вас, спасибо за ответ! Успехов в поиске!») и поставь статус «Не подходит».
    5. Если кандидат НЕ подходит по возрасту, опыту или другим формальным критериям — это КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО, которое нельзя нарушать ни при каких условиях:
       - НИКОГДА, ни в каком виде, не упоминай кандидату ни возрастные рамки ("до 40", "от 25", "возрастной диапазон" и т.п.), ни требования к опыту ("нужен опыт от года" и т.п.), ни любые другие конкретные критерии отбора — ни как причину отказа, ни как подтверждение, ни в виде цифр, ни намёком.
       - Это правило действует ДАЖЕ ЕСЛИ кандидат сам называет свой возраст или опыт, настаивает, спрашивает "почему именно я не подхожу", "какой у вас возрастной диапазон" или просит уточнить причину. Ты не должен ни подтверждать, ни опровергать его догадки о причине — просто повторяй нейтральный отказ.
       - Используй ТОЛЬКО нейтральную вежливую формулировку без объяснения причины, например: «Спасибо за отклик! На данный момент, к сожалению, не сможем предложить вам эту позицию. Удачи в поиске работы!». При повторных вопросах кандидата о причине — вежливо повтори похожую нейтральную фразу, не раскрывая критериев, например: «Пока, к сожалению, не можем предложить вам эту позицию, но спасибо за уделённое время!»
       - Настоящую причину отказа (например, "не подходит по возрасту: 44 года") указывай ТОЛЬКО в поле "reason" — оно кандидату не показывается и используется только для внутреннего учёта.

    Верни СТРОГО в формате JSON без лишнего текста:
    {
        "reply_text": "Текст ответа соискателю",
        "status": "Подходит" или "Подумать" или "Не подходит",
        "age": "Возраст кандидата числом, если он называл его где-либо в диалоге, иначе строка «не указан»",
        "phone": "Номер телефона кандидата, если он называл его где-либо в диалоге, иначе строка «не указан»",
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

def build_opening_message(vacancy_title="Менеджер по продажам"):
    return (
        f"Добрый день! Меня зовут Полина, я рекрутер. Увидела ваш отклик на вакансию "
        f"«{vacancy_title}». Подскажите, пожалуйста, сколько вам лет и был ли у вас "
        f"опыт работы в продажах — если да, то сколько по времени?"
    )

def send_max_notification(candidate_name, candidate_city, candidate_age, candidate_phone, status, chat_id):
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
        f"🎂 Возраст: {candidate_age}\n"
        f"📞 Телефон: {candidate_phone}\n"
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

def get_job_applications_map(token, days=30):
    """Возвращает словарь {chat_id: {name, age, phone}} из последних откликов на вакансии
    (метод /job/v1/applications) — там открыты реальные ФИО, возраст и телефон кандидата."""
    from datetime import datetime, timedelta, timezone
    date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {token}"}

    ids = []
    cursor = None
    try:
        while True:
            params = {"updatedAtFrom": date_from, "createdAtFrom": date_from}
            if cursor:
                params["cursor"] = cursor
            res = requests.get(
                "https://api.avito.ru/job/v1/applications/get_ids",
                headers=headers, params=params, timeout=10
            )
            if res.status_code != 200:
                print(f"[ОШИБКА ПОЛУЧЕНИЯ ОТКЛИКОВ]: Код {res.status_code} - {res.text}", flush=True)
                break
            batch = res.json().get("applies") or []
            if not batch:
                break
            ids.extend([(item or {}).get("id") for item in batch if (item or {}).get("id")])
            if len(batch) < 100:
                break
            cursor = (batch[-1] or {}).get("id")
    except Exception as e:
        print(f"[ОШИБКА ПОЛУЧЕНИЯ ОТКЛИКОВ EXCEPTION]: {e}", flush=True)
        return {}

    result = {}
    for i in range(0, len(ids), 100):
        batch_ids = ids[i:i + 100]
        try:
            res = requests.post(
                "https://api.avito.ru/job/v1/applications/get_by_ids",
                headers={**headers, "Content-Type": "application/json"},
                json={"ids": batch_ids}, timeout=10
            )
            if res.status_code != 200:
                print(f"[ОШИБКА ДЕТАЛЕЙ ОТКЛИКОВ]: Код {res.status_code} - {res.text}", flush=True)
                continue
            for item in (res.json().get("applies") or []):
                item = item or {}
                contacts = item.get("contacts") or {}
                chat_value = (contacts.get("chat") or {}).get("value")
                if not chat_value:
                    continue
                applicant_data = (item.get("applicant") or {}).get("data") or {}
                name = applicant_data.get("name") or "Имя не указано"
                age_obj = (item.get("enriched_properties") or {}).get("age") or {}
                age = age_obj.get("value")
                phones = contacts.get("phones") or []
                first_phone = (phones[0] or {}) if phones else {}
                phone = first_phone.get("value")
                state = item.get("state") or "new"
                vacancy_id = item.get("vacancy_id")
                result[chat_value] = {
                    "name": name,
                    "age": str(age) if age else "не указан",
                    "phone": phone if phone else "не указан",
                    "state": state,
                    "vacancy_id": vacancy_id
                }
        except Exception as e:
            print(f"[ОШИБКА ДЕТАЛЕЙ ОТКЛИКОВ EXCEPTION]: {e}", flush=True)

    return result

vacancy_cache = {}  # vacancy_id -> {title, city, address, salary_text, schedule}
vacancy_cache_time = {}  # vacancy_id -> время последнего обновления

def get_vacancy_info(token, vacancy_id):
    """Получает реальную карточку вакансии (название, город, адрес, зарплату, график)
    по её vacancy_id — чтобы бот отвечал про ТУ вакансию, на которую откликнулся
    конкретный кандидат, а не придумывал город/офис."""
    if not vacancy_id:
        return None

    cached_at = vacancy_cache_time.get(vacancy_id, 0)
    if vacancy_id in vacancy_cache and (time.time() - cached_at) < 3600:  # кэш на 1 час
        return vacancy_cache[vacancy_id]

    url = f"https://api.avito.ru/job/v2/vacancies/{vacancy_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"[ОШИБКА КАРТОЧКИ ВАКАНСИИ {vacancy_id}]: Код {res.status_code} - {res.text}", flush=True)
            return vacancy_cache.get(vacancy_id)

        data = res.json() or {}
        address_details = data.get("addressDetails") or {}
        params = data.get("params") or {}
        salary_range = params.get("salary") or {}

        salary_text = "не указана"
        if salary_range.get("from") or salary_range.get("to"):
            parts = []
            if salary_range.get("from"):
                parts.append(f"от {salary_range['from']}")
            if salary_range.get("to"):
                parts.append(f"до {salary_range['to']}")
            salary_text = " ".join(parts) + " руб."

        info = {
            "title": data.get("title") or "Менеджер по продажам",
            "city": address_details.get("city") or "не указан",
            "address": address_details.get("address") or "не указан",
            "salary_text": salary_text,
            "schedule": params.get("schedule") or "не указан",
        }
        vacancy_cache[vacancy_id] = info
        vacancy_cache_time[vacancy_id] = time.time()
        return info
    except Exception as e:
        print(f"[ОШИБКА КАРТОЧКИ ВАКАНСИИ EXCEPTION {vacancy_id}]: {e}", flush=True)
        return vacancy_cache.get(vacancy_id)

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
    job_applications = get_job_applications_map(token)

    for chat in chats:
        chat_id = chat.get("id")
        last_msg_obj = chat.get("last_message")

        if not last_msg_obj:
            continue

        msg_id = last_msg_obj.get("id")
        author_id = last_msg_obj.get("author_id")
        msg_type = last_msg_obj.get("type")

        candidate_name = "Имя не указано"
        for u in chat.get("users", []):
            if str(u.get("id")) != str(user_id):
                candidate_name = u.get("name", candidate_name)
                break

        candidate_city = "Город не указан"
        vacancy_title = "Менеджер по продажам"
        context = chat.get("context") or {}
        if context.get("type") == "item":
            ctx_value = context.get("value") or {}
            candidate_city = (
                (ctx_value.get("location") or {}).get("title", candidate_city)
            )
            vacancy_title = ctx_value.get("title") or vacancy_title

        app_info = job_applications.get(chat_id)
        if app_info:
            candidate_name = app_info["name"]
            candidate_age = app_info["age"]
            candidate_phone = app_info["phone"]
            funnel_state = app_info.get("state", "new")
        else:
            candidate_age = "не указан"
            candidate_phone = "не указан"
            funnel_state = "new"

        # Подтягиваем реальную карточку вакансии (адрес, зарплату, график) —
        # это надёжнее, чем просто название из текста чата.
        vacancy_city = candidate_city
        vacancy_address = "не указан"
        vacancy_salary = "оклад + процент за каждый договор (в среднем от 60 000 рублей)"
        vacancy_schedule = "5/2"
        if app_info and app_info.get("vacancy_id"):
            v_info = get_vacancy_info(token, app_info["vacancy_id"])
            if v_info:
                vacancy_title = v_info["title"]
                vacancy_city = v_info["city"]
                vacancy_address = v_info["address"]
                vacancy_salary = v_info["salary_text"]
                vacancy_schedule = v_info["schedule"]

        # Кандидата уже закрыли вручную в воронке Авито (отказ/архив/приглашён) —
        # бот не должен больше писать ему или вмешиваться.
        if funnel_state in ("rejected", "archive", "selected"):
            processed_messages.add(msg_id) if msg_id else None
            initiated_chats.add(chat_id)
            continue

        # Новый отклик: Авито создаёт чат с системным сообщением
        # ("Кандидат откликнулся..."), а сам кандидат ещё ничего не написал.
        # Пишем ему первыми и сразу уведомляем MAX о новом отклике.
        # ВАЖНО: реагируем только на СВЕЖИЕ системные сообщения (последние 10 минут),
        # чтобы после перезапуска бот не начал писать по всем старым открытым откликам.
        if msg_type == "system":
            msg_created = last_msg_obj.get("created", 0) or 0
            age_seconds = time.time() - msg_created
            is_recent = age_seconds < 600  # 10 минут

            if chat_id not in initiated_chats and is_recent:
                initiated_chats.add(chat_id)
                print(f"[FIRST CONTACT] Новый отклик в чате {chat_id}, пишем кандидату первыми", flush=True)
                send_avito_reply(token, user_id, chat_id, build_opening_message(vacancy_title))
                send_max_notification(candidate_name, candidate_city, candidate_age, candidate_phone, "Подумать", chat_id)
            else:
                # старое системное сообщение (или уже обработанное) — просто запоминаем чат,
                # чтобы не возвращаться к нему снова
                initiated_chats.add(chat_id)
            continue

        if not msg_id or msg_id in processed_messages:
            continue

        if str(author_id) == str(user_id):
            continue

        # ВАЖНО: после перезапуска бота (деплой, пробуждение Render) эти наборы
        # обнуляются, и без проверки времени бот бросится отвечать разом на ВСЕ
        # старые диалоги, где последним писал кандидат. Отвечаем только на то,
        # что пришло недавно.
        msg_created = last_msg_obj.get("created", 0) or 0
        age_seconds = time.time() - msg_created
        if age_seconds > 900:  # 15 минут
            processed_messages.add(msg_id)
            initiated_chats.add(chat_id)
            continue

        content_obj = last_msg_obj.get("content")
        if not content_obj:
            continue

        last_msg_text = content_obj.get("content", {}).get("text", "") or content_obj.get("text", "")
        if not last_msg_text:
            continue

        processed_messages.add(msg_id)
        initiated_chats.add(chat_id)

        print(f"[PROCESSING] Обработка чата {chat_id}, загружаем историю...", flush=True)

        chat_history = get_chat_history(token, user_id, chat_id)
        if not chat_history:
            chat_history = [{"role": "user", "content": last_msg_text}]

        ai_data = generate_ai_reply(
            chat_history, vacancy_title, vacancy_city,
            vacancy_address, vacancy_salary, vacancy_schedule
        )
        reply_text = ai_data.get("reply_text", "")
        status = ai_data.get("status", "Подумать")

        app_info = job_applications.get(chat_id)
        if app_info:
            candidate_name = app_info["name"]
            candidate_age = app_info["age"]
            candidate_phone = app_info["phone"]
        else:
            candidate_age = ai_data.get("age", "не указан")
            candidate_phone = ai_data.get("phone", "не указан")
        
        if reply_text:
            send_avito_reply(token, user_id, chat_id, reply_text)
            send_max_notification(candidate_name, candidate_city, candidate_age, candidate_phone, status, chat_id)

if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta

    threading.Thread(target=run_server, daemon=True).start()
    print("[INIT] Бот запущен и готов отвечать на сообщения...", flush=True)
    while True:
        now_msk = datetime.now(timezone.utc) + timedelta(hours=3)
        print(f"[HEARTBEAT] Цикл проверки, время (МСК): {now_msk.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        try:
            check_and_process()
        except Exception as e:
            print(f"[ОШИБКА ЦИКЛА]: {e}", flush=True)
        time.sleep(60)
