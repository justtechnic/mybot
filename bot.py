import os
import sqlite3
import asyncio
import aiosqlite
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    InputMediaPhoto
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7614009397"))
CHANNELS = ["@JustTechnic", "@baraholkamskkupiproday"]
DB_PATH = "market.db"
SUPPORT_CONTACT = "@justtechnic1"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

bot = Bot(token=TOKEN)
dp = Dispatcher()

class AdForm(StatesGroup):
    choosing_type = State()
    choosing_category = State()
    entering_model = State()
    entering_memory = State()
    entering_description = State()
    entering_price = State()
    entering_city = State()
    choosing_delivery = State()
    uploading_photos = State()
    preview = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ad_type TEXT,
                category TEXT,
                model TEXT,
                memory TEXT,
                description TEXT,
                price TEXT,
                city TEXT,
                delivery TEXT,
                photos TEXT,
                status TEXT DEFAULT 'DRAFT',
                created_at TIMESTAMP,
                expires_at TIMESTAMP,
                is_trial BOOLEAN DEFAULT 0,
                channel TEXT,
                message_id INTEGER,
                message_ids TEXT
            )
        """)
        try:
            await db.execute("ALTER TABLE listings ADD COLUMN message_ids TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen TIMESTAMP
            )
        """)
        await db.commit()

