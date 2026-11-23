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


DEFAULT_PORTS = {'http': 80, 'https': 443}


def _origin_entry(entry, allow_http):
    """Return the normalized scheme://host[:port] for one entry or raise ValueError."""
    if not isinstance(entry, str):
        raise ValueError(f'allowed_origins entry {entry!r} is not a string')
    if not entry:
        raise ValueError('allowed_origins entry is empty')
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in entry):
        raise ValueError(f'allowed_origins entry {entry!r} contains whitespace or control characters')
    if '*' in entry:
        raise ValueError(f'allowed_origins entry {entry!r} contains a wildcard; list exact origins only')
    if entry.startswith('.'):
        raise ValueError(f'allowed_origins entry {entry!r} starts with a dot; list exact origins only')
    parts = urlsplit(entry)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError(f'allowed_origins entry {entry!r} must look like https://host or https://host:port')
    if '@' in parts.netloc:
        raise ValueError(f'allowed_origins entry {entry!r} contains user information')
    if '?' in entry or '#' in entry:
        raise ValueError(f'allowed_origins entry {entry!r} contains a query or fragment')
    if parts.path not in ('', '/'):
        raise ValueError(f'allowed_origins entry {entry!r} contains a path; list the origin only')
    try:
        port = parts.port
    except ValueError:
        port = 0
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f'allowed_origins entry {entry!r} has a port outside 1-65535')
    host = parts.hostname
    if not host or host.startswith('.'):
        raise ValueError(f'allowed_origins entry {entry!r} has no valid host')
    if parts.scheme == 'http' and not allow_http:
        raise ValueError(f'allowed_origins entry {entry!r} uses HTTP, which is disabled; use HTTPS or enable allow_http')
    host = host.lower()
    if ':' in host:
        host = f'[{host}]'
    if port is None or port == DEFAULT_PORTS[parts.scheme]:
        return f'{parts.scheme}://{host}'
    return f'{parts.scheme}://{host}:{port}'


def _allowed_origins(entries, allow_http, ha_url):
    """Return (normalized origins, error messages); never raises for bad entries."""
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        return [], ['allowed_origins must be a list of origins']
    ha_origin = None
    if ha_url:
        try:
            ha_origin = origin(ha_url)
        except ValueError:
            pass
    result, errors = [], []
    for entry in entries:
        try:
            normalized = _origin_entry(entry, allow_http)
        except ValueError as error:
            errors.append(str(error))
            continue
        if ha_origin is not None and origin(normalized) == ha_origin:
            continue
        if normalized not in result:
            result.append(normalized)
    return result, errors


def normalize_allowed_origins(entries, allow_http=True, ha_url=None):
    """Validate and normalize the extra origins allowed while navigation is locked.

    Only exact scheme://host[:port] entries are accepted. Every bad entry is
    reported in one ValueError. Entries equal to the Home Assistant origin are
    dropped because that origin is always allowed.
    """
    result, errors = _allowed_origins(entries, allow_http, ha_url)
    if errors:
        raise ValueError('; '.join(errors))
    return result


def validate_options(options):
    errors = []
    ha_url = None
    try:
        ha_url = effective_ha_url(options)
    except ValueError as error:
        errors.append(str(error))
    if options.get('control_api_enabled', False):
        token = options.get('api_token', '')
        if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
            errors.append('Enabled control API requires an api_token of at least 32 non-whitespace characters')
    if options.get('allow_self_signed', False) and not (options.get('self_signed_certificate') or '').strip():
        errors.append('Paste the trusted PEM certificate in self_signed_certificate')
    if options.get('auth_method', 'none') == 'credentials':
        if not options.get('ha_username') or not options.get('ha_password'):
            errors.append('auth_method is credentials, which requires both ha_username and ha_password')
    # Checked even when lock_navigation is off so a typo surfaces immediately.
    errors.extend(_allowed_origins(options.get('allowed_origins'), options.get('allow_http', True), ha_url)[1])
    if errors:
        raise ValueError('; '.join(errors))
    return options
