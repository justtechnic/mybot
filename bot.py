# ============================================================
#  JustTrade — bot.py  (финальная версия)
#  ЧАСТЬ 1/4. Склейте части 1 → 2 → 3 → 4 в один файл bot.py
# ============================================================
import os
import re
import time
import asyncio
import sqlite3
import traceback
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict

import aiosqlite
from aiohttp import web

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
    FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, KeyboardButton, LabeledPrice, Message,
    PreCheckoutQuery, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


def _env(name: str, default: str = "") -> str:
    """Читает переменную окружения и обрезает случайные пробелы."""
    return (os.getenv(name) or default).strip()


TOKEN = _env("BOT_TOKEN")
ADMIN_ID = int(_env("ADMIN_ID", "7614009397"))
CHANNELS = ["@JustTechnic", "@baraholkamskkupiproday"]
FLEA_CHAT = _env("FLEA_CHAT", "@baraholkamskkupiproday")
DB_PATH = _env("DB_PATH", "market.db")
SUPPORT_CONTACT = "@justtechnic1"
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
DONATEPAY_PAGE_URL = _env("DONATEPAY_PAGE_URL", "https://new.donatepay.ru/@1409884")

STARS_PRICE = 25
MAX_PHOTOS = 10
MAX_CAPTION = 1024
LIMIT_MODEL = 200
LIMIT_MEMORY = 32
LIMIT_DESCRIPTION = 700
LIMIT_CITY = 100
LIMIT_KEYWORD = 50
MAX_SUBSCRIPTIONS = 20

MENU_MAIN = "🏡 Главное меню"
MENU_PROFILE = "🥷🏻 Профиль"
MENU_LOTS = "📚 Мои лоты"
MENU_BUTTONS = {MENU_MAIN, MENU_PROFILE, MENU_LOTS}

SUBCATEGORIES = {
    "Аудио": ["Наушники", "Колонка", "Микрофон"],
    "Запчасти/лот": ["Запчасти", "Лот"],
    "Прочее": ["Аксессуары", "Приставка", "Дисплей", "Клавиатура", "Мышка"],
}
NO_MEMORY_CATEGORIES = {"Прочее", "Услуги", "Запчасти/лот", "Аудио"}
CATEGORY_NAMES = {
    "phone": "Телефон", "tablet": "Планшет", "laptop": "Ноутбук",
    "audio": "Аудио", "parts": "Запчасти/лот", "services": "Услуги", "other": "Прочее",
}
AD_TYPE_NAMES = {"sell": "💵 Продажа", "buy": "🕵🏻 Покупка", "exchange": "🔁 Обмен"}

if not TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

bot = Bot(token=TOKEN, default=DefaultBotProperties(link_preview_is_disabled=True))
dp = Dispatcher(storage=MemoryStorage())


class AdForm(StatesGroup):
    choosing_type = State()
    choosing_category = State()
    choosing_subcategory = State()
    entering_model = State()
    entering_memory = State()
    entering_description = State()
    entering_price = State()
    entering_city = State()
    choosing_delivery = State()
    uploading_photos = State()
    preview = State()
    waiting_for_restore_db = State()
    waiting_for_subscription_keyword = State()


# ---------------------- БАЗОВЫЕ ХЕЛПЕРЫ ----------------------
def row_get(row, key, default=None):
    """Безопасное чтение поля из aiosqlite.Row / dict / tuple."""
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def escape_html(text) -> str:
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text or "")


def tg_len(text: str) -> int:
    """Длина строки так, как её считает Telegram (UTF-16)."""
    return len((text or "").encode("utf-16-le")) // 2


def make_author_tag(username: str, first_name: str, user_id: int) -> str:
    if username:
        return f'<a href="https://t.me/{username}">@{escape_html(username)}</a>'
    return f'<a href="tg://user?id={user_id}">{escape_html(first_name or "Продавец")}</a>'


async def get_author_tag(user_id: int) -> str:
    try:
        chat = await bot.get_chat(user_id)
        return make_author_tag(chat.username, chat.first_name, user_id)
    except Exception:
        return f'<a href="tg://user?id={user_id}">Продавец</a>'


async def is_verified(user_id: int) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT verified FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False


def fsm_for_user(user_id: int) -> FSMContext:
    """FSM-контекст ДРУГОГО пользователя (подтверждение оплаты админом)."""
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


async def get_clean_text(message: types.Message, limit: int):
    """Единая проверка ввода: None → пустая строка → лимит. None = ввод не принят."""
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустое сообщение. Напишите, пожалуйста, текстом.")
        return None
    if len(text) > limit:
        await message.answer(f"⚠️ Слишком длинно: {len(text)} симв. Максимум — {limit}.")
        return None
    return text


class TextAnswer(BaseFilter):
    """Только осмысленный текст: не None, не команда, не кнопка нижнего меню."""
    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        return text not in MENU_BUTTONS


# ---------------------- ТЕКСТ ПОСТА ----------------------
def build_post_text(ad, status="АКТУАЛЬНО", author_tag=None, author_id=None,
                    verified=False, max_len=MAX_CAPTION) -> str:
    model = str(row_get(ad, "model", "—"))
    price = str(row_get(ad, "price", "—"))
    city = str(row_get(ad, "city", "—"))
    category = str(row_get(ad, "category", "—"))
    description = str(row_get(ad, "description", ""))
    delivery = str(row_get(ad, "delivery", "—"))
    memory = row_get(ad, "memory")
    subcat = row_get(ad, "subcategory")
    uid = row_get(ad, "user_id", author_id)

    if author_tag:
        tag = author_tag
    elif author_id:
        tag = f'<a href="tg://user?id={author_id}">Продавец</a>'
    else:
        tag = escape_html(str(uid))

    if verified:
        suffix = " ✅" if status == "ПРОДАНО" else " ✅ Проверенный продавец"
    else:
        suffix = ""

    if status == "ПРОДАНО":
        return (
            f"❌ПРОДАНО❌\n"
            f"📦 {escape_html(model)}\n"
            f"💸 {escape_html(price)}\n"
            f"🇷🇺 {escape_html(city)}\n\n"
            f"🥷🏻 {tag}{suffix}"
        )

    header = "✅АКТУАЛЬНО✅"
    if bool(row_get(ad, "is_promoted", 0)):
        header = "🔥 ТОП\n" + header

    head = f"{header}\n#{category}\n"
    if subcat:
        head += f"#{subcat}\n"
    head += f"#{city}\n\n📦 {model}\n\n"
    if memory and memory != "—":
        head += f"💾Память: {memory}\n\n"
    tail = f"\n\n🇷🇺{city}\n💸{price}\n🚛{delivery}\n\n🥷🏻"

    overhead = tg_len(head) + tg_len(tail) + tg_len(_strip_tags(tag)) + tg_len(suffix)
    budget = max(0, max_len - overhead)
    if tg_len(description) > budget:
        description = description[:max(0, budget - 1)].rstrip() + "…"

    return escape_html(head) + escape_html(description) + escape_html(tail) + tag + suffix


def iter_first_posts(ad):
    """Первое сообщение каждого канала/чата (у медиагрупп — только первое фото)."""
    raw = row_get(ad, "message_ids", "") or ""
    pairs = [p.strip() for p in raw.split(",") if p.strip()]
    if not pairs:
        ch, mid = row_get(ad, "channel"), row_get(ad, "message_id")
        if ch and mid:
            pairs = [f"{ch}:{mid}"]
    seen = set()
    for pair in pairs:
        if ":" not in pair:
            continue
        ch, mid = pair.rsplit(":", 1)
        if ch in seen:
            continue
        seen.add(ch)
        try:
            yield ch, int(mid)
        except ValueError:
            continue


async def edit_post_caption_only_first(ad, caption: str, parse_mode: str = "HTML"):
    for ch, mid in iter_first_posts(ad):
        try:
            await bot.edit_message_caption(chat_id=ch, message_id=mid,
                                           caption=caption, parse_mode=parse_mode)
            continue
        except Exception as e:
            if "message is not modified" in str(e).lower():
                continue
        try:
            await bot.edit_message_text(chat_id=ch, message_id=mid,
                                        text=caption, parse_mode=parse_mode)
        except Exception as e:
            print(f"DEBUG: edit post {ch}/{mid} failed: {e}")


async def pin_first_posts(ad):
    for ch, mid in iter_first_posts(ad):
        try:
            await bot.pin_chat_message(chat_id=ch, message_id=mid, disable_notification=True)
        except Exception as e:
            print(f"Не удалось закрепить {ch}/{mid}: {e}")


async def unpin_first_posts(ad):
    for ch, mid in iter_first_posts(ad):
        try:
            await bot.unpin_chat_message(chat_id=ch, message_id=mid)
        except Exception:
            pass


async def rebuild_caption(ad, status="АКТУАЛЬНО", promoted=None, verified=None) -> str:
    ad_dict = dict(ad)
    if promoted is not None:
        ad_dict["is_promoted"] = 1 if promoted else 0
    uid = ad_dict["user_id"]
    tag = await get_author_tag(uid)
    ver = await is_verified(uid) if verified is None else verified
    caption = build_post_text(ad_dict, status=status, author_tag=tag, author_id=uid, verified=ver)
    await edit_post_caption_only_first(ad_dict, caption)
    return caption


async def safe_edit_or_caption(message: types.Message, text: str, reply_markup=None):
    try:
        if getattr(message, "caption", None) is not None:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            pass


async def safe_append_and_edit(message: types.Message, suffix: str):
    try:
        if getattr(message, "caption", None) is not None:
            await message.edit_caption(caption=(message.caption or "") + suffix)
        elif getattr(message, "text", None):
            await message.edit_text(message.text + suffix)
    except Exception:
        pass


