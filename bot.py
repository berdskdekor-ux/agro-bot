print("FORCE REBUILD 3")

import nest_asyncio
nest_asyncio.apply()

import json, datetime, requests, os, asyncio
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import openai

# ====== KEYS ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

DATA_FILE = "data.json"
user_data = {}
reminders = []

# ====== STORAGE ======
def save():
    with open(DATA_FILE, "w") as f:
        json.dump(user_data, f)

def load():
    global user_data
    try:
        with open(DATA_FILE) as f:
            user_data = json.load(f)
    except:
        pass

# ====== LOGIC ======
def is_premium(uid):
    return user_data.get(uid, {}).get("premium", False)

def get_week_weather(city):
    url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    data = requests.get(url).json()
    days = {}
    for item in data["list"]:
        date = item["dt_txt"].split(" ")[0]
        temp = item["main"]["temp"]
        desc = item["weather"][0]["description"]
        days.setdefault(date, []).append((temp, desc))

    text = "🌦 Прогноз на 7 дней:\n\n"
    for d, values in list(days.items())[:7]:
        avg = sum(v[0] for v in values) / len(values)
        text += f"{d}: {values[0][1]}, {round(avg,1)}°C\n"
    return text

async def ask_gpt(region, q):
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":f"Ты опытный агроном. Регион {region}. Пиши пошагово."},
            {"role":"user","content":q}
        ]
    )
    return r.choices[0].message.content

# ====== UI ======
menu = ReplyKeyboardMarkup(
    [["🌦 Погода","📸 Диагностика"],
     ["⏰ Напоминание","💎 Премиум"]],
    resize_keyboard=True
)

# ====== HANDLERS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user_data[uid] = {}
    await update.message.reply_text("Введите ваш регион:")
    save()

async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    d = user_data.setdefault(uid, {})

    if update.message.photo:
        if not is_premium(uid):
            await update.message.reply_text("📸 Диагностика доступна только в Премиум.")
            return
        await update.message.reply_text("🔍 Похоже на дефицит азота. Рекомендуется комплексная подкормка.")
        return

    text = update.message.text

    if "region" not in d:
        d["region"] = text
        await update.message.reply_text("Готово 🌿", reply_markup=menu)
        save()
        return

    if text == "🌦 Погода":
        await update.message.reply_text(get_week_weather(d["region"]))
    elif text == "📸 Диагностика":
        await update.message.reply_text("Пришлите фото растения.")
    elif text == "⏰ Напоминание":
        reminders.append({"user": uid, "time": datetime.datetime.now() + datetime.timedelta(minutes=1)})
        await update.message.reply_text("Напоминание установлено.")
    elif text == "💎 Премиум":
        d["premium"] = True
        await update.message.reply_text("💎 Премиум активирован!")
        save()
    else:
        ans = await ask_gpt(d["region"], text)
        await update.message.reply_text(ans)

# ====== REMINDERS ======
async def reminder_loop():
    while True:
        now = datetime.datetime.now()
        for r in reminders[:]:
            if now >= r["time"]:
                await app.bot.send_message(r["user"], "⏰ Пора заняться растениями 🌱")
                reminders.remove(r)
        await asyncio.sleep(30)

# ====== RUN ======
load()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.ALL, handler))

async def main():
    print("🤖 Бот запущен")
    app.create_task(reminder_loop())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())



