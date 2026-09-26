"""Observe browser crash events without attaching a privileged debugger."""
import asyncio
import logging
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType

CENSUS_INTERVAL = 60


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
    """Reconnect across browser starts; this socket never holds the command lock."""
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
                    async for message in ws:
                        if message.type != WSMsgType.TEXT:
                            continue
                        event = message.json()
                        if event.get('id') == 1:
                            if 'error' in event:
                                raise RuntimeError('Crash event subscription rejected')
                            state['crash_monitor_connected'] = True
                            logging.info('Browser crash event subscription active.')
                        if event.get('method') == 'Target.targetCrashed':
                            params = event.get('params', {})
                            state['renderer_crashes'] += 1
                            state['last_crash'] = {
                                'status': str(params.get('status', 'unknown'))[:80],
                                'error_code': params.get('errorCode'),
                            }
                            logging.error('Chromium target crashed: status=%s error_code=%s; '
                                          'crashes=%d; cgroup memory.events=%s',
                                          state['last_crash']['status'],
                                          state['last_crash']['error_code'],
                                          state['renderer_crashes'], memory_events())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failures += 1
            if failures == 3 or failures % 60 == 0:
                logging.warning('Browser crash monitor reconnecting (%s).', type(error).__name__)
        state['crash_monitor_connected'] = False
        await asyncio.sleep(5)