# ---------------------- БД ----------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, ad_type TEXT, category TEXT, subcategory TEXT,
                model TEXT, memory TEXT, description TEXT, price TEXT, city TEXT,
                delivery TEXT, photos TEXT, status TEXT DEFAULT 'DRAFT',
                created_at TIMESTAMP, expires_at TIMESTAMP, is_trial BOOLEAN DEFAULT 0,
                channel TEXT, message_id INTEGER, message_ids TEXT, charge_id TEXT,
                is_promoted INTEGER DEFAULT 0, promoted_until TIMESTAMP,
                seller_verified INTEGER DEFAULT 0, full_caption TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, first_seen TIMESTAMP, verified INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, keyword TEXT NOT NULL, created_at TIMESTAMP
            )
        """)
        await db.commit()

        async with db.execute("PRAGMA table_info(listings)") as cur:
            existing = {row[1] for row in await cur.fetchall()}
        required = {
            "subcategory": "TEXT", "message_ids": "TEXT", "charge_id": "TEXT",
            "is_promoted": "INTEGER DEFAULT 0", "promoted_until": "TIMESTAMP",
            "seller_verified": "INTEGER DEFAULT 0", "full_caption": "TEXT",
            "ad_type": "TEXT", "model": "TEXT", "memory": "TEXT", "description": "TEXT",
            "price": "TEXT", "city": "TEXT", "delivery": "TEXT", "photos": "TEXT",
            "status": "TEXT DEFAULT 'DRAFT'", "created_at": "TIMESTAMP",
            "expires_at": "TIMESTAMP", "is_trial": "BOOLEAN DEFAULT 0",
            "channel": "TEXT", "message_id": "INTEGER",
        }
        for col, coltype in required.items():
            if col not in existing:
                try:
                    await db.execute(f"ALTER TABLE listings ADD COLUMN {col} {coltype}")
                    await db.commit()
                    print(f"DEBUG: added listings.{col}")
                except sqlite3.OperationalError as e:
                    print(f"DEBUG: ALTER {col} failed: {e}")

        async with db.execute("PRAGMA table_info(users)") as cur:
            ucols = {row[1] for row in await cur.fetchall()}
        if "verified" not in ucols:
            try:
                await db.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
                await db.commit()
            except sqlite3.OperationalError:
                pass

        await db.execute("CREATE INDEX IF NOT EXISTS idx_listings_user ON listings(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id)")
        await db.commit()
    print("DEBUG: init_db finished")


async def save_listing(user_id, data, status="PENDING_MODERATION", is_trial=False):
    async with aiosqlite.connect(DB_PATH) as db:
        duration = timedelta(hours=24) if is_trial else timedelta(days=7)
        now = datetime.now()
        cursor = await db.execute("""
            INSERT INTO listings (
                user_id, ad_type, category, subcategory, model, memory, description,
                price, city, delivery, photos, status, created_at,
                expires_at, is_trial, charge_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get("ad_type"), data.get("category"), data.get("subcategory"),
            data.get("model"), data.get("memory"), data.get("description"), data.get("price"),
            data.get("city"), data.get("delivery"), ",".join(data.get("photos") or []),
            status, now.isoformat(), (now + duration).isoformat(),
            1 if is_trial else 0, data.get("charge_id"),
        ))
        await db.commit()
        return cursor.lastrowid


# ---------------------- КЛАВИАТУРЫ ----------------------
def bottom_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_MAIN), KeyboardButton(text=MENU_PROFILE)],
            [KeyboardButton(text=MENU_LOTS)],
        ],
        resize_keyboard=True, is_persistent=True,
    )


def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои товары", callback_data="my_ads")],
        [InlineKeyboardButton(text="🔔 Подписки", callback_data="subs_menu")],
        [InlineKeyboardButton(text="🥷🏻 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="📣 Канал", url="https://t.me/JustTechnic")],
    ])


def publish_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌱 Бесплатно", callback_data="pub_free")],
        [InlineKeyboardButton(text="💳 Платно", callback_data="pub_paid")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def ad_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Продажа", callback_data="adtype_sell")],
        [InlineKeyboardButton(text="🕵🏻 Покупка", callback_data="adtype_buy")],
        [InlineKeyboardButton(text="🔁 Обмен", callback_data="adtype_exchange")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="pub_free")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
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
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def subcategory_kb(subcats: list):
    kb = [[InlineKeyboardButton(text=name, callback_data=f"subcat_{name}")] for name in subcats]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_category")])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def delivery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправлю", callback_data="del_yes")],
        [InlineKeyboardButton(text="❌ Не отправлю", callback_data="del_no")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_city")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def step_kb(back_callback: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data=back_callback)],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def photos_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово!", callback_data="photos_done")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_delivery")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def preview_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💫 Отправка на модерацию", callback_data="send_moderation")],
        [InlineKeyboardButton(text="↩️ Назад к фото", callback_data="back_to_photos")],
        [InlineKeyboardButton(text="✒️ Сбросить объявление", callback_data="reset_ad")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def subscriptions_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить подписку", callback_data="sub_add")],
        [InlineKeyboardButton(text="📋 Мои подписки", callback_data="sub_list")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def help_menu_kb(with_back: bool = True):
    kb = [
        [InlineKeyboardButton(text="📄 Правила публикации", callback_data="help_rules")],
        [InlineKeyboardButton(text="❔ Как выставить?", callback_data="help_howto")],
        [InlineKeyboardButton(text="⚠️ Отказ в публикации", callback_data="help_reject")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
    ]
    if with_back:
        kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


PROMOTE_TEXT = (
    "🚀 <b>Поднятие объявления в топ</b>\n\n"
    "Объявление будет закреплено в канале на 7 дней с пометкой 🔥 ТОП.\n\n"
    "Выберите способ оплаты:"
)


def promote_pay_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="25⭐️", callback_data="promote_pay_stars")],
        [InlineKeyboardButton(text="25💸", callback_data="promote_pay_donatepay")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="my_ads")],
    ])


PHOTOS_PROMPT = (
    "📸Фотографии\n\n"
    "Покажите фото товара, которое сейчас опубликуете.\n\n"
    "Фото обязательно! До 10 штук.\n\n"
    "Фото должны быть настоящими, а не из интернета, иначе отклоним запрос на публикацию.\n\n"
    "Как отправили всё, что хотели опубликовать — нажмите «✅ Готово!»"
)
CATEGORY_PROMPT = (
    "Выберите тип устройства — для каждого есть подсказка:\n\n"
    "📱 Телефон — кнопочные, сенсорные, раскладушки (Nokia, iPhone, Samsung)\n"
    "📲 Планшет — iPad, Xiaomi pad, Samsung pad\n"
    "💻 Ноутбук — нетбук, лэптоп, ноутбук (Asus, Dell и др)\n"
    "🎧 Аудио — наушники, колонки, саундбары\n"
    "🧩 Запчасти/лот — дисплей, АКБ и тд, несколько разных вещей\n"
    "🔨 Услуги — ремонт техники\n"
    "🎮 Прочее — Xbox, игры, телевизор и тд"
)
FORM_INTRO = (
    "Нужно выбрать, что ты хочешь сделать с товаром.\n"
    "Далее — по пунктам: тип товара, описание, цена, город, доставка, фото товара "
    "(при покупке можно без фото).\n\n"
    "Отмена любых действий: /cancel"
)


# ---------------------- МИДЛВАРИ ----------------------
class PrivateOnlyMiddleware(BaseMiddleware):
    """🔒 Бот отвечает ТОЛЬКО в личных сообщениях.
    В группах и каналах — полное молчание. Публикация/редактирование/закреп
    идут исходящими запросами bot.send_* и через dispatcher не проходят."""

    async def __call__(self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
                       event: Any, data: Dict[str, Any]) -> Any:
        chat = None
        if isinstance(event, types.Message):
            chat = event.chat
        elif isinstance(event, types.CallbackQuery):
            chat = getattr(event.message, "chat", None)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)


class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, cooldown: float = 2.0):
        self.cooldown = cooldown
        self.last_press: Dict[tuple, float] = {}

    async def __call__(self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
                       event: Any, data: Dict[str, Any]) -> Any:
        if isinstance(event, types.CallbackQuery) and event.from_user:
            if event.from_user.id == ADMIN_ID:
                return await handler(event, data)
            key = (event.from_user.id, event.data)
            now = time.time()
            if now - self.last_press.get(key, 0) < self.cooldown:
                try:
                    await event.answer()
                except Exception:
                    pass
                return
            self.last_press[key] = now
            if len(self.last_press) > 1000:
                cutoff = now - 60
                self.last_press = {k: v for k, v in self.last_press.items() if v > cutoff}
        return await handler(event, data)


class AdminErrorMiddleware(BaseMiddleware):
    def __init__(self):
        self._last_sent: Dict[str, float] = {}

    async def __call__(self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
                       event: Any, data: Dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:
            tb = traceback.format_exc()[-1500:]
            key = f"{type(e).__name__}:{str(e)[:120]}"
            now = time.time()
            if now - self._last_sent.get(key, 0) > 60:
                self._last_sent[key] = now
                uid = getattr(getattr(event, "from_user", None), "id", "—")
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Ошибка в боте</b>\n\n"
                        f"User: <code>{uid}</code>\n"
                        f"<code>{escape_html(str(e))}</code>\n\n"
                        f"<pre>{escape_html(tb)}</pre>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            raise

# ============================================================
#  ЧАСТЬ 2/4. Глобальные хендлеры регистрируются ПЕРВЫМИ,
#  чтобы кнопки меню и /cancel не «съедались» шагами анкеты.
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
            (message.from_user.id, datetime.now().isoformat()),
        )
        await db.commit()
    await message.answer("Добро пожаловать в JustTrade!", reply_markup=bottom_menu())
    await show_main_menu(message)


async def show_main_menu(message: types.Message, user_id: int = None, first_name: str = None):
    uid = user_id or message.from_user.id
    name = first_name or message.from_user.first_name or "друг"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'ACTIVE'", (uid,)
        ) as cur:
            count = (await cur.fetchone())[0]
    await message.answer(
        f"Добрый день, {name}!\n\n"
        f"JustTrade — не просто бот, а лучший помощник в продаже техники;)\n\n"
        f"Ваши объявления: {count}",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=bottom_menu())
    await show_main_menu(message)


@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    await _send_profile(message, message.from_user)


@dp.message(Command("my_lots"))
async def cmd_my_lots(message: types.Message):
    await _send_my_ads(message, message.from_user.id)


@dp.message(Command("subs"))
async def cmd_subs(message: types.Message):
    await message.answer(
        "🔔 <b>Подписки на новые объявления</b>\n\n"
        "Подпишитесь на ключевые слова — и бот пришлёт уведомление, "
        "когда появится подходящее объявление.",
        reply_markup=subscriptions_menu_kb(), parse_mode="HTML",
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("❓ <b>Помощь</b>\n\nВыберите раздел:",
                         reply_markup=help_menu_kb(with_back=False), parse_mode="HTML")


# ---------- кнопки нижнего меню (до FSM!) ----------
@dp.message(F.text == MENU_MAIN)
async def go_main(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)


@dp.message(F.text == MENU_PROFILE)
async def profile_message(message: types.Message, state: FSMContext):
    await state.clear()
    await _send_profile(message, message.from_user)


@dp.message(F.text == MENU_LOTS)
async def my_lots_message(message: types.Message, state: FSMContext):
    await state.clear()
    await _send_my_ads(message, message.from_user.id)


@dp.callback_query(F.data == "main_menu")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message, callback.from_user.id, callback.from_user.first_name)
    await callback.answer()


