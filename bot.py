# ============================================================
#  JustTrade — bot.py  (защищённая версия)
#  ЧАСТЬ 1/4. Склейте части 1 → 2 → 3 → 4 в один файл bot.py
# ============================================================
import os
import re
import time
import shutil
import hashlib
import asyncio
import sqlite3
import traceback
from datetime import datetime, timedelta
from typing import Dict

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

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_OK = True
except ImportError:
    AESGCM = None
    CRYPTO_OK = False


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


TOKEN = _env("BOT_TOKEN")
ADMIN_ID = int(_env("ADMIN_ID", "7614009397"))
DB_PATH = _env("DB_PATH", "market.db")
SUPPORT_CONTACT = "@justtechnic1"
DONATEPAY_PAGE_URL = _env("DONATEPAY_PAGE_URL", "https://new.donatepay.ru/@1409884")
FLEA_CHAT = _env("FLEA_CHAT", "@baraholkamskkupiproday")
BACKUP_DIR = _env("BACKUP_DIR", "backups")

# ---------- ОБЯЗАТЕЛЬНЫЕ СЕКРЕТЫ (fail-fast) ----------
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
ADMIN_PIN = _env("ADMIN_PIN")
BACKUP_PASSWORD = _env("BACKUP_PASSWORD")
_audit_raw = _env("AUDIT_CHAT_ID")
AUDIT_CHAT_ID = int(_audit_raw) if re.fullmatch(r"-?\d{5,20}", _audit_raw or "") else None

if not TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", WEBHOOK_SECRET or ""):
    raise RuntimeError("WEBHOOK_SECRET обязателен: 16–256 символов, только A-Z a-z 0-9 _ -")
if ADMIN_PIN and not re.fullmatch(r"\d{6,12}", ADMIN_PIN):
    raise RuntimeError("ADMIN_PIN должен быть 6–12 цифр")
if BACKUP_PASSWORD and not CRYPTO_OK:
    print("WARNING: BACKUP_PASSWORD задан, но нет библиотеки cryptography — "
          "бэкапы не будут отправляться. Добавьте cryptography в requirements.txt")

# ---------- КАНАЛЫ ПО РАЗДЕЛАМ ----------
SECTION_CHANNELS = {
    "Электроника": ["@JustTechnic", "@baraholkamskkupiproday"],
    "Одежда": ["@JstBranded"],
    "Искусство": ["@JustArtsell"],
}
DEFAULT_SECTION = "Электроника"
SECTION_CODES = {"electronics": "Электроника", "clothes": "Одежда", "art": "Искусство"}
SECTION_BUTTONS = [("electronics", "📱 Электроника"), ("clothes", "👕 Одежда"), ("art", "🎨 Искусство")]

CATEGORY_CODES = {
    "Электроника": [
        ("phone", "Телефон", "📱 Телефон"), ("tablet", "Планшет", "📲 Планшет"),
        ("laptop", "Ноутбук", "💻 Ноутбук"), ("audio", "Аудио", "🎧 Аудио"),
        ("parts", "Запчасти/лот", "🧩 Запчасти/лот"), ("services", "Услуги", "🔨 Услуги"),
        ("other", "Прочее", "🎮 Прочее"),
    ],
    "Одежда": [
        ("outer", "Верхняя одежда", "Верхняя одежда"),
        ("shoulder", "Плечевые и поясные изделия", "Плечевые и поясные изделия"),
        ("head", "Головные уборы и аксессуары", "Головные уборы и аксессуары"),
    ],
    "Искусство": [
        ("paint", "Живопись и постеры", "Живопись и постеры"),
        ("figure", "Фигурки и статуэтки", "Фигурки и статуэтки"),
        ("book", "Книги и документы", "Книги и документы"),
        ("hand", "Ручная работа", "Ручная работа"),
        ("artother", "Другое", "Другое"),
    ],
}
CODE_TO_CATEGORY = {c: (s, n) for s, items in CATEGORY_CODES.items() for c, n, _ in items}

SUBCATEGORIES = {
    "Электроника": {
        "Аудио": ["Наушники", "Колонка", "Микрофон"],
        "Запчасти/лот": ["Запчасти", "Лот"],
        "Прочее": ["Аксессуары", "Приставка", "Дисплей", "Клавиатура", "Мышка"],
    },
    "Одежда": {
        "Верхняя одежда": ["Пальто", "Куртки", "Плащи", "Пуховики", "Ветровки"],
        "Плечевые и поясные изделия": ["Футболки", "Рубашки", "Свитеры", "Платья",
                                       "Костюмы", "Брюки", "Юбки", "Шорты"],
        "Головные уборы и аксессуары": ["Шапки", "Кепки", "Перчатки", "Шарфы", "Ремни"],
    },
    "Искусство": {
        "Живопись и постеры": ["Картина", "Постер", "Гобелен"],
        "Фигурки и статуэтки": ["Статуэтка", "Фигура"],
        "Книги и документы": ["Книга", "Документ", "Автограф"],
    },
}
NO_SIZE_CATEGORIES = {"Прочее", "Услуги", "Запчасти/лот", "Аудио"}
CLOTHES_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]
HASHTAG_ALIASES = {"Запчасти/лот": "ЗапчастиЛот"}

STARS_PRICE = 25
MAX_PHOTOS = 10
MAX_CAPTION = 1024
MAX_PENDING = 3
LIMIT_MODEL, LIMIT_SIZE, LIMIT_DESCRIPTION = 200, 30, 700
LIMIT_CITY, LIMIT_KEYWORD, MAX_SUBSCRIPTIONS = 100, 50, 20
RESTORE_MAX_FILES, RESTORE_MAX_MB = 10, 20
SUDO_TTL = 300

MENU_MAIN, MENU_PROFILE, MENU_LOTS = "🏡 Главное меню", "🥷🏻 Профиль", "📚 Мои лоты"
MENU_BUTTONS = {MENU_MAIN, MENU_PROFILE, MENU_LOTS}
AD_TYPE_NAMES = {"sell": "💵 Продажа", "buy": "🕵🏻 Покупка", "exchange": "🔁 Обмен"}

bot = Bot(token=TOKEN, default=DefaultBotProperties(link_preview_is_disabled=True))
dp = Dispatcher(storage=MemoryStorage())


class AdForm(StatesGroup):
    choosing_section = State()
    choosing_category = State()
    choosing_subcategory = State()
    entering_model = State()
    entering_memory = State()
    entering_size = State()
    entering_description = State()
    entering_price = State()
    entering_city = State()
    choosing_delivery = State()
    uploading_photos = State()
    preview = State()
    waiting_for_restore_db = State()
    waiting_for_subscription_keyword = State()


# ================== БЕЗОПАСНОСТЬ: БАЗА ==================
def scrub(text) -> str:
    """Вырезает все секреты из любого текста перед логом/отправкой."""
    s = str(text)
    for secret in (TOKEN, WEBHOOK_SECRET, ADMIN_PIN, BACKUP_PASSWORD):
        if secret:
            s = s.replace(secret, "***SECRET***")
    return re.sub(r"bot\d{6,}:[A-Za-z0-9_-]{20,}", "bot***SECRET***", s)


def log(*args):
    print(scrub(" ".join(str(a) for a in args)))


async def audit(text: str):
    if AUDIT_CHAT_ID is None:
        return
    try:
        await bot.send_message(AUDIT_CHAT_ID, scrub(text), parse_mode="HTML",
                               disable_notification=True)
    except Exception as e:
        log("audit failed:", e)


PANIC = {"on": False, "since": ""}
_sudo_until: Dict[int, float] = {}


def is_sudo(uid: int) -> bool:
    return time.time() < _sudo_until.get(uid, 0)


async def require_sudo(message: types.Message) -> bool:
    if message.from_user.id != ADMIN_ID:
        return False
    if not ADMIN_PIN:
        await message.answer("🔒 Задайте <code>ADMIN_PIN</code> в переменных Render — "
                             "без него опасные команды отключены.", parse_mode="HTML")
        return False
    if is_sudo(ADMIN_ID):
        return True
    await message.answer("🔒 Нужен <code>/sudo PIN</code> — команда меняет данные, "
                         "рассылает сообщения или выгружает базу.", parse_mode="HTML")
    return False


# ================== ШИФРОВАНИЕ БЭКАПОВ ==================
def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000, 32)


def encrypt_file(src: str, dst: str, password: str):
    salt, nonce = os.urandom(16), os.urandom(12)
    with open(src, "rb") as f:
        data = f.read()
    ct = AESGCM(_derive_key(password, salt)).encrypt(nonce, data, None)
    with open(dst, "wb") as f:
        f.write(b"JTB1" + salt + nonce + ct)


def decrypt_file(src: str, dst: str, password: str):
    with open(src, "rb") as f:
        raw = f.read()
    if not raw.startswith(b"JTB1"):
        raise ValueError("не зашифрованный файл JustTrade")
    salt, nonce, ct = raw[4:20], raw[20:32], raw[32:]
    data = AESGCM(_derive_key(password, salt)).decrypt(nonce, ct, None)
    with open(dst, "wb") as f:
        f.write(data)


async def make_snapshot() -> str:
    """Консистентный снимок базы (не копирование файла!)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.db")
    if os.path.exists(path):
        os.remove(path)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"VACUUM INTO '{path}'")
    return path


# ================== ХЕЛПЕРЫ ==================
def row_get(row, key, default=None):
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


def hashtag(text) -> str:
    if not text:
        return ""
    raw = str(text).strip()
    if raw in HASHTAG_ALIASES:
        return "#" + HASHTAG_ALIASES[raw]
    parts = [p for p in re.split(r"[^\w]+", raw, flags=re.UNICODE) if p]
    if not parts:
        return ""
    joined = "".join(p[0].upper() + p[1:] for p in parts)
    joined = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", joined)
    return "#" + joined if joined else ""


def _strip_tags(t: str) -> str:
    return re.sub(r"<[^>]*>", "", t or "")


def tg_len(t: str) -> int:
    return len((t or "").encode("utf-16-le")) // 2


def get_section(obj) -> str:
    s = obj.get("section") if isinstance(obj, dict) else row_get(obj, "section")
    return s if s in SECTION_CHANNELS else DEFAULT_SECTION


def form_label(data) -> str:
    if isinstance(data, dict):
        return data.get("subcategory") or data.get("category") or "—"
    return row_get(data, "subcategory") or row_get(data, "category") or "—"


def size_label(section: str) -> str:
    return {"Одежда": "📏Размер", "Искусство": "📐Размер"}.get(section, "💾Память")


def section_icon(section: str) -> str:
    return {"Одежда": "👕", "Искусство": "🎨"}.get(section, "📱")


def name_prompt(section: str) -> str:
    return ("Напишите модель устройства или ключевую особенность." if section == "Электроника"
            else "Напишите название товара или ключевую особенность.")


def desc_prompt(section: str, size_asked: bool = True) -> str:
    if section == "Электроника":
        base = ("Теперь нужно сделать описание: внешний вид, состояние, "
                "комплектация, ремонты, возможен ли торг.")
        tail = "\nПамять уже указана, повторять не нужно." if size_asked else ""
    else:
        base = ("Теперь нужно сделать описание: внешний вид, состояние, "
                "комплектация, материалы изделия и возможен ли торг.")
        tail = "\nРазмеры уже указаны, повторять не обязательно."
    return base + tail


def delivery_prompt(section: str) -> str:
    word = "устройство" if section == "Электроника" else "товар"
    return f"Отправите ли вы {word} через какую либо транспортную компанию?"


def make_author_tag(username, first_name, user_id) -> str:
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
            async with db.execute("SELECT verified FROM users WHERE user_id=?", (user_id,)) as cur:
                row = await cur.fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False


def fsm_for_user(user_id: int) -> FSMContext:
    return FSMContext(storage=dp.storage,
                      key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id))


async def get_clean_text(message: types.Message, limit: int):
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустое сообщение. Напишите, пожалуйста, текстом.")
        return None
    if len(text) > limit:
        await message.answer(f"⚠️ Слишком длинно: {len(text)} симв. Максимум — {limit}.")
        return None
    return text


class TextAnswer(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        return text not in MENU_BUTTONS


# ================== ТЕКСТ ПОСТА ==================
def build_post_text(ad, status="АКТУАЛЬНО", author_tag=None, author_id=None,
                    verified=False, max_len=MAX_CAPTION) -> str:
    section = get_section(ad)
    model = str(row_get(ad, "model", "—"))
    price = str(row_get(ad, "price", "—"))
    city = str(row_get(ad, "city", "—"))
    category = str(row_get(ad, "category", "—"))
    description = str(row_get(ad, "description", ""))
    delivery = str(row_get(ad, "delivery", "—"))
    size_val = row_get(ad, "memory")
    subcat = row_get(ad, "subcategory")
    uid = row_get(ad, "user_id", author_id)

    tag = author_tag or (f'<a href="tg://user?id={author_id}">Продавец</a>'
                         if author_id else escape_html(str(uid)))
    suffix = (" ✅" if status == "ПРОДАНО" else " ✅ Проверенный продавец") if verified else ""

    if status == "ПРОДАНО":
        return (f"❌ПРОДАНО❌\n📦 {escape_html(model)}\n💸 {escape_html(price)}\n"
                f"🇷🇺 {escape_html(city)}\n\n🥷🏻 {tag}{suffix}")

    header = "✅АКТУАЛЬНО✅"
    if bool(row_get(ad, "is_promoted", 0)):
        header = "🔥 ТОП\n" + header
    tags_line = " ".join(t for t in (hashtag(section), hashtag(category), hashtag(subcat)) if t)
    head = f"{header}\n{tags_line}\n{hashtag(city)}\n\n📦 {model}\n\n"
    if size_val and size_val != "—":
        head += f"{size_label(section)}: {size_val}\n\n"
    tail = f"\n\n🇷🇺{city}\n💸{price}\n🚛{delivery}\n\n🥷🏻"
    overhead = tg_len(head) + tg_len(tail) + tg_len(_strip_tags(tag)) + tg_len(suffix)
    budget = max(0, max_len - overhead)
    if tg_len(description) > budget:
        description = description[:max(0, budget - 1)].rstrip() + "…"
    return escape_html(head) + escape_html(description) + escape_html(tail) + tag + suffix


def iter_first_posts(ad):
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


def iter_all_posts(ad):
    raw = row_get(ad, "message_ids", "") or ""
    for pair in [p.strip() for p in raw.split(",") if p.strip()]:
        if ":" not in pair:
            continue
        ch, mid = pair.rsplit(":", 1)
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
            log(f"edit post {ch}/{mid} failed:", e)


async def pin_first_posts(ad):
    for ch, mid in iter_first_posts(ad):
        try:
            await bot.pin_chat_message(chat_id=ch, message_id=mid, disable_notification=True)
        except Exception as e:
            log(f"pin {ch}/{mid} failed:", e)


async def unpin_first_posts(ad):
    for ch, mid in iter_first_posts(ad):
        try:
            await bot.unpin_chat_message(chat_id=ch, message_id=mid)
        except Exception:
            pass


async def rebuild_caption(ad, status="АКТУАЛЬНО", promoted=None, verified=None) -> str:
    d = dict(ad)
    if promoted is not None:
        d["is_promoted"] = 1 if promoted else 0
    uid = d["user_id"]
    ver = await is_verified(uid) if verified is None else verified
    caption = build_post_text(d, status=status, author_tag=await get_author_tag(uid),
                              author_id=uid, verified=ver)
    await edit_post_caption_only_first(d, caption)
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


# ================== БД ==================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, ad_type TEXT, section TEXT, category TEXT, subcategory TEXT,
                model TEXT, memory TEXT, description TEXT, price TEXT, city TEXT,
                delivery TEXT, photos TEXT, status TEXT DEFAULT 'DRAFT',
                created_at TIMESTAMP, expires_at TIMESTAMP, is_trial BOOLEAN DEFAULT 0,
                channel TEXT, message_id INTEGER, message_ids TEXT, charge_id TEXT,
                is_promoted INTEGER DEFAULT 0, promoted_until TIMESTAMP,
                seller_verified INTEGER DEFAULT 0, full_caption TEXT
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, first_seen TIMESTAMP,
                verified INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, keyword TEXT NOT NULL, created_at TIMESTAMP
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                charge_id TEXT PRIMARY KEY, user_id INTEGER, payload TEXT,
                amount INTEGER, created_at TIMESTAMP
            )""")
        await db.commit()

        async with db.execute("PRAGMA table_info(listings)") as cur:
            existing = {r[1] for r in await cur.fetchall()}
        required = {
            "section": "TEXT", "subcategory": "TEXT", "message_ids": "TEXT", "charge_id": "TEXT",
            "is_promoted": "INTEGER DEFAULT 0", "promoted_until": "TIMESTAMP",
            "seller_verified": "INTEGER DEFAULT 0", "full_caption": "TEXT",
            "ad_type": "TEXT", "model": "TEXT", "memory": "TEXT", "description": "TEXT",
            "price": "TEXT", "city": "TEXT", "delivery": "TEXT", "photos": "TEXT",
            "status": "TEXT DEFAULT 'DRAFT'", "created_at": "TIMESTAMP",
            "expires_at": "TIMESTAMP", "is_trial": "BOOLEAN DEFAULT 0",
            "channel": "TEXT", "message_id": "INTEGER",
        }
        for col, ctype in required.items():
            if col not in existing:
                try:
                    await db.execute(f"ALTER TABLE listings ADD COLUMN {col} {ctype}")
                    await db.commit()
                except sqlite3.OperationalError as e:
                    log(f"ALTER {col} failed:", e)

        async with db.execute("PRAGMA table_info(users)") as cur:
            ucols = {r[1] for r in await cur.fetchall()}
        for col, ctype in (("verified", "INTEGER DEFAULT 0"), ("blocked", "INTEGER DEFAULT 0")):
            if col not in ucols:
                try:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")
                    await db.commit()
                except sqlite3.OperationalError:
                    pass

        await db.execute("CREATE INDEX IF NOT EXISTS idx_listings_user ON listings(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id)")
        await db.commit()
    log("init_db finished")


