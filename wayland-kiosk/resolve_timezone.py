"""Resolve a named browser timezone without modifying host-mounted files."""

import json
import os
from pathlib import Path
import sys


def resolve_timezone(options, environ, etc=Path('/etc'), zones=Path('/usr/share/zoneinfo')):
    def valid(value):
        if not isinstance(value, str) or not value or value.startswith('/'):
            return False
        if any(part in ('', '.', '..') for part in value.split('/')):
            return False
        try:
            return (zones / value).read_bytes().startswith(b'TZif')
        except OSError:
            return False

    override = options.get('time_zone', 'America/Chicago')
    if override:
        if not valid(override):
            raise ValueError('Invalid time_zone option; use an IANA name such as America/Chicago')
        return override

    candidates = [environ.get('TZ', '')]
    try:
        candidates.append((etc / 'timezone').read_text().strip())
    except OSError:
        pass
    localtime = etc / 'localtime'
    try:
        candidates.append(localtime.resolve().relative_to(zones.resolve()).as_posix())
    except (OSError, ValueError):
        pass
    for candidate in candidates:
        if valid(candidate):
            return candidate

    # A bind-mounted TZif file has no symlink name. Match its full rules,
    # never just today's UTC offset (which loses daylight saving behavior).
    try:
        data = localtime.read_bytes()
        names = (zones / 'zone.tab').read_text().splitlines()
        for line in names:
            if not line or line.startswith('#'):
                continue
            name = line.split()[2]
            if valid(name) and (zones / name).read_bytes() == data:
                return name
    except OSError:
        pass
    return ''


if __name__ == '__main__':
    try:
        with open('/data/options.json', encoding='utf-8') as source:
            options = json.load(source)
        print(resolve_timezone(options, os.environ))
    except (OSError, ValueError) as error:
        print(f'Time zone configuration failed: {error}', file=sys.stderr)
        sys.exit(1)
