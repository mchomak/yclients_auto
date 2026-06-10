"""
Чтение/запись Google-таблицы через сервис-аккаунт (gspread).

Колонки (как в демо-таблице заказчика):
    A = №   B = имя   C = телефон   D = текст   E = статус

Проверка подключения:
    python sheets.py        # выведет строки со статусом «новый»

Требуется:
    - service_account.json (ключ сервис-аккаунта) в корне проекта;
    - таблица расшарена на email сервис-аккаунта с правом «Редактор»;
    - SHEET_URL / WORKSHEET / GOOGLE_CREDENTIALS заданы в .env.
"""

import os
import re
from pathlib import Path

import gspread
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

CRED = os.getenv("GOOGLE_CREDENTIALS", "service_account.json")
SHEET_URL = os.getenv("SHEET_URL", "")
WORKSHEET = os.getenv("WORKSHEET", "").strip()

# Какой статус считаем «надо обработать» и что пишем после успеха.
STATUS_NEW = "новый"
STATUS_SENT = "отправлено"
STATUS_ERROR = "ошибка"

# Номера колонок (1-based): B, C, D, E.
COL_NAME, COL_PHONE, COL_TEXT, COL_STATUS = 2, 3, 4, 5
HEADER_ROWS = 1  # первая строка — заголовки


def _spreadsheet_id(url_or_id: str) -> str:
    """Из полной ссылки достаём ID; если передан уже ID — вернём как есть."""
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    return m.group(1) if m else url_or_id


def get_worksheet():
    """Авторизация по сервис-аккаунту и открытие нужного листа."""
    gc = gspread.service_account(filename=str(ROOT / CRED))
    sh = gc.open_by_key(_spreadsheet_id(SHEET_URL))
    return sh.worksheet(WORKSHEET) if WORKSHEET else sh.sheet1


def read_new_rows(ws=None):
    """Список строк со статусом «новый»: [{row, name, phone, text}, ...]."""
    ws = ws or get_worksheet()
    rows = ws.get_all_values()
    out = []
    for i, r in enumerate(rows, start=1):
        if i <= HEADER_ROWS:
            continue
        status = (r[COL_STATUS - 1] if len(r) >= COL_STATUS else "").strip().lower()
        if status == STATUS_NEW:
            out.append({
                "row": i,
                "name": r[COL_NAME - 1] if len(r) >= COL_NAME else "",
                "phone": r[COL_PHONE - 1] if len(r) >= COL_PHONE else "",
                "text": r[COL_TEXT - 1] if len(r) >= COL_TEXT else "",
            })
    return out


def update_status(row: int, status: str, ws=None):
    """Записать статус в колонку E указанной строки."""
    ws = ws or get_worksheet()
    ws.update_cell(row, COL_STATUS, status)


if __name__ == "__main__":
    new_rows = read_new_rows()
    print(f"Подключение к таблице успешно. Новых строк (статус «{STATUS_NEW}»): {len(new_rows)}")
    for r in new_rows:
        print(r)
