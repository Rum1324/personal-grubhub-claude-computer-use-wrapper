#!/usr/bin/env python3
"""Tiny adb driver for the Grubhub Android app running in a local emulator.

Usage: droid.py <command> [args]
  up [--window] [--budget=S]  boot the "grubhub" AVD (headless unless --window), wait up to S s (default 180)
                       for it: prints "ready", or "booting" and exits 75 — then poll `status`
  status               ready | booting | stopped, with emulator pid and adb state (never blocks)
  down                 shut the emulator down (quick-boot snapshot is saved), wait up to 15 s for it to exit
  launch               open the Grubhub app
  screen [--all]       list on-screen elements: [i] text | desc | id | flags @(x,y)
  tap <query> [--exact] [--nth N]   tap element whose text/desc matches (case-insensitive)
  tapxy <x> <y>        tap coordinates
  find <query> [--up] [--max N]     scroll until an element matches, then print it
  collect <id> [<id>..] scroll to the end, printing each element with those resource ids once
  pick <choice>       select an option row in an item sheet (scrolls, verifies it's selected)
  wait <query> [secs]  wait until an element matches (default 15s)
  type <text>          type text into the focused field
  key <back|enter|home|tab>
  scroll <down|up>
  shot <path.png>      save a screenshot
  prepare <favorite id> scripted order up to a verified checkout (no order placed): prints READY + summary,
                       or STOP: <reason> + screen (exit 3) where the model takes over
  place                tap place-order ONCE on a verified checkout, wait for the confirmation: PLACED + order #
"""
import glob, os, re, subprocess, sys, time
import xml.etree.ElementTree as ET

SDK = os.environ.get("ANDROID_HOME", "/opt/homebrew/share/android-commandlinetools")
ADB = os.path.join(SDK, "platform-tools", "adb")
EMU = os.path.join(SDK, "emulator", "emulator")
AVD = os.environ.get("GRUBHUB_AVD", "grubhub")
AVD_DIR = os.path.join(os.environ.get("ANDROID_AVD_HOME", os.path.expanduser("~/.android/avd")), AVD + ".avd")
PKG = "com.grubhub.android"
KEYS = {"back": 4, "enter": 66, "home": 3, "tab": 61}
BOOTING = 75  # exit code of `up` when the budget ran out before boot finished (EX_TEMPFAIL)


