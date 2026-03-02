# bot.py (или main.py) — полный код под FastAPI / ASGI
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
main_loop = asyncio.get_event_loop()
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
            <p><a href="https://t.me/ВашБотНик">Вернуться в бот</a></p>
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
STATE_WAIT_OTHER_CULTURE = "wait_other_culture"
# ─── Категории культур ───
CATEGORIES = {
    "🥦 Овощи": ["🍅 Томаты", "🥒 Огурцы", "🌶 Перец", "🥬 Капуста", "🥕 Морковь", "🫜 Свёкла", "🥔 Картофель", "🧅 Лук", "🧄 Чеснок", "🍆 Баклажаны", "🥬 Кабачки"],
    "🍎 Фрукты": ["🍓 Клубника", "🍇 Малина", "🍉 Арбуз", "🍈 Дыня", "🍏 Яблоки", "🍐 Груши", "🍒 Вишня"],
    "🌸 Цветы": ["🌺 Петуния", "🌼 Бархатцы", "🌹 Розы", "🌷 Лилии", "🌻 Астры"],
    "🌳 Кустарники, плодовые деревья": ["🍇 Смородина", "🥝 Крыжовник", "🍇 Малина", "🍇 Виноград", "🍎 Яблоня", "🍐 Груша"],
    "🌿 Другие культуры": []
}
ALL_CULTURES = [c for cats in CATEGORIES.values() for c in cats]
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
                            save_data()  # Сохраняем после каждого изменения
                           
                            # ─── Улучшенное уведомление об окончании ───
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
                        # на случай битой даты
                        user["premium"] = False
                        user.pop("premium_until", None)
                        changed = True
                        save_data()
        if changed:
            print("Обновлены статусы премиум-доступа")
        time.sleep(300) # 5 минут
# ─── YandexGPT ───
def ask_yandexgpt(region, question):
    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.4, "maxTokens": 1200},
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
    """
    Анализирует фотографию растения через PlantNet + YandexGPT.
    Возвращает текстовый результат или сообщение об ошибке.
    """
    temp_path = f"temp_plant_{uuid.uuid4().hex[:8]}.jpg"
    try:
        print(f"[PLANTNET] Начинаем обработку фото, file_id={file_id}, region={region}")
        # 1. Получаем объект File из Telegram
        file_obj = await application.bot.get_file(file_id)
        print(f"[PLANTNET] Получен File объект, file_path={file_obj.file_path}")
        # 2. Скачиваем фото в память (bytearray)
        photo_bytes = await file_obj.download_as_bytearray()
        print(f"[PLANTNET] Фото скачано, размер: {len(photo_bytes)} байт")
        # Проверка размера фото
        if len(photo_bytes) > 5 * 1024 * 1024:
            return "Фото слишком большое (>5 МБ). Сожмите и пришлите снова."
        # 3. Сохраняем на диск для отправки в PlantNet
        with open(temp_path, "wb") as f:
            f.write(photo_bytes)
        print(f"[PLANTNET] Фото сохранено во временный файл: {temp_path}")
        # 4. Отправляем в PlantNet API
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
        # Запрос к YandexGPT с информацией о растении
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
        # Удаляем временный файл в любом случае
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print(f"[PLANTNET] Временный файл удалён: {temp_path}")
            except Exception as cleanup_e:
                print(f"[PLANTNET-CLEANUP] Не удалось удалить {temp_path}: {cleanup_e}")
