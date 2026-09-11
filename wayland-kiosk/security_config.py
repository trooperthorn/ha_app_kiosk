"""Shared security configuration; invalid settings fail closed."""

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def load_options(path='/data/options.json'):
    options = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(options, dict):
        raise ValueError('App options must be an object')
    return options


def origin(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Only HTTP and HTTPS URLs are supported')
    if parts.username is not None or parts.password is not None:
        raise ValueError('Credentials must not be embedded in URLs')
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80)


def validate_url(url, allow_http=True):
    scheme, _, _ = origin(url)
    if scheme == 'http' and not allow_http:
        raise ValueError('HTTP is disabled; use HTTPS or enable allow_http')
    if any(ord(c) < 32 or c.isspace() for c in url):
        raise ValueError('URL contains whitespace or control characters')
    return url


def effective_ha_url(options):
    url = options.get('ha_url', 'http://homeassistant:8123')
    validate_url(url, options.get('allow_http', True))
    parts = urlsplit(url)
    # Legacy loopback was Core on host networking; it is not Core on bridge.
    if parts.hostname in ('127.0.0.1', 'localhost', '::1'):
        if parts.scheme == 'https':
            raise ValueError('Replace the HTTPS loopback URL with the hostname on its certificate')
        url = urlunsplit((parts.scheme, f'homeassistant:{parts.port or 8123}', parts.path, parts.query, parts.fragment))
    return url


def validate_options(options):
    effective_ha_url(options)
    if options.get('control_api_enabled', False):
        token = options.get('api_token', '')
        if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError('Enabled control API requires an api_token of at least 32 non-whitespace characters')
    if options.get('allow_self_signed', False) and not options.get('self_signed_certificate', '').strip():
        raise ValueError('Paste the trusted PEM certificate in self_signed_certificate')
    if options.get('auth_method', 'none') == 'credentials':
        if not options.get('ha_username') or not options.get('ha_password'):
            raise ValueError('Credential mode requires a username and password')
    return options
