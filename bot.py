print("FORCE REBUILD 1")
import nest_asyncio
nest_asyncio.apply()

import json, datetime, time, threading, requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import openai

# === КЛЮЧИ ===
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

DATA_FILE="data.json"
user_data={}
reminders=[]

def save():
    with open(DATA_FILE,"w") as f:
        json.dump(user_data,f)

def load():
    global user_data
    try:
        with open(DATA_FILE) as f:
            user_data=json.load(f)
    except: pass

def is_premium(uid):
    return user_data.get(uid,{}).get("premium",False)

def calendar_text():
    return ["","Планирование","Рассада","Посев","Грядки","Высадка","Рост","Защита","Урожай","Уборка","Подготовка","Укрытие","Отдых"][datetime.datetime.now().month]

def get_weather(city):
    r=requests.get(f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru").json()
    return f"{r['weather'][0]['description']}, {r['main']['temp']}°C"

async def ask_gpt(d,q):
    r=client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":f"Ты опытный агроном. Регион {d['region']}, почва {d['soil']}. Пиши пошагово."},
                  {"role":"user","content":q}]
    )
    return r.choices[0].message.content

menu = ReplyKeyboardMarkup(
    [["🌦 Погода","🗓 Календарь"],
     ["🧩 План участка","📸 Диагностика"],
     ["⏰ Напоминание","💎 Премиум"]], resize_keyboard=True)

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=str(update.effective_user.id)
    user_data[uid]={}
    await update.message.reply_text("Ваш регион?")
    save()

async def handler(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=str(update.effective_user.id)
    d=user_data.setdefault(uid,{})
    
    if update.message.photo:
        if not is_premium(uid):
            await update.message.reply_text("📸 Диагностика доступна только в Премиум.")
            return
        await update.message.reply_text("🔍 Анализирую растение...")
        await update.message.reply_text("По фото похоже на дефицит азота. Рекомендую подкормку комплексным удобрением.")
        return

    text=update.message.text

    if "region" not in d:
        d["region"]=text
        await update.message.reply_text("Тип почвы?")
    elif "soil" not in d:
        d["soil"]=text
        await update.message.reply_text("Введите размер участка:")
    elif "size" not in d:
        d["size"]=text
        await update.message.reply_text("Готово 🌿",reply_markup=menu)
        save()
    else:
        if text=="🌦 Погода":
            await update.message.reply_text(get_weather(d["region"]))
        elif text=="🗓 Календарь":
            await update.message.reply_text(calendar_text())
        elif text=="🧩 План участка":
            if not is_premium(uid):
                await update.message.reply_text("🧩 Доступно в Премиум.")
                return
            plan=await ask_gpt(d,"Составь подробную схему участка с зонами.")
            await update.message.reply_text(plan)
        elif text=="📸 Диагностика":
            await update.message.reply_text("Пришлите фото растения.")
        elif text=="⏰ Напоминание":
            reminders.append({"user":uid,"time":datetime.datetime.now()+datetime.timedelta(minutes=1)})
            await update.message.reply_text("Напоминание установлено.")
        elif text=="💎 Премиум":
            d["premium"]=True
            await update.message.reply_text("💎 Премиум активирован!")
            save()
        else:
            ans=await ask_gpt(d,text)
            await update.message.reply_text(ans)

def reminder_loop(app):
    while True:
        now=datetime.datetime.now()
        for r in reminders[:]:
            if now>=r["time"]:
                app.bot.send_message(r["user"],"⏰ Пора заняться растениями 🌱")
                reminders.remove(r)
        time.sleep(30)

load()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(MessageHandler(filters.ALL,handler))

threading.Thread(target=reminder_loop,args=(app,),daemon=True).start()

import asyncio

async def runner():
    await app.initialize()
    await app.start()
    print("🤖 Бот запущен и работает")
    await asyncio.Event().wait()

asyncio.get_event_loop().create_task(runner())





