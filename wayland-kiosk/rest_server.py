import os
import shlex
import logging
import asyncio
import time
import urllib.request
import json
import hmac
from security_config import load_options, validate_options, validate_url, origin
from credential_login import credential_login
from crash_monitor import watch_crashes, memory_events
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

# Type alias for your payload
Payload = Dict[str, Any]
SHORT_TIMEOUT = 5
WATCHDOG_RENDERER_TIMEOUT = 15
WATCHDOG_FINAL_TIMEOUT = 30
WATCHDOG_FAILURE_LIMIT = 3
WATCHDOG_INTERVAL = 30
DISPLAY_POLL_INTERVAL = 2
MEMORY_LOG_INTERVAL = 1800
# Chromium's own error pages (unreachable host, DNS failure, renderer crash
# recovery) all report this document URI while the renderer itself stays
# perfectly responsive; see docs/operations.md.
ERROR_PAGE_PREFIX = "chrome-error://"
# One line per this many watchdog cycles while an error page persists, so a
# Core restart does not fill the log.
ERROR_PAGE_LOG_EVERY = 10

START_TIME = time.monotonic()

# Set by display_freeze_watcher, read by chromium_watchdog and health_handler;
# see docs/operations.md.
RUNTIME_STATE: Dict[str, Any] = {
    "renderer_crashes": 0,
    "last_crash": None,
    "display_on": None,
    "display_frozen": False,
    "chromium_responsive": True,
    "on_error_page": False,
    "error_page_reloads": 0,
    "unresponsive_reloads": 0,
    "container_memory_bytes": None,
    "container_memory_limit_bytes": None,
    "chromium_rss_bytes": None,
    "chromium_rss_peak_bytes": None,
}

# CDP calls are serialized; see docs/design.md.
CDP_LOCK = asyncio.Lock()

# wlr-randr has no wildcard support; run.sh exports the real connector name.
KIOSK_OUTPUT = os.environ.get("KIOSK_OUTPUT", "")

# The fully assembled dashboard URL (ha_url + ha_dashboard) exported by run.sh.
KIOSK_URL = os.environ.get("KIOSK_URL", "")
# Mirrors the Chromium managed policy run.sh writes; see docs/security.md.
NAVIGATION_LOCKED = os.environ.get("KIOSK_LOCK_NAVIGATION", "") == "true"
# A blocked auth redirect would just bounce back here every cycle.
RETURN_EXEMPT_PREFIXES = ("/auth/",)

# Command whitelist enforced by execute_command/start_background_command;
# see docs/security.md.
ALLOWED_COMMANDS = {"wlr-randr", "wtype", "killall", "swayidle"}

OPTIONS = validate_options(load_options())
API_TOKEN: Optional[str] = OPTIONS.get("api_token") or None

try:
    RETURN_TO_DASHBOARD = int(OPTIONS.get("return_to_dashboard") or 0)
except (TypeError, ValueError):
    RETURN_TO_DASHBOARD = 0

# SERVER ROUTING
ROUTES = {}


def register_function(name, optional=None, required=None, validators=None):
    """Registers a function into ROUTES, enforcing required fields and
    per-field validators before the handler ever runs."""
    required = required or []
    validators = validators or {}

    def decorator(func):
        async def wrapper(data: Payload) -> Dict[str, Any]:
            missing = [key for key in required if key not in data]
            if missing:
                return {"success": False, "error": f"Missing required field(s): {missing}"}
            for key, validator in validators.items():
                if key in data and not validator(data[key]):
                    return {"success": False, "error": f"Invalid value for field: {key}"}
            return await func(data)

        ROUTES[name] = wrapper
        return func

    return decorator