# ---------- ПРОФИЛЬ ----------
async def build_profile_text(user_id: int, first_name: str, username: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT first_seen, verified FROM users WHERE user_id = ?", (user_id,)) as cur:
            u = await cur.fetchone()
        first_seen = (row_get(u, "first_seen", "") or "")[:10] or "—"
        verified = row_get(u, "verified", 0)

        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'ACTIVE'", (user_id,)
        ) as cur:
            active = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'SOLD'", (user_id,)
        ) as cur:
            sold = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT model, channel, message_id FROM listings "
            "WHERE user_id = ? AND status = 'ACTIVE' ORDER BY id DESC LIMIT 30", (user_id,)
        ) as cur:
            ads = await cur.fetchall()

    ads_lines = ""
    for ad in ads:
        safe_model = escape_html(row_get(ad, "model", "—"))
        ch, mid = row_get(ad, "channel"), row_get(ad, "message_id")
        if ch and mid:
            link = f"https://t.me/{str(ch).lstrip('@')}/{mid}"
            ads_lines += f'• <a href="{link}">{safe_model}</a>\n'
        else:
            ads_lines += f"• {safe_model}\n"

    username_str = f"@{username}" if username else "нет"
    badge = " ✅" if verified else ""
    return (
        f"👤 {escape_html(first_name)}{badge}\n"
        f"Профиль в JustTrade создан {first_seen}\n"
        f"Объявления: {active} актуально\n"
        f"Продано: {sold}\n"
        f"Телеграмм: {username_str}\n\n"
        f"Сейчас в канале:\n{ads_lines or 'нет активных объявлений'}"
    )


async def _send_profile(target_message: types.Message, user):
    text = await build_profile_text(user.id, user.first_name, user.username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
        [InlineKeyboardButton(text="🛡 Верификация (25 ⭐️)", callback_data="verify_start")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])
    await target_message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "profile")
async def cb_profile(callback: types.CallbackQuery):
    await _send_profile(callback.message, callback.from_user)
    await callback.answer()


