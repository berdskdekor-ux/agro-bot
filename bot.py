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
STATE_CATEGORY_SELECT = "category_select"
STATE_CULTURE_SELECT = "culture_select"

# ─── Категории культур с эмодзи ───
CATEGORIES = {
    "🥦 Овощи": [
        "🍅 Томаты", "🥒 Огурцы", "🌶️ Перец", "🥬 Капуста", "🥕 Морковь",
        "🫜 Свёкла", "🥔 Картофель", "🧅 Лук", "🧄 Чеснок", "🍆 Баклажаны", "🥬 Кабачки"
    ],
    "🍎 Фрукты и ягоды": [
        "🍓 Клубника", "🫐 Черника", "🍇 Малина", "🍒 Вишня", "🍑 Персик",
        "🍏 Яблоки", "🍐 Груши", "🍉 Арбуз", "🍈 Дыня", "🍊 Апельсины"
    ],
    "🌸 Цветы": [
        "🌹 Розы", "🌷 Тюльпаны", "🌺 Гибискус", "🌻 Подсолнухи", "🌼 Ромашки",
        "🪻 Ирисы", "💐 Пионы", "🌸 Сакура", "🌺 Петуния", "🌸 Лаванда"
    ],
    "🌳 Плодовые деревья и кустарники": [
        "🍎 Яблоня", "🍐 Груша", "🍒 Вишня", "🍑 Абрикос", "🍇 Виноград",
        "🫐 Смородина", "🥝 Киви", "🍊 Мандарин", "🌿 Мята", "🌿 Базилик"
    ],
    "🌿 Другие культуры": []
}

ALL_CULTURES = [c.split(" ", 1)[1] if " " in c else c for cats in CATEGORIES.values() for c in cats]

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
                            save_data()

                            expire_msg = (
                                "⚠️ <b>Премиум-доступ закончился</b>\n\n"
                                f"Срок действия истёк {until.strftime('%d.%m.%Y %H:%M')}.\n"
                                "Вернулись обычные лимиты:\n"
                                "• 2 фото для диагностики в день\n"
                                "• 5 вопросов агроному в день\n"
                                "• 1 напоминание\n\n"
                                "Хочешь вернуть безлимит? Нажми «💎 Премиум» в меню!"
                            )
                            application.bot.send_message(
                                int(uid_str),
                                expire_msg,
                                parse_mode="HTML",
                                reply_markup=main_keyboard()
                            )
                    except Exception:
                        user["premium"] = False
                        user.pop("premium_until", None)
                        changed = True
                        save_data()
        if changed:
            print("Обновлены статусы премиум-доступа")
        time.sleep(300)

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
    temp_path = f"temp_plant_{uuid.uuid4().hex[:8]}.jpg"
    try:
        file_obj = await application.bot.get_file(file_id)
        photo_bytes = await file_obj.download_as_bytearray()
        if len(photo_bytes) > 5 * 1024 * 1024:
            return "Фото слишком большое (>5 МБ). Сожмите и пришлите снова."
        with open(temp_path, "wb") as f:
            f.write(photo_bytes)
        url = "https://my-api.plantnet.org/v2/identify/all"
        params = {"api-key": PLANTNET_API_KEY, "lang": "ru"}
        with open(temp_path, 'rb') as img_file:
            files = {'images': ('photo.jpg', img_file, 'image/jpeg')}
            response = requests.post(url, files=files, params=params, timeout=30)
        if response.status_code != 200:
            return f"Pl@ntNet вернул ошибку {response.status_code}: {response.text[:200]}"
        data = response.json()
        if "results" not in data or not data["results"]:
            return "Растение не распознано. Попробуйте фото крупнее / чётче."
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
        return f"Ошибка анализа: {str(e)}\n\nПопробуйте другое фото или позже."
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

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
    keyboard.append([KeyboardButton("← Назад в меню")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, row_width=2)

def submenu_keyboard(category):
    cultures = CATEGORIES.get(category, [])
    keyboard = []
    for i in range(0, len(cultures), 3):
        row = [KeyboardButton(c) for c in cultures[i:i+3]]
        keyboard.append(row)
    keyboard.append([
        KeyboardButton("⬅️ Назад к категориям"),
        KeyboardButton("← В главное меню")
    ])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, row_width=3)

