import ast
import asyncio
import logging
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

SOURCE = Path(__file__).resolve().parents[1] / 'wayland-kiosk/rest_server.py'


def watchdog_namespace():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'display_health_probe', 'chromium_watchdog'}
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    ns = dict(asyncio=type('Clock', (), {'sleep': AsyncMock()})(), logging=logging,
              DISPLAY_PROBE='render probe', ERROR_PAGE_PREFIX='chrome-error://',
              WATCHDOG_INTERVAL=30, WATCHDOG_RENDERER_TIMEOUT=15,
              WATCHDOG_FINAL_TIMEOUT=30, WATCHDOG_FAILURE_LIMIT=3,
              RUNTIME_STATE={'renderer_crashes': 0, 'display_frozen': False,
                             'unresponsive_reloads': 0, 'last_crash': None},
              container_memory=lambda: (None, None), chromium_rss=lambda: (0, 0),
              _mib=str, memory_events=lambda: {},
              cdp_page_command=AsyncMock(), execute_command=AsyncMock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_page_restarts_after_one_reload_and_final_check(self):
        ns = watchdog_namespace()
        ns['display_health_probe'] = AsyncMock(return_value=False)
        await ns['chromium_watchdog']()
        self.assertEqual(ns['display_health_probe'].await_count, 4)
        ns['cdp_page_command'].assert_awaited_once()
        ns['execute_command'].assert_awaited_once_with(
            ['killall', 'cage'], log_prefix='watchdog', allow_command=True)

    async def test_recorded_crash_restarts_without_accepting_live_renderer(self):
        ns = watchdog_namespace()
        ns['RUNTIME_STATE']['renderer_crashes'] = 1
        await ns['chromium_watchdog']()
        ns['execute_command'].assert_awaited_once()
        ns['cdp_page_command'].assert_not_awaited()

    async def test_probe_rejects_live_but_blank_or_error_pages(self):
        for value in ('http://homeassistant:8123/lovelace',
                      {'uri': 'http://homeassistant:8123', 'ready': True, 'visible': False},
                      {'uri': 'chrome-error://chromewebdata/', 'ready': True, 'visible': True}):
            ns = watchdog_namespace()
            ns['cdp_page_command'].return_value = {'success': True, 'result': {'result': {'value': value}}}
            self.assertFalse(await ns['display_health_probe'](15))

    async def test_probe_accepts_rendered_content(self):
        ns = watchdog_namespace()
        ns['cdp_page_command'].return_value = {'success': True, 'result': {'result': {'value':
            {'uri': 'http://homeassistant:8123/lovelace', 'ready': True, 'visible': True}}}}
        self.assertTrue(await ns['display_health_probe'](15))
        self.assertTrue(ns['cdp_page_command'].call_args.args[1]['awaitPromise'])

    async def test_intentional_display_off_skips_probe(self):
        ns = watchdog_namespace()
        ns['RUNTIME_STATE']['display_frozen'] = True
        ns['asyncio'].sleep.side_effect = [None, asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            await ns['chromium_watchdog']()
        ns['cdp_page_command'].assert_not_awaited()
        ns['execute_command'].assert_not_awaited()

    async def test_successful_final_render_check_cancels_restart(self):
        ns = watchdog_namespace()
        ns['display_health_probe'] = AsyncMock(side_effect=[False, False, False, True, asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError):
            await ns['chromium_watchdog']()
        ns['execute_command'].assert_not_awaited()
        self.assertTrue(ns['RUNTIME_STATE']['chromium_responsive'])
