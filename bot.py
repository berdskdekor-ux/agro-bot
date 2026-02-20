import os
import json
import time
import threading
import uuid
from datetime import datetime, timedelta, date
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, HTMLResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
from datetime import timezone

main_loop = None

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

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# ─── FastAPI приложение ───
app = FastAPI(title="Агроном-бот", description="Telegram бот для садоводов и огородников")

@app.get("/success")
async def payment_success():
    html_content = """
    <html>
        <head><title>Оплата прошла успешно</title></head>
        <body style="font-family:sans-serif; text-align:center; padding:50px;">
            <h1 style="color:#2e7d32;">Оплата прошла успешно! 🎉</h1>
            <p>Премиум-доступ уже активирован в боте.</p>
            <p>Можете вернуться в Telegram и продолжить пользоваться ботом.</p>
            <p><a href="https://t.me/ВАШ_БОТ_НИК">Вернуться в бот</a></p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# ─── Telegram Application ───
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

# ─── Проверка лимитов ───
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
                            expire_msg = (
                                "⚠️ <b>Премиум-доступ закончился</b>\n\n"
                                f"Срок действия истёк {until.strftime('%d.%m.%Y %H:%M')}.\n"
                                "Вернулись обычные лимиты:\n"
                                "• 2 фото для диагностики в день\n"
                                "• 5 вопросов агроному в день\n"
                                "• 1 напоминание\n\n"
                                "Хочешь вернуть безлимит? Нажми «💎 Премиум» в меню!"
                            )
                            asyncio.run_coroutine_threadsafe(
                                application.bot.send_message(
                                    int(uid_str),
                                    expire_msg,
                                    parse_mode="HTML",
                                    reply_markup=main_keyboard()
                                ),
                                main_loop
                            )
                    except Exception:
                        user["premium"] = False
                        user.pop("premium_until", None)
                        changed = True
        if changed:
            save_data()
            print("Обновлены статусы премиум-доступа")
        time.sleep(300)

# ─── YandexGPT ───
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
        print(f"[PLANTNET] Начинаем обработку фото, file_id={file_id}, region={region}")
        file_obj = await application.bot.get_file(file_id)
        print(f"[PLANTNET] Получен File объект, file_path={file_obj.file_path}")
        photo_bytes = await file_obj.download_as_bytearray()
        print(f"[PLANTNET] Фото скачано, размер: {len(photo_bytes)} байт")
        with open(temp_path, "wb") as f:
            f.write(photo_bytes)
        print(f"[PLANTNET] Фото сохранено во временный файл: {temp_path}")
        url = "https://my-api.plantnet.org/v2/identify/all"
        params = {"api-key": PLANTNET_API_KEY, "lang": "ru"}
        with open(temp_path, 'rb') as img_file:
            files = {'images': ('photo.jpg', img_file, 'image/jpeg')}
            response = requests.post(url, files=files, params=params, timeout=30)
        print(f"[PLANTNET] Ответ от API: status={response.status_code}")
        if response.status_code != 200:
            return f"Pl@ntNet вернул ошибку {response.status_code}: {response.text[:200]}"
        data = response.json()
        if "results" not in data or not data["results"]:
            return "Растение не распознано. Попробуйте фото крупнее / чётче / с другого ракурса."
        best = data["results"][0]
        species = best["species"]
        sci_name = species.get("scientificNameWithoutAuthor", "—")
        family = species.get("family", {}).get("scientificNameWithoutAuthor", "—")
        common_names = species.get("commonNames", [])
        common_str = ", ".join(common_names[:3]) if common_names else "—"
        score = best["score"] * 100
        desc = f"**{sci_name}**\nСемейство: {family}\nНародные названия: {common_str}\nУверенность: {score:.1f}%"
        prompt = (
            f"Растение: {sci_name} ({family}). Вероятность {score:.0f}%. "
            f"Возможные болезни, вредители? Дай 2–3 совета по уходу в регионе {region}."
        )
        gpt_advice = ask_yandexgpt(region, prompt)
        result = f"Анализ фото:\n{desc}\n\n{gpt_advice}"
        return result
    except Exception as e:
        error_text = f"Ошибка анализа: {type(e).__name__}: {str(e)}"
        print(f"[PLANTNET-ERROR] {error_text}")
        return error_text + "\n\nПопробуйте отправить другое фото или повторить позже."
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print(f"[PLANTNET] Временный файл удалён: {temp_path}")
            except Exception as cleanup_e:
                print(f"[PLANTNET-CLEANUP] Не удалось удалить {temp_path}: {cleanup_e}")

# ─── Напоминания ───
def get_user_reminders(uid):
    return user_data.get(uid, {}).get("reminders", [])

def save_reminder(uid, text, dt_local):
    user = user_data.setdefault(uid, {})
    tz_str = user.get("timezone", "UTC")
    tz = pytz.timezone(tz_str)
    dt_aware_local = tz.localize(dt_local)
    dt_utc = dt_aware_local.astimezone(pytz.UTC)
    reminders = user.setdefault("reminders", [])
    new_id = max([r.get("id", 0) for r in reminders], default=0) + 1
    reminders.append({
        "id": new_id,
        "text": text.strip(),
        "datetime_utc": dt_utc.isoformat(),
        "sent": False
    })
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

# ─── Клавиатуры ───
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
@app.post("/yookassa-webhook")
async def yookassa_webhook(request: Request):
    try:
        event = await request.json()
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
                success_msg = (
                    "🎉 <b>Оплата прошла успешно!</b>\n\n"
                    f"💎 Премиум-доступ активирован до {until.strftime('%d.%m.%Y %H:%M')}\n"
                    "Теперь у тебя:\n"
                    "• безлимитная диагностика растений\n"
                    "• безлимитные запросы к агроному\n"
                    "• безлимитные напоминания\n\n"
                    "Спасибо, что поддерживаешь проект 🌱"
                )
                asyncio.run_coroutine_threadsafe(
                    application.bot.send_message(
                        int(uid),
                        success_msg,
                        parse_mode="HTML",
                        reply_markup=main_keyboard()
                    ),
                    main_loop
                )
        return PlainTextResponse("", status_code=200)
    except Exception as e:
        print(f"Webhook error: {e}")
        return PlainTextResponse("", status_code=200)

# ─── Telegram webhook ───
@app.post("/telegram_webhook")
async def telegram_webhook(request: Request):
    if request.headers.get("content-type") != "application/json":
        raise HTTPException(status_code=403)
    try:
        update_dict = await request.json()
        update = Update.de_json(update_dict, application.bot)
        await application.process_update(update)
        return {}
    except Exception as e:
        print(f"Ошибка process_update: {e}")
        return {}

# ─── Health check ───
@app.get("/health")
async def health_check():
    return {"status": "OK"}

# ─── Handlers ───
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
    await update.message.reply_text(analysis, reply_markup=main_keyboard())

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    text = update.message.text.strip() if update.message.text else ""
    if uid not in user_data:
        await update.message.reply_text("Нажми /start")
        return
    user = user_data[uid]
    state = user.get("state")

    if state == STATE_WAIT_REGION:
        region = text.strip()
        if len(region) < 3:
            await update.message.reply_text("Название региона слишком короткое. Попробуйте ещё раз.")
            return

        user_timezone = "UTC"
        try:
            geolocator = Nominatim(user_agent="agro_bot")
            location = geolocator.geocode(region, exactly_one=True, timeout=10)
            if location:
                tf = TimezoneFinder()
                tz_name = tf.timezone_at(lng=location.longitude, lat=location.latitude)
                if tz_name:
                    user_timezone = tz_name
                    print(f"[TZ] Для региона '{region}' найден timezone: {tz_name}")
        except Exception as e:
            print(f"[TZ-ERROR] {type(e).__name__}: {e}")

        user["region"] = region
        user["timezone"] = user_timezone
        user.pop("state", None)
        save_data()

        await update.message.reply_text(
            f"Отлично! Запомнил: **{region}** 🌍\n"
            f"Часовой пояс: **{user_timezone}**\n"
            "Теперь рекомендации и напоминания будут учитывать ваш часовой пояс.",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        return

    if state == STATE_ADD_REM_TEXT:
        if not text.strip():
            await update.message.reply_text("Текст не может быть пустым.")
            return
        user["temp_rem_text"] = text.strip()
        user["state"] = STATE_ADD_REM_DATE
        await update.message.reply_text("Укажите дату: дд.мм.гггг\nПример: 15.03.2026")
        save_data()
        return

    elif state == STATE_ADD_REM_DATE:
        try:
            d, m, y = map(int, text.replace(" ", "").split("."))
            dt_date = datetime(y, m, d)
            if dt_date < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
                await update.message.reply_text("Дата должна быть в будущем.")
                return
            user["temp_rem_date"] = dt_date
            user["state"] = STATE_ADD_REM_TIME
            await update.message.reply_text("Укажите время: чч:мм\nПример: 14:30")
            save_data()
        except:
            await update.message.reply_text("Неверный формат. Ожидается: 15.03.2026")
        return

    elif state == STATE_ADD_REM_TIME:
        try:
            h, mm = map(int, text.replace(" ", "").split(":"))
            dt_local = user["temp_rem_date"].replace(hour=h, minute=mm)
            if dt_local < datetime.now():
                await update.message.reply_text("Дата+время должны быть в будущем.")
                return

            save_reminder(uid, user["temp_rem_text"], dt_local)

            tz_str = user.get("timezone", "UTC")
            tz = pytz.timezone(tz_str)
            dt_aware_local = tz.localize(dt_local)
            local_str = dt_aware_local.strftime("%d.%m.%Y %H:%M")

            await update.message.reply_text(
                f"Напоминание создано!\n"
                f"Время: **{local_str}** (ваш пояс: {tz_str})\n"
                f"Текст: {user['temp_rem_text']}",
                reply_markup=main_keyboard(),
                parse_mode="Markdown"
            )

            can_use, _ = can_use_feature(uid, "reminders")
            if not can_use and not is_premium_active(uid):
                reminders = get_user_reminders(uid)
                if reminders:
                    delete_reminder(uid, max(r["id"] for r in reminders))
                await update.message.reply_text("Лимит бесплатных напоминаний исчерпан.")
                return

            if not is_premium_active(uid):
                user["reminders_created"] = user.get("reminders_created", 0) + 1
                save_data()

            user.pop("state", None)
            user.pop("temp_rem_text", None)
            user.pop("temp_rem_date", None)
            save_data()

        except:
            await update.message.reply_text("Неверный формат времени. Пример: 14:30")
        return

    elif state == STATE_EDIT_REM_VALUE:
        rem_id = user.get("temp_rem_id")
        field = user.get("edit_field")
        reminder = next((r for r in get_user_reminders(uid) if r.get("id") == rem_id), None)
        if not reminder or not field:
            await update.message.reply_text("Ошибка. Попробуйте заново.")
            user.pop("state", None)
            save_data()
            return

        dt_utc = datetime.fromisoformat(reminder["datetime_utc"]).replace(tzinfo=pytz.UTC)
        tz = pytz.timezone(user.get("timezone", "UTC"))
        dt_local = dt_utc.astimezone(tz)

        try:
            if field == "text":
                reminder["text"] = text.strip()
            elif field == "date":
                d, m, y = map(int, text.replace(" ", "").split("."))
                new_dt_local = datetime(y, m, d, dt_local.hour, dt_local.minute)
                if new_dt_local < datetime.now():
                    await update.message.reply_text("Дата должна быть в будущем.")
                    return
                new_dt_aware = tz.localize(new_dt_local)
                reminder["datetime_utc"] = new_dt_aware.astimezone(pytz.UTC).isoformat()
            elif field == "time":
                h, mm = map(int, text.replace(" ", "").split(":"))
                new_dt_local = dt_local.replace(hour=h, minute=mm)
                if new_dt_local < datetime.now():
                    await update.message.reply_text("Время должно быть в будущем.")
                    return
                new_dt_aware = tz.localize(new_dt_local)
                reminder["datetime_utc"] = new_dt_aware.astimezone(pytz.UTC).isoformat()

            save_data()
            await update.message.reply_text(
                f"Значение обновлено ✓\n"
                f"Новое время: {new_dt_local.strftime('%d.%m.%Y %H:%M')} ({user.get('timezone', 'UTC')})",
                reply_markup=main_keyboard()
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка формата: {str(e)}")
        finally:
            user.pop("state", None)
            user.pop("temp_rem_id", None)
            user.pop("edit_field", None)
            save_data()
        return

    text_lower = text.lower()
    if text == "🌦 Погода":
        answer = get_week_weather(user.get("region", "Moscow"))
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    elif text == "📸 Диагностика":
        await update.message.reply_text("Пришли фото растения крупным планом (лист, цветок, плод, стебель или повреждения).")
        return
    elif text == "⏰ Напоминание":
        await update.message.reply_text("Выбери действие:", reply_markup=reminder_inline_keyboard())
        return
    elif text == "💎 Премиум":
        await update.message.reply_text(
            "💎 <b>Premium-доступ</b>\n\nЧто даёт:\n• Без ограничений\n• Приоритетные ответы\n• Поддержка проекта\n\nВыбери тариф:",
            parse_mode="HTML",
            reply_markup=premium_inline_keyboard()
        )
        return
    elif text == "📅 Календарь посадок":
        calendar_text = """🌙 **Лунный посевной календарь на 2026 год**
