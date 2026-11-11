"""Scripted happy path for one favorite, so an order takes 2 tool calls instead of ~25 screen/tap round-trips.

  prepare <id>   fresh app → search → location → item → choices + drink → bag → cart → checkout → payment gate.
                 Never places an order. Prints READY + a summary, or STOP: <reason> + the screen (exit 3):
                 anything unexpected stops here and the model continues by hand from that screen.
  place          re-checks the checkout gate, taps place-order exactly once, waits for the confirmation.

Every step waits for the screen it expects (polling the UI dump) instead of sleeping, and verifies what it did.
Python 3.9 compatible (the MCP server runs /usr/bin/python3).
"""
import json, os, re, sys, time

import droid as d

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOP, UNCERTAIN = 3, 4
DEADLINE = 100  # seconds; stays under the MCP client's ~120 s call timeout
SODAS = ("pepsi", "coke", "coca", "sprite", "soda", "dr pepper", "mountain dew", "7up", "ginger ale")
GATEWAY_DRINKS = ["Proud Source", "Pepsi", "Diet Pepsi"]
T0 = time.monotonic()


class Stop(Exception):
    pass


def say(msg):
    print(f"· {msg}  [{time.monotonic() - T0:.0f}s]", flush=True)


def nodes():
    try:
        return d.nodes()
    except SystemExit as e:
        raise Stop(str(e))


def by_id(ns, i):
    return [n for n in ns if n["id"] == i]


def label(n):
    return n["text"] or n["desc"]


def text_of(ns, i):
    hits = by_id(ns, i)
    return label(hits[0]) if hits else ""


def has(ns, q):
    q = q.lower()
    return next((n for n in ns if q in n["text"].lower() or q in n["desc"].lower()), None)


def tap(n, pause=0.0):
    d.adb("shell", "input", "tap", *map(str, n["xy"]))
    if pause:
        time.sleep(pause)


def interrupt(ns):
    """Dismiss known popups. Returns True if it did something."""
    if by_id(ns, "rr_page_title"):  # "How did it go?" rating screen
        n = has(ns, "Not now")
        if n:
            say("dismissed rating popup")
            tap(n, 0.8)
            return True
    return False


def wait_for(pred, secs, what):
    if time.monotonic() - T0 > DEADLINE:
        raise Stop(f"out of time before: {what} — continue by hand from this screen")
    end = time.monotonic() + secs
    while True:
        ns = nodes()
        bot_check(ns)
        if not interrupt(ns):
            r = pred(ns)
            if r:
                return ns, r
        if time.monotonic() > end:
            raise Stop(f"timed out ({secs:.0f}s) waiting for {what}")
        time.sleep(0.3)


def sig(ns):
    return tuple((n["id"], label(n), n["xy"][1]) for n in ns if label(n))


def to_top(max_=8):
    prev = None
    for _ in range(max_):
        d.swipe("up")
        s = sig(nodes())
        if s == prev:
            return
        prev = s


# ---------- item sheet ----------

def same(a, b):
    """Option label a matches wanted b: exact, else prefix (so "Diet Pepsi" matches "Diet Pepsi Can"
    and "Pepsi" matches "Pepsi Can" but not "Diet Pepsi")."""
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a.startswith(b)


def row_label(n):
    return n["desc"].split(", ")[0]


def scan_sheet():
    """Scroll the item sheet top→bottom. Returns groups in order:
    [{"name", "rule", "options": [{"label", "selected"}]}]."""
    to_top()
    groups, seen, prev = [], set(), None
    cur = None
    for _ in range(14):
        ns = nodes()
        items = [n for n in ns if n["id"] in ("menu_choice_name", "menu_choice_selection", "menu_option_selection_root")]
        items.sort(key=lambda n: n["xy"][1])
        for n in items:
            if n["id"] == "menu_choice_name":
                name = label(n)
                cur = next((g for g in groups if g["name"] == name), None)
                if cur is None:
                    cur = {"name": name, "rule": "", "options": []}
                    groups.append(cur)
            elif n["id"] == "menu_choice_selection" and cur is not None and not cur["rule"]:
                cur["rule"] = label(n)
            elif n["id"] == "menu_option_selection_root" and cur is not None:
                key = (cur["name"], row_label(n))
                if key not in seen:
                    seen.add(key)
                    cur["options"].append({"label": row_label(n), "selected": "not selected" not in n["desc"]})
        s = sig(ns)
        if s == prev:
            break
        prev = s
        d.swipe("down")
    return groups