async def execute_command(cmd_list, timeout=SHORT_TIMEOUT, log_prefix="", allow_command=False, print_stdout=True):
    """Safe execution wrapper enforcing the command whitelist."""
    if not allow_command or cmd_list[0] not in ALLOWED_COMMANDS:
        return {"success": False, "error": f"Command {cmd_list[0]} not whitelisted."}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.communicate()
            raise
        if print_stdout and stdout:
            logging.info("[%s] stdout: %s", log_prefix, stdout.decode().strip())
        if stderr:
            logging.warning("[%s] stderr: %s", log_prefix, stderr.decode().strip())
        return {"success": proc.returncode == 0, "stdout": stdout.decode(), "stderr": stderr.decode()}
    except Exception as e:
        logging.exception("[%s] execution failed", log_prefix)
        return {"success": False, "error": str(e)}


async def query_display_on() -> Optional[bool]:
    """Query wlr-randr for the current output power state, or None on failure."""
    result = await execute_command(
        ["wlr-randr"], print_stdout=False, timeout=SHORT_TIMEOUT,
        log_prefix="display_poll", allow_command=True,
    )
    if not result["success"]:
        return None
    return "Enabled: yes" in result["stdout"]


async def start_background_command(cmd_list, log_prefix=""):
    """Start a whitelisted long-running process without waiting for exit."""
    if not cmd_list or cmd_list[0] not in ALLOWED_COMMANDS:
        return {"success": False, "error": "Command is not whitelisted."}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(0.2)
        if proc.returncode is not None:
            return {
                "success": False,
                "error": f"Command exited immediately with status {proc.returncode}",
            }
        logging.info("[%s] started PID %d", log_prefix, proc.pid)
        return {"success": True, "pid": proc.pid}
    except Exception as exc:
        logging.exception("[%s] background start failed", log_prefix)
        return {"success": False, "error": str(exc)}


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def container_memory() -> tuple:
    """(used, limit) bytes for this container, or (None, None).

    cgroup v2 first, then v1. An "unlimited" v1 limit is a sentinel near the
    word size rather than a real number, so it is reported as no limit.
    """
    used = _read_int("/sys/fs/cgroup/memory.current")
    if used is not None:
        limit = _read_int("/sys/fs/cgroup/memory.max")  # "max" -> None
        return used, limit
    used = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit is not None and limit > (1 << 60):
        limit = None
    return used, limit


def chromium_rss() -> tuple:
    """(summed RSS bytes, process count) for Chromium's process tree.

    Chromium is multi-process and RSS counts shared pages once per process,
    so this over-reports somewhat. It is a trend line for "is memory
    climbing between restarts", not an accounting figure.
    """
    total = 0
    count = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status", encoding="utf-8") as handle:
                name = ""
                for line in handle:
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                        if not name.startswith("chrom"):
                            break
                    elif line.startswith("VmRSS:") and name.startswith("chrom"):
                        total += int(line.split()[1]) * 1024
                        count += 1
                        break
        except (OSError, ValueError):
            continue
    return (total, count) if count else (None, 0)


def _mib(value: Optional[int]) -> str:
    return "unknown" if value is None else f"{value / (1024 * 1024):.0f} MiB"


async def memory_reporter():
    """Log a periodic memory line so resource questions have evidence.

    Answering "is the kiosk crashing because it is out of memory" from a
    support log needs a trend, not a snapshot taken after the fact; see
    docs/operations.md.
    """
    while True:
        used, limit = container_memory()
        rss, procs = chromium_rss()
        RUNTIME_STATE["container_memory_bytes"] = used
        RUNTIME_STATE["container_memory_limit_bytes"] = limit
        RUNTIME_STATE["chromium_rss_bytes"] = rss
        if rss is not None:
            peak = RUNTIME_STATE["chromium_rss_peak_bytes"]
            if peak is None or rss > peak:
                RUNTIME_STATE["chromium_rss_peak_bytes"] = rss

        share = ""
        if used is not None and limit:
            share = f" ({used / limit:.0%} of {_mib(limit)})"
        logging.info(
            "Memory: container %s%s, Chromium summed RSS %s across %d process(es), sampled RSS peak %s (shared pages counted per process).",
            _mib(used),
            share,
            _mib(rss),
            procs,
            _mib(RUNTIME_STATE["chromium_rss_peak_bytes"]),
        )
        await asyncio.sleep(MEMORY_LOG_INTERVAL)


