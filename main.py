import os
import sqlite3
import logging
import time
import pandas as pd
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# --- SOZLAMALAR ---
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not API_TOKEN:
    raise ValueError("❌ API_TOKEN topilmadi! Railway Variables ni tekshir")

if not ADMIN_ID_RAW:
    raise ValueError("❌ ADMIN_ID topilmadi! Railway Variables ni tekshir")

ADMIN_ID = int(ADMIN_ID_RAW.strip())

current_dir = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(current_dir, "student_files")
DB_NAME = os.path.join(current_dir, "education_bot.db")

# Papka borligini qat'iy tekshirish
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- BAZA BILAN ISHLASH ---
def execute_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    try:
        with sqlite3.connect(DB_NAME, timeout=20) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            if commit:
                conn.commit()
            if fetchone:
                return cur.fetchone()
            if fetchall:
                return cur.fetchall()
            return None
    except Exception as e:
        logging.error(f"Baza bilan ishlashda xato: {e}")
        return None

def init_db():
    execute_query('''CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY, name TEXT)''', commit=True)
    execute_query('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT, description TEXT, file_id TEXT)''', commit=True)
    execute_query('''CREATE TABLE IF NOT EXISTS task_assignments (task_id INTEGER, group_id INTEGER)''', commit=True)
    execute_query('''CREATE TABLE IF NOT EXISTS students (user_id INTEGER PRIMARY KEY, full_name TEXT, group_id INTEGER)''', commit=True)
    execute_query('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY, user_id INTEGER, task_id INTEGER, 
        file_path TEXT, grade TEXT, status TEXT, submitted_at TIMESTAMP
    )''', commit=True)
    
    count = execute_query("SELECT COUNT(*) FROM tasks", fetchone=True)
    if count and count[0] == 0:
        for i in range(1, 16):
            execute_query("INSERT INTO tasks (id, title, description) VALUES (?, ?, ?)", 
                         (i, f"{i}-topshiriq", "Hozircha ma'lumot yo'q."), commit=True)

init_db()

# --- HOLATLAR ---
class Form(StatesGroup):
    register = State()
    add_group = State()
    edit_task_id = State()
    edit_task_content = State()
    assign_task_group = State()
    give_grade = State()
    waiting_for_sub = State()

# --- MENYULAR ---
def get_main_menu(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if uid == ADMIN_ID:
        kb.add("📝 Topshiriqlarni tahrirlash", "🔗 Guruhga biriktirish")
        kb.add("📥 Ishlarni tekshirish", "📊 Hisobot (.xlsx)")
        kb.add("➕ Guruh yaratish", "🗑 Talaba/Guruhni o'chirish")
    else:
        kb.add("📚 Mavjud topshiriqlar", "🏆 Mening ballarim")
    return kb

# --- START ---
@dp.message_handler(commands=['start'], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    uid = message.from_user.id
    user = execute_query("SELECT * FROM students WHERE user_id=?", (uid,), fetchone=True)
    
    if uid == ADMIN_ID:
        await message.answer("Xush kelibsiz, Ustoz Mo'minbek!", reply_markup=get_main_menu(uid))
    elif user:
        await message.answer(f"Salom, {user[1]}! O'qishlaringizga omad.", reply_markup=get_main_menu(uid))
    else:
        await message.answer("Assalomu alaykum! Botdan foydalanish uchun Ism familiyangizni kiriting:")
        await Form.register.set()

# --- RO'YXATDAN O'TISH ---
@dp.message_handler(state=Form.register)
async def process_reg(message: types.Message, state: FSMContext):
    name = message.text
    groups = execute_query("SELECT * FROM groups", fetchall=True)
    if not groups:
        await message.answer("Hozircha hech qanday guruh ochilmagan.")
        await state.finish()
        return
    kb = types.InlineKeyboardMarkup()
    for gid, gname in groups:
        kb.add(types.InlineKeyboardButton(gname, callback_data=f"reg_{gid}_{name}"))
    await message.answer(f"Janob {name}, guruhingizni tanlang:", reply_markup=kb)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('reg_'))
async def finalize_reg(callback: types.CallbackQuery):
    _, gid, name = callback.data.split('_')
    execute_query("INSERT OR REPLACE INTO students VALUES (?, ?, ?)", (callback.from_user.id, name, gid), commit=True)
    await callback.message.answer("Siz muvaffaqiyatli ro'yxatdan o'tdingiz!", reply_markup=get_main_menu(callback.from_user.id))

# --- ADMIN FUNKSIYALARI ---
@dp.message_handler(lambda m: m.text == "➕ Guruh yaratish" and m.from_user.id == ADMIN_ID)
async def add_g(message: types.Message):
    await message.answer("Yangi guruh nomini kiriting:")
    await Form.add_group.set()

@dp.message_handler(state=Form.add_group)
async def finish_g(message: types.Message, state: FSMContext):
    execute_query("INSERT INTO groups (name) VALUES (?)", (message.text,), commit=True)
    await message.answer(f"'{message.text}' guruhi yaratildi!", reply_markup=get_main_menu(ADMIN_ID))
    await state.finish()

@dp.message_handler(lambda m: m.text == "📝 Topshiriqlarni tahrirlash" and m.from_user.id == ADMIN_ID)
async def edit_t_start(message: types.Message):
    await message.answer("Qaysi raqamli topshiriqni tahrirlaysiz? (1-15):")
    await Form.edit_task_id.set()

@dp.message_handler(state=Form.edit_task_id)
async def edit_t_step2(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Iltimos, faqat raqam kiriting.")
        return
    await state.update_data(tid=message.text)
    await message.answer(f"{message.text}-topshiriq matnini yuboring yoki fayl biriktiring:")
    await Form.edit_task_content.set()

@dp.message_handler(state=Form.edit_task_content, content_types=['any'])
async def edit_t_finish(message: types.Message, state: FSMContext):
    data = await state.get_data(); tid = data['tid']
    desc = message.text or message.caption or "Vazifa fayli"
    fid = message.document.file_id if message.document else None
    execute_query("UPDATE tasks SET description=?, file_id=? WHERE id=?", (desc, fid, tid), commit=True)
    await message.answer(f"✅ {tid}-topshiriq yangilandi!", reply_markup=get_main_menu(ADMIN_ID))
    await state.finish()

@dp.message_handler(lambda m: m.text == "🔗 Guruhga biriktirish" and m.from_user.id == ADMIN_ID)
async def assign_t(message: types.Message):
    await message.answer("Qaysi topshiriqni guruhga biriktiramiz?")
    await Form.assign_task_group.set()

@dp.message_handler(state=Form.assign_task_group)
async def assign_t_s2(message: types.Message, state: FSMContext):
    tid = message.text
    groups = execute_query("SELECT * FROM groups", fetchall=True)
    kb = types.InlineKeyboardMarkup()
    for gid, gname in groups:
        kb.add(types.InlineKeyboardButton(gname, callback_data=f"asg_{tid}_{gid}"))
    await message.answer(f"{tid}-topshiriq uchun guruhni tanlang:", reply_markup=kb)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('asg_'))
async def assign_t_f(callback: types.CallbackQuery):
    _, tid, gid = callback.data.split('_')
    execute_query("INSERT OR REPLACE INTO task_assignments VALUES (?, ?)", (tid, gid), commit=True)
    await callback.answer("Muvaffaqiyatli biriktirildi!")

# --- TALABA: TOPSHIRIQ KO'RISH ---
@dp.message_handler(lambda m: m.text == "📚 Mavjud topshiriqlar")
async def st_tasks(message: types.Message):
    user = execute_query("SELECT group_id FROM students WHERE user_id=?", (message.from_user.id,), fetchone=True)
    if not user: return
    gid = user[0]
    tasks = execute_query("SELECT t.id FROM tasks t JOIN task_assignments ta ON t.id=ta.task_id WHERE ta.group_id=?", (gid,), fetchall=True)
    if not tasks:
        await message.answer("Hozircha sizning guruhingizga topshiriqlar biriktirilmagan.")
        return
    kb = types.InlineKeyboardMarkup(row_width=5)
    kb.add(*[types.InlineKeyboardButton(str(t[0]), callback_data=f"v_{t[0]}") for t in tasks])
    await message.answer("Topshiriq raqamini tanlang:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('v_'))
async def view_t(callback: types.CallbackQuery):
    tid = callback.data.split('_')[1]
    t = execute_query("SELECT title, description, file_id FROM tasks WHERE id=?", (tid,), fetchone=True)
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📤 Fayl topshirish", callback_data=f"sub_{tid}"))
    text = f"<b>{t[0]}</b>\n\n{t[1]}"
    if t[2]: await bot.send_document(callback.from_user.id, t[2], caption=text, reply_markup=kb)
    else: await callback.message.answer(text, reply_markup=kb)

# --- TALABA: TOPSHIRIQ TOPSHIRISH ---
@dp.callback_query_handler(lambda c: c.data.startswith('sub_'))
async def sub_start(callback: types.CallbackQuery, state: FSMContext):
    tid = callback.data.split('_')[1]
    await state.update_data(stid=tid)
    await Form.waiting_for_sub.set()
    await callback.message.answer(f"📎 <b>{tid}-topshiriq uchun</b> faylingizni yuboring.\n\n<i>Eslatma: Faylni 'Document' ko'rinishida yuboring.</i>")
    await callback.answer()

@dp.message_handler(content_types=['document'], state=Form.waiting_for_sub)
async def handle_sub(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get('stid')
    
    if not tid:
        await message.answer("❌ Xatolik yuz berdi. Iltimos, topshiriqni qaytadan tanlang.")
        await state.finish()
        return

    try:
        ts = int(time.time())
        file_name = f"ID{message.from_user.id}_TASK{tid}_{ts}_{message.document.file_name}"
        path = os.path.join(UPLOADS_DIR, file_name)
        
        await message.document.download(destination_file=path)
        
        execute_query("""
            INSERT INTO submissions (user_id, task_id, file_path, status, submitted_at) 
            VALUES (?, ?, ?, ?, ?)
        """, (message.from_user.id, tid, path, "Topshirildi", datetime.now()), commit=True)
        
        await message.answer(
            f"✅ <b>{tid}-topshiriq qabul qilindi!</b>\n\n"
            f"Fayl nomi: {message.document.file_name}\n"
            f"Vaqt: {datetime.now().strftime('%H:%M')}\n\n"
            "Ustoz ishingizni tekshirganidan so'ng sizga xabar boradi.",
            reply_markup=get_main_menu(message.from_user.id)
        )
        await state.finish()
        
    except Exception as e:
        logging.error(f"Fayl saqlashda xato: {e}")
        await message.answer("❌ Faylni yuklashda xatolik yuz berdi.")

# --- ADMIN: ISHLARNI TEKSHIRISH ---
@dp.message_handler(lambda m: m.text == "📥 Ishlarni tekshirish" and m.from_user.id == ADMIN_ID)
async def check_list(message: types.Message):
    res = execute_query("""
        SELECT s.id, st.full_name, s.task_id 
        FROM submissions s 
        JOIN students st ON s.user_id = st.user_id 
        WHERE s.grade IS NULL
    """, fetchall=True)
    
    if not res:
        await message.answer("Hozircha tekshirilmagan ishlar yo'q.")
        return
    
    kb = types.InlineKeyboardMarkup()
    for sid, name, tid in res:
        kb.add(types.InlineKeyboardButton(f"👤 {name} - {tid}-vazifa", callback_data=f"ch_{sid}"))
    await message.answer("Tekshirish uchun talabani tanlang:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('ch_'))
async def ch_view(callback: types.CallbackQuery, state: FSMContext):
    sid = callback.data.split('_')[1]
    res = execute_query("SELECT file_path, task_id FROM submissions WHERE id=?", (sid,), fetchone=True)
    if not res:
        await callback.answer("Fayl topilmadi!")
        return
        
    path, tid = res
    if os.path.exists(path):
        await state.update_data(grade_sid=sid)
        await bot.send_document(callback.from_user.id, types.InputFile(path), 
                               caption=f"Vazifa: {tid}\n\nBaholash uchun ball yuboring:")
        await Form.give_grade.set()
    else:
        await callback.message.answer(f"❌ Fayl topilmadi! ({path})")

@dp.message_handler(state=Form.give_grade)
async def ch_finish(message: types.Message, state: FSMContext):
    d = await state.get_data(); sid = d['grade_sid']
    execute_query("UPDATE submissions SET grade=? WHERE id=?", (message.text, sid), commit=True)
    
    info = execute_query("SELECT user_id, task_id FROM submissions WHERE id=?", (sid,), fetchone=True)
    if info:
        try:
            await bot.send_message(info[0], f"🔔 Natija: {info[1]}-topshiriq → {message.text} ball")
        except:
            pass
    
    await message.answer("Baho saqlandi!", reply_markup=get_main_menu(ADMIN_ID))
    await state.finish()

# --- START BOT ---
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