# ─── Напоминания ───
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
def category_keyboard():
    cats = list(CATEGORIES.keys())
    keyboard = []
    for i in range(0, len(cats), 2):
        row = [KeyboardButton(c) for c in cats[i:i+2]]
        keyboard.append(row)
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
def submenu_keyboard(category):
    cultures = CATEGORIES.get(category, [])
    keyboard = []
    for i in range(0, len(cultures), 3):
        row = [KeyboardButton(c) for c in cultures[i:i+3]]
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
            if not uid:
                return PlainTextResponse("", status_code=200)
            uid = int(uid)
            plan = metadata.get("plan")
            if uid and plan:
                days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
                days = days_map.get(plan, 30)
                now = datetime.now()
                until = now + timedelta(days=days)
               
                user = user_data.setdefault(str(uid), {})
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
                uid,
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
        user["region"] = region
        user.pop("state", None)
        save_data()
        await update.message.reply_text(
            f"Отлично! Запомнил: **{region}** 🌍\nТеперь рекомендации будут учитывать ваш климат.\n\nЧто хотите сделать?",
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
            text_clean = text.replace(" ", "").strip()
            parts = text_clean.split(".")
            if len(parts) < 3:
                raise ValueError("Мало частей")
            d = int(parts[0])
            m = int(parts[1])
            y = int(parts[2])
            dt_date = datetime(y, m, d)
            if dt_date < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
                await update.message.reply_text("Дата должна быть в будущем.")
                return
            user["temp_rem_date"] = dt_date
            user["state"] = STATE_ADD_REM_TIME
            await update.message.reply_text("Укажите время: чч:мм\nПример: 14:30")
            save_data()
        except Exception as e:
            print(f"[DATE-PARSE-ERROR] Ввод: {text!r} → {type(e).__name__}: {e}")
            await update.message.reply_text("Неверный формат даты. Ожидается: 15.03.2026\nПопробуйте ещё раз.")
        return
    elif state == STATE_ADD_REM_TIME:
        try:
            h, mm = map(int, text.replace(" ", "").split(":"))
            dt = user["temp_rem_date"].replace(hour=h, minute=mm)
            if dt < datetime.now():
                await update.message.reply_text("Дата+время должны быть в будущем.")
                return
            save_reminder(uid, user["temp_rem_text"], dt.isoformat())
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
            await update.message.reply_text(
                f"Напоминание создано на\n{dt.strftime('%d.%m.%Y %H:%M')}\n\n{text}",
                reply_markup=main_keyboard()
            )
        except Exception as e:
            print(f"[TIME-PARSE-ERROR] Ввод: {text!r} → {type(e).__name__}: {e}")
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
        dt = datetime.fromisoformat(reminder["datetime"])
        try:
            if field == "text":
                reminder["text"] = text.strip()
            elif field == "date":
                d, m, y = map(int, text.replace(" ", "").split("."))
                new_dt = datetime(y, m, d, dt.hour, dt.minute)
                if new_dt < datetime.now():
                    await update.message.reply_text("Дата должна быть в будущем.")
                    return
                reminder["datetime"] = new_dt.isoformat()
            elif field == "time":
                h, mm = map(int, text.replace(" ", "").split(":"))
                new_dt = dt.replace(hour=h, minute=mm)
                if new_dt < datetime.now():
                    await update.message.reply_text("Время должно быть в будущем.")
                    return
                reminder["datetime"] = new_dt.isoformat()
            # Сбрасываем статус отправки при изменении даты/времени
            if field in ("date", "time"):
                reminder["sent"] = False
            save_data()
            await update.message.reply_text("Значение обновлено ✓", reply_markup=main_keyboard())
        except Exception as e:
            print(f"[EDIT-ERROR] uid={uid}, rem_id={rem_id}, field={field}: {type(e).__name__}: {e}")
            await update.message.reply_text(f"Ошибка формата: {str(e)}")
        finally:
            user.pop("state", None)
            user.pop("temp_rem_id", None)
            user.pop("edit_field", None)
            save_data()
        return
    elif state == STATE_WAIT_OTHER_CULTURE:
        culture = text.strip()
        if not culture:
            await update.message.reply_text("Название культуры не может быть пустым.")
            return
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        prompt = (
            f"Для культуры '{culture}' в регионе {region} на {year} год: "
            "оптимальное время посадки/посева по лунному календарю, "
            "рекомендуемые сорта, актуальная информация на посевной сезон. "
            "Основывайся на свежих данных из интернета."
        )
        answer = ask_yandexgpt(region, prompt)
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        user.pop("state", None)
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
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        prompt = (
            f"Дай общий лунный посевной календарь на {year} год для России/СНГ, "
            "с благоприятными днями по месяцам для вершков и корешков, "
            "запрещёнными днями (новолуние, полнолуние). "
            "Формат: **Месяц**: Благоприятные для вершков: ..., для корешков: ..., Запрещённые: ..."
        )
        calendar_text = ask_yandexgpt(region, prompt)
        await update.message.reply_text(
            calendar_text + "\n\nВыберите категорию культуры:",
            reply_markup=category_keyboard(),
            parse_mode="Markdown"
        )
        return
    elif text in CATEGORIES:
        if text == "Другие культуры":
            user["state"] = STATE_WAIT_OTHER_CULTURE
            await update.message.reply_text(
                "Напишите название интересующей вас культуры и я постараюсь найти о ней информацию",
                reply_markup=ReplyKeyboardRemove()
            )
            save_data()
            return
        else:
            await update.message.reply_text(
                f"Выберите культуру из категории '{text}':",
                reply_markup=submenu_keyboard(text)
            )
            return
    elif text in ALL_CULTURES:
        culture = text
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        prompt = (
            f"Для культуры '{culture}' в регионе {region} на {year} год: "
            "оптимальное время посадки/посева по лунному календарю, "
            "рекомендуемые сорта, актуальная информация на посевной сезон. "
            "Основывайся на свежих данных из интернета."
        )
        answer = ask_yandexgpt(region, prompt)
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    elif any(kw in text_lower for kw in ["лунный", "календарь посадок", "лунный календарь"]):
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, remaining = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов к агроному исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        prompt = (
            f"Краткий лунный календарь посадок на {year} год для России/СНГ: "
            "самые благоприятные дни по месяцам, запрещённые дни."
        )
        answer = ask_yandexgpt(region, prompt)
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
        use_feature(uid, "gpt_queries")
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
            for r in sorted(reminders, key=lambda x: x.get("datetime", "9999-99-99T99:99:99")):
                try:
                    dt = datetime.fromisoformat(r["datetime"])
                    status = "✅" if r.get("sent") else "⏳"
                    lines.append(f"{status} #{r['id']} | {dt.strftime('%d.%m.%Y %H:%M')} | {r['text'][:40]}{'...' if len(r['text'])>40 else ''}")
                except:
                    lines.append(f"#{r['id']} | (ошибка даты) | {r['text'][:40]}...")
            text = "\n".join(lines)
        markup = InlineKeyboardMarkup.from_column([
            InlineKeyboardButton("← Назад", callback_data="rem_back")
        ])
        await query.edit_message_text(text or "Список пуст", reply_markup=markup)
    elif data == "rem_edit_menu":
        reminders = get_user_reminders(uid)
        if not reminders:
            await query.answer("Нет напоминаний для редактирования", show_alert=True)
            return
        keyboard = []
        for r in sorted(reminders, key=lambda x: x.get("datetime", "9999")):
            try:
                dt = datetime.fromisoformat(r["datetime"])
                btn_text = f"#{r['id']} | {dt.strftime('%d.%m %H:%M')} | {r['text'][:25]}{'...' if len(r['text'])>25 else ''}"
            except:
                btn_text = f"#{r['id']} | (ошибка даты) | {r['text'][:25]}..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"edit_rem_{r['id']}")])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data="rem_back")])
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите напоминание:", reply_markup=markup)
    elif data.startswith("edit_rem_") and not data.startswith(("edit_text_", "edit_date_", "edit_time_")):
        try:
            rem_id = int(data.split("_")[-1])
        except:
            await query.answer("Некорректный ID", show_alert=True)
            return
        reminder = next((r for r in get_user_reminders(uid) if r["id"] == rem_id), None)
        if not reminder:
            await query.answer("Напоминание не найдено", show_alert=True)
            return
        user["temp_rem_id"] = rem_id
        user["state"] = STATE_EDIT_REM_CHOOSE
        try:
            dt = datetime.fromisoformat(reminder["datetime"])
            dt_str = dt.strftime('%d.%m.%Y %H:%M')
        except:
            dt_str = "(ошибка формата даты)"
        text = (
            f"Напоминание #{rem_id}\n"
            f"Текст: {reminder['text']}\n"
            f"Дата и время: {dt_str}\n\n"
            "Что хотите изменить?"
        )
        await query.edit_message_text(text, reply_markup=edit_reminder_actions_markup(rem_id))
    elif data.startswith(("edit_text_", "edit_date_", "edit_time_")):
        parts = data.split("_")
        field = parts[1]
        try:
            rem_id = int(parts[2])
        except:
            await query.answer("Ошибка", show_alert=True)
            return
        user["temp_rem_id"] = rem_id
        user["edit_field"] = field
        prompts = {
            "text": "Введите новый текст напоминания:",
            "date": "Введите новую дату (дд.мм.гггг):",
            "time": "Введите новое время (чч:мм):"
        }
        await query.edit_message_text(
            prompts.get(field, "Ошибка поля"),
            reply_markup=InlineKeyboardMarkup.from_column([
                InlineKeyboardButton("← Отмена", callback_data="rem_cancel_edit")
            ])
        )
        user["state"] = STATE_EDIT_REM_VALUE
        save_data()
    elif data.startswith("del_rem_"):
        try:
            rem_id = int(data.split("_")[-1])
        except:
            await query.answer("Некорректный ID", show_alert=True)
            return
        if delete_reminder(uid, rem_id):
            await query.answer("Напоминание удалено ✓", show_alert=True)
            reminders = get_user_reminders(uid)
            if not reminders:
                text = "У вас пока нет напоминаний."
            else:
                lines = ["Ваши напоминания:"]
                for r in sorted(reminders, key=lambda x: x.get("datetime", "9999-99-99T99:99:99")):
                    try:
                        dt = datetime.fromisoformat(r["datetime"])
                        status = "✅" if r.get("sent") else "⏳"
                        lines.append(f"{status} #{r['id']} | {dt.strftime('%d.%m.%Y %H:%M')} | {r['text'][:40]}{'...' if len(r['text'])>40 else ''}")
                    except:
                        lines.append(f"#{r['id']} | (ошибка даты) | {r['text'][:40]}...")
                text = "\n".join(lines)
            markup = InlineKeyboardMarkup.from_column([
                InlineKeyboardButton("← Назад", callback_data="rem_back")
            ])
            await query.edit_message_text(text or "Список пуст", reply_markup=markup)
        else:
            await query.answer("Не удалось удалить", show_alert=True)
    elif data in ("rem_cancel", "rem_cancel_edit", "rem_back"):
        for key in ["state", "temp_rem_id", "edit_field", "temp_rem_text", "temp_rem_date"]:
            user.pop(key, None)
        save_data()
        await query.edit_message_text(
            "Меню напоминаний",
            reply_markup=reminder_inline_keyboard()
        )
    elif data.startswith("premium_"):
        plan = data.split("_")[1]
       
        # ДЕБАГ
        print(f"[DEBUG-PREMIUM] Нажат тариф '{plan}' пользователем {uid}")
        await query.answer(f"[ТЕСТ] Пытаемся создать платёж для {plan}...", show_alert=True)
       
        plans = {
            "day": {"amount": "10.00", "desc": "Премиум на 1 день"},
            "week": {"amount": "50.00", "desc": "Премиум на 7 дней"},
            "month": {"amount": "150.00", "desc": "Премиум на 30 дней"},
            "year": {"amount": "1500.00", "desc": "Премиум на 365 дней"},
        }
       
        if plan not in plans:
            print(f"[DEBUG-PREMIUM] Неизвестный план: {plan}")
            await query.answer("Неизвестный тариф", show_alert=True)
            return
       
        p = plans[plan]
       
        try:
            print(f"[DEBUG-PREMIUM] Создаём платёж: {p['amount']} RUB, описание: {p['desc']}")
           
            idempotency_key = str(uuid.uuid4())
            payment = Payment.create({
                "amount": {
                    "value": p["amount"],
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://agro-bot-uxva.onrender.com/success" # упрощённый
                },
                "capture": True,
                "description": p["desc"],
                "metadata": {
                    "user_id": uid,
                    "plan": plan
                }
            }, idempotency_key)
           
            payment_url = payment.confirmation.confirmation_url
            print(f"[DEBUG-PREMIUM] Ссылка получена: {payment_url}")
           
            await query.message.reply_text(
                f"Для активации премиум перейдите по ссылке:\n\n"
                f"{payment_url}\n\n"
                f"После успешной оплаты премиум активируется автоматически."
            )
            await query.answer("Ссылка на оплату создана")
        except Exception as e:
            print(f"[ERROR-PREMIUM] Ошибка при создании платежа: {str(e)}")
            import traceback
            print(traceback.format_exc())
            await query.answer(f"Ошибка создания платежа: {str(e)}", show_alert=True)
# ─── Добавляем handlers ───
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(callback_handler))
# ─── Фоновые задачи ───
def reminders_checker():
    print("[НАПОМИНАНИЕ-ПРОВЕРКА] Фоновая задача запущена")
    while True:
        try:
            server_now = datetime.now()
            print(f"[НАПОМИНАНИЕ-ПРОВЕРКА] Проверка времени сервера: {server_now.isoformat()}")
            changed = False
            for uid_str, user in list(user_data.items()):
                region = user.get("region", "").lower()
                reminders = user.get("reminders", [])
                if not reminders:
                    continue
                # Простое определение смещения (в часах) относительно UTC
                offset_hours = 3 # по умолчанию Москва / европейская часть
                if any(word in region for word in ["новосибирск", "красноярск", "омск", "+7", "сибирь"]):
                    offset_hours = 7
                elif any(word in region for word in ["владивосток", "хабаровск", "+10"]):
                    offset_hours = 10
                elif any(word in region for word in ["екатеринбург", "самара", "+5", "урал"]):
                    offset_hours = 5
                elif any(word in region for word in ["калининград", "+2"]):
                    offset_hours = 2
                # можно добавить ещё 2–3 популярных пояса по необходимости
                user_local_now = server_now + timedelta(hours=offset_hours)
                print(f"[НАПОМИНАНИЕ-ПРОВЕРКА] uid={uid_str}, регион='{region}', локальное время ~ {user_local_now.isoformat()}")
                for rem in reminders:
                    if rem.get("sent"):
                        continue
                    try:
                        rem_time = datetime.fromisoformat(rem["datetime"])
                        print(f"[НАПОМИНАНИЕ-ПРОВЕРКА] Проверяем напоминание {rem['id']}: {rem_time.isoformat()}")
                        if rem_time <= user_local_now:
                            print(f"[НАПОМИНАНИЕ-ПРОВЕРКА] Время пришло для uid={uid_str}! Отправляем: {rem['text']}")
                            asyncio.run_coroutine_threadsafe(
                                application.bot.send_message(
                                    chat_id=int(uid_str),
                                    text=f"🔔 Напоминание!\n{rem['text']}",
                                    reply_markup=main_keyboard()
                                ),
                                main_loop
                            ).result(timeout=8)
                            mark_reminder_sent(uid_str, rem["id"])
                            changed = True
                    except Exception as e:
                        print(f"[НАПОМИНАНИЕ-ПРОВЕРКА-ОШИБКА] uid={uid_str}, rem_id={rem.get('id')}: {type(e).__name__}: {e}")
            if changed:
                save_data()
                print("[НАПОМИНАНИЕ-ПРОВЕРКА] Данные сохранены после отправки")
        except Exception as outer_e:
            print(f"[НАПОМИНАНИЕ-ПРОВЕРКА-КРИТИЧЕСКАЯ] {outer_e}")
        time.sleep(60)
# ─── Lifespan (startup / shutdown) ───
@app.on_event("startup")
async def startup_event():
    print("Starting Telegram Application...")
    await application.initialize()
    await application.start()
    # Установка webhook автоматически
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if domain:
        webhook_url = f"https://{domain}/telegram_webhook"
        try:
            await application.bot.set_webhook(url=webhook_url)
            print(f"Webhook успешно установлен: {webhook_url}")
        except Exception as e:
            print(f"Ошибка установки webhook: {e}")
    else:
        print("RENDER_EXTERNAL_HOSTNAME не найден — webhook не установлен автоматически")
    # Запуск фоновых задач
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