def read_cdp_targets(timeout: float = SHORT_TIMEOUT):
    """Read Chromium's DevTools target list. Blocking; call in a thread."""
    with urllib.request.urlopen(
        "http://127.0.0.1:9222/json", timeout=timeout
    ) as response:
        return json.loads(response.read())


async def cdp_page_command(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = SHORT_TIMEOUT,
    log_failure: bool = True,
) -> Dict[str, Any]:
    """Send one serialized command to the first Chromium page target."""
    try:
        async with CDP_LOCK:
            targets = await asyncio.to_thread(
                read_cdp_targets, min(timeout, SHORT_TIMEOUT)
            )
            page = next((target for target in targets if target.get("type") == "page"), None)
            if not page or not page.get("webSocketDebuggerUrl"):
                return {"success": False, "error": "No Chromium page target found"}

            client_timeout = ClientTimeout(total=timeout + SHORT_TIMEOUT)
            async with ClientSession(timeout=client_timeout) as session:
                async with session.ws_connect(
                    page["webSocketDebuggerUrl"],
                    receive_timeout=timeout,
                ) as ws:
                    await ws.send_json({"id": 1, "method": method, "params": params or {}})
                    async for message in ws:
                        if message.type == WSMsgType.TEXT:
                            response = json.loads(message.data)
                            if response.get("id") != 1:
                                continue
                            if "error" in response:
                                return {"success": False, "error": response["error"]}
                            return {"success": True, "result": response.get("result", {})}
                        if message.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
            return {"success": False, "error": "Chromium closed the CDP connection"}
    except TimeoutError:
        error = f"CDP command {method} timed out after {timeout:g}s"
        if log_failure:
            logging.warning(error)
        return {"success": False, "error": error, "timed_out": True}
    except Exception as exc:
        if log_failure:
            logging.exception("CDP command %s failed", method)
        return {"success": False, "error": str(exc)}


def same_origin(url: str, reference: str) -> bool:
    """True when both URLs share a scheme, host and port."""
    try:
        return origin(url) == origin(reference)
    except ValueError:
        return False


def on_dashboard(url: str) -> bool:
    """True when the page is still somewhere inside the kiosk dashboard.

    Sub-views such as /lovelace/kitchen count as the dashboard: bouncing a
    wall panel back to the first view every cycle would be a bug, not
    lockdown. Only leaving the dashboard's own path counts as wandering.
    """
    if not KIOSK_URL or not url:
        return True
    if not same_origin(url, KIOSK_URL):
        return False
    path = urlsplit(url).path.rstrip("/")
    if path.startswith(RETURN_EXEMPT_PREFIXES):
        return True
    home = urlsplit(KIOSK_URL).path.rstrip("/")
    return path == home or path.startswith(home + "/")


async def current_page_url() -> Optional[str]:
    """URL of the first Chromium page target, or None if it cannot be read."""
    try:
        targets = await asyncio.to_thread(read_cdp_targets)
    except Exception:
        return None
    return next((t.get("url", "") for t in targets if t.get("type") == "page"), None)


async def report_browser_timezone():
    """Read the real renderer's timezone once it is available at startup."""
    for _ in range(12):
        await asyncio.sleep(10)
        result = await cdp_page_command(
            "Runtime.evaluate",
            {"expression": "JSON.stringify({zone: Intl.DateTimeFormat().resolvedOptions().timeZone, local: new Date().toString()})",
             "returnByValue": True},
            timeout=5,
            log_failure=False,
        )
        if result["success"]:
            value = result["result"].get("result", {}).get("value")
            if isinstance(value, str):
                logging.info("Chromium time zone: %s (requested TZ=%s)", value, os.environ.get("TZ", "unset"))
                return
    logging.warning("Could not verify Chromium time zone at startup.")


