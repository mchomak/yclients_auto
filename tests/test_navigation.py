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


if __name__ == "__main__":
    unittest.main()
