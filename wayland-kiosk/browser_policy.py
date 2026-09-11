"""Generate browser restrictions independently of navigation lockdown."""

import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from security_config import load_options, validate_url


def policy_for(options, url):
    validate_url(url, options.get('allow_http', True))
    parts = urlsplit(url)
    policy = {
        'DefaultPopupsSetting': 2, 'IncognitoModeAvailability': 1,
        'BrowserSignin': 0, 'SyncDisabled': True,
        'PasswordManagerEnabled': False, 'AutofillAddressEnabled': False,
        'AutofillCreditCardEnabled': False, 'PrintingEnabled': False,
        'DownloadRestrictions': 3, 'AllowFileSelectionDialogs': False,
        'BackgroundModeEnabled': False, 'DefaultNotificationsSetting': 2,
        'AudioCaptureAllowed': False, 'VideoCaptureAllowed': False,
        'DefaultSoundContentSetting': 1 if options.get('audio_enabled', True) else 2,
    }
    if options.get('lock_navigation', True):
        policy.update(URLBlocklist=['*'], URLAllowlist=[f'{parts.scheme}://{parts.netloc}'])
    elif not options.get('allow_http', True):
        policy['URLBlocklist'] = ['http://*']
    return policy


if __name__ == '__main__':
    payload = json.dumps(policy_for(load_options(), sys.argv[1]), indent=2)
    for directory in ('/etc/chromium/policies/managed', '/etc/chromium-browser/policies/managed', '/etc/opt/chrome/policies/managed'):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / 'kiosk-lockdown.json').unlink(missing_ok=True)
        (root / 'kiosk-security.json').write_text(payload, encoding='utf-8')
