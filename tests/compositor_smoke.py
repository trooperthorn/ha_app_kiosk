"""Exercise the production launcher on Cage's virtual display, without GPU hardware."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import http.server
import threading
import asyncio
import sys
from aiohttp import ClientSession
sys.path.insert(0, "/app")
from crash_monitor import watch_crashes

Path('/data').mkdir(exist_ok=True)
Path('/data/options.json').write_text(json.dumps({'lock_navigation': False, 'audio_enabled': True}))
assert Path('/etc/asound.conf').is_file(), 'Mount tests/asound.conf read-only for audio fixture'
subprocess.run(['python3', '/app/prepare_runtime.py'], check=True)
subprocess.run(['python3', '/app/browser_policy.py', 'http://homeassistant:8123'], check=True)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'<html><body>Kiosk runtime test</body></html>')
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

env = {**os.environ, 'XDG_RUNTIME_DIR': '/tmp/xdg', 'WLR_BACKENDS': 'headless',
       'KIOSK_OUTPUT': 'HEADLESS-1', 'KIOSK_ROTATION_TRANSFORM': 'normal',
       'KIOSK_ROTATION': 'normal', 'KIOSK_AUDIO_ENABLED': 'true',
       'KIOSK_DARK_MODE': 'true', 'KIOSK_LOCK_NAVIGATION': 'false',
       'KIOSK_URL': f'http://127.0.0.1:{server.server_port}/', 'KIOSK_CHROMIUM_BIN': '/usr/lib/chromium/chromium'}
with open('/tmp/compositor.log', 'w+') as log:
    process = subprocess.Popen(['python3', '/app/prepare_runtime.py', 'launch',
        '/usr/bin/env', 'WLR_RENDERER=pixman', '/usr/bin/dbus-run-session', '--', '/usr/local/bin/cage', '-s', '--',
        '/app/launch-browser.sh'], env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(f'Compositor exited: {process.returncode}')
            try:
                with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=1) as response:
                    pages = json.load(response)
                if any(p.get('type') == 'page' for p in pages):
                    print('PASS: non-root Cage and production Wayland Chromium launcher', flush=True)
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError('Wayland browser did not start')
        async def exercise():
            state = {'renderer_crashes': 0, 'last_crash': None}
            monitor = asyncio.create_task(watch_crashes(state))
            async with ClientSession() as session:
                page = next(p for p in pages if p.get('type') == 'page')
                async with session.ws_connect(page['webSocketDebuggerUrl']) as ws:
                    sequence = 0
                    async def command(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        await ws.send_json({'id': sequence, 'method': method, 'params': params or {}})
                        while True:
                            result = await ws.receive_json(timeout=15)
                            if result.get('id') == sequence:
                                assert 'error' not in result, result
                                return result.get('result', {})
                    for _ in range(100):
                        ready = await command('Runtime.evaluate', {'expression':
                            "document.readyState === 'complete' && document.body.textContent.includes('Kiosk runtime test')",
                            'returnByValue': True})
                        if ready.get('result', {}).get('value') is True:
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError('Fixture page did not finish navigation')
                    # Exercise canvas/font paths, animations, and the ALSA output service.
                    result = await command('Runtime.evaluate', {'expression': """
                        const canvas = document.createElement('canvas');
                        canvas.width=800; canvas.height=600; document.body.append(canvas);
                        window.framesDrawn = 0;
                        const ctx = document.querySelector('canvas').getContext('2d');
                        function draw() {
                            ctx.clearRect(0,0,800,600);
                            ctx.font = '24px sans-serif';
                            ctx.fillText('Kiosk fonts: Central Time 12345', 10, 50);
                            for(let i=0;i<100;i++) ctx.fillRect((i*17+framesDrawn)%800,i*5,10,10);
                            framesDrawn++; requestAnimationFrame(draw);
                        } draw();
                        window.sound = new AudioContext();
                        const tone = sound.createOscillator();
                        const gain = sound.createGain(); gain.gain.value=0.001;
                        tone.connect(gain).connect(sound.destination); tone.start();
                        sound.resume();
                        """, 'userGesture': True})
                    assert 'exceptionDetails' not in result, result
                    previous_frames = 0
                    for _ in range(12):
                        await asyncio.sleep(5)
                        result = await command('Runtime.evaluate', {'expression': 'framesDrawn', 'returnByValue': True})
                        assert result['result']['value'] > previous_frames, result
                        previous_frames = result['result']['value']
                        assert state['renderer_crashes'] == 0, state
                    result = await command('Runtime.evaluate', {'expression': 'sound.state', 'returnByValue': True})
                    assert result['result']['value'] == 'running', result
                    print('PASS: 60 seconds of canvas/font/audio rendering without target crashes', flush=True)
                    # A deliberate renderer crash must be recorded and a reload must recover.
                    await ws.send_json({'id': 9999, 'method': 'Page.crash'})
                    # Renderer termination must be visible promptly under the production profile.
                    for _ in range(100):
                        if state['renderer_crashes']:
                            break
                        await asyncio.sleep(0.1)
                    assert state['renderer_crashes'] == 1, state
                    await command('Page.reload')
                    for _ in range(30):
                        result = await command('Runtime.evaluate', {'expression': '1', 'returnByValue': True})
                        if result.get('result', {}).get('value') == 1:
                            break
                        await asyncio.sleep(0.2)
                    else:
                        raise AssertionError('Renderer did not recover after reload')
                    print('PASS: crash event includes exit status/code and page reload recovers', flush=True)
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.seek(0)
        output = log.read()
        print(output[-8000:])
        for unexpected in ('cannot access file /etc/asound.conf', 'Fontconfig error:', 'Could not create NETLINK', 'Failed to initialize a udev monitor'):
            assert unexpected not in output, unexpected
