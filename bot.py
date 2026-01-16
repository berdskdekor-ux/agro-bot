import json
import datetime
import requests
import os
import asyncio
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from openai import OpenAI

# ==================== КОНФИГУРАЦИЯ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEATHER_API_KEY]):
    print("ОШИБКА: Не все необходимые переменные окружения установлены!")
    print("Нужны: TELEGRAM_TOKEN, OPENAI_API_KEY, WEATHER_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

DATA_FILE = "data.json"

# Глобальные хранилища
user_data = {}
reminders = []

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
def load_data():
    global user_data
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            user_data = json.load(f)
    except Exception as e:
        print("Ошибка загрузки data.json:", e)


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка сохранения data.json:", e)


# ==================== ПОГОДА ====================
def get_week_weather(city: str) -> str:
    if not WEATHER_API_KEY:
        return "Прогноз погоды временно недоступен (нет ключа API)"

    url = (
        f"https://api.openweathermap.org/data/2.5/forecast"
        f"?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    )

    try:
        resp = requests.get(url, timeout=10).json()
        if resp.get("cod") != "200":
            return f"Ошибка погоды: {resp.get('message', 'неизвестная ошибка')}"

        days = {}
        for item in resp["list"]:
            date = item["dt_txt"].split(" ")[0]
            temp = item["main"]["temp"]
            desc = item["weather"][0]["description"]
            days.setdefault(date, []).append((temp, desc))

        lines = ["🌦 Прогноз на ближайшие дни:\n"]
        for d, values in list(days.items())[:5]:  # разумнее ограничить 5 днями
            avg = sum(v[0] for v in values) / len(values)
            lines.append(f"{d}: {values[0][1].capitalize()}, ≈{round(avg,1)}°C")

        return "\n".join(lines)

    except Exception as e:
        return f"Не удалось получить погоду: {str(e)}"


# ==================== GPT ====================
async def ask_gpt(region: str, question: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"Ты опытный агроном-консультант. Регион выращивания — {region}. "
                    "Отвечай пошагово, понятно, практически. Используй русский язык.",
                },
                {"role": "user", "content": question},
            ],
            temperature=0.75,
            max_tokens=1200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠ Ошибка GPT: {str(e)[:180]}"


# ==================== КЛАВИАТУРА ====================
main_menu = ReplyKeyboardMarkup(
    [["🌦 Погода", "📸 Диагностика"], ["⏰ Напоминание", "💎 Премиум"]],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие...",
)


# ==================== HANDLERS ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user_data[uid] = user_data.get(uid, {})
    await update.message.reply_text("Привет! Введите ваш регион выращивания (например: Подмосковье, Краснодарский край, Беларусь и т.д.)")
    save_data()


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    uid = str(update.effective_user.id)
    user = user_data.setdefault(uid, {})

    # Обработка фото (диагностика)
    if update.message.photo:
        if not user.get("premium", False):
            await update.message.reply_text("📸 Диагностика растений доступна только в Премиум-версии!")
            return
        # Здесь должна быть настоящая обработка фото через vision-модель
        await update.message.reply_text("🔍 Пока заглушка: возможно дефицит азота.\nРекомендую внести комплексное удобрение с преобладанием азота.")
        return

    text = update.message.text.strip()

    # Первый вход — регион
    if "region" not in user:
        user["region"] = text
        await update.message.reply_text("Отлично, регион сохранён! 🌱", reply_markup=main_menu)
        save_data()
        return

    # Команды меню
    if text == "🌦 Погода":
        city = user["region"]
        weather_text = get_week_weather(city)
        await update.message.reply_text(weather_text)

    elif text == "📸 Диагностика":
        if user.get("premium", False):
            await update.message.reply_text("Пришлите фото растения (лучше всего лист крупным планом)")
        else:
            await update.message.reply_text("Эта функция доступна только в Премиум-версии 💎")

    elif text == "⏰ Напоминание":
        remind_time = datetime.datetime.now() + datetime.timedelta(minutes=30)
        reminders.append({"user": uid, "time": remind_time})
        await update.message.reply_text("Напоминание установлено на 30 минут позже 🌿")

    elif text == "💎 Премиум":
        user["premium"] = True
        await update.message.reply_text("💎 Премиум-режим активирован! (демо-режим)")
        save_data()

    else:
        # Обычный вопрос → GPT
        region = user.get("region", "не указан")
        answer = await ask_gpt(region, text)
        await update.message.reply_text(answer)


# ==================== ФОНОВАЯ ЗАДАЧА ====================
async def reminder_checker(application):
    while True:
        try:
            now = datetime.datetime.now()
            to_remove = []

            for r in reminders:
                if now >= r["time"]:
                    try:
                        await application.bot.send_message(
                            r["user"],
                            "⏰ Пора заняться растениями! 🌱\nЧто сегодня в плане?"
                        )
                    except Exception:
                        pass  # пользователь мог заблокировать бота
                    to_remove.append(r)

            for r in to_remove:
                reminders.remove(r)

        except Exception as e:
            print("Ошибка в reminder_checker:", e)

        await asyncio.sleep(30)


# ==================== ЗАПУСК ====================
async def main():
    load_data()

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .get_updates_connect_timeout(10)
        .get_updates_read_timeout(10)
        .get_updates_write_timeout(10)
        .build()
    )

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    # Запускаем проверку напоминаний
    asyncio.create_task(reminder_checker(application))

    print("🤖 Бот успешно стартовал")

    # Запуск polling
    await application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем")
    except Exception as e:
        print("Критическая ошибка при запуске бота:", e)


if __name__ == "__main__":
    print("🤖 Бот запущен")
    app.run_polling()


