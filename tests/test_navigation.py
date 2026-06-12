import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import simple_run as sr


TARGET_URL = "https://example.test/clients/base/"


class FakePage:
    def __init__(self, fail_target_times=1):
        self.fail_target_times = fail_target_times
        self.target_calls = 0
        self.calls = []
        self.waits = []
        self.stops = 0
        self.url = "about:blank"

    def goto(self, url, wait_until, timeout):
        self.calls.append((url, wait_until, timeout))
        if url == TARGET_URL:
            self.target_calls += 1
            if self.target_calls <= self.fail_target_times:
                self.url = "chrome-error://timeout"
                raise sr.PWTimeout("timeout")
        self.url = url

    def evaluate(self, script):
        self.stops += 1
        self.last_script = script

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def title(self):
        return "Fake page"


class FakeTextLocator:
    def __init__(self, visible):
        self.first = self
        self.visible = visible

    def wait_for(self, state, timeout):
        if not self.visible:
            raise sr.PWTimeout("missing")


class FakeAccountPage:
    def __init__(self, visible_texts):
        self.visible_texts = visible_texts
        self.url = "https://example.test/clients/base/"

    def get_by_text(self, text, exact=False):
        return FakeTextLocator(any(text in item for item in self.visible_texts))

    def title(self):
        return "Client base"


class TestGotoWithRetry(unittest.TestCase):
    def test_resets_page_after_timeout_before_retry(self):
        page = FakePage(fail_target_times=1)

        sr.goto_with_retry(page, TARGET_URL, attempts=2, timeout=123)

        self.assertEqual(
            page.calls,
            [
                (TARGET_URL, "commit", 123),
                ("about:blank", "commit", 5000),
                (TARGET_URL, "commit", 123),
            ],
        )
        self.assertEqual(page.stops, 1)
        self.assertEqual(page.last_script, "window.stop()")
        self.assertEqual(page.waits, [2000])

    def test_raises_last_timeout_after_final_attempt(self):
        page = FakePage(fail_target_times=2)

        with self.assertRaises(sr.PWTimeout):
            sr.goto_with_retry(page, TARGET_URL, attempts=1, timeout=123)

        self.assertEqual(
            page.calls,
            [
                (TARGET_URL, "commit", 123),
                ("about:blank", "commit", 5000),
            ],
        )
        self.assertEqual(page.stops, 1)
        self.assertEqual(page.waits, [])


class FakeRetryPage:
    def __init__(self):
        self.reloads = 0
        self.waits = []
        self.url = "about:blank"

    def reload(self, wait_until, timeout):
        self.reloads += 1

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


class TestWithPageRetry(unittest.TestCase):
    def setUp(self):
        # dump_debug дергает page.screenshot/content — в юнит-тесте подменяем no-op.
        self._orig_dump = sr.dump_debug
        sr.dump_debug = lambda page, tag: None

    def tearDown(self):
        sr.dump_debug = self._orig_dump

    def test_returns_without_reload_on_success(self):
        page = FakeRetryPage()
        calls = []

        def action():
            calls.append(1)
            return "ok"

        self.assertEqual(sr.with_page_retry(page, action, "шаг", attempts=3), "ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(page.reloads, 0)

    def test_retries_with_reload_then_raises(self):
        page = FakeRetryPage()
        calls = []

        def action():
            calls.append(1)
            raise sr.PWTimeout("boom")

        with self.assertRaises(sr.PWTimeout):
            sr.with_page_retry(page, action, "шаг", attempts=2)

        self.assertEqual(len(calls), 2)       # ровно attempts попыток
        self.assertEqual(page.reloads, 1)     # один reload между попытками
        self.assertEqual(page.waits, [1500])  # пауза после reload


class TestAccountGuard(unittest.TestCase):
    def setUp(self):
        self.required = sr.YCLIENTS_REQUIRED_ACCOUNT_TEXT
        self.forbidden = sr.YCLIENTS_FORBIDDEN_ACCOUNT_TEXT

    def tearDown(self):
        sr.YCLIENTS_REQUIRED_ACCOUNT_TEXT = self.required
        sr.YCLIENTS_FORBIDDEN_ACCOUNT_TEXT = self.forbidden

    def test_blocks_forbidden_account_text(self):
        sr.YCLIENTS_REQUIRED_ACCOUNT_TEXT = ""
        sr.YCLIENTS_FORBIDDEN_ACCOUNT_TEXT = "old@example.test"

        self.assertFalse(sr.is_allowed_account(FakeAccountPage(["old@example.test"])))

    def test_allows_required_account_text(self):
        sr.YCLIENTS_REQUIRED_ACCOUNT_TEXT = "customer"
        sr.YCLIENTS_FORBIDDEN_ACCOUNT_TEXT = ""

        self.assertTrue(sr.is_allowed_account(FakeAccountPage(["customer dashboard"])))

    def test_blocks_when_required_account_text_missing(self):
        sr.YCLIENTS_REQUIRED_ACCOUNT_TEXT = "customer"
        sr.YCLIENTS_FORBIDDEN_ACCOUNT_TEXT = ""

        self.assertFalse(sr.is_allowed_account(FakeAccountPage(["other dashboard"])))


if __name__ == "__main__":
    unittest.main()