# Require a rendered surface and animation progress, not merely a live JS VM.
# Walk open shadow roots because Home Assistant renders its UI inside them.
DISPLAY_PROBE = r"""new Promise(resolve => {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    let content = false;
    function scan(root) {
      for (const el of root.children || []) {
        if (['HEAD','SCRIPT','STYLE','TEMPLATE','NOSCRIPT'].includes(el.tagName)) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' ||
            Number(style.opacity) === 0) continue;
        const r = el.getBoundingClientRect();
        const visible = r.width > 0 && r.height > 0 && r.bottom > 0 &&
          r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        const text = [...el.childNodes].some(n =>
          n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        if (visible && (text || ['CANVAS','VIDEO','IMG','SVG','INPUT','BUTTON'].includes(el.tagName)))
          content = true;
        if (el.shadowRoot) scan(el.shadowRoot);
        scan(el);
      }
    }
    if (document.body) scan(document.documentElement);
    resolve({uri: document.documentURI, visible: content,
      ready: document.readyState !== 'loading'});
  }));
})"""


async def display_health_probe(timeout):
    result = await cdp_page_command(
        "Runtime.evaluate",
        {"expression": DISPLAY_PROBE, "returnByValue": True, "awaitPromise": True},
        timeout=timeout, log_failure=False,
    )
    value = result.get("result", {}).get("result", {}).get("value")
    RUNTIME_STATE["on_error_page"] = bool(
        isinstance(value, dict) and isinstance(value.get("uri"), str)
        and value["uri"].startswith(ERROR_PAGE_PREFIX))
    healthy = (result.get("success") and isinstance(value, dict)
               and value.get("visible") is True and value.get("ready") is True
               and isinstance(value.get("uri"), str)
               and not value["uri"].startswith(ERROR_PAGE_PREFIX))
    return bool(healthy)


async def chromium_watchdog():
    """Restart on a recorded crash or persistent rendering failure.

    Intentional display-off remains exempt. DOM and animation checks cannot
    prove that a physical monitor is receiving pixels.
    """
    failures = 0
    await asyncio.sleep(20)
    while True:
        if RUNTIME_STATE["renderer_crashes"]:
            logging.error("Watchdog: recorded Chromium crash; restarting kiosk.")
            await execute_command(["killall", "cage"], log_prefix="watchdog",
                                  allow_command=True)
            return
        if RUNTIME_STATE["display_frozen"]:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            continue

        healthy = await display_health_probe(WATCHDOG_RENDERER_TIMEOUT)
        RUNTIME_STATE["chromium_responsive"] = healthy
        if healthy:
            if failures:
                logging.info("Watchdog: visible page content and animation checks recovered.")
            failures = 0
        else:
            failures += 1
            used, limit = container_memory()
            rss, procs = chromium_rss()
            logging.warning(
                "Display failure diagnostics: container=%s limit=%s; summed RSS=%s "
                "processes=%d; memory.events=%s; last_crash=%s",
                _mib(used), _mib(limit), _mib(rss), procs,
                memory_events(), RUNTIME_STATE["last_crash"],
            )
            logging.warning("Watchdog: display health failed (%d/%d).",
                            failures, WATCHDOG_FAILURE_LIMIT)
            if failures == 1:
                RUNTIME_STATE["unresponsive_reloads"] += 1
                await cdp_page_command("Page.reload", {"ignoreCache": False},
                                       timeout=WATCHDOG_RENDERER_TIMEOUT,
                                       log_failure=False)
            if failures >= WATCHDOG_FAILURE_LIMIT:
                # Never cancel restart just because evaluating '1' succeeds.
                if not await display_health_probe(WATCHDOG_FINAL_TIMEOUT):
                    logging.error("Watchdog: display health failed final confirmation; restarting kiosk.")
                    await execute_command(["killall", "cage"], log_prefix="watchdog",
                                          allow_command=True)
                    return
                failures = 0
                RUNTIME_STATE["chromium_responsive"] = True
        await asyncio.sleep(WATCHDOG_INTERVAL)


