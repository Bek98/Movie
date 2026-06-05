import os
from pymongo import MongoClient
import asyncio
import logging
import sqlite3
from os import getenv
from typing import List, Dict, Optional
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, 
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()
TOKEN = str(getenv("BOT_TOKEN"))
ADMIN_ID = int(getenv("ID", 0))
MONGO_URL = os.environ.get("MONGO_URL")
klient = MongoClient(MONGO_URL)
db = klient["movie_bot_baza"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_movie_code = State()
    waiting_for_movie_file = State()
    waiting_for_delete_code = State()
    waiting_for_search_code = State()
    waiting_for_channel_add = State()
    waiting_for_channel_del = State()

class DatabaseManager:
    DB_PATH = 'bot_database.db'
    
    @staticmethod
    def init_db():
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.execute('CREATE TABLE IF NOT EXISTS movies (code TEXT PRIMARY KEY, file_id TEXT, name TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS channels (username TEXT PRIMARY KEY)')
        conn.commit()
        conn.close()

    @staticmethod
    def add_channel(username: str):
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.execute('INSERT OR IGNORE INTO channels (username) VALUES (?)', (username,))
        conn.commit()
        conn.close()

    @staticmethod
    def del_channel(username: str):
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.execute('DELETE FROM channels WHERE username = ?', (username,))
        conn.commit()
        conn.close()

    @staticmethod
    def get_channels() -> List[str]:
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        res = conn.execute('SELECT username FROM channels').fetchall()
        conn.close()
        return [r[0] for r in res]

    @staticmethod
    def add_user(user_id: int):
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def get_user_count() -> int:
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        count = conn.execute('SELECT count(*) FROM users').fetchone()[0]
        conn.close()
        return count

    @staticmethod
    def add_movie(code: str, file_id: str, name: str):
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.execute('REPLACE INTO movies (code, file_id, name) VALUES (?, ?, ?)', (code.upper(), file_id, name))
        conn.commit()
        conn.close()

    @staticmethod
    def delete_movie(code: str) -> bool:
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        curr = conn.execute('DELETE FROM movies WHERE code = ?', (code.upper(),))
        conn.commit()
        count = curr.rowcount
        conn.close()
        return count > 0

    @staticmethod
    def get_movie(code: str) -> Optional[Dict]:
        conn = sqlite3.connect(DatabaseManager.DB_PATH)
        conn.row_factory = sqlite3.Row
        res = conn.execute('SELECT * FROM movies WHERE code = ?', (code.upper(),)).fetchone()
        conn.close()
        return dict(res) if res else None

# ================= KEYBOARDS =================
def get_admin_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Kino qo'shish"), KeyboardButton(text="🗑️ Kinoni o'chirish")],
        [KeyboardButton(text="📢 Kanallarni boshqarish"), KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="🏠 Bosh sahifa")]
    ], resize_keyboard=True)

def get_channel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Kanal qo'shish"), KeyboardButton(text="➖ Kanal o'chirish")],
        [KeyboardButton(text="◀️ Ortga")]
    ], resize_keyboard=True)

def get_user_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔍 Kino izlash")]
    ], resize_keyboard=True)

# ================= SUBSCRIPTION CHECK =================
async def check_subs(user_id: int) -> List[str]:
    if user_id == ADMIN_ID: return []
    missing = []
    channels = DatabaseManager.get_channels()
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ['left', 'kicked']: 
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing

