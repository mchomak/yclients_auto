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
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from utils import normalize_phone, phones_match, first_line

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / "browser_profile"
# Каталог логов и диагностических артефактов. Монтируется томом в docker-compose
# (./logs:/app/logs), поэтому app.log и дампы ошибок видны на хосте и переживают
# рестарт контейнера. Путь переопределяется через LOG_DIR.
LOG_DIR = Path(os.getenv("LOG_DIR", str(ROOT / "logs")))
# Скриншот+HTML страницы на каждой ошибке/ретрае — для пост-мортем разбора вёрстки.
ARTIFACTS_DIR = LOG_DIR / "artifacts"

load_dotenv(ROOT / ".env")
BASE_URL = os.getenv("BASE_URL", "https://yclients.com").rstrip("/")
SALON_ID = os.getenv("SALON_ID", "1971030")
TEST_NAME = os.getenv("TEST_NAME", "Клиент Тест1111")
TEST_PHONE = os.getenv("TEST_PHONE", "")
TEST_PUSH_TEXT = os.getenv("TEST_PUSH_TEXT", "Тест системы пушей")
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
# Креды для автоматического входа (без них — только ручной логин в видимом режиме).
YCLIENTS_LOGIN = os.getenv("YCLIENTS_LOGIN", "").strip()
YCLIENTS_PASSWORD = os.getenv("YCLIENTS_PASSWORD", "")
YCLIENTS_REQUIRED_ACCOUNT_TEXT = os.getenv("YCLIENTS_REQUIRED_ACCOUNT_TEXT", "").strip()
YCLIENTS_FORBIDDEN_ACCOUNT_TEXT = os.getenv("YCLIENTS_FORBIDDEN_ACCOUNT_TEXT", "").strip()
# Замедление для наблюдения за видимым прогоном (0 = выкл, на сервере не трогаем).
# SLOW_MO_MS — задержка Playwright перед каждым действием; STEP_PAUSE_MS — явные
# паузы на ключевых шагах, чтобы успеть рассмотреть экран.
SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "0"))
STEP_PAUSE_MS = int(os.getenv("STEP_PAUSE_MS", "0"))
# Уровень логов в файл/консоль и ретраи. NAV_RETRIES — попытки перехода по URL;
# STEP_RETRIES — попытки шага с перезагрузкой страницы (поиск/создание/форма push).
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
NAV_RETRIES = int(os.getenv("NAV_RETRIES", "3"))
STEP_RETRIES = int(os.getenv("STEP_RETRIES", "2"))
# Отладка согласий: когда чекбоксы согласий не найдены, сохранить скриншот+HTML
# карточки (по умолчанию выкл — иначе дамп писался бы на каждую строку и забивал
# диск). Включай временно, чтобы вытащить реальный селектор согласий.
DUMP_CONSENT_CARD = os.getenv("DUMP_CONSENT_CARD", "false").lower() in ("1", "true", "yes")

BASE_PAGE_URL = f"{BASE_URL}/clients/{SALON_ID}/base/"

# Чекбоксы согласия в карточке клиента (Bootstrap-модалка block_client_edit).
# Это настоящие <input type="checkbox"> со стабильными data-locator — кликаем по
# ним напрямую, а не по тексту label (текст с хвостовыми пробелами не находился).
CONSENT_PERSONAL = "Клиент явно дал согласие на обработку персональных данных"
CONSENT_ADVERT = "Клиент явно дал согласие на отправку информационно-рекламной рассылки"
CONSENTS = (
    (CONSENT_PERSONAL, "is_personal_data_processing_allowed_checkbox"),
    (CONSENT_ADVERT, "is_newsletter_allowed_checkbox"),
)


def click_label_or_button(scope, text: str, exact: bool = True, timeout: int = 15000):
    """Кликнуть по кнопке/тексту: сперва role=button, потом любой видимый текст.
    Нужно, потому что часть кнопок YClients — не <button>, а styled-элементы."""
    btn = scope.get_by_role("button", name=text)
    if btn.count() > 0:
        btn.first.click(timeout=timeout)
        return
    scope.get_by_text(text, exact=exact).first.click(timeout=timeout)