# ---------- МОИ ЛОТЫ ----------
async def _send_my_ads(target_message: types.Message, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE user_id = ? AND status = 'ACTIVE' ORDER BY id DESC LIMIT 20",
            (user_id,),
        ) as cur:
            ads = await cur.fetchall()

    if not ads:
        await target_message.answer("У вас нет активных объявлений.", reply_markup=main_menu_kb())
        return

    lines = "\n".join(
        f"• {row_get(ad, 'model', '—')} — {row_get(ad, 'price', '—')} руб." for ad in ads
    )
    kb = []
    for ad in ads:
        model = row_get(ad, "model", "—")
        price = row_get(ad, "price", "—")
        kb.append([InlineKeyboardButton(text=f"{model} ({price} руб.)", callback_data=f"lot_{ad['id']}")])
        if not bool(row_get(ad, "is_promoted", 0)):
            kb.append([InlineKeyboardButton(
                text=f"🚀 Поднять в топ #{ad['id']} (25 ⭐️)",
                callback_data=f"promote_lot_{ad['id']}",
            )])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    await target_message.answer(
        f"📃 Мои объявления\n{len(ads)} в канале\n\n{lines}\n\n"
        "Нажми на объявление ниже — там все функции.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


@dp.callback_query(F.data == "my_ads")
async def cb_my_ads(callback: types.CallbackQuery):
    await _send_my_ads(callback.message, callback.from_user.id)
    await callback.answer()


# ---------- ВЫБОР СПОСОБА ПУБЛИКАЦИИ ----------
@dp.callback_query(F.data == "publish")
async def cb_publish(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message, "Выберите способ публикации", publish_type_kb())
    await callback.answer()


@dp.callback_query(F.data == "pub_free")
async def cb_pub_free(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_or_caption(
        callback.message,
        "Отлично! Ваше объявление будет выставлено бесплатно на 24 часа, после чего оно будет "
        "автоматически снято с продажи. Если вы захотите продлить срок публикации, "
        "то это будет стоить 25 руб или 25 звёзд на 7 дней.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Продолжить", callback_data="free_continue")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "free_continue")
async def cb_free_continue(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(is_trial=True, paid=False, moderation_sent=False)
    await safe_edit_or_caption(callback.message, FORM_INTRO, ad_type_kb())
    await callback.answer()


@dp.callback_query(F.data == "pub_paid")
async def cb_pub_paid(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(paid_context="new", moderation_sent=False)
    await safe_edit_or_caption(
        callback.message,
        "Отлично! Ваше объявление будет выставлено на 7 дней с публикацией в истории у Владельца, "
        "после окончания срока объявление будет снято с продажи, но его можно будет продлить.\n\n"
        "Выберите способ оплаты:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="25⭐️", callback_data="pay_stars")],
            [InlineKeyboardButton(text="25💸", callback_data="pay_donatepay")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
        ]),
    )
    await callback.answer()


# ---------- ОПЛАТА: STARS ----------
@dp.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    context = data.get("paid_context", "new")
    if context == "renew":
        lot_id = data.get("renew_lot_id")
        if not lot_id:
            await callback.answer("Объявление не найдено.", show_alert=True)
            return
        await state.update_data(moderation_sent=False)
        payload = f"renew_{lot_id}_{callback.from_user.id}"
        title = "Продление объявления на 7 дней"
    else:
        payload = f"pub_{callback.from_user.id}"
        title = "Публикация объявления на 7 дней"
    await bot.send_invoice(
        chat_id=callback.from_user.id, title=title,
        description="JustTrade — размещение объявления",
        payload=payload, currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="",
    )
    await callback.answer()


# ---------- ОПЛАТА: DONATEPAY ----------
@dp.callback_query(F.data == "pay_donatepay")
async def cb_pay_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_initiated=True, donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "⚠️ Бот не может автоматически проверить пожертвование, поэтому его проверяет "
        "Владелец бота @JustTechnic1. Для публикации объявления потребуется:\n\n"
        "1) Отправить донат по ссылке ниже\n"
        "2) В строке имя укажите ваш никнейм/юзернейм\n"
        "3) После оплаты нажмите «✅ Готово» — админ проверит поступление и подтвердит.\n\n"
        "Обычно проверка занимает несколько минут.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "donatepay_done")
async def cb_donatepay_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("donatepay_pending"):
        await callback.answer("Заявка уже отправлена, ожидайте проверки.", show_alert=True)
        return
    context = data.get("paid_context", "new")
    lot_id = data.get("renew_lot_id") or 0
    if context == "renew" and not lot_id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return

    uid = callback.from_user.id
    username_str = f"@{callback.from_user.username}" if callback.from_user.username else "нет username"
    text = (
        f"💰 <b>Заявка DonatePay ({'продление' if context == 'renew' else 'публикация'})</b>\n\n"
        f"Пользователь: {escape_html(callback.from_user.first_name)} ({username_str})\n"
        f"Telegram ID: <code>{uid}</code>\n"
        f"Объявление: <code>{lot_id or 'новое'}</code>\n\n"
        f"Сверьте никнейм с полем «Имя» в кабинете DonatePay.\n"
        f"Проверьте поступление 25₽ и подтвердите."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_confirm_{uid}_{context}_{lot_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")],
    ])
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        print(f"Не удалось отправить заявку админу: {e}")
        await callback.answer("Ошибка, напишите в поддержку.", show_alert=True)
        return

    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(callback.message, "✅ Заявка отправлена админу. Ожидайте подтверждения.")
    await callback.answer()


# ---------- ПРОДВИЖЕНИЕ (ТОП) ----------
@dp.callback_query(F.data.startswith("promote_lot_"))
async def cb_promote(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    lot_id = int(parts[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE id = ? AND user_id = ? AND status = 'ACTIVE'",
            (lot_id, callback.from_user.id),
        ) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await state.update_data(promote_lot_id=lot_id, paid_context="promote", donatepay_pending=False)
    await safe_edit_or_caption(callback.message, PROMOTE_TEXT, promote_pay_kb())
    await callback.answer()


@dp.callback_query(F.data == "promote_pay_stars")
async def cb_promote_stars(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lot_id = data.get("promote_lot_id")
    if not lot_id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.from_user.id, title="Поднятие объявления в топ",
        description="Закрепление в канале на 7 дней + пометка ТОП",
        payload=f"promote_{lot_id}_{callback.from_user.id}", currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="",
    )
    await callback.answer()


@dp.callback_query(F.data == "promote_pay_donatepay")
async def cb_promote_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "⚠️ Для поднятия в топ отправьте донат 25₽ по ссылке ниже, "
        "в поле «Имя» укажите свой юзернейм и нажмите «✅ Готово».",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="promote_donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="promote_back")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "promote_back")
async def cb_promote_back(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("promote_lot_id"):
        await safe_edit_or_caption(callback.message, PROMOTE_TEXT, promote_pay_kb())
    else:
        await safe_edit_or_caption(callback.message, "Объявление не найдено.", main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "promote_donatepay_done")
async def cb_promote_donatepay_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("donatepay_pending"):
        await callback.answer("Заявка уже отправлена, ожидайте проверки.", show_alert=True)
        return
    lot_id = data.get("promote_lot_id")
    if not lot_id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    uid = callback.from_user.id
    username_str = f"@{callback.from_user.username}" if callback.from_user.username else "нет username"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_promote_confirm_{uid}_{lot_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")],
    ])
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Заявка DonatePay (ТОП)</b>\n\n"
            f"Пользователь: {escape_html(callback.from_user.first_name)} ({username_str})\n"
            f"Telegram ID: <code>{uid}</code>\nОбъявление #{lot_id}\n\n"
            f"Проверьте поступление 25₽ и подтвердите.",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        print(f"Не удалось отправить заявку админу: {e}")
        await callback.answer("Ошибка, напишите в поддержку.", show_alert=True)
        return
    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(
        callback.message,
        "✅ Заявка отправлена админу. Как только оплата подтвердится — объявление будет закреплено.",
    )
    await callback.answer()


# ---------- ВЕРИФИКАЦИЯ ----------
@dp.callback_query(F.data == "verify_start")
async def cb_verify_start(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(paid_context="verify", donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "🛡 <b>Верификация продавца</b>\n\n"
        "После оплаты в вашем профиле появится галочка «✅ Проверенный продавец».\n\n"
        "Выберите способ оплаты:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="25⭐️", callback_data="verify_stars")],
            [InlineKeyboardButton(text="25💸", callback_data="verify_donatepay")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="profile")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "verify_stars")
async def cb_verify_stars(callback: types.CallbackQuery):
    await bot.send_invoice(
        chat_id=callback.from_user.id, title="Верификация продавца",
        description="Галочка «Проверенный продавец» в профиле",
        payload=f"verify_{callback.from_user.id}", currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="",
    )
    await callback.answer()


@dp.callback_query(F.data == "verify_donatepay")
async def cb_verify_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "⚠️ Для верификации через DonatePay отправьте донат и нажмите «✅ Готово».",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="verify_donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="verify_start")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "verify_donatepay_done")
async def cb_verify_donatepay_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("donatepay_pending"):
        await callback.answer("Заявка уже отправлена.", show_alert=True)
        return
    uid = callback.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_verify_confirm_{uid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")],
    ])
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🛡 <b>Заявка на верификацию (DonatePay)</b>\n\n"
            f"Пользователь: {escape_html(callback.from_user.first_name)} "
            f"(@{callback.from_user.username or 'нет'})\n"
            f"ID: <code>{uid}</code>\n\nПодтвердите оплату 25₽.",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        print(f"Ошибка: {e}")
        await callback.answer("Ошибка.", show_alert=True)
        return
    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(callback.message, "✅ Заявка отправлена админу.")
    await callback.answer()


# ---------- ПОДТВЕРЖДЕНИЯ АДМИНА ----------
async def mark_verified(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
            (user_id, datetime.now().isoformat()),
        )
        await db.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE user_id = ? AND status = 'ACTIVE'", (user_id,)
        ) as cur:
            ads = await cur.fetchall()
        for ad in ads:
            caption = await rebuild_caption(ad, verified=True)
            await db.execute("UPDATE listings SET full_caption = ? WHERE id = ?", (caption, ad["id"]))
        await db.commit()


@dp.callback_query(F.data.startswith("dp_verify_confirm_"))
async def cb_dp_verify_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 4 or not parts[3].isdigit():
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    user_id = int(parts[3])
    await mark_verified(user_id)
    try:
        await bot.send_message(user_id, "✅ Верификация подтверждена! Теперь в вашем профиле есть галочка.")
    except Exception:
        pass
    await safe_append_and_edit(callback.message, "\n\n✅ Подтверждено")
    await callback.answer("Подтверждено")


@dp.callback_query(F.data.startswith("dp_confirm_"))
async def cb_dp_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")  # dp, confirm, uid, context, lot_id
    if len(parts) < 5:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    try:
        user_id, context, lot_id = int(parts[2]), parts[3], int(parts[4])
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    if context == "renew" and lot_id:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await callback.answer("Объявление не найдено.", show_alert=True)
            return
        caption = await rebuild_caption(ad, status="АКТУАЛЬНО")
        new_expires = (datetime.now() + timedelta(days=7)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE listings SET status='ACTIVE', is_trial=0, expires_at=?, full_caption=? WHERE id=?",
                (new_expires, caption, lot_id),
            )
            await db.commit()
        try:
            await bot.send_message(user_id, "✅ Оплата подтверждена! Публикация продлена на 7 дней.")
        except Exception:
            pass
    else:
        user_state = fsm_for_user(user_id)
        await user_state.update_data(is_trial=False, paid=True, moderation_sent=False,
                                     donatepay_pending=False)
        try:
            await bot.send_message(user_id, "✅ Оплата подтверждена!\n\n" + FORM_INTRO,
                                   reply_markup=ad_type_kb())
        except Exception:
            pass

    await safe_append_and_edit(callback.message, "\n\n✅ Подтверждено")
    await callback.answer("Подтверждено")


@dp.callback_query(F.data.startswith("dp_promote_confirm_"))
async def cb_dp_promote_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    try:
        user_id, lot_id = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    promoted_until = (datetime.now() + timedelta(days=7)).isoformat()
    caption = await rebuild_caption(ad, promoted=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE listings SET is_promoted=1, promoted_until=?, full_caption=? WHERE id=?",
            (promoted_until, caption, lot_id),
        )
        await db.commit()
    await pin_first_posts(ad)
    try:
        await bot.send_message(user_id, "✅ Оплата подтверждена! Объявление закреплено в топе на 7 дней.")
    except Exception:
        pass
    await safe_append_and_edit(callback.message, "\n\n✅ Подтверждено")
    await callback.answer("Подтверждено")


@dp.callback_query(F.data.startswith("dp_reject_"))
async def cb_dp_reject(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    user_id = int(parts[2])
    try:
        await bot.send_message(
            user_id,
            "❌ Оплата через DonatePay не подтверждена. Если вы уверены, что отправили донат — "
            f"напишите в поддержку: {SUPPORT_CONTACT}",
        )
    except Exception:
        pass
    await safe_append_and_edit(callback.message, "\n\n❌ Отклонено")
    await callback.answer("Отклонено")


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

# ============================================================
#  ЧАСТЬ 3/4. Все текстовые шаги защищены TextAnswer()
#  и get_clean_text() — ошибки len(None) больше нет.
# ============================================================

@dp.callback_query(F.data.startswith("adtype_"))
async def cb_adtype(callback: types.CallbackQuery, state: FSMContext):
    ad_type = callback.data.split("_")[1]
    await state.update_data(ad_type=ad_type)
    await state.set_state(AdForm.choosing_category)
    await safe_edit_or_caption(callback.message, CATEGORY_PROMPT, category_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("cat_"), AdForm.choosing_category)
async def cb_category(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.split("_")[1]
    category_name = CATEGORY_NAMES.get(cat, cat)
    await state.update_data(category=category_name, subcategory=None)
    if category_name in SUBCATEGORIES:
        await state.set_state(AdForm.choosing_subcategory)
        await safe_edit_or_caption(
            callback.message, f"{category_name}.\n\nВыберите подкатегорию:",
            subcategory_kb(SUBCATEGORIES[category_name]),
        )
    else:
        await state.set_state(AdForm.entering_model)
        await safe_edit_or_caption(
            callback.message,
            f"{category_name}.\n\nНапишите модель устройства или ключевую особенность.",
            step_kb("back_to_category"),
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("subcat_"), AdForm.choosing_subcategory)
async def cb_subcategory(callback: types.CallbackQuery, state: FSMContext):
    subcat = callback.data.replace("subcat_", "", 1)
    data = await state.get_data()
    await state.update_data(subcategory=subcat)
    await state.set_state(AdForm.entering_model)
    await safe_edit_or_caption(
        callback.message,
        f"{data.get('category')} → {subcat}.\n\nНапишите модель устройства или ключевую особенность.",
        step_kb("back_to_subcategory"),
    )
    await callback.answer()


# ---------- НАЗАД (без state-фильтра: кнопки не «умирают») ----------
async def _need_form(callback: types.CallbackQuery, data: dict) -> bool:
    if not data.get("category"):
        await callback.answer("Сессия устарела, начните заново: /menu", show_alert=True)
        return False
    return True


@dp.callback_query(F.data == "back_to_category")
async def back_to_category(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(model=None, memory=None, subcategory=None)
    await state.set_state(AdForm.choosing_category)
    await safe_edit_or_caption(callback.message, CATEGORY_PROMPT, category_kb())
    await callback.answer()


@dp.callback_query(F.data == "back_to_subcategory")
async def back_to_subcategory(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("category")
    if category in SUBCATEGORIES:
        await state.update_data(subcategory=None, model=None, memory=None)
        await state.set_state(AdForm.choosing_subcategory)
        await safe_edit_or_caption(
            callback.message, f"{category}.\n\nВыберите подкатегорию:",
            subcategory_kb(SUBCATEGORIES[category]),
        )
    else:
        await back_to_category(callback, state)
        return
    await callback.answer()


@dp.callback_query(F.data == "back_to_model")
async def back_to_model(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(model=None, memory=None)
    await state.set_state(AdForm.entering_model)
    back = "back_to_subcategory" if data.get("subcategory") else "back_to_category"
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}.\n\nНапишите модель устройства или ключевую особенность.",
        step_kb(back),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_memory")
async def back_to_memory(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(description=None)
    if data.get("memory") == "—":
        await back_to_model(callback, state)
        return
    await state.update_data(memory=None)
    await state.set_state(AdForm.entering_memory)
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}, 💾Память\n\nНапишите память устройства, формат ОЗУ/ПЗУ.\n"
        "Без текста Гб и без лишних символов.",
        step_kb("back_to_model"),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_description")
async def back_to_description(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(price=None)
    await state.set_state(AdForm.entering_description)
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}, 📃Описание\n\n"
        "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.",
        step_kb("back_to_memory"),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_price")
async def back_to_price(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(city=None)
    await state.set_state(AdForm.entering_price)
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}, 🪎Цена\n\nУкажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно",
        step_kb("back_to_description"),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_city")
async def back_to_city(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(delivery=None)
    await state.set_state(AdForm.entering_city)
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}, 🌇Город\n\nУкажите город самовывоз — только название.\n"
        "Пример: Москва, Питер, Казань\nНикаких лишних символов указывать не нужно",
        step_kb("back_to_price"),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_delivery")
async def back_to_delivery(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(photos=[])
    await state.set_state(AdForm.choosing_delivery)
    await safe_edit_or_caption(
        callback.message,
        f"{data['category']}, 🗃️Доставка\n\n"
        "Отправите ли вы устройство через какую либо транспортную компанию?",
        delivery_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_photos")
async def back_to_photos(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.set_state(AdForm.uploading_photos)
    await safe_edit_or_caption(callback.message, f"{data['category']}, {PHOTOS_PROMPT}", photos_kb())
    await callback.answer()


# ---------- ШАГ: МОДЕЛЬ (здесь была ошибка len(None)) ----------
@dp.message(AdForm.entering_model, TextAnswer())
async def process_model(message: types.Message, state: FSMContext):
    model = await get_clean_text(message, LIMIT_MODEL)
    if model is None:
        return
    await state.update_data(model=model)
    data = await state.get_data()
    category = data.get("category") or "—"
    if category in NO_MEMORY_CATEGORIES:
        await state.update_data(memory="—")
        await state.set_state(AdForm.entering_description)
        await message.answer(
            f"{category}, 📃Описание\n\n"
            "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.",
            reply_markup=step_kb("back_to_model"),
        )
    else:
        await state.set_state(AdForm.entering_memory)
        await message.answer(
            f"{category}, 💾Память\n\nНапишите память устройства, формат ОЗУ/ПЗУ.\n"
            "Без текста Гб и без лишних символов.",
            reply_markup=step_kb("back_to_model"),
        )


@dp.message(AdForm.entering_memory, TextAnswer())
async def process_memory(message: types.Message, state: FSMContext):
    memory = await get_clean_text(message, LIMIT_MEMORY)
    if memory is None:
        return
    await state.update_data(memory=memory)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await message.answer(
        f"{data['category']}, 📃Описание\n\n"
        "Теперь нужно сделать описание: внешний вид, состояние, комплектация, ремонты, возможен ли торг.\n"
        "Память уже указана, повторять не нужно.",
        reply_markup=step_kb("back_to_memory"),
    )


@dp.message(AdForm.entering_description, TextAnswer())
async def process_description(message: types.Message, state: FSMContext):
    description = await get_clean_text(message, LIMIT_DESCRIPTION)
    if description is None:
        return
    await state.update_data(description=description)
    data = await state.get_data()
    await state.set_state(AdForm.entering_price)
    await message.answer(
        f"{data['category']}, 🪎Цена\n\nУкажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно",
        reply_markup=step_kb("back_to_description"),
    )


@dp.message(AdForm.entering_price, TextAnswer())
async def process_price(message: types.Message, state: FSMContext):
    raw = await get_clean_text(message, 20)
    if raw is None:
        return
    price = raw.replace(" ", "").replace("\u00a0", "").replace(",", "")
    if not price.isdigit() or not (1 <= int(price) <= 100_000_000):
        await message.answer("Введите цену цифрами (от 1 до 100 000 000).")
        return
    await state.update_data(price=price)
    data = await state.get_data()
    await state.set_state(AdForm.entering_city)
    await message.answer(
        f"{data['category']}, 🌇Город\n\nУкажите город самовывоз — только название.\n"
        "Пример: Москва, Питер, Казань\nНикаких лишних символов указывать не нужно",
        reply_markup=step_kb("back_to_price"),
    )


@dp.message(AdForm.entering_city, TextAnswer())
async def process_city(message: types.Message, state: FSMContext):
    city = await get_clean_text(message, LIMIT_CITY)
    if city is None:
        return
    await state.update_data(city=city)
    data = await state.get_data()
    await state.set_state(AdForm.choosing_delivery)
    await message.answer(
        f"{data['category']}, 🗃️Доставка\n\n"
        "Отправите ли вы устройство через какую либо транспортную компанию?",
        reply_markup=delivery_kb(),
    )


@dp.callback_query(F.data.startswith("del_"), AdForm.choosing_delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    delivery = "Отправлю" if callback.data == "del_yes" else "Не отправлю"
    await state.update_data(delivery=delivery, photos=[])
    await state.set_state(AdForm.uploading_photos)
    data = await state.get_data()
    await safe_edit_or_caption(callback.message, f"{data['category']}, {PHOTOS_PROMPT}", photos_kb())
    await callback.answer()


# ---------- ФОТО (с блокировкой от гонки при альбоме) ----------
_photo_locks: Dict[int, asyncio.Lock] = {}


def _photo_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _photo_locks:
        if len(_photo_locks) > 2000:
            _photo_locks.clear()
        _photo_locks[user_id] = asyncio.Lock()
    return _photo_locks[user_id]


@dp.message(AdForm.uploading_photos, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    async with _photo_lock(message.from_user.id):
        data = await state.get_data()
        photos = list(data.get("photos") or [])
        if len(photos) >= MAX_PHOTOS:
            await message.answer(f"Максимум {MAX_PHOTOS} фото.")
            return
        photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos)
    await message.answer(f"Загружено фотографий {len(photos)} из {MAX_PHOTOS}")


@dp.message(AdForm.uploading_photos, F.video | F.video_note | F.animation)
async def reject_video(message: types.Message):
    await message.answer("Видео не поддерживается. Пожалуйста, отправьте фото.")


@dp.message(AdForm.uploading_photos, F.document)
async def reject_document_photo(message: types.Message):
    await message.answer("Фото нужно отправлять сжатым (как фото), а не файлом 📎")


@dp.callback_query(F.data == "photos_done", AdForm.uploading_photos)
async def photos_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos") or []
    if not photos:
        await callback.answer("Загрузите хотя бы одно фото!", show_alert=True)
        return

    preview_text = (
        "🔍Предпросмотр\n\n"
        "Проверь текст и вложения на наличие ошибок. Если всё нормально — отправь на модерацию.\n\n"
        "✅АКТУАЛЬНО✅\n"
        f"#{data['category']}\n"
    )
    if data.get("subcategory"):
        preview_text += f"#{data['subcategory']}\n"
    preview_text += f"#{data['city']}\n\n📦 {data['model']}\n\n"
    if data.get("memory") and data["memory"] != "—":
        preview_text += f"💾Память: {data['memory']}\n\n"
    preview_text += (
        f"{data['description']}\n\n"
        f"🇷🇺{data['city']}\n💸{data['price']}\n🚛{data['delivery']}\n\n"
        f"🥷🏻{callback.from_user.first_name}"
    )
    if tg_len(preview_text) > MAX_CAPTION:
        await callback.answer("Описание слишком длинное, сократите.", show_alert=True)
        return

    await state.set_state(AdForm.preview)
    kb = preview_kb()
    if len(photos) == 1:
        await callback.message.answer_photo(photo=photos[0], caption=preview_text, reply_markup=kb)
    else:
        media = [InputMediaPhoto(media=ph, caption=preview_text if i == 0 else None)
                 for i, ph in enumerate(photos)]
        await callback.message.answer_media_group(media=media)
        await callback.message.answer("Выберите действие:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "reset_ad")
async def reset_ad(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message, callback.from_user.id, callback.from_user.first_name)
    await callback.answer()


@dp.callback_query(F.data == "send_moderation", AdForm.preview)
async def send_moderation(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("moderation_sent"):
        await callback.answer("Объявление уже отправлено на модерацию.", show_alert=True)
        return
    if not data.get("model") or not data.get("photos"):
        await callback.answer("Анкета заполнена не полностью. Начните заново: /menu", show_alert=True)
        return

    user_id = callback.from_user.id
    is_trial = bool(data.get("is_trial", True))
    try:
        lot_id = await save_listing(user_id, data, status="PENDING_MODERATION", is_trial=is_trial)
    except Exception as e:
        print(f"DEBUG: save_listing error: {e}\n{traceback.format_exc()[:500]}")
        await callback.answer("Ошибка сохранения, попробуй ещё раз.", show_alert=True)
        return

    await state.update_data(moderation_sent=True)
    await safe_edit_or_caption(
        callback.message,
        "Объявление отправлено на модерацию!\n\n"
        "Как только модератор примет пост — придёт уведомление вам в чат с ботом, "
        'а статус появится в разделе "Мои объявления"',
    )

    admin_text = (
        f"🔔 Новое объявление на модерации #{lot_id}\n\n"
        f"Пользователь: {callback.from_user.first_name} (@{callback.from_user.username or 'нет'})\n"
        f"ID: {user_id}\n"
        f"Тариф: {'бесплатно 24ч' if is_trial else 'оплачено 7 дней'}\n"
        f"Тип: {AD_TYPE_NAMES.get(data.get('ad_type'), data.get('ad_type'))} | Категория: {data.get('category')}\n"
        f"Модель: {data.get('model')}\n"
    )
    if data.get("memory") and data["memory"] != "—":
        admin_text += f"Память: {data['memory']}\n"
    admin_text += (
        f"Цена: {data.get('price')} | Город: {data.get('city')}\n"
        f"Доставка: {data.get('delivery')}\n"
        f"Описание: {data.get('description')}"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрено", callback_data=f"approve_{lot_id}")],
        [InlineKeyboardButton(text="❌ Не одобрено", callback_data=f"reject_{lot_id}")],
    ])
    photos = data.get("photos") or []
    sent_ok = False
    try:
        if len(photos) == 1:
            await bot.send_photo(ADMIN_ID, photo=photos[0], caption=admin_text[:1024], reply_markup=admin_kb)
            sent_ok = True
        elif photos:
            media = [InputMediaPhoto(media=ph, caption=admin_text[:1024] if i == 0 else None)
                     for i, ph in enumerate(photos)]
            await bot.send_media_group(ADMIN_ID, media=media)
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb)
            sent_ok = True
    except Exception as e:
        print(f"DEBUG: send photos to admin failed: {e}")
    if not sent_ok:
        try:
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb)
        except Exception as e:
            print(f"DEBUG: send text to admin failed too: {e}")

    await state.clear()
    await callback.answer()


# ---------- МОДЕРАЦИЯ ----------
async def notify_subscribers(row, first_channel: str, first_message_id: int):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT DISTINCT user_id, keyword FROM subscriptions") as cur:
                subs = await cur.fetchall()
        haystack = " ".join([
            str(row_get(row, "model", "")), str(row_get(row, "description", "")),
            str(row_get(row, "category", "")), str(row_get(row, "subcategory", "")),
        ]).lower()
        notified = {row_get(row, "user_id")}
        for s in subs:
            kw = (row_get(s, "keyword", "") or "").lower()
            if kw and kw in haystack and s["user_id"] not in notified:
                notified.add(s["user_id"])
                try:
                    msg = (
                        f"🔔 Новое объявление по подписке «{escape_html(s['keyword'])}»:\n\n"
                        f"📦 {escape_html(row_get(row, 'model', '—'))}\n"
                        f"💸 {escape_html(row_get(row, 'price', '—'))} руб.\n"
                        f"🇷🇺 {escape_html(row_get(row, 'city', '—'))}\n"
                    )
                    if first_channel and first_message_id:
                        link = f"https://t.me/{str(first_channel).lstrip('@')}/{first_message_id}"
                        msg += f"\n🔗 <a href='{link}'>Открыть в канале</a>"
                    await bot.send_message(s["user_id"], msg, parse_mode="HTML")
                except Exception as e:
                    print(f"Не удалось уведомить {s['user_id']}: {e}")
    except Exception as e:
        print(f"Ошибка уведомлений подписчикам: {e}")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await callback.answer("Объявление не найдено", show_alert=True)
        return
    if row_get(row, "status") == "ACTIVE":
        await callback.answer("Уже опубликовано.", show_alert=True)
        return

    verified = await is_verified(row["user_id"])
    tag = await get_author_tag(row["user_id"])
    post_text = build_post_text(row, status="АКТУАЛЬНО", author_tag=tag,
                                author_id=row["user_id"], verified=verified)
    photos = [p for p in (row_get(row, "photos", "") or "").split(",") if p]

    saved_pairs, first_channel, first_message_id = [], "", 0
    for channel in CHANNELS:
        try:
            if len(photos) == 1:
                sent = await bot.send_photo(channel, photo=photos[0], caption=post_text, parse_mode="HTML")
                saved_pairs.append(f"{channel}:{sent.message_id}")
                if not first_channel:
                    first_channel, first_message_id = channel, sent.message_id
            elif photos:
                media = [InputMediaPhoto(media=ph, caption=post_text if i == 0 else None,
                                         parse_mode="HTML" if i == 0 else None)
                         for i, ph in enumerate(photos)]
                sent_msgs = await bot.send_media_group(channel, media=media)
                for sm in sent_msgs:
                    saved_pairs.append(f"{channel}:{sm.message_id}")
                if not first_channel:
                    first_channel, first_message_id = channel, sent_msgs[0].message_id
            else:
                sent = await bot.send_message(channel, post_text, parse_mode="HTML")
                saved_pairs.append(f"{channel}:{sent.message_id}")
                if not first_channel:
                    first_channel, first_message_id = channel, sent.message_id
        except Exception as e:
            print(f"Ошибка публикации в {channel}: {e}")

    if not saved_pairs:
        await callback.answer("Не удалось опубликовать ни в один канал.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE listings SET status='ACTIVE', channel=?, message_id=?, message_ids=?, full_caption=? WHERE id=?",
            (first_channel, first_message_id, ",".join(saved_pairs), post_text, lot_id),
        )
        await db.commit()

    try:
        await bot.send_message(
            row["user_id"], "Ваша публикация одобрена!👍",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
            ]),
        )
    except Exception as e:
        print(f"DEBUG: не удалось уведомить {row['user_id']}: {e}")

    await notify_subscribers(row, first_channel, first_message_id)
    await safe_edit_or_caption(callback.message, "Объявление одобрено и опубликовано.")
    await callback.answer()


@dp.callback_query(F.data.startswith("reject_"))
async def reject_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, charge_id FROM listings WHERE id = ?", (lot_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Объявление не найдено", show_alert=True)
            return
        await db.execute("UPDATE listings SET status='REJECTED' WHERE id = ?", (lot_id,))
        await db.commit()

    charge_id = row_get(row, "charge_id")
    if charge_id:
        try:
            await bot.refund_star_payment(user_id=row["user_id"], telegram_payment_charge_id=charge_id)
            await bot.send_message(
                ADMIN_ID,
                f"↩️ <b>Возврат Stars</b>\n\nПользователь: <code>{row['user_id']}</code>\n"
                f"Объявление #{lot_id} отклонено, звёзды возвращены.\n"
                f"Charge ID: <code>{escape_html(charge_id)}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"Не удалось вернуть звёзды {row['user_id']}: {e}")

    try:
        await bot.send_message(
            row["user_id"], "Ваша публикация не одобрена👎, попробуйте заполнить анкету заново",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
            ]),
        )
    except Exception:
        pass
    await safe_edit_or_caption(callback.message, "Объявление отклонено.")
    await callback.answer()


# ---------- КАРТОЧКА ЛОТА / СНЯТИЕ ----------
@dp.callback_query(F.data.startswith("lot_"))
async def cb_lot_details(callback: types.CallbackQuery):
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено", show_alert=True)
        return
    if ad["user_id"] != callback.from_user.id and callback.from_user.id != ADMIN_ID:
        await callback.answer("Это не ваше объявление.", show_alert=True)
        return

    ch, mid = row_get(ad, "channel"), row_get(ad, "message_id")
    link = f"https://t.me/{str(ch).lstrip('@')}/{mid}" if ch and mid else ""
    created = (row_get(ad, "created_at", "") or "")[:10] or "—"
    status = row_get(ad, "status", "—")
    text = (
        f"{row_get(ad, 'model', '—')}\n{row_get(ad, 'price', '—')} руб.\n"
        f"{row_get(ad, 'city', '—')}\nСтатус: {status}\nВ канале с {created}"
    )
    kb = []
    if link:
        kb.append([InlineKeyboardButton(text="🔗 Открыть в канале", url=link)])
    if status == "ACTIVE":
        kb.append([InlineKeyboardButton(text="🗑️ Снять с продажи", callback_data=f"remove_{lot_id}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="my_ads")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@dp.callback_query(F.data.startswith("remove_"))
async def cb_remove_ad(callback: types.CallbackQuery):
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM listings WHERE id = ? AND user_id = ? AND status = 'ACTIVE'",
            (lot_id, callback.from_user.id),
        ) as cur:
            ad = await cur.fetchone()
        if not ad:
            await callback.answer("Объявление не найдено или уже снято.", show_alert=True)
            return
        await db.execute(
            "UPDATE listings SET status='SOLD', is_promoted=0, promoted_until=NULL WHERE id=?", (lot_id,)
        )
        await db.commit()

    if bool(row_get(ad, "is_promoted", 0)):
        await unpin_first_posts(ad)
    caption = await rebuild_caption(ad, status="ПРОДАНО", promoted=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE listings SET full_caption=? WHERE id=?", (caption, lot_id))
        await db.commit()
    await safe_edit_or_caption(callback.message, "Объявление снято с продажи.", main_menu_kb())
    await callback.answer()

# ============================================================
#  ЧАСТЬ 4/4. FALLBACK-хендлеры идут ПОСЛЕДНИМИ
#  и работают ТОЛЬКО в личных чатах.
# ============================================================

# ---------- ПОДПИСКИ ----------
@dp.callback_query(F.data == "subs_menu")
async def cb_subs_menu(callback: types.CallbackQuery):
    await safe_edit_or_caption(
        callback.message,
        "🔔 <b>Подписки на новые объявления</b>\n\n"
        "Подпишитесь на ключевые слова — и бот пришлёт уведомление, "
        "когда появится подходящее объявление.",
        subscriptions_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "sub_add")
async def cb_sub_add(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdForm.waiting_for_subscription_keyword)
    await safe_edit_or_caption(
        callback.message, "Введите ключевое слово для подписки (например, «iPhone 13»):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="subs_menu")]
        ]),
    )
    await callback.answer()


@dp.message(AdForm.waiting_for_subscription_keyword, TextAnswer())
async def process_subscription_keyword(message: types.Message, state: FSMContext):
    keyword = await get_clean_text(message, LIMIT_KEYWORD)
    if keyword is None:
        return
    if len(keyword) < 2:
        await message.answer("Слишком короткое слово, минимум 2 символа.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (message.from_user.id,)
        ) as cur:
            count = (await cur.fetchone())[0]
        if count >= MAX_SUBSCRIPTIONS:
            await message.answer(f"Максимум {MAX_SUBSCRIPTIONS} подписок. Удалите лишние: /subs")
            await state.clear()
            return
        async with db.execute(
            "SELECT 1 FROM subscriptions WHERE user_id = ? AND LOWER(keyword) = LOWER(?)",
            (message.from_user.id, keyword),
        ) as cur:
            exists = await cur.fetchone()
        if exists:
            await message.answer("Такая подписка уже есть.")
            await state.clear()
            return
        await db.execute(
            "INSERT INTO subscriptions (user_id, keyword, created_at) VALUES (?, ?, ?)",
            (message.from_user.id, keyword, datetime.now().isoformat()),
        )
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Подписка на «{keyword}» добавлена.")
    await show_main_menu(message)


@dp.callback_query(F.data == "sub_list")
async def cb_sub_list(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC", (callback.from_user.id,)
        ) as cur:
            subs = await cur.fetchall()
    if not subs:
        await safe_edit_or_caption(
            callback.message, "У вас нет подписок.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить", callback_data="sub_add")],
                [InlineKeyboardButton(text="↩️ Назад", callback_data="subs_menu")],
            ]),
        )
        await callback.answer()
        return
    kb = [[InlineKeyboardButton(text=f"❌ {s['keyword']}", callback_data=f"sub_del_{s['id']}")] for s in subs]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="subs_menu")])
    await safe_edit_or_caption(callback.message, "📋 Ваши подписки. Нажмите, чтобы удалить:",
                               InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_del_"))
async def cb_sub_del(callback: types.CallbackQuery):
    try:
        sub_id = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
                         (sub_id, callback.from_user.id))
        await db.commit()
    await callback.answer("Удалено")
    await cb_sub_list(callback)