def find_row(want, max_=14):
    """Scroll down from the current position to the option row matching want."""
    prev = None
    for _ in range(max_):
        ns = nodes()
        rows = [n for n in ns if n["id"] == "menu_option_selection_root"]
        r = (next((n for n in rows if row_label(n).lower() == want.lower()), None)
             or next((n for n in rows if same(row_label(n), want)), None))
        if r:
            return r
        s = sig(ns)
        if s == prev:
            return None
        prev = s
        d.swipe("down")
    return None


def set_option(want, selected=True):
    to_top()
    r = find_row(want)
    if r is None:
        return False
    if ("not selected" not in r["desc"]) != selected:
        tap(r, 0.8)
        lbl = row_label(r)
        rows = [n for n in nodes() if n["id"] == "menu_option_selection_root" and row_label(n) == lbl]
        now = bool(rows) and "not selected" not in rows[0]["desc"]
        if now != selected:
            raise Stop(f'tapped "{lbl}" but it is still {"not " if selected else ""}selected')
    return True


def expand_collapsed(groups):
    """Optional groups can be collapsed (no option rows visible). Tap their headers open."""
    opened = False
    for g in groups:
        if not g["options"]:
            to_top()
            for _ in range(14):
                n = next((x for x in nodes() if x["id"] == "menu_choice_name" and label(x) == g["name"]), None)
                if n:
                    say(f'expanded collapsed group "{g["name"]}"')
                    tap(n, 0.8)
                    opened = True
                    break
                d.swipe("down")
    return opened


def drink_plan(groups, restaurant, notes):
    g = next((g for g in groups if re.search(r"drink|beverage", g["name"], re.I)), None)
    if g is None:
        return []
    opts = [o["label"] for o in g["options"]]
    if "gateway" in restaurant.lower() and re.search(r"up to [2-9]", g["rule"], re.I):
        return GATEWAY_DRINKS
    if any(same(o, "Diet Pepsi") for o in opts):
        return ["Diet Pepsi"]
    soda = next((o for o in opts if any(s in o.lower() for s in SODAS)), None)
    alt = soda or next((o for o in opts if not re.match(r"no (drink|beverage)", o, re.I)), None)
    if not alt:
        raise Stop(f'no Diet Pepsi and no other drink in "{g["name"]}": {opts}')
    notes.append(f"Diet Pepsi missing → {alt}")
    return [alt]


# ---------- steps ----------

def load_favorite(key):
    with open(os.path.join(SKILL, "favorites.json"), encoding="utf-8") as fh:
        favs = json.load(fh)
    if key.isdigit():
        f = next((f for f in favs if str(f["id"]) == key), None)
    else:
        hits = [f for f in favs if key.lower() in f["label"].lower() or key.lower() in f["item"].lower()]
        f = hits[0] if len(hits) == 1 else None
    if not f:
        raise Stop(f'no single favorite matches "{key}"')
    return f


def bot_check(ns):
    if has(ns, "not a robot") or has(ns, "Human Challenge") or has(ns, "hit a roadblock"):
        raise Stop("Grubhub bot check (\"Press & Hold\" CAPTCHA) — the user must clear it themselves: "
                   "`down`, then `up --window`. Never try to solve it.")