async def save_listing(user_id, data, status="PENDING_MODERATION", is_trial=False):
    async with aiosqlite.connect(DB_PATH) as db:
        duration = timedelta(hours=24) if is_trial else timedelta(days=7)
        now = datetime.now()
        cur = await db.execute("""
            INSERT INTO listings (
                user_id, ad_type, section, category, subcategory, model, memory, description,
                price, city, delivery, photos, status, created_at, expires_at, is_trial, charge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            user_id, data.get("ad_type"), get_section(data), data.get("category"),
            data.get("subcategory"), data.get("model"), data.get("memory"),
            data.get("description"), data.get("price"), data.get("city"),
            data.get("delivery"), ",".join(data.get("photos") or []),
            status, now.isoformat(), (now + duration).isoformat(),
            1 if is_trial else 0, data.get("charge_id"),
        ))
        await db.commit()
        return cur.lastrowid


# ================== КЛАВИАТУРЫ ==================
def bottom_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=MENU_MAIN), KeyboardButton(text=MENU_PROFILE)],
        [KeyboardButton(text=MENU_LOTS)],
    ], resize_keyboard=True, is_persistent=True)


def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои товары", callback_data="my_ads")],
        [InlineKeyboardButton(text="🔔 Подписки", callback_data="subs_menu")],
        [InlineKeyboardButton(text="🥷🏻 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="📣 Электроника", url="https://t.me/JustTechnic")],
        [InlineKeyboardButton(text="👕 Одежда", url="https://t.me/JstBranded")],
        [InlineKeyboardButton(text="🎨 Искусство", url="https://t.me/JustArtsell")],
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


def section_kb():
    kb = [[InlineKeyboardButton(text=l, callback_data=f"sec_{c}")] for c, l in SECTION_BUTTONS]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_adtype")])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def category_kb(section):
    kb = [[InlineKeyboardButton(text=l, callback_data=f"cat_{c}")]
          for c, n, l in CATEGORY_CODES.get(section, [])]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_section")])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def subcategory_kb(subcats):
    kb = [[InlineKeyboardButton(text=n, callback_data=f"subcat_{n}")] for n in subcats]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_category")])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def size_kb():
    rows = [[InlineKeyboardButton(text=s, callback_data=f"size_{s}")
             for s in CLOTHES_SIZES[i:i + 3]] for i in range(0, len(CLOTHES_SIZES), 3)]
    rows.append([InlineKeyboardButton(text="✍️ Другой размер", callback_data="size_other")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_model")])
    rows.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delivery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправлю", callback_data="del_yes")],
        [InlineKeyboardButton(text="❌ Не отправлю", callback_data="del_no")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_city")],
        [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")],
    ])


def step_kb(back):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data=back)],
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


def help_menu_kb(with_back=True):
    kb = [
        [InlineKeyboardButton(text="📄 Правила публикации", callback_data="help_rules")],
        [InlineKeyboardButton(text="❔ Как выставить?", callback_data="help_howto")],
        [InlineKeyboardButton(text="⚠️ Отказ в публикации", callback_data="help_reject")],
        [InlineKeyboardButton(text="🔐 Мои данные", callback_data="help_privacy")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
    ]
    if with_back:
        kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


PROMOTE_TEXT = ("🚀 <b>Поднятие объявления в топ</b>\n\n"
                "Объявление будет закреплено в своём канале на 7 дней с пометкой 🔥 ТОП.\n\n"
                "Выберите способ оплаты:")


def promote_pay_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="25⭐️", callback_data="promote_pay_stars")],
        [InlineKeyboardButton(text="25💸", callback_data="promote_pay_donatepay")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="my_ads")],
    ])


SECTION_PROMPT = (
    "Выберите раздел, к которому относится товар:\n\n"
    "📱 Электроника — телефоны, планшеты, ноутбуки, аудио, запчасти, услуги\n"
    "👕 Одежда — верхняя одежда, плечевые и поясные изделия, головные уборы и аксессуары\n"
    "🎨 Искусство — живопись, фигурки, книги, ручная работа")
ELECTRONICS_PROMPT = (
    "Выберите тип устройства — для каждого есть подсказка:\n\n"
    "📱 Телефон — кнопочные, сенсорные, раскладушки (Nokia, iPhone, Samsung)\n"
    "📲 Планшет — iPad, Xiaomi pad, Samsung pad\n"
    "💻 Ноутбук — нетбук, лэптоп, ноутбук (Asus, Dell и др)\n"
    "🎧 Аудио — наушники, колонки, саундбары\n"
    "🧩 Запчасти/лот — дисплей, АКБ и тд, несколько разных вещей\n"
    "🔨 Услуги — ремонт техники\n"
    "🎮 Прочее — Xbox, игры, телевизор и тд")
PHOTOS_PROMPT = (
    "📸Фотографии\n\nПокажите фото товара, которое сейчас опубликуете.\n\n"
    "Фото обязательно! До 10 штук.\n\n"
    "Фото должны быть настоящими, а не из интернета, иначе отклоним запрос на публикацию.\n\n"
    "Как отправили всё, что хотели опубликовать — нажмите «✅ Готово!»")
FORM_INTRO = (
    "Нужно выбрать, что ты хочешь сделать с товаром.\n"
    "Далее — по пунктам: раздел, тип товара, описание, цена, город, доставка, фото товара.\n\n"
    "Отмена любых действий: /cancel")


def category_prompt(section):
    return ELECTRONICS_PROMPT if section == "Электроника" else f"{section}.\n\nВыберите категорию:"


def name_step_content(data):
    subcat = data.get("subcategory")
    prefix = f"{data.get('category')} → {subcat}" if subcat else (data.get("category") or "—")
    back = "back_to_subcategory" if subcat else "back_to_category"
    return f"{prefix}.\n\n{name_prompt(get_section(data))}", step_kb(back)


def memory_step_content(data):
    return (f"{form_label(data)}, 💾Память\n\nНапишите память устройства, формат ОЗУ/ПЗУ.\n"
            "Без текста Гб и без лишних символов."), step_kb("back_to_model")


def size_step_content(data):
    label = form_label(data)
    if get_section(data) == "Одежда":
        return (f"{label}, 📏Размер\n\nУкажите точный размер одежды: "
                "XS, S, M, L, XL, 2XL, 3XL, 4XL и так далее."), size_kb()
    return (f"{label}, 📐Размер изделия\n\nУкажите размер изделия как в примере: "
            "12/10/11 или 12/10 см или м (в зависимости от размеренности)."), step_kb("back_to_model")


# ================== МИДЛВАРИ ==================
class PrivateOnlyMiddleware(BaseMiddleware):
    """В группах и каналах бот молчит всегда."""
    async def __call__(self, handler, event, data):
        chat = None
        if isinstance(event, types.Message):
            chat = event.chat
        elif isinstance(event, types.CallbackQuery):
            chat = getattr(event.message, "chat", None)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)


class PanicMiddleware(BaseMiddleware):
    """Kill switch: при /panic on обслуживается только владелец."""
    async def __call__(self, handler, event, data):
        if PANIC["on"]:
            user = getattr(event, "from_user", None)
            if user and user.id == ADMIN_ID:
                return await handler(event, data)
            if isinstance(event, types.CallbackQuery):
                try:
                    await event.answer("🛠 Технические работы. Попробуйте позже.", show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, types.Message):
                try:
                    await event.answer("🛠 Бот на техническом обслуживании. Попробуйте позже.")
                except Exception:
                    pass
            return None
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Token bucket на пользователя — защита от спама и флуда."""
    def __init__(self, rate: float = 0.7, burst: int = 6):
        self.rate, self.burst = rate, burst
        self.buckets: Dict[int, list] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID:
            now = time.time()
            tokens, ts = self.buckets.get(user.id, [self.burst, now])
            tokens = min(self.burst, tokens + (now - ts) * self.rate)
            if tokens < 1:
                self.buckets[user.id] = [tokens, now]
                if isinstance(event, types.CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                return None
            self.buckets[user.id] = [tokens - 1, now]
            if len(self.buckets) > 5000:
                self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 300}
        return await handler(event, data)


class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, cooldown: float = 2.0):
        self.cooldown = cooldown
        self.last_press: Dict[tuple, float] = {}

    async def __call__(self, handler, event, data):
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
                return None
            self.last_press[key] = now
            if len(self.last_press) > 1000:
                self.last_press = {k: v for k, v in self.last_press.items() if now - v > 60}
        return await handler(event, data)


class AdminErrorMiddleware(BaseMiddleware):
    def __init__(self):
        self._last_sent: Dict[str, float] = {}

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as e:
            tb = scrub(traceback.format_exc())[-1500:]
            key = f"{type(e).__name__}:{str(e)[:120]}"
            now = time.time()
            if now - self._last_sent.get(key, 0) > 60:
                self._last_sent[key] = now
                uid = getattr(getattr(event, "from_user", None), "id", "—")
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Ошибка в боте</b>\n\nUser: <code>{uid}</code>\n"
                        f"<code>{escape_html(scrub(e))}</code>\n\n<pre>{escape_html(tb)}</pre>",
                        parse_mode="HTML")
                except Exception:
                    pass
            log("handler error:", e)
            raise
# ============================================================
#  ЧАСТЬ 2/4. Сначала /sudo и /panic, затем остальные команды.
# ============================================================