# ---------- ПОМОЩЬ ----------
@dp.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    await safe_edit_or_caption(
        callback.message,
        "❓ <b>Помощь</b>\n\nЗдесь вы найдёте ответы на частые вопросы о публикации "
        "объявлений в JustTrade.\n\nВыберите раздел ниже:",
        help_menu_kb(),
    )
    await callback.answer()


def _help_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="help")]
    ])


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
    await safe_edit_or_caption(callback.message, text, _help_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "help_howto")
async def cb_help_howto(callback: types.CallbackQuery):
    text = (
        "❔ <b>Как выставить объявление</b>\n\n"
        "1. Нажмите «✅ Опубликовать» в главном меню.\n"
        "2. Выберите «🌱 Бесплатно» (24 часа) или «💳 Платно» (7 дней).\n"
        "3. Выберите: «💵 Продажа», «🕵🏻 Покупка» или «🔁 Обмен».\n"
        "4. Выберите тип устройства.\n"
        "5. Укажите модель или ключевую особенность.\n"
        "6. Для техники — память в формате ОЗУ/ПЗУ (например, 12/256).\n"
        "7. Напишите описание: внешний вид, состояние, комплектация, ремонты, торг.\n"
        "8. Укажите цену (только цифры).\n"
        "9. Укажите город.\n"
        "10. Выберите, отправляете ли вы товар транспортной компанией.\n"
        "11. Загрузите от 1 до 10 реальных фото (видео не поддерживается).\n"
        "12. Проверьте предпросмотр и нажмите «💫 Отправка на модерацию».\n\n"
        "После одобрения модератором объявление появится в каналах.\n\n"
        "Отмена действий в любой момент: /cancel."
    )
    await safe_edit_or_caption(callback.message, text, _help_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "help_reject")