def fresh_home():
    """Launch and get to the home screen. Same as flow.md: reuse the running app (no force-stop),
    leave the order tracker via Navigate Up, and clear stale screens with back ×3 + relaunch."""
    def launch():
        d.adb("shell", "monkey", "-p", d.PKG, "-c", "android.intent.category.LAUNCHER", "1")

    def state(ns):
        bot_check(ns)
        if by_id(ns, "welcome_login_message_button") or by_id(ns, "email_heading"):
            raise Stop("logged out — the user must sign in (`up --window`); never type credentials")
        if by_id(ns, "order_num") or by_id(ns, "complex_dialog_toolbar"):
            n = has(ns, "Navigate Up")
            if n:
                say("left the order tracker")
                tap(n, 1.0)
            return None
        return has(ns, "Restaurants, dishes, grocery")
    launch()
    try:
        return wait_for(state, 8, "the home screen")
    except Stop as e:
        if "bot check" in str(e) or "logged out" in str(e):
            raise
    say("not home — back ×3 and relaunch")
    for _ in range(3):
        d.adb("shell", "input", "keyevent", "4")
        time.sleep(0.4)
    launch()
    return wait_for(state, 25, "the home screen")


def open_location(restaurant, search):
    tap(search)
    wait_for(lambda ns: any(n["focused"] for n in ns), 8, "the search field")
    d.adb("shell", "input", "text", "Meal%sEquivalency")
    d.adb("shell", "input", "keyevent", "66")
    want = restaurant.lower()

    def card(ns):
        return next((n for n in by_id(ns, "restaurant_card_view") if n["desc"].lower().startswith(want)), None)
    _, c = wait_for(lambda ns: card(ns) or (by_id(ns, "restaurant_card_view") and "list"), 15, "search results")
    for _ in range(4):
        if c != "list":
            break
        d.swipe("down")
        c = card(nodes()) or "list"
    if c == "list":
        raise Stop(f'"{restaurant}" not in the Meal Equivalency search results')
    tap(c)
    ns, _ = wait_for(lambda ns: by_id(ns, "header_restaurant_name"), 15, "the restaurant page")
    if text_of(ns, "status_label").lower() == "closed" or by_id(ns, "empty_menu_item_title"):
        raise Stop(f"{restaurant} is closed / its menu isn't available right now")
    say(f"opened {restaurant}")


def open_item(item):
    want = item.lower()

    def card(ns):
        cs = [n for n in by_id(ns, "card") if n["desc"].lower().startswith(want + ".")
              or n["desc"].lower().startswith(want + ",") or n["desc"].lower() == want]
        return cs[0] if cs else None
    prev, c, ns = None, None, []
    for _ in range(14):
        ns = nodes()
        c = card(ns)
        if c:
            break
        s = sig(ns)
        if s == prev:
            break
        prev = s
        d.swipe("down")
    if not c:
        raise Stop(f'item "{item}" not found on the menu (removed, renamed or time-of-day menu)')
    x0, y0, x1, y1 = c["b"]
    if "out of stock" in c["desc"].lower() or any(
            x0 <= n["xy"][0] <= x1 and y0 <= n["xy"][1] <= y1 for n in by_id(ns, "out_of_stock")):
        raise Stop(f'"{item}" is out of stock')
    tap(c)
    wait_for(lambda ns: by_id(ns, "add_button"), 12, "the item sheet")
    say(f"opened item {item}")


def build_item(fav, notes):
    groups = scan_sheet()
    if expand_collapsed(groups):
        groups = scan_sheet()
    wanted = list(fav.get("choices") or [])
    drinks = drink_plan(groups, fav["restaurant"], notes)
    keep = wanted + drinks
    for w in keep:
        if not set_option(w):
            raise Stop(f'option "{w}" no longer exists on this item')
        say(f"picked {w}")
    # Optional groups may carry picks remembered from past orders: drop anything we didn't ask for.
    groups = scan_sheet()
    for g in groups:
        if "optional" not in g["rule"].lower():
            continue
        for o in g["options"]:
            if o["selected"] and not any(same(o["label"], w) for w in keep):
                set_option(o["label"], selected=False)
                notes.append(f'removed remembered extra "{o["label"]}"')
                say(f'deselected remembered extra {o["label"]}')
                groups = None
    if groups is None:
        groups = scan_sheet()
    for w in keep:
        if not any(o["selected"] and same(o["label"], w) for g in groups for o in g["options"]):
            raise Stop(f'"{w}" is not selected after picking')
    picked = [o["label"] for g in groups for o in g["options"] if o["selected"]]
    extra_req = [f'{g["name"]}: {o["label"]}' for g in groups if "optional" not in g["rule"].lower()
                 for o in g["options"] if o["selected"] and not any(same(o["label"], w) for w in keep)]
    if extra_req:
        notes.append("kept preselected required choice(s) not in favorites: " + "; ".join(extra_req))
    add = by_id(nodes(), "add_button")
    btn = label(add[0]) if add else ""
    if "required" in btn.lower():
        missing = [g["name"] for g in groups
                   if "required" in g["rule"].lower() and not any(o["selected"] for o in g["options"])]
        raise Stop(f"required choice(s) without a saved pick: {missing} — ask the user")
    if "add to bag" not in btn.lower():
        raise Stop(f'add button says "{btn}"')
    return picked, add[0]