@dp.message(Command("sudo"))
async def cmd_sudo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split(maxsplit=1)
    try:
        await message.delete()          # PIN не остаётся в истории чата
    except Exception:
        pass
    if not ADMIN_PIN:
        await message.answer("🔒 ADMIN_PIN не задан в переменных Render.")
        return
    if len(parts) < 2 or parts[1].strip() != ADMIN_PIN:
        await audit(f"🚫 Неверный PIN от <code>{message.from_user.id}</code>")
        await message.answer("❌ Неверный PIN.")
        return
    _sudo_until[ADMIN_ID] = time.time() + SUDO_TTL
    await audit("🔓 Владелец включил режим подтверждения на 5 минут")
    await message.answer(f"🔓 Подтверждение активно {SUDO_TTL // 60} минут. Повторите команду.")


@dp.message(Command("panic"))
async def cmd_panic(message: types.Message):
    if not await require_sudo(message):
        return
    arg = (message.text or "").split(maxsplit=1)
    mode = arg[1].strip().lower() if len(arg) > 1 else ""
    if mode not in ("on", "off"):
        await message.answer(
            f"Использование: <code>/panic on</code> или <code>/panic off</code>\n"
            f"Сейчас: {'🔴 ВКЛ' if PANIC['on'] else '🟢 выкл'}", parse_mode="HTML")
        return
    PANIC["on"] = (mode == "on")
    PANIC["since"] = datetime.now().isoformat() if PANIC["on"] else ""
    await audit(f"🚨 Panic mode: <b>{mode.upper()}</b>")
    await message.answer("🔴 Бот закрыт для всех, кроме вас. Выключить: /panic off"
                         if PANIC["on"] else "🟢 Бот снова работает для всех.")


@dp.message(Command("security"))
async def cmd_security(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "🔐 <b>Состояние защиты</b>\n\n"
        f"Вебхук-секрет: ✅ задан ({len(WEBHOOK_SECRET)} симв.)\n"
        f"ADMIN_PIN: {'✅ задан' if ADMIN_PIN else '❌ НЕ задан'}\n"
        f"Режим /sudo: {'🔓 активен' if is_sudo(ADMIN_ID) else '🔒 закрыт'}\n"
        f"Panic mode: {'🔴 ВКЛ' if PANIC['on'] else '🟢 выкл'}\n"
        f"Аудит-канал: {'✅ ' + str(AUDIT_CHAT_ID) if AUDIT_CHAT_ID else '❌ не задан'}\n"
        f"Шифрование бэкапов: {'✅ включено' if (BACKUP_PASSWORD and CRYPTO_OK) else '❌ выключено'}\n"
        f"Проверка платежей у Telegram: ✅ включена\n"
        f"Лимит сообщений: ✅ 0.7/сек, всплеск 6\n"
        f"Лимит на модерации: {MAX_PENDING} объявления на пользователя",
        parse_mode="HTML")


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
                         (message.from_user.id, datetime.now().isoformat()))
        await db.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer("Добро пожаловать в JustTrade!", reply_markup=bottom_menu())
    await show_main_menu(message)


async def show_main_menu(message: types.Message, user_id: int = None, first_name: str = None):
    uid = user_id or message.from_user.id
    name = first_name or message.from_user.first_name or "друг"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM listings WHERE user_id=? AND status='ACTIVE'",
                              (uid,)) as cur:
            count = (await cur.fetchone())[0]
    await message.answer(
        f"Добрый день, {name}!\n\n"
        f"JustTrade — электроника, одежда и искусство в одном месте;)\n\n"
        f"Ваши объявления: {count}", reply_markup=main_menu_kb())


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
    await message.answer("🔔 <b>Подписки на новые объявления</b>\n\n"
                         "Работают по всем разделам: Электроника, Одежда, Искусство.",
                         reply_markup=subscriptions_menu_kb(), parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("❓ <b>Помощь</b>\n\nВыберите раздел:",
                         reply_markup=help_menu_kb(with_back=False), parse_mode="HTML")


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
        async with db.execute("SELECT first_seen, verified FROM users WHERE user_id=?", (user_id,)) as cur:
            u = await cur.fetchone()
        first_seen = (row_get(u, "first_seen", "") or "")[:10] or "—"
        verified = row_get(u, "verified", 0)
        async with db.execute("SELECT COUNT(*) FROM listings WHERE user_id=? AND status='ACTIVE'",
                              (user_id,)) as cur:
            active = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM listings WHERE user_id=? AND status='SOLD'",
                              (user_id,)) as cur:
            sold = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT model, section, channel, message_id FROM listings "
            "WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 30", (user_id,)) as cur:
            ads = await cur.fetchall()
    lines = ""
    for ad in ads:
        icon = section_icon(get_section(ad))
        name = escape_html(row_get(ad, "model", "—"))
        ch, mid = row_get(ad, "channel"), row_get(ad, "message_id")
        if ch and mid:
            lines += f'{icon} <a href="https://t.me/{str(ch).lstrip("@")}/{mid}">{name}</a>\n'
        else:
            lines += f"{icon} {name}\n"
    return (f"👤 {escape_html(first_name)}{' ✅' if verified else ''}\n"
            f"Профиль в JustTrade создан {first_seen}\n"
            f"Объявления: {active} актуально\nПродано: {sold}\n"
            f"Телеграмм: {f'@{username}' if username else 'нет'}\n\n"
            f"Сейчас в каналах:\n{lines or 'нет активных объявлений'}")


