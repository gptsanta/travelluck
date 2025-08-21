import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, Defaults, filters
)

from .config import TELEGRAM_TOKEN
from .generate import generate_post
from .sheets import (
    append_post, get_post_by_id, list_recent_posts, delete_post, update_post_fields
)

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("travelluck.bot")

# -------------------- Утилиты --------------------
def chunk_text(s: str, max_len: int = 3500):
    s = s or ""
    for i in range(0, len(s), max_len):
        yield s[i:i + max_len]

def sanitize_plain(s: str) -> str:
    return (s or "").replace("\r", "").strip()

# -------------------- Команды --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("/start from chat_id=%s", update.effective_chat.id)
    await update.message.reply_text(
        "Привет! Я бот-редактор тревел-постов.\n"
        "Команды:\n"
        "/newpost <тема> — сгенерировать черновик и сохранить в Google Sheets\n"
        "/list — показать последние записи\n"
        "/list <id> — показать запись целиком\n"
        "/edit <id> — отредактировать title, text, image_prompt\n"
        "/delete <id> — удалить запись"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("/help from chat_id=%s", update.effective_chat.id)
    await start(update, context)

# -------------------- NEWPOST --------------------
async def newpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи тему: /newpost Париж весной")
        return

    topic = " ".join(context.args).strip()
    log.info("/newpost topic='%s' chat_id=%s", topic, update.effective_chat.id)
    await update.message.reply_text(f"Генерирую пост про: {topic} ...")

    try:
        post = await generate_post(topic)
        log.info("Generated post: title='%s...' image_url='%s' has_bytes=%s",
                 (post.get('title') or "")[:60],
                 post.get('image_url') or "",
                 bool(post.get('image_bytes')))

        title = sanitize_plain(post.get("title")) or "Без названия"
        text = sanitize_plain(post.get("text")) or "..."
        image_prompt = sanitize_plain(post.get("image_prompt"))
        image_url = sanitize_plain(post.get("image_url"))
        image_bytes = post.get("image_bytes")

        # Фолбэк: если Drive не дал ссылку, но есть байты — зальём как фото в Telegram и сохраним file_id
        tg_file_id = ""
        if not image_url and image_bytes:
            try:
                log.info("Uploading photo to Telegram (fallback)...")
                sent = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=image_bytes,
                    caption="Черновик: сгенерированное изображение (временное хранение в Telegram)."
                )
                if sent and sent.photo:
                    tg_file_id = sent.photo[-1].file_id
                    image_url = f"tg:{tg_file_id}"
                    log.info("Telegram file_id saved: %s", tg_file_id)
            except Exception as e:
                log.exception("Failed to upload photo to Telegram: %s", e)

        row_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        created_at = datetime.now(timezone.utc).isoformat()

        row_dict = {
            "id": row_id,
            "status": "draft",
            "title": title,
            "text": text,
            "image_prompt": image_prompt,
            "image_url": image_url,  # либо https://drive..., либо tg:<file_id>, либо пусто
            "created_at": created_at,
            "scheduled_at": "",
            "posted_at": "",
            "chat_id": str(update.effective_chat.id),
            "message_id": "",
            "error": ""
        }

        saved = append_post(row_dict)
        log.info("Row appended to sheet: id=%s", saved["id"])

        preview_plain = (
            f"Черновик создан (id: {saved['id']})\n\n"
            f"Title:\n{saved['title']}\n\n"
            f"Text:\n{saved['text']}\n\n"
            f"Image prompt:\n{saved['image_prompt']}\n"
        )
        if saved.get("image_url"):
            preview_plain += f"\nImage Source:\n{saved['image_url']}\n"
            if saved['image_url'].startswith("tg:"):
                preview_plain += "(Сохранено как Telegram file_id — подходит для повторной отправки этим ботом)\n"
        else:
            preview_plain += "\nImage: not generated or not uploaded ❌\n"

        for part in chunk_text(preview_plain):
            await update.message.reply_text(part)

    except Exception as e:
        log.exception("Error in /newpost: %s", e)
        await update.message.reply_text(
            f"Не удалось создать пост: {e}\nПопробуй ещё раз или измени тему."
        )

