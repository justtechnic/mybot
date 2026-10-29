import os
import asyncio
import aiosqlite
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7614009397"))
CHANNELS = ["@JustTechnic", "@baraholkamskkupiproday"]
DB_PATH = "market.db"

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
                message_id INTEGER
            )
        """)
        await db.commit()

async def save_listing(user_id, data, status='PENDING_MODERATION', is_trial=False):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO listings (user_id, ad_type, category, model, memory, description, price, city, delivery, photos, status, created_at, is_trial)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get('ad_type'), data.get('category'), data.get('model'),
            data.get('memory'), data.get('description'), data.get('price'),
            data.get('city'), data.get('delivery'), ",".join(data.get('photos', [])),
            status, datetime.now(), is_trial
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
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def delivery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправлю", callback_data="del_yes")],
        [InlineKeyboardButton(text="❌ Не отправлю", callback_data="del_no")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

def back_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Добро пожаловать в JustTrade!", reply_markup=bottom_menu())
    await show_main_menu(message)

async def show_main_menu(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'ACTIVE'", (message.from_user.id,)) as cur:
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
    await callback.message.delete()
    await show_main_menu(callback.message)
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
    await state.update_data(category=cat_names.get(cat, cat))
    await state.set_state(AdForm.entering_model)
    await callback.message.edit_text(
        f"{cat_names.get(cat, cat)}.\n\nНапишите модель устройства и ключевую особенность.",
        reply_markup=back_menu_kb()
    )
    await callback.answer()

@dp.message(AdForm.entering_model)
async def process_model(message: types.Message, state: FSMContext):
    await state.update_data(model=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_memory)
    await message.answer(
        f"{data['category']}, 💾Память\n\n"
        "Напишите память устройства, формат ОЗУ/ПЗУ.\n"
        "Без текста Гб и без лишних символов.",
        reply_markup=back_menu_kb()
    )

@dp.message(AdForm.entering_memory)
async def process_memory(message: types.Message, state: FSMContext):
    await state.update_data(memory=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await message.answer(
        f"{data['category']}, 📃Описание\n\n"
        "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.\n"
        "Память уже указана, повторять не нужно.",
        reply_markup=back_menu_kb()
    )

@dp.message(AdForm.entering_description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_price)
    await message.answer(
        f"{data['category']}, 🪎Цена\n\n"
        "Укажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно",
        reply_markup=back_menu_kb()
    )

@dp.message(AdForm.entering_price)
async def process_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите только цифры.")
        return
    await state.update_data(price=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.entering_city)
    await message.answer(
        f"{data['category']}, 🌇Город\n\n"
        "Укажите город самовывоз - только название.\n"
        "Пример: Москва, Питер, Казань\n"
        "Никаких лишних символов указывать не нужно",
        reply_markup=back_menu_kb()
    )

@dp.message(AdForm.entering_city)
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    data = await state.get_data()
    await state.set_state(AdForm.choosing_delivery)
    await message.answer(
        f"{data['category']}, 🗃️Доставка\n\n"
        "Отправите ли вы устройство через какую либо транспортную компанию?",
        reply_markup=delivery_kb()
    )

@dp.callback_query(F.data.startswith("del_"), AdForm.choosing_delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    delivery = "Отправлю" if callback.data == "del_yes" else "Не отправлю"
    await state.update_data(delivery=delivery, photos=[])
    await state.set_state(AdForm.uploading_photos)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{data['category']}, 📸Фотографии или видео\n\n"
        "Покажите фото или видео товара, которое сейчас опубликуете.\n\n"
        "Фото обязательно!\n\n"
        "Фото и видео должны быть настоящими, а не из интернета, иначе отклоним запрос на публикацию.\n\n"
        "Как отправили все что хотели опубликовать - нажмите \"✅Готово!\"",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово!", callback_data="photos_done")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
        ])
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
    await message.answer(f"Загружено {len(photos)} фотографий из 10")

@dp.callback_query(F.data == "photos_done", AdForm.uploading_photos)
async def photos_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get('photos'):
        await callback.answer("Загрузите хотя бы одно фото!", show_alert=True)
        return
    await state.set_state(AdForm.preview)
    preview_text = (
        f"🔍Предпросмотр\n\n"
        f"Проверь текст и вложения на наличие ошибок. Если все нормально - отправь на модерацию.\n\n"
        f"✅АКТУАЛЬНО✅\n"
        f"#{data['category']}\n"
        f"#{data['city']}\n\n"
        f"💾Память: {data['memory']}\n\n"
        f"{data['description']}\n\n"
        f"🇷🇺{data['city']}\n"
        f"💸{data['price']}\n"
        f"🚛{data['delivery']}\n\n"
        f"🥷🏻{callback.from_user.first_name}"
    )
    await callback.message.answer_photo(
        photo=data['photos'][0],
        caption=preview_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💫 Отправка на модерацию", callback_data="send_moderation")],
            [InlineKeyboardButton(text="✒️ Сбросить объявление", callback_data="reset_ad")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "send_moderation", AdForm.preview)
async def send_moderation(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    lot_id = await save_listing(user_id, data, status='PENDING_MODERATION', is_trial=True)
    await callback.message.edit_caption(
        caption="Объявление отправлено на модерацию!\n\n"
                "Как только модератор примет пост - придёт уведомление вам в чат с ботом, а статус появится в разделе \"Мои объявления\"",
        reply_markup=None
    )
    admin_text = (
        f"🔔 Новое объявление на модерации #{lot_id}\n\n"
        f"Пользователь: {callback.from_user.first_name} (@{callback.from_user.username})\n"
        f"Тип: {data['ad_type']} | Категория: {data['category']}\n"
        f"Модель: {data['model']}\n"
        f"Память: {data['memory']}\n"
        f"Цена: {data['price']} | Город: {data['city']}\n"
        f"Доставка: {data['delivery']}\n"
        f"Описание: {data['description']}"
    )
    await bot.send_photo(
        chat_id=ADMIN_ID,
        photo=data['photos'][0],
        caption=admin_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Одобрено", callback_data=f"approve_{lot_id}")],
            [InlineKeyboardButton(text="❌ Не одобрено", callback_data=f"reject_{lot_id}")]
        ])
    )
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
        post_text = (
            f"✅АКТУАЛЬНО✅\n"
            f"#{row['category']}\n"
            f"#{row['city']}\n\n"
            f"💾Память: {row['memory']}\n\n"
            f"{row['description']}\n\n"
            f"🇷🇺{row['city']}\n"
            f"💸{row['price']}\n"
            f"🚛{row['delivery']}\n\n"
            f"🥷🏻{callback.from_user.first_name}"
        )
        photos = row['photos'].split(",") if row['photos'] else []
        for channel in CHANNELS:
            try:
                if photos:
                    sent = await bot.send_photo(chat_id=channel, photo=photos[0], caption=post_text)
                else:
                    sent = await bot.send_message(chat_id=channel, text=post_text)
                await db.execute(
                    "UPDATE listings SET status = 'ACTIVE', channel = ?, message_id = ? WHERE id = ?",
                    (channel, sent.message_id, lot_id)
                )
            except Exception as e:
                print(f"Ошибка публикации: {e}")
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
    await callback.message.edit_caption(caption="Объявление одобрено и опубликовано.")
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    lot_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM listings WHERE id = ?", (lot_id,)) as cur:
            row = await cur.fetchone()
        await db.execute("UPDATE listings SET status = 'REJECTED' WHERE id = ?", (lot_id,))
        await db.commit()
    if row:
        try:
            await bot.send_message(
                row[0],
                "Ваша публикация не одобрена👎, попробуйте заполнить анкету заново",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                    [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]
                ])
            )
        except Exception:
            pass
    await callback.message.edit_caption(caption="Объявление отклонено.")
    await callback.answer()

@dp.callback_query(F.data == "reset_ad")
async def reset_ad(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await show_main_menu(callback.message)
    await callback.answer()

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=bottom_menu())
    await show_main_menu(message)

async def on_startup(bot: Bot):
    base_url = os.getenv("RENDER_EXTERNAL_URL")
    if base_url:
        await bot.set_webhook(f"{base_url}/webhook")
        print(f"Webhook установлен на {base_url}/webhook")
    else:
        print("RENDER_EXTERNAL_URL не задан")

async def main():
    await init_db()
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Сервер запущен на порту {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())