async def _send_profile(target_message: types.Message, user):
    text = await build_profile_text(user.id, user.first_name, user.username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="📚 Мои лоты", callback_data="my_ads")],
        [InlineKeyboardButton(text="🛡 Верификация (25 ⭐️)", callback_data="verify_start")],
        [InlineKeyboardButton(text="🗑 Удалить мои данные", callback_data="forget_ask")],
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
            "SELECT * FROM listings WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 20",
            (user_id,)) as cur:
            ads = await cur.fetchall()
    if not ads:
        await target_message.answer("У вас нет активных объявлений.", reply_markup=main_menu_kb())
        return
    lines = "\n".join(f"{section_icon(get_section(a))} {row_get(a,'model','—')} — "
                      f"{row_get(a,'price','—')} руб." for a in ads)
    kb = []
    for ad in ads:
        kb.append([InlineKeyboardButton(
            text=f"{row_get(ad,'model','—')} ({row_get(ad,'price','—')} руб.)",
            callback_data=f"lot_{ad['id']}")])
        if not bool(row_get(ad, "is_promoted", 0)):
            kb.append([InlineKeyboardButton(text=f"🚀 Поднять в топ #{ad['id']} (25 ⭐️)",
                                            callback_data=f"promote_lot_{ad['id']}")])
    kb.append([InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")])
    await target_message.answer(
        f"📃 Мои объявления\n{len(ads)} в каналах\n\n{lines}\n\n"
        "Нажми на объявление ниже — там все функции.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data == "my_ads")
async def cb_my_ads(callback: types.CallbackQuery):
    await _send_my_ads(callback.message, callback.from_user.id)
    await callback.answer()


# ---------- ТАРИФ ----------
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
        "автоматически снято с продажи. Продление — 25 руб или 25 звёзд на 7 дней.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Продолжить", callback_data="free_continue")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]]))
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
        "Отлично! Ваше объявление будет выставлено на 7 дней, после окончания срока его "
        "можно продлить.\n\nВыберите способ оплаты:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="25⭐️", callback_data="pay_stars")],
            [InlineKeyboardButton(text="25💸", callback_data="pay_donatepay")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")]]))
    await callback.answer()


# ---------- ОПЛАТА ----------
@dp.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("paid_context") == "renew":
        lot_id = data.get("renew_lot_id")
        if not lot_id:
            await callback.answer("Объявление не найдено.", show_alert=True)
            return
        await state.update_data(moderation_sent=False)
        payload, title = f"renew_{lot_id}_{callback.from_user.id}", "Продление объявления на 7 дней"
    else:
        payload, title = f"pub_{callback.from_user.id}", "Публикация объявления на 7 дней"
    await bot.send_invoice(chat_id=callback.from_user.id, title=title,
                           description="JustTrade — размещение объявления",
                           payload=payload, currency="XTR",
                           prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="")
    await callback.answer()


@dp.callback_query(F.data == "pay_donatepay")
async def cb_pay_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_initiated=True, donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "⚠️ Пожертвование проверяет владелец бота @JustTechnic1. Потребуется:\n\n"
        "1) Отправить донат по ссылке ниже\n"
        "2) В строке «Имя» указать ваш никнейм/юзернейм\n"
        "3) Нажать «✅ Готово» — админ проверит поступление и подтвердит.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="publish")],
            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]]))
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
    uname = f"@{callback.from_user.username}" if callback.from_user.username else "нет username"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_confirm_{uid}_{context}_{lot_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")]])
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Заявка DonatePay ({'продление' if context=='renew' else 'публикация'})</b>\n\n"
            f"{escape_html(callback.from_user.first_name)} ({uname})\n"
            f"ID: <code>{uid}</code>\nОбъявление: <code>{lot_id or 'новое'}</code>\n\n"
            f"Сверьте никнейм в кабинете DonatePay и поступление {STARS_PRICE}₽.",
            reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log("donatepay request failed:", e)
        await callback.answer("Ошибка, напишите в поддержку.", show_alert=True)
        return
    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(callback.message, "✅ Заявка отправлена админу. Ожидайте подтверждения.")
    await callback.answer()


# ---------- ТОП ----------
@dp.callback_query(F.data.startswith("promote_lot_"))
async def cb_promote(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    lot_id = int(parts[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id=? AND user_id=? AND status='ACTIVE'",
                              (lot_id, callback.from_user.id)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await state.update_data(promote_lot_id=lot_id, paid_context="promote", donatepay_pending=False)
    await safe_edit_or_caption(callback.message, PROMOTE_TEXT, promote_pay_kb())
    await callback.answer()


@dp.callback_query(F.data == "promote_pay_stars")
async def cb_promote_stars(callback: types.CallbackQuery, state: FSMContext):
    lot_id = (await state.get_data()).get("promote_lot_id")
    if not lot_id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await bot.send_invoice(chat_id=callback.from_user.id, title="Поднятие объявления в топ",
                           description="Закрепление в канале на 7 дней + пометка ТОП",
                           payload=f"promote_{lot_id}_{callback.from_user.id}", currency="XTR",
                           prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="")
    await callback.answer()


@dp.callback_query(F.data == "promote_pay_donatepay")
async def cb_promote_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "⚠️ Для поднятия в топ отправьте донат 25₽, в поле «Имя» укажите свой юзернейм "
        "и нажмите «✅ Готово».",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="promote_donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="promote_back")]]))
    await callback.answer()


@dp.callback_query(F.data == "promote_back")
async def cb_promote_back(callback: types.CallbackQuery, state: FSMContext):
    if (await state.get_data()).get("promote_lot_id"):
        await safe_edit_or_caption(callback.message, PROMOTE_TEXT, promote_pay_kb())
    else:
        await safe_edit_or_caption(callback.message, "Объявление не найдено.", main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "promote_donatepay_done")
async def cb_promote_donatepay_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("donatepay_pending"):
        await callback.answer("Заявка уже отправлена.", show_alert=True)
        return
    lot_id = data.get("promote_lot_id")
    if not lot_id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    uid = callback.from_user.id
    uname = f"@{callback.from_user.username}" if callback.from_user.username else "нет username"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_promote_confirm_{uid}_{lot_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")]])
    try:
        await bot.send_message(ADMIN_ID,
                               f"💰 <b>Заявка DonatePay (ТОП)</b>\n\n"
                               f"{escape_html(callback.from_user.first_name)} ({uname})\n"
                               f"ID: <code>{uid}</code>\nОбъявление #{lot_id}\n\nПроверьте 25₽.",
                               reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log("promote request failed:", e)
        await callback.answer("Ошибка, напишите в поддержку.", show_alert=True)
        return
    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(callback.message, "✅ Заявка отправлена админу. Ожидайте подтверждения.")
    await callback.answer()


# ---------- ВЕРИФИКАЦИЯ ----------
@dp.callback_query(F.data == "verify_start")
async def cb_verify_start(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(paid_context="verify", donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message,
        "🛡 <b>Верификация продавца</b>\n\n"
        "Галочка «✅ Проверенный продавец» появится в профиле и во всех ваших объявлениях.\n\n"
        "Выберите способ оплаты:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="25⭐️", callback_data="verify_stars")],
            [InlineKeyboardButton(text="25💸", callback_data="verify_donatepay")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="profile")]]))
    await callback.answer()


@dp.callback_query(F.data == "verify_stars")
async def cb_verify_stars(callback: types.CallbackQuery):
    await bot.send_invoice(chat_id=callback.from_user.id, title="Верификация продавца",
                           description="Галочка «Проверенный продавец»",
                           payload=f"verify_{callback.from_user.id}", currency="XTR",
                           prices=[LabeledPrice(label="XTR", amount=STARS_PRICE)], provider_token="")
    await callback.answer()


@dp.callback_query(F.data == "verify_donatepay")
async def cb_verify_donatepay(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(donatepay_pending=False)
    await safe_edit_or_caption(
        callback.message, "⚠️ Для верификации через DonatePay отправьте донат и нажмите «✅ Готово».",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Перейти к оплате", url=DONATEPAY_PAGE_URL)],
            [InlineKeyboardButton(text="✅ Готово", callback_data="verify_donatepay_done")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="verify_start")]]))
    await callback.answer()


@dp.callback_query(F.data == "verify_donatepay_done")
async def cb_verify_donatepay_done(callback: types.CallbackQuery, state: FSMContext):
    if (await state.get_data()).get("donatepay_pending"):
        await callback.answer("Заявка уже отправлена.", show_alert=True)
        return
    uid = callback.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dp_verify_confirm_{uid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dp_reject_{uid}")]])
    try:
        await bot.send_message(ADMIN_ID,
                               f"🛡 <b>Верификация (DonatePay)</b>\n\n"
                               f"{escape_html(callback.from_user.first_name)} "
                               f"(@{callback.from_user.username or 'нет'})\n"
                               f"ID: <code>{uid}</code>\n\nПодтвердите оплату 25₽.",
                               reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.answer("Ошибка.", show_alert=True)
        return
    await state.update_data(donatepay_pending=True)
    await safe_edit_or_caption(callback.message, "✅ Заявка отправлена админу.")
    await callback.answer()


# ---------- ПОДТВЕРЖДЕНИЯ АДМИНА ----------
async def mark_verified(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
                         (user_id, datetime.now().isoformat()))
        await db.execute("UPDATE users SET verified=1 WHERE user_id=?", (user_id,))
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE user_id=? AND status='ACTIVE'",
                              (user_id,)) as cur:
            ads = await cur.fetchall()
        for ad in ads:
            caption = await rebuild_caption(ad, verified=True)
            await db.execute("UPDATE listings SET full_caption=? WHERE id=?", (caption, ad["id"]))
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
    await audit(f"🛡 Верификация выдана вручную: <code>{user_id}</code>")
    try:
        await bot.send_message(user_id, "✅ Верификация подтверждена! Галочка появилась в профиле.")
    except Exception:
        pass
    await safe_append_and_edit(callback.message, "\n\n✅ Подтверждено")
    await callback.answer("Подтверждено")


@dp.callback_query(F.data.startswith("dp_confirm_"))
async def cb_dp_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
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
            async with db.execute("SELECT * FROM listings WHERE id=?", (lot_id,)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await callback.answer("Объявление не найдено.", show_alert=True)
            return
        caption = await rebuild_caption(ad, status="АКТУАЛЬНО")
        new_expires = (datetime.now() + timedelta(days=7)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE listings SET status='ACTIVE', is_trial=0, expires_at=?, "
                             "full_caption=? WHERE id=?", (new_expires, caption, lot_id))
            await db.commit()
        await audit(f"💰 DonatePay продление #{lot_id} для <code>{user_id}</code>")
        try:
            await bot.send_message(user_id, "✅ Оплата подтверждена! Публикация продлена на 7 дней.")
        except Exception:
            pass
    else:
        user_state = fsm_for_user(user_id)
        await user_state.update_data(is_trial=False, paid=True, moderation_sent=False,
                                     donatepay_pending=False)
        await audit(f"💰 DonatePay новое объявление для <code>{user_id}</code>")
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
        async with db.execute("SELECT * FROM listings WHERE id=?", (lot_id,)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    until = (datetime.now() + timedelta(days=7)).isoformat()
    caption = await rebuild_caption(ad, promoted=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE listings SET is_promoted=1, promoted_until=?, full_caption=? WHERE id=?",
                         (until, caption, lot_id))
        await db.commit()
    await pin_first_posts(ad)
    await audit(f"🚀 ТОП выдан вручную #{lot_id} для <code>{user_id}</code>")
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
    try:
        await bot.send_message(int(parts[2]),
                               "❌ Оплата через DonatePay не подтверждена. Если вы уверены, что "
                               f"отправили донат — напишите в поддержку: {SUPPORT_CONTACT}")
    except Exception:
        pass
    await safe_append_and_edit(callback.message, "\n\n❌ Отклонено")
    await callback.answer("Отклонено")


# ---------- ПРОВЕРКА ПЛАТЕЖА ДО СПИСАНИЯ ----------
@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    pl = query.invoice_payload or ""
    uid = query.from_user.id
    valid = (query.currency == "XTR" and query.total_amount == STARS_PRICE and (
        pl == f"pub_{uid}" or pl == f"verify_{uid}"
        or re.fullmatch(rf"renew_\d+_{uid}", pl)
        or re.fullmatch(rf"promote_\d+_{uid}", pl)))
    if not valid:
        await audit(f"🚫 pre_checkout отклонён: user <code>{uid}</code>, "
                    f"payload <code>{escape_html(pl)}</code>, amount {query.total_amount}")
    await query.answer(ok=bool(valid),
                       error_message="Платёж отклонён. Начните заново: /menu")
# ============================================================
#  ЧАСТЬ 3/4. Раздел → категория → подкатегория → название →
#  размер/память → описание → цена → город → доставка → фото.
# ============================================================

@dp.callback_query(F.data.startswith("adtype_"))
async def cb_adtype(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(ad_type=callback.data.split("_")[1], section=None,
                            category=None, subcategory=None, model=None, memory=None)
    await state.set_state(AdForm.choosing_section)
    await safe_edit_or_caption(callback.message, SECTION_PROMPT, section_kb())
    await callback.answer()


@dp.callback_query(F.data == "back_to_adtype")
async def back_to_adtype(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(section=None, category=None, subcategory=None)
    await state.set_state(AdForm.choosing_section)
    await safe_edit_or_caption(callback.message, FORM_INTRO, ad_type_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("sec_"), AdForm.choosing_section)
async def cb_section(callback: types.CallbackQuery, state: FSMContext):
    section = SECTION_CODES.get(callback.data.split("_", 1)[1])
    if not section:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await state.update_data(section=section, category=None, subcategory=None,
                            model=None, memory=None)
    await state.set_state(AdForm.choosing_category)
    await safe_edit_or_caption(callback.message, category_prompt(section), category_kb(section))
    await callback.answer()


@dp.callback_query(F.data == "back_to_section")
async def back_to_section(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(section=None, category=None, subcategory=None, model=None, memory=None)
    await state.set_state(AdForm.choosing_section)
    await safe_edit_or_caption(callback.message, SECTION_PROMPT, section_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("cat_"), AdForm.choosing_category)
async def cb_category(callback: types.CallbackQuery, state: FSMContext):
    info = CODE_TO_CATEGORY.get(callback.data.split("_", 1)[1])
    if not info:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    section, category_name = info
    await state.update_data(section=section, category=category_name, subcategory=None,
                            model=None, memory=None)
    subcats = SUBCATEGORIES.get(section, {}).get(category_name)
    if subcats:
        await state.set_state(AdForm.choosing_subcategory)
        await safe_edit_or_caption(callback.message, f"{category_name}.\n\nВыберите подкатегорию:",
                                   subcategory_kb(subcats))
    else:
        await state.set_state(AdForm.entering_model)
        text, kb = name_step_content(await state.get_data())
        await safe_edit_or_caption(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("subcat_"), AdForm.choosing_subcategory)
async def cb_subcategory(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(subcategory=callback.data.replace("subcat_", "", 1),
                            model=None, memory=None)
    await state.set_state(AdForm.entering_model)
    text, kb = name_step_content(await state.get_data())
    await safe_edit_or_caption(callback.message, text, kb)
    await callback.answer()


async def _need_form(callback, data) -> bool:
    if not data.get("category"):
        await callback.answer("Сессия устарела, начните заново: /menu", show_alert=True)
        return False
    return True


@dp.callback_query(F.data == "back_to_category")
async def back_to_category(callback: types.CallbackQuery, state: FSMContext):
    section = get_section(await state.get_data())
    await state.update_data(category=None, subcategory=None, model=None, memory=None)
    await state.set_state(AdForm.choosing_category)
    await safe_edit_or_caption(callback.message, category_prompt(section), category_kb(section))
    await callback.answer()


@dp.callback_query(F.data == "back_to_subcategory")
async def back_to_subcategory(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("category")
    subcats = SUBCATEGORIES.get(get_section(data), {}).get(category)
    if subcats:
        await state.update_data(subcategory=None, model=None, memory=None)
        await state.set_state(AdForm.choosing_subcategory)
        await safe_edit_or_caption(callback.message, f"{category}.\n\nВыберите подкатегорию:",
                                   subcategory_kb(subcats))
        await callback.answer()
    else:
        await back_to_category(callback, state)


@dp.callback_query(F.data == "back_to_model")
async def back_to_model(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(model=None, memory=None)
    await state.set_state(AdForm.entering_model)
    text, kb = name_step_content(data)
    await safe_edit_or_caption(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "back_to_size")
async def back_to_size(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    section = get_section(data)
    await state.update_data(description=None, memory=None)
    if section == "Электроника":
        if data.get("memory") == "—":
            await back_to_model(callback, state)
            return
        await state.set_state(AdForm.entering_memory)
        text, kb = memory_step_content(data)
    else:
        await state.set_state(AdForm.entering_size)
        text, kb = size_step_content(data)
    await safe_edit_or_caption(callback.message, text, kb)
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
        f"{form_label(data)}, 📃Описание\n\n{desc_prompt(get_section(data), data.get('memory') != '—')}",
        step_kb("back_to_size"))
    await callback.answer()


@dp.callback_query(F.data == "back_to_price")
async def back_to_price(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(city=None)
    await state.set_state(AdForm.entering_price)
    await safe_edit_or_caption(
        callback.message, f"{form_label(data)}, 🪎Цена\n\nУкажите цену, которую считаете нужной.\n"
        "Валюту, точки и пробелы указывать не нужно", step_kb("back_to_description"))
    await callback.answer()


@dp.callback_query(F.data == "back_to_city")
async def back_to_city(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(delivery=None)
    await state.set_state(AdForm.entering_city)
    await safe_edit_or_caption(
        callback.message, f"{form_label(data)}, 🌇Город\n\nУкажите город самовывоз — только название.\n"
        "Пример: Москва, Санкт-Петербург, Нижний Новгород", step_kb("back_to_price"))
    await callback.answer()


@dp.callback_query(F.data == "back_to_delivery")
async def back_to_delivery(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.update_data(photos=[])
    await state.set_state(AdForm.choosing_delivery)
    await safe_edit_or_caption(
        callback.message, f"{form_label(data)}, 🗃️Доставка\n\n{delivery_prompt(get_section(data))}",
        delivery_kb())
    await callback.answer()


@dp.callback_query(F.data == "back_to_photos")
async def back_to_photos(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not await _need_form(callback, data):
        return
    await state.set_state(AdForm.uploading_photos)
    await safe_edit_or_caption(callback.message, f"{form_label(data)}, {PHOTOS_PROMPT}", photos_kb())
    await callback.answer()


@dp.message(AdForm.entering_model, TextAnswer())
async def process_model(message: types.Message, state: FSMContext):
    model = await get_clean_text(message, LIMIT_MODEL)
    if model is None:
        return
    await state.update_data(model=model)
    data = await state.get_data()
    section = get_section(data)
    if section == "Электроника":
        if data.get("category") in NO_SIZE_CATEGORIES:
            await state.update_data(memory="—")
            await state.set_state(AdForm.entering_description)
            await message.answer(f"{form_label(data)}, 📃Описание\n\n{desc_prompt(section, False)}",
                                 reply_markup=step_kb("back_to_model"))
        else:
            await state.set_state(AdForm.entering_memory)
            text, kb = memory_step_content(data)
            await message.answer(text, reply_markup=kb)
    else:
        await state.set_state(AdForm.entering_size)
        text, kb = size_step_content(data)
        await message.answer(text, reply_markup=kb)


@dp.message(AdForm.entering_memory, TextAnswer())
async def process_memory(message: types.Message, state: FSMContext):
    memory = await get_clean_text(message, LIMIT_SIZE)
    if memory is None:
        return
    await state.update_data(memory=memory)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await message.answer(f"{form_label(data)}, 📃Описание\n\n{desc_prompt('Электроника', True)}",
                         reply_markup=step_kb("back_to_size"))


@dp.callback_query(F.data.startswith("size_"), AdForm.entering_size)
async def cb_size(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.split("_", 1)[1]
    data = await state.get_data()
    if value == "other":
        await safe_edit_or_caption(
            callback.message,
            f"{form_label(data)}, 📏Размер\n\nНапишите размер вручную: например 46, 48, 50 или XXL.",
            step_kb("back_to_size"))
        await callback.answer()
        return
    await state.update_data(memory=value)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await safe_edit_or_caption(
        callback.message, f"{form_label(data)}, 📃Описание\n\n{desc_prompt(get_section(data), True)}",
        step_kb("back_to_size"))
    await callback.answer(f"Размер: {value}")


@dp.message(AdForm.entering_size, TextAnswer())
async def process_size(message: types.Message, state: FSMContext):
    size = await get_clean_text(message, LIMIT_SIZE)
    if size is None:
        return
    await state.update_data(memory=size)
    data = await state.get_data()
    await state.set_state(AdForm.entering_description)
    await message.answer(f"{form_label(data)}, 📃Описание\n\n{desc_prompt(get_section(data), True)}",
                         reply_markup=step_kb("back_to_size"))


@dp.message(AdForm.entering_description, TextAnswer())
async def process_description(message: types.Message, state: FSMContext):
    description = await get_clean_text(message, LIMIT_DESCRIPTION)
    if description is None:
        return
    await state.update_data(description=description)
    data = await state.get_data()
    await state.set_state(AdForm.entering_price)
    await message.answer(f"{form_label(data)}, 🪎Цена\n\nУкажите цену, которую считаете нужной.\n"
                         "Валюту, точки и пробелы указывать не нужно",
                         reply_markup=step_kb("back_to_description"))


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
    await message.answer(f"{form_label(data)}, 🌇Город\n\nУкажите город самовывоз — только название.\n"
                         "Пример: Москва, Санкт-Петербург, Нижний Новгород",
                         reply_markup=step_kb("back_to_price"))


@dp.message(AdForm.entering_city, TextAnswer())
async def process_city(message: types.Message, state: FSMContext):
    city = await get_clean_text(message, LIMIT_CITY)
    if city is None:
        return
    await state.update_data(city=city)
    data = await state.get_data()
    await state.set_state(AdForm.choosing_delivery)
    await message.answer(f"{form_label(data)}, 🗃️Доставка\n\n{delivery_prompt(get_section(data))}",
                         reply_markup=delivery_kb())


@dp.callback_query(F.data.startswith("del_"), AdForm.choosing_delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    delivery = "Отправлю" if callback.data == "del_yes" else "Не отправлю"
    await state.update_data(delivery=delivery, photos=[])
    await state.set_state(AdForm.uploading_photos)
    data = await state.get_data()
    await safe_edit_or_caption(callback.message, f"{form_label(data)}, {PHOTOS_PROMPT}", photos_kb())
    await callback.answer()


_photo_locks: Dict[int, asyncio.Lock] = {}


def _photo_lock(uid: int) -> asyncio.Lock:
    if uid not in _photo_locks:
        if len(_photo_locks) > 2000:
            _photo_locks.clear()
        _photo_locks[uid] = asyncio.Lock()
    return _photo_locks[uid]


@dp.message(AdForm.uploading_photos, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    async with _photo_lock(message.from_user.id):
        photos = list((await state.get_data()).get("photos") or [])
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
    section = get_section(data)
    tags = " ".join(t for t in (hashtag(section), hashtag(data.get("category")),
                                hashtag(data.get("subcategory"))) if t)
    text = ("🔍Предпросмотр\n\nПроверь текст и вложения на наличие ошибок. "
            "Если всё нормально — отправь на модерацию.\n\n"
            f"✅АКТУАЛЬНО✅\n{tags}\n{hashtag(data.get('city'))}\n\n📦 {data['model']}\n\n")
    if data.get("memory") and data["memory"] != "—":
        text += f"{size_label(section)}: {data['memory']}\n\n"
    text += (f"{data['description']}\n\n🇷🇺{data['city']}\n💸{data['price']}\n"
             f"🚛{data['delivery']}\n\n🥷🏻{callback.from_user.first_name}")
    if tg_len(text) > MAX_CAPTION:
        await callback.answer("Описание слишком длинное, сократите.", show_alert=True)
        return
    await state.set_state(AdForm.preview)
    kb = preview_kb()
    if len(photos) == 1:
        await callback.message.answer_photo(photo=photos[0], caption=text, reply_markup=kb)
    else:
        media = [InputMediaPhoto(media=ph, caption=text if i == 0 else None)
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM listings WHERE user_id=? AND status='PENDING_MODERATION'",
                              (callback.from_user.id,)) as cur:
            if (await cur.fetchone())[0] >= MAX_PENDING:
                await callback.answer(f"У вас уже {MAX_PENDING} объявления на модерации. "
                                      "Дождитесь решения.", show_alert=True)
                return
    section = get_section(data)
    is_trial = bool(data.get("is_trial", True))
    try:
        lot_id = await save_listing(callback.from_user.id, data,
                                    status="PENDING_MODERATION", is_trial=is_trial)
    except Exception as e:
        log("save_listing error:", e)
        await callback.answer("Ошибка сохранения, попробуй ещё раз.", show_alert=True)
        return
    await state.update_data(moderation_sent=True)
    await safe_edit_or_caption(
        callback.message,
        f"Объявление отправлено на модерацию!\n\nРаздел: {section}\n"
        f"Публикация: {', '.join(SECTION_CHANNELS.get(section, []))}\n\n"
        "Как только модератор примет пост — придёт уведомление вам в чат с ботом.")
    admin_text = (
        f"🔔 Новое объявление на модерации #{lot_id}\n\n"
        f"Пользователь: {callback.from_user.first_name} (@{callback.from_user.username or 'нет'})\n"
        f"ID: {callback.from_user.id}\n"
        f"Тариф: {'бесплатно 24ч' if is_trial else 'оплачено 7 дней'}\n"
        f"Раздел: {section} → {data.get('category')}"
        + (f" → {data['subcategory']}" if data.get("subcategory") else "") + "\n"
        f"Тип: {AD_TYPE_NAMES.get(data.get('ad_type'), data.get('ad_type'))}\n"
        f"Название: {data.get('model')}\n")
    if data.get("memory") and data["memory"] != "—":
        admin_text += f"{size_label(section)}: {data['memory']}\n"
    admin_text += (f"Цена: {data.get('price')} | Город: {data.get('city')}\n"
                   f"Доставка: {data.get('delivery')}\nОписание: {data.get('description')}")
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрено", callback_data=f"approve_{lot_id}")],
        [InlineKeyboardButton(text="❌ Не одобрено", callback_data=f"reject_{lot_id}")]])
    photos = data.get("photos") or []
    ok = False
    try:
        if len(photos) == 1:
            await bot.send_photo(ADMIN_ID, photo=photos[0], caption=admin_text[:1024], reply_markup=admin_kb)
            ok = True
        elif photos:
            media = [InputMediaPhoto(media=ph, caption=admin_text[:1024] if i == 0 else None)
                     for i, ph in enumerate(photos)]
            await bot.send_media_group(ADMIN_ID, media=media)
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb)
            ok = True
    except Exception as e:
        log("send to admin failed:", e)
    if not ok:
        try:
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb)
        except Exception as e:
            log("send text to admin failed:", e)
    await state.clear()
    await callback.answer()


async def notify_subscribers(row, first_channel, first_message_id):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT DISTINCT s.user_id, s.keyword FROM subscriptions s "
                                  "LEFT JOIN users u ON u.user_id = s.user_id "
                                  "WHERE IFNULL(u.blocked,0) = 0") as cur:
                subs = await cur.fetchall()
        haystack = " ".join(str(row_get(row, k, "")) for k in
                            ("model", "description", "section", "category", "subcategory")).lower()
        notified = {row_get(row, "user_id")}
        for s in subs:
            kw = (row_get(s, "keyword", "") or "").lower()
            if kw and kw in haystack and s["user_id"] not in notified:
                notified.add(s["user_id"])
                try:
                    msg = (f"🔔 Новое объявление по подписке «{escape_html(s['keyword'])}»:\n\n"
                           f"🗂 {escape_html(get_section(row))}\n"
                           f"📦 {escape_html(row_get(row,'model','—'))}\n"
                           f"💸 {escape_html(row_get(row,'price','—'))} руб.\n"
                           f"🇷🇺 {escape_html(row_get(row,'city','—'))}\n")
                    if first_channel and first_message_id:
                        link = f"https://t.me/{str(first_channel).lstrip('@')}/{first_message_id}"
                        msg += f"\n🔗 <a href='{link}'>Открыть в канале</a>"
                    await bot.send_message(s["user_id"], msg, parse_mode="HTML")
                except Exception as e:
                    log(f"notify {s['user_id']} failed:", e)
    except Exception as e:
        log("notify_subscribers:", e)


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
        async with db.execute("SELECT * FROM listings WHERE id=?", (lot_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await callback.answer("Объявление не найдено", show_alert=True)
        return
    if row_get(row, "status") == "ACTIVE":
        await callback.answer("Уже опубликовано.", show_alert=True)
        return
    section = get_section(row)
    channels = SECTION_CHANNELS.get(section) or SECTION_CHANNELS[DEFAULT_SECTION]
    post_text = build_post_text(row, author_tag=await get_author_tag(row["user_id"]),
                                author_id=row["user_id"], verified=await is_verified(row["user_id"]))
    photos = [p for p in (row_get(row, "photos", "") or "").split(",") if p]
    saved, first_ch, first_mid, errors = [], "", 0, []
    for channel in channels:
        try:
            if len(photos) == 1:
                sent = await bot.send_photo(channel, photo=photos[0], caption=post_text, parse_mode="HTML")
                saved.append(f"{channel}:{sent.message_id}")
                if not first_ch:
                    first_ch, first_mid = channel, sent.message_id
            elif photos:
                media = [InputMediaPhoto(media=ph, caption=post_text if i == 0 else None,
                                         parse_mode="HTML" if i == 0 else None)
                         for i, ph in enumerate(photos)]
                msgs = await bot.send_media_group(channel, media=media)
                for m in msgs:
                    saved.append(f"{channel}:{m.message_id}")
                if not first_ch:
                    first_ch, first_mid = channel, msgs[0].message_id
            else:
                sent = await bot.send_message(channel, post_text, parse_mode="HTML")
                saved.append(f"{channel}:{sent.message_id}")
                if not first_ch:
                    first_ch, first_mid = channel, sent.message_id
        except Exception as e:
            errors.append(f"{channel}: {e}")
            log(f"publish {channel} failed:", e)
    if not saved:
        await callback.answer("Не удалось опубликовать ни в один канал.", show_alert=True)
        try:
            await bot.send_message(ADMIN_ID, f"❌ Публикация #{lot_id} ({section}) провалилась:\n"
                                             + scrub("\n".join(errors))[:1500])
        except Exception:
            pass
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE listings SET status='ACTIVE', channel=?, message_id=?, "
                         "message_ids=?, full_caption=? WHERE id=?",
                         (first_ch, first_mid, ",".join(saved), post_text, lot_id))
        await db.commit()
    await audit(f"✅ Опубликовано #{lot_id} ({section}) для <code>{row['user_id']}</code>")
    try:
        note = ("\n\n⚠️ Не во все каналы: " + scrub("; ".join(errors))) if errors else ""
        await bot.send_message(row["user_id"], f"Ваша публикация одобрена!👍\nРаздел: {section}{note}",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                                   [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]]))
    except Exception as e:
        log("notify author failed:", e)
    await notify_subscribers(row, first_ch, first_mid)
    await safe_edit_or_caption(callback.message,
                               f"Объявление одобрено и опубликовано.\nРаздел: {section}\n"
                               + ", ".join(dict.fromkeys(p.rsplit(":", 1)[0] for p in saved)))
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
        async with db.execute("SELECT user_id, charge_id FROM listings WHERE id=?", (lot_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Объявление не найдено", show_alert=True)
            return
        await db.execute("UPDATE listings SET status='REJECTED' WHERE id=?", (lot_id,))
        await db.commit()
    charge_id = row_get(row, "charge_id")
    if charge_id:
        try:
            await bot.refund_star_payment(user_id=row["user_id"], telegram_payment_charge_id=charge_id)
            await audit(f"↩️ Возврат Stars #{lot_id} пользователю <code>{row['user_id']}</code>")
        except Exception as e:
            log("refund failed:", e)
    try:
        await bot.send_message(row["user_id"],
                               "Ваша публикация не одобрена👎, попробуйте заполнить анкету заново",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🥷🏻 Мой профиль", callback_data="profile")],
                                   [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]]))
    except Exception:
        pass
    await safe_edit_or_caption(callback.message, "Объявление отклонено.")
    await callback.answer()


@dp.callback_query(F.data.startswith("lot_"))
async def cb_lot_details(callback: types.CallbackQuery):
    try:
        lot_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE id=?", (lot_id,)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено", show_alert=True)
        return
    if ad["user_id"] != callback.from_user.id and callback.from_user.id != ADMIN_ID:
        await callback.answer("Это не ваше объявление.", show_alert=True)
        return
    ch, mid = row_get(ad, "channel"), row_get(ad, "message_id")
    status = row_get(ad, "status", "—")
    text = (f"{row_get(ad,'model','—')}\n{row_get(ad,'price','—')} руб.\n"
            f"{row_get(ad,'city','—')}\nРаздел: {get_section(ad)}\nСтатус: {status}\n"
            f"В канале с {(row_get(ad,'created_at','') or '')[:10] or '—'}")
    kb = []
    if ch and mid:
        kb.append([InlineKeyboardButton(text="🔗 Открыть в канале",
                                        url=f"https://t.me/{str(ch).lstrip('@')}/{mid}")])
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
        async with db.execute("SELECT * FROM listings WHERE id=? AND user_id=? AND status='ACTIVE'",
                              (lot_id, callback.from_user.id)) as cur:
            ad = await cur.fetchone()
        if not ad:
            await callback.answer("Объявление не найдено или уже снято.", show_alert=True)
            return
        await db.execute("UPDATE listings SET status='SOLD', is_promoted=0, promoted_until=NULL WHERE id=?",
                         (lot_id,))
        await db.commit()
    if bool(row_get(ad, "is_promoted", 0)):
        await unpin_first_posts(ad)
    caption = await rebuild_caption(ad, status="ПРОДАНО", promoted=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE listings SET full_caption=? WHERE id=?", (caption, lot_id))
        await db.commit()
    await safe_edit_or_caption(callback.message, "Объявление снято с продажи.", main_menu_kb())
    await callback.answer()


# ---------- ПРАВО НА УДАЛЕНИЕ ДАННЫХ ----------
@dp.callback_query(F.data == "forget_ask")
async def forget_ask(callback: types.CallbackQuery):
    await safe_edit_or_caption(
        callback.message,
        "🗑 <b>Удаление ваших данных</b>\n\n"
        "Будет удалено безвозвратно:\n"
        "• все ваши объявления из базы и <b>посты из каналов</b>\n"
        "• подписки на关键词\n"
        "• запись профиля (включая галочку верификации)\n\n"
        "История платежей у Telegram остаётся у Telegram — бот её не хранит.\n\n"
        "Продолжить?",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Да, удалить всё", callback_data="forget_yes")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data="profile")]]))
    await callback.answer()


@dp.callback_query(F.data == "forget_yes")
async def forget_yes(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE user_id=?", (uid,)) as cur:
            ads = await cur.fetchall()
    removed = 0
    for ad in ads:
        for ch, mid in iter_all_posts(ad):
            try:
                await bot.unpin_chat_message(chat_id=ch, message_id=mid)
            except Exception:
                pass
            try:
                await bot.delete_message(chat_id=ch, message_id=mid)
                removed += 1
            except Exception:
                pass
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM listings WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM users WHERE user_id=?", (uid,))
        await db.commit()
    await state.clear()
    await audit(f"🗑 Пользователь <code>{uid}</code> удалил свои данные. "
                f"Постов удалено: {removed}")
    await safe_edit_or_caption(
        callback.message,
        f"✅ Данные удалены.\nОбъявлений в базе: удалены все\nПостов в каналах удалено: {removed}\n\n"
        "Если снова захотите пользоваться ботом — нажмите /start.", main_menu_kb())
    await callback.answer("Удалено")
# ============================================================
#  ЧАСТЬ 4/4. FALLBACK — последними и только для лички.
# ============================================================

@dp.callback_query(F.data == "subs_menu")
async def cb_subs_menu(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message,
                               "🔔 <b>Подписки на новые объявления</b>\n\n"
                               "Работают по всем разделам: Электроника, Одежда, Искусство.",
                               subscriptions_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "sub_add")
async def cb_sub_add(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdForm.waiting_for_subscription_keyword)
    await safe_edit_or_caption(
        callback.message, "Введите ключевое слово для подписки (например, «iPhone 13» или «Пуховик»):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="subs_menu")]]))
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
        async with db.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id=?",
                              (message.from_user.id,)) as cur:
            if (await cur.fetchone())[0] >= MAX_SUBSCRIPTIONS:
                await message.answer(f"Максимум {MAX_SUBSCRIPTIONS} подписок. Удалите лишние: /subs")
                await state.clear()
                return
        async with db.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND LOWER(keyword)=LOWER(?)",
                              (message.from_user.id, keyword)) as cur:
            if await cur.fetchone():
                await message.answer("Такая подписка уже есть.")
                await state.clear()
                return
        await db.execute("INSERT INTO subscriptions (user_id, keyword, created_at) VALUES (?,?,?)",
                         (message.from_user.id, keyword, datetime.now().isoformat()))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Подписка на «{keyword}» добавлена.")
    await show_main_menu(message)


@dp.callback_query(F.data == "sub_list")
async def cb_sub_list(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM subscriptions WHERE user_id=? ORDER BY id DESC",
                              (callback.from_user.id,)) as cur:
            subs = await cur.fetchall()
    if not subs:
        await safe_edit_or_caption(callback.message, "У вас нет подписок.",
                                   InlineKeyboardMarkup(inline_keyboard=[
                                       [InlineKeyboardButton(text="➕ Добавить", callback_data="sub_add")],
                                       [InlineKeyboardButton(text="↩️ Назад", callback_data="subs_menu")]]))
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
        await db.execute("DELETE FROM subscriptions WHERE id=? AND user_id=?",
                         (sub_id, callback.from_user.id))
        await db.commit()
    await callback.answer("Удалено")
    await cb_sub_list(callback)


# ---------- ПОМОЩЬ ----------
@dp.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    await safe_edit_or_caption(
        callback.message,
        "❓ <b>Помощь</b>\n\nJustTrade публикует объявления в трёх разделах:\n"
        "📱 Электроника → @JustTechnic\n👕 Одежда → @JstBranded\n🎨 Искусство → @JustArtsell",
        help_menu_kb())
    await callback.answer()


def _help_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="help")]])


@dp.callback_query(F.data == "help_rules")
async def cb_help_rules(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message,
        "📄 <b>Правила публикации</b>\n\n"
        "<b>Нельзя:</b> запрещённые товары, пиратские копии, животных, услуги интимного характера, "
        "финансовые пирамиды, фото из интернета, дубли одного товара, оскорбления и спам.\n\n"
        "<b>Обязательно:</b> реальные фото (1–10), название и описание, размер или память, "
        "цена, город, доставка, соответствие разделу.\n\n"
        "Нарушение правил — отказ модератора.", _help_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "help_howto")
async def cb_help_howto(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message,
        "❔ <b>Как выставить объявление</b>\n\n"
        "1. «✅ Опубликовать» → бесплатно (24 ч) или платно (7 дней).\n"
        "2. Продажа / Покупка / Обмен.\n"
        "3. Раздел: 📱 Электроника, 👕 Одежда, 🎨 Искусство.\n"
        "4. Категория → подкатегория (если есть).\n"
        "5. Название товара / модель.\n"
        "6. Размер: одежда — кнопками, искусство — текстом, техника — память ОЗУ/ПЗУ.\n"
        "7. Описание, цена, город, доставка.\n"
        "8. 1–10 реальных фото.\n"
        "9. Предпросмотр → «💫 Отправка на модерацию».\n\n"
        "Отмена в любой момент: /cancel.", _help_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "help_reject")
async def cb_help_reject(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message,
        "⚠️ <b>Отказ в публикации</b>\n\nЧастые причины: фото не соответствуют товару, "
        "неполное описание, запрещённый товар, неверный раздел, нет цены/города/размера.\n\n"
        "Создайте объявление заново, исправив замечания.\n\n"
        f"Не согласны — напишите: {SUPPORT_CONTACT}.", _help_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "help_privacy")
async def cb_help_privacy(callback: types.CallbackQuery):
    await safe_edit_or_caption(callback.message,
        "🔐 <b>Ваши данные</b>\n\n"
        "Бот хранит: ваш Telegram ID, имя/юзернейм, тексты объявлений, размеры, цену, город "
        "и идентификаторы платежей Stars.\n\n"
        "Бот <b>не хранит</b> данные карт и не имеет доступа к вашему аккаунту.\n\n"
        "В каналах публикуются только текст объявления, фото и ссылка на ваш профиль.\n\n"
        "Вы можете удалить всё: Профиль → «🗑 Удалить мои данные». "
        "Посты в каналах будут удалены тоже.", _help_back_kb())
    await callback.answer()


# ---------- АДМИН: СТАТИСТИКА ----------
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
        blocked = await cnt("SELECT COUNT(*) FROM users WHERE blocked=1")
        subs = await cnt("SELECT COUNT(*) FROM subscriptions")
        pays = await cnt("SELECT COUNT(*) FROM payments")
        el = await cnt("SELECT COUNT(*) FROM listings WHERE status='ACTIVE' AND (section='Электроника' OR section IS NULL)")
        cl = await cnt("SELECT COUNT(*) FROM listings WHERE status='ACTIVE' AND section='Одежда'")
        ar = await cnt("SELECT COUNT(*) FROM listings WHERE status='ACTIVE' AND section='Искусство'")
    await message.answer(
        f"📊 <b>Статистика JustTrade</b>\n\n"
        f"👥 Пользователей: {users} (заблокировали бота: {blocked})\n"
        f"📦 Всего объявлений: {total}\n🟢 Активных: {active}\n🕓 На модерации: {pending}\n"
        f"🔴 Проданных/снятых: {sold}\n🔥 В топе: {promoted}\n🔔 Подписок: {subs}\n"
        f"💳 Проверенных платежей: {pays}\n\n"
        f"<b>Активные по разделам:</b>\n📱 {el} | 👕 {cl} | 🎨 {ar}", parse_mode="HTML")


@dp.message(Command("check_ads"))
async def cmd_check_ads(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, model, section, status, channel FROM listings "
                              "ORDER BY id DESC LIMIT 30") as cur:
            rows = await cur.fetchall()
    if not rows:
        await message.answer("Объявлений нет.")
        return
    lines = [f"#{r['id']} | {(row_get(r,'model','') or '')[:25]} | {get_section(r)} | "
             f"{row_get(r,'status','')} | {row_get(r,'channel','')}" for r in rows]
    await message.answer("<pre>" + escape_html("\n".join(lines)) + "</pre>", parse_mode="HTML")


@dp.message(Command("dbinfo"))
async def cmd_dbinfo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA table_info(listings)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        async with db.execute("PRAGMA integrity_check") as cur:
            integrity = (await cur.fetchone())[0]
    await message.answer(f"Столбцы listings:\n<code>{escape_html(', '.join(cols))}</code>\n\n"
                         f"Целостность базы: <b>{escape_html(integrity)}</b>", parse_mode="HTML")


@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split(maxsplit=1)
    search = parts[1].strip() if len(parts) > 1 else ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = ("SELECT user_id, first_seen, verified, blocked FROM users "
               + ("WHERE CAST(user_id AS TEXT) LIKE ? " if search else "")
               + "ORDER BY first_seen DESC LIMIT 30")
        async with db.execute(sql, (f"%{search}%",) if search else ()) as cur:
            rows = await cur.fetchall()
    if not rows:
        await message.answer("Пользователей не найдено.")
        return
    lines = [f"{r['user_id']} {'✅' if row_get(r,'verified',0) else '  '}"
             f"{'🚫' if row_get(r,'blocked',0) else '  '} | "
             f"{(row_get(r,'first_seen','') or '—')[:10]}" for r in rows]
    await message.answer("👥 Пользователи:\n<pre>" + escape_html("\n".join(lines)) + "</pre>",
                         parse_mode="HTML")


@dp.message(Command("send"))
async def cmd_send(message: types.Message):
    if not await require_sudo(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: <code>/send user_id текст</code>", parse_mode="HTML")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ user_id должен быть числом.")
        return
    try:
        try:
            await bot.send_message(target_id, parts[2].strip(), parse_mode="HTML")
        except Exception as e:
            if "can't parse entities" in str(e).lower():
                await bot.send_message(target_id, parts[2].strip())
            else:
                raise
        await audit(f"✉️ Владелец отправил сообщение <code>{target_id}</code>")
        await message.answer(f"✅ Отправлено <code>{target_id}</code>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(scrub(e))}</code>", parse_mode="HTML")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not await require_sudo(message):
        return
    from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: <code>/broadcast Текст</code>\n\n"
                             "Отправляется только тем, кто не заблокировал бота.", parse_mode="HTML")
        return
    text = parts[1].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE IFNULL(blocked,0)=0") as cur:
            users = [r[0] for r in await cur.fetchall()]
    if not users:
        await message.answer("❌ Нет получателей.")
        return
    await audit(f"📢 Начата рассылка на {len(users)} получателей")
    status = await message.answer(f"📢 Рассылка на {len(users)} пользователей...")
    sent = failed = blocked = floods = 0
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
            floods += 1
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            failed += 1
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
                await db.commit()
        except Exception as e:
            failed += 1
            log(f"broadcast {uid}:", e)
        if i % 50 == 0:
            try:
                await status.edit_text(f"📢 {i}/{len(users)}\n✅ {sent} | ❌ {failed} | 🚫 {blocked}")
            except Exception:
                pass
    await audit(f"📢 Рассылка завершена: ✅{sent} ❌{failed} 🚫{blocked}")
    try:
        await status.edit_text(f"📢 <b>Рассылка завершена</b>\n\n✅ {sent}\n❌ {failed}\n"
                               f"🚫 {blocked}\n⏳ {floods}", parse_mode="HTML")
    except Exception:
        await message.answer(f"✅ {sent}\n❌ {failed}")


# ---------- БЭКАПЫ ----------
async def send_backup(target_chat: int, caption: str):
    """Снимок → шифрование (если включено) → отправка → удаление временных файлов."""
    snap = await make_snapshot()
    enc = None
    try:
        if BACKUP_PASSWORD:
            if not CRYPTO_OK:
                await bot.send_message(target_chat,
                                       "❌ Бэкап НЕ отправлен: задан BACKUP_PASSWORD, но нет "
                                       "библиотеки cryptography. Добавьте её в requirements.txt.")
                return
            enc = snap + "enc"
            encrypt_file(snap, enc, BACKUP_PASSWORD)
            await bot.send_document(target_chat, FSInputFile(enc),
                                    caption=caption + "\n🔐 Зашифровано (AES-GCM). "
                                                      "Файл .dbenc — расшифруется командой /restore.")
        else:
            await bot.send_document(target_chat, FSInputFile(snap),
                                    caption=caption + "\n⚠️ Без шифрования. "
                                                      "Задайте BACKUP_PASSWORD для защиты.")
    finally:
        for p in (snap, enc):
            if p:
                try:
                    os.remove(p)
                except Exception:
                    pass


@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    if not await require_sudo(message):
        return
    if not os.path.exists(DB_PATH):
        await message.answer("Файл БД не найден.")
        return
    try:
        await send_backup(ADMIN_ID, f"Бэкап базы за {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        await audit("💾 Владелец выгрузил бэкап базы")
        await message.answer("✅ Бэкап отправлен.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(scrub(e))}</code>", parse_mode="HTML")


@dp.message(Command("fix_tags"))
async def cmd_fix_tags(message: types.Message):
    if not await require_sudo(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM listings WHERE status='ACTIVE'") as cur:
            ads = await cur.fetchall()
    if not ads:
        await message.answer("Активных объявлений нет.")
        return
    status = await message.answer(f"♻️ Обновляю хештеги: 0/{len(ads)}")
    ok = fail = 0
    for i, ad in enumerate(ads, 1):
        try:
            caption = await rebuild_caption(ad)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE listings SET full_caption=? WHERE id=?", (caption, ad["id"]))
                await db.commit()
            ok += 1
        except Exception as e:
            fail += 1
            log(f"fix_tags #{ad['id']}:", e)
        await asyncio.sleep(0.3)
        if i % 10 == 0:
            try:
                await status.edit_text(f"♻️ Обновляю хештеги: {i}/{len(ads)}")
            except Exception:
                pass
    await audit(f"♻️ fix_tags: обновлено {ok}, ошибок {fail}")
    await message.answer(f"✅ Готово.\nОбновлено постов: {ok}\nОшибок: {fail}")


# ---------- ВОССТАНОВЛЕНИЕ: НЕСКОЛЬКО ФАЙЛОВ ----------
_restore_locks: Dict[int, asyncio.Lock] = {}


def _restore_lock(uid: int) -> asyncio.Lock:
    if uid not in _restore_locks:
        if len(_restore_locks) > 500:
            _restore_locks.clear()
        _restore_locks[uid] = asyncio.Lock()
    return _restore_locks[uid]


def restore_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="restore_done")],
        [InlineKeyboardButton(text="🗑 Отмена", callback_data="restore_cancel")]])


def restore_action_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧩 Объединить все", callback_data="restore_merge")],
        [InlineKeyboardButton(text="♻️ Заменить базой", callback_data="restore_replace")],
        [InlineKeyboardButton(text="🗑 Отмена", callback_data="restore_cancel")]])


async def _cols(db, table, schema="main"):
    async with db.execute(f"PRAGMA {schema}.table_info({table})") as cur:
        return [r[1] for r in await cur.fetchall()]


async def _has_table(db, table, schema="main"):
    async with db.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
                          (table,)) as cur:
        return await cur.fetchone() is not None


SAFE_COL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def inspect_backup(path: str) -> int:
    with open(path, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise ValueError("это не файл SQLite")
    async with aiosqlite.connect(path) as db:
        async with db.execute("PRAGMA integrity_check") as cur:
            if (await cur.fetchone())[0] != "ok":
                raise ValueError("база повреждена (integrity_check)")
        if not await _has_table(db, "listings"):
            raise ValueError("нет таблицы listings")
        async with db.execute("SELECT COUNT(*) FROM listings") as cur:
            return (await cur.fetchone())[0]


async def merge_backup(path: str) -> dict:
    added = {"listings": 0, "users": 0, "subscriptions": 0}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("ATTACH DATABASE ? AS src", (path,))
        try:
            if await _has_table(db, "listings", "src"):
                main_c = await _cols(db, "listings")
                src_c = await _cols(db, "listings", "src")
                use = [c for c in main_c if c in src_c and c != "id" and SAFE_COL.match(c)]
                if use:
                    sig = [c for c in ("user_id", "created_at", "model") if c in use] or use
                    cond = " AND ".join(f"IFNULL(m.{c},'')=IFNULL(s.{c},'')" for c in sig)
                    grp = ", ".join(f"s.{c}" for c in sig)
                    async with db.execute("SELECT COUNT(*) FROM main.listings") as cur:
                        before = (await cur.fetchone())[0]
                    await db.execute(
                        f"INSERT INTO main.listings ({', '.join(use)}) "
                        f"SELECT {', '.join('s.'+c for c in use)} FROM src.listings s "
                        f"WHERE NOT EXISTS (SELECT 1 FROM main.listings m WHERE {cond}) GROUP BY {grp}")
                    async with db.execute("SELECT COUNT(*) FROM main.listings") as cur:
                        added["listings"] = (await cur.fetchone())[0] - before
            if await _has_table(db, "users", "src"):
                uc = [c for c in await _cols(db, "users")
                      if c in await _cols(db, "users", "src") and SAFE_COL.match(c)]
                if uc:
                    async with db.execute("SELECT COUNT(*) FROM main.users") as cur:
                        before = (await cur.fetchone())[0]
                    await db.execute(f"INSERT OR IGNORE INTO main.users ({', '.join(uc)}) "
                                     f"SELECT {', '.join(uc)} FROM src.users")
                    if "verified" in uc:
                        await db.execute("UPDATE main.users SET verified=1 WHERE user_id IN "
                                         "(SELECT user_id FROM src.users WHERE verified=1)")
                    async with db.execute("SELECT COUNT(*) FROM main.users") as cur:
                        added["users"] = (await cur.fetchone())[0] - before
            if await _has_table(db, "subscriptions", "src"):
                mc = await _cols(db, "subscriptions")
                sc_src = await _cols(db, "subscriptions", "src")
                sc = [c for c in ("user_id", "keyword", "created_at") if c in mc and c in sc_src]
                if "user_id" in sc and "keyword" in sc:
                    async with db.execute("SELECT COUNT(*) FROM main.subscriptions") as cur:
                        before = (await cur.fetchone())[0]
                    await db.execute(
                        f"INSERT INTO main.subscriptions ({', '.join(sc)}) "
                        f"SELECT {', '.join('s.'+c for c in sc)} FROM src.subscriptions s "
                        f"WHERE NOT EXISTS (SELECT 1 FROM main.subscriptions m "
                        f"WHERE m.user_id=s.user_id AND LOWER(m.keyword)=LOWER(s.keyword))")
                    async with db.execute("SELECT COUNT(*) FROM main.subscriptions") as cur:
                        added["subscriptions"] = (await cur.fetchone())[0] - before
            await db.commit()
        finally:
            try:
                await db.execute("DETACH DATABASE src")
            except Exception:
                pass
    return added


@dp.message(Command("restore"))
async def restore_db(message: types.Message, state: FSMContext):
    if not await require_sudo(message):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    await state.set_state(AdForm.waiting_for_restore_db)
    await state.update_data(restore_files=[])
    await message.answer(
        f"📁 Пришлите файлы баз — можно <b>несколько подряд</b>, до {RESTORE_MAX_FILES} шт., "
        f"каждый до {RESTORE_MAX_MB} МБ.\n\n"
        "Форматы: <code>.db</code> (обычный) и <code>.dbenc</code> (зашифрованный).\n\n"
        "Когда отправите все — нажмите «✅ Готово» и выберите режим:\n"
        "🧩 объединить все без дублей\n♻️ заменить базу самым полным файлом",
        reply_markup=restore_kb(), parse_mode="HTML")


@dp.message(AdForm.waiting_for_restore_db, F.document)
async def process_restore_file(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    doc = message.document
    name = doc.file_name or ""
    low = name.lower()
    is_enc = low.endswith(".dbenc")
    if not (is_enc or low.endswith((".db", ".db3", ".sqlite", ".sqlite3"))):
        await message.answer("❌ Нужен файл базы: .db / .db3 / .sqlite / .dbenc")
        return
    if (doc.file_size or 0) > RESTORE_MAX_MB * 1024 * 1024:
        await message.answer(f"❌ Файл больше {RESTORE_MAX_MB} МБ — Telegram не даст его скачать.")
        return
    async with _restore_lock(message.from_user.id):
        files = list((await state.get_data()).get("restore_files") or [])
        if len(files) >= RESTORE_MAX_FILES:
            await message.answer(f"Лимит {RESTORE_MAX_FILES} файлов. Нажмите «✅ Готово».")
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}_{doc.file_unique_id}"
        raw = os.path.join(BACKUP_DIR, stamp + (".dbenc" if is_enc else ".db"))
        path = os.path.join(BACKUP_DIR, stamp + ".db")
        try:
            tg_file = await bot.get_file(doc.file_id)
            await bot.download_file(tg_file.file_path, raw)
            if is_enc:
                if not (CRYPTO_OK and BACKUP_PASSWORD):
                    raise ValueError("нужен BACKUP_PASSWORD и библиотека cryptography")
                decrypt_file(raw, path, BACKUP_PASSWORD)
                os.remove(raw)
            count = await inspect_backup(path)
        except Exception as e:
            for p in (raw, path):
                try:
                    os.remove(p)
                except Exception:
                    pass
            await message.answer(f"❌ «{escape_html(name)}» не подходит: {escape_html(scrub(e))}",
                                 parse_mode="HTML")
            return
        files.append({"path": path, "name": name, "count": count})
        await state.update_data(restore_files=files)
    await message.answer(f"📥 Принято: {name}\nОбъявлений в файле: {count}\n"
                         f"Всего файлов: {len(files)} из {RESTORE_MAX_FILES}", reply_markup=restore_kb())


@dp.message(AdForm.waiting_for_restore_db, TextAnswer())
async def restore_waiting_text(message: types.Message):
    await message.answer("Пришлите файлы базы как ДОКУМЕНТ или нажмите «✅ Готово».",
                         reply_markup=restore_kb())


@dp.callback_query(F.data == "restore_done", AdForm.waiting_for_restore_db)
async def restore_done(callback: types.CallbackQuery, state: FSMContext):
    files = (await state.get_data()).get("restore_files") or []
    if not files:
        await callback.answer("Сначала пришлите хотя бы один файл", show_alert=True)
        return
    lines = "\n".join(f"{i}. {f['name']} — {f['count']} объявл." for i, f in enumerate(files, 1))
    await safe_edit_or_caption(callback.message,
                               f"📦 Файлов: {len(files)}\n"
                               f"Объявлений в них: {sum(f['count'] for f in files)}\n\n{lines}\n\n"
                               "Что сделать?", restore_action_kb())
    await callback.answer()


@dp.callback_query(F.data == "restore_merge", AdForm.waiting_for_restore_db)
async def restore_merge(callback: types.CallbackQuery, state: FSMContext):
    if not is_sudo(callback.from_user.id):
        await callback.answer("Сессия /sudo истекла. Повторите /restore", show_alert=True)
        return
    files = (await state.get_data()).get("restore_files") or []
    if not files:
        await callback.answer("Файлов нет.", show_alert=True)
        return
    await safe_edit_or_caption(callback.message, "🧩 Объединяю, подождите…")
    total = {"listings": 0, "users": 0, "subscriptions": 0}
    lines = []
    for f in files:
        try:
            res = await merge_backup(f["path"])
            for k in total:
                total[k] += res[k]
            lines.append(f"✅ {f['name']}: +{res['listings']} объявл., +{res['users']} польз., "
                         f"+{res['subscriptions']} подписок")
        except Exception as e:
            lines.append(f"❌ {f['name']}: {scrub(e)}")
        try:
            os.remove(f["path"])
        except Exception:
            pass
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM listings") as cur:
            cnt = (await cur.fetchone())[0]
    await audit(f"📥 Объединение бэкапов: +{total['listings']} объявлений. Всего: {cnt}")
    await safe_edit_or_caption(callback.message,
                               "🧩 Объединение завершено\n\n" + "\n".join(lines) +
                               f"\n\nИтого: +{total['listings']} объявлений, +{total['users']} пользователей, "
                               f"+{total['subscriptions']} подписок.\nВ базе сейчас: {cnt}\n\n"
                               "Дубли пропущены, ничего не удалено.", main_menu_kb())
    await callback.answer("Готово")


@dp.callback_query(F.data == "restore_replace", AdForm.waiting_for_restore_db)
async def restore_replace(callback: types.CallbackQuery, state: FSMContext):
    if not is_sudo(callback.from_user.id):
        await callback.answer("Сессия /sudo истекла. Повторите /restore", show_alert=True)
        return
    files = (await state.get_data()).get("restore_files") or []
    if not files:
        await callback.answer("Файлов нет.", show_alert=True)
        return
    best = max(files, key=lambda f: f["count"])
    await safe_edit_or_caption(callback.message, f"♻️ Заменяю базой {best['name']}…")
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM listings") as cur:
                current = (await cur.fetchone())[0]
        if current > best["count"]:
            await safe_edit_or_caption(
                callback.message,
                f"🛑 Защита: в текущей базе {current} объявлений, а в файле только "
                f"{best['count']}.\n\nЗамена отменена, чтобы не потерять данные.\n"
                "Используйте «🧩 Объединить все».", main_menu_kb())
            await callback.answer()
            return
        safety = await make_snapshot()
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + suffix)
            except Exception:
                pass
        shutil.copy2(best["path"], DB_PATH)
        await init_db()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM listings") as cur:
                cnt = (await cur.fetchone())[0]
    except Exception as e:
        await safe_edit_or_caption(callback.message,
                                   f"❌ Ошибка замены: {escape_html(scrub(e))}", main_menu_kb())
        await callback.answer()
        return
    for f in files:
        try:
            os.remove(f["path"])
        except Exception:
            pass
    await state.clear()
    await audit(f"♻️ База заменена файлом {escape_html(best['name'])}. Объявлений: {cnt}")
    await safe_edit_or_caption(callback.message,
                               f"✅ База заменена файлом {best['name']}\nОбъявлений: {cnt}\n\n"
                               f"Прежняя база сохранена: {safety}", main_menu_kb())
    await callback.answer("Готово")


@dp.callback_query(F.data == "restore_cancel")
async def restore_cancel(callback: types.CallbackQuery, state: FSMContext):
    for f in (await state.get_data()).get("restore_files") or []:
        try:
            os.remove(f["path"])
        except Exception:
            pass
    await state.clear()
    await safe_edit_or_caption(callback.message, "🗑 Восстановление отменено.", main_menu_kb())
    await callback.answer()


# ---------- УБОРКА В ЧАТЕ ----------
@dp.message(Command("chat_clean"))
async def cmd_chat_clean(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        m = await bot.send_message(FLEA_CHAT, "⌨️",
                                   reply_markup=ReplyKeyboardRemove(selective=False),
                                   disable_notification=True)
        await message.answer(
            f"✅ Клавиатура снята. Служебное сообщение id={m.message_id} оставлено — "
            "не удаляйте его, иначе кнопки вернутся.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(scrub(e))}</code>", parse_mode="HTML")


@dp.message(Command("chat_reset"))
async def cmd_chat_reset(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        chat = await bot.get_chat(FLEA_CHAT)
    except Exception as e:
        await message.answer(f"❌ Не могу получить чат {FLEA_CHAT}: {escape_html(scrub(e))}",
                             parse_mode="HTML")
        return
    report = [f"Чат: {chat.title}\nid: {chat.id}\n"]
    keep = "keep" in (message.text or "").lower()
    try:
        m = await bot.send_message(chat.id, "⌨️", reply_markup=ReplyKeyboardRemove(selective=False),
                                   disable_notification=True)
        if not keep:
            await asyncio.sleep(5)
            try:
                await bot.delete_message(chat.id, m.message_id)
                report.append("✅ Клавиатура снята (сообщение удалено)")
            except Exception:
                report.append(f"✅ Клавиатура снята, id={m.message_id} удалить не удалось")
        else:
            report.append(f"✅ Клавиатура снята, сообщение id={m.message_id} оставлено")
    except Exception as e:
        report.append(f"❌ Клавиатура: {scrub(e)}")
    for scope in (BotCommandScopeChat(chat_id=chat.id), BotCommandScopeAllGroupChats(),
                  BotCommandScopeAllChatAdministrators(), BotCommandScopeDefault()):
        try:
            await bot.set_my_commands([], scope=scope)
            report.append(f"✅ Команды очищены: {type(scope).__name__}")
        except Exception as e:
            report.append(f"❌ {type(scope).__name__}: {scrub(e)}")
    await message.answer("\n".join(report))


@dp.message(Command("chat_unpin"))
async def cmd_chat_unpin(message: types.Message):
    if not await require_sudo(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/chat_unpin message_id</code>", parse_mode="HTML")
        return
    try:
        await bot.unpin_chat_message(chat_id=FLEA_CHAT, message_id=int(parts[1]))
        await message.answer("✅ Откреплено.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(scrub(e))}</code>", parse_mode="HTML")


@dp.message(Command("chat_del"))
async def cmd_chat_del(message: types.Message):
    if not await require_sudo(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/chat_del message_id</code>", parse_mode="HTML")
        return
    try:
        await bot.delete_message(chat_id=FLEA_CHAT, message_id=int(parts[1]))
        await audit(f"🗑 Удалено сообщение {parts[1]} в чате")
        await message.answer("✅ Удалено.")
    except Exception as e:
        await message.answer(f"❌ <code>{escape_html(scrub(e))}</code>", parse_mode="HTML")


@dp.message(Command("reload_menu"))
async def cmd_reload_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await setup_bot_commands()
    await message.answer("✅ Меню команд обновлено.")


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
        async with db.execute("SELECT * FROM listings WHERE id=? AND user_id=?",
                              (lot_id, callback.from_user.id)) as cur:
            ad = await cur.fetchone()
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    await state.update_data(paid_context="renew", renew_lot_id=lot_id, donatepay_pending=False)
    await safe_edit_or_caption(callback.message,
                               "Объявление будет выставлено на 7 дней.\n\nВыберите способ оплаты:",
                               InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="25⭐️", callback_data="pay_stars")],
                                   [InlineKeyboardButton(text="25💸", callback_data="pay_donatepay")],
                                   [InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu")]]))
    await callback.answer()


# ---------- STARS: ПРИЁМ ПЛАТЕЖА С ПРОВЕРКОЙ ----------
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

    # 1) защита от повтора
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM payments WHERE charge_id=?", (charge_id,)) as cur:
            if await cur.fetchone():
                log("duplicate charge, skip:", charge_id)
                return

    # 2) payload должен принадлежать этому пользователю
    owns = (payload in (f"pub_{user_id}", f"verify_{user_id}")
            or re.fullmatch(rf"renew_\d+_{user_id}", payload)
            or re.fullmatch(rf"promote_\d+_{user_id}", payload))
    if not owns:
        await audit(f"🚨 <b>Чужой payload</b>\nUser: <code>{user_id}</code>\n"
                    f"Payload: <code>{escape_html(payload)}</code>\nУслуга НЕ выдана.")
        return

    # 3) сверка с реальной транзакцией Telegram
    verified_tx = False
    api_failed = False
    try:
        txs = await bot.get_star_transactions(limit=100)
        verified_tx = any(t.id == charge_id and t.amount == STARS_PRICE for t in txs.transactions)
    except Exception as e:
        api_failed = True
        log("get_star_transactions failed:", e)
    if not verified_tx and not api_failed:
        await bot.send_message(ADMIN_ID,
                               f"🚨 <b>Поддельный платёж?</b>\nUser: <code>{user_id}</code>\n"
                               f"Charge: <code>{escape_html(charge_id)}</code>\n"
                               f"Payload: <code>{escape_html(payload)}</code>\n"
                               "Транзакция не найдена у Telegram. Услуга НЕ выдана.",
                               parse_mode="HTML")
        await audit(f"🚨 Платёж не подтверждён Telegram: <code>{escape_html(charge_id)}</code>")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO payments (charge_id, user_id, payload, amount, created_at) "
                         "VALUES (?,?,?,?,?)",
                         (charge_id, user_id, payload, STARS_PRICE, datetime.now().isoformat()))
        await db.commit()
    await audit(f"💳 Платёж принят: <code>{user_id}</code>, {STARS_PRICE}⭐️, "
                f"<code>{escape_html(payload)}</code>"
                + (" (API сверки недоступен)" if api_failed else ""))

    if payload.startswith("promote_"):
        lot_id = _safe_lot_id(payload)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM listings WHERE id=? AND user_id=?",
                                  (lot_id, user_id)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await message.answer(f"Объявление не найдено, но оплата получена. {SUPPORT_CONTACT}")
            return
        until = (datetime.now() + timedelta(days=7)).isoformat()
        caption = await rebuild_caption(ad, promoted=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE listings SET is_promoted=1, promoted_until=?, full_caption=? WHERE id=?",
                             (until, caption, lot_id))
            await db.commit()
        await pin_first_posts(ad)
        await message.answer("✅ Оплата получена! Объявление закреплено в топе на 7 дней.")
        return

    if payload.startswith("verify_"):
        await mark_verified(user_id)
        await message.answer("✅ Верификация подтверждена! Галочка появилась в профиле.")
        return

    if payload.startswith("renew_"):
        lot_id = _safe_lot_id(payload)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM listings WHERE id=? AND user_id=?",
                                  (lot_id, user_id)) as cur:
                ad = await cur.fetchone()
        if not ad:
            await message.answer(f"Объявление не найдено, но оплата получена. {SUPPORT_CONTACT}")
            return
        caption = await rebuild_caption(ad, status="АКТУАЛЬНО")
        new_expires = (datetime.now() + timedelta(days=7)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE listings SET status='ACTIVE', is_trial=0, expires_at=?, "
                             "charge_id=?, full_caption=? WHERE id=?",
                             (new_expires, charge_id, caption, lot_id))
            await db.commit()
        await message.answer("✅ Оплата получена! Публикация продлена на 7 дней.")
        await state.update_data(paid_context=None, renew_lot_id=None)
        return

    await state.update_data(is_trial=False, paid=True, charge_id=charge_id, moderation_sent=False)
    await message.answer("✅ Оплата получена!\n\n" + FORM_INTRO, reply_markup=ad_type_kb())


# ---------- ФОНОВЫЕ ЗАДАЧИ ----------
async def expire_loop():
    while True:
        try:
            now = datetime.now().isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM listings WHERE status='ACTIVE' "
                                      "AND expires_at IS NOT NULL AND expires_at <= ?", (now,)) as cur:
                    expired = await cur.fetchall()
            for ad in expired:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute("UPDATE listings SET status='SOLD', is_promoted=0, "
                                           "promoted_until=NULL WHERE id=? AND status='ACTIVE'",
                                           (ad["id"],))
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
                        f"У вашего объявления «{row_get(ad,'model','')}» кончился срок публикации. "
                        "Продлить на 7 дней?",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💸 Продлить", callback_data=f"renew_{ad['id']}")],
                            [InlineKeyboardButton(text="🏡 В главное меню", callback_data="main_menu")]]))
                except Exception as e:
                    log("expire notify:", e)
        except Exception as e:
            log("expire_loop:", e)
        await asyncio.sleep(60)


async def unpin_loop():
    while True:
        try:
            now = datetime.now().isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM listings WHERE is_promoted=1 "
                                      "AND promoted_until IS NOT NULL AND promoted_until <= ?",
                                      (now,)) as cur:
                    expired = await cur.fetchall()
            for ad in expired:
                caption = await rebuild_caption(ad, status="АКТУАЛЬНО", promoted=False)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE listings SET is_promoted=0, promoted_until=NULL, "
                                     "full_caption=? WHERE id=?", (caption, ad["id"]))
                    await db.commit()
                await unpin_first_posts(ad)
                try:
                    await bot.send_message(ad["user_id"],
                                           f"⏰ Срок закрепа «{row_get(ad,'model','')}» истёк. "
                                           "Пометка 🔥 ТОП убрана.")
                except Exception:
                    pass
        except Exception as e:
            log("unpin_loop:", e)
        await asyncio.sleep(60)


