import io
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import google.generativeai as genai
from PIL import Image
from fastapi import FastAPI, Request

import config
from system_prompts import TUTOR_SYSTEM_PROMPT

logging.basicConfig(level=logging.INFO)

genai.configure(api_key=config.GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name=config.MODEL_NAME,
    system_instruction=TUTOR_SYSTEM_PROMPT
)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

user_history = defaultdict(list)

class TutorState(StatesGroup):
    choosing_subject = State()
    choosing_grade = State()
    chatting = State()

@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.WEBHOOK_URL and config.WEBHOOK_URL != "https://temp.com":
        webhook_uri = f"{config.WEBHOOK_URL}{config.WEBHOOK_PATH}"
        await bot.set_webhook(webhook_uri)
        logging.info(f"Webhook o'rnatildi: {webhook_uri}")
    yield
    if config.WEBHOOK_URL and config.WEBHOOK_URL != "https://temp.com":
        await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_history[user_id] = []
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📐 Matematika"), KeyboardButton(text="⚡ Fizika")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"Assalomu alaykum, {message.from_user.first_name}!\n\n"
        "Men **OʻzbekTutor** — matematika va fizika boʻyicha AI repetitoringizman.\n"
        "Masalani tayyor yechib bermayman, uni **qanday yechishni birga oʻrganamiz**.\n\n"
        "Qaysi fandan shugʻullanamiz?",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await state.set_state(TutorState.choosing_subject)

@dp.message(TutorState.choosing_subject, F.text.in_(["📐 Matematika", "⚡ Fizika"]))
async def subject_chosen(message: types.Message, state: FSMContext):
    await state.update_data(subject=message.text)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="5-6 sinf"), KeyboardButton(text="7-9 sinf")],
            [KeyboardButton(text="10-11 sinf")]
        ],
        resize_keyboard=True
    )
    
    await message.answer("Sinfingizni tanlang:", reply_markup=kb)
    await state.set_state(TutorState.choosing_grade)

@dp.message(TutorState.choosing_grade)
async def grade_chosen(message: types.Message, state: FSMContext):
    data = await state.get_data()
    subject = data.get("subject", "Fan")
    grade = message.text
    
    await state.set_state(TutorState.chatting)
    
    await message.answer(
        f"Ajoyib! **{subject}** ({grade}) tanlandi.\n\n"
        "Yechilishi kerak bo'lgan masalani **matn** ko'rinishida yozing yoki darslikdagi **rasmini** yuboring:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

@dp.message(TutorState.chatting, F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_bytes = await bot.download_file(file_info.file_path)
    
    pil_image = Image.open(io.BytesIO(photo_bytes.read()))
    caption = message.caption if message.caption else "Ushbu rasmdagi masalani yechishda yordam bering."

    try:
        chat = gemini_model.start_chat(history=user_history[user_id])
        response = chat.send_message([caption, pil_image])
        
        reply_text = response.text
        
        user_history[user_id].append({"role": "user", "parts": [caption]})
        user_history[user_id].append({"role": "model", "parts": [reply_text]})
        
        await message.answer(reply_text)
        
    except Exception as e:
        logging.error(f"Gemini API Error (Rasm): {e}")
        await message.answer("Rasmni oʻqishda xatolik yuz berdi. Iltimos, rasmni aniqroq olib qayta yuboring.")

@dp.message(TutorState.chatting, F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        chat = gemini_model.start_chat(history=user_history[user_id])
        response = chat.send_message(message.text)
        
        reply_text = response.text
        
        user_history[user_id].append({"role": "user", "parts": [message.text]})
        user_history[user_id].append({"role": "model", "parts": [reply_text]})
        
        await message.answer(reply_text)
        
    except Exception as e:
        logging.error(f"Gemini API Error (Matn): {e}")
        await message.answer("Xatolik yuz berdi. Qaytadan urinib ko'ring yoki /start bosing.")

@app.post(config.WEBHOOK_PATH)
async def bot_webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "O'zbekTutor Bot running!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)