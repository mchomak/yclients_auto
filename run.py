"""
Воркер: каждые POLL_INTERVAL секунд опрашивает Google-таблицу, берёт строки со
статусом «новый» и прогоняет их через браузер (YClients), записывая статус обратно.

«Очередь» — это сама таблица: строки «новый» = ожидающие задачи. Обработка строго
по одной, один браузер с persistent-сессией (логинимся один раз при старте).
Статус «отправлено»/«ошибка» в колонке E не даёт обработать строку повторно.

Запуск:
    python run.py        # работает постоянно; Ctrl+C — остановка

Конфиг (.env):
    POLL_INTERVAL  — период опроса в секундах (по умолчанию 60).
    + всё из simple_run/sheets (доступ к таблице, DRY_RUN, и т.д.).
"""

import os
import time

from loguru import logger
from playwright.sync_api import sync_playwright

import sheets
import simple_run as sr
from utils import first_line, NoPushChannel

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))


def handle_row(page, ws, row, processed):
    """Обработать одну строку таблицы и записать результат в колонку статуса."""
    phone = sr.normalize_phone(row["phone"])
    logger.info("Строка {}: {} / {}", row["row"], row["name"], phone)
    try:
        if not phone:
            sheets.update_status(row["row"], f"{sheets.STATUS_ERROR}: пустой телефон", ws)
            return
        if not sr.DRY_RUN:
            # Идемпотентность: если упадём между отправкой и записью статуса,
            # строка останется «в работе» и не уйдёт в повторную обработку
            # (read_new_rows берёт только статус «новый»).
            sheets.update_status(row["row"], sheets.STATUS_IN_PROGRESS, ws)
        sr.process_one(page, row["name"], phone, row["text"])
        if sr.DRY_RUN:
            logger.warning("Строка {}: DRY_RUN=true — статус в таблицу не пишу.", row["row"])
            processed.add(row["row"])  # чтобы в dry-run не гонять её каждую минуту
        else:
            sheets.update_status(row["row"], sheets.STATUS_SENT, ws)
            logger.success("Строка {}: {}", row["row"], sheets.STATUS_SENT)
    except NoPushChannel as e:
        sheets.update_status(row["row"], sheets.STATUS_NO_PUSH_CHANNEL, ws)
        logger.warning("Строка {}: {} — {}", row["row"], sheets.STATUS_NO_PUSH_CHANNEL, first_line(e))
    except Exception as e:
        err = first_line(e)
        sr.dump_debug(page, f"row{row['row']}_error")
        sheets.update_status(row["row"], f"{sheets.STATUS_ERROR}: {err}", ws)
        logger.error("Строка {}: ошибка — {}", row["row"], err)


def main():
    sr.setup_logging()
    ws = sheets.get_worksheet()
    processed = set()  # используется только в DRY_RUN (в боевом режиме защита — статус в таблице)
    logger.info("Воркер запущен. Опрос таблицы каждые {} c. Ctrl+C — стоп.", POLL_INTERVAL)

    with sync_playwright() as p:
        context = sr.launch_context(p)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            # Первичный вход. ensure_logged_in сам делает автовход по кредам
            # (YCLIENTS_LOGIN/PASSWORD) — работает и в headless. В видимом режиме при
            # неудаче автовхода есть ручной fallback. В headless при неудаче просто
            # вернёт False, цикл ниже повторит попытку (graceful, без падения).
            sr.ensure_logged_in(page)
            while True:
                try:
                    sr.goto_with_retry(page, sr.BASE_PAGE_URL)
                    if not sr.is_on_client_base(page):
                        logger.warning(
                            "Сессия не на странице базы — пробую перелогиниться. Состояние: {}",
                            sr.describe_page(page),
                        )
                        if not sr.ensure_logged_in(page):
                            logger.error("Войти не удалось — повторю через {} c.", POLL_INTERVAL)
                            time.sleep(POLL_INTERVAL)
                            continue

                    if not sr.is_allowed_account(page):
                        time.sleep(POLL_INTERVAL)
                        continue

                    rows = [r for r in sheets.read_new_rows(ws) if r["row"] not in processed]
                    if rows:
                        logger.info("Новых строк к обработке: {}", len(rows))
                        for row in rows:
                            handle_row(page, ws, row, processed)
                    else:
                        logger.debug("Новых строк нет.")
                except Exception as e:
                    logger.error("Ошибка при опросе/обработке: {}", first_line(e))
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
