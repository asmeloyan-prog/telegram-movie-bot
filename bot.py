import asyncio
import re
import sqlite3
import requests
import os
import json

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.filters import Command

BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = "meta-llama/llama-3.1-8b-instruct"

def extract_titles_llm(text: str) -> list[str]:
    """
    Возвращает ТОЛЬКО названия фильмов и сериалов.
    Если ничего нет — пустой список.
    """

    prompt = f"""
Ты извлекаешь ТОЛЬКО названия ФИЛЬМОВ и СЕРИАЛОВ из текста на русском языке.

Жёсткие правила:
- добавляй ТОЛЬКО если это точно фильм или сериал
- НЕ добавляй книги, игры, людей, YouTube, подкасты
- НЕ добавляй общие слова и жанры
- если есть сомнение — НЕ добавляй
- если ничего нет — верни пустой список

Верни ответ СТРОГО в JSON без пояснений.

Формат:
{{"titles": ["Название 1", "Название 2"]}}

Текст:
{text}
"""

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": "Ты аккуратный извлекатель сущностей."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        },
        timeout=20
    )

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return parsed.get("titles", [])
    except Exception:
        return []

# ---------- DATABASE ----------
db = sqlite3.connect("movies.db")
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    tmdb_id INTEGER,
    title TEXT,
    media_type TEXT,
    overview TEXT,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    watched INTEGER DEFAULT 0,
    UNIQUE(user_id, tmdb_id)
)
""")
db.commit()

# ---------- TMDB ----------
def search_tmdb(title: str):
    url = "https://api.themoviedb.org/3/search/multi"
    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "language": "ru-RU"
    }
    r = requests.get(url, params=params).json()

    for item in r.get("results", []):
        if item["media_type"] in ("movie", "tv"):
            return {
                "tmdb_id": item["id"],
                "title": item.get("title") or item.get("name"),
                "media_type": item["media_type"],
                "overview": item.get("overview", "")
            }
    return None

# ---------- TITLE EXTRACTION ----------
#def extract_titles(text: str):
#    titles = set()
#
    # Кавычки
#    titles.update(re.findall(r"[«\"]([^»\"]+)[»\"]", text))

    # spaCy
#    doc = nlp(text)
#    for ent in doc.ents:
#        if ent.label_ == "WORK_OF_ART":
#            titles.add(ent.text)

#    return list(titles)

# ---------- KEYBOARDS ----------
def watched_keyboard(movie_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Просмотрено",
            callback_data=f"watched:{movie_id}"
        )]
    ])

# ---------- HANDLERS ----------
async def handle_message(message: Message):
    if len(text) < 5:
    return
    
    text = message.text or message.caption
    if not text:
        return

    titles = extract_titles_llm(text)
    added = []

    cleaned = []
for t in titles:
    if 2 < len(t) < 60 and not any(x in t.lower() for x in ["обзор", "рецензия"]):
        cleaned.append(t)

titles = cleaned

    for raw in titles:
        data = search_tmdb(raw)
        if not data:
            continue

        try:
            cur.execute("""
                INSERT OR IGNORE INTO movies
                (user_id, tmdb_id, title, media_type, overview)
                VALUES (?, ?, ?, ?, ?)
            """, (
                message.from_user.id,
                data["tmdb_id"],
                data["title"],
                data["media_type"],
                data["overview"]
            ))
            if cur.rowcount:
                added.append(data["title"])
            db.commit()
        except:
            pass

    if added:
        await message.answer(
            "Добавлено:\n" + "\n".join(f"• {t}" for t in added)
        )

async def list_movies(message: Message):
    cur.execute("""
        SELECT id, title, media_type, overview
        FROM movies
        WHERE user_id = ? AND watched = 0
        ORDER BY added_at DESC
    """, (message.from_user.id,))

    rows = cur.fetchall()
    if not rows:
        await message.answer("Список пуст 🎬")
        return

    for movie_id, title, media_type, overview in rows:
        icon = "🎬" if media_type == "movie" else "📺"
        text = f"{icon} <b>{title}</b>\n{overview[:400]}"
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=watched_keyboard(movie_id)
        )

async def watched_callback(call: CallbackQuery):
    movie_id = int(call.data.split(":")[1])
    cur.execute(
        "UPDATE movies SET watched = 1 WHERE id = ?",
        (movie_id,)
    )
    db.commit()
    await call.message.edit_text("✅ Отмечено как просмотренное")
    await call.answer()

async def watched_command(message: Message):
    title = message.text.replace("/watched", "").strip()
    if not title:
        await message.answer("Укажи название")
        return

    cur.execute("""
        UPDATE movies
        SET watched = 1
        WHERE user_id = ? AND title LIKE ?
    """, (message.from_user.id, f"%{title}%"))
    db.commit()

    if cur.rowcount:
        await message.answer("Отмечено как просмотренное ✅")
    else:
        await message.answer("Фильм не найден")

# ---------- MAIN ----------
async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(list_movies, Command("list"))
    dp.message.register(watched_command, Command("watched"))
    dp.callback_query.register(watched_callback, F.data.startswith("watched:"))
    dp.message.register(handle_message, F.text | F.caption)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
