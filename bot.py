import os
import asyncio
import aiohttp
import aiosqlite
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from centrifuge import Client

# ============================================================
# НАСТРОЙКИ
# ============================================================
TOKEN = os.getenv("BOT_TOKEN")
DONATEPAY_TOKEN = os.getenv("DONATEPAY_TOKEN")
DONATEPAY_USER_ID = os.getenv("DONATEPAY_USER_ID", "1409884")

PAYMENT_URL = "https://new.donatepay.ru/@1409884"
CHANNELS = ["@JustTechnic", "@baraholkamskkupiproday"]
DB_PATH = "ads.db"
ADMIN_IDS = [7614009397]

PRICE_RUB = 25.0
PAID_DAYS = 7
TRIAL_HOURS = 24

bot = Bot(token=TOKEN)
dp = Dispatcher()

class AdState(StatesGroup):
    waiting_for_text = State()

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ad_text TEXT NOT NULL,
                channel TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'АКТУАЛЬНО',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                is_trial INTEGER DEFAULT 0
            )
        """)
        for ddl in [
            "ALTER TABLE ads ADD COLUMN expires_at TEXT",
            "ALTER TABLE ads ADD COLUMN is_trial INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(ddl)
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_used (
                user_id INTEGER PRIMARY KEY,
                used_at TEXT NOT NULL
            )
        """)
        await db.commit()

