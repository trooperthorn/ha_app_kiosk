"""Observe browser crash events without attaching a privileged debugger."""
import asyncio
import logging
import re
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType

CENSUS_INTERVAL = 60
CRASH_DUMP_DIR = Path('/data/chromium-profile/Crash Reports')
CONSOLE_HISTORY = 8
CONSOLE_TEXT_LIMIT = 200
_URL_QUERY = re.compile(r'(https?://[^\s?#"\']*)[?#][^\s"\']*')
_TOKEN_LIKE = re.compile(r'[A-Za-z0-9_\-.]{40,}')


def memory_events():
    """Only numeric cgroup counters; never log page content or credentials."""
    try:
        return {key: int(value) for key, value in
                (line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())}
    except (OSError, ValueError):
        return {}


def describe_census(previous, current):
    """One line describing what changed between two process censuses, or
    None when nothing did.

    A census is {"processes": {pid: type}, "targets": {target_id: type}}
    from SystemInfo.getProcessInfo and Target.getTargets. The host journal
    records every Chromium child that dies (systemd-coredump, SIGILL), but
    not which child it was: this diff says whether the page's own target
    survived the death, which separates "the dashboard renderer crashed"
    from "a spare or worker renderer was discarded".
    """
    if previous is None:
        return None
    gone = {pid: kind for pid, kind in previous['processes'].items()
            if pid not in current['processes']}
    new = {pid: kind for pid, kind in current['processes'].items()
           if pid not in previous['processes']}
    targets_gone = {tid: kind for tid, kind in previous['targets'].items()
                    if tid not in current['targets']}
    targets_new = {tid: kind for tid, kind in current['targets'].items()
                   if tid not in previous['targets']}
    if not (gone or new or targets_gone or targets_new):
        return None
    page_ids = [tid for tid, kind in previous['targets'].items() if kind == 'page']
    page_survived = bool(page_ids) and all(tid in current['targets'] for tid in page_ids)
    parts = []
    if gone:
        parts.append('gone=' + ','.join(f'{pid}:{kind}' for pid, kind in sorted(gone.items())))
    if new:
        parts.append('new=' + ','.join(f'{pid}:{kind}' for pid, kind in sorted(new.items())))
    if targets_gone:
        parts.append('targets_gone=' + ','.join(sorted(targets_gone.values())))
    if targets_new:
        parts.append('targets_new=' + ','.join(sorted(targets_new.values())))
    parts.append('page_target_survived=%s' % ('yes' if page_survived else 'no'))
    counts = {}
    for kind in current['processes'].values():
        counts[kind] = counts.get(kind, 0) + 1
    parts.append('now=' + ','.join(f'{kind}x{n}' for kind, n in sorted(counts.items())))
    return 'Chromium process census: ' + ' '.join(parts)


def safe_url(url):
    """Scheme, host and path only. Query strings and fragments carry auth
    codes (auth_callback?code=...) and access tokens, so they are dropped."""
    text = str(url)
    try:
        parts = urlsplit(text)
    except ValueError:
        return '?'
    if not parts.scheme:
        return text.split('?')[0].split('#')[0][:160]
    return f'{parts.scheme}://{parts.netloc}{parts.path}'[:160]


def redact_text(text):
    """Page error text with URL queries and token-shaped strings removed,
    whitespace collapsed and cut to CONSOLE_TEXT_LIMIT characters."""
    text = _URL_QUERY.sub(r'\1?[redacted]', str(text))
    text = _TOKEN_LIKE.sub('[redacted]', text)
    return ' '.join(text.split())[:CONSOLE_TEXT_LIMIT]


def console_entry(event):
    """A short, redacted line for a page error, or None for anything else.
    Only uncaught exceptions and console.error calls are kept: they are the
    last thing the dashboard said before a renderer assertion kills it."""
    method = event.get('method')
    params = event.get('params', {})
    if method == 'Runtime.exceptionThrown':
        details = params.get('exceptionDetails', {})
        text = details.get('exception', {}).get('description') or details.get('text', 'exception')
        where = safe_url(details['url']) if details.get('url') else ''
        return 'exception: ' + redact_text(f'{text} {where}')
    if method == 'Runtime.consoleAPICalled' and params.get('type') == 'error':
        args = params.get('args', [])[:3]
        text = ' '.join(str(arg.get('value', arg.get('description', ''))) for arg in args)
        return 'console.error: ' + redact_text(text)
    return None


def latest_crash_dumps(directory=CRASH_DUMP_DIR, limit=3):
    """[(relative path, bytes)] for the newest crashpad minidumps, newest
    first. Uploads are off, so dumps stay under the profile for pickup."""
    found = []
    try:
        for path in Path(directory).rglob('*.dmp'):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((stat.st_mtime, str(path.relative_to(directory)), stat.st_size))
    except OSError:
        return []
    found.sort(reverse=True)
    return [(name, size) for _, name, size in found[:limit]]


def _log_crash_dumps():
    """Crashpad writes the minidump a moment after the target dies; name the
    newest ones so a dump can be pulled from /data for symbolization."""
    dumps = latest_crash_dumps()
    if dumps:
        logging.error('Newest crashpad minidumps under %s: %s', CRASH_DUMP_DIR,
                      ', '.join(f'{name} ({size} bytes)' for name, size in dumps))
    else:
        logging.error('No crashpad minidump found under %s.', CRASH_DUMP_DIR)


async def _browser_command(session, ws_url, method, params=None):
    async with session.ws_connect(ws_url, receive_timeout=5) as ws:
        await ws.send_json({'id': 1, 'method': method, 'params': params or {}})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            event = message.json()
            if event.get('id') == 1:
                if 'error' in event:
                    raise RuntimeError(f"{method}: {event['error']}")
                return event.get('result', {})
    raise RuntimeError(f'{method}: connection closed')


