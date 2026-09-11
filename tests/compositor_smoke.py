"""Exercise the production launcher on Cage's virtual display, without GPU hardware."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

Path('/data').mkdir(exist_ok=True)
Path('/data/options.json').write_text('{}')
subprocess.run(['python3', '/app/prepare_runtime.py'], check=True)
env = {**os.environ, 'XDG_RUNTIME_DIR': '/tmp/xdg', 'WLR_BACKENDS': 'headless',
       'KIOSK_OUTPUT': 'HEADLESS-1', 'KIOSK_ROTATION_TRANSFORM': 'normal',
       'KIOSK_ROTATION': 'normal', 'KIOSK_AUDIO_ENABLED': 'false',
       'KIOSK_DARK_MODE': 'true', 'KIOSK_LOCK_NAVIGATION': 'false',
       'KIOSK_URL': 'about:blank', 'KIOSK_CHROMIUM_BIN': '/usr/lib/chromium/chromium'}
with open('/tmp/compositor.log', 'w+') as log:
    process = subprocess.Popen(['python3', '/app/prepare_runtime.py', 'launch',
        '/usr/bin/env', 'WLR_RENDERER=pixman', '/usr/local/bin/cage', '-s', '--',
        '/app/launch-browser.sh'], env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(f'Compositor exited: {process.returncode}')
            try:
                with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=1) as response:
                    pages = json.load(response)
                if any(p.get('type') == 'page' for p in pages):
                    print('PASS: non-root Cage and production Wayland Chromium launcher')
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError('Wayland browser did not start')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.seek(0)
        print(log.read()[-8000:])
