#!/usr/bin/env python3
"""Stdio MCP server exposing the grubhub skill's emulator driver and data files to Claude Desktop / Cowork.

Cowork runs its shell in a sandboxed VM that can't reach the Mac's Android emulator, so the skill
calls these tools instead of `python3 scripts/droid.py` when it runs there. Stdlib only (Python 3.9+).

Every tools/call runs on its own thread, so a slow or stuck call never blocks the others: `now` and
`health` answer while an adb command is running. droid.py runs one at a time (adb_lock) inside a
per-command time budget; when a budget runs out the call returns an error instead of hanging.
Each request is logged to stderr (→ ~/Library/Logs/Claude/mcp-server-grubhub.log) and to server.log.

Register in ~/Library/Application Support/Claude/claude_desktop_config.json:
  "grubhub": {"command": "/usr/bin/python3", "args": ["<abs path>/scripts/mcp_server.py"]}
"""
import base64, json, os, re, signal, subprocess, sys, threading, time
from datetime import datetime

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DROID = os.path.join(SKILL, "scripts", "droid.py")
LOG = os.path.join(SKILL, "server.log")
FILES = {  # name -> (path, writable)
    "favorites.json": (os.path.join(SKILL, "favorites.json"), True),
    "state.json": (os.path.join(SKILL, "state.json"), True),
    "flow.md": (os.path.join(SKILL, "references", "flow.md"), True),
    "locations.md": (os.path.join(SKILL, "references", "locations.md"), True),
    "SKILL.md": (os.path.join(SKILL, "SKILL.md"), False),
}
FILE_TIMEOUT = 15
UP_BUDGET = 50  # droid.py up stops waiting after this and reports "booting"
BOOTING = 75  # droid.py up's exit code for "booting"
# Seconds each droid.py command may take, waiting for adb_lock included. All stay under the client's ~120 s.
BUDGET = {"up": UP_BUDGET + 8, "down": 20, "status": 15, "launch": 20, "screen": 30, "tap": 30, "tapxy": 15,
          "pick": 90, "find": 90, "collect": 110, "prepare": 115, "place": 70, "type": 15, "key": 15, "scroll": 15, "shot": 30}
DEFAULT_BUDGET = 60
NO_LOCK = {"status"}  # read-only, answers while another command holds adb_lock

TOOLS = [
    {"name": "android",
     "description": "Run one droid.py command against the Grubhub Android emulator on the user's Mac "
                    "(up, status, down, launch, screen, tap, tapxy, find, pick, collect, wait, type, key, scroll, shot, prepare, place). "
                    "prepare <favorite id> runs the whole order up to a verified checkout in one call (never places): "
                    "READY + summary, or STOP: <reason> + screen to continue by hand. "
                    "place taps place-order once on that checkout and returns PLACED + order number. "
                    "up returns within ~60 s: \"ready\", or \"booting\" — then poll `status` until it says ready. "
                    "shot takes one arg (file name) and returns the screenshot image. "
                    "Same semantics as `python3 scripts/droid.py <command> <args...>` in the grubhub skill.",
     "inputSchema": {"type": "object", "properties": {
         "command": {"type": "string"},
         "args": {"type": "array", "items": {"type": "string"}, "default": []}},
         "required": ["command"]}},
    {"name": "skill_file_read",
     "description": "Read a grubhub skill file from the Mac: " + ", ".join(FILES) + ". Missing state.json returns ''.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "enum": list(FILES)}},
                     "required": ["name"]}},
    {"name": "skill_file_write",
     "description": "Overwrite a grubhub skill file on the Mac (favorites.json, state.json, flow.md, locations.md). "
                    "JSON files are validated before writing.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "enum": [n for n, (_, w) in FILES.items() if w]},
         "content": {"type": "string"}}, "required": ["name", "content"]}},
    {"name": "now",
     "description": "Current local time on the Mac as ISO-8601 with offset (for the 30-minute cooldown).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "health",
     "description": "Liveness check: server pid, uptime, and the requests in flight (answers even while adb is busy).",
     "inputSchema": {"type": "object", "properties": {}}},
]

STARTED = time.time()
out_lock, log_lock, adb_lock = threading.Lock(), threading.Lock(), threading.Lock()
inflight = {}  # request id -> {"tool", "detail", "since"}
adb_holder = [None]  # the droid.py command holding adb_lock