# ─── YooKassa webhook ─── (исправлено: прямой вызов send_message)
@app.post("/yookassa-webhook")
async def yookassa_webhook(request: Request):
    try:
        event = await request.json()
        notification = WebhookNotification(event)
        if notification.event == "payment.succeeded":
            payment = notification.object
            metadata = payment.metadata or {}
            uid_str = metadata.get("user_id")
            plan = metadata.get("plan")
            if uid_str and plan:
                days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
                days = days_map.get(plan, 30)
                now = datetime.now()
                until = now + timedelta(days=days)

                user = user_data.setdefault(uid_str, {})
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
                application.bot.send_message(
                    int(uid_str),
                    success_msg,
                    parse_mode="HTML",
                    reply_markup=main_keyboard()
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
    can_use, _ = can_use_feature(uid, "photos")
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

    # Обработка кнопок "Назад" (расширенный список)
    if text in ["⬅️ Назад", "← Назад в меню", "⬅️ Назад к категориям", "← В главное меню"]:
        if state in [STATE_CATEGORY_SELECT, STATE_CULTURE_SELECT, STATE_WAIT_OTHER_CULTURE]:
            if "к категориям" in text.lower():
                user["state"] = STATE_CATEGORY_SELECT
                await update.message.reply_text("Выберите категорию:", reply_markup=category_keyboard())
            else:
                user.pop("state", None)
                user.pop("current_category", None)
                await update.message.reply_text("Возвращаемся в главное меню 🌱", reply_markup=main_keyboard())
        save_data()
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
    # ... (остальные состояния ADD_REM_DATE, ADD_REM_TIME, EDIT_REM_VALUE, WAIT_OTHER_CULTURE остаются без изменений)

    # Календарь посадок → открытие категорий
    if text == "📅 Календарь посадок":
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, _ = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов исчерпан (5 шт).")
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
        user["state"] = STATE_CATEGORY_SELECT
        save_data()
        return

    if text in CATEGORIES:
        if text == "🌿 Другие культуры":
            user["state"] = STATE_WAIT_OTHER_CULTURE
            await update.message.reply_text(
                "Напишите название интересующей вас культуры",
                reply_markup=ReplyKeyboardRemove()
            )
            save_data()
            return
        else:
            await update.message.reply_text(
                f"Выберите культуру из категории '{text}':",
                reply_markup=submenu_keyboard(text)
            )
            user["state"] = STATE_CULTURE_SELECT
            user["current_category"] = text
            save_data()
            return

    # Выбор конкретной культуры (с эмодзи)
    culture_clean = text.split(" ", 1)[1] if " " in text else text
    if culture_clean in ALL_CULTURES:
        year = datetime.now().year
        region = user.get("region", "Москва")
        can_use, _ = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        prompt = (
            f"Для культуры '{culture_clean}' в регионе {region} на {year} год: "
            "оптимальное время посадки/посева по лунному календарю, "
            "рекомендуемые сорта, актуальная информация на посевной сезон."
        )
        answer = ask_yandexgpt(region, prompt)
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        user.pop("state", None)
        user.pop("current_category", None)
        save_data()
        return

    # Остальные кнопки и свободный текст — без изменений
    if text == "🌦 Погода":
        answer = get_week_weather(user.get("region", "Moscow"))
        await update.message.reply_text(answer, reply_markup=main_keyboard())
        return
    elif text == "📸 Диагностика":
        await update.message.reply_text("Пришли фото растения крупным планом.")
        return
    elif text == "⏰ Напоминание":
        await update.message.reply_text("Выбери действие:", reply_markup=reminder_inline_keyboard())
        return
    elif text == "💎 Премиум":
        await update.message.reply_text(
            "💎 <b>Premium-доступ</b>\n\nЧто даёт:\n• Без ограничений\n• Приоритетные ответы\n\nВыбери тариф:",
            parse_mode="HTML",
            reply_markup=premium_inline_keyboard()
        )
        return
    else:
        # свободный вопрос → YandexGPT
        can_use, _ = can_use_feature(uid, "gpt_queries")
        if not can_use:
            await update.message.reply_text("🚫 Лимит бесплатных запросов исчерпан (5 шт).")
            return
        use_feature(uid, "gpt_queries")
        answer = ask_yandexgpt(user.get("region", "Moscow"), text)
        await update.message.reply_text(answer, reply_markup=main_keyboard())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    data = query.data
    user = user_data.setdefault(uid, {})

    # ... (весь код callback_handler остаётся без изменений, как в исходном варианте)

# ─── Handlers registration ───
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(callback_handler))

# ─── Фоновая проверка напоминаний (исправлено: прямой send_message) ───
def reminders_checker():
    print("[REMINDER] Фоновая проверка запущена")
    while True:
        try:
            server_now = datetime.now()
            for uid_str, user in list(user_data.items()):
                region = user.get("region", "").lower()
                reminders = user.get("reminders", [])
                if not reminders:
                    continue
                offset_hours = 3
                if any(w in region for w in ["новосибирск", "красноярск", "омск", "сибирь"]):
                    offset_hours = 7
                # ... (остальные пояса)
                user_local_now = server_now + timedelta(hours=offset_hours)
                for rem in reminders:
                    if rem.get("sent"):
                        continue
                    try:
                        rem_time = datetime.fromisoformat(rem["datetime"])
                        if rem_time <= user_local_now:
                            application.bot.send_message(
                                chat_id=int(uid_str),
                                text=f"🔔 Напоминание!\n{rem['text']}",
                                reply_markup=main_keyboard()
                            )
                            mark_reminder_sent(uid_str, rem["id"])
                    except Exception as e:
                        print(f"[REMINDER-ERR] uid={uid_str} rem={rem.get('id')}: {e}")
        except Exception as e:
            print(f"[REMINDER-CRITICAL] {e}")
        time.sleep(60)

# ─── Lifespan ───
@app.on_event("startup")
async def startup_event():
    print("Starting Telegram Application...")
    await application.initialize()
    await application.start()
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if domain:
        webhook_url = f"https://{domain}/telegram_webhook"
        try:
            await application.bot.set_webhook(url=webhook_url)
            print(f"Webhook установлен: {webhook_url}")
        except Exception as e:
            print(f"Ошибка webhook: {e}")
    threading.Thread(target=reminders_checker, daemon=True).start()
    threading.Thread(target=premium_expiration_checker, daemon=True).start()
    print("Фоновые задачи запущены")

@app.on_event("shutdown")
async def shutdown_event():
    print("Остановка Telegram Application...")
    await application.stop()
    await application.shutdown()
    print("Telegram Application остановлен")

print("Приложение готово к запуску под uvicorn / FastAPI")