async def has_used_trial(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM trial_used WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def mark_trial_used(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO trial_used (user_id, used_at) VALUES (?, ?)",
            (user_id, datetime.now().isoformat())
        )
        await db.commit()

async def save_pending_ad(user_id: int, ad_text: str, expires_at: str, is_trial: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ads (user_id, ad_text, channel, message_id, status, created_at, expires_at, is_trial) "
            "VALUES (?, ?, 'PENDING', 0, 'ОЖИДАНИЕ', ?, ?, ?)",
            (user_id, ad_text, datetime.now().isoformat(), expires_at, 1 if is_trial else 0)
        )
        await db.commit()

async def activate_pending_ad(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ads WHERE user_id = ? AND status = 'ОЖИДАНИЕ' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute(
            "UPDATE ads SET status = 'АКТУАЛЬНО' WHERE id = ?",
            (row["id"],)
        )
        await db.commit()
        return dict(row)

async def get_user_ads(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ads WHERE user_id = ? AND status = 'АКТУАЛЬНО' ORDER BY id DESC",
            (user_id,)
        ) as cur:
            return await cur.fetchall()

async def mark_as_sold(ad_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ads WHERE id = ? AND user_id = ? AND status = 'АКТУАЛЬНО'",
            (ad_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
        await db.execute("UPDATE ads SET status = 'ПРОДАНО' WHERE id = ?", (ad_id,))
        await db.commit()
        return dict(row)

async def admin_mark_sold(ad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ads WHERE id = ? AND status = 'АКТУАЛЬНО'", (ad_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
        await db.execute("UPDATE ads SET status = 'ПРОДАНО' WHERE id = ?", (ad_id,))
        await db.commit()
        return dict(row)

async def get_all_ads(page: int = 0, per_page: int = 5):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM ads WHERE status = 'АКТУАЛЬНО'") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT * FROM ads WHERE status = 'АКТУАЛЬНО' ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, page * per_page)
        ) as cur:
            rows = await cur.fetchall()
        return rows, total

async def get_expired_ads():
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ads WHERE status = 'АКТУАЛЬНО' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,)
        ) as cur:
            return await cur.fetchall()

async def expire_ad(ad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE ads SET status = 'ПРОДАНО' WHERE id = ?", (ad_id,))
        await db.commit()

# ============================================================
# WEBSOCKET (DONATEPAY)
# ============================================================
async def start_donatepay_listener():
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {DONATEPAY_TOKEN}"}
            async with session.post("https://donatepay.ru/api/v2/socket/token", headers=headers) as resp:
                if resp.status != 200:
                    print(f"Не удалось получить socket token: {resp.status}")
                    return
                token_data = await resp.json()
                socket_token = token_data.get("token")

        client = Client("wss://centrifugo.donatepay.ru:443/connection/websocket")
        await client.connect()
        print("WebSocket подключен.")

        async def on_publication(ctx):
            try:
                payload = ctx.pub.data
                notification = payload.get("result", {}).get("data", {}).get("data", {}).get("notification", {})
                if not notification:
                    return

                vars_data = notification.get("vars", {})
                amount = float(vars_data.get("sum", 0))
                comment = (vars_data.get("comment") or "").strip()
                donor_name = vars_data.get("name", "Unknown")

                print(f"DEBUG WS: Донат от {donor_name} на {amount}, комментарий: '{comment}'")

                if amount >= 20.0:
                    for word in comment.split():
                        if word.isdigit():
                            telegram_id = int(word)
                            activated_ad = await activate_pending_ad(telegram_id)
                            if activated_ad:
                                print(f"WS: Активировано объявление #{activated_ad['id']} для {telegram_id}")
                                for channel in CHANNELS:
                                    try:
                                        sent = await bot.send_message(
                                            channel,
                                            f"**АКТУАЛЬНО**\n\n{activated_ad['ad_text']}",
                                            parse_mode="Markdown"
                                        )
                                        async with aiosqlite.connect(DB_PATH) as db:
                                            await db.execute(
                                                "UPDATE ads SET channel = ?, message_id = ? WHERE id = ?",
                                                (channel, sent.message_id, activated_ad["id"])
                                            )
                                            await db.commit()
                                    except Exception as e:
                                        print(f"Ошибка публикации в {channel}: {e}")
                                try:
                                    await bot.send_message(
                                        telegram_id,
                                        f"✅ Оплата получена! Ваше объявление активировано на {PAID_DAYS} дней."
                                    )
                                except Exception:
                                    pass
                            break
            except Exception as e:
                print(f"Ошибка обработки WS: {e}")

        sub = client.new_subscription(f"$public:{DONATEPAY_USER_ID}")
        sub.on_publication(on_publication)
        await sub.subscribe()
        print(f"Подписка на канал $public:{DONATEPAY_USER_ID} оформлена.")

        while True:
            await asyncio.sleep(10)

    except Exception as e:
        print(f"Ошибка WebSocket: {e}")

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def main_menu(user_id: int = 0):
    kb = [
        [InlineKeyboardButton(text="Создать объявление", callback_data="create_ad")],
        [InlineKeyboardButton(text="Мои объявления", callback_data="my_ads")],
        [InlineKeyboardButton(text="Оплата", callback_data="pay")],
        [InlineKeyboardButton(text="Справка", callback_data="help")],
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton(text="Админ-панель", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def pay_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить 25 руб", url=PAYMENT_URL)],
        [InlineKeyboardButton(text="Назад", callback_data="main")],
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все объявления", callback_data="admin_ads_0")],
        [InlineKeyboardButton(text="В главное меню", callback_data="main")],
    ])

# ============================================================
# ХЕНДЛЕРЫ
# ============================================================
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Добро пожаловать! Выберите действие:",
                         reply_markup=main_menu(message.from_user.id))

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа.")
        return
    await message.answer("**Админ-панель**", reply_markup=admin_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "main")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "help")
async def help_cb(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="main")]])
    await callback.message.edit_text(
        "**Справка**\n\n"
        f"Первое объявление — бесплатно на {TRIAL_HOURS} ч. (пробный период).\n"
        f"Далее — {PRICE_RUB:.0f} руб. за {PAID_DAYS} дней публикации.\n"
        "После оплаты в комментарии к донату укажите ваш Telegram ID.\n"
        "Бот автоматически получит уведомление и активирует объявление.\n"
        "«Мои объявления» — список активных постов и кнопка «Продано».",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "pay")