async def cb_help_reject(callback: types.CallbackQuery):
    text = (
        "⚠️ <b>Отказ в публикации</b>\n\n"
        "Если ваше объявление отклонено, значит по мнению модератора оно не соблюдает правила.\n\n"
        "<b>Частые причины отказа:</b>\n"
        "• Фото не соответствуют товару или взяты из интернета.\n"
        "• Описание неполное или вводит в заблуждение.\n"
        "• Товар запрещён к продаже.\n"
        "• Объявление не по тематике канала.\n"
        "• Нарушены правила оформления (нет цены, города, доставки).\n\n"
        "<b>Что делать:</b>\n"
        "Создайте объявление заново, исправив замечания.\n\n"
        f"Если вы не согласны с отказом — напишите в поддержку: {SUPPORT_CONTACT}."
    )
    await safe_edit_or_caption(callback.message, text, _help_back_kb())
    await callback.answer()


# ---------- КОМАНДЫ АДМИНА ----------
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async def cnt(sql):
            async with db.execute(sql) as cur:
                return (await cur.fetchone())[0]
        total = await cnt("SELECT COUNT(*) FROM listings")
        active = await cnt("SELECT COUNT(*) FROM listings WHERE status='ACTIVE'")
        sold = await cnt("SELECT COUNT(*) FROM listings WHERE status='SOLD'")
        pending = await cnt("SELECT COUNT(*) FROM listings WHERE status='PENDING_MODERATION'")
        promoted = await cnt("SELECT COUNT(*) FROM listings WHERE is_promoted=1")
        users = await cnt("SELECT COUNT(*) FROM users")
        subs = await cnt("SELECT COUNT(*) FROM subscriptions")
    await message.answer(
        f"📊 <b>Статистика JustTrade</b>\n\n"
        f"👥 Пользователей: {users}\n📦 Всего объявлений: {total}\n"
        f"🟢 Активных: {active}\n🕓 На модерации: {pending}\n"
        f"🔴 Проданных/снятых: {sold}\n🔥 В топе: {promoted}\n🔔 Подписок: {subs}",
        parse_mode="HTML",
    )