def setup_logging():
    """Настроить loguru: цветная консоль + ротируемый файл logs/app.log.
    Вызывается из main() обоих entrypoint'ов (не при импорте — чтобы тесты,
    импортирующие модуль, не создавали файлов)."""
    logger.remove()
    logger.add(sys.stderr, level=LOG_LEVEL,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(
            LOG_DIR / "app.log",
            level="DEBUG",                 # в файл пишем подробнее, чем в консоль
            rotation="10 MB",
            retention="14 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,                  # потокобезопасно (фоновый writer)
            backtrace=True,
            diagnose=False,                # не печатать значения переменных (секреты!)
        )
        logger.info("Логи пишутся в {}", LOG_DIR / "app.log")
    except Exception as e:
        logger.warning("Файловое логирование не настроено ({}): {}", LOG_DIR, e)


def dump_debug(page, tag: str):
    """Сохранить скриншот + HTML страницы для пост-мортем разбора (вёрстка/модалки).
    Файлы — с таймштампом в logs/artifacts/ (смонтированный том → видны на хосте,
    история не перетирается). best-effort, никогда не роняет основной поток."""
    if page is None:
        return
    safe = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "_", tag)[:60]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = ARTIFACTS_DIR / f"{stamp}_{safe}"
    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=f"{base}.png", full_page=True)
        except Exception as e:
            logger.debug("Скриншот не сохранён ({}): {}", tag, first_line(e))
        try:
            Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:
            logger.debug("HTML не сохранён ({}): {}", tag, first_line(e))
        logger.info("Артефакты ошибки сохранены: {}.png / .html  (состояние: {})",
                    base, describe_page(page))
    except Exception as e:
        logger.debug("Не удалось сохранить артефакты ({}): {}", tag, first_line(e))


def _watch_pause(page):
    """Пауза для наблюдения (только если STEP_PAUSE_MS>0) — даёт рассмотреть экран."""
    if STEP_PAUSE_MS > 0:
        page.wait_for_timeout(STEP_PAUSE_MS)


def describe_page(page) -> str:
    """Короткое состояние страницы для headless-диагностики; best-effort."""
    url = getattr(page, "url", "<unknown>")
    try:
        title = page.title()
    except Exception as e:
        title = f"<title unavailable: {type(e).__name__}>"
    return f"url={url!r}, title={title!r}"


def _stop_and_reset_page(page):
    try:
        page.evaluate("window.stop()")
    except Exception as e:
        logger.debug("Не удалось остановить загрузку страницы: {}", e)
    try:
        page.goto("about:blank", wait_until="commit", timeout=5000)
    except Exception as e:
        logger.debug("Не удалось сбросить страницу на about:blank: {}", e)


def goto_with_retry(page, url: str, attempts: int = NAV_RETRIES, timeout: int = 60000,
                    wait_until: str = "commit"):
    """Переход по URL с ретраями.

    YClients — тяжёлая SPA: готовность дальше проверяем селекторами, а не событием
    DOMContentLoaded. Это также помогает восстановиться, если навигация зависла в
    persistent-профиле Chromium. Ретраим не только таймаут, но и сетевые ошибки
    (net::ERR_*), которые Playwright кидает как обычный Error.
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as e:
            last_err = e
            logger.warning(
                "Переход не успел ({}/{}): {} — {} — текущая страница: {}",
                i, attempts, url, first_line(e), describe_page(page)
            )
            _stop_and_reset_page(page)
            if i < attempts:
                page.wait_for_timeout(2000)
    if last_err is None:
        raise RuntimeError(f"goto_with_retry: ни одной попытки перехода (attempts={attempts}): {url}")
    raise last_err


def with_page_retry(page, action, what: str, attempts: int = STEP_RETRIES,
                    reload_url: str = None):
    """Выполнить action(); при любой ошибке сохранить артефакты, перезагрузить
    страницу и повторить. Для шагов, где элемент мог просто не успеть прогрузиться
    (медленная SPA, моргнувшая модалка).

    ВАЖНО: оборачивать только идемпотентные/безопасные-к-повтору шаги (поиск,
    создание-если-нет, открытие формы ДО необратимой отправки). Шаг с реальной
    кнопкой «Отправить» сюда заворачивать нельзя — иначе риск дубля push.
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return action()
        except Exception as e:
            last_err = e
            logger.warning("Шаг «{}» не удался ({}/{}): {}", what, i, attempts, first_line(e))
            dump_debug(page, f"{what}_fail_{i}")
            if i < attempts:
                logger.info("Перезагружаю страницу и повторяю шаг «{}».", what)
                if reload_url:
                    try:
                        goto_with_retry(page, reload_url)
                    except Exception as ne:
                        logger.warning("Перезагрузка ({}) не удалась: {}", reload_url, first_line(ne))
                else:
                    try:
                        page.reload(wait_until="commit", timeout=60000)
                    except Exception as ne:
                        logger.warning("page.reload не удался: {}", first_line(ne))
                page.wait_for_timeout(1500)
    logger.error("Шаг «{}» не удался после {} попыток.", what, attempts)
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