# -------------------- LIST --------------------
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        post_id = context.args[0].strip()
        log.info("/list %s chat_id=%s", post_id, update.effective_chat.id)
        try:
            data = get_post_by_id(post_id)
        except Exception as e:
            log.exception("get_post_by_id failed for id=%s: %s", post_id, e)
            await update.message.reply_text("Ошибка чтения записи.")
            return

        if not data:
            await update.message.reply_text("Запись не найдена.")
            return

        out = []
        if data.get("title"):
            out.append(f"Title:\n{data['title']}\n")
        if data.get("text"):
            out.append(f"Text:\n{data['text']}\n")
        if data.get("image_prompt"):
            out.append(f"Image prompt:\n{data['image_prompt']}\n")
        if data.get("image_url"):
            out.append(f"Image Source:\n{data['image_url']}\n")
        full = "\n".join(out).strip() or "(пусто)"
        for part in chunk_text(full):
            await update.message.reply_text(part)
        return

    # Показать последние N
    log.info("/list recent chat_id=%s", update.effective_chat.id)
    try:
        posts = list_recent_posts(limit=10)
    except Exception as e:
        log.exception("list_recent_posts failed: %s", e)
        await update.message.reply_text("Не удалось получить список записей.")
        return

    if not posts:
        await update.message.reply_text("Пока записей нет.")
        return

    lines = []
    for p in posts:
        t = (p.get("title") or "").strip().replace("\n", " ")
        if len(t) > 80:
            t = t[:77] + "..."
        lines.append(f"{p['id']} — {t or '(без названия)'}")

    await update.message.reply_text("Последние записи:\n" + "\n".join(lines))

# -------------------- EDIT (Conversation) --------------------
EDIT_TITLE, EDIT_TEXT, EDIT_IMAGE_PROMPT = range(3)

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи id: /edit <id>")
        return ConversationHandler.END

    post_id = context.args[0].strip()
    log.info("/edit id=%s chat_id=%s", post_id, update.effective_chat.id)
    try:
        data = get_post_by_id(post_id)
    except Exception as e:
        log.exception("get_post_by_id failed for edit id=%s: %s", post_id, e)
        await update.message.reply_text("Ошибка чтения записи.")
        return ConversationHandler.END

    if not data:
        await update.message.reply_text("Запись не найдена.")
        return ConversationHandler.END

    # Сохраняем текущее состояние
    context.user_data["edit_id"] = post_id
    context.user_data["orig_title"] = data.get("title") or ""
    context.user_data["orig_text"] = data.get("text") or ""
    context.user_data["orig_image_prompt"] = data.get("image_prompt") or ""

    # временные новые значения (None = оставить как есть)
    context.user_data["new_title"] = None
    context.user_data["new_text"] = None
    context.user_data["new_image_prompt"] = None

    # Показать текущий title
    cur_title = data.get("title") or "(пусто)"
    msg = (
        "Редактирование записи: {pid}\n\n"
        "Текущий Title:\n{t}\n\n"
        "Отправь новый заголовок, или отправь 'Z' чтобы оставить без изменений."
    ).format(pid=post_id, t=cur_title)
    for part in chunk_text(msg):
        await update.message.reply_text(part)
    return EDIT_TITLE

async def edit_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    log.info("edit_title input len=%d", len(text))
    if text.lower() == "z":
        context.user_data["new_title"] = None
    else:
        context.user_data["new_title"] = sanitize_plain(text)

    # Показать текущий text
    cur_text = context.user_data.get("orig_text") or "(пусто)"
    header = "Текущий Text (может быть длинным):\n"
    for part in chunk_text(header + cur_text):
        await update.message.reply_text(part)

    await update.message.reply_text(
        "Отправь новый Text, или отправь 'Z' чтобы оставить без изменений."
    )
    return EDIT_TEXT

