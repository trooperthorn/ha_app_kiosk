import importlib.util
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo
from datetime import datetime

MODULE = Path(__file__).resolve().parents[1] / 'wayland-kiosk/resolve_timezone.py'
spec = importlib.util.spec_from_file_location('resolve_timezone', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TimezoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.etc = Path(self.temp.name)
        self.zones = Path('/usr/share/zoneinfo')

    def resolve(self, options=None, env=None):
        return module.resolve_timezone(options if options is not None else {'time_zone': ''}, env or {}, self.etc, self.zones)

    def test_override_beats_inherited_utc(self):
        self.assertEqual(self.resolve({'time_zone': 'America/Chicago'}, {'TZ': 'UTC'}), 'America/Chicago')

    def test_existing_options_without_new_key_default_to_chicago(self):
        self.assertEqual(self.resolve({}, {'TZ': 'UTC'}), 'America/Chicago')

    def test_supervisor_default(self):
        self.assertEqual(self.resolve(env={'TZ': 'America/Chicago'}), 'America/Chicago')

    def test_invalid_override_rejected(self):
        for name in ('../etc/passwd', '/etc/localtime', 'Not/AZone'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.resolve({'time_zone': name}, {'TZ': 'UTC'})

    def test_invalid_environment_uses_timezone_file(self):
        (self.etc / 'timezone').write_text('America/Chicago\n')
        self.assertEqual(self.resolve(env={'TZ': 'Not/AZone'}), 'America/Chicago')

    def test_symlink(self):
        (self.etc / 'localtime').symlink_to(self.zones / 'America/Chicago')
        self.assertEqual(self.resolve(), 'America/Chicago')

    def test_regular_localtime_file_is_preserved(self):
        data = (self.zones / 'America/Chicago').read_bytes()
        (self.etc / 'localtime').write_bytes(data)
        self.assertEqual(self.resolve(), 'America/Chicago')
        self.assertEqual((self.etc / 'localtime').read_bytes(), data)
        self.assertFalse((self.etc / 'localtime').is_symlink())

    def test_missing_sources(self):
        self.assertEqual(self.resolve(), '')

    def test_central_dst(self):
        name = self.resolve({'time_zone': 'America/Chicago'})
        zone = ZoneInfo(name)
        self.assertEqual(datetime(2026, 9, 11, tzinfo=zone).utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(datetime(2026, 1, 11, tzinfo=zone).utcoffset().total_seconds(), -6 * 3600)
