# test_gsheets.py
import sys
import os
sys.path.append(os.path.dirname(__file__))  # добавляем папку app в sys.path


from sheets import append_post  # используем абсолютный импорт, предполагается, что sheets.py в той же папке

if __name__ == "__main__":
    # Тестовые данные для добавления в Google Sheets
    test_row = {
        "status": "draft",
        "title": "Тестовый пост",
        "text": "Это проверка записи в таблицу",
        "image_prompt": "test image",
        "image_url": "http://example.com/image.jpg",
    }

    # Добавляем пост и получаем id
    row_id = append_post(test_row)
    print("Добавлен пост с id:", row_id)