async def pay_cb(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f"Для публикации на {PAID_DAYS} дней необходимо оплатить {PRICE_RUB:.0f} руб.\n"
        "Нажмите кнопку ниже:",
        reply_markup=pay_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "create_ad")
async def create_ad_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    used = await has_used_trial(user_id)
    hint = (f"Публикация платная — {PRICE_RUB:.0f} руб. за {PAID_DAYS} дней."
            if used else
            f"Первое объявление — бесплатно на {TRIAL_HOURS} ч.")
    await callback.message.edit_text(
        hint + "\n\nВведите текст объявления (до 500 символов):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="main")]])
    )
    await state.set_state(AdState.waiting_for_text)
    await callback.answer()

@dp.message(AdState.waiting_for_text)
async def receive_ad_text(message: types.Message, state: FSMContext):
    if not message.text or len(message.text) > 500:
        await message.answer("Текст слишком длинный. Максимум 500 символов.")
        return

    user_id = message.from_user.id
    used = await has_used_trial(user_id)

    if not used:
        await message.answer(f"Публикую пробное объявление на {TRIAL_HOURS} ч...")
        expires_at = (datetime.now() + timedelta(hours=TRIAL_HOURS)).isoformat()
        await save_pending_ad(user_id, message.text, expires_at, is_trial=True)
        activated_ad = await activate_pending_ad(user_id)
        if activated_ad:
            for channel in CHANNELS:
                try:
                    sent = await bot.send_message(channel, f"**АКТУАЛЬНО**\n\n{message.text}", parse_mode="Markdown")
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE ads SET channel = ?, message_id = ? WHERE id = ?",
                            (channel, sent.message_id, activated_ad["id"])
                        )
                        await db.commit()
                except Exception as e:
                    print(f"Ошибка публикации в {channel}: {e}")
        await mark_trial_used(user_id)
        await state.clear()
        await message.answer(f"Пробное объявление опубликовано на {TRIAL_HOURS} ч.")
        return

    expires_at = (datetime.now() + timedelta(days=PAID_DAYS)).isoformat()
    await save_pending_ad(user_id, message.text, expires_at, is_trial=False)
    await state.clear()
    await message.answer(
        f"Ваш текст сохранён и ожидает оплаты.\n\n"
        f"**Для публикации на {PAID_DAYS} дней оплатите {PRICE_RUB:.0f} руб.:**\n"
        f"1. Перейдите по кнопке «Оплатить».\n"
        f"2. В комментарии к донату укажите ваш Telegram ID: `{user_id}`\n"
        f"3. Бот автоматически получит уведомление и опубликует объявление.",
        reply_markup=pay_keyboard(), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "my_ads")