@dp.message(Command("check_ads"))
async def cmd_check_ads(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, model, status, channel, message_id FROM listings ORDER BY id DESC LIMIT 30"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await message.answer("Объявлений нет.")
        return
    lines = [
        f"#{r['id']} | {(row_get(r, 'model', '') or '')[:30]} | {row_get(r, 'status', '')} | "
        f"{row_get(r, 'channel', '')} | {row_get(r, 'message_id', '')}"
        for r in rows
    ]
    await message.answer("<pre>" + escape_html("\n".join(lines)) + "</pre>", parse_mode="HTML")


@dp.message(Command("dbinfo"))
async def cmd_dbinfo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA table_info(listings)") as cur:
            cols = [row[1] for row in await cur.fetchall()]
    await message.answer(f"Столбцы listings:\n<code>{escape_html(', '.join(cols))}</code>", parse_mode="HTML")


@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split(maxsplit=1)
    search = parts[1].strip() if len(parts) > 1 else ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if search:
            async with db.execute(
                "SELECT user_id, first_seen, verified FROM users "
                "WHERE CAST(user_id AS TEXT) LIKE ? LIMIT 30", (f"%{search}%",)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT user_id, first_seen, verified FROM users ORDER BY first_seen DESC LIMIT 30"
            ) as cur:
                rows = await cur.fetchall()
    if not rows:
        await message.answer("Пользователей не найдено.")
        return
    lines = [
        f"{r['user_id']} {'✅' if row_get(r, 'verified', 0) else '  '} | "
        f"{(row_get(r, 'first_seen', '') or '—')[:10]}" for r in rows
    ]
    header = "👥 Пользователи" + (f" (поиск: «{escape_html(search)}»)" if search else f" (последние {len(rows)})")
    await message.answer(
        f"{header}:\n<pre>" + escape_html("\n".join(lines)) + "</pre>\n\n"
        "💡 Отправить сообщение: <code>/send user_id текст</code>",
        parse_mode="HTML",
    )


@dp.message(Command("send"))
async def cmd_send(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/send user_id текст</code>\n"
            "Пример: <code>/send 123456789 Привет! Твой лот одобрен ✅</code>",
            parse_mode="HTML",
        )
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ user_id должен быть числом.")
        return
    text = parts[2].strip()
    if not text:
        await message.answer("❌ Текст сообщения пустой.")
        return
    try:
        try:
            await bot.send_message(target_id, text, parse_mode="HTML")
        except Exception as e:
            if "can't parse entities" in str(e).lower():
                await bot.send_message(target_id, text)
            else:
                raise
        await message.answer(f"✅ Отправлено пользователю <code>{target_id}</code>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: <code>{escape_html(str(e))}</code>", parse_mode="HTML")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование: <code>/broadcast Текст сообщения</code>\n\n"
            "💡 Поддерживается HTML. Если HTML сломан — бот отправит как plain text.",
            parse_mode="HTML",
        )
        return
    text = parts[1].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            users = [row[0] for row in await cur.fetchall()]
    if not users:
        await message.answer("❌ В базе нет пользователей.")
        return

    status_msg = await message.answer(f"📢 Начинаю рассылку на {len(users)} пользователей...")
    sent = failed = blocked = flood_waits = 0
    for i, uid in enumerate(users, 1):
        try:
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
            except Exception as e:
                if "can't parse entities" in str(e).lower():
                    await bot.send_message(uid, text)
                else:
                    raise
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            flood_waits += 1
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            failed += 1
        except Exception as e:
            failed += 1
            print(f"Broadcast: не удалось отправить {uid}: {e}")
        if i % 50 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 Рассылка: {i}/{len(users)}\n✅ {sent} | ❌ {failed} | 🚫 {blocked} | ⏳ {flood_waits}"
                )
            except Exception:
                pass
    try:
        await status_msg.edit_text(
            f"📢 <b>Рассылка завершена</b>\n\n✅ Доставлено: {sent}\n❌ Ошибок: {failed}\n"
            f"🚫 Заблокировали: {blocked}\n⏳ FloodWait: {flood_waits}",
            parse_mode="HTML",
        )
    except Exception:
        await message.answer(f"✅ Отправлено: {sent}\n❌ Не доставлено: {failed}")


@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not os.path.exists(DB_PATH):
        await message.answer("Файл БД не найден.")
        return
    await bot.send_document(ADMIN_ID, FSInputFile(DB_PATH),
                            caption=f"Бэкап базы за {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await message.answer("✅ Бэкап отправлен.")


@dp.message(Command("restore"))
async def restore_db(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdForm.waiting_for_restore_db)
    await message.answer(
        "📁 Пришлите файл <code>market.db</code> как документ.\n\n"
        "⚠️ Восстановление временное: после перезапуска Render данные исчезнут.",
        parse_mode="HTML",
    )


@dp.message(AdForm.waiting_for_restore_db, F.document)
async def process_restore_file(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    file_name = message.document.file_name or ""
    if not file_name.endswith(".db"):
        await message.answer("❌ Нужен файл с расширением .db (например, market.db).")
        return
    try:
        file = await bot.get_file(message.document.file_id)
        await bot.download_file(file.file_path, DB_PATH)
        await init_db()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM listings") as cur:
                count = (await cur.fetchone())[0]
            async with db.execute("PRAGMA table_info(listings)") as cur:
                cols = [r[1] for r in await cur.fetchall()]
        await message.answer(
            f"✅ БД восстановлена и мигрирована!\n\n📊 Объявлений: {count}\n"
            f"📋 Колонок: {len(cols)}\n<code>{escape_html(', '.join(cols))}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"❌ Ошибка восстановления:\n<code>{escape_html(str(e))}</code>\n\n"
            f"<pre>{escape_html(traceback.format_exc()[-1000:])}</pre>",
            parse_mode="HTML",
        )
    finally:
        await state.clear()


# ---------- УБОРКА В ЧАТЕ (барахолка остаётся чатом) ----------
@dp.message(Command("chat_clean"))
async def cmd_chat_clean(message: types.Message):
    """Снимает ReplyKeyboard бота со всех участников чата."""
    if message.from_user.id != ADMIN_ID:
        return
    try:
        m = await bot.send_message(FLEA_CHAT, "⚙️", reply_markup=ReplyKeyboardRemove(selective=False))
        await asyncio.sleep(2)
        try:
            await bot.delete_message(FLEA_CHAT, m.message_id)
        except Exception:
            pass
        await message.answer(
            "✅ Клавиатура бота убрана из чата.\n"
            "Если кнопки у кого-то остались — им нужно закрыть и открыть чат."
        )
    except Exception as e:
        await message.answer(f"❌ Не получилось: <code>{escape_html(str(e))}</code>", parse_mode="HTML")


@dp.message(Command("chat_reset"))
async def cmd_chat_reset(message: types.Message):
    """Полный сброс меню бота в чате: reply-клавиатура + список команд.
    /chat_reset keep — оставить служебное сообщение (надёжнее держит клавиатуру убранной)."""
    if message.from_user.id != ADMIN_ID:
        return
    try:
        chat = await bot.get_chat(FLEA_CHAT)
    except Exception as e:
        await message.answer(
            f"❌ Не могу получить чат {FLEA_CHAT}: {e}\n\n"
            "Проверьте, что бот добавлен в чат и является админом."
        )
        return

    report = [f"Чат: {chat.title}\nid: {chat.id}\nтип: {chat.type}\n"]
    keep = "keep" in (message.text or "").lower()
    try:
        m = await bot.send_message(chat.id, "⌨️", reply_markup=ReplyKeyboardRemove(selective=False))
        if not keep:
            await asyncio.sleep(5)
            try:
                await bot.delete_message(chat.id, m.message_id)
                report.append("✅ Reply-клавиатура снята (служебное сообщение удалено)")
            except Exception:
                report.append(f"✅ Reply-клавиатура снята, сообщение id={m.message_id} удалить не удалось")
        else:
            report.append(f"✅ Reply-клавиатура снята, сообщение id={m.message_id} оставлено в чате")
    except Exception as e:
        report.append(f"❌ Reply-клавиатура: {e}")

    for scope in (
        BotCommandScopeChat(chat_id=chat.id),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
        BotCommandScopeDefault(),
    ):
        try:
            await bot.set_my_commands([], scope=scope)
            report.append(f"✅ Команды очищены: {type(scope).__name__}")
        except Exception as e:
            report.append(f"❌ {type(scope).__name__}: {e}")

    report.append(
        "\n⚠️ У части участников меню может остаться в кэше — "
        "им нужно закрыть и заново открыть чат (или перезапустить Telegram)."
    )
    await message.answer("\n".join(report))


@dp.message(Command("chat_unpin"))
async def cmd_chat_unpin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/chat_unpin message_id</code>", parse_mode="HTML")
        return
    try:
        await bot.unpin_chat_message(chat_id=FLEA_CHAT, message_id=int(parts[1]))
        await message.answer("✅ Откреплено.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(str(e))}</code>", parse_mode="HTML")


@dp.message(Command("chat_del"))
async def cmd_chat_del(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/chat_del message_id</code>", parse_mode="HTML")
        return
    try:
        await bot.delete_message(chat_id=FLEA_CHAT, message_id=int(parts[1]))
        await message.answer("✅ Сообщение удалено.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(str(e))}</code>", parse_mode="HTML")


@dp.message(Command("reload_menu"))
async def cmd_reload_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await setup_bot_commands()
    await message.answer("✅ Меню команд обновлено: список по / виден только вам в личке.")


# ---------- ПРОДЛЕНИЕ ----------
@dp.callback_query(F.data.startswith("renew_"))
async def renew_cb(callback: types.CallbackQuery, state: FSMContext):
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id = ? AND user_id = ?",
                              (lot_id, callback.from_user.id)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await state.update_data(paid_context="renew", renew_lot_id=lot_id, donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "Отлично! Объявление будет выставлено на 7 дней. "
        "После окончания срока его можно продлить снова.\n\nВыберите способ оплаты:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="25⭐️", callback_data="pay_stars")],
            [InlineKeyboardButton(text="25💸", callback_data="pay_donatepay")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu")],
        ]),
    )
    await callback.answer()


# ---------- УСПЕШНАЯ ОПЛАТА STARS ----------
def _safe_lot_id(pl: str) -> int:
    try:
        return int(pl.split("_")[1])
    except (IndexError, ValueError):
        return 0


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message, state: FSMContext):
    payment = message.successful_payment
    payload = payment.invoice_payload or ""
    charge_id = payment.telegram_payment_charge_id
    user_id = message.from_user.id

    if payload.startswith("promote_"):
        lot_id = _safe_lot_id(payload)
        if not lot_id:
            await message.answer("⚠️ Оплата получена, но не удалось определить объявление. "
                                 f"Напишите в поддержку: {SUPPORT_CONTACT}")
            await bot.send_message(ADMIN_ID, f"⚠️ Битый payload: {payload}\nUser: {user_id}\nCharge: {charge_id}")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await message.answer("Объявление не найдено, но оплата получена. Напишите в поддержку.")
            return
        promoted_until = (datetime.now() + timedelta(days=7)).isoformat()
        caption = await rebuild_caption(ad, promoted=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE listings SET is_promoted=1, promoted_until=?, full_caption=? WHERE id=?",
                (promoted_until, caption, lot_id),
            )
            await db.commit()
        await pin_first_posts(ad)
        await message.answer("✅ Оплата получена! Объявление закреплено в топе на 7 дней.")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🚀 <b>Продвижение (Stars)</b>\n\nID: <code>{ad['user_id']}</code>\n"
                f"Объявление #{lot_id}: {escape_html(row_get(ad, 'model', ''))}\n"
                f"Сумма: {STARS_PRICE} ⭐️\nЗакреп до: {promoted_until[:10]}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if payload.startswith("verify_"):
        await mark_verified(user_id)
        await message.answer("✅ Верификация подтверждена! В профиле появилась галочка.")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛡 <b>Верификация (Stars)</b>\n\n"
                f"{escape_html(message.from_user.first_name)} (@{message.from_user.username or 'нет'})\n"
                f"ID: <code>{user_id}</code>\nСумма: {STARS_PRICE} ⭐️",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if payload.startswith("renew_"):
        lot_id = _safe_lot_id(payload)
        if not lot_id:
            await message.answer("⚠️ Оплата получена, но не удалось определить объявление. "
                                 f"Напишите в поддержку: {SUPPORT_CONTACT}")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM listings WHERE id = ?", (lot_id,)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await message.answer("Объявление не найдено, но оплата получена. Напишите в поддержку.")
            return
        caption = await rebuild_caption(ad, status="АКТУАЛЬНО")
        new_expires = (datetime.now() + timedelta(days=7)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE listings SET status='ACTIVE', is_trial=0, expires_at=?, charge_id=?, full_caption=? WHERE id=?",
                (new_expires, charge_id, caption, lot_id),
            )
            await db.commit()
        await message.answer("✅ Оплата получена! Публикация продлена на 7 дней.")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⭐ <b>Оплата Stars (продление)</b>\n\nID: <code>{user_id}</code>\n"
                f"Объявление #{lot_id}: {escape_html(row_get(ad, 'model', ''))}\n"
                f"Charge ID: <code>{escape_html(charge_id)}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await state.update_data(paid_context=None, renew_lot_id=None)
        return

    await state.update_data(is_trial=False, paid=True, charge_id=charge_id, moderation_sent=False)
    await message.answer("✅ Оплата получена!\n\n" + FORM_INTRO, reply_markup=ad_type_kb())
    try:
        await bot.send_message(
            ADMIN_ID,
            f"⭐ <b>Оплата Stars (новое объявление)</b>\n\n"
            f"{escape_html(message.from_user.first_name)} (@{message.from_user.username or 'нет'})\n"
            f"ID: <code>{user_id}</code>\nСумма: {STARS_PRICE} ⭐️\n"
            f"Charge ID: <code>{escape_html(charge_id)}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"Не удалось уведомить админа об оплате: {e}")


# ---------- ФОНОВЫЕ ЗАДАЧИ ----------
async def expire_loop():
    while True:
        try:
            now = datetime.now().isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM listings WHERE status='ACTIVE' "
                    "AND expires_at IS NOT NULL AND expires_at <= ?", (now,)
                ) as cur:
                    expired = await cur.fetchall()
            for ad in expired:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute(
                        "UPDATE listings SET status='SOLD', is_promoted=0, promoted_until=NULL "
                        "WHERE id=? AND status='ACTIVE'", (ad["id"],)
                    )
                    await db.commit()
                    if cur.rowcount == 0:
                        continue
                await unpin_first_posts(ad)
                caption = await rebuild_caption(ad, status="ПРОДАНО", promoted=False)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE listings SET full_caption=? WHERE id=?", (caption, ad["id"]))
                    await db.commit()
                try:
                    await bot.send_message(
                        ad["user_id"],
                        f"У вашего объявления «{row_get(ad, 'model', '')}» кончился срок публикации. "
                        "Хотите продлить его на 7 дней?",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💸 Продлить", callback_data=f"renew_{ad['id']}")],
                            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
                        ]),
                    )
                except Exception as e:
                    print(f"Не удалось уведомить {ad['user_id']}: {e}")
        except Exception as e:
            print(f"Ошибка в expire_loop: {e}")
        await asyncio.sleep(60)