async def display_freeze_watcher():
    """Freeze Chromium's page lifecycle while the output is blanked.

    The physical output can go dark through three independent paths --
    run.sh's static swayidle process, a second swayidle started by
    display_on's `timeout` argument, or a direct display_off/wlr_randr call --
    none of which know about this server. Polling wlr-randr instead of
    hooking each path is what makes freezing cover all three; see
    docs/operations.md.
    """
    if not KIOSK_OUTPUT:
        logging.info("Freeze watcher: KIOSK_OUTPUT is not set; not starting.")
        return

    while True:
        is_on = await query_display_on()
        if is_on is not None:
            RUNTIME_STATE["display_on"] = is_on
            should_freeze = not is_on
            if should_freeze != RUNTIME_STATE["display_frozen"]:
                state = "frozen" if should_freeze else "active"
                result = await cdp_page_command(
                    "Page.setWebLifecycleState", {"state": state}, log_failure=False
                )
                if result["success"]:
                    RUNTIME_STATE["display_frozen"] = should_freeze
                    logging.info(
                        "Freeze watcher: output %s -> page %s",
                        "off" if should_freeze else "on",
                        state,
                    )

        await asyncio.sleep(DISPLAY_POLL_INTERVAL)


async def dashboard_return_watcher():
    """Send the browser home after it has wandered off the dashboard.

    The Chromium policy stops navigation to another host, but Home Assistant
    is a single-page app: moving from the dashboard to Settings or Developer
    tools is client-side routing, which no URL policy can see. Polling the
    current page and navigating back is the only thing that covers it; the
    real fix for a shared panel is a non-admin Home Assistant user, and
    docs/security.md says so.
    """
    if RETURN_TO_DASHBOARD <= 0:
        return
    if not KIOSK_URL:
        logging.warning(
            "return_to_dashboard is set but KIOSK_URL is not in the environment; "
            "the dashboard return watcher was NOT started."
        )
        return

    logging.info(
        "Dashboard return watcher: checking every %ds, home is %s",
        RETURN_TO_DASHBOARD,
        KIOSK_URL,
    )
    while True:
        await asyncio.sleep(RETURN_TO_DASHBOARD)
        # A blanked screen has a frozen page lifecycle; navigating it would
        # only fight display_freeze_watcher.
        if RUNTIME_STATE["display_frozen"]:
            continue
        url = await current_page_url()
        if url is None or on_dashboard(url):
            continue
        logging.info("Dashboard return watcher: page is at %s, navigating home.", url)
        result = await cdp_page_command("Page.navigate", {"url": KIOSK_URL})
        if not result["success"]:
            logging.warning(
                "Dashboard return watcher: navigation home failed: %s",
                result.get("error"),
            )


# WAYLAND API ENDPOINTS

@register_function("refresh_browser")
async def handle_refresh_browser(data: Payload) -> Dict[str, Any]:
    """Reload the kiosk page through Chromium's DevTools protocol."""
    return await cdp_page_command("Page.reload", {"ignoreCache": False})


@register_function("is_display_on")
async def handle_is_display_on(data: Payload) -> Dict[str, Any]:
    """Return boolean whether monitor is currently on."""
    is_on = await query_display_on()
    if is_on is None:
        return {"success": False, "error": "Failed to query display state"}

    logging.info("[is_display_on] Monitor is %s", "ON" if is_on else "OFF")
    return {"success": True, "display_on": is_on}


