import os
import json
import time
import threading
import uuid
from datetime import datetime, timedelta, date
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification
from flask import Flask, request, abort

# ─── Переменные окружения ───
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

required = {
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "YOOKASSA_SHOP_ID": YOOKASSA_SHOP_ID,
    "YOOKASSA_SECRET_KEY": YOOKASSA_SECRET_KEY,
    "YANDEX_API_KEY": YANDEX_API_KEY,
    "YANDEX_FOLDER_ID": YANDEX_FOLDER_ID,
    "PLANTNET_API_KEY": PLANTNET_API_KEY,
    "WEATHER_API_KEY": WEATHER_API_KEY,
}
missing = [k for k, v in required.items() if not v]
if missing:
    raise ValueError(f"Отсутствуют обязательные переменные: {', '.join(missing)}")

# На Render порт всегда приходит через переменную окружения PORT
PORT = int(os.environ.get("PORT", "10000"))

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

flask_app = Flask(__name__)

# ─── СОЗДАЁМ APPLICATION ───
application = Application.builder().token(TELEGRAM_TOKEN).build()

# ─── ДАННЫЕ ───
DATA_FILE = "data.json"
user_data = {}
FREE_LIMITS = {
    "photos": 2,
    "reminders": 1,
    "gpt_queries": 5
}
STATE_WAIT_REGION = "wait_region"
STATE_ADD_REM_TEXT = "add_rem_text"
STATE_ADD_REM_DATE = "add_rem_date"
STATE_ADD_REM_TIME = "add_rem_time"
STATE_EDIT_REM_CHOOSE = "edit_rem_choose"
STATE_EDIT_REM_VALUE = "edit_rem_value"

# ─── Загрузка / сохранение ───
def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
            print("Данные загружены")
        except Exception as e:
            print(f"Ошибка загрузки: {e}")
            user_data = {}
    else:
        user_data = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
        print("Данные сохранены")
    except Exception as e:
        print(f"Ошибка сохранения: {e}")

load_data()

# ─── Проверка лимитов ─── (все функции ниже остаются без изменений)
def can_use_feature(uid: str, feature: str) -> tuple[bool, int]:
    user = user_data.setdefault(uid, {})
    if is_premium_active(uid):
        return True, 999
    today = date.today().isoformat()
    key_last = f"{feature}_last_date"
    key_count = f"{feature}_count"
    last_date = user.get(key_last)
    count = user.get(key_count, 0)
    if last_date != today:
        count = 0
        user[key_last] = today
        user[key_count] = 0
    max_count = FREE_LIMITS.get(feature, 999)
    if count >= max_count:
        return False, 0
    remaining = max_count - count - 1
    return True, max(0, remaining)

def use_feature(uid: str, feature: str):
    if is_premium_active(uid):
        return
    user = user_data.setdefault(uid, {})
    today = date.today().isoformat()
    user[f"{feature}_last_date"] = today
    user[f"{feature}_count"] = user.get(f"{feature}_count", 0) + 1
    save_data()

# ─── Премиум ───
def is_premium_active(uid: str) -> bool:
    user = user_data.get(uid, {})
    if not user.get("premium", False):
        return False
    until_str = user.get("premium_until")
    if not until_str:
        return False
    try:
        until = datetime.fromisoformat(until_str)
        return datetime.now() < until
    except:
        return False

def premium_expiration_checker():
    while True:
        now = datetime.now()
        changed = False
        for uid_str, user in list(user_data.items()):
            if user.get("premium", False):
                until_str = user.get("premium_until")
                if until_str:
                    try:
                        until = datetime.fromisoformat(until_str)
                        if now >= until:
                            user["premium"] = False
                            user.pop("premium_until", None)
                            changed = True
                            asyncio.run_coroutine_threadsafe(
                                application.bot.send_message(
                                    int(uid_str),
                                    "⚠️ Срок действия вашего Премиум-доступа истёк.\n"
                                    "Лимиты вернулись к бесплатным значениям.\n"
                                    "Чтобы продлить — нажмите кнопку «💎 Премиум»"
                                ),
                                asyncio.get_event_loop()
                            )
                    except:
                        user["premium"] = False
                        user.pop("premium_until", None)
                        changed = True
        if changed:
            save_data()
            print("Обновлены статусы премиум-доступа")
        time.sleep(300)

