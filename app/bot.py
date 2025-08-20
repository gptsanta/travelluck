import asyncio
from datetime import datetime, timezone
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters
)
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

from .config import TELEGRAM_TOKEN, ADMIN_ID
from .generate import generate_post, generate_image
from .sheets import append_post, update_post, get_post_by_id, delete_post, update_image_url

HELP_TEXT = (
    "/newpost <тема> — сгенерировать черновик и добавить в Google Sheets\n"
    "/editpost <id> — редактировать пост по ID\n"
    "/list [status|ID] — показать список постов или пост по ID\n"
    "/delete <id> — удалить пост по ID\n"
    "/help — эта справка"
)

# Состояния для ConversationHandler
EDIT_TITLE, EDIT_TEXT, EDIT_PROMPT = range(3)
DELETE_CONFIRM = 100

# ----------------- Стандартные команды -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот-редактор тревел-постов.\n"
        "Для создания нового поста введите /newpost <тема>.\n"
        "Для редактирования поста введите /editpost <id>\n"
        "Для удаления поста используйте /delete <id>\n"
        "Для списка постов — /list [draft|posted|ID]\n"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

# ----------------- Команда /newpost -----------------
async def newpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажи тему поста: /newpost <тема>")
        return

    topic = " ".join(context.args).strip()
    await update.message.reply_text(
        f"Генерирую пост про: *{topic}* ...", parse_mode=ParseMode.MARKDOWN
    )

    try:
        post = await generate_post(topic)
    except Exception as e:
        print(f"[newpost] Ошибка генерации поста: {e}")
        await update.message.reply_text("❌ Не удалось сгенерировать пост")
        return

    row = {
        "status": "draft",
        "title": post.get("title", ""),
        "text": post.get("text", ""),
        "image_prompt": post.get("image_prompt", ""),
        "image_url": post.get("image_url", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scheduled_at": "",
        "posted_at": "",
        "chat_id": str(update.effective_chat.id),
        "message_id": ""
    }

    try:
        row_id = await asyncio.to_thread(append_post, row)
    except Exception as e:
        print(f"[newpost] Ошибка добавления поста в таблицу: {e}")
        await update.message.reply_text("❌ Не удалось сохранить пост в таблице")
        return

    preview = (
        f"*Черновик создан* (id: `{row_id}`)\n\n"
        f"*{row['title']}*\n\n{row['text']}\n\n"
        f"_image prompt:_ `{row['image_prompt']}`\n"
    )
    if row['image_url']:
        preview += f"_image url:_ {row['image_url']}"

    await update.message.reply_text(preview, parse_mode=ParseMode.MARKDOWN)

# ----------------- Команда /editpost -----------------
async def editpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Используй: /editpost <id>")
        return ConversationHandler.END

    post_id = context.args[0]
    context.user_data["edit_id"] = post_id

    post = await asyncio.to_thread(get_post_by_id, post_id)
    if not post:
        await update.message.reply_text(f"❌ Пост с ID {post_id} не найден")
        return ConversationHandler.END

    context.user_data["current_post"] = post
    await update.message.reply_text(
        f"Редактируем пост с ID {post_id}.\n\n"
        f"*Текущий заголовок:* {post.get('title','')}\n"
        "Пришли новый заголовок или отправь Z, чтобы не менять"
    )
    return EDIT_TITLE

async def editpost_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_title"] = None if text.upper() == "Z" else text
    post = context.user_data.get("current_post", {})
    await update.message.reply_text(
        f"*Текущий текст:* {post.get('text','')}\n"
        "Пришли новый текст поста или отправь Z, чтобы не менять"
    )
    return EDIT_TEXT

async def editpost_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_text"] = None if text.upper() == "Z" else text
    post = context.user_data.get("current_post", {})
    await update.message.reply_text(
        f"*Текущий image prompt:* {post.get('image_prompt','')}\n"
        "Пришли новый image prompt или отправь Z, чтобы не менять"
    )
    return EDIT_PROMPT

async def editpost_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    new_prompt = None if text.upper() == "Z" else text
    context.user_data["new_prompt"] = new_prompt

    post_id = context.user_data.get("edit_id")
    success = await asyncio.to_thread(
        update_post,
        post_id,
        context.user_data.get("new_title"),
        context.user_data.get("new_text"),
        new_prompt
    )

    image_url = None
    if new_prompt:
        try:
            image_url = await generate_image(new_prompt)
            if image_url:
                await asyncio.to_thread(update_image_url, post_id, image_url)
        except Exception as e:
            print(f"[editpost_prompt] Ошибка генерации изображения: {e}")
            image_url = None

    if success:
        await update.message.reply_text(
            f"✅ Пост {post_id} обновлён\n"
            f"{'Новая картинка сгенерирована' if image_url else ''}"
        )
    else:
        await update.message.reply_text("❌ Не найден пост с таким ID")

    context.user_data.clear()
    return ConversationHandler.END

# ----------------- Команда /list -----------------
async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0].lower() if context.args else None
    from .sheets import _client, _open_sheet
    gc = await asyncio.to_thread(_client)
    ws = await asyncio.to_thread(_open_sheet, gc)
    all_values = ws.get_all_values()[1:]  # пропускаем заголовок

    if arg in ["draft", "posted"]:
        filtered = [row for row in all_values if len(row) > 2 and row[1] == arg]
        if not filtered:
            await update.message.reply_text(f"Постов со статусом {arg} нет.")
            return
        lines = []
        for row in filtered:
            title_match = re.match(r'\*\*(.*?)\*\*', row[2])
            title = title_match.group(1) if title_match else row[2].split("\n\n")[0]
            lines.append(f"{row[0]} — {title}")
        await update.message.reply_text("\n".join(lines))
    elif arg:
        post_id = arg
        post = await asyncio.to_thread(get_post_by_id, post_id)
        if not post:
            await update.message.reply_text(f"❌ Пост с ID {post_id} не найден")
            return
        msg = (
            f"*ID:* {post_id}\n"
            f"*Title:* {post.get('title','')}\n"
            f"*Text:* {post.get('text','')}\n"
            f"*Image prompt:* {post.get('image_prompt','')}\n"
            f"*Image url:* {post.get('image_url','')}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ----------------- Команда /delete -----------------
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Используй: /delete <id>")
        return ConversationHandler.END

    post_id = context.args[0]
    post = await asyncio.to_thread(get_post_by_id, post_id)
    if not post:
        await update.message.reply_text(f"❌ Пост с ID {post_id} не найден")
        return ConversationHandler.END

    context.user_data["delete_id"] = post_id
    msg = f"Вы уверены, что хотите удалить пост?\n\n*{post.get('title','')}\n{post.get('text','')}*\n\nОтправьте Y для удаления."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    return DELETE_CONFIRM

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text != "Y":
        await update.message.reply_text("❌ Удаление отменено")
        context.user_data.pop("delete_id", None)
        return ConversationHandler.END

    post_id = context.user_data.get("delete_id")
    success = await asyncio.to_thread(delete_post, post_id)
    if success:
        await update.message.reply_text(f"✅ Пост {post_id} удалён")
    else:
        await update.message.reply_text(f"❌ Не удалось удалить пост {post_id}")

    context.user_data.clear()
    return ConversationHandler.END

# ----------------- Основная функция -----------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newpost", newpost))
    app.add_handler(CommandHandler("list", list_posts))

    # ConversationHandler для /editpost
    edit_handler = ConversationHandler(
        entry_points=[CommandHandler("editpost", editpost_command)],
        states={
            EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, editpost_title)],
            EDIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, editpost_text)],
            EDIT_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, editpost_prompt)],
        },
        fallbacks=[]
    )
    app.add_handler(edit_handler)

    # ConversationHandler для /delete
    delete_handler = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_command)],
        states={
            DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)]
        },
        fallbacks=[]
    )
    app.add_handler(delete_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