async def edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    log.info("edit_text input len=%d", len(text))
    if text.lower() == "z":
        context.user_data["new_text"] = None
    else:
        context.user_data["new_text"] = sanitize_plain(text)

    # Показать текущий image_prompt
    cur_ip = context.user_data.get("orig_image_prompt") or "(пусто)"
    msg = (
        "Текущий image_prompt:\n{ip}\n\n"
        "Отправь новый image_prompt, или отправь 'Z' чтобы оставить без изменений."
    ).format(ip=cur_ip)
    for part in chunk_text(msg):
        await update.message.reply_text(part)
    return EDIT_IMAGE_PROMPT

async def edit_image_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    log.info("edit_image_prompt input len=%d", len(text))
    if text.lower() == "z":
        context.user_data["new_image_prompt"] = None
    else:
        context.user_data["new_image_prompt"] = sanitize_plain(text)

    # Сохраняем изменения
    pid = context.user_data.get("edit_id")
    try:
        ok = update_post_fields(
            pid,
            title=context.user_data["new_title"],
            text=context.user_data["new_text"],
            image_prompt=context.user_data["new_image_prompt"]
        )
        log.info("update_post_fields(id=%s) -> %s", pid, ok)
        if ok:
            await update.message.reply_text("Изменения сохранены.")
            data = get_post_by_id(pid)
            if data:
                out = []
                if data.get("title"):
                    out.append(f"Title:\n{data['title']}\n")
                if data.get("text"):
                    out.append(f"Text:\n{data['text']}\n")
                if data.get("image_prompt"):
                    out.append(f"Image prompt:\n{data['image_prompt']}\n")
                if data.get("image_url"):
                    out.append(f"Image Source:\n{data['image_url']}\n")
                full = "\n".join(out).strip() or "(пусто)"
                for part in chunk_text(full):
                    await update.message.reply_text(part)
        else:
            await update.message.reply_text("Не удалось обновить запись (возможно, не найдена).")
    except Exception as e:
        log.exception("Error in edit save for id=%s: %s", pid, e)
        await update.message.reply_text(f"Ошибка при сохранении: {e}")

    # очистим временные данные
    for k in ("edit_id","orig_title","orig_text","orig_image_prompt",
              "new_title","new_text","new_image_prompt"):
        context.user_data.pop(k, None)

    return ConversationHandler.END

# -------------------- DELETE (Conversation) --------------------
DELETE_CONFIRM = 100

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи id: /delete <id>")
        return ConversationHandler.END

    context.user_data["delete_id"] = context.args[0].strip()
    log.info("/delete ask id=%s chat_id=%s", context.user_data["delete_id"], update.effective_chat.id)
    await update.message.reply_text(
        f"Удалить запись {context.user_data['delete_id']}? Напиши 'да' или 'нет'."
    )
    return DELETE_CONFIRM

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    pid = context.user_data.get("delete_id")
    log.info("/delete confirm id=%s answer=%s", pid, text)
    if text in ("да", "yes", "y"):
        try:
            ok = delete_post(pid)
        except Exception as e:
            log.exception("delete_post failed for id=%s: %s", pid, e)
            await update.message.reply_text("Ошибка удаления.")
            return ConversationHandler.END

        await update.message.reply_text(
            "Удалено." if ok else "Не найдено — ничего не удалено."
        )
    else:
        await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# -------------------- main --------------------
def main():
    defaults = Defaults(parse_mode=None)
    app = Application.builder().token(TELEGRAM_TOKEN).defaults(defaults).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newpost", newpost))
    app.add_handler(CommandHandler("list", list_cmd))

    # Edit conversation
    edit_handler = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_command)],
        states={
            EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_title)],
            EDIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_text)],
            EDIT_IMAGE_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_image_prompt)],
        },
        fallbacks=[]
    )
    app.add_handler(edit_handler)

    # Delete conversation
    delete_handler = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_command)],
        states={DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)]},
        fallbacks=[]
    )
    app.add_handler(delete_handler)

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    log.info("Bot stopped.")

if __name__ == "__main__":
    main()