async def my_ads(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    ads = await get_user_ads(user_id)
    if not ads:
        await callback.message.edit_text(
            "У вас нет активных объявлений.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="main")]])
        )
        await callback.answer()
        return
    kb = []
    for ad in ads:
        short = ad["ad_text"][:30] + ("..." if len(ad["ad_text"]) > 30 else "")
        kb.append([InlineKeyboardButton(
            text=f"Продано #{ad['id']}: {short}",
            callback_data=f"sold_{ad['id']}"
        )])
    kb.append([InlineKeyboardButton(text="Назад", callback_data="main")])
    await callback.message.edit_text(
        "Ваши активные объявления.\nНажмите, чтобы отметить как **ПРОДАНО**:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("sold_"))
async def sold_cb(callback: types.CallbackQuery):
    ad_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    row = await mark_as_sold(ad_id, user_id)
    if not row:
        await callback.answer("Объявление не найдено или уже продано.", show_alert=True)
        return
    try:
        await bot.edit_message_text(
            chat_id=row["channel"], message_id=row["message_id"],
            text=f"**ПРОДАНО**\n\n{row['ad_text']}", parse_mode="Markdown"
        )
        note = "Статус обновлён на **ПРОДАНО** в канале."
    except Exception as e:
        print(f"Ошибка редактирования: {e}")
        note = "Статус в базе обновлён, но пост в канале отредактировать не удалось."
    await callback.message.edit_text(
        note, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="main")]])
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_menu")
async def admin_menu_cb(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("**Админ-панель**", reply_markup=admin_menu(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_ads_"))
async def admin_ads_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    page = int(callback.data.split("_")[2])
    ads, total = await get_all_ads(page=page)
    total_pages = max(1, (total + 4) // 5)
    if not ads:
        await callback.message.edit_text(
            "Активных объявлений нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="admin_menu")]
            ])
        )
        await callback.answer()
        return
    lines = [f"**Активные объявления** (стр. {page + 1}/{total_pages}, всего: {total})\n"]
    kb = []
    for ad in ads:
        short = ad["ad_text"][:40] + ("..." if len(ad["ad_text"]) > 40 else "")
        lines.append(
            f"**#{ad['id']}** | user `{ad['user_id']}` | {ad['channel']}\n"
            f"_{short}_\n"
        )
        kb.append([InlineKeyboardButton(text=f"Снять #{ad['id']}",
                                        callback_data=f"admin_sold_{ad['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"admin_ads_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд", callback_data=f"admin_ads_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="В админ-меню", callback_data="admin_menu")])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_sold_"))
async def admin_sold_cb(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ad_id = int(callback.data.split("_")[2])
    row = await admin_mark_sold(ad_id)
    if not row:
        await callback.answer("Объявление не найдено или уже снято.", show_alert=True)
        return
    try:
        await bot.edit_message_text(
            chat_id=row["channel"], message_id=row["message_id"],
            text=f"**ПРОДАНО**\n\n{row['ad_text']}", parse_mode="Markdown"
        )
        note = f"Объявление #{ad_id} снято, пост обновлён."
    except Exception as e:
        print(f"Ошибка редактирования: {e}")
        note = f"Объявление #{ad_id} снято в базе, но пост в канале не отредактирован."
    try:
        await bot.send_message(
            row["user_id"],
            f"Ваше объявление #{ad_id} снято администратором.\n\n_{row['ad_text'][:100]}_",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await callback.message.edit_text(
        note,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="К списку", callback_data="admin_ads_0")],
            [InlineKeyboardButton(text="В админ-меню", callback_data="admin_menu")],
        ])
    )
    await callback.answer()

async def expire_loop():
    while True:
        try:
            expired = await get_expired_ads()
            for ad in expired:
                await expire_ad(ad["id"])
                try:
                    await bot.edit_message_text(
                        chat_id=ad["channel"], message_id=ad["message_id"],
                        text=f"**ПРОДАНО**\n\n{ad['ad_text']}", parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"Ошибка редактирования #{ad['id']}: {e}")
                text = (
                    f"**Срок публикации ({PAID_DAYS} дней) истёк.**\n\n"
                    "Объявление переведено в статус ПРОДАНО.\n"
                    f"Чтобы продлить ещё на {PAID_DAYS} дней, оплатите ниже.\n\n"
                    f"Для подтверждения укажите в комментарии к донату ваш ID: `{ad['user_id']}`"
                )
                renew_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"Оплатить {PRICE_RUB:.0f} руб", url=PAYMENT_URL)],
                ])
                try:
                    await bot.send_message(ad["user_id"], text,
                                           reply_markup=renew_kb, parse_mode="Markdown")
                except Exception as e:
                    print(f"Не удалось уведомить {ad['user_id']}: {e}")
        except Exception as e:
            print(f"Ошибка в expire_loop: {e}")
        await asyncio.sleep(60)

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    await init_db()
    asyncio.create_task(expire_loop())
    asyncio.create_task(start_donatepay_listener())
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())