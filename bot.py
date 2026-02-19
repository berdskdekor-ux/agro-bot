# bot.py (или main.py) — полный код под FastAPI / ASGI

import os
import json
import time
import threading
import uuid
from datetime import datetime, timedelta, date
import asyncio

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification

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
                                    chat_id=int(uid_str),
                                    text=expire_msg,
                                    parse_mode="HTML",
                                    reply_markup=main_keyboard()
                                ),
                                asyncio.get_event_loop()
                            )
                    except Exception:
                        # на случай битой даты
                        user["premium"] = False
                        user.pop("premium_until", None)
                        changed = True
        if changed:
            save_data()
            print("Обновлены статусы премиум-доступа")
        time.sleep(300)   # 5 минут
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
                    f"💎 Премиум-доступ активирован до <b>{until.strftime('%d.%m.%Y %H:%M')}</b>\n"
                    "Теперь у тебя:\n"
                    "• безлимитная диагностика растений\n"
                    "• безлимитные запросы к агроному\n"
                    "• безлимитные напоминания\n\n"
                    "Спасибо, что поддерживаешь проект 🌱"
                )
        
        asyncio.run_coroutine_threadsafe(
            application.bot.send_message(
                chat_id=int(uid),
                text=success_msg,
                parse_mode="HTML",
                reply_markup=main_keyboard()
            ),
            asyncio.get_event_loop()
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

# ─── Handlers ─── (все твои обработчики остаются без изменений)

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
            save_data()
            await update.message.reply_text("Значение обновлено ✓", reply_markup=main_keyboard())
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
        plan = data.split("_")[1]  # ← здесь отступ 8 пробелов (или 2 таба), если выше функция с 4
        
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
                    "return_url": "https://agro-bot-uxva.onrender.com/success"  # упрощённый
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
    threading.Thread(target=premium_expiration_checker, daemon=True).start()
    print("Фоновые проверки запущены")

@app.on_event("shutdown")
async def shutdown_event():
    print("Остановка Telegram Application...")
    await application.stop()
    await application.shutdown()
    print("Telegram Application остановлен")

print("Приложение готово к запуску под uvicorn / FastAPI")