def to_cart(add_btn):
    tap(add_btn)

    def after(ns):
        n = has(ns, "Start new order")
        if n and n["click"]:
            say("replaced stale cart (Start new order)")
            tap(n, 1.0)
            return None
        return next((x for x in by_id(ns, "order_button") if "items in order" in x["desc"].lower()), None)
    _, ob = wait_for(after, 15, "the item to land in the bag")
    m = re.search(r"items in order:\s*(\d+)", ob["desc"], re.I)
    if m and m.group(1) != "1":
        raise Stop(f"bag has {m.group(1)} items, expected 1 — remove the others by hand")
    tap(ob)
    wait_for(lambda ns: has(ns, "Proceed to checkout"), 15, "the cart")
    say("in cart (1 item)")


def add_instructions(text):
    _, f = wait_for(lambda ns: next(iter(by_id(ns, "campus_order_instructions_input")), None), 8,
                    "Order instructions")
    tap(f, 0.6)
    d.adb("shell", "input", "text", text.replace(" ", "%s").replace("'", r"\'"))
    time.sleep(0.5)
    if not has(nodes(), text[:20]):
        raise Stop("order instructions did not stick")
    say("added order instructions")  # never press back here: it discards the text


def checkout_screen(ns):
    return has(ns, "Review your pickup order") and by_id(ns, "current_payment_description")


def tender_ok(ns):
    desc = text_of(ns, "current_payment_description")
    return "meal equivalency" in desc.lower() and not has(ns, "Not accepted")


def fix_payment(ns):
    tap(by_id(ns, "current_payment_description")[0])
    ns, _ = wait_for(lambda ns: by_id(ns, "tenderName"), 12, "the payment picker")
    msgs = by_id(ns, "message")
    tenders = sorted(by_id(ns, "tenderName"), key=lambda n: n["xy"][1])
    choice = None
    for i, t in enumerate(tenders):
        nxt_y = tenders[i + 1]["xy"][1] if i + 1 < len(tenders) else 10 ** 6
        m = next((m for m in msgs if t["xy"][1] < m["xy"][1] < nxt_y), None)  # this tender's message line
        if "meal equivalency" in label(t).lower() and not (m and "not accepted" in label(m).lower()):
            choice = t
            break
    if not choice:
        raise Stop("no Meal Equivalency tender is accepted here: " + "; ".join(label(t) for t in tenders))
    tap(choice, 0.6)
    save = next((n for n in nodes() if label(n).lower() == "save"), None)
    if not save:
        raise Stop("no Save button in the payment picker")
    tap(save)
    say(f"payment → {label(choice)}")
    return wait_for(checkout_screen, 12, "checkout after changing payment")[0]


