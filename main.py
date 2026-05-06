import os
import sqlite3
import logging
import time
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# --- SOZLAMALAR ---
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, '.env'))

API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not API_TOKEN or not ADMIN_ID_RAW:
    print("❌ XATO: .env faylidan ma'lumotlar o'qilmadi!")
    exit()

ADMIN_ID = int(ADMIN_ID_RAW.strip())
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

# --- ADMIN FUNKSIYALARI (QISQARTIRILMADI) ---
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

# --- TALABA: TOPSHIRIQ TOPSHIRISH (XATOLIKLAR TUZATILDI) ---
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

    # Faylni saqlash jarayoni
    try:
        ts = int(time.time())
        file_name = f"ID{message.from_user.id}_TASK{tid}_{ts}_{message.document.file_name}"
        path = os.path.join(UPLOADS_DIR, file_name)
        
        # Faylni yuklab olish
        await message.document.download(destination_file=path)
        
        # Bazaga yozish
        execute_query("""
            INSERT INTO submissions (user_id, task_id, file_path, status, submitted_at) 
            VALUES (?, ?, ?, ?, ?)
        """, (message.from_user.id, tid, path, "Topshirildi", datetime.now()), commit=True)
        
        # TALABAGA TASDIQLASH XABARI
        await message.answer(
            f"✅ <b>{tid}-topshiriq qabul qilindi!</b>\n\n"
            f"Fayl nomi: {message.document.file_name}\n"
            f"Vaqt: {datetime.now().strftime('%H:%M')}\n\n"
            "Ustoz ishingizni tekshirganidan so'ng sizga xabar boradi. "
            "Yana boshqa topshiriqlarni ishlashda davom etishingiz mumkin.",
            reply_markup=get_main_menu(message.from_user.id)
        )
        await state.finish()
        
    except Exception as e:
        logging.error(f"Fayl saqlashda xato: {e}")
        await message.answer("❌ Faylni yuklashda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")

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
        await callback.message.answer(f"❌ Xato: Fayl papkadan topilmadi! ({path})")

@dp.message_handler(state=Form.give_grade)
async def ch_finish(message: types.Message, state: FSMContext):
    d = await state.get_data(); sid = d['grade_sid']
    execute_query("UPDATE submissions SET grade=? WHERE id=?", (message.text, sid), commit=True)
    
    info = execute_query("SELECT user_id, task_id FROM submissions WHERE id=?", (sid,), fetchone=True)
    if info:
        try: await bot.send_message(info[0], f"🔔 <b>Natija:</b> Sizning {info[1]}-topshirig'ingiz tekshirildi.\nBall: {message.text}")
        except: pass
    
    await message.answer("Baho saqlandi!", reply_markup=get_main_menu(ADMIN_ID))
    await state.finish()

# --- HISOBOT VA O'CHIRISH (QISQARTIRILMADI) ---
@dp.message_handler(lambda m: m.text == "📊 Hisobot (.xlsx)" and m.from_user.id == ADMIN_ID)
async def rep_m(message: types.Message):
    groups = execute_query("SELECT * FROM groups", fetchall=True)
    if not groups:
        await message.answer("Guruhlar yo'q.")
        return
    kb = types.InlineKeyboardMarkup()
    for gid, gn in groups: kb.add(types.InlineKeyboardButton(gn, callback_data=f"r_{gid}"))
    await message.answer("Hisobot uchun guruhni tanlang:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('r_'))
async def rep_f(callback: types.CallbackQuery):
    gid = callback.data.split('_')[1]
    
    # 1. Guruh nomini olish
    group_info = execute_query("SELECT name FROM groups WHERE id=?", (gid,), fetchone=True)
    gname = group_info[0] if group_info else "Noma'lum"

    # 2. Shu guruhdagi barcha talabalarni olish
    students = execute_query("SELECT user_id, full_name FROM students WHERE group_id=?", (gid,), fetchall=True)
    
    if not students:
        await callback.answer("Bu guruhda talabalar yo'q!", show_alert=True)
        return

    final_data = []

    # 3. Har bir talaba uchun ma'lumot yig'ish
    for sid, sname in students:
        row = {
            'Guruhi': gname,
            'Ism familiyasi': sname
        }
        
        # 1-dan 15-gacha topshiriqlarni tekshirish
        for tid in range(1, 16):
            # Bazadan topshiriq holatini tekshiramiz
            res = execute_query(
                "SELECT grade, status FROM submissions WHERE user_id=? AND task_id=?", 
                (sid, tid), fetchone=True
            )
            
            if res:
                grade, status = res
                if grade and str(grade).strip(): # Agar baho qo'yilgan bo'lsa
                    row[str(tid)] = grade
                else: # Agar topshirilgan-u, lekin baho bo'sh bo'lsa
                    row[str(tid)] = "+"
            else:
                # Topshirilmagan bo'lsa
                row[str(tid)] = "-"
        
        final_data.append(row)

    # 4. Excel faylni yaratish
    try:
        df = pd.DataFrame(final_data)
        
        # Ustunlar tartibini belgilash (Guruh, Ism, 1, 2, ..., 15)
        columns_order = ['Guruhi', 'Ism familiyasi'] + [str(i) for i in range(1, 16)]
        df = df[columns_order]

        timestamp = datetime.now().strftime("%d-%m_%H-%M")
        fname = f"Hisobot_{gname}_{timestamp}.xlsx"
        
        # Excelga yozish (index=False - qator raqamlarini chiqarmaydi)
        df.to_excel(fname, index=False)

        # 5. Admin (Sizga) yuborish
        await callback.message.answer_document(
            types.InputFile(fname), 
            caption=f"📊 <b>{gname}</b> guruhi bo'yicha hisobot\n📅 Sana: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        # Serverni tozalash
        os.remove(fname)
        await callback.answer()
        
    except Exception as e:
        logging.error(f"Excel yaratishda xato: {e}")
        await callback.message.answer("❌ Excel hisobotini tayyorlashda xatolik yuz berdi.")

@dp.message_handler(lambda m: m.text == "🗑 Talaba/Guruhni o'chirish" and m.from_user.id == ADMIN_ID)
async def del_menu(message: types.Message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Guruhni o'chirish", callback_data="del_mode_group"))
    await message.answer("O'chirish menyusi:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "del_mode_group")
async def del_group_list(callback: types.CallbackQuery):
    groups = execute_query("SELECT * FROM groups", fetchall=True)
    kb = types.InlineKeyboardMarkup()
    for gid, gname in groups:
        kb.add(types.InlineKeyboardButton(f"🗑 {gname}", callback_data=f"dg_{gid}"))
    await callback.message.edit_text("O'chirish uchun guruhni tanlang:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('dg_'))
async def finalize_dg(callback: types.CallbackQuery):
    gid = callback.data.split('_')[1]
    execute_query("DELETE FROM groups WHERE id=?", (gid,), commit=True)
    execute_query("DELETE FROM students WHERE group_id=?", (gid,), commit=True)
    await callback.answer("Guruh o'chirildi!")
    await callback.message.delete()

@dp.message_handler(lambda m: m.text == "🏆 Mening ballarim")
async def my_scores(message: types.Message):
    res = execute_query("SELECT task_id, grade FROM submissions WHERE user_id=? AND grade IS NOT NULL", (message.from_user.id,), fetchall=True)
    if not res:
        await message.answer("Sizda hali baholangan topshiriqlar yo'q.")
        return
    text = "📊 <b>Natijalaringiz:</b>\n\n" + "\n".join([f"🔹 {t}-vazifa: {g} ball" for t, g in res])
    await message.answer(text)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)