async def backup_loop():
    while True:
        await asyncio.sleep(3600)
        try:
            if os.path.exists(DB_PATH):
                await send_backup(ADMIN_ID, f"Автобэкап за {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        except Exception as e:
            log("backup_loop:", e)


async def watchdog_loop():
    """Сторож вебхука: если его удалили/подменили — вернёт и предупредит."""
    while True:
        await asyncio.sleep(300)
        try:
            base_url = _env("RENDER_EXTERNAL_URL")
            if not base_url:
                continue
            want = f"{base_url}/webhook"
            info = await bot.get_webhook_info()
            if info.url != want:
                await bot.set_webhook(want, secret_token=WEBHOOK_SECRET)
                await bot.send_message(
                    ADMIN_ID,
                    "🚨 <b>Вебхук был изменён или удалён!</b>\n"
                    f"Было: <code>{escape_html(scrub(info.url or '—'))}</code>\n"
                    "Восстановлен. Если это не вы — немедленно смените токен в BotFather.",
                    parse_mode="HTML")
                await audit("🚨 Вебхук был изменён извне и восстановлен")
            if (info.pending_update_count or 0) > 200:
                await bot.send_message(ADMIN_ID,
                                       f"⚠️ Очередь апдейтов: {info.pending_update_count}\n"
                                       f"Ошибка: {escape_html(scrub(info.last_error_message or '—'))}")
        except Exception as e:
            log("watchdog:", e)


# ---------- МЕНЮ КОМАНД: только владелец ----------
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
        BotCommand(command="sudo", description="🔓 Подтвердить личность (PIN)"),
        BotCommand(command="security", description="🔐 Состояние защиты"),
        BotCommand(command="panic", description="🚨 Закрыть/открыть бота"),
        BotCommand(command="stats", description="📊 Статистика по разделам"),
        BotCommand(command="check_ads", description="📋 Последние 30 объявлений"),
        BotCommand(command="dbinfo", description="🗂 Схема и целостность базы"),
        BotCommand(command="broadcast", description="📢 Рассылка (нужен /sudo)"),
        BotCommand(command="send", description="✉️ Написать пользователю"),
        BotCommand(command="users", description="👥 Список пользователей"),
        BotCommand(command="backup", description="💾 Выгрузить бэкап"),
        BotCommand(command="restore", description="📥 Восстановить базу"),
        BotCommand(command="fix_tags", description="♻️ Починить хештеги"),
        BotCommand(command="chat_clean", description="🧹 Убрать клавиатуру в чате"),
        BotCommand(command="chat_reset", description="♻️ Сброс меню в чате"),
        BotCommand(command="chat_unpin", description="📌 Открепить в чате"),
        BotCommand(command="chat_del", description="🗑 Удалить сообщение в чате"),
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
            log(f"set_my_commands {type(scope).__name__}:", e)


# ---------- FALLBACK (последними!) ----------
@dp.message(~F.text, F.chat.type == ChatType.PRIVATE)
async def handle_non_text(message: types.Message, state: FSMContext):
    if (message.successful_payment or message.pinned_message
            or message.new_chat_members or message.left_chat_member):
        return
    current = await state.get_state()
    if current == AdForm.uploading_photos.state:
        await message.answer("📸 Нужны именно ФОТО (сжатые), а не файл, видео или стикер.",
                             reply_markup=photos_kb())
        return
    if current == AdForm.waiting_for_restore_db.state:
        await message.answer("📁 Пришлите файлы базы как ДОКУМЕНТ.", reply_markup=restore_kb())
        return
    await message.answer("📎 Я понимаю только текстовые сообщения.\n"
                         "Напишите текстом или воспользуйтесь меню ниже 👇", reply_markup=bottom_menu())


@dp.message(F.text.startswith("/"), F.chat.type == ChatType.PRIVATE)
async def unknown_command(message: types.Message):
    await message.answer("🤷 Неизвестная команда. Откройте меню: /menu", reply_markup=bottom_menu())


@dp.message(F.text, F.chat.type == ChatType.PRIVATE)
async def fallback_text(message: types.Message):
    await message.answer("Я не понял сообщение 🙂\nОткройте главное меню: /menu",
                         reply_markup=bottom_menu())


# ---------- ЗАПУСК ----------
async def on_startup(bot: Bot):
    await init_db()
    await setup_bot_commands()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    base_url = _env("RENDER_EXTERNAL_URL")
    if base_url:
        await bot.set_webhook(f"{base_url}/webhook", secret_token=WEBHOOK_SECRET,
                              drop_pending_updates=False)
        log(f"Webhook установлен: {base_url}/webhook")
    for section, chans in SECTION_CHANNELS.items():
        log(f"Раздел {section} → {', '.join(chans)}")
    log(f"Защита: секрет ✅ | PIN {'✅' if ADMIN_PIN else '❌'} | "
        f"аудит {'✅' if AUDIT_CHAT_ID else '❌'} | "
        f"шифрование бэкапов {'✅' if (BACKUP_PASSWORD and CRYPTO_OK) else '❌'}")
    await audit("🟢 Бот запущен")


async def health_check(request):
    return web.json_response({"status": "ok"})


async def main():
    await init_db()
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", health_check)
    SimpleRequestHandler(dispatcher=dp, bot=bot,
                         secret_token=WEBHOOK_SECRET).register(app, path="/webhook")

    dp.message.outer_middleware(AdminErrorMiddleware())
    dp.callback_query.outer_middleware(AdminErrorMiddleware())
    dp.message.outer_middleware(PrivateOnlyMiddleware())
    dp.callback_query.outer_middleware(PrivateOnlyMiddleware())
    dp.message.outer_middleware(PanicMiddleware())
    dp.callback_query.outer_middleware(PanicMiddleware())
    dp.message.outer_middleware(RateLimitMiddleware(rate=0.7, burst=6))
    dp.callback_query.middleware(AntiSpamMiddleware(cooldown=2.0))

    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    tasks = [asyncio.create_task(c) for c in
             (expire_loop(), unpin_loop(), backup_loop(), watchdog_loop())]

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(_env("PORT", "8080")))
    await site.start()
    log(f"Сервер запущен на порту {_env('PORT', '8080')}")
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