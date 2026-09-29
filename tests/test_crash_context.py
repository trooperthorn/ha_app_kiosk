import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wayland-kiosk'))

from crash_monitor import (  # noqa: E402
    CONSOLE_TEXT_LIMIT, console_entry, latest_crash_dumps, redact_text, safe_url)


class SafeUrlTests(unittest.TestCase):
    def test_drops_query_and_fragment(self):
        self.assertEqual(
            safe_url('http://homeassistant:8123/auth_callback?code=abc123&state=x#top'),
            'http://homeassistant:8123/auth_callback')

    def test_keeps_plain_path(self):
        self.assertEqual(safe_url('http://homeassistant:8123/lovelace/cameras'),
                         'http://homeassistant:8123/lovelace/cameras')

    def test_schemeless_value(self):
        self.assertEqual(safe_url('/lovelace?edit=1'), '/lovelace')


class RedactTextTests(unittest.TestCase):
    def test_strips_url_query(self):
        text = redact_text('failed http://ha:8123/api/camera_proxy/camera.door?token=secret here')
        self.assertIn('http://ha:8123/api/camera_proxy/camera.door?[redacted]', text)
        self.assertNotIn('secret', text)

    def test_redacts_token_shaped_strings(self):
        token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJhYmMifQ.sig'
        self.assertNotIn(token, redact_text(f'Bearer {token}'))

    def test_length_capped(self):
        self.assertEqual(len(redact_text('word ' * 200)), CONSOLE_TEXT_LIMIT)


class ConsoleEntryTests(unittest.TestCase):
    def test_uncaught_exception(self):
        event = {'method': 'Runtime.exceptionThrown', 'params': {'exceptionDetails': {
            'text': 'Uncaught', 'url': 'http://ha:8123/frontend_latest/app.js?v=1',
            'exception': {'description': 'TypeError: x is undefined'}}}}
        self.assertEqual(console_entry(event),
                         'exception: TypeError: x is undefined http://ha:8123/frontend_latest/app.js')

    def test_console_error_kept_other_levels_dropped(self):
        error = {'method': 'Runtime.consoleAPICalled',
                 'params': {'type': 'error', 'args': [{'value': 'WebRTC failed'}, {'value': 3}]}}
        log = {'method': 'Runtime.consoleAPICalled',
               'params': {'type': 'log', 'args': [{'value': 'hello'}]}}
        self.assertEqual(console_entry(error), 'console.error: WebRTC failed 3')
        self.assertIsNone(console_entry(log))
        self.assertIsNone(console_entry({'method': 'Page.loadEventFired'}))


class LatestCrashDumpsTests(unittest.TestCase):
    def test_newest_first_and_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pending').mkdir()
            (root / 'completed').mkdir()
            for index, name in enumerate(['pending/a.dmp', 'completed/b.dmp',
                                          'pending/c.dmp', 'pending/d.dmp']):
                path = root / name
                path.write_bytes(b'x' * (index + 1))
                os.utime(path, (1000 + index, 1000 + index))
            (root / 'pending' / 'c.meta').write_text('not a dump')
            dumps = latest_crash_dumps(root, limit=2)
        self.assertEqual([name.replace('\\', '/') for name, _ in dumps],
                         ['pending/d.dmp', 'pending/c.dmp'])
        self.assertEqual([size for _, size in dumps], [4, 3])

    def test_missing_directory(self):
        self.assertEqual(latest_crash_dumps(Path('/nonexistent/crash')), [])


if __name__ == '__main__':
    unittest.main()
