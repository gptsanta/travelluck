import gspread
from datetime import datetime, timezone
import json
import re
from .config import GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON

SHEET_NAME = "posts"
HEADERS = [
    "id","status","post","image_prompt","image_url",
    "created_at","scheduled_at","posted_at","chat_id","message_id","error"
]

def _client():
    return gspread.service_account(filename=GOOGLE_SERVICE_ACCOUNT_JSON)

def _open_sheet(gc):
    sh = gc.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADERS))
        ws.update("A1", [HEADERS])
        return ws
    _ensure_headers(ws)
    return ws

def _ensure_headers(ws):
    if ws.col_count < len(HEADERS):
        ws.add_cols(len(HEADERS) - ws.col_count)
    first_row = ws.row_values(1)
    norm = lambda arr: [str(x).strip().lower() for x in arr]
    if not first_row or norm(first_row[:len(HEADERS)]) != norm(HEADERS):
        ws.update("A1", [HEADERS])

# -------------------- Добавление поста --------------------
def append_post(row: dict) -> str:
    gc = _client()
    ws = _open_sheet(gc)
    row_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")

    def safe_str(value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        elif isinstance(value, str):
            v = value.replace('"""', '“”').replace('"', '“')
            v = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', v)
            v = re.sub(r'(?<!\*)\*(?!\*)', '', v)
            return v
        return str(value)

    title = (row.get("title") or "").strip()
    text = (row.get("text") or "").strip()
    combined_post = f"**{title}**\n\n{text}" if title else text

    values = [
        row_id,
        safe_str(row.get("status", "draft")),
        safe_str(combined_post),
        safe_str(row.get("image_prompt", "")),
        safe_str(row.get("image_url", "")),  # сохраняем URL изображения
        safe_str(row.get("created_at", datetime.now(timezone.utc).isoformat())),
        safe_str(row.get("scheduled_at", "")),
        safe_str(row.get("posted_at", "")),
        safe_str(row.get("chat_id", "")),
        safe_str(row.get("message_id", "")),
        ""
    ]

    try:
        ws.append_row(values, value_input_option="RAW")
        print(f"Пост с id {row_id} успешно добавлен.")
    except Exception as e:
        error_values = values[:-1] + [str(e)]
        ws.append_row(error_values, value_input_option="RAW")
        print(f"[Ошибка] Не удалось добавить пост с id {row_id}: {e}")

    return row_id

# -------------------- Получение поста по ID --------------------
def get_post_by_id(post_id: str) -> dict | None:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    for row in all_values[1:]:
        if len(row) < 4:
            continue
        if row[0] == post_id:
            post_cell = row[2]  # **Title**\n\nText
            title_match = re.match(r'\*\*(.*?)\*\*', post_cell)
            title = title_match.group(1) if title_match else ""
            text = post_cell.split("\n\n", 1)[1] if "\n\n" in post_cell else ""
            image_prompt = row[3] if len(row) > 3 else ""
            image_url = row[4] if len(row) > 4 else ""
            return {"title": title, "text": text, "image_prompt": image_prompt, "image_url": image_url}
    return None

# -------------------- Обновление поста --------------------
def update_post(post_id: str, new_title: str | None, new_text: str | None, new_prompt: str | None, new_image_url: str | None = None) -> bool:
    gc = _client()
    ws = _open_sheet(gc)

    def safe_str(value):
        if not value:
            return ""
        v = value.replace('"""', '“”').replace('"', '“')
        v = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', v)
        v = re.sub(r'(?<!\*)\*(?!\*)', '', v)
        return v.strip()

    all_values = ws.get_all_values()
    for idx, row in enumerate(all_values[1:], start=2):
        if len(row) == 0:
            continue
        if row[0] == post_id:
            post_cell = row[2]
            title_match = re.match(r'\*\*(.*?)\*\*', post_cell)
            old_title = title_match.group(1) if title_match else ""
            old_text = post_cell.split("\n\n",1)[1] if "\n\n" in post_cell else ""

            title_to_use = new_title if new_title is not None else old_title
            text_to_use = new_text if new_text is not None else old_text
            combined_post = f"**{title_to_use}**\n\n{text_to_use}" if title_to_use else text_to_use
            ws.update_cell(idx, 3, safe_str(combined_post))

            if new_prompt is not None:
                ws.update_cell(idx, 4, safe_str(new_prompt))
            if new_image_url is not None:
                ws.update_cell(idx, 5, safe_str(new_image_url))
            return True
    return False

def update_image_url(post_id: str, new_url: str) -> bool:
    """Обновляет поле image_url для поста с указанным ID"""
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()

    for idx, row in enumerate(all_values[1:], start=2):  # заголовок в строке 1
        if row[0] == post_id:
            ws.update_cell(idx, 5, new_url)  # колонка image_url
            print(f"[update_image_url] Пост {post_id} обновлен с новым image_url")
            return True
    print(f"[update_image_url] Пост {post_id} не найден")
    return False


# -------------------- Получение всех постов --------------------
def get_all_posts() -> list[dict]:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    posts = []
    for row in all_values[1:]:
        if len(row) < 3:
            continue
        posts.append({
            "id": row[0],
            "status": row[1],
            "post": row[2],
            "image_prompt": row[3] if len(row)>3 else "",
            "image_url": row[4] if len(row)>4 else "",
            "created_at": row[5] if len(row)>5 else "",
            "scheduled_at": row[6] if len(row)>6 else "",
            "posted_at": row[7] if len(row)>7 else "",
            "chat_id": row[8] if len(row)>8 else "",
            "message_id": row[9] if len(row)>9 else "",
            "error": row[10] if len(row)>10 else ""
        })
    return posts

# -------------------- Удаление поста --------------------
def delete_post(post_id: str) -> bool:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    for idx, row in enumerate(all_values[1:], start=2):
        if len(row) == 0:
            continue
        if row[0] == post_id:
            ws.delete_rows(idx)
            print(f"Пост с id {post_id} удалён.")
            return True
    print(f"Пост с id {post_id} не найден для удаления.")
    return False