@register_function("screenshot")
async def handle_screenshot(data: Payload) -> Dict[str, Any]:
    """Capture the current Chromium frame via CDP, base64-encoded PNG."""
    result = await cdp_page_command("Page.captureScreenshot", {"format": "png"})
    if not result["success"]:
        return result
    return {"success": True, "format": "png", "data": result["result"].get("data", "")}


@register_function("display_on", optional=["timeout"])
async def handle_display_on(data: Payload) -> Dict[str, Any]:
    """Turn display on, optionally set swayidle blanking timeout."""
    if not KIOSK_OUTPUT:
        return {"success": False, "error": "KIOSK_OUTPUT is not set -- cannot target an output."}
    blank_timeout = data.get("timeout")
    cmds = [["wlr-randr", "--output", KIOSK_OUTPUT, "--on"]]
    log_msg = ""

    if blank_timeout is None:
        pass
    elif blank_timeout == 0:
        cmds += [["killall", "swayidle"]]
        log_msg = " Screen timeout disabled"
    elif blank_timeout > 0:
        t = str(blank_timeout)
        cmds += [
            ["killall", "swayidle"],
            ["swayidle", "-w", "timeout", t,
             f"wlr-randr --output {KIOSK_OUTPUT} --off",
             "resume", f"wlr-randr --output {KIOSK_OUTPUT} --on"]
        ]
        log_msg = f" Screen timeout: {blank_timeout}s"

    results = []
    for cmd in cmds:
        if cmd[0] == "swayidle":
            results.append(await start_background_command(cmd, log_prefix="display_on"))
        else:
            results.append(await execute_command(
                cmd, timeout=SHORT_TIMEOUT, log_prefix="display_on", allow_command=True
            ))
    logging.info("[display_on]%s", log_msg)
    return {"success": all(r["success"] for r in results), "results": results}


@register_function("display_off")
async def handle_display_off(data: Payload) -> Dict[str, Any]:
    """Force display off immediately using Wayland."""
    if not KIOSK_OUTPUT:
        return {"success": False, "error": "KIOSK_OUTPUT is not set -- cannot target an output."}
    result = await execute_command(["wlr-randr", "--output", KIOSK_OUTPUT, "--off"],
                                    timeout=SHORT_TIMEOUT, log_prefix="display_off", allow_command=True)
    return {"success": result["success"]}


@register_function("wlr_randr", required=["args"])
async def handle_wlr_randr(data: Payload) -> Dict[str, Any]:
    """Run a whitelisted wlr-randr command; see docs/security.md."""
    args = data["args"]
    try:
        args_list = shlex.split(args)
    except ValueError as e:
        return {"success": False, "error": f"Could not parse args: {e}"}

    result = await execute_command(["wlr-randr"] + args_list, timeout=SHORT_TIMEOUT, log_prefix="wlr_randr", allow_command=True)
    return {"success": result["success"], "result": result}


@register_function("launch_url", optional=["url"])
async def handle_launch_url(data: Payload) -> Dict[str, Any]:
    """Redirect browser to given URL via Chrome DevTools Protocol."""
    url = str(data["url"]) if data.get("url") else str(OPTIONS.get("ha_url") or "http://127.0.0.1:8123")
    if url != "about:blank":
        try:
            validate_url(url, OPTIONS.get('allow_http', True))
        except ValueError as error:
            return {"success": False, "error": str(error)}
    # Chromium would refuse this anyway once lock_navigation is on; refusing
    # it here turns a silently blocked page into an answer the caller can act
    # on. See docs/security.md.
    if (
        NAVIGATION_LOCKED
        and KIOSK_URL
        and url != "about:blank"
        and not same_origin(url, KIOSK_URL)
    ):
        return {
            "success": False,
            "error": (
                "lock_navigation is enabled; only URLs on "
                f"{urlsplit(KIOSK_URL).scheme}://{urlsplit(KIOSK_URL).netloc} "
                "can be loaded."
            ),
        }
    result = await cdp_page_command("Page.navigate", {"url": url})
    if result["success"]:
        result["url"] = url
    return result