async def save_listing(user_id, data, status='PENDING_MODERATION', is_trial=False):
    async with aiosqlite.connect(DB_PATH) as db:
        duration = timedelta(hours=24) if is_trial else timedelta(days=7)
        now = datetime.now()
        expires = (now + duration).isoformat()
        cursor = await db.execute("""
            INSERT INTO listings (
                user_id, ad_type, category, model, memory, description,
                price, city, delivery, photos, status, created_at,
                expires_at, is_trial
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get('ad_type'), data.get('category'), data.get('model'),
            data.get('memory'), data.get('description'), data.get('price'),
            data.get('city'), data.get('delivery'),
            ",".join(data.get('photos') or []),
            status, now.isoformat(), expires, 1 if is_trial else 0
        ))
        await db.commit()
        return cursor.lastrowid

def bottom_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏡 Главное меню"), KeyboardButton(text="🥷🏻 Профиль")],
            [KeyboardButton(text="📚 Мои лоты")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои товары", callback_data="my_ads")],
        [InlineKeyboardButton(text="🥷🏻 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="📣 Канал", url="https://t.me/JustTechnic")]
    ])

def publish_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌱 Бесплатно", callback_data="pub_free")],
        [InlineKeyboardButton(text="💳 Платно", callback_data="pub_paid")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def ad_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Продажа", callback_data="adtype_sell")],
        [InlineKeyboardButton(text="🕵🏻 Покупка", callback_data="adtype_buy")],
        [InlineKeyboardButton(text="🔁 Обмен", callback_data="adtype_exchange")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="pub_free")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def category_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Телефон", callback_data="cat_phone")],
        [InlineKeyboardButton(text="📲 Планшет", callback_data="cat_tablet")],
        [InlineKeyboardButton(text="💻 Ноутбук", callback_data="cat_laptop")],
        [InlineKeyboardButton(text="🎧 Аудио", callback_data="cat_audio")],
        [InlineKeyboardButton(text="🧩 Запчасти/лот", callback_data="cat_parts")],
        [InlineKeyboardButton(text="🔨 Услуги", callback_data="cat_services")],
        [InlineKeyboardButton(text="🎮 Прочее", callback_data="cat_other")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="free_continue")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def delivery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправлю", callback_data="del_yes")],
        [InlineKeyboardButton(text="❌ Не отправлю", callback_data="del_no")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_city")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def step_kb(back_callback: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data=back_callback)],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_post_text(ad, status="АКТУАЛЬНО", author_tag=None):
    if status == "ПРОДАНО":
        header = "❌ПРОДАНО❌"
    else:
        header = "✅АКТУАЛЬНО✅"

    tag = author_tag
    if not tag:
        try:
            tag = ad["author_tag"]
        except (KeyError, IndexError):
            tag = ad["user_id"]

    safe_model = escape_html(ad["model"])
    safe_category = escape_html(ad["category"])
    safe_city = escape_html(ad["city"])
    safe_desc = escape_html(ad["description"])
    safe_price = escape_html(ad["price"])
    safe_delivery = escape_html(ad["delivery"])
    safe_memory = escape_html(ad["memory"]) if ad["memory"] and ad["memory"] != "—" else None

    text = f"{header}\n#{safe_category}\n#{safe_city}\n\n"
    text += f"📦 {safe_model}\n\n"
    if safe_memory:
        text += f"💾Память: {safe_memory}\n\n"
    text += (
        f"{safe_desc}\n\n"
        f"🇷🇺{safe_city}\n"
        f"💸{safe_price}\n"
        f"🚛{safe_delivery}\n\n"
        f"🥷🏻{tag}"
    )
    return text

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
            (message.from_user.id, datetime.now().isoformat())
        )
        await db.commit()
    await message.answer("Добро пожаловать в JustTrade!", reply_markup=bottom_menu())
    await show_main_menu(message)

async def show_main_menu(message: types.Message, user_id: int = None):
    uid = user_id or message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'ACTIVE'",
            (uid,)
        ) as cur:
            count = (await cur.fetchone())[0]
    text = (
        f"Добрый день, {message.from_user.first_name}!\n\n"
        f"JustTrade - не просто бот, а лучший помощник в продаже техники;)\n\n"
        f"Ваши объявления: {count}"
    )
    await message.answer(text, reply_markup=main_menu_kb())

@dp.message(F.text == "🏡 Главное меню")
async def go_main(message: types.Message):
    await show_main_menu(message)

@dp.callback_query(F.data == "main_menu")
async def cb_main(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message, user_id=callback.from_user.id)
    await callback.answer()

@dp.callback_query(F.data == "publish")
async def cb_publish(callback: types.CallbackQuery):
    await callback.message.edit_text("Выберите способ публикации", reply_markup=publish_type_kb())
    await callback.answer()

@dp.callback_query(F.data == "pub_free")
async def cb_pub_free(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Отлично! Ваше объявление будет выставлено бесплатно на 24 часа, после чего оно будет автоматически снято с продажи. Если вы захотите продлить срок публикации, то это будет стоить 25 руб или 25 звезд на 7 дней.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Продолжить", callback_data="free_continue")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "free_continue")
async def cb_free_continue(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(is_trial=True)
    await callback.message.edit_text(
        "Нужно выбрать, что ты хочешь сделать с товаром.\n"
        "Далее - по пунктам: тип товара, описание, цена, город, доставка, фото товара (при покупке можно без фото).\n\n"
        "Отмена любых действий: /cancel",
        reply_markup=ad_type_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("adtype_"))
async def cb_adtype(callback: types.CallbackQuery, state: FSMContext):
    ad_type = callback.data.split("_")[1]
    await state.update_data(ad_type=ad_type)
    await state.set_state(AdForm.choosing_category)
    await callback.message.edit_text(
        "Выберите тип устройства - для каждого есть подсказка:\n\n"
        "📱 Телефон - кнопочные, сенсорные, раскладушки (Nokia, iPhone, Samsung)\n"
        "📲 Планшет - iPad, Xiaomi pad, Samsung pad\n"
        "💻 Ноутбук - нетбук, лэптоп, ноутбук (Asus, Dell и др)\n"
        "🎧 Аудио - наушники, колонки, саундбары\n"
        "🧩 Запчасти/лот - дисплей, АКБ и тд, несколько разных вещей\n"
        "🔨 Услуги - ремонт техники\n"
        "🎮 Прочее - Xbox, игры, телевизор и тд",
        reply_markup=category_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("cat_"), AdForm.choosing_category)
async def cb_category(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.split("_")[1]
    cat_names = {
        "phone": "Телефон", "tablet": "Планшет", "laptop": "Ноутбук",
        "audio": "Аудио", "parts": "Запчасти/лот", "services": "Услуги", "other": "Прочее"
    }
    category_name = cat_names.get(cat, cat)
    await state.update_data(category=category_name)
    await state.set_state(AdForm.entering_model)
    await callback.message.edit_text(
        f"{category_name}.\n\nНапишите модель устройства или ключевую особенность.",
        reply_markup=step_kb("back_to_category")
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_category", AdForm.entering_model)
async def back_to_category(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(model=None)
    await state.set_state(AdForm.choosing_category)
    await callback.message.edit_text(
        "Выберите тип устройства - для каждого есть подсказка:\n\n"
        "📱 Телефон - кнопочные, сенсорные, раскладушки (Nokia, iPhone, Samsung)\n"
        "📲 Планшет - iPad, Xiaomi pad, Samsung pad\n"
        "💻 Ноутбук - нетбук, лэптоп, ноутбук (Asus, Dell и др)\n"
        "🎧 Аудио - наушники, колонки, саундбары\n"
        "🧩 Запчасти/лот - дисплей, АКБ и тд, несколько разных вещей\n"
        "🔨 Услуги - ремонт техники\n"
        "🎮 Прочее - Xbox, игры, телевизор и тд",
        reply_markup=category_kb()
    )
    await callback.answer()

@dp.message(AdForm.entering_model)
async def process_model(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("Слишком длинно, максимум 200 символов.")
        return
    await state.update_data(model=message.text)
    data = await state.get_data()
    category = data.get('category')
    no_memory_categories = {"Прочее", "Услуги", "Запчасти/лот"}
    if category in no_memory_categories:
        await state.update_data(memory="—")
        await state.set_state(AdForm.entering_description)
        await message.answer(
            f"{category}, 📃Описание\n\n"
            "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.",
            reply_markup=step_kb("back_to_model")
        )
    else:
        await state.set_state(AdForm.entering_memory)
        await message.answer(
            f"{category}, 💾Память\n\n"
            "Напишите память устройства, формат ОЗУ/ПЗУ.\n"
            "Без текста Гб и без лишних символов.",
            reply_markup=step_kb("back_to_model")
        )

@dp.callback_query(F.data == "back_to_model", AdForm.entering_memory)
async def back_to_model_from_memory(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(memory=None)
    await state.set_state(AdForm.entering_model)
    await callback.message.edit_text(
        f"{data['category']}.\n\nНапишите модель устройства или ключевую особенность.",
        reply_markup=step_kb("back_to_category")
    )
    await callback.answer()

@dp.message(AdForm.entering_memory)
async def process_memory(message: types.Message, state: FSMContext):
    await state.update_data(memory=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await message.answer(
        f"{data['category']}, 📃Описание\n\n"
        "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.\n"
        "Память уже указана, повторять не нужно.",
        reply_markup=step_kb("back_to_memory")
    )

@dp.callback_query(F.data == "back_to_memory", AdForm.entering_description)
async def back_to_memory(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(description=None)
    if data.get('memory') == "—":
        await state.set_state(AdForm.entering_model)
        await callback.message.edit_text(
            f"{data['category']}.\n\nНапишите модель устройства или ключевую особенность.",
            reply_markup=step_kb("back_to_category")
        )
    else:
        await state.set_state(AdForm.entering_memory)
        await callback.message.edit_text(
            f"{data['category']}, 💾Память\n\n"
            "Напишите память устройства, формат ОЗУ/ПЗУ.\n"
            "Без текста Гб и без лишних символов.",
            reply_markup=step_kb("back_to_model")
        )
    await callback.answer()

@dp.message(AdForm.entering_description)
async def process_description(message: types.Message, state: FSMContext):
    if len(message.text) > 700:
        await message.answer("Слишком длинно, максимум 700 символов.")
        return
    await state.update_data(description=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_price)
    await message.answer(
        f"{data['category']}, 🪎Цена\n\n"
        "Укажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно",
        reply_markup=step_kb("back_to_description")
    )

@dp.callback_query(F.data == "back_to_description", AdForm.entering_price)
async def back_to_description(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(price=None)
    await state.set_state(AdForm.entering_description)
    await callback.message.edit_text(
        f"{data['category']}, 📃Описание\n\n"
        "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.",
        reply_markup=step_kb("back_to_memory")
    )
    await callback.answer()

@dp.message(AdForm.entering_price)
async def process_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or not (1 <= int(message.text) <= 100_000_000):
        await message.answer("Введите цену цифрами (от 1 до 100 000 000).")
        return
    await state.update_data(price=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_city)
    await message.answer(
        f"{data['category']}, 🌇Город\n\n"
        "Укажите город самовывоз - только название.\n"
        "Пример: Москва, Питер, Казань\n"
        "Никаких лишних символов указывать не нужно",
        reply_markup=step_kb("back_to_price")
    )

@dp.callback_query(F.data == "back_to_price", AdForm.entering_city)
async def back_to_price(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(city=None)
    await state.set_state(AdForm.entering_price)
    await callback.message.edit_text(
        f"{data['category']}, 🪎Цена\n\n"
        "Укажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно",
        reply_markup=step_kb("back_to_description")
    )
    await callback.answer()

@dp.message(AdForm.entering_city)
async def process_city(message: types.Message, state: FSMContext):
    if len(message.text) > 100:
        await message.answer("Слишком длинно, максимум 100 символов.")
        return
    await state.update_data(city=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.choosing_delivery)
    await message.answer(
        f"{data['category']}, 🗃️Доставка\n\n"
        "Отправите ли вы устройство через какую либо транспортную компанию?",
        reply_markup=delivery_kb()
    )

@dp.callback_query(F.data == "back_to_city", AdForm.choosing_delivery)
async def back_to_city(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(delivery=None)
    await state.set_state(AdForm.entering_city)
    await callback.message.edit_text(
        f"{data['category']}, 🌇Город\n\n"
        "Укажите город самовывоз - только название.\n"
        "Пример: Москва, Питер, Казань\n"
        "Никаких лишних символов указывать не нужно",
        reply_markup=step_kb("back_to_price")
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("del_"), AdForm.choosing_delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    delivery = "Отправлю" if callback.data == "del_yes" else "Не отправлю"
    await state.update_data(delivery=delivery, photos=[])
    await state.set_state(AdForm.uploading_photos)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{data['category']}, 📸Фотографии\n\n"
        "Покажите фото товара, которое сейчас опубликуете.\n\n"
        "Фото обязательно! До 10 штук.\n\n"
        "Фото должны быть настоящими, а не из интернета, иначе отклоним запрос на публикацию.\n\n"
        "Как отправили все что хотели опубликовать - нажмите \"✅Готово!\"",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово!", callback_data="photos_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_delivery")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_delivery", AdForm.uploading_photos)
async def back_to_delivery(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(photos=[])
    await state.set_state(AdForm.choosing_delivery)
    await callback.message.edit_text(
        f"{data['category']}, 🗃️Доставка\n\n"
        "Отправите ли вы устройство через какую либо транспортную компанию?",
        reply_markup=delivery_kb()
    )
    await callback.answer()

@dp.message(AdForm.uploading_photos, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])
    if len(photos) >= 10:
        await message.answer("Максимум 10 фото.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"Загружено фотографий {len(photos)} из 10")

@dp.message(AdForm.uploading_photos, F.video)
async def reject_video(message: types.Message, state: FSMContext):
    await message.answer("Видео не поддерживается. Пожалуйста, отправьте фото.")

@dp.callback_query(F.data == "photos_done", AdForm.uploading_photos)
async def photos_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])
    if not photos:
        await callback.answer("Загрузите хотя бы одно фото!", show_alert=True)
        return
    await state.set_state(AdForm.preview)
    preview_text = (
        f"🔍Предпросмотр\n\n"
        f"Проверь текст и вложения на наличие ошибок. Если все нормально - отправь на модерацию.\n\n"
        f"✅АКТУАЛЬНО✅\n"
        f"#{data['category']}\n"
        f"#{data['city']}\n\n"
        f"📦 {data['model']}\n\n"
    )
    if data.get('memory') and data['memory'] != "—":
        preview_text += f"💾Память: {data['memory']}\n\n"
    preview_text += (
        f"{data['description']}\n\n"
        f"🇷🇺{data['city']}\n"
        f"💸{data['price']}\n"
        f"🚛{data['delivery']}\n\n"
        f"🥷🏻{callback.from_user.first_name}"
    )
    if len(preview_text) > 1024:
        await callback.answer("Описание слишком длинное, сократите.", show_alert=True)
        return
    preview_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💫 Отправка на модерацию", callback_data="send_moderation")],
        [InlineKeyboardButton(text="↩️ Назад к фото", callback_data="back_to_photos")],
        [InlineKeyboardButton(text="✒️ Сбросить объявление", callback_data="reset_ad")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])
    if len(photos) == 1:
        await callback.message.answer_photo(photo=photos[0], caption=preview_text, reply_markup=preview_kb)
    else:
        media = []
        for i, ph in enumerate(photos):
            if i == 0:
                media.append(InputMediaPhoto(media=ph, caption=preview_text))
            else:
                media.append(InputMediaPhoto(media=ph))
        await callback.message.answer_media_group(media=media)
        await callback.message.answer("Выберите действие:", reply_markup=preview_kb)
    await callback.answer()

@dp.callback_query(F.data == "back_to_photos", AdForm.preview)
async def back_to_photos(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(AdForm.uploading_photos)
    text = (
        f"{data['category']}, 📸Фотографии\n\n"
        "Покажите фото товара, которое сейчас опубликуете.\n\n"
        "Фото обязательно! До 10 штук.\n\n"
        "Как отправили все что хотели опубликовать - нажмите \"✅Готово!\""
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово!", callback_data="photos_done")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_delivery")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])
    try:
        await callback.message.edit_caption(caption=text, reply_markup=kb)
    except Exception:
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            await callback.message.answer(text, reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "send_moderation", AdForm.preview)
async def send_moderation(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    is_trial = data.get('is_trial', True)
    lot_id = await save_listing(user_id, data, status='PENDING_MODERATION', is_trial=is_trial)
    moderation_text = (
        "Объявление отправлено на модерацию!\n\n"
        "Как только модератор примет пост - придёт уведомление вам в чат с ботом, а статус появится в разделе \"Мои объявления\""
    )
    try:
        await callback.message.edit_caption(caption=moderation_text, reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_text(moderation_text, reply_markup=None)
        except Exception:
            await callback.message.answer(moderation_text)

    admin_text = (
        f"🔔 Новое объявление на модерации #{lot_id}\n\n"
        f"Пользователь: {callback.from_user.first_name} (@{callback.from_user.username})\n"
        f"Тип: {data['ad_type']} | Категория: {data['category']}\n"
        f"Модель: {data['model']}\n"
    )
    if data.get('memory') and data['memory'] != "—":
        admin_text += f"Память: {data['memory']}\n"
    admin_text += (
        f"Цена: {data['price']} | Город: {data['city']}\n"
        f"Доставка: {data['delivery']}\n"
        f"Описание: {data['description']}"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрено", callback_data=f"approve_{lot_id}")],
        [InlineKeyboardButton(text="❌ Не одобрено", callback_data=f"reject_{lot_id}")]
    ])
    photos = data.get('photos', [])
    if photos:
        if len(photos) == 1:
            await bot.send_photo(chat_id=ADMIN_ID, photo=photos[0], caption=admin_text, reply_markup=admin_kb)
        else:
            media = []
            for i, ph in enumerate(photos):
                if i == 0:
                    media.append(InputMediaPhoto(media=ph, caption=admin_text))
                else:
                    media.append(InputMediaPhoto(media=ph))
            await bot.send_media_group(chat_id=ADMIN_ID, media=media)
            await bot.send_message(chat_id=ADMIN_ID, text="Выберите действие:", reply_markup=admin_kb)
    else:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=admin_kb)
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    lot_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Объявление не найдено", show_alert=True)
            return
        try:
            author = await bot.get_chat(row['user_id'])
            author_tag = f"@{author.username}" if author.username else author.first_name
        except Exception:
            author_tag = str(row['user_id'])
        post_text = build_post_text(row, status="АКТУАЛЬНО", author_tag=author_tag)
        photos = row['photos'].split(",") if row['photos'] else []
        saved_pairs = []
        first_channel = ""
        first_message_id = 0
        for channel in CHANNELS:
            try:
                if photos:
                    if len(photos) == 1:
                        sent = await bot.send_photo(chat_id=channel, photo=photos[0], caption=post_text, parse_mode="HTML")
                    else:
                        media = []
                        for i, ph in enumerate(photos):
                            if i == 0:
                                media.append(InputMediaPhoto(media=ph, caption=post_text, parse_mode="HTML"))
                            else:
                                media.append(InputMediaPhoto(media=ph))
                        sent_msgs = await bot.send_media_group(chat_id=channel, media=media)
                        sent = sent_msgs[0]
                else:
                    sent = await bot.send_message(chat_id=channel, text=post_text, parse_mode="HTML")
                saved_pairs.append(f"{channel}:{sent.message_id}")
                if not first_channel:
                    first_channel = channel
                    first_message_id = sent.message_id
            except Exception as e:
                print(f"Ошибка публикации: {e}")
        await db.execute(
            "UPDATE listings SET status = 'ACTIVE', channel = ?, message_id = ?, message_ids = ? WHERE id = ?",
            (first_channel, first_message_id, ",".join(saved_pairs), lot_id)
        )
        await db.commit()
    try:
        await bot.send_message(
            row['user_id'],
            "Ваша публикация одобрена!👍",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
            ])
        )
    except Exception:
        pass
    try:
        await callback.message.edit_caption(caption="Объявление одобрено и опубликовано.")
    except Exception:
        try:
            await callback.message.edit_text("Объявление одобрено и опубликовано.")
        except Exception:
            await
                try:
        await callback.message.edit_caption(caption="Объявление одобрено и опубликовано.")
    except Exception:
        try:
            await callback.message.edit_text("Объявление одобрено и опубликовано.")
        except Exception:
            await callback.message.answer("Объявление одобрено и опубликовано.")
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    lot_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM listings WHERE id = ?", (lot_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Объявление не найдено", show_alert=True)
            return
        await db.execute("UPDATE listings SET status = 'REJECTED' WHERE id = ?", (lot_id,))
        await db.commit()
    try:
        await bot.send_message(
            row['user_id'],
            "Ваша публикация не одобрена👎, попробуйте заполнить анкету заново",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
            ])
        )
    except Exception:
        pass
    try:
        await callback.message.edit_caption(caption="Объявление отклонено.")
    except Exception:
        try:
            await callback.message.edit_text("Объявление отклонено.")
        except Exception:
            await callback.message.answer("Объявление отклонено.")
    await callback.answer()

@dp.callback_query(F.data == "reset_ad")
async def reset_ad(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message, user_id=callback.from_user.id)
    await callback.answer()

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=bottom_menu())
    await show_main_menu(message)

@dp.callback_query(F.data == "my_ads")
async def cb_my_ads(callback: types.CallbackQuery):
    await _send_my_ads(callback.message, callback.from_user.id)
    await callback.answer()

@dp.message(F.text == "📚 Мои лоты")
async def my_lots_message(message: types.Message):
    await _send_my_ads(message, message.from_user.id)

async def _send_my_ads(target_message: types.Message, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE user_id = ? AND status = 'ACTIVE' ORDER BY id DESC",
            (user_id,)
        ) as cur:
            ads = await cur.fetchall()

    if not ads:
        await target_message.answer("У вас нет активных объявлений.")
        return

    lines = "\n".join(
        f"• {escape_html(ad['model'])} — {escape_html(str(ad['price']))} руб."
        for ad in ads
    )
    kb = []
    for ad in ads:
        price = ad['price'] if ad['price'] is not None else "—"
        kb.append([InlineKeyboardButton(
            text=f"{ad['model']} ({price} руб.)",
            callback_data=f"lot_{ad['id']}"
        )])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])

    await target_message.answer(
        f"📃 Мои объявления\n{len(ads)} в канале\n\n"
        f"{lines}\n\n"
        "Нажми на объявление ниже - там все функции.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(F.data.startswith("lot_"))
async def cb_lot_details(callback: types.CallbackQuery):
    lot_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено", show_alert=True)
        return

    link = ""
    if ad["channel"] and ad["message_id"]:
        link = f"https://t.me/{ad['channel'].lstrip('@')}/{ad['message_id']}"

    created = ad["created_at"][:10] if ad["created_at"] else "—"
    text = (
        f"{ad['model']}\n"
        f"{ad['price']} руб.\n"
        f"{ad['city']}\n"
        f"В канале с {created}"
    )

    kb = []
    if link:
        kb.append([InlineKeyboardButton(text="🔗 Открыть в канале", url=link)])
    kb.append([InlineKeyboardButton(text="🗑️ Снять с продажи", callback_data=f"remove_{lot_id}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="my_ads")])

    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data.startswith("remove_"))
async def cb_remove_ad(callback: types.CallbackQuery):
    lot_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE id = ? AND user_id = ? AND status = 'ACTIVE'",
            (lot_id, user_id)
        ) as cur:
            ad = await cur.fetchone()
        if not ad:
            await callback.answer("Объявление не найдено или уже снято.", show_alert=True)
            return
        await db.execute("UPDATE listings SET status = 'SOLD' WHERE id = ?", (lot_id,))
        await db.commit()

    try:
        author = await bot.get_chat(ad['user_id'])
        tag = f"@{author.username}" if author.username else author.first_name
    except Exception:
        tag = str(ad['user_id'])
    new_caption = build_post_text(ad, status="ПРОДАНО", author_tag=tag)

    pairs = (ad["message_ids"] or "").split(",") if ad["message_ids"] else []
    if not pairs and ad["channel"] and ad["message_id"]:
        pairs = [f"{ad['channel']}:{ad['message_id']}"]
    for pair in pairs:
        pair = pair.strip()
        if ":" not in pair:
            continue
        ch, mid = pair.rsplit(":", 1)
        try:
            await bot.edit_message_caption(
                chat_id=ch, message_id=int(mid),
                caption=new_caption, parse_mode="HTML"
            )
        except Exception:
            try:
                await bot.edit_message_text(
                    chat_id=ch, message_id=int(mid),
                    text=new_caption, parse_mode="HTML"
                )
            except Exception as e:
                print(f"Не удалось отредактировать {ch}/{mid}: {e}")

    try:
        await callback.message.edit_text("Объявление снято с продажи.")
    except Exception:
        await callback.message.answer("Объявление снято с продажи.")
    await callback.answer()

async def build_profile_text(user_id: int, first_name: str, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT first_seen FROM users WHERE user_id = ?", (user_id,)) as cur:
            u = await cur.fetchone()
        first_seen = u["first_seen"][:10] if u and u["first_seen"] else "—"

        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'ACTIVE'", (user_id,)
        ) as cur:
            active = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'SOLD'", (user_id,)
        ) as cur:
            sold = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT model, channel, message_id FROM listings WHERE user_id = ? AND status = 'ACTIVE'",
            (user_id,)
        ) as cur:
            ads = await cur.fetchall()

    ads_lines = ""
    for ad in ads:
        safe_model = escape_html(ad["model"])
        if ad["channel"] and ad["message_id"]:
            link = f"https://t.me/{ad['channel'].lstrip('@')}/{ad['message_id']}"
            ads_lines += f'• <a href="{link}">{safe_model}</a>\n'
        else:
            ads_lines += f"• {safe_model}\n"

    username_str = f"@{username}" if username else "нет"
    text = (
        f"👤 {escape_html(first_name)}\n"
        f"Профиль в JustTrade создан {first_seen}\n"
        f"Объявления: {active} актуально\n"
        f"Продано: {sold}\n"
        f"Телеграмм: {username_str}\n\n"
        f"Сейчас в канале:\n{ads_lines or 'нет активных объявлений'}"
    )
    return text

@dp.callback_query(F.data == "profile")
async def cb_profile(callback: types.CallbackQuery):
    await _send_profile(callback.message, callback.from_user)
    await callback.answer()

@dp.message(F.text == "🥷🏻 Профиль")
async def profile_message(message: types.Message):
    await _send_profile(message, message.from_user)

async def _send_profile(target_message: types.Message, user):
    text = await build_profile_text(user.id, user.first_name, user.username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])
    await target_message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    text = (
        "❓ <b>Помощь</b>\n\n"
        "Здесь вы найдёте ответы на частые вопросы о публикации объявлений в JustTrade.\n\n"
        "Выберите раздел ниже:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Правила публикации", callback_data="help_rules")],
        [InlineKeyboardButton(text="❔ Как выставить?", callback_data="help_howto")],
        [InlineKeyboardButton(text="⚠️ Отказ в публикации", callback_data="help_reject")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help_rules")
async def cb_help_rules(callback: types.CallbackQuery):
    text = (
        "📄 <b>Правила публикации</b>\n\n"
        "<b>Что нельзя выставлять:</b>\n"
        "• Запрещённые товары (оружие, наркотики, алкоголь, табак, лекарства).\n"
        "• Товары, нарушающие авторские права (пиратские копии, взломанный софт).\n"
        "• Животных, услуги интимного характера, финансовые пирамиды.\n"
        "• Объявления без реальных фото товара (фото из интернета запрещены).\n"
        "• Повторные объявления одного и того же товара от одного пользователя.\n"
        "• Объявления с оскорблениями, нецензурной лексикой, спамом.\n\n"
        "<b>Без чего нельзя выставить объявление:</b>\n"
        "• Реальные фотографии товара (от 1 до 10 штук).\n"
        "• Корректное название модели и описание состояния.\n"
        "• Указание цены и города.\n"
        "• Выбор способа доставки.\n"
        "• Соблюдение тематики канала (техника и электроника).\n\n"
        "Если объявление нарушает правила, модератор отклонит его."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="help")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help_howto")
async def cb_help_howto(callback: types.CallbackQuery):
    text = (
        "❔ <b>Как выставить объявление</b>\n\n"
        "1. Нажмите «✅ Опубликовать» в главном меню.\n"
        "2. Выберите «🌱 Бесплатно» (первое объявление — на 24 часа) или «💳 Платно» (на 7 дней).\n"
        "3. Выберите, что хотите сделать: «💵 Продажа», «🕵🏻 Покупка» или «🔁 Обмен».\n"
        "4. Выберите тип устройства: Телефон, Планшет, Ноутбук, Аудио, Запчасти/лот, Услуги, Прочее.\n"
        "5. Укажите модель или ключевую особенность.\n"
        "6. Для техники — укажите память в формате ОЗУ/ПЗУ (например, 12/256).\n"
        "7. Напишите описание: внешний вид, состояние, комплектация, ремонты, торг.\n"
        "8. Укажите цену (только цифры).\n"
        "9. Укажите город.\n"
        "10. Выберите, отправляете ли вы товар транспортной компанией.\n"
        "11. Загрузите от 1 до 10 реальных фото товара (видео не поддерживается).\n"
        "12. Проверьте предпросмотр и нажмите «💫 Отправка на модерацию».\n\n"
        "После одобрения модератором объявление появится в каналах.\n\n"
        "Отмена действий в любой момент: /cancel."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="help")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help_reject")
async def cb_help_reject(callback: types.CallbackQuery):
    text = (
        "⚠️ <b>Отказ в публикации</b>\n\n"
        "Если ваше объявление отклонено, это значит, что по мнению модератора оно "
        "не соблюдает правила публикации объявлений.\n\n"
        "<b>Частые причины отказа:</b>\n"
        "• Фото не соответствуют товару или взяты из интернета.\n"
        "• Описание неполное или вводит в заблуждение.\n"
        "• Товар запрещён к продаже.\n"
        "• Объявление не по тематике канала.\n"
        "• Нарушены правила оформления (нет цены, города, доставки).\n\n"
        "<b>Что делать:</b>\n"
        "Создайте объявление заново, исправив замечания. Нажмите «✅ Опубликовать» "
        "в главном меню и заполните анкету снова.\n\n"
        f"Если вы не согласны с отказом — напишите в поддержку: {SUPPORT_CONTACT}."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="help")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

async def expire_loop():
    try:
        while True:
            try:
                now = datetime.now().isoformat()
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM listings WHERE status = 'ACTIVE' AND expires_at IS NOT NULL AND expires_at <= ?",
                        (now,)
                    ) as cur:
                        expired = await cur.fetchall()

                for ad in expired:
                    async with aiosqlite.connect(DB_PATH) as db:
                        cur = await db.execute(
                            "UPDATE listings SET status = 'SOLD' WHERE id = ? AND status = 'ACTIVE'",
                            (ad["id"],)
                        )
                        await db.commit()
                        if cur.rowcount == 0:
                            continue

                    try:
                        author = await bot.get_chat(ad['user_id'])
                        tag = f"@{author.username}" if author.username else author.first_name
                    except Exception:
                        tag = str(ad['user_id'])
                    new_caption = build_post_text(ad, status="ПРОДАНО", author_tag=tag)

                    pairs = (ad["message_ids"] or "").split(",") if ad["message_ids"] else []
                    if not pairs and ad["channel"] and ad["message_id"]:
                        pairs = [f"{ad['channel']}:{ad['message_id']}"]
                    for pair in pairs:
                        pair = pair.strip()
                        if ":" not in pair:
                            continue
                        ch, mid = pair.rsplit(":", 1)
                        try:
                            await bot.edit_message_caption(
                                chat_id=ch, message_id=int(mid),
                                caption=new_caption, parse_mode="HTML"
                            )
                        except Exception:
                            try:
                                await bot.edit_message_text(
                                    chat_id=ch, message_id=int(mid),
                                    text=new_caption, parse_mode="HTML"
                                )
                            except Exception as e:
                                print(f"Ошибка редактирования {ch}/{mid}: {e}")

                    try:
                        await bot.send_message(
                            ad["user_id"],
                            f"У вашего объявления «{ad['model']}» кончился срок публикации. Хотите ли вы его продлить?",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="💸 Продлить", callback_data=f"renew_{ad['id']}")],
                                [InlineKeyboardButton(text="🗑️ Снять с продажи", callback_data=f"remove_{ad['id']}")]
                            ])
                        )
                    except Exception as e:
                        print(f"Не удалось уведомить {ad['user_id']}: {e}")
            except Exception as e:
                print(f"Ошибка в expire_loop: {e}")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        print("expire_loop остановлен")
        raise

@dp.callback_query(F.data.startswith("renew_"))
async def renew_cb(callback: types.CallbackQuery):
    lot_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE id = ? AND user_id = ?",
            (lot_id, user_id)
        ) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return

    new_expires = (datetime.now() + timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE listings SET status = 'ACTIVE', expires_at = ? WHERE id = ?",
            (new_expires, lot_id)
        )
        await db.commit()

    try:
        author = await bot.get_chat(ad['user_id'])
        tag = f"@{author.username}" if author.username else author.first_name
    except Exception:
        tag = str(ad['user_id'])
    new_caption = build_post_text(ad, status="АКТУАЛЬНО", author_tag=tag)

    pairs = (ad["message_ids"] or "").split(",") if ad["message_ids"] else []
    if not pairs and ad["channel"] and ad["message_id"]:
        pairs = [f"{ad['channel']}:{ad['message_id']}"]
    for pair in pairs:
        pair = pair.strip()
        if ":" not in pair:
            continue
        ch, mid = pair.rsplit(":", 1)
        try:
            await bot.edit_message_caption(
                chat_id=ch, message_id=int(mid),
                caption=new_caption, parse_mode="HTML"
            )
        except Exception:
            try:
                await bot.edit_message_text(
                    chat_id=ch, message_id=int(mid),
                    text=new_caption, parse_mode="HTML"
                )
            except Exception as e:
                print(f"Не удалось отредактировать {ch}/{mid}: {e}")

    try:
        await callback.message.edit_text("Публикация продлена на 7 дней.")
    except Exception:
        await callback.message.answer("Публикация продлена на 7 дней.")
    await callback.answer()

async def on_startup(bot: Bot):
    base_url = os.getenv("RENDER_EXTERNAL_URL")
    if base_url:
        await bot.set_webhook(
            f"{base_url}/webhook",
            secret_token=WEBHOOK_SECRET or None
        )
        print(f"Webhook установлен на {base_url}/webhook")
    else:
        print("RENDER_EXTERNAL_URL не задан")

async def health_check(request):
    return web.json_response({"status": "ok"})

async def main():
    await init_db()
    app = web.Application()
    app.router.add_get("/", health_check)
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET or None
    )
    webhook_requests_handler.register(app, path="/webhook")
    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    expire_task = asyncio.create_task(expire_loop())
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Сервер запущен на порту {port}")
    try:
        await asyncio.Event().wait()
    finally:
        expire_task.cancel()
        try:
            await expire_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(main())