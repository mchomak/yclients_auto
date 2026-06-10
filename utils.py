"""
Чистые хелперы без сторонних зависимостей (только stdlib `re`).

Используются в `simple_run.py`/`run.py` и покрыты юнит-тестами
(`tests/test_utils.py`), см. Obsidian: plan-02-correctness-core.
"""

import re


def normalize_phone(raw: str) -> str:
    """Только цифры — в таком виде телефон вводится в поле «Сотовый»."""
    return re.sub(r"\D", "", raw or "")


def phones_match(query: str, candidate: str) -> bool:
    """Устойчивое сравнение телефонов: по последним 10 цифрам (терпимо к коду
    страны 7/8 и форматированию). Если в query меньше 10 цифр — False (защита
    от ложных совпадений по короткому вводу)."""
    q = normalize_phone(query)
    c = normalize_phone(candidate)
    if len(q) < 10:
        return False
    return q[-10:] == c[-10:]


def first_line(err) -> str:
    """Первая строка сообщения об ошибке, безопасно (никогда не IndexError),
    усечённая до 200 символов."""
    return (str(err).splitlines() or [""])[0][:200]


def parse_recipient_count(text: str) -> int | None:
    """Вернуть последнее целое число в строке (счётчик получателей push),
    или None, если цифр нет."""
    matches = re.findall(r"\d+", text or "")
    if not matches:
        return None
    return int(matches[-1])


class NoPushChannel(Exception):
    """Получателей push — 0 (слать некому)."""