async def unpin_loop():
    while True:
        try:
            now = datetime.now().isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM listings WHERE is_promoted=1 "
                    "AND promoted_until IS NOT NULL AND promoted_until <= ?", (now,)
                ) as cur:
                    expired = await cur.fetchall()
            for ad in expired:
                caption = await rebuild_caption(ad, status="АКТУАЛЬНО", promoted=False)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE listings SET is_promoted=0, promoted_until=NULL, full_caption=? WHERE id=?",
                        (caption, ad["id"]),
                    )
                    await db.commit()
                await unpin_first_posts(ad)
                try:
                    await bot.send_message(
                        ad["user_id"],
                        f"⏰ Срок закрепа объявления «{row_get(ad, 'model', '')}» истёк. "
                        "Пометка 🔥 ТОП убрана. Вы можете снова поднять его в топ за 25 ⭐️.",
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"Ошибка в unpin_loop: {e}")
        await asyncio.sleep(60)


async def backup_loop():
    while True:
        await asyncio.sleep(3600)
        try:
            if os.path.exists(DB_PATH):
                await bot.send_document(
                    ADMIN_ID, FSInputFile(DB_PATH),
                    caption=f"Бэкап базы за {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                )
            else:
                print(f"Файл {DB_PATH} не найден, бэкап пропущен")
        except Exception as e:
            print(f"Ошибка бэкапа: {e}")


# ---------- МЕНЮ КОМАНД: список по / виден ТОЛЬКО владельцу ----------
async def setup_bot_commands():
    user_commands = [
        BotCommand(command="start", description="🏡 Запустить / Главное меню"),
        BotCommand(command="menu", description="🏠 Открыть главное меню"),
        BotCommand(command="profile", description="🥷🏻 Мой профиль"),
        BotCommand(command="my_lots", description="📚 Мои объявления"),
        BotCommand(command="subs", description="🔔 Подписки на объявления"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="cancel", description="❌ Отменить действие"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="stats", description="📊 Статистика бота"),
        BotCommand(command="check_ads", description="📋 Последние 30 объявлений"),
        BotCommand(command="dbinfo", description="🗂 Столбцы таблицы listings"),
        BotCommand(command="broadcast", description="📢 Рассылка всем пользователям"),
        BotCommand(command="send", description="✉️ Написать пользователю по ID"),
        BotCommand(command="users", description="👥 Список пользователей"),
        BotCommand(command="backup", description="💾 Скачать бэкап базы"),
        BotCommand(command="restore", description="📥 Восстановить базу из файла"),
        BotCommand(command="chat_clean", description="🧹 Убрать клавиатуру бота из чата"),
        BotCommand(command="chat_reset", description="♻️ Полный сброс меню бота в чате"),
        BotCommand(command="chat_unpin", description="📌 Открепить сообщение в чате"),
        BotCommand(command="chat_del", description="🗑 Удалить сообщение бота в чате"),
        BotCommand(command="reload_menu", description="🔄 Обновить меню команд"),
    ]
    for scope, cmds in (
        (BotCommandScopeDefault(), []),
        (BotCommandScopeAllGroupChats(), []),
        (BotCommandScopeAllChatAdministrators(), []),
        (BotCommandScopeAllPrivateChats(), []),
        (BotCommandScopeChat(chat_id=ADMIN_ID), admin_commands),
    ):
        try:
            await bot.set_my_commands(cmds, scope=scope)
        except Exception as e:
            print(f"DEBUG: set_my_commands {type(scope).__name__} error: {e}")


# ============================================================
#  FALLBACK — строго ПОСЛЕ всех остальных message-хендлеров
#  и ТОЛЬКО для личных чатов.
# ============================================================
@dp.message(~F.text, F.chat.type == ChatType.PRIVATE)
async def handle_non_text(message: types.Message, state: FSMContext):
    if (message.successful_payment or message.pinned_message
            or message.new_chat_members or message.left_chat_member):
        return
    current = await state.get_state()
    if current == AdForm.uploading_photos.state:
        await message.answer(
            "📸 Нужны именно ФОТО (сжатые), а не файл, видео или стикер.\n"
            "Отправьте фото товара или нажмите «✅ Готово!».",
            reply_markup=photos_kb(),
        )
        return
    if current == AdForm.waiting_for_restore_db.state:
        await message.answer("📁 Пришлите файл базы как ДОКУМЕНТ (.db).")
        return
    await message.answer(
        "📎 Я понимаю только текстовые сообщения.\n"
        "Напишите текстом или воспользуйтесь меню ниже 👇",
        reply_markup=bottom_menu(),
    )


@dp.message(F.text.startswith("/"), F.chat.type == ChatType.PRIVATE)
async def unknown_command(message: types.Message):
    await message.answer("🤷 Неизвестная команда. Откройте меню: /menu", reply_markup=bottom_menu())


@dp.message(F.text, F.chat.type == ChatType.PRIVATE)
async def fallback_text(message: types.Message):
    await message.answer(
        "Я не понял сообщение 🙂\nОткройте главное меню: /menu",
        reply_markup=bottom_menu(),
    )


# ---------- ЗАПУСК ----------
async def on_startup(bot: Bot):
    await init_db()
    await setup_bot_commands()
    base_url = _env("RENDER_EXTERNAL_URL")
    if base_url:
        await bot.set_webhook(f"{base_url}/webhook", secret_token=WEBHOOK_SECRET or None,
                              drop_pending_updates=False)
        print(f"Webhook установлен на {base_url}/webhook")
    else:
        print("RENDER_EXTERNAL_URL не задан — вебхук не установлен")
    print(f"DonatePay страница: {DONATEPAY_PAGE_URL}")
    print("🔒 Режим: диалоги только в личке; список команд по / — только у владельца")


async def health_check(request):
    return web.json_response({"status": "ok"})


async def main():
    await init_db()

    app = web.Application()
    app.router.add_get("/", health_check)
    SimpleRequestHandler(dispatcher=dp, bot=bot,
                         secret_token=WEBHOOK_SECRET or None).register(app, path="/webhook")

    # Порядок важен: outer-мидлвари срабатывают ДО фильтров
    dp.message.outer_middleware(AdminErrorMiddleware())
    dp.callback_query.outer_middleware(AdminErrorMiddleware())
    dp.message.outer_middleware(PrivateOnlyMiddleware())
    dp.callback_query.outer_middleware(PrivateOnlyMiddleware())
    dp.callback_query.middleware(AntiSpamMiddleware(cooldown=2.0))

    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)

    tasks = [asyncio.create_task(c) for c in (expire_loop(), unpin_loop(), backup_loop())]

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(_env("PORT", "8080")))
    await site.start()
    print(f"Сервер запущен на порту {_env('PORT', '8080')}")
    try:
        await asyncio.Event().wait()
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())