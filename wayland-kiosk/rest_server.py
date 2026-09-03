import os
import shlex
import logging
import asyncio
import time
import urllib.request
import json
from typing import Any, Dict, Optional
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

# Type alias for your payload
Payload = Dict[str, Any]
SHORT_TIMEOUT = 5
WATCHDOG_RENDERER_TIMEOUT = 15
WATCHDOG_FINAL_TIMEOUT = 30
WATCHDOG_FAILURE_LIMIT = 3
WATCHDOG_INTERVAL = 30
DISPLAY_POLL_INTERVAL = 2

START_TIME = time.monotonic()

# Set by display_freeze_watcher, read by chromium_watchdog and health_handler;
# see docs/operations.md.
RUNTIME_STATE: Dict[str, Any] = {
    "display_on": None,
    "display_frozen": False,
    "chromium_responsive": True,
}

# CDP calls are serialized; see docs/design.md.
CDP_LOCK = asyncio.Lock()

# wlr-randr has no wildcard support; run.sh exports the real connector name.
KIOSK_OUTPUT = os.environ.get("KIOSK_OUTPUT", "")

# Command whitelist enforced by execute_command/start_background_command;
# see docs/security.md.
ALLOWED_COMMANDS = {"wlr-randr", "wtype", "killall", "swayidle"}

def load_options() -> Dict[str, Any]:
    try:
        with open("/data/options.json") as f:
            return json.load(f)
    except Exception:
        logging.exception("Failed to read /data/options.json")
        return {}

OPTIONS = load_options()
API_TOKEN: Optional[str] = OPTIONS.get("api_token") or None

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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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


async def cdp_page_command(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = SHORT_TIMEOUT,
    log_failure: bool = True,
) -> Dict[str, Any]:
    """Send one serialized command to the first Chromium page target."""
    try:
        async with CDP_LOCK:
            def _targets():
                with urllib.request.urlopen(
                    "http://127.0.0.1:9222/json",
                    timeout=min(timeout, SHORT_TIMEOUT),
                ) as response:
                    return json.loads(response.read())

            targets = await asyncio.to_thread(_targets)
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


# CHROME WATCHDOG
async def chromium_watchdog():
    """Recover a persistently frozen page without killing a busy renderer."""
    failures = 0
    await asyncio.sleep(20)  # give Chromium time to finish its initial boot

    while True:
        # A frozen page (see display_freeze_watcher) fails a renderer check by
        # design -- its timers are paused -- so skip the check entirely rather
        # than let this false-positive drive a restart.
        if RUNTIME_STATE["display_frozen"]:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            continue

        result = await cdp_page_command(
            "Runtime.evaluate",
            {"expression": "1", "returnByValue": True},
            timeout=WATCHDOG_RENDERER_TIMEOUT,
            log_failure=False,
        )

        if result["success"]:
            if failures:
                logging.info("Watchdog: Chromium renderer recovered.")
            failures = 0
            RUNTIME_STATE["chromium_responsive"] = True
        else:
            failures += 1
            RUNTIME_STATE["chromium_responsive"] = False
            logging.warning(
                "Watchdog: Chromium renderer unresponsive (%d/%d): %s",
                failures,
                WATCHDOG_FAILURE_LIMIT,
                result.get("error", "unknown CDP failure"),
            )

            # A reload is less disruptive than a full container restart.
            if failures == WATCHDOG_FAILURE_LIMIT - 1:
                reload_result = await cdp_page_command(
                    "Page.reload",
                    {"ignoreCache": False},
                    timeout=WATCHDOG_RENDERER_TIMEOUT,
                    log_failure=False,
                )
                if reload_result["success"]:
                    logging.warning(
                        "Watchdog: requested a page reload before considering restart."
                    )

            if failures >= WATCHDOG_FAILURE_LIMIT:
                # Longer confirmation avoids mistaking a busy renderer for a freeze.
                final_result = await cdp_page_command(
                    "Runtime.evaluate",
                    {"expression": "1", "returnByValue": True},
                    timeout=WATCHDOG_FINAL_TIMEOUT,
                    log_failure=False,
                )
                if final_result["success"]:
                    logging.info(
                        "Watchdog: renderer responded during final confirmation; "
                        "container restart cancelled."
                    )
                    failures = 0
                else:
                    logging.error(
                        "Watchdog: renderer failed final %ds confirmation; "
                        "forcing container restart.",
                        WATCHDOG_FINAL_TIMEOUT,
                    )
                    await execute_command(
                        ["killall", "cage"],
                        log_prefix="watchdog",
                        allow_command=True,
                    )
                    break

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
    if url != "about:blank" and not url.startswith(("http://", "https://")):
        url = "http://" + url
    result = await cdp_page_command("Page.navigate", {"url": url})
    if result["success"]:
        result["url"] = url
    return result

# SERVER INITIALIZATION & WATCHDOG EXECUTION


async def api_handler(request):
    """Handles incoming POST requests and routes them to the correct function."""
    if API_TOKEN:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {API_TOKEN}":
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

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
    })


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Starting HAOS-Wayland-Kiosk REST API...")
    if not API_TOKEN:
        logging.info(
            "Kiosk control API token (api_token) is not set; the loopback-only "
            "API does not require a Bearer token. This option is an app-local "
            "shared secret, not a Home Assistant long-lived access token."
        )

    asyncio.create_task(chromium_watchdog())
    logging.info("Chromium Watchdog initialized.")
    asyncio.create_task(display_freeze_watcher())
    logging.info("Display freeze watcher initialized.")

    app = web.Application()
    app.router.add_post('/api', api_handler)
    app.router.add_get('/api/health', health_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    # Loopback bind is the security boundary here; see docs/security.md.
    site = web.TCPSite(runner, '127.0.0.1', 8034, reuse_address=True)
    await site.start()

    logging.info("API listening on 127.0.0.1:8034. Ready for Home Assistant commands.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
