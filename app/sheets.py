import gspread
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
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=100, cols=len(HEADERS))
        ws.append_row(HEADERS)
    return ws

def _ensure_header(ws):
    current = ws.row_values(1)
    if current != HEADERS:
        if not current:
            ws.update("A1", [HEADERS])
        else:
            ws.update(f"A1:{chr(64+len(HEADERS))}1", [HEADERS])

def _pack_post_cell(title: str, text: str) -> str:
    title = (title or "").strip()
    text = (text or "").strip()
    if title:
        return f"**{title}**\n\n{text}"
    return text

def _parse_post_cell(cell: str) -> tuple[str, str]:
    """Парсит '**Title**\\n\\nText' или просто 'Text'."""
    cell = (cell or "").strip()
    if not cell:
        return "", ""
    if cell.startswith("**"):
        end = cell.find("**", 2)
        if end != -1:
            title = cell[2:end].strip()
            rest = cell[end+2:]
            if rest.startswith("\n\n"):
                rest = rest[2:]
            return title, (rest or "").strip()
    return "", cell

# -------------------- Добавление поста --------------------
def append_post(row_dict: dict) -> dict:
    gc = _client()
    ws = _open_sheet(gc)
    _ensure_header(ws)

    post_cell = _pack_post_cell(row_dict.get("title",""), row_dict.get("text",""))
    row = [
        row_dict.get("id",""),
        row_dict.get("status","draft"),
        post_cell,
        row_dict.get("image_prompt",""),
        row_dict.get("image_url",""),
        row_dict.get("created_at",""),
        row_dict.get("scheduled_at",""),
        row_dict.get("posted_at",""),
        row_dict.get("chat_id",""),
        row_dict.get("message_id",""),
        row_dict.get("error",""),
    ]
    ws.append_row(row, value_input_option="RAW")
    return {
        "id": row[0],
        "status": row[1],
        "title": row_dict.get("title",""),
        "text": row_dict.get("text",""),
        "image_prompt": row[3],
        "image_url": row[4],
        "created_at": row[5]
    }

# -------------------- Получение по id --------------------
def get_post_by_id(post_id: str) -> dict | None:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    for row in all_values[1:]:
        if not row or len(row) < 3:
            continue
        if row[0] == post_id:
            title, text = _parse_post_cell(row[2])
            image_prompt = row[3] if len(row) > 3 else ""
            image_url = row[4] if len(row) > 4 else ""
            return {
                "id": row[0],
                "status": row[1],
                "title": title,
                "text": text,
                "image_prompt": image_prompt,
                "image_url": image_url,
                "created_at": row[5] if len(row) > 5 else ""
            }
    return None

# -------------------- Последние N --------------------
def list_recent_posts(limit: int = 10) -> list[dict]:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    body = all_values[1:]
    body = body[-limit:] if limit and len(body) > limit else body
    out = []
    for row in reversed(body):
        if not row or len(row) < 3:
            continue
        title, text = _parse_post_cell(row[2])
        out.append({
            "id": row[0],
            "status": row[1],
            "title": title,
            "text": text,
            "image_prompt": row[3] if len(row) > 3 else "",
            "image_url": row[4] if len(row) > 4 else "",
            "created_at": row[5] if len(row) > 5 else ""
        })
    return out

# -------------------- Обновление (для /edit) --------------------
def update_post_fields(post_id: str, title: str | None = None, text: str | None = None, image_prompt: str | None = None) -> bool:
    """
    Обновляет title/text/image_prompt для строки с заданным id.
    Если параметр = None — поле не меняется.
    """
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()

    # найдём строку и текущее состояние
    target_idx = None  # индекс строки в таблице (1-based)
    cur_title = ""
    cur_text = ""
    cur_image_prompt = ""

    for idx, row in enumerate(all_values[1:], start=2):  # с учётом заголовка
        if not row:
            continue
        if row[0] == post_id:
            target_idx = idx
            pt, tx = _parse_post_cell(row[2] if len(row) > 2 else "")
            cur_title, cur_text = pt, tx
            cur_image_prompt = row[3] if len(row) > 3 else ""
            break

    if not target_idx:
        return False

    new_title = cur_title if title is None else title
    new_text = cur_text if text is None else text
    new_ip = cur_image_prompt if image_prompt is None else image_prompt

    # Обновим объединённую ячейку поста и image_prompt
    ws.update_cell(target_idx, 3, _pack_post_cell(new_title, new_text))  # col 3 = "post"
    ws.update_cell(target_idx, 4, new_ip)  # col 4 = "image_prompt"
    return True

# -------------------- Удаление --------------------
def delete_post(post_id: str) -> bool:
    gc = _client()
    ws = _open_sheet(gc)
    all_values = ws.get_all_values()
    for idx, row in enumerate(all_values[1:], start=2):
        if not row:
            continue
        if row[0] == post_id:
            ws.delete_rows(idx)
            return True
    return False
