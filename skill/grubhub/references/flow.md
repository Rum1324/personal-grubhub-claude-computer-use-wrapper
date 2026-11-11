# Grubhub Android app flow (Cal Dining, meal equivalency pickup)

Status: **verified end-to-end** 2026-09-24 (first real order, #87 at Monsoon Kitchens).

Last verified: 2026-10-29 (order #297 at Ladle & Leaf)

Package: `com.grubhub.android` · AVD: `grubhub` · Driver: `scripts/droid.py`

## !! Standing rule (user, 2026-10-29)
**Never disobey an order request.** When the user asks for an order (now or at a scheduled time), place exactly that order. An already-active order, a same-item order earlier today, or a "looks like a duplicate" situation is NEVER a reason to hold or ask — place it, then mention the observation in one line after. Only the hard stops in SKILL.md (cooldown < 30 min, closed, logged out, item missing, payment gate fails) may stop an order. (Incident: 2026-10-29 a scheduled order was held because #252 was active; user was furious.)

## 0. Launch / session
- Logged-out signal (verified 2026-09-22): launch shows `id=welcome_button` "Search nearby" + `id=welcome_login_message_button` "Sign in"; sign-in screen heading `id=email_heading` "Sign in or create an account" (Google / Amazon / Facebook / SSO / email). → stop, `D up --window`, user logs in.
- Logged-in home: `id=campus_name` "Order around UC Berkeley", tabs "UC Berkeley Dining" / "Off-campus", balance tile `id=tile_header`.
- Bottom nav: `D tap --exact Home|Offers|Orders|Account`.
- **Scripted path:** `scripts/order.py` (`D prepare <id>` / `D place`) encodes this file's happy path. When the app changes, update both.
- **Bot check (2026-11-11):** "We've hit a roadblock" / "Confirm that you're not a robot" with a "Press & Hold Human Challenge" button (random ids). Never solve it — `D down`, `D up --window`, user clears it.
- Popups on launch: "Your review" / "How did it go?" rating screen (`id=rr_page_title`) → `D tap "Not now"`.
- Stale screens (e.g. a restaurant "Store Info" sheet) can reopen on launch → `D key back` ×3 (to Android home) → `D launch` again.
- After an order, launch reopens the **order tracker** (`id=complex_dialog_toolbar`, `id=order_num`). `D key back` does NOT leave it — use `D tap "Navigate Up"` to reach home.
- **Cooldown check:** state.json can be stale (user also orders by hand). Always check Orders tab → Active orders → Track → "View order details" → `id=date_time` for the real latest order time. Past-orders list shows date only (`id=account_order_details` "Sep 25 • Pickup at …"); if no meal-equivalency order is dated today, no cooldown. The cooldown check is ONLY about the 30-min window; an active order older than 30 min does not block a new one.

## 1. Location list → location
- Fastest: `D tap "Restaurants, dishes, grocery"` → `D type "Meal Equivalency"` → `D key enter` → results (`id=restaurant_card_view`, desc "<name> Closed1 in list of 4 items") → `D find "<restaurant>"` → `D tap "<restaurant>"`.
- Alt (fast when present): home "Recent On Campus Orders" card, e.g. `D tap "1 item: Cheeseburger"` → popup (Express reorder / Add to new bag / **View menu**) → `D tap "View menu"`. Don't use Express reorder: it reuses the old drink/options.
- Restaurant page: `id=header_restaurant_name`, `id=status_label` ("Closed" when closed; open shows `id=header_state_indicator_state` "Open now"), `id=section_title`, items `id=menu_item_name`, price `1M`. Top section "Order Again" lists past items with previous options.
- **Opening an item (2026-10-01):** the search-results list stays in the UI dump underneath the restaurant page, so tapping the item name / `tapxy` on it can do nothing. Reliable: tap the card by its desc prefix, e.g. `D tap "1/2 Turkey Pesto Sandwich + 1/2 Soup + Beverage. 1M"` (`id=card`). `D find` only scrolls down; after overshooting use `D scroll up`.
- Sold-out items show `id=out_of_stock` "Out of stock" on the card.
- Hours: `D tap "More information"` → `D tap "See full schedule"` → `id=restaurant_info_period_title` / `id=restaurant_info_delivery_period`.
- After close the menu may be empty: `id=empty_menu_item_title` "This menu isn't available right now", or show only a subset (time-based menus).
- Closed signal: `id=status_label` "Closed"; on search/home cards `id=eta` "Closed".

## 2. Item sheet
- Header "Menu item"; `id=menu_item_name`, `id=menu_item_price` "1M"; quantity stepper.
- Groups: `id=menu_choice_name` (e.g. "Soup", "Drink", "Beverage", "Base", "Side", "Top it Off", "Protein"), `id=menu_choice_selection` "Choose one (Required)" / "Choose up to 5 (Optional)". Optional groups can be collapsed — tap the group name to expand.
- Required groups may come **pre-selected** (the app remembers past picks): `id=menu_choice_selection` shows the current pick. Always verify and `D pick` the wanted option anyway.
- Option rows: `id=menu_option_selection_root` desc "<label>, not selected, checkbox"; label text `id=menu_option_desc`. Use `D pick "<label>"` (scrolls + verifies). `D pick "Pepsi"` correctly picks "Pepsi" and not "Diet Pepsi".
- Golden Bear Café Cheeseburger (2026-09-28): Side (req): French Fries · Side Green Salad · No Side · Fruit Salad. Beverage (req): Milk · Orange Juice · Hot Tea · Coffee · Proud Source · Diet Pepsi · Pepsi · Gatorade Orange / Fruit Punch / Lemon Lime · No Beverage. Customize (optional, up to 7).
- Ladle & Leaf soups (2026-09-25, same on 2026-10-01): Minestrone, Hot & Sour Chicken, Organic Tomato Bisque, Mexican Chicken Tortilla, New England Clam Chowder, Chicken Noodle Soup, Turkey Chili, Organic Macaroni & Cheese. Drinks: Proud Source, Diet Pepsi, Gatorade ×3, No Drink (no Coke/regular Pepsi). No chicken-pesto item; Turkey Pesto is the only pesto sandwich.
- Special instructions: none on item sheet; use cart "Order instructions" field.
- Add button: `id=add_button`. Closed: "Restaurant is closed" (disabled). Open: "Make required choices (N) : 1M" until required groups are filled, then "Add to bag: 1M".
- Collapsed optional groups (e.g. "Top it Off") must be expanded by tapping the group name before `D pick` can see their rows.
- The app can reopen a stale item sheet from a previous session on launch — press `D key back` ×4 and relaunch before starting.

## 3. Cart
- **Stale cart from another location (2026-10-29):** don't try to delete it. Deleting failed twice with "Error deleting Item / unknown error", and tapping the row opens the item sheet instead. Just add the new item: "Add to bag" shows dialog "Start a new order?" → `D tap "Start new order"` → old cart is replaced. Fastest path.
- Cart entry point: after adding, restaurant page shows `id=order_button` desc "View order, items in order: N" → `D tap "View order, items in order: 1"` (right after "Start new order" the short label may not match; use the full desc).
- Screen title "Your pickup order" ("Pickup, ASAP", ready-in minutes, `Order instructions` field).
- Order instructions: tap `id=campus_order_instructions_input` (placeholder "e.g. Cilantro on the side") → `D type "..."`. Do NOT `D key back` afterwards — it closes the cart and discards the text. Tap "Proceed to checkout" directly (verify with `D screen` the text is there).
- Remove item control: trash icon in the stepper (`id=stepper_button_left` when qty 1) → dialog "Remove Item from Cart" → "Remove". May error server-side (see stale cart above).
- Checkout button: "Proceed to checkout" (`id=order_button_label`). Cart shows Items subtotal `1M`, Sales tax `$0.00`, Total `1M`.

## 4. Checkout
- Checkout title "Review your pickup order" (`D wait "Review your pickup order"`); `id=restaurant_header_eta` "Pick up in 4-14 min"; `id=pickup_banner_primary_text` "Pick up from <address>".
- Payment: `id=current_payment_description`. Historically defaulted to "Berkeley Dining Flex Dollars" with `id=campus_description_message` "Not accepted by this shop" → tap it and switch → picker "Select payment method" lists `id=tenderName` rows with `id=message`. The accepted tender (Monsoon, Ladle & Leaf, Golden Bear Café) is **"Berkeley Dining Meal Equivalency + Flex Dollars"** ("Balance unknown"). `D tap --exact` it → `D tap --exact Save`. The app remembers this choice (verified 2026-09-24/25/28, 10-01, 10-29 — it was preselected) — still re-check every time. Re-read checkout: no "Not accepted" message, total `1M`, tax `$0.00`.
- Totals: `id=total_text_view` "1M"; Sales tax "$0.00"; `id=checkout_campus_pickup_eta` "Pick up in N mins".
- Place order button: `id=order_button_label` "Place your pickup order: 1M" — tap once (`D tap "Place your pickup order"`).

## 5. Confirmation
- Confirmation screen (appears ~5 s after placing; `D wait "ORDER #"`): `id=order_num` "ORDER #925"; `id=order_state` "04:36PM | 2 people ahead of you" (pickup ETA before the `|`); `id=progress_description` "In queue"; `id=pickup_address`; `id=pickup_time` "Pickup 04:36PM"; `id=num_of_orders_in_line`.
- Cancel: in-app "Need to cancel? … Cancel now" link (`id=body`) while the order is still "In queue"; after that, contact support.
- Orders tab (fallback): `D tap --exact Orders`; cards `id=past_order_container` (the top card hides under the sticky "Past orders" header — tap a lower card or scroll first); detail shows `id=short_order_id` "Order #51", `id=date_time`, `View receipt`. Active orders section has `id=account_track_cta` "Track" per card.