Общие правила:
🌱 Растущая Луна → «вершки» (томаты 🍅, огурцы 🥒, перец 🌶️, капуста 🥬, зелень 🌿, цветы 🌸)
🌿 Убывающая Луна → «корешки» (картофель 🥔, морковь 🥕, свёкла 🍠, лук 🧅, чеснок 🧄)
Самые благоприятные дни (общие, усреднённые):
Январь: 2, 17, 21–22, 26–27, 30
Февраль: 13, 18–19, 20–21, 26–27
Март: 4, 8, 20–21, 26–29
Апрель: 5, 7–8, 11, 28
Май: 20–21, 25, 27–29
Июнь: 9, 21, 23–25
Июль: 7, 9, 25
Август: 4, 6, 18–19, 25, 27
Сентябрь: 1, 12, 15–16, 22
Октябрь: 17, 22, 24, 29
Ноябрь: 3–4, 13, 18, 22
Декабрь: 1, 10–11, 19–20, 28
**Запрещённые дни** (новолуние / полнолуние):
Январь: 3, 18
Февраль: 2, 17
Март: 3, 19
Апрель: 2, 17
Май: 1, 16, 31
Июнь: 15, 30
Июль: 14, 29
Август: 12, 28
Сентябрь: 11, 26
Октябрь: 10, 26
Ноябрь: 8, 24
Декабрь: 8, 23
Выбери культуру ниже или напиши свою:"""
        await update.message.reply_text(
            calendar_text,
            reply_markup=culture_keyboard(),
            parse_mode="Markdown"
        )
        return
    elif any(word in text_lower for word in [
        "томат", "помидор", "перец", "огурец", "морковь", "картофель", "капуста", "лук", "чеснок",
        "клубника", "малина", "баклажан", "кабачок", "арбуз", "цветы", "яблоня", "груша", "вишня"
    ]):
        culture_clean = text.strip().replace("🍅", "").replace("🌶️", "").replace("🥒", "").replace("🥬", "").replace("🥕", "").replace("🍠", "").replace("🥔", "").replace("🧅", "").replace("🧄", "").replace("🍓", "").replace("🍇", "").replace("🌿", "").replace("🍆", "").replace("🍉", "").replace("🌸", "").strip()
        region = user.get("region", "Москва")
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        if not is_premium_active(uid):
            user["gpt_queries"] = user.get("gpt_queries", 0) + 1
            save_data()
        prompt = (
            f"Ты — точный агроном-консультант, специализирующийся исключительно на лунных посевных календарях России/СНГ. "
            f"Регион пользователя: {region}. Год — 2026. "
            f"Дай **самые благоприятные дни** по лунному посевному календарю **именно для культуры '{culture_clean}'** в 2026 году. "
            f"Укажи по месяцам: посев на рассаду, пикировка, высадка в теплицу/открытый грунт. "
            f"Укажи **запрещённые дни** (новолуние, полнолуние). "
            f"Формат: **{culture_clean} в 2026 году**\nЯнварь: ...\nЗапрещённые дни: ...\nКороткий совет."
        )
        answer = ask_yandexgpt(region, prompt)
        if len(answer.strip()) < 80 or "не знаю" in answer.lower():
            answer = f"Для **{culture_clean}** в 2026 году точные даты зависят от сорта и региона. Уточни!"
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    elif any(kw in text_lower for kw in ["лунный", "календарь посадок", "лунный календарь"]):
        answer = (
            "Вот краткий лунный календарь на 2026 год (самые благоприятные дни):\n\n"
            "Январь: 2, 17, 21–22, 26–27\n"
            "Февраль: 13, 18–19, 20–21, 26–27\n"
            "Март: 4, 8, 20–21, 26–29\n\n"
            "Полный календарь и по культурам — по кнопке «📅 Календарь посадок»"
        )
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    elif "что я умею" in text_lower or "умеешь" in text_lower:
        answer = (
            "Я умею:\n"
            "• Показывать погоду на 5 дней 🌦\n"
            "• Анализировать фото растений 📸\n"
            "• Ставить напоминания ⏰\n"
            "• Отвечать на вопросы по саду ❓\n"
            "• Показывать лунный календарь посадок 📅\n"
            "• **Премиум-доступ без лимитов** 💎\n\n"
            "Просто пиши вопрос!"
        )
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    else:
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        if not is_premium_active(uid):
            user["gpt_queries"] = user.get("gpt_queries", 0) + 1
            save_data()
        answer = ask_yandexgpt(user.get("region", "Moscow"), text)
        await update.message.reply_text(answer, reply_markup=main_keyboard())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    user = user_data.setdefault(uid, {})
    data = query.data

    if data == "rem_add":
        user["state"] = STATE_ADD_REM_TEXT
        user.pop("temp_rem_id", None)
        await query.edit_message_text(
            "Напишите текст напоминания:",
            reply_markup=InlineKeyboardMarkup.from_column([
                InlineKeyboardButton("← Отмена", callback_data="rem_cancel")
            ])
        )
        save_data()
    elif data == "rem_list":
        reminders = get_user_reminders(uid)
        if not reminders:
            text = "У вас пока нет напоминаний."
        else:
            lines = ["Ваши напоминания:"]
            tz = pytz.timezone(user.get("timezone", "UTC"))
            for r in sorted(reminders, key=lambda x: x.get("datetime_utc", "9999")):
                try:
                    dt_utc = datetime.fromisoformat(r["datetime_utc"]).replace(tzinfo=pytz.UTC)
                    dt_local = dt_utc.astimezone(tz)
                    status = "✅" if r.get("sent") else "⏳"
                    lines.append(f"{status} #{r['id']} | {dt_local.strftime('%d.%m.%Y %H:%M')} | {r['text'][:40]}{'...' if len(r['text'])>40 else ''}")
                except:
                    lines.append(f"#{r['id']} | (ошибка даты) | {r['text'][:40]}...")
            text = "\n".join(lines)
        markup = InlineKeyboardMarkup.from_column([
            InlineKeyboardButton("← Назад", callback_data="rem_back")
        ])
        await query.edit_message_text(text or "Список пуст", reply_markup=markup)
    # ... остальной код callback_handler без изменений ...

# ─── Фоновые задачи ───
def reminders_checker():
    print("[REMINDER-CHECKER] Фоновая задача запущена")
    while True:
        now_utc = datetime.now(pytz.UTC)
        changed = False
        for uid_str, user in list(user_data.items()):
            reminders = user.get("reminders", [])
            tz_str = user.get("timezone", "UTC")
            tz = pytz.timezone(tz_str)
            for rem in reminders:
                if rem.get("sent"):
                    continue
                try:
                    dt_utc = datetime.fromisoformat(rem["datetime_utc"]).replace(tzinfo=pytz.UTC)
                    if dt_utc <= now_utc:
                        asyncio.run_coroutine_threadsafe(
                            application.bot.send_message(
                                int(uid_str),
                                f"🔔 Напоминание!\n{rem['text']}\n\n(в вашем времени: {dt_utc.astimezone(tz).strftime('%d.%m.%Y %H:%M')})",
                                reply_markup=main_keyboard()
                            ),
                            main_loop
                        )
                        rem["sent"] = True
                        changed = True
                except Exception as e:
                    print(f"[REMINDER-ERROR] uid={uid_str}: {e}")
        if changed:
            save_data()
        time.sleep(60)

# ─── Lifespan ───
@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("Starting Telegram Application...")
    await application.initialize()
    await application.start()
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if domain:
        webhook_url = f"https://{domain}/telegram_webhook"
        try:
            await application.bot.set_webhook(url=webhook_url)
            print(f"Webhook успешно установлен: {webhook_url}")
        except Exception as e:
            print(f"Ошибка установки webhook: {e}")
    threading.Thread(target=reminders_checker, daemon=True).start()
    print("[STARTUP] Запущена проверка напоминаний")
    threading.Thread(target=premium_expiration_checker, daemon=True).start()
    print("Фоновые проверки запущены")

@app.on_event("shutdown")
async def shutdown_event():
    print("Остановка Telegram Application...")
    await application.stop()
    await application.shutdown()
    print("Telegram Application остановлен")

print("Приложение готово к запуску под uvicorn / FastAPI")