def _visible_text_exists(page, text: str, timeout: int = 2000) -> bool:
    try:
        page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout)
        return True
    except PWTimeout:
        return False


def is_allowed_account(page) -> bool:
    """Проверить опциональные guard-тексты аккаунта перед обработкой строк."""
    if YCLIENTS_FORBIDDEN_ACCOUNT_TEXT and _visible_text_exists(page, YCLIENTS_FORBIDDEN_ACCOUNT_TEXT):
        logger.error(
            "Открыт запрещённый YClients-профиль/аккаунт: {!r}. "
            "Нужен ручной перелогин persistent-профиля.",
            YCLIENTS_FORBIDDEN_ACCOUNT_TEXT,
        )
        return False
    if YCLIENTS_REQUIRED_ACCOUNT_TEXT and not _visible_text_exists(page, YCLIENTS_REQUIRED_ACCOUNT_TEXT):
        logger.error(
            "Не найден ожидаемый текст YClients-аккаунта: {!r}. Текущее состояние: {}",
            YCLIENTS_REQUIRED_ACCOUNT_TEXT,
            describe_page(page),
        )
        return False
    return True


def login(page) -> bool:
    """Автоматический вход по YCLIENTS_LOGIN/PASSWORD.

    Открытие базы без сессии редиректит на www.yclients.com/signin/<return-url>
    (Vue-форма email+пароль; return-url ведёт обратно на базу после входа). 2FA на
    аккаунте нет. Возвращает True, если после входа оказались на клиентской базе."""
    if not (YCLIENTS_LOGIN and YCLIENTS_PASSWORD):
        logger.error("YCLIENTS_LOGIN/YCLIENTS_PASSWORD не заданы — автовход невозможен.")
        return False

    logger.info("Автовход: {}", YCLIENTS_LOGIN)
    goto_with_retry(page, BASE_PAGE_URL)
    if is_on_client_base(page):
        return True

    # Дождаться формы входа (поле пароля рендерится Vue с задержкой).
    try:
        page.locator('input[type="password"]').first.wait_for(state="visible", timeout=25000)
    except PWTimeout:
        logger.error("Форма входа не отрисовалась (нет поля пароля). Состояние: {}", describe_page(page))
        dump_debug(page, "login_no_form")
        return False

    # reCAPTCHA блокирует автовход — честно об этом сообщаем (нужен ручной вход).
    rc = page.locator('iframe[src*="recaptcha"]')
    if rc.count() and rc.first.is_visible():
        logger.error("На входе показана reCAPTCHA — автовход невозможен, нужен ручной вход.")
        dump_debug(page, "login_captcha")
        return False

    # Поле логина в форме signin — внутри Vue web-components (shadow DOM), причём в
    # light-DOM висит скрытая копия Email (display:none). Локаторы Playwright пробивают
    # shadow DOM, поэтому перебираем ВСЕ <input> и берём первое ВИДИМОЕ не-password
    # (а не .first — первым часто оказывается скрытая копия).
    login_box = None
    inputs = page.locator("input")
    for i in range(inputs.count()):
        cand = inputs.nth(i)
        try:
            itype = (cand.get_attribute("type") or "text").lower()
            if itype in ("password", "hidden", "checkbox", "radio", "submit", "button"):
                continue
            if cand.is_visible():
                login_box = cand
                logger.info("Поле логина: type={}, label={}", itype, cand.get_attribute("label"))
                break
        except Exception:
            continue
    if login_box is None:
        logger.error("Поле логина не найдено на форме входа.")
        dump_debug(page, "login_no_login_field")
        return False

    login_box.fill(YCLIENTS_LOGIN)
    pwd = page.locator('input[type="password"]').first
    pwd.fill(YCLIENTS_PASSWORD)
    # Кнопка «Войти» — не <button>, а styled Vue-элемент; кликаем по тексту, fallback —
    # Enter в поле пароля (форма сабмитится по Enter).
    try:
        page.get_by_text("Войти", exact=True).first.click(timeout=5000)
    except PWTimeout:
        pwd.press("Enter")

    # Дождаться УХОДА со страницы signin — успешная авторизация редиректит на return-url
    # (база). НЕ уходим навигацией сами, иначе прервём асинхронный логин.
    try:
        page.wait_for_url(lambda u: "/signin" not in u, timeout=25000)
    except PWTimeout:
        logger.error(
            "После сабмита остались на signin — вход не прошёл (неверные креды / captcha?). "
            "Состояние: {}", describe_page(page),
        )
        dump_debug(page, "login_stuck_signin")
        return False

    # Авторизовались — открыть базу и проверить.
    goto_with_retry(page, BASE_PAGE_URL)
    if is_on_client_base(page):
        logger.success("Автовход успешен — клиентская база открыта.")
        return True
    logger.error("После автовхода база не открылась. Состояние: {}", describe_page(page))
    dump_debug(page, "login_failed")
    return False


