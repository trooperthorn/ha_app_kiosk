"""Root-only setup, then launch the desktop with a restricted environment."""

import os
import json
import shutil
from pathlib import Path
import pwd
import ssl
import subprocess
import sys

from security_config import load_options, validate_options

HOME = Path('/data/browser-home')
PROFILE = Path('/data/chromium-profile')


def prepare():
    options = validate_options(load_options())
    Path('/data/options.json').chmod(0o600)
    user = pwd.getpwnam('kiosk')
    for path in (HOME, PROFILE, Path('/tmp/xdg')):
        path.mkdir(parents=True, exist_ok=True)
        # Never follow profile symlinks as root during migration.
        if path.is_symlink():
            raise ValueError(f'Refusing symlink at {path}')
        os.chown(path, user.pw_uid, user.pw_gid)
        path.chmod(0o700)
    for base in (HOME, PROFILE):
        for directory, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                os.chown(Path(directory) / name, user.pw_uid, user.pw_gid, follow_symlinks=False)

    # Configure trust as the browser user, not as root over writable paths.
    result = subprocess.run(
        [sys.executable, '/app/prepare_runtime.py', 'trust'],
        user=user.pw_uid, group=user.pw_gid, extra_groups=[],
        input=options.get('self_signed_certificate', '') if options.get('allow_self_signed', False) else '',
        text=True, check=True, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': str(HOME), 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    return result.returncode


def trust():
    pem = sys.stdin.read()
    prefs = PROFILE / 'Default/Preferences'
    if prefs.exists():
        data = json.loads(prefs.read_text(encoding='utf-8'))
        data.setdefault('profile', {}).update(exit_type='Normal', exited_cleanly=True)
        data.setdefault('session', {})['restore_on_startup'] = 5
        data.setdefault('partition', {})['per_host_zoom_levels'] = {}
        prefs.write_text(json.dumps(data), encoding='utf-8')
    sessions = PROFILE / 'Default/Sessions'
    if sessions.is_symlink():
        sessions.unlink()
    elif sessions.exists():
        shutil.rmtree(sessions)

    db = HOME / '.pki/nssdb'
    db.mkdir(parents=True, exist_ok=True)
    cert = HOME / 'local-trust.pem'
    if not (db / 'cert9.db').exists():
        subprocess.run(['certutil', '-N', '--empty-password', '-d', f'sql:{db}'], check=True)
    # Remove the previous explicitly trusted cert when disabled or replaced.
    existing = subprocess.run(['certutil', '-L', '-d', f'sql:{db}', '-n', 'kiosk-local'], capture_output=True)
    if existing.returncode == 0:
        subprocess.run(['certutil', '-D', '-d', f'sql:{db}', '-n', 'kiosk-local'], check=True)
    cert.unlink(missing_ok=True)
    if pem:
        if pem.count('-----BEGIN CERTIFICATE-----') != 1 or 'PRIVATE KEY' in pem:
            raise ValueError('Supply one public PEM certificate, never a private key')
        ssl.PEM_cert_to_DER_cert(pem.strip())
        cert.write_text(pem, encoding='ascii')
        subprocess.run(['openssl', 'x509', '-in', str(cert), '-checkend', '0', '-noout'], check=True)
        subprocess.run(['certutil', '-A', '-d', f'sql:{db}', '-n', 'kiosk-local', '-t', 'C,,', '-i', str(cert)], check=True)


def launch(command):
    user = pwd.getpwnam('kiosk')
    options = load_options()
    # Add only selected render-node groups; seatd brokers compositor devices.
    groups = {user.pw_gid}
    for path in options.get('gpu_devices', ['/dev/dri/card0', '/dev/dri/renderD128']):
        if path.startswith('/dev/dri/') and Path(path).exists():
            groups.add(Path(path).stat().st_gid)
    env = {k: v for k, v in os.environ.items() if k.startswith('KIOSK_') or k in {
        'PATH', 'TZ', 'XDG_RUNTIME_DIR', 'LIBSEAT_BACKEND', 'SEATD_SOCK',
        'SEATD_VTBOUND', 'WLR_BACKENDS', 'CAGE_TOUCH_OUTPUT', 'CHROME_DESKTOP',
    }}
    env.update(HOME=str(HOME), USER='kiosk', LOGNAME='kiosk',
               PYTHONDONTWRITEBYTECODE='1', FONTCONFIG_PATH='/etc/fonts', FONTCONFIG_FILE='/etc/fonts/fonts.conf',
               XDG_CACHE_HOME=str(HOME / '.cache'))
    if options.get('audio_enabled', True):
        env.update({k: v for k, v in os.environ.items() if k.startswith('PULSE_')})
    else:
        env['PULSE_SERVER'] = 'unix:/tmp/kiosk-audio-disabled'
    os.setgroups(sorted(groups))
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.execvpe(command[0], command, env)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'trust':
        trust()
    elif len(sys.argv) > 1 and sys.argv[1] == 'launch':
        launch(sys.argv[2:])
    else:
        prepare()
