"""Run inside the built app image; verify startup and actual Chromium ICU."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

# Exercise the real startup block, including bashio's strict shell flags,
# with the same inherited UTC that an explicit setting must override.
Path('/data').mkdir(exist_ok=True)
Path('/data/options.json').write_text(json.dumps({'time_zone': 'America/Chicago'}))
startup = Path('/run.sh').read_text()
block = startup[startup.index('KIOSK_TZ='):startup.index('# Seat management')]
with tempfile.TemporaryDirectory() as temp:
    probe = Path(temp) / 'startup.sh'
    probe.write_text(block + '\nprintf "RESOLVED=%s\\n" "$TZ"\n')
    result = subprocess.run(['bashio', str(probe)], env={**os.environ, 'TZ': 'UTC'},
                            check=True, capture_output=True, text=True)
    assert 'RESOLVED=America/Chicago' in result.stdout, result.stdout
    print(result.stdout.strip())
    page = Path(temp) / 'timezone.html'
    page.write_text('''<body><script>
document.body.textContent = JSON.stringify({
 zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
 summer: new Date('2026-09-11T15:12:00Z').getHours(),
 summerOffset: new Date('2026-09-11T15:12:00Z').getTimezoneOffset(),
 winter: new Date('2026-01-11T15:12:00Z').getHours(),
 winterOffset: new Date('2026-01-11T15:12:00Z').getTimezoneOffset()
});</script></body>''')
    result = subprocess.run([
        '/usr/lib/chromium/chromium', '--headless', '--no-sandbox',
        '--disable-dev-shm-usage', '--no-first-run', '--disable-gpu',
        f'--user-data-dir={temp}/profile', '--dump-dom', page.as_uri(),
    ], env={**os.environ, 'TZ': 'America/Chicago'},
        check=True, capture_output=True, text=True, timeout=60)
    match = re.search(r'<body>(\{.*?\})</body>', result.stdout)
    assert match, result.stdout + result.stderr
    actual = json.loads(match.group(1))
    assert actual == {'zone': 'America/Chicago', 'summer': 10, 'summerOffset': 300,
                      'winter': 9, 'winterOffset': 360}, actual
    print('Actual Chromium:', actual)
