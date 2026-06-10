"""
Простой прогон браузерной автоматизации YClients (этап 1, см. Obsidian:
plan-01-mvp-prostoy-progon).

Делает ОДИН сценарий из docx-инструкции на тестовом аккаунте:
    логин → поиск клиента по телефону → создать, если нет →
    проставить 2 согласия → отправить PUSH в YPLACES.

БЕЗ Google Sheets / БД / Docker. Тестовые данные берутся из .env.
Браузер — persistent Chromium-профиль (browser_profile/): один раз логинишься
вручную (включая 2FA), дальше сессия живёт в профиле.

Запуск:
    pip install -r requirements.txt
    playwright install chromium
    cp .env.example .env   # и заполнить
    python simple_run.py
"""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from utils import normalize_phone, phones_match, parse_recipient_count, NoPushChannel

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / "browser_profile"

load_dotenv(ROOT / ".env")
BASE_URL = os.getenv("BASE_URL", "https://yclients.com").rstrip("/")
SALON_ID = os.getenv("SALON_ID", "1971030")
TEST_NAME = os.getenv("TEST_NAME", "Клиент Тест")
TEST_PHONE = os.getenv("TEST_PHONE", "")
TEST_PUSH_TEXT = os.getenv("TEST_PUSH_TEXT", "Тест системы пушей")
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

BASE_PAGE_URL = f"{BASE_URL}/clients/{SALON_ID}/base/"

# Тексты чекбоксов согласия (из i18n YClients).
CONSENT_PERSONAL = "Клиент явно дал согласие на обработку персональных данных"
CONSENT_ADVERT = "Клиент явно дал согласие на отправку информационно-рекламной рассылки"


def click_label_or_button(scope, text: str, exact: bool = True, timeout: int = 15000):
    """Кликнуть по кнопке/тексту: сперва role=button, потом любой видимый текст.
    Нужно, потому что часть кнопок YClients — не <button>, а styled-элементы."""
    btn = scope.get_by_role("button", name=text)
    if btn.count() > 0:
        btn.first.click(timeout=timeout)
        return
    scope.get_by_text(text, exact=exact).first.click(timeout=timeout)


