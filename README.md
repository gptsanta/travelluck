# Telegram Travel Bot — Variant B (Python + Make + Google Sheets)

This is a minimal working scaffold for your diploma project:
- Python bot generates **text** and an **image prompt**, saves a row in **Google Sheets**.
- **Make** (no-code) runs on a schedule, reads the next `draft` row, optionally generates an image (or uses `image_url` if already filled), and posts to **Telegram**.

## Structure
```
telegram_travel_bot_B/
  app/
    bot.py
    generate.py
    sheets.py
    config.py
    __init__.py
  .env.example
  requirements.txt
  README.md
```

## Quick start
1) Create a Google Cloud **Service Account** and download its JSON key.
   - Enable **Google Sheets API**.
   - Share the target spreadsheet with the service account email.
2) Create a spreadsheet and take its **Spreadsheet ID** (from the URL).
3) Copy `.env.example` to `.env` and fill values.
4) Install deps:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
5) Start the bot:
```bash
python -m app.bot
```
6) In Telegram, send to your bot:
```
/newpost Париж, весна и прогулка по набережным
```
The bot will create a `posts` sheet (if missing) and append a row with the draft text & image prompt.

## Make scenario (outline)
- **Trigger**: Scheduler (e.g., 10:00 and 18:00 Europe/Paris).
- **Step 1**: Google Sheets → Search rows where `status == draft`, sorted by `created_at` asc → take first.
- **Step 2 (optional)**: OpenAI Images → generate image by `image_prompt` → write `image_url` back to the row.
- **Step 3**: Telegram → send `text` + `image_url` to your channel/group.
- **Step 4**: Update `status = posted`, set `posted_at` and store `message_id`.
```

