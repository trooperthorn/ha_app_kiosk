"""Observe browser crash events without attaching a privileged debugger."""
import asyncio
import logging
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType


def memory_events():
    """Only numeric cgroup counters; never log page content or credentials."""
    try:
        return {key: int(value) for key, value in
                (line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())}
    except (OSError, ValueError):
        return {}


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
                async with session.ws_connect(info['webSocketDebuggerUrl'], heartbeat=20) as ws:
                    await ws.send_json({'id': 1, 'method': 'Target.setDiscoverTargets',
                                        'params': {'discover': True}})
                    failures = 0
                    async for message in ws:
                        if message.type != WSMsgType.TEXT:
                            continue
                        event = message.json()
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
        await asyncio.sleep(5)