# ================= USER HANDLERS =================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if not message.from_user:
        return 
        
    DatabaseManager.add_user(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        await message.answer("Admin panelga xush kelibsiz!", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Botga xush kelibsiz! Kino izlash uchun pastdagi tugmani bosing.", reply_markup=get_user_keyboard())

@dp.message(F.text == "🔍 Kino izlash")
async def search_start(message: Message, state: FSMContext):
    if not message.from_user: return
    
    # Majburiy obunani tekshirish
    missing = await check_subs(message.from_user.id)
    if missing:
        btns = [[InlineKeyboardButton(text=f"A'zo bo'lish", url=f"https://t.me/{ch.replace('@', '')}")] for ch in missing]
        await message.answer("❌ Kino izlashdan oldin quyidagi kanallarga a'zo bo'lishingiz shart:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
        return

    await state.set_state(AdminStates.waiting_for_search_code)
    await message.answer("Kino kodini kiriting:")

@dp.message(AdminStates.waiting_for_search_code)
async def find_movie(message: Message, state: FSMContext):
    if not message.text or not message.from_user: return
    
    # Kiritish jarayonida ham yana obunani tekshirish (ehtiyot shart)
    missing = await check_subs(message.from_user.id)
    if missing:
        btns = [[InlineKeyboardButton(text=f"A'zo bo'lish", url=f"https://t.me/{ch.replace('@', '')}")] for ch in missing]
        await message.answer("❌ Kechirasiz, siz kanallarga a'zo bo'lmadingiz:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
        return

    movie = DatabaseManager.get_movie(message.text)
    if movie:
        # protect_content=True orqali kinoni boshqaga yuborib bo'lmaydigan (saqlab bo'lmaydigan) qilib qo'yildi
        await message.answer_video(movie['file_id'], caption=movie['name'], protect_content=True)
    else:
        await message.answer("❌ Kino topilmadi.")
    await state.clear()

# ================= ADMIN KANAL BOSHQARUVI =================
@dp.message(F.text == "📢 Kanallarni boshqarish")
async def manage_channels(message: Message):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    channels = DatabaseManager.get_channels()
    ch_list = "\n".join(channels) if channels else "Kanallar qo'shilmagan."
    await message.answer(f"📢 Majburiy obuna kanallari:\n\n{ch_list}", reply_markup=get_channel_keyboard())

@dp.message(F.text == "➕ Kanal qo'shish")
async def add_channel_start(message: Message, state: FSMContext):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_for_channel_add)
    await message.answer("Kanal nomini kiritng (Masalan: @mening_kanalim):")

@dp.message(AdminStates.waiting_for_channel_add)
async def process_channel_add(message: Message, state: FSMContext):
    if not message.text: return
    
    DatabaseManager.add_channel(message.text)
    await message.answer(f"✅ Kanal qo'shildi: {message.text}", reply_markup=get_channel_keyboard())
    await state.clear()

@dp.message(F.text == "➖ Kanal o'chirish")
async def del_channel_start(message: Message, state: FSMContext):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_for_channel_del)
    await message.answer("O'chirmoqchi bo'lgan kanal nomini kiritng (Masalan: @mening_kanalim):")

@dp.message(AdminStates.waiting_for_channel_del)
async def process_channel_del(message: Message, state: FSMContext):
    if not message.text: return
    
    DatabaseManager.del_channel(message.text)
    await message.answer(f"✅ Kanal ro'yxatdan o'chirildi: {message.text}", reply_markup=get_channel_keyboard())
    await state.clear()

@dp.message(F.text == "◀️ Ortga")
async def back_to_admin(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user and message.from_user.id == ADMIN_ID:
        await message.answer("Admin panel", reply_markup=get_admin_keyboard())

# ================= QOLGAN ADMIN HANDLERLARI =================
@dp.message(F.text == "📊 Statistika")
async def show_stats(message: Message):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    count = DatabaseManager.get_user_count()
    await message.answer(f"📊 Botdagi umumiy obunachilar: {count} ta.")

@dp.message(F.text == "➕ Kino qo'shish")
async def add_kino(message: Message, state: FSMContext):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_for_movie_code)
    await message.answer("Kino kodini kiriting:")

@dp.message(AdminStates.waiting_for_movie_code)
async def get_code(message: Message, state: FSMContext):
    if not message.text: return
        
    await state.update_data(code=message.text)
    await state.set_state(AdminStates.waiting_for_movie_file)
    await message.answer("Endi kinoni (video) yuboring:")

@dp.message(AdminStates.waiting_for_movie_file)
async def get_file(message: Message, state: FSMContext):
    data = await state.get_data()
    code = data.get('code')
    
    if not code or not isinstance(code, str) or not message.video:
        return 
        
    DatabaseManager.add_movie(code, message.video.file_id, message.caption or "Kino")
    await message.answer("✅ Kino muvaffaqiyatli qo'shildi!", reply_markup=get_admin_keyboard())
    await state.clear()

@dp.message(F.text == "🗑️ Kinoni o'chirish")
async def delete_kino(message: Message, state: FSMContext):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_for_delete_code)
    await message.answer("O'chirmoqchi bo'lgan kino kodini kiriting:")

@dp.message(AdminStates.waiting_for_delete_code)
async def process_delete(message: Message, state: FSMContext):
    if not message.text: return
        
    if DatabaseManager.delete_movie(message.text):
        await message.answer("✅ Kino o'chirildi!", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Bunday kodli kino topilmadi.")
    await state.clear()

@dp.message(F.text == "🏠 Bosh sahifa")
async def home(message: Message, state: FSMContext):
    await state.clear()
    if not message.from_user: return
        
    if message.from_user.id == ADMIN_ID:
        await message.answer("Bosh sahifa", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Bosh sahifa", reply_markup=get_user_keyboard())

async def main():
    DatabaseManager.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
