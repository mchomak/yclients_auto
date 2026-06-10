"""
Юнит-тесты для utils.py (этап plan-02-correctness-core).

Запуск:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import first_line, normalize_phone, parse_recipient_count, phones_match


class TestNormalizePhone(unittest.TestCase):
    def test_formatted_phone(self):
        self.assertEqual(normalize_phone("+7 (999) 123-45-67"), "79991234567")

    def test_empty_string(self):
        self.assertEqual(normalize_phone(""), "")

    def test_none(self):
        self.assertEqual(normalize_phone(None), "")


class TestPhonesMatch(unittest.TestCase):
    def test_formatted_vs_8(self):
        self.assertTrue(phones_match("+7 (999) 123-45-67", "89991234567"))

    def test_7_vs_formatted(self):
        self.assertTrue(phones_match("79991234567", "+7 999 123-45-67"))

    def test_different_numbers(self):
        self.assertFalse(phones_match("89991234567", "+7 999 765-43-21"))

    def test_short_query(self):
        self.assertFalse(phones_match("123", "+7 999 123-45-67"))

    def test_empty_query(self):
        self.assertFalse(phones_match("", "что угодно"))


class TestFirstLine(unittest.TestCase):
    def test_empty_string_no_indexerror(self):
        self.assertEqual(first_line(""), "")

    def test_two_lines(self):
        self.assertEqual(first_line("a\nb"), "a")

    def test_truncated_to_200(self):
        long_line = "x" * 300
        result = first_line(long_line)
        self.assertEqual(len(result), 200)
        self.assertEqual(result, "x" * 200)


class TestParseRecipientCount(unittest.TestCase):
    def test_zero_recipients(self):
        self.assertEqual(
            parse_recipient_count("Клиентов из списка, установивших мобильное приложение -  0"), 0
        )

    def test_will_be_sent_to_n(self):
        self.assertEqual(parse_recipient_count("Будет отправлено 5 клиентам"), 5)

    def test_no_digits(self):
        self.assertIsNone(parse_recipient_count("без цифр"))

    def test_empty_string(self):
        self.assertIsNone(parse_recipient_count(""))


if __name__ == "__main__":
    unittest.main()