def ensure_logged_in(page) -> bool:
    """Гарантировать открытую клиентскую базу: сперва автовход по кредам, при неудаче
    в видимом режиме — ручной вход. Возвращает True, если база открыта."""
    logger.info("Открываю клиентскую базу: {}", BASE_PAGE_URL)
    goto_with_retry(page, BASE_PAGE_URL)
    if is_on_client_base(page):
        logger.success("Сессия активна — уже залогинены.")
        return True

    if login(page):
        return True

    if HEADLESS:
        logger.error("Автовход не удался (headless) — нужен ручной перелогин persistent-профиля.")
        return False

    logger.warning(
        "Автовход не удался. В открытом окне браузера залогинься в YClients вручную, "
        "дождись клиентской базы."
    )
    input(">>> После входа нажми Enter здесь, чтобы продолжить... ")
    goto_with_retry(page, BASE_PAGE_URL)
    if not is_on_client_base(page):
        raise RuntimeError("Не вижу клиентскую базу после ручного входа. Прерываю.")
    logger.success("Залогинены, клиентская база открыта.")
    return True


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


def _filter_active(page, query: str) -> bool:
    """Активен ли быстрый фильтр по этому запросу — проверяем по URL (там остаётся
    quick_search со значением запроса). Нужно, чтобы не искать повторно, когда список
    уже отфильтрован, но и подстраховаться, если фильтр сбросился."""
    try:
        return query in (page.url or "")
    except Exception:
        return False