# ─── YandexGPT ─── (все функции дальше без изменений)
def ask_yandexgpt(region, question):
    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.75, "maxTokens": 1200},
            "messages": [
                {"role": "system", "text": f"Ты агроном-консультант. Регион: {region}. Отвечай на русском, пошагово, понятно."},
                {"role": "user", "text": question}
            ]
        }
        response = requests.post(url, headers=headers, json=data, timeout=15)
        response.raise_for_status()
        return response.json()["result"]["alternatives"][0]["message"]["text"].strip()
    except Exception as e:
        print(f"YandexGPT FAIL: {type(e).__name__}: {str(e)}")
        return f"Ошибка YandexGPT: {str(e)}. Попробуй спросить проще или позже."

# ─── Погода ───
def get_week_weather(city):
    try:
        url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        resp = requests.get(url, timeout=10).json()
        if resp.get("cod") != "200":
            return f"Ошибка погоды: {resp.get('message')}"
        days = {}
        for item in resp["list"]:
            d = item["dt_txt"].split()[0]
            temp = item["main"]["temp"]
            desc = item["weather"][0]["description"]
            days.setdefault(d, []).append((temp, desc))
        lines = ["🌦 Прогноз на 5 дней:"]
        for d, vals in list(days.items())[:5]:
            avg = sum(v[0] for v in vals) / len(vals)
            lines.append(f"{d}: {vals[0][1].capitalize()}, ≈{round(avg,1)}°C")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка погоды: {str(e)}"

# ─── PlantNet ───
async def analyze_plantnet(file_id, region):
    temp_path = f"temp_plant_{uuid.uuid4().hex[:8]}.jpg"
    try:
        file = await application.bot.get_file(file_id)
        downloaded_file = await application.bot.download_file(file.file_path)
        with open(temp_path, "wb") as f:
            f.write(downloaded_file)
        url = "https://my-api.plantnet.org/v2/identify/all"
        params = {"api-key": PLANTNET_API_KEY, "lang": "ru"}
        with open(temp_path, 'rb') as img_file:
            files = {'images': ('photo.jpg', img_file, 'image/jpeg')}
            response = requests.post(url, files=files, params=params, timeout=30)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if response.status_code != 200:
            return f"Pl@ntNet ошибка {response.status_code}"
        data = response.json()
        if "results" not in data or not data["results"]:
            return "Растение не распознано."
        best = data["results"][0]
        species = best["species"]
        sci_name = species.get("scientificNameWithoutAuthor", "—")
        family = species.get("family", {}).get("scientificNameWithoutAuthor", "—")
        common_names = species.get("commonNames", [])
        common_str = ", ".join(common_names[:3]) if common_names else "—"
        score = best["score"] * 100
        desc = f"**{sci_name}**\nСемейство: {family}\nНародные названия: {common_str}\nУверенность: {score:.1f}%"
        prompt = f"Растение: {sci_name} ({family}). Вероятность {score:.0f}%. Возможные болезни, вредители? Дай 2–3 совета по уходу в регионе {region}."
        gpt_advice = ask_yandexgpt(region, prompt)
        return f"Анализ фото:\n{desc}\n\n{gpt_advice}"
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return f"Ошибка анализа: {str(e)}"

# ─── Напоминания ─── (все функции без изменений)
def get_user_reminders(uid):
    return user_data.get(uid, {}).get("reminders", [])

def save_reminder(uid, text, dt_iso):
    user = user_data.setdefault(uid, {})
    reminders = user.setdefault("reminders", [])
    new_id = max([r.get("id", 0) for r in reminders], default=0) + 1
    reminders.append({"id": new_id, "text": text.strip(), "datetime": dt_iso, "sent": False})
    save_data()

