from openai import OpenAI
from .config import OPENAI_API_KEY
import asyncio

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты — креативный автор тревел-контента с многолетним опытом. Пиши живо, полезно, без воды.
Форматируй абзацы коротко (2-4 предложения), используй Markdown для выделения ключевых моментов.
В конце обязательно добавляй 1 Call To Action и выдели его.
Для картинки придумай максимально детализированный промт image_prompt (на английском).
Картинка должна быть фотореалистичной, в естественном цвете и освещении, без текста и людей. Добавь максимум деталей, так чтобы при генерации картинки нейросеть ничего не придумывала. Укажи пропорции картинки 1:1.

Верни результат строго в формате:
---
TITLE:
<короткий заголовок>

TEXT:
<текст поста в markdown>

IMAGE_PROMPT:
<только текст промпта для картинки>
---
"""

async def generate_image(prompt_text: str) -> str:
    """Генерация изображения через OpenAI. Возвращает URL или пустую строку."""
    if not prompt_text:
        return ""
    for model_name in ["gpt-image-1", "dall-e-3"]:
        try:
            resp = client.images.generate(
                model=model_name,
                prompt=prompt_text,
                size="1024x1024"
            )
            return resp.data[0].url
        except Exception as e:
            print(f"[generate_image] Ошибка генерации изображения ({model_name}): {e}")
    return ""

def parse_generated_text(raw_text: str) -> dict:
    """Парсим ответ модели по маркерам TITLE / TEXT / IMAGE_PROMPT"""
    title, text, image_prompt = "", "", ""
    try:
        if "TITLE:" in raw_text and "TEXT:" in raw_text:
            parts = raw_text.split("TITLE:")[1]
            title_part, rest = parts.split("TEXT:", 1)
            title = title_part.strip()
            if "IMAGE_PROMPT:" in rest:
                text_part, img_part = rest.split("IMAGE_PROMPT:", 1)
                text = text_part.strip()
                image_prompt = img_part.strip()
            else:
                text = rest.strip()
        else:
            text = raw_text
    except Exception:
        text = raw_text
    return {"title": title, "text": text, "image_prompt": image_prompt}

async def generate_post(topic: str) -> dict:
    """
    Генерация поста через OpenAI.
    Возвращает dict: {"title": str, "text": str, "image_prompt": str, "image_url": str}
    """
    prompt = f"{SYSTEM_PROMPT}\nТема: {topic}"

    fallback = {
        "title": f"Путешествие: {topic}",
        "text": f"*Пост на тему:* {topic}",
        "image_prompt": "",
        "image_url": ""
    }

    try:
        # Оборачиваем синхронный вызов в asyncio.to_thread
        resp = await asyncio.to_thread(
            client.responses.create,
            model="gpt-4o-mini",
            input=prompt
        )

        # Иногда API возвращает структуру с "output" пустой или None
        raw_text = ""
        try:
            raw_text = resp.output[0].content[0].text.strip()
        except Exception:
            print(f"[generate_post] Не удалось извлечь текст из ответа, используем fallback")

        parsed = parse_generated_text(raw_text)

        # Генерируем картинку по промту
        image_url = await generate_image(parsed.get("image_prompt", ""))

        return {
            "title": parsed.get("title") or fallback["title"],
            "text": parsed.get("text") or fallback["text"],
            "image_prompt": parsed.get("image_prompt") or fallback["image_prompt"],
            "image_url": image_url or fallback["image_url"]
        }

    except Exception as e:
        print(f"[generate_post] Ошибка генерации поста по теме '{topic}': {e}")
        return fallback