def adb(*args, check=True, timeout=20):
    try:
        r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if check:
            sys.exit(f"adb {' '.join(args)} timed out after {timeout}s")
        return ""
    if check and r.returncode:
        sys.exit(f"adb {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def booted():
    return adb("shell", "getprop", "sys.boot_completed", check=False, timeout=5).strip() == "1"


def emulator_pids():
    """pids of emulator/qemu processes running this AVD."""
    ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    return [int(line.split(None, 1)[0]) for line in ps.splitlines()
            if re.search(rf"(emulator|qemu-system)\S*\s.*-avd\s+{re.escape(AVD)}(\s|$)", line)]


def adb_state():
    """'device', 'offline', ... for the first emulator in `adb devices`, or None."""
    m = re.search(r"^emulator-\d+\s+(\S+)", adb("devices", check=False, timeout=5), re.M)
    return m.group(1) if m else None


def clear_stale_locks():
    """Remove *.lock left in the AVD dir by a crashed emulator. Only call when no emulator process exists."""
    removed = []
    for p in glob.glob(os.path.join(AVD_DIR, "*.lock")):
        try:
            if os.path.isdir(p):
                for f in os.listdir(p):
                    os.remove(os.path.join(p, f))
                os.rmdir(p)
            else:
                os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            sys.exit(f"stale lock {p} could not be removed: {e}")
    return removed


def up(window=False, budget=180.0):
    t0 = time.monotonic()
    launched = False
    if not emulator_pids():
        removed = clear_stale_locks()
        if removed:
            print("cleared stale locks:", ", ".join(removed))
        cmd = [EMU, "-avd", AVD, "-no-audio", "-no-boot-anim"] + ([] if window else ["-no-window"])
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        launched = True
    while time.monotonic() - t0 < budget:
        if adb_state() == "device" and booted():
            adb("shell", "input", "keyevent", "224")  # wake
            adb("shell", "wm", "dismiss-keyguard", check=False)
            print("ready")
            return
        if launched and time.monotonic() - t0 > 5 and not emulator_pids():
            sys.exit("emulator exited during boot — run `status`, or boot with `up --window` to see why")
        time.sleep(1)
    print(f"booting ({time.monotonic() - t0:.0f}s so far) — poll `status` until it says ready")
    sys.exit(BOOTING)


def status():
    pids, state = emulator_pids(), adb_state()
    locks = [os.path.basename(p) for p in glob.glob(os.path.join(AVD_DIR, "*.lock"))]
    if not pids:
        print("stopped" + (f" (stale locks: {', '.join(locks)} — `up` clears them)" if locks else ""))
    elif state == "device" and booted():
        print(f"ready (emulator pid {pids[0]}, adb {state})")
    elif state is None and not booted():
        print(f"booting or stopping (emulator pid {pids[0]}, not on adb yet / any more)")
    else:
        print(f"booting (emulator pid {pids[0]}, adb {state})")


def down(wait=15):
    adb("emu", "kill", check=False, timeout=10)
    end = time.monotonic() + wait
    while time.monotonic() < end and emulator_pids():
        time.sleep(0.5)
    pids = emulator_pids()
    print(f"stopping (pid {pids[0]} still saving its snapshot)" if pids else "stopped")


def nodes():
    if "emulator-" not in adb("devices"):
        sys.exit("emulator is not running — run `up` first")
    for _ in range(4):
        out = adb("exec-out", "uiautomator", "dump", "/dev/tty", check=False)
        m = re.search(r"<\?xml.*</hierarchy>", out, re.S)
        if m:
            break
        time.sleep(0.7)
    else:
        sys.exit("could not dump UI (screen still animating?)")
    res = []
    for n in ET.fromstring(m.group(0)).iter("node"):
        b = [int(v) for v in re.findall(r"\d+", n.get("bounds", "[0,0][0,0]"))]
        if b[2] <= b[0] or b[3] <= b[1]:
            continue
        res.append({
            "text": n.get("text", "").strip(), "desc": n.get("content-desc", "").strip(),
            "id": n.get("resource-id", "").split("/")[-1],
            "click": n.get("clickable") == "true", "checked": n.get("checked") == "true",
            "checkable": n.get("checkable") == "true", "enabled": n.get("enabled") == "true",
            "focused": n.get("focused") == "true", "b": b,
            "xy": ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2),
        })
    return res


def fmt(i, n):
    flags = [f for f, on in (("click", n["click"]), ("checked", n["checked"]),
             ("unchecked", n["checkable"] and not n["checked"]), ("disabled", not n["enabled"])) if on]
    desc = n["desc"] if len(n["desc"]) <= 160 else n["desc"][:157] + "..."
    parts = [f'"{n["text"]}"' if n["text"] else "", f'desc="{desc}"' if desc else "",
             f'id={n["id"]}' if n["id"] else "", " ".join(flags)]
    return f"[{i}] " + " | ".join(p for p in parts if p) + f' @{n["xy"][0]},{n["xy"][1]}'


def screen(show_all=False):
    for i, n in enumerate(nodes()):
        if show_all or n["text"] or n["desc"]:
            print(fmt(i, n))


def match(query, exact=False):
    q = query.lower()
    ns = nodes()
    hits = [n for n in ns if q in (n["text"].lower(), n["desc"].lower())]
    if not exact:
        hits += [n for n in ns if n not in hits and (q in n["text"].lower() or q in n["desc"].lower())]
    return hits


def tap(query, exact=False, nth=0):
    hits = match(query, exact)
    if len(hits) <= nth:
        sys.exit(f'no element matching "{query}"')
    n = hits[nth]
    adb("shell", "input", "tap", *map(str, n["xy"]))
    print("tapped", fmt(nth, n), f"({len(hits)} matches)" if len(hits) > 1 else "")


_size = []


