import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from src import readiness


class ReadinessTest(unittest.TestCase):
    def check(self, replies, deadline):
        current = 0.0
        calls = []

        def sleep(seconds):
            nonlocal current
            current += seconds

        def open_service(url, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, deadline - current)
            calls.append(url)
            attempts = replies[url]
            response = attempts.pop(0) if len(attempts) > 1 else attempts[0]
            if isinstance(response, OSError):
                raise response
            return nullcontext(SimpleNamespace(status=response))

        with (patch.object(readiness.time, "monotonic", side_effect=lambda: current),
              patch.object(readiness.time, "sleep", side_effect=sleep),
              patch.object(readiness, "build_opener", return_value=SimpleNamespace(open=open_service))):
            pending = readiness.wait_for_services({url: url for url in replies}, timeout=deadline)
        return current, calls, pending

    def test_waits_for_all_services_and_stops_as_soon_as_they_are_ready(self):
        elapsed, calls, pending = self.check({
            "backend": [200], "gateway": [OSError("starting"), 200], "desktop": [503, 200],
        }, 5)
        self.assertEqual(pending, [])
        self.assertEqual(calls, ["backend", "gateway", "desktop", "gateway", "desktop"])
        self.assertEqual(elapsed, 0.25)

    def test_reports_the_unready_service_at_the_deadline(self):
        elapsed, calls, pending = self.check({"backend": [200], "desktop": [503]}, 0.6)
        self.assertEqual(pending, ["desktop"])
        self.assertEqual(elapsed, 0.6)
        self.assertGreater(calls.count("desktop"), 1)