def goto_with_retry(page, url: str, attempts: int = 3, timeout: int = 60000,
                    wait_until: str = "domcontentloaded"):
    """Переход по URL с ретраями — YClients тяжёлый и иногда не успевает за дефолтный таймаут."""
    last_err = None
    for i in range(1, attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except PWTimeout as e:
            last_err = e
            logger.warning("Переход не успел ({}/{}): {} — повтор...", i, attempts, url)
            page.wait_for_timeout(2000)
    if last_err is None:
        raise RuntimeError(f"goto_with_retry: ни одной попытки перехода (attempts={attempts}): {url}")
    raise last_err


def is_on_client_base(page) -> bool:
    """Мы на странице клиентской базы (залогинены)?"""
    try:
        page.locator('[data-locator="page_title"]', has_text="Клиентская база").wait_for(
            state="visible", timeout=8000
        )
        return True
    except PWTimeout:
        return False


def ensure_logged_in(page):
    """Открыть клиентскую базу. Если не залогинены — попросить войти вручную
    (включая 2FA) и дождаться, пока откроется база. Сессия сохранится в профиле."""
    logger.info("Открываю клиентскую базу: {}", BASE_PAGE_URL)
    goto_with_retry(page, BASE_PAGE_URL)
    if is_on_client_base(page):
        logger.success("Сессия активна — уже залогинены.")
        return
    logger.warning(
        "Похоже, требуется вход. В открытом окне браузера залогинься в YClients "
        "(логин/пароль + 2FA, если есть), дождись клиентской базы."
    )
    input(">>> После входа нажми Enter здесь, чтобы продолжить... ")
    goto_with_retry(page, BASE_PAGE_URL)
    if not is_on_client_base(page):
        raise RuntimeError("Не вижу клиентскую базу после ручного входа. Прерываю.")
    logger.success("Залогинены, клиентская база открыта.")


def search(page, query: str):
    """Ввести запрос в строку поиска и нажать «Найти»."""
    logger.info("Поиск: {}", query)
    box = page.locator('[data-locator="search_input"]')
    if box.count() == 0:
        box = page.get_by_placeholder(re.compile("Поиск", re.I))
    box.first.fill(query)
    try:
        click_label_or_button(page, "Найти", timeout=8000)
    except PWTimeout:
        box.first.press("Enter")  # запасной вариант
    page.wait_for_timeout(1500)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout:
        pass


def _matched_row(page, phone: str):
    """Locator строки результата, чей телефон совпал по phones_match (иначе None).

    Строки клиентской базы — это `.v-row[data-locator^="client_tr_<id>"]`, ячейка
    телефона внутри — `[data-locator="phone"]` (текст вида «+7 926 954-91-97»).
    Сравниваем по phones_match, затем поднимаемся к строке-предку client_tr_.
    (НЕ использовать `tr` — на странице есть скрытые календари-датапикеры.)"""
    cells = page.locator('[data-locator="phone"]')
    for i in range(cells.count()):
        cell = cells.nth(i)
        if phones_match(phone, cell.inner_text()):
            row = cell.locator('xpath=ancestor::*[starts-with(@data-locator,"client_tr_")][1]')
            if row.count() > 0:
                return row
    return None


def client_exists(page, phone: str) -> bool:
    """Есть ли в результатах поиска клиент с этим телефоном (сравнение через
    phones_match по ячейкам `[data-locator="phone"]` — терпимо к форматированию)."""
    found = _matched_row(page, phone) is not None
    logger.info("Клиент с телефоном {} {}", phone, "найден" if found else "не найден")
    return found


def create_client(page, name: str, phone: str):
    """Открыть модалку «Добавить клиента», заполнить Имя+Сотовый, Сохранить."""
    logger.info("Создаю клиента: {} / {}", name, phone)
    click_label_or_button(page, "Добавить клиента")
    modal = page.locator('[data-locator="block_client_add"]')
    modal.wait_for(state="visible", timeout=15000)
    modal.locator('[data-locator="input_client_name"]').fill(name)
    modal.locator('[data-locator="input_client_phone"]').fill(phone)
    # Кнопка «Сохранить» в этой модалке — button.add.
    modal.locator("button.add").click()
    modal.wait_for(state="hidden", timeout=15000)
    logger.success("Клиент создан.")


def open_card(page, name: str, phone: str):
    """Открыть карточку клиента кликом по имени в результатах поиска."""
    logger.info("Открываю карточку клиента.")
    search(page, phone)
    row = _matched_row(page, phone)
    if row is None:
        raise RuntimeError(f"Не нашёл строку клиента по телефону {phone} для открытия карточки.")
    # Имя-ссылка в строке: <a data-locator="...edit_client_link...">.
    row.locator('a[data-locator*="edit_client_link"]').first.click()
    page.locator('[data-locator="block_client_edit"]').wait_for(
        state="visible", timeout=15000
    )


def set_consents_and_save(page):
    """Отметить 2 чекбокса согласий и сохранить карточку — best-effort.
    Если блока согласий в карточке нет (не на всех аккаунтах он есть) — пропускаем шаг."""
    logger.info("Проставляю согласия (если есть в карточке).")
    card = page.locator('[data-locator="block_client_edit"]')
    changed = False
    for text in (CONSENT_PERSONAL, CONSENT_ADVERT):
        label = page.get_by_text(text, exact=False).first
        try:
            label.wait_for(state="visible", timeout=3000)
            label.scroll_into_view_if_needed()
            label.click()
            changed = True
            logger.info("  отмечено: {}", text)
        except PWTimeout:
            logger.warning("  чекбокс согласия не найден — пропускаю: {}", text)
    if not changed:
        logger.warning("Согласий в карточке нет — шаг пропущен, иду дальше.")
        return
    try:
        card.locator("button.card_save").click(timeout=5000)
        logger.success("Карточка сохранена с согласиями.")
    except PWTimeout:
        logger.warning("Кнопка «Сохранить» в карточке не найдена — пропускаю сохранение.")


def send_push(page, phone: str, text: str):
    """Поиск → чекбокс строки нужного клиента → Действия → «Отправить PUSH в YPLACES»
    → текст → проверка получателей → Отправить."""
    logger.info("Готовлю отправку push.")
    goto_with_retry(page, BASE_PAGE_URL)
    is_on_client_base(page)
    search(page, phone)

    # Найти ровно одну ячейку телефона, совпавшую по phones_match (защита от
    # отправки не тому клиенту), затем отметить чекбокс её строки.
    cells = page.locator('[data-locator="phone"]')
    matched = [i for i in range(cells.count()) if phones_match(phone, cells.nth(i).inner_text())]
    if len(matched) != 1:
        raise RuntimeError(
            f"Не удалось однозначно определить строку клиента по телефону {phone}: "
            f"совпадений найдено {len(matched)} (ожидалась ровно 1)."
        )
    row = cells.nth(matched[0]).locator(
        'xpath=ancestor::*[starts-with(@data-locator,"client_tr_")][1]'
    )
    # Нативный чекбокс скрыт (Quasar) — кликаем по styled-обёртке .q-checkbox.
    row.locator(".q-checkbox").first.click()

    click_label_or_button(page, "Действия")
    page.get_by_text("Отправить PUSH в YPLACES", exact=True).first.click()

    area = page.locator("textarea.clients-base-table-yplaces-push-form__message")
    area.wait_for(state="visible", timeout=15000)
    area.fill(text)

    # Проверить счётчик получателей до отправки.
    info = page.locator(".clients-base-table-yplaces-push-form__title-info")
    n = None
    try:
        n = parse_recipient_count(info.first.inner_text())
    except Exception:
        logger.warning("Не удалось прочитать счётчик получателей.")
    if n is None:
        logger.warning("Не удалось распарсить число получателей из счётчика.")
    elif n == 0:
        raise NoPushChannel(f"Получателей push: 0 (телефон {phone}) — слать некому.")

    if DRY_RUN:
        logger.warning(
            "DRY_RUN=true — текст введён, но НЕ нажимаю «Отправить». "
            "Поставь DRY_RUN=false в .env для реальной отправки."
        )
        return

    # Оранжевая кнопка «Отправить» в футере формы push (рядом серая «Отмена»).
    form = page.locator(".v-form").filter(
        has=page.locator("textarea.clients-base-table-yplaces-push-form__message")
    )
    send_btn = form.get_by_role("button", name="Отправить")
    if send_btn.count() == 0:
        send_btn = form.locator("button.y-button__type-orange")
    send_btn.first.click()

    # Дождаться сигнала завершения отправки: появление .loading-message и/или
    # скрытие/детач формы. По таймауту — ошибка (не логировать успех вслепую).
    try:
        page.locator(".loading-message").wait_for(state="visible", timeout=15000)
    except PWTimeout:
        try:
            form.wait_for(state="hidden", timeout=15000)
        except PWTimeout:
            raise RuntimeError(
                "Не дождался подтверждения отправки push (нет .loading-message и форма не скрылась)."
            )

    logger.success("Push отправлен.")


def launch_context(p):
    """Persistent Chromium-контекст (сессия YClients живёт в browser_profile/)."""
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=HEADLESS,
        viewport={"width": 1440, "height": 900},
        locale="ru-RU",
    )
    context.set_default_timeout(15000)
    return context