def swipe(direction):
    if not _size:
        _size.extend(map(int, re.findall(r"(\d+)x(\d+)", adb("shell", "wm", "size"))[-1]))
    w, h = _size
    y1, y2 = (int(h * .75), int(h * .30)) if direction == "down" else (int(h * .30), int(h * .75))
    adb("shell", "input", "swipe", str(w // 2), str(y1), str(w // 2), str(y2), "300")
    time.sleep(0.6)


def find(query, up_=False, max_=12):
    for _ in range(max_):
        hits = match(query)
        if hits:
            print(fmt(0, hits[0]))
            return
        swipe("up" if up_ else "down")
    sys.exit(f'"{query}" not found after {max_} scrolls')


def pick(choice, max_=12):
    """Select an option in a Grubhub item sheet (rows are id=menu_option_selection_root,
    desc="<choice>, not selected, checkbox"); scrolls to it and verifies it ends up selected."""
    def row():
        want = choice.lower()
        rows = [n for n in nodes() if n["id"] == "menu_option_selection_root"]
        label = lambda n: n["desc"].split(", ")[0].lower()
        return (next((n for n in rows if label(n) == want), None)
                or next((n for n in rows if label(n).startswith(want)), None))
    for _ in range(max_):
        r = row()
        if r:
            if "not selected" in r["desc"]:
                adb("shell", "input", "tap", *map(str, r["xy"]))
                time.sleep(0.8)
                r = row() or r
            if "not selected" in r["desc"]:
                sys.exit(f'tapped "{choice}" but it is still not selected: {r["desc"]}')
            print("picked", fmt(0, r))
            return
        swipe("down")
    sys.exit(f'option "{choice}" not found after {max_} scrolls')


def collect(ids, max_=40):
    """Scroll down a list, printing each (id, text) once in order, until two scrolls add nothing."""
    seen, stale = set(), 0
    for _ in range(max_):
        new = 0
        for n in nodes():
            key = (n["id"], n["text"] or n["desc"])
            if n["id"] in ids and key[1] and key not in seen:
                seen.add(key)
                new += 1
                print(f'{n["id"]}: {key[1]}')
        stale = 0 if new else stale + 1
        if stale >= 2:
            return
        swipe("down")


def wait(query, secs=15.0):
    end = time.time() + secs
    while time.time() < end:
        hits = match(query)
        if hits:
            print(fmt(0, hits[0]))
            return
        time.sleep(1)
    sys.exit(f'timed out waiting for "{query}"')


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = a[0], a[1:]
    flag = lambda f: f in rest and (rest.remove(f) or True)
    if cmd == "up":
        b = next((x for x in rest if x.startswith("--budget=")), None)
        up(window=flag("--window"), budget=float(b.split("=", 1)[1]) if b else 180.0)
    elif cmd == "status":
        status()
    elif cmd == "down":
        down()
    elif cmd == "launch":
        adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
        print("launched", PKG)
    elif cmd == "screen":
        screen(flag("--all"))
    elif cmd == "tap":
        exact = flag("--exact")
        nth = 0
        if "--nth" in rest:
            k = rest.index("--nth"); nth = int(rest[k + 1]); del rest[k:k + 2]
        tap(" ".join(rest), exact, nth)
    elif cmd == "tapxy":
        adb("shell", "input", "tap", rest[0], rest[1])
    elif cmd == "find":
        up_ = flag("--up")
        max_ = 12
        if "--max" in rest:
            k = rest.index("--max"); max_ = int(rest[k + 1]); del rest[k:k + 2]
        find(" ".join(rest), up_, max_)
    elif cmd == "collect":
        collect(set(rest))
    elif cmd == "pick":
        pick(" ".join(rest))
    elif cmd == "wait":
        secs = float(rest.pop()) if len(rest) > 1 and re.fullmatch(r"[\d.]+", rest[-1]) else 15
        wait(" ".join(rest), secs)
    elif cmd == "type":
        adb("shell", "input", "text", " ".join(rest).replace(" ", "%s").replace("'", r"\'"))
    elif cmd == "key":
        adb("shell", "input", "keyevent", str(KEYS[rest[0]]))
    elif cmd == "scroll":
        swipe(rest[0])
    elif cmd in ("prepare", "place"):
        import order  # scripts/order.py
        order.main(cmd, rest)
    elif cmd == "shot":
        with open(rest[0], "wb") as f:
            f.write(subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=20).stdout)
        print("saved", rest[0])
    else:
        sys.exit(f"unknown command {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