async def take_census(session, ws_url):
    """{"processes": {pid: type}, "targets": {id: type}} from the browser."""
    info = await _browser_command(session, ws_url, 'SystemInfo.getProcessInfo')
    targets = await _browser_command(session, ws_url, 'Target.getTargets')
    return {
        'processes': {int(p['id']): str(p.get('type', '?')) for p in info.get('processInfo', [])},
        'targets': {str(t['targetId']): str(t.get('type', '?'))
                    for t in targets.get('targetInfos', [])},
    }


async def process_census(state, interval=CENSUS_INTERVAL):
    """Log a line whenever a Chromium child process or DevTools target
    appears or disappears. Read-only, one short CDP round trip a minute,
    never holds the command lock. The line is the missing half of the
    host's systemd-coredump record for a dead renderer.
    """
    previous = None
    failures = 0
    while True:
        try:
            async with ClientSession(timeout=ClientTimeout(total=15, sock_connect=5)) as session:
                async with session.get('http://127.0.0.1:9222/json/version',
                                       timeout=ClientTimeout(total=5)) as response:
                    response.raise_for_status()
                    info = await response.json()
                current = await take_census(session, info['webSocketDebuggerUrl'])
            failures = 0
            line = describe_census(previous, current)
            if line:
                logging.info(line)
            state['process_census'] = {
                'processes': len(current['processes']),
                'targets': len(current['targets']),
            }
            previous = current
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failures += 1
            previous = None
            if failures == 3 or failures % 60 == 0:
                logging.warning('Process census unavailable (%s).', type(error).__name__)
        await asyncio.sleep(interval)


async def watch_crashes(state):
    """Reconnect across browser starts; this socket never holds the command lock.

    Besides counting crashes, it remembers each target's URL (path only) and
    attaches to page targets to keep their last few errors, so a crash line
    says which page died and what it logged just before.
    """
    failures = 0
    while True:
        try:
            async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=5)) as session:
                async with session.get('http://127.0.0.1:9222/json/version',
                                       timeout=ClientTimeout(total=5)) as response:
                    response.raise_for_status()
                    info = await response.json()
                async with session.ws_connect(info['webSocketDebuggerUrl']) as ws:
                    await ws.send_json({'id': 1, 'method': 'Target.setDiscoverTargets',
                                        'params': {'discover': True}})
                    failures = 0
                    next_id = 100
                    urls = {}
                    sessions = {}
                    pending_attach = {}
                    page_errors = {}
                    async for message in ws:
                        if message.type != WSMsgType.TEXT:
                            continue
                        event = message.json()
                        method = event.get('method')
                        params = event.get('params', {})
                        if event.get('id') == 1:
                            if 'error' in event:
                                raise RuntimeError('Crash event subscription rejected')
                            state['crash_monitor_connected'] = True
                            logging.info('Browser crash event subscription active.')
                        elif event.get('id') in pending_attach:
                            target_id = pending_attach.pop(event['id'])
                            session_id = event.get('result', {}).get('sessionId')
                            if session_id:
                                sessions[session_id] = target_id
                                next_id += 1
                                await ws.send_json({'id': next_id, 'sessionId': session_id,
                                                    'method': 'Runtime.enable'})
                        elif method in ('Target.targetCreated', 'Target.targetInfoChanged'):
                            target = params.get('targetInfo', {})
                            target_id = target.get('targetId')
                            if target_id:
                                urls[target_id] = safe_url(target.get('url', ''))
                                if (target.get('type') == 'page'
                                        and target_id not in sessions.values()
                                        and target_id not in pending_attach.values()):
                                    next_id += 1
                                    pending_attach[next_id] = target_id
                                    await ws.send_json({
                                        'id': next_id, 'method': 'Target.attachToTarget',
                                        'params': {'targetId': target_id, 'flatten': True}})
                        elif method == 'Target.targetDestroyed':
                            urls.pop(params.get('targetId'), None)
                            page_errors.pop(params.get('targetId'), None)
                        elif method == 'Target.detachedFromTarget':
                            sessions.pop(params.get('sessionId'), None)
                        elif event.get('sessionId') in sessions:
                            entry = console_entry(event)
                            if entry:
                                page_errors.setdefault(
                                    sessions[event['sessionId']], deque(maxlen=CONSOLE_HISTORY)
                                ).append(entry)
                        if method == 'Target.targetCrashed':
                            target_id = params.get('targetId')
                            state['renderer_crashes'] += 1
                            state['last_crash'] = {
                                'status': str(params.get('status', 'unknown'))[:80],
                                'error_code': params.get('errorCode'),
                                'url': urls.get(target_id, 'unknown'),
                            }
                            logging.error('Chromium target crashed: status=%s error_code=%s url=%s; '
                                          'crashes=%d; cgroup memory.events=%s',
                                          state['last_crash']['status'],
                                          state['last_crash']['error_code'],
                                          state['last_crash']['url'],
                                          state['renderer_crashes'], memory_events())
                            recent = list(page_errors.get(target_id, ()))
                            logging.error('Page errors before the crash (oldest first): %s',
                                          ' | '.join(recent) if recent else 'none')
                            asyncio.get_running_loop().call_later(10, _log_crash_dumps)
                            # A crashed target keeps its id through a reload; re-attach then.
                            for session_id, attached in list(sessions.items()):
                                if attached == target_id:
                                    del sessions[session_id]
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failures += 1
            if failures == 3 or failures % 60 == 0:
                logging.warning('Browser crash monitor reconnecting (%s).', type(error).__name__)
        state['crash_monitor_connected'] = False
        await asyncio.sleep(5)
