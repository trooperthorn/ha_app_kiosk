"""Real Chromium trust, sandbox and privilege checks in the built image."""

import http.server
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import threading


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        print(result.stderr[-7000:], flush=True)
    result.check_returncode()
    return result


def prepare(options):
    Path('/data').mkdir(exist_ok=True)
    Path('/data/options.json').write_text(json.dumps(options))
    run(['python3', '/app/prepare_runtime.py'])


def browser(url):
    return run(['python3', '/app/prepare_runtime.py', 'launch',
        '/usr/lib/chromium/chromium', '--headless', '--disable-gpu', '--disable-gpu-shader-disk-cache', '--disable-software-rasterizer', '--use-gl=disabled',
        '--no-first-run', '--allow-chrome-scheme-url', '--disable-dev-shm-usage',
        '--host-resolver-rules=MAP ha.test 127.0.0.1, MAP wrong.test 127.0.0.1',
        '--user-data-dir=/data/chromium-profile', '--dump-dom', url]).stdout


prepare({})
env = run(['python3', '/app/prepare_runtime.py', 'launch', '/usr/bin/env'],
          env={**os.environ, 'SUPERVISOR_TOKEN': 'must-not-leak', 'HASSIO_TOKEN': 'must-not-leak'}).stdout
assert 'SUPERVISOR_TOKEN' not in env and 'HASSIO_TOKEN' not in env
uid = run(['python3', '/app/prepare_runtime.py', 'launch', '/usr/bin/id', '-u']).stdout.strip()
assert uid == '1000', uid
denied = subprocess.run(['python3', '/app/prepare_runtime.py', 'launch', '/bin/cat', '/data/options.json'], capture_output=True)
assert denied.returncode != 0, 'Desktop can read root options'
sandbox = re.sub('<[^>]+>', ' ', browser('chrome://sandbox'))
assert re.search(r'Layer 1 Sandbox\s+Namespace', sandbox), sandbox
assert re.search(r'Seccomp-BPF sandbox\s+Yes', sandbox, re.I), sandbox
print('PASS: non-root desktop, stripped service tokens, protected options, namespace and seccomp sandbox')

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(b'<body>KIOSK_TRUST_VERIFIED</body>')
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *args):
        pass


with tempfile.TemporaryDirectory() as temp:
    cert, key = Path(temp) / 'cert.pem', Path(temp) / 'key.pem'
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
         '-subj', '/CN=ha.test', '-addext', 'subjectAltName=DNS:ha.test',
         '-keyout', str(key), '-out', str(cert)])
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'https://ha.test:{server.server_port}/'
        assert 'KIOSK_TRUST_VERIFIED' not in browser(url), 'Untrusted certificate accepted'
        prepare({'ha_url': url, 'allow_self_signed': True, 'self_signed_certificate': cert.read_text()})
        trusted = browser(url)
        assert 'KIOSK_TRUST_VERIFIED' in trusted, trusted
        assert 'KIOSK_TRUST_VERIFIED' not in browser(url.replace('ha.test', 'wrong.test')), 'Wrong hostname accepted'
        prepare({'ha_url': url, 'allow_self_signed': False})
        assert 'KIOSK_TRUST_VERIFIED' not in browser(url), 'Disabled trust remains active'
        print('PASS: explicit self-signed trust, hostname verification, and trust removal')
    finally:
        server.shutdown()
        server.server_close()


# Navigation lockdown with allowed_origins, checked in the real browser: the
# managed policy must let the HA origin and a listed origin load while any
# other origin shows Chromium's blocked page. Plain HTTP on a loopback port
# is enough because the policy matches on scheme, host and port.
class MarkerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        try:
            self.wfile.write(b'<body>KIOSK_ORIGIN_REACHED</body>')
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def policy_browser(url):
    return run(['python3', '/app/prepare_runtime.py', 'launch',
        '/usr/lib/chromium/chromium', '--headless', '--disable-gpu', '--disable-gpu-shader-disk-cache', '--disable-software-rasterizer', '--use-gl=disabled',
        '--no-first-run', '--disable-dev-shm-usage',
        '--host-resolver-rules=MAP ha.test 127.0.0.1, MAP ok.test 127.0.0.1, MAP bad.test 127.0.0.1',
        '--user-data-dir=/data/chromium-profile', '--dump-dom', url]).stdout


POLICY_FILES = [Path(directory) / 'kiosk-security.json' for directory in (
    '/etc/chromium/policies/managed', '/etc/chromium-browser/policies/managed', '/etc/opt/chrome/policies/managed')]
server = http.server.HTTPServer(('127.0.0.1', 0), MarkerHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    port = server.server_port
    ha_url = f'http://ha.test:{port}'
    options = {'ha_url': ha_url, 'allow_http': True, 'lock_navigation': True,
               'allowed_origins': [f'http://ok.test:{port}/']}
    prepare(options)
    run(['python3', '/app/browser_policy.py', ha_url])
    policy = json.loads(POLICY_FILES[0].read_text())
    assert policy['URLAllowlist'] == [ha_url, f'http://ok.test:{port}'], policy
    assert 'KIOSK_ORIGIN_REACHED' in policy_browser(ha_url + '/'), 'HA origin blocked'
    assert 'KIOSK_ORIGIN_REACHED' in policy_browser(f'http://ok.test:{port}/'), 'Allowed origin blocked'
    assert 'KIOSK_ORIGIN_REACHED' not in policy_browser(f'http://bad.test:{port}/'), 'Unlisted origin loaded'
    print('PASS: lockdown allows the HA origin and allowed_origins and blocks every other origin')
finally:
    server.shutdown()
    server.server_close()
    for policy_file in POLICY_FILES:
        policy_file.unlink(missing_ok=True)