def _wait_for_matched_row(page, phone: str, timeout_ms: int = 12000):
    """Дождаться появления строки клиента с этим телефоном в отфильтрованном списке
    (после создания клиента/сохранения карточки список перерисовывается)."""
    for _ in range(max(1, timeout_ms // 500)):
        row = _matched_row(page, phone)
        if row is not None:
            return row
        page.wait_for_timeout(500)
    return None


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
    """Открыть карточку только что созданного клиента БЕЗ повторного поиска: после
    создания список сам обновляется с активным фильтром, и клиент уже в нём — ждём
    появления его строки и кликаем по имени. Повторно ищем только если фильтр сброшен."""
    logger.info("Открываю карточку клиента.")
    if not _filter_active(page, phone):
        logger.info("Фильтр по номеру сброшен — повторяю поиск.")
        search(page, phone)
    row = _wait_for_matched_row(page, phone)
    if row is None:
        raise RuntimeError(f"Не нашёл строку клиента по телефону {phone} для открытия карточки.")
    # Имя-ссылка в строке: <a data-locator="...edit_client_link...">.
    row.locator('a[data-locator*="edit_client_link"]').first.click()
    page.locator('[data-locator="block_client_edit"]').wait_for(
        state="visible", timeout=15000
    )


def set_consents_and_save(page):
    """Отметить 2 чекбокса согласий в карточке клиента и сохранить.
    Чекбоксы — настоящие <input type="checkbox"> в Bootstrap-модалке, но styled и
    могут считаться Playwright «невидимыми», а тело модалки догружается через AJAX.
    Поэтому ждём появления чекбокса в DOM (state=attached) и ставим галку через JS
    (выставляем .checked + шлём input/change), минуя проверки видимости."""
    logger.info("Проставляю согласия в карточке клиента.")
    card = page.locator('[data-locator="block_client_edit"]')
    try:
        logger.debug("DEBUG модалка block_client_edit: count={}, visible={}",
                     card.count(), card.first.is_visible() if card.count() else False)
    except Exception as e:
        logger.debug("DEBUG оценка модалки не удалась: {}", e)

    any_found = False
    for text, locator in CONSENTS:
        box = card.locator(f'[data-locator="{locator}"]')
        try:
            box.wait_for(state="attached", timeout=8000)
        except PWTimeout:
            logger.warning("  чекбокс согласия не найден в DOM — пропускаю: {}", text)
            continue
        any_found = True
        try:
            box.scroll_into_view_if_needed()
            _watch_pause(page)
            if box.is_checked():
                logger.info("  согласие уже стояло: {}", text)
                continue
            # Настоящий клик (не JS-.checked) — галочка появляется визуально и
            # корректно регистрируется формой. Сам input может быть перекрыт стилями —
            # тогда кликаем по обёртке-label.
            try:
                box.check(timeout=4000)
            except PWTimeout:
                box.locator("xpath=ancestor::label[1]").first.click(timeout=4000)
            if box.is_checked():
                logger.success("  согласие проставлено: {}", text)
            else:
                logger.warning("  не удалось отметить согласие: {}", text)
        except Exception as e:
            logger.warning("  ошибка при отметке согласия ({}): {}", text, e)

    if not any_found:
        # Блока согласий по известным селекторам в карточке нет. Обычно НЕ дампим
        # (иначе скриншот+HTML писались бы на каждую строку и забивали диск), но при
        # DUMP_CONSENT_CARD=true снимаем карточку — это нужно, чтобы вытащить реальный
        # селектор согласий и починить шаг.
        logger.warning("Согласий в карточке нет — шаг пропущен, иду дальше.")
        if DUMP_CONSENT_CARD:
            dump_debug(page, "consents_not_found")
        return

    _watch_pause(page)
    # Кнопка «Сохранить» в футере карточки — внизу, нужно проскроллить. Класс
    # card_save есть не везде, поэтому ищем по роли/тексту.
    save = card.get_by_role("button", name="Сохранить", exact=True)
    if save.count() == 0:
        save = card.locator("button.card_save")
    if save.count() == 0:
        save = card.get_by_text("Сохранить", exact=True)
    if save.count() == 0:
        logger.warning("Кнопка «Сохранить» в карточке не найдена — пропускаю сохранение.")
        dump_debug(page, "card_save_not_found")
        return
    try:
        save.first.scroll_into_view_if_needed()
        _watch_pause(page)
        save.first.click(timeout=8000)
    except Exception as e:
        logger.warning("Не удалось нажать «Сохранить»: {}", e)
        dump_debug(page, "card_save_click_failed")
        return
    # Карточка-модалка должна закрыться после сохранения. Если не закрыть её,
    # следующий шаг (клик по чекбоксу строки в базе) падает по таймауту.
    try:
        card.wait_for(state="hidden", timeout=15000)
        logger.success("Карточка сохранена с согласиями.")
    except PWTimeout:
        logger.warning("Карточка не закрылась после сохранения — проверь вручную.")


def send_push(page, phone: str, text: str):
    """Поиск → чекбокс строки нужного клиента → Действия → «Отправить PUSH в YPLACES»
    → текст → проверка получателей → Отправить."""
    logger.info("Готовлю отправку push.")

    def open_push_form():
        # После сохранения карточки (или если клиент уже был) список остаётся
        # отфильтрованным по номеру — повторно не ищем. При ретрае (страница
        # перезагружена) фильтр по URL сброшен → ищем заново.
        if not _filter_active(page, phone):
            logger.info("Фильтр по номеру не активен — открываю базу и ищу.")
            goto_with_retry(page, BASE_PAGE_URL)
            is_on_client_base(page)
            search(page, phone)
        if _wait_for_matched_row(page, phone) is None:
            raise RuntimeError(f"Строка клиента по телефону {phone} не появилась в списке.")

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
        cb = row.locator(".q-checkbox").first
        cb.click()
        # Проверить, что строка реально выделилась — иначе у push будет 0 получателей.
        try:
            logger.debug("q-checkbox после клика: aria-checked={}", cb.get_attribute("aria-checked"))
        except Exception as e:
            logger.debug("Не удалось прочитать состояние q-checkbox: {}", e)

        click_label_or_button(page, "Действия")
        page.get_by_text("Отправить PUSH в YPLACES", exact=True).first.click(timeout=8000)

        area = page.locator("textarea.clients-base-table-yplaces-push-form__message")
        area.wait_for(state="visible", timeout=15000)
        return area

    # Открытие формы безопасно повторять с перезагрузкой — это всё ДО необратимой
    # кнопки «Отправить». Саму отправку (ниже) НЕ ретраим, чтобы не задвоить push.
    area = with_page_retry(page, open_push_form, "форма push", reload_url=BASE_PAGE_URL)
    area.fill(text)

    # Счётчик получателей — только для лога; отправку НЕ блокируем. Бот всегда жмёт
    # «Отправить», даже если получателей 0 (нет приложения) — YClients сам решает,
    # кому доставить. Это поведение по требованию: не обращать внимание на счётчик.
    info = page.locator(".clients-base-table-yplaces-push-form__title-info")
    try:
        logger.info("Счётчик получателей: {!r}", info.first.inner_text())
    except Exception:
        logger.debug("Счётчик получателей не прочитан.")

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
        slow_mo=SLOW_MO_MS,  # задержка перед каждым действием (для наблюдения)
        viewport={"width": 1440, "height": 900},
        locale="ru-RU",
        # В Docker /dev/shm по умолчанию 64MB — тяжёлые SPA (signin, ERP) исчерпывают
        # его и headless Chromium зависает на навигации. Пишем shm в /tmp вместо этого.
        # --no-sandbox нужен для запуска под root в контейнере.
        args=["--disable-dev-shm-usage", "--no-sandbox"],
    )
    context.set_default_timeout(15000)
    return context


def process_one(page, name: str, phone: str, text: str):
    """Один лид: поиск → создать при отсутствии → согласия → push.
    Используется и в одиночном прогоне, и в прогоне из Google-таблицы (run.py)."""

    def prepare():
        # Идемпотентно и безопасно к повтору: всегда сначала ищем, создаём только
        # если не нашли (защита от дублей). Поэтому весь блок можно ретраить с
        # перезагрузкой страницы, если элементы не прогрузились.
        goto_with_retry(page, BASE_PAGE_URL)
        is_on_client_base(page)
        search(page, phone)
        if not client_exists(page, phone):
            create_client(page, name, phone)
            open_card(page, name, phone)
            _watch_pause(page)  # дать рассмотреть открытую карточку до согласий
            set_consents_and_save(page)
        else:
            logger.info("Клиент уже есть — пропускаю создание (защита от дублей).")

    with_page_retry(page, prepare, "поиск/создание клиента")
    _watch_pause(page)
    send_push(page, phone, text)


def main():
    setup_logging()
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
            logger.error("Ошибка прогона: {}", first_line(e))
            dump_debug(page, "simple_run_error")
            input(">>> Enter — закрыть браузер... ")
            context.close()
            sys.exit(1)

        input(">>> Готово. Enter — закрыть браузер... ")
        context.close()


if __name__ == "__main__":
    main()