def delete_reminder(uid, rem_id):
    user = user_data.get(uid, {})
    if "reminders" not in user:
        return False
    old_len = len(user["reminders"])
    user["reminders"] = [r for r in user["reminders"] if r.get("id") != rem_id]
    if len(user["reminders"]) < old_len:
        save_data()
        return True
    return False

def mark_reminder_sent(uid, rem_id):
    user = user_data.get(uid, {})
    for r in user.get("reminders", []):
        if r.get("id") == rem_id:
            r["sent"] = True
            save_data()
            return True
    return False

# ─── Клавиатуры ─── (без изменений)
def main_keyboard():
    keyboard = [
        [KeyboardButton("🌦 Погода"), KeyboardButton("📸 Диагностика")],
        [KeyboardButton("⏰ Напоминание"), KeyboardButton("💎 Премиум")],
        [KeyboardButton("📅 Календарь посадок")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def reminder_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить напоминание", callback_data="rem_add")],
        [InlineKeyboardButton("📋 Мои напоминания", callback_data="rem_list")],
        [InlineKeyboardButton("✏️ Редактировать / Удалить", callback_data="rem_edit_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def edit_reminder_actions_markup(rem_id):
    keyboard = [
        [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit_text_{rem_id}")],
        [InlineKeyboardButton("🗓 Изменить дату", callback_data=f"edit_date_{rem_id}")],
        [InlineKeyboardButton("⏰ Изменить время", callback_data=f"edit_time_{rem_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"del_rem_{rem_id}")],
        [InlineKeyboardButton("← Назад к списку", callback_data="rem_list")]
    ]
    return InlineKeyboardMarkup(keyboard)

def premium_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("🟡 День — 10 ₽", callback_data="premium_day")],
        [InlineKeyboardButton("🟢 Неделя — 50 ₽", callback_data="premium_week")],
        [InlineKeyboardButton("🔵 Месяц — 150 ₽", callback_data="premium_month")],
        [InlineKeyboardButton("🟣 Год — 1500 ₽", callback_data="premium_year")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="premium_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def culture_keyboard():
    cultures = [
        "Томаты 🍅", "Перец 🌶️", "Огурцы 🥒", "Капуста 🥬",
        "Морковь 🥕", "Свёкла 🍠", "Картофель 🥔", "Лук 🧅",
        "Чеснок 🧄", "Клубника 🍓", "Малина 🍇", "Зелень 🌿",
        "Баклажаны 🍆", "Кабачки", "Арбуз 🍉", "Дыня 🍈",
        "Фасоль", "Горох", "Цветы 🌸", "Другая культура"
    ]
    keyboard = []
    for i in range(0, len(cultures), 3):
        row = [KeyboardButton(c) for c in cultures[i:i+3] if c]
        keyboard.append(row)
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ─── YooKassa webhook ───
@flask_app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    try:
        event = request.get_json()
        notification = WebhookNotification(event)
        if notification.event == "payment.succeeded":
            payment = notification.object
            metadata = payment.metadata or {}
            uid = metadata.get("user_id")
            plan = metadata.get("plan")
            if uid and plan:
                days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
                days = days_map.get(plan, 30)
                now = datetime.now()
                until = now + timedelta(days=days)
                user = user_data.setdefault(uid, {})
                user["premium"] = True
                user["premium_until"] = until.isoformat()
                save_data()
                asyncio.run_coroutine_threadsafe(
                    application.bot.send_message(
                        int(uid),
                        f"✅ Оплата прошла успешно!\nПремиум до **{until.strftime('%d.%m.%Y %H:%M')}**!\nСпасибо 🌱",
                        parse_mode="Markdown",
                        reply_markup=main_keyboard()
                    ),
                    asyncio.get_event_loop()
                )
        return '', 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return '', 200

# ─── Telegram webhook ───
@flask_app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') != 'application/json':
        abort(403)
    try:
        update_dict = request.get_json(force=True)
    except:
        print("Невалидный JSON")
        return '', 200

    if not hasattr(application, 'bot') or application.bot is None:
        print("Бот ещё не готов → пропускаем")
        return '', 200

    try:
        update = Update.de_json(update_dict, application.bot)
        asyncio.create_task(application.process_update(update))
    except Exception as e:
        print(f"Ошибка process_update: {e}")
    return '', 200

# ─── Временный маршрут для установки webhook (вызвать один раз после деплоя) ───
@flask_app.route('/set_webhook', methods=['GET'])
def set_webhook():
    try:
        # На Render имя хоста приходит в переменной RENDER_EXTERNAL_HOSTNAME
        domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if not domain:
            return "Переменная RENDER_EXTERNAL_HOSTNAME не найдена", 500
        webhook_url = f"https://{domain}/telegram_webhook"
        application.bot.set_webhook(url=webhook_url)
        return f"Webhook установлен на: {webhook_url}", 200
    except Exception as e:
        return f"Ошибка установки webhook: {str(e)}", 500

# ─── Handlers ─── (все без изменений)
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {}
    user = user_data[uid]
    if "region" in user and user["region"].strip():
        await update.message.reply_text(
            f"Рад вас снова видеть! Ваш регион: {user['region']}",
            reply_markup=main_keyboard()
        )
    else:
        await update.message.reply_text(
            "Привет! Я бот-агроном. Укажи свой регион для персонализированных советов.",
            reply_markup=ReplyKeyboardRemove()
        )
        user["state"] = STATE_WAIT_REGION
        save_data()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_data or "region" not in user_data[uid]:
        await update.message.reply_text("Сначала /start и укажи регион.")
        return
    can_use, remaining = can_use_feature(uid, "photos")
    if not can_use:
        await update.message.reply_text("🚫 Лимит бесплатной диагностики исчерпан (2 фото).\nХотите без ограничений? Купите Премиум!")
        return
    use_feature(uid, "photos")
    photo = update.message.photo[-1].file_id
    analysis = await analyze_plantnet(photo, user_data[uid].get("region", "Москва"))
    await update.message.reply_text(analysis, reply_markup=main_keyboard(), parse_mode="Markdown")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    text = update.message.text.strip() if update.message.text else ""
    if uid not in user_data:
        await update.message.reply_text("Нажми /start")
        return
    user = user_data[uid]
    state = user.get("state")

    # ... (весь остальной код message_handler без изменений)
    # Для экономии места здесь опущен, но он должен остаться полностью как был

    # В конце функции должен быть возврат '', но поскольку это async — просто завершается

# ─── Callback handler ─── (без изменений, опущен для краткости)

# ─── Добавляем handlers ───
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(callback_handler))

# ─── Фоновые задачи ───
def reminders_checker():
    while True:
        now = datetime.now()
        for uid_str, user in list(user_data.items()):
            reminders = user.get("reminders", [])
            for rem in reminders:
                if rem.get("sent"):
                    continue
                try:
                    rem_time = datetime.fromisoformat(rem["datetime"])
                    if rem_time <= now:
                        asyncio.run_coroutine_threadsafe(
                            application.bot.send_message(
                                chat_id=int(uid_str),
                                text=f"🔔 Напоминание!\n{rem['text']}"
                            ),
                            asyncio.get_event_loop()
                        )
                        mark_reminder_sent(uid_str, rem["id"])
                except Exception as e:
                    print(f"Ошибка отправки напоминания {uid_str}: {e}")
        time.sleep(60)

# ─── Health check ───
@flask_app.route('/health', methods=['GET', 'HEAD'])
def health_check():
    return 'OK', 200

# ─── Запускаем фоновые потоки (важно для Render / gunicorn) ───
threading.Thread(target=reminders_checker, daemon=True).start()
threading.Thread(target=premium_expiration_checker, daemon=True).start()

# ─── Обработка graceful shutdown ───
import signal

async def shutdown():
    print("Остановка Telegram Application...")
    await application.stop()
    await application.shutdown()
    print("Telegram Application остановлен")

def handle_shutdown(signum, frame):
    print(f"Получен сигнал {signum}, останавливаем приложение...")
    asyncio.run(shutdown())

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

print("Приложение готово к работе. Ожидание запросов...")