def gate(ns):
    """The payment gate: meal-equivalency tender, total 1M, no dollar charge anywhere. Returns a summary dict."""
    seen = list(ns)
    for _ in range(3):  # also read whatever is below the fold
        d.swipe("down")
        more = nodes()
        new = [n for n in more if label(n) and label(n) not in {label(x) for x in seen}]
        seen += new
        if not new:
            break
    texts = [label(n) for n in seen if label(n)]
    problems = []
    tender = text_of(seen, "current_payment_description")
    if "meal equivalency" not in tender.lower():
        problems.append(f'tender is "{tender}"')
    if any("not accepted" in t.lower() for t in texts):
        problems.append('"Not accepted by this shop" is shown')
    total = text_of(seen, "total_text_view")
    if total != "1M":
        problems.append(f'total is "{total}", not 1M')
    dollars = sorted({a for t in texts for a in re.findall(r"\$\s?(\d+(?:\.\d\d)?)", t) if float(a) > 0})
    if dollars:
        problems.append("dollar amounts on screen: " + ", ".join("$" + a for a in dollars))
    if not any("pickup" in t.lower() for t in texts):
        problems.append("not a pickup order")
    if problems:
        raise Stop("payment gate failed: " + "; ".join(problems))
    eta = text_of(seen, "checkout_campus_pickup_eta") or text_of(seen, "restaurant_header_eta")
    return {"tender": tender, "total": total, "eta": eta, "where": text_of(seen, "pickup_banner_primary_text")}


def place_button():
    for _ in range(3):
        n = next((x for x in nodes() if "place your pickup order" in label(x).lower()), None)
        if n:
            return n
        d.swipe("down")
    return None


# ---------- commands ----------

def prepare(key):
    fav = load_favorite(key)
    notes = []
    _, search = fresh_home()
    say("home")
    open_location(fav["restaurant"], search)
    open_item(fav["item"])
    picked, add_btn = build_item(fav, notes)
    to_cart(add_btn)
    if fav.get("instructions"):
        add_instructions(fav["instructions"])
    _, btn = wait_for(lambda ns: has(ns, "Proceed to checkout"), 8, "Proceed to checkout")
    tap(btn)
    ns, _ = wait_for(checkout_screen, 15, "checkout")
    if not tender_ok(ns):
        ns = fix_payment(ns)
    g = gate(ns)
    if not place_button():
        raise Stop('no "Place your pickup order" button on checkout')
    print("READY (nothing placed)")
    print(f'restaurant: {fav["restaurant"]}')
    print(f'item: {fav["item"]}')
    print("options: " + (", ".join(picked) or "none"))
    print(f'payment: {g["tender"]} · total {g["total"]} · no $ charge')
    print(f'pickup: {g["eta"]}' + (f' · {g["where"]}' if g["where"] else ""))
    if notes:
        print("notes: " + "; ".join(notes))
    print(f"took {time.monotonic() - T0:.0f}s")


def place():
    ns = nodes()
    if not checkout_screen(ns):
        raise Stop("not on the checkout screen — run `prepare` again (nothing was placed)")
    if not tender_ok(ns):
        raise Stop("checkout tender is not an accepted Meal Equivalency — nothing was placed")
    gate(ns)
    btn = place_button()
    if not btn or "1m" not in label(btn).lower():
        raise Stop(f'place button missing or not 1M: "{label(btn) if btn else ""}" — nothing was placed')
    tap(btn)  # exactly once, whatever happens next
    say("tapped place order (once)")
    end = time.monotonic() + 45
    while time.monotonic() < end:
        if by_id(nodes(), "order_num"):
            break
        time.sleep(1)
    else:
        print("UNCERTAIN: no confirmation screen after 45s. Check the Orders tab — NEVER tap place again.")
        sys.exit(UNCERTAIN)
    time.sleep(1.5)
    ns = nodes()
    num = re.sub(r"(?i)order\s*", "", text_of(ns, "order_num")).strip()
    state = text_of(ns, "order_state")
    print("PLACED")
    print(f"order: {num}")
    print(f'pickup: {state.split("|")[0].strip() or text_of(ns, "pickup_time")}'
          + (f' ({state.split("|", 1)[1].strip()})' if "|" in state else ""))
    if text_of(ns, "pickup_address"):
        print(f'at: {text_of(ns, "pickup_address")}')


def main(cmd, args):
    try:
        if cmd == "prepare":
            if not args:
                raise Stop("usage: prepare <favorite id or name>")
            prepare(" ".join(args))
        else:
            place()
    except Stop as e:
        print(f"STOP: {e}")
        print("--- screen ---")
        try:
            d.screen()
        except SystemExit:
            pass
        sys.exit(STOP)