def process_one(page, name: str, phone: str, text: str):
    """Один лид: поиск → создать при отсутствии → согласия → push.
    Используется и в одиночном прогоне, и в прогоне из Google-таблицы (run.py)."""
    goto_with_retry(page, BASE_PAGE_URL)
    is_on_client_base(page)
    search(page, phone)
    if not client_exists(page, phone):
        create_client(page, name, phone)
        open_card(page, name, phone)
        set_consents_and_save(page)
    else:
        logger.info("Клиент уже есть — пропускаю создание (защита от дублей).")
    send_push(page, phone, text)


def main():
    if not TEST_PHONE:
        logger.error("Не задан TEST_PHONE в .env")
        sys.exit(1)

    phone = normalize_phone(TEST_PHONE)
    logger.info("Старт. Телефон={}, имя={}, dry_run={}", phone, TEST_NAME, DRY_RUN)

    with sync_playwright() as p:
        context = launch_context(p)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_logged_in(page)
            process_one(page, TEST_NAME, phone, TEST_PUSH_TEXT)
            logger.success("Прогон завершён успешно.")
        except Exception as e:
            shot = ROOT / "error_run.png"
            try:
                page.screenshot(path=str(shot))
                logger.error("Ошибка: {}. Скриншот: {}", e, shot)
            except Exception:
                logger.error("Ошибка: {} (скриншот сделать не удалось)", e)
            input(">>> Enter — закрыть браузер... ")
            context.close()
            sys.exit(1)

        input(">>> Готово. Enter — закрыть браузер... ")
        context.close()


if __name__ == "__main__":
    main()