# SERVER INITIALIZATION & WATCHDOG EXECUTION


async def api_handler(request):
    """Handles incoming POST requests and routes them to the correct function."""
    if not OPTIONS.get('control_api_enabled', False) or not API_TOKEN:
        return web.json_response({"success": False, "error": "Control API disabled"}, status=503)
    if request.headers.get('Origin') is not None:
        return web.json_response({"success": False, "error": "Browser-origin requests are not accepted"}, status=403)
    auth_header = request.headers.get('Authorization', '')
    if not hmac.compare_digest(auth_header.encode(), f'Bearer {API_TOKEN}'.encode()):
        return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
    if request.content_type != 'application/json':
        return web.json_response({"success": False, "error": "Use application/json"}, status=415)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if not isinstance(data, dict) or not isinstance(data.get('command'), str):
        return web.json_response({"success": False, "error": "Expected an object with a command string"}, status=400)
    command = data.get("command")
    if command not in ROUTES:
        return web.json_response({"success": False, "error": f"Unknown command: {command}"}, status=400)

    try:
        result = await ROUTES[command](data)
        return web.json_response(result)
    except Exception:
        # Log the real exception server-side, but don't leak internals to
        # the caller.
        logging.exception("Unhandled error handling command: %s", command)
        return web.json_response({"success": False, "error": "Internal server error"}, status=500)


async def health_handler(request):
    """Unauthenticated read-only status, for external monitoring; see
    docs/security.md for why this endpoint carries no token check."""
    return web.json_response({
        "success": True,
        "app_uptime_seconds": round(time.monotonic() - START_TIME, 1),
        "display_on": RUNTIME_STATE["display_on"],
        "display_frozen": RUNTIME_STATE["display_frozen"],
        "chromium_responsive": RUNTIME_STATE["chromium_responsive"],
        "navigation_locked": NAVIGATION_LOCKED,
        "renderer_crashes": RUNTIME_STATE["renderer_crashes"],
        "last_crash": RUNTIME_STATE["last_crash"],
        "on_error_page": RUNTIME_STATE["on_error_page"],
        "error_page_reloads": RUNTIME_STATE["error_page_reloads"],
        "unresponsive_reloads": RUNTIME_STATE["unresponsive_reloads"],
        "container_memory_bytes": RUNTIME_STATE["container_memory_bytes"],
        "container_memory_limit_bytes": RUNTIME_STATE["container_memory_limit_bytes"],
        "chromium_rss_bytes": RUNTIME_STATE["chromium_rss_bytes"],
        "chromium_rss_peak_bytes": RUNTIME_STATE["chromium_rss_peak_bytes"],
    })


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Starting kiosk watchdog services...")
    asyncio.create_task(credential_login(OPTIONS, cdp_page_command))
    asyncio.create_task(report_browser_timezone())
    asyncio.create_task(watch_crashes(RUNTIME_STATE))
    logging.info("Browser crash event monitor initialized (no ptrace capability).")
    asyncio.create_task(chromium_watchdog())
    logging.info("Chromium Watchdog initialized.")
    asyncio.create_task(display_freeze_watcher())
    logging.info("Display freeze watcher initialized.")
    asyncio.create_task(dashboard_return_watcher())
    asyncio.create_task(memory_reporter())
    logging.info("Memory reporter initialized (one line every %ds).", MEMORY_LOG_INTERVAL)

    if OPTIONS.get('control_api_enabled', False):
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_post('/api', api_handler)
        app.router.add_get('/api/health', health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        # Bridge-only container; no published ports. Core uses the app DNS name.
        site = web.TCPSite(runner, '0.0.0.0', 8034, reuse_address=True)  # nosec B104
        await site.start()
        logging.info('Authenticated control API available on the internal app network, port 8034.')
    else:
        logging.info('Control API disabled; browser watchdog remains active.')

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