def log(msg):
    line = f"{datetime.now().isoformat(timespec='milliseconds')} [grubhub-server pid={os.getpid()}] {msg}"
    with log_lock:
        print(line, file=sys.stderr, flush=True)
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def with_timeout(fn, secs, what):
    """Run fn on a daemon thread; raise TimeoutError after secs (the thread is abandoned, not the server)."""
    box = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(secs)
    if t.is_alive():
        raise TimeoutError(f"{what} did not finish in {secs}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def run_droid(cmd, args, budget):
    """Run droid.py within budget seconds; kill its whole process group (adb children too) on timeout."""
    p = subprocess.Popen([sys.executable, DROID, cmd, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        return f"android {' '.join([cmd, *args])} timed out after {budget:.0f}s (killed)", True
    text = (out + (("\n" if out else "") + err if err else "")).strip()
    still_booting = cmd == "up" and p.returncode == BOOTING  # progress, not failure: the model polls `status`
    return text or "(no output)", p.returncode != 0 and not still_booting


def droid(cmd, args, budget):
    """run_droid under adb_lock: one emulator command at a time; waiting for the lock counts against budget."""
    if cmd in NO_LOCK:
        return run_droid(cmd, args, budget)
    t0 = time.monotonic()
    if not adb_lock.acquire(timeout=budget):
        return f"android busy: `{adb_holder[0]}` still running after {budget}s; retry, or check `android status`", True
    try:
        adb_holder[0] = " ".join([cmd, *args])
        return run_droid(cmd, args, max(1.0, budget - (time.monotonic() - t0)))
    finally:
        adb_holder[0] = None
        adb_lock.release()


def read_file(name):
    path, _ = FILES[name]
    if not os.path.exists(path):
        if name == "state.json":
            return ""
        raise FileNotFoundError(f"{name} not found at {path} (run install.sh)")
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file(name, content):
    path, _ = FILES[name]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def call(name, a):
    if name == "android":
        cmd, args = a["command"], list(a.get("args") or [])
        if cmd == "up":
            args = [x for x in args if not x.startswith("--budget")] + [f"--budget={UP_BUDGET}"]
        if cmd == "wait":  # wait <query> [secs]: the requested wait plus overhead, capped under the client timeout
            secs = float(args[-1]) if len(args) > 1 and re.fullmatch(r"[\d.]+", args[-1]) else 15
            budget = min(secs + 15, 110)
        else:
            budget = BUDGET.get(cmd, DEFAULT_BUDGET)
        if cmd == "shot":
            fname = re.sub(r"[^\w.-]", "_", (args or ["screen"])[0]).removesuffix(".png") + ".png"
            path = os.path.join(SKILL, "receipts", fname)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            text, err = droid("shot", [path], budget)
            if err:
                return text, True
            data = base64.b64encode(open(path, "rb").read()).decode()
            return [{"type": "image", "data": data, "mimeType": "image/png"},
                    {"type": "text", "text": f"saved {path}"}], False
        return droid(cmd, args, budget)
    if name == "skill_file_read":
        n = a["name"]
        if n not in FILES:
            raise ValueError(f"unknown file {n}")
        return with_timeout(lambda: read_file(n), FILE_TIMEOUT, f"reading {n}"), False
    if name == "skill_file_write":
        n = a["name"]
        if n not in FILES or not FILES[n][1]:
            raise ValueError(f"{n} is read-only")
        if n.endswith(".json"):
            json.loads(a["content"])
        with_timeout(lambda: write_file(n, a["content"]), FILE_TIMEOUT, f"writing {n}")
        return f"wrote {n}", False
    if name == "now":
        return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"), False
    if name == "health":
        t = time.monotonic()
        return json.dumps({"ok": True, "pid": os.getpid(), "uptime_s": round(time.time() - STARTED),
                           "adb_busy": adb_holder[0],
                           "inflight": [{"id": i, "tool": r["tool"], "detail": r["detail"],
                                         "elapsed_s": round(t - r["since"], 1)} for i, r in list(inflight.items())]}), False
    raise ValueError(f"unknown tool {name}")


def send(reply):
    with out_lock:
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def run_tool(mid, p):
    name, a = p.get("name"), p.get("arguments") or {}
    detail = " ".join([a.get("command", ""), *a.get("args", [])]) if name == "android" else a.get("name", "")
    t0 = time.monotonic()
    inflight[mid] = {"tool": name, "detail": detail, "since": t0}
    log(f"start id={mid} {name} {detail}".rstrip())
    try:
        text, is_error = call(name, a)
    except Exception as e:  # report tool failures to the model, not as protocol errors
        text, is_error = f"{type(e).__name__}: {e}", True
    finally:
        inflight.pop(mid, None)
    content = text if isinstance(text, list) else [{"type": "text", "text": text}]
    send({"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": is_error}})
    summary = "" if isinstance(text, list) else " | " + text.replace("\n", " ")[:120]
    log(f"done  id={mid} {name} {detail} {'ERROR' if is_error else 'ok'} {1000 * (time.monotonic() - t0):.0f}ms{summary}")


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification
        return None
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "grubhub", "version": "1.2.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        threading.Thread(target=run_tool, args=(mid, msg.get("params", {})), daemon=True).start()
        return None
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    log(f"started python={sys.version.split()[0]} skill={SKILL}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if reply is not None:
            send(reply)
    log("stdin closed; exiting")


if __name__ == "__main__":
    main()
