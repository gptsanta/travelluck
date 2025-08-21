import asyncio
import io
import logging
from datetime import datetime, timezone

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from openai import OpenAI

from .config import (
    OPENAI_API_KEY,
    STABILITY_API_KEY,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    # (опционально) GOOGLE_DRIVE_FOLDER_ID
)

# --------- Логирование ----------
log = logging.getLogger("travelluck.generate")

# --------- Google Drive ----------
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

def get_drive_service():
    log.info("Init Google Drive service via %s", GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)

async def upload_image_to_drive(file_data: bytes, file_name_prefix: str = "travelluck_post_image", folder_id: str | None = None) -> str | None:
    try:
        log.info("Uploading image to Drive (size: %s bytes)", len(file_data))
        service = get_drive_service()
        file_name = f"{file_name_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jpeg"
        metadata = {"name": file_name}

        try:
            from .config import GOOGLE_DRIVE_FOLDER_ID  # если есть — используем
            if GOOGLE_DRIVE_FOLDER_ID:
                metadata["parents"] = [GOOGLE_DRIVE_FOLDER_ID]
        except Exception:
            if folder_id:
                metadata["parents"] = [folder_id]

        media = MediaIoBaseUpload(io.BytesIO(file_data), mimetype="image/jpeg", resumable=True)
        file = service.files().create(body=metadata, media_body=media, fields="id").execute()
        file_id = file.get("id")
        log.info("Drive file created: id=%s", file_id)
        service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
        url = f"https://drive.google.com/uc?id={file_id}"
        log.info("Drive file published: %s", url)
        return url
    except HttpError as e:
        # Явно логируем текст — чтобы увидеть storageQuotaExceeded
        log.error("[upload_image_to_drive] Google API error: %s", e)
        return None
    except Exception as e:
        log.error("[upload_image_to_drive] Unknown error: %s", e)
        return None

# --------- OpenAI text generation ----------
def _openai_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)

def _text_system_prompt() -> str:
    return (
        "Ты профессиональный редактор тревел-канала. Тебе сообщают — тему путешествия.\n"
        "Сгенерируй:\n"
        "1) Короткий вирусный привлекательный заголовок (до 80 символов).\n"
        "2) Основной текст поста строго до 800 символов, дружелюбный, с Markdown.\n"
        "3) Детализированный промпт для максимальго фотореалистичного изображения, иллюстрирующего данную тему (EN, высокое разрешение, без людей и текста).\n"
        "Верни JSON с ключами: title, text, image_prompt."
    )

async def generate_text(topic: str) -> dict:
    client = _openai_client()
    log.info("OpenAI text generation for topic='%s'", topic)

    def _call():
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.8,
            messages=[
                {"role": "system", "content": _text_system_prompt()},
                {"role": "user", "content": f"Тема: {topic}"}
            ],
            response_format={"type": "json_object"}
        )
        return resp.choices[0].message.content

    raw = await asyncio.to_thread(_call)

    import json
    try:
        data = json.loads(raw)
        log.info("OpenAI text parsed successfully")
        return {
            "title": (data.get("title") or "").strip(),
            "text": (data.get("text") or "").strip(),
            "image_prompt": (data.get("image_prompt") or "").strip()
        }
    except Exception as e:
        log.error("[generate_text] JSON parse error: %s; raw=%s", e, raw[:300])
        return {
            "title": f"{topic}: вдохновляющий маршрут",
            "text": (
                f"Путешествие на тему «{topic}». Составьте свой маршрут, "
                f"запланируйте ключевые точки и оставьте время для импровизаций."
            ),
            "image_prompt": f"{topic}, scenic landscape, photorealistic, golden hour, no people, 1:1"
        }

# --------- Stability image generation ----------
async def generate_image(image_prompt: str) -> bytes | None:
    if not STABILITY_API_KEY:
        log.warning("[generate_image] STABILITY_API_KEY отсутствует — пропускаю генерацию.")
        return None

    api_host = "https://api.stability.ai"
    url = f"{api_host}/v2beta/stable-image/generate/ultra"
    headers = {"authorization": f"Bearer {STABILITY_API_KEY}", "accept": "image/*"}
    files = {
        "prompt": (None, image_prompt),
        "mode": (None, "text-to-image"),
        "output_format": (None, "jpeg"),
    }

    try:
        log.info("Requesting Stability Ultra image for prompt: %s", image_prompt)
        resp = await asyncio.to_thread(
            requests.post, url, headers=headers, files=files, timeout=120
        )
        if resp.status_code != 200:
            log.error("[generate_image] HTTP %s: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        log.info("Stability image generated: %d bytes", len(resp.content))
        return resp.content
    except Exception as e:
        log.error("[generate_image] Ошибка Stability: %s", e)
        return None

# --------- Публичная функция ---------
async def generate_post(topic: str) -> dict:
    """
    Возвращает словарь:
    {
      "title": str, "text": str, "image_prompt": str,
      "image_url": str,          # URL на картинку в Drive ИЛИ 'tg:<file_id>' если загрузили в Telegram
      "image_bytes": bytes|None  # байты картинки, если Drive не принял (например, storageQuotaExceeded)
    }
    """
    log.info("generate_post started for topic='%s'", topic)

    fallback = {
        "title": f"{topic}: заметки путешественника",
        "text": f"Пост о теме «{topic}».",
        "image_prompt": f"{topic}, photorealistic, no people, 1:1",
        "image_url": "",
        "image_bytes": None,
    }

    try:
        text_part = await generate_text(topic)
        img_bytes = await generate_image(text_part.get("image_prompt", ""))

        image_url = ""
        image_bytes_out = None

        if img_bytes:
            log.info("Uploading generated image to Drive...")
            uploaded = await upload_image_to_drive(img_bytes)
            if uploaded:
                image_url = uploaded
            else:
                # Важный момент: если не смогли в Drive (например 403 storageQuotaExceeded),
                # вернём байты, чтобы бот мог отправить фото в чат и сохранить tg:file_id
                image_bytes_out = img_bytes
                log.warning("Drive upload failed — will fallback to Telegram file_id in bot.")
        else:
            log.warning("No image bytes received — skipping Drive upload.")

        result = {
            "title": text_part.get("title") or fallback["title"],
            "text": text_part.get("text") or fallback["text"],
            "image_prompt": text_part.get("image_prompt") or fallback["image_prompt"],
            "image_url": image_url,
            "image_bytes": image_bytes_out,
        }
        log.info("generate_post done: image_url='%s', has_bytes=%s", result["image_url"], bool(result["image_bytes"]))
        return result
    except Exception as e:
        log.error("[generate_post] Критическая ошибка: %s", e)
        return fallback
