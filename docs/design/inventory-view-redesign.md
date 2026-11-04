# Inventory view redesign: audit and spec

Plugin `inventory` 0.4.2, shown as a merchantAgent rail view. Requested by Josh, 2026-11-02.
Status: **approved 2026-11-02.** This repo is public, so dollar figures from the real inventory are redacted to `$NN`, and the interactive preview (built on real data) is not committed.

Inputs: `inbox/inventory-glowup/` (BRIEF.md, plugin-README.md, `view-0.4.2-live.py`, `real-items.json`
with 101 items, `real-lots.json` with 20 lots, and five screenshots).

> **Evidence.** The audit comes from the live 0.4.2 source and the real data. I couldn't open the
> screenshots myself (the image tool timed out). Claude Code read desktop-1440-items and iphone-390-items
> on 2026-11-02, and they confirm findings 2, 3 and 6. Measured at 390px with real data, the page is
> 11,718px tall, and Status, Target and the actions are off screen. The Lots, Sales and Activity
> screenshots haven't been checked by eye yet.

## Numbers this spec designs around (from the real data)

| | |
|---|---|
| Items | 101: 46 `planned`, 33 `available`, **0 `listed`**, **0 `pending`**, 16 `sold`, 5 `kept`, 1 `withdrawn` |
| Lots | 20. Total cost $NN. 11 of the 20 have a blank `acquired_on` |
| Unsold at target | $NN (planned $NN + available $NN) |
| Systems | Blood Bowl 42, Warhammer 40K 36, Age of Sigmar 11, Kill Team 6, Warcry / Age of Sigmar 4, Hobby supplies 1, BattleTech 1 |
| Conditions | `NoS` 64, `New on sprue` 12, `NIB` 7, plus 9 other spellings of "new" |
| Pricing | Every unsold item has a target and a basis. 53 price dates are from 2026-09 |
| Longest name / id | 70 chars ("Ogor Mawtribes Battletome (Army Set special edition) + warscroll cards"), next 62 and 55 / 36 chars (`helsmiths-hashut-army-box-2026-09-13`) |
| Qty > 1 | Green Stuff ×18, Ogor Gluttons ×2, Ork Nobz ×2 |
| Bins, listings | None recorded. 7 eBay sales have no listing behind them |

Reference lot used below: **Warcry: Pyre and Flood** (`WARCRY-PYREFLOOD-2026-10`), $NN. Two units kept
(Pyregheists, Warband Tome) and two to sell: Idol of the Old Ones at $NN and Ydrilan Riverblades at
$NN. Remaining at target is $NN and projected net is +$NN.

---

## 1. Audit of the current view (ranked)

Severity: **P0** breaks a real task or the DS contract. **P1** adds heavy cognitive load. **P2** is polish.

### P0

1. **The view is built around tables, not questions.** The tabs are Items, Lots, Sales and Activity, one per
   SQLite table (all four desktop screenshots). None of them answers what Josh actually asks: *what do I
   have to sell, where is it, what's next to list, did this box pay off?* This is the "every capability
   shown equally" failure he rejected on 10-30.
2. **It doesn't fit the real panel width.** The Items table has 11 columns (select, ID, Item, System, Qty,
   Status, Low, Target, High, Basis, actions), and every cell is `white-space:nowrap`. The action cell
   alone holds 5 buttons. At about 495px, and at 390px (iphone-390-items), the table scrolls sideways and
   pushes Target and every action off screen. The filter bar is three selects at `min-width:140px` plus
   a search field, so it wraps onto two or three rows. The 5-tile stat strip then gets about 90px per
   tile, which is too narrow for "$NN".
3. **Every row carries a raw status `<select>`** with all seven statuses. Picking `sold` is caught and
   redirected to the dialog, which is good. Every other change is one mis-tap away on a phone, with no
   undo and no reason recorded, and the control sits on 101 rows at once.
4. **DS contract breaks in the source.**
   - Literal font sizes: 13/12/11px are hardcoded throughout. They should be `--pl-font-size-sm`, `-xs`
     and `-2xs`.
   - Off-scale padding: `6px 9px` (9px is ruled out, so it snaps to 8 or 10) and `1px 5px`.
   - Literal radius `4px` where `--pl-radius` exists. Literal `gap:4px`.
   - DS classes restyled by the app: `.bar .pl-input`, `.bar .pl-select`, `.stats .pl-stats`,
     `.selbar .pl-kbd`, `.pl-toast-stack`, `.pl-table td`.
   - `.kicker` rebuilds `pl-eyebrow` by hand.
   - `×` and `✕` text glyphs stand in for icon buttons.
   - When the kit import fails, a **tokenless `fetch` fallback** takes over. The plugin-view guide says to
     fail loudly instead (protoAgent #2392), because the fallback only produces confusing 401s.
5. **Nothing records where an item physically is, and nothing records listings.** There's no location
   field. A listing is a URL typed into a dialog, so 7 eBay sales show 0 listings. The scan, bin and
   listing work in the brief has nowhere to go in the current IA.

### P1

6. **The global stat strip is always on and rarely useful.** Lot cost, Remaining, Realized, Projected and
   Items sit above every tab, including Activity. The "Items: all priced" tile always reads all priced
   on real data (0 unsold items lack a price), so it never carries a signal. P&L only matters for one
   lot at a time. It belongs on Lots.
7. **The header gives equal weight to rare and frequent actions.** Import CSV, Export CSV, + Lot and
   + Item all sit in the header (all screenshots). Import and Export are rare. "+ Item" by hand is the
   path the scan flow replaces. Publish is described in the README but has no clear place.
8. **ID is the second column.** Ids are up to 29 chars, mix three naming schemes (`BB2-007`,
   `WC-PF-IDOL`, `helsmiths-hashut-army-box-2026-09-13`) and mean nothing to Josh in a scan. They take
   width that Target needs.
9. **Low / Target / High take three columns** for a band he decides once and rarely reads again. Basis is
   cut to 28ch, so it shows the start of a sentence ("eBay sold comps THIN. I filtered them by hand to 5
   standalon…") without enough to judge it.
10. **Editing has no visible affordance.** Name, category, condition, system and qty are edited by
    double-click, signalled only by a hover background. There's no double-click on touch, so that editing
    doesn't exist on the iPhone.
11. **Sales and Activity are full tabs for history.** Activity (desktop-1440-Activity) dumps raw
    `JSON.stringify(changes)`. Sales (desktop-1440-Sales) repeats item names with ids underneath. Both
    are reference data, opened rarely, and they get the same weight as Items.
12. **Empty states have nothing to act on.** The status filter offers Listed and Pending, which hold 0
    items. The Lots tab shows `Edit` and `✕` on every lot, and `✕` only works on an empty lot.

### P2

13. **Lot P&L is correct but hard to read.** The Lots tab (desktop-1440-Lots) puts the remaining low–high
    band on a sub-line in the same column as target. Projected net is signed, but nothing makes a loss
    stand out. Status colour, `--pl-color-status-error`, should be used for meaning here.
14. **Toasts sit bottom-right.** On the phone they land on top of the bottom thumb zone, where the scan
    action will go.
15. **Data hygiene the view could show without fixing.**
    - 11 lots have no date.
    - Condition is spelled 12 ways. That leaks straight into Copy list: "…Riverblades (10, Lumineth
      warband) New on sprue — $NN" next to "… NoS — $NN".

    The Copy list **format** must not change (brief). Condition is fixed as data: suggest `NoS` when the
    condition is entered. Never rewrite it on copy.
16. **Dead code.** `mdLine`, `mdList` and the markdown-bullet format are leftovers that contradict the
    format Josh hand-edited. Remove them so nobody revives them.

### Keep (things that already work)

- **Copy list**: the `chatDoc` format, the ⌘C shortcut, re-reading from the server before copying, and the
  textarea fallback when the clipboard is blocked. Keep it **byte for byte**.
- The Sold dialog, with the net formula shown in it.
- The Price dialog requiring a basis, plus the optional evidence block.
- The un-sell confirm, and the delete confirm that steers toward `withdrawn` or `kept`.
- The `inflight` guard against stale renders, and the slug-aware `BASE`.

---

## 2. Information architecture

### Organizing idea

The view is organized around **where a piece is in its life**: in a box, binned, listed, sold, packed. It is
**not** organized around which table the piece is stored in. Each tab answers one question Josh asks:

| Tab | The question it answers | Replaces |
|---|---|---|
| **Next** (default) | "What needs me right now?" | nothing, it's new |
| **Stock** | "What do I have to sell, and for how much?" | Items |
| **Bins** | "Where is it?" | nothing, it's new |
| **Lots** | "Did this box pay off?" | Lots, plus the global stat strip, plus Sales |

Four short labels fit `pl-tabs` at 390px without scrolling. **Sales** and **Activity** stop being tabs. A
sale shows inside its lot's detail and inside the item's history. The audit log becomes **History** in the
overflow menu, shown as readable lines ("Price set on Riverblades: $NN basis eBay sold comps") instead
of JSON.

### Header

- Title "Inventory". Drop the "Source of truth" kicker, which only takes space.
- One primary action, **Scan**, which opens the scan flow (§3). On the phone it's a full-width button
  pinned at the bottom of the screen, in the thumb zone.
- One `pl-menu` overflow button holding the rare actions: Add item by hand · New lot · Import CSV ·
  Export CSV · Print bin labels · Publish site… · History.
- **Nothing settings-like on the view.** The weekly review, its cron and timezone, `site_dir` and the
  publish flags belong in Settings ▸ Plugins ▸ inventory.

### Next: a work queue, not a dashboard

Next shows a stack of sections, and **a section only appears when it has something to act on**. That
answers the "permanent sections" and "controls with nothing to act on" failures. The order is by urgency:

1. **To pack.** Sold items that haven't been picked yet, with each item's bin (§4 pick list). Empty today.
2. **Needs a look.** Scans the agent couldn't identify with confidence, or failed to identify (§3).
3. **Ready to list.** Items that are priced and available, with no listing yet. Each row shows its draft
   state (§5). Today that's 33 items.
4. **To split.** `planned` pieces, grouped by lot, with one row per box: "Blood Bowl Collection · 30 to
   split". Today that's 46 pieces in 6 lots.
5. **Housekeeping.** A single line for each count that is too big to list: "79 items have no bin · Start
   binning", "53 prices are from September · Ask for a re-price".

A section shows at most 5 rows. Its "See all N" link opens Stock, already filtered to that set. If every
section is empty, Next shows one `pl-empty`: "Nothing waiting. Scan a box to start." with the Scan
button beside it.

"Ask for a re-price" and any other step that needs the agent goes through `protoagent:chat:compose`. It
**drafts** the request in chat, and Josh sends it. The view never sends to the agent silently.

### Stock

- **Lifecycle filter** as a `pl-segmented` control: **For sale** (default: available, listed and pending)
  · **To split** (planned) · **Sold** · **All**. Kept and withdrawn only appear under All. The raw
  seven-value status select goes away.
- Search, plus **one** filter button that opens a `pl-popover` for System, Lot and Bin. Each active filter
  appears as a removable `pl-chip`. This replaces the three always-visible selects.
- The list is grouped by **system**, the same grouping Copy list uses, so what Josh sees is what he
  copies.
- Multi-select and **Copy list** work exactly as they do today (§6).

### Bins

Covered in §4.

### Lots

The P&L lives here and only here. It's a list of lots, and tapping one opens its detail, which shows the
four numbers Josh reads (§6).

### Item detail: one surface, used everywhere

Tapping any item row anywhere in the view opens the same detail. On desktop it's a `pl-drawer--right`,
and below 640px it's a `pl-drawer--bottom` that fills the screen. Every per-item action that today sits
on the row moves here: Price, Sold, Listing, Edit, Keep/Withdraw, Delete, Photos, Public, History.

That removes the 5 buttons per row, the status select, and the double-click editing that touch screens
can't do.

---

## 3. Phone-first scan flow: from sealed box to binned units

Target: an iPhone at 390px, one-handed, with the bin box in the other hand. Every screen has **one primary
action**, in the bottom thumb zone, at least 44×44px (WCAG 2.5.5 AAA target size, used here as the house
minimum for this view).

### Capture mechanism (no viewfinder)

The flow captures with `<input type="file" accept="image/*" capture="environment">`, styled as a
`pl-btn--primary` through its `<label>`. iOS opens the native camera, Josh takes the photo, and the image
comes back as a File. Uploads go through `apiFetch` to the existing raw-body photo route
(`items/{id}/photos`, which handles HEIC and strips metadata) or to a new scan route (below). There is no
live preview, no barcode overlay and no `getUserMedia`. A viewfinder would need `camera` added to
`PLUGIN_FRAME_ALLOW` in core, and this spec doesn't depend on that.

### Backend this flow needs (new, not in 0.4.2)

- **Scan record:** `scans` with `id, photos[], kind (box|unit), state (identifying|proposed|confirmed|failed),
  proposal {name, system, category, condition, confidence, alternatives[≤3], comps}, lot_id, item_id`.
- **Identification runs server-side, asynchronously.** The view can't send to the agent: `chat:compose`
  only drafts. So the scan route starts a one-off agent turn itself with
  `sdk.run_in_session(session_id, prompt, job_id="scan-<id>")`, which fires about 1s later (verified on
  core main, 2026-11-02). The photos reach the model through a plugin tool, `inventory_scan_photos`,
  which returns `multimodal_tool_result(caption, images=[{"path": p}])`. The turn writes its proposal
  back through an `inventory_scan_propose` tool. There's no cron sweep.
- When a proposal is ready, the plugin emits `inventory.scan.proposed`. The view subscribes through
  `protoagent:subscribe`, with `since` so it replays anything it missed, and updates without polling.
- Barcodes are a bonus. If the box photo contains a readable EAN/UPC, the agent uses it. There's no
  separate barcode screen.

### Screen by screen

**S1. Scan** (the bottom-sheet drawer that opens from the Scan button). Two large choices, as
`pl-radiocard`s:
- **New box I bought.** Starts a lot (go to S2).
- **Single piece.** Adds one unit, into an existing lot or none (go to S4 with one slot).

A third, smaller link, **Scan a bin label**, opens the label-photo capture from §4. Once the core
deep-link change ships, the iPhone Camera app opens labels directly.

**S2. The box** (one screen, three fields):
1. **Photo of the box.** The capture button. The thumbnail shows once it's taken.
2. **What did you pay?** A number input with `inputmode="decimal"`, required. This becomes the lot cost.
3. **Where from?** Optional, with recent sources offered as suggestions.

Primary: **Identify**. It creates the lot right away with a provisional name ("Box scanned 11-02 14:20"),
so nothing is lost if he walks away, and moves on to S3. **He never waits on S2.**

**S3. Box identification.** Pending, then proposed.
- *Pending:* a `pl-skel-lines` placeholder for the name, the text "Identifying… usually under a minute",
  and the photo. **He can keep going.** "Break it down" is already available, so he can take unit photos
  while the box is still identifying.
- *Proposed, high confidence:* the name ("Warcry: Pyre and Flood") as a heading, the system as a `pl-tag`,
  and one muted line explaining the confidence ("Matched box art and title"). Primary: **Looks right**.
- *Proposed, low confidence* (below the threshold the plugin sets): a `pl-callout--warning` saying "Not
  sure. Pick one or type it", then up to 3 alternatives as `pl-radiocard`s, then a text input with
  suggestions from existing names. **There's always a typing escape.** One tap on an alternative confirms
  it.
- *Failed:* a `pl-callout--error` with "Couldn't identify". Primary: **Type the name**. Secondary:
  **Retake photo**.

A wrong guess costs **one tap** (an alternative) or **one field** (typing). The correction is stored on
the scan, so the agent can learn from what it got wrong.

**S4. Break it down: keep this, sell that.** This is the core screen, and it reproduces Josh's own sentence
("keeping pyregheists, selling terrain and ydrilian half. bought for 101").
- The lot header on one line: name · $NN cost.
- A list of **units**. If the agent knows the box contents, it pre-fills them as `proposed` rows. Otherwise
  each unit starts empty, and Josh adds them with **+ Photo of a piece** (capture, then a new row in
  `identifying`).
- Each row shows the thumbnail, the name (or a skeleton while identifying), and a **two-option
  `pl-segmented` control: Keep | Sell**, where Sell is the default. That is the whole decision per unit.
  Status follows from it: Keep → `kept`, Sell → `planned`, which becomes `available` once it's binned (S6).
- A low-confidence row shows a `pl-badge--warning` "Check" and opens the same correction sheet as S3.
- A row that's still identifying doesn't block anything. It just stays pending.
- A running footer shows **"2 to sell · 2 kept"**, plus the estimated value at target once comps arrive.

Primary: **Price the sell pile**.

**S5. Prices** (only for Sell units). Each row shows the name and the proposed target, with the basis as one
muted line ("eBay sold comps, 22 sold, 10-29"). The basis is **never** empty here: a unit without comps
shows "No comps yet. Ask the agent" and gets no number. Tapping a row opens the existing Price dialog, which
already requires a basis. **Accept all** sets the proposed targets with their recorded bases through the
existing `items/{id}/price` route. The lot's projected net shows live at the top (+$NN for Pyre and
Flood).

**S6. Bin them.** For each Sell unit, the screen shows "Put it in a bin". The first bin of the session is
picked from a list of recent bins. After that, the default is **the same bin as the last unit**, so one bin
takes one tap per unit. **Done** closes the flow and shows a toast at the top, clear of the thumb zone:
"Pyre and Flood: 2 binned in B07, 2 kept". Kept units can optionally go to a bin as well.

He can leave any screen. The lot and its scans persist in their current state, and the unfinished work
shows up on **Next ▸ Needs a look** or **To split**, ready to resume from the same screen.

---

## 4. Bins

### Model (new)

- `bins`: `id` (short and human, `B01`…`B99`), `label` (optional, e.g. "Blood Bowl overflow"), `note`,
  `created_on`.
- `items.bin_id`, nullable. A move is an audited update with actor and from/to, so History shows "Moved
  Riverblades B03 → B07".
- An item has **one** bin, not one per unit. Green Stuff ×18 sits in one bin. Splitting a quantity across
  bins is out of scope until it comes up.

### The QR deep link

The label encodes a URL. The iPhone Camera app reads it and Safari opens it, already paired to the hub:

`https://<hub>/agents/merchantAgent-6604/<console route that opens the inventory view>?bin=B07`

Two real constraints:

1. **The URL must open the console, not the bare plugin page.** The bare page
   (`/agents/merchantAgent-6604/plugins/inventory/view`) gets its bearer only through the console's
   `protoagent:init` handshake, so opened on its own every gated call 401s. **Verified 2026-11-02:** no
   console route opens a rail view by URL, and `PluginView.tsx` builds the iframe `src` from the view path
   alone, so no query string gets through. **A small core change will add this.** Labels encode the
   plugin's own stable redirect, `/agents/merchantAgent-6604/plugins/inventory/b/B07`. Until the core
   change lands, the redirect points at the inventory view with no bin. After it lands, the redirect
   forwards to the new console route with `bin=B07`. The printed URL never changes.
2. Once params are forwarded, the view reads `bin` from its own `location.search` on boot and opens
   **Bin detail** directly, skipping Next.
3. **Fallback that needs no core change: Bins ▸ Scan a label.** It's a capture button
   (`capture=environment`) that photographs the label and resolves it in the view. The plugin decodes the
   QR server-side, and if that fails, reads the large printed bin id from the photo. The result opens Bin
   detail. A wrong or unreadable label shows "Couldn't read that label", with a bin picker as the escape.
   This is the path until core ships, and a backup after.

### Screens

- **Bins tab.** One row per bin: id, label, item count, value at target. Sorted by id. Rows with no bin
  collapse into one line at the top, "79 items not in a bin", which opens Stock filtered to no bin.
  Empty state: "No bins yet. Make labels to start." with **Print labels**.
- **Bin detail** (where a scanned label lands). The header is "B07" plus its label and count. Below it,
  the items in the bin, each with name, condition and target. Actions:
  - **Move items here**: the primary. Opens Stock in selection mode filtered to *not in this bin*. Pick
    rows, then **Move to B07**.
  - **Put a piece in** on the phone: capture a photo, the agent matches it to an existing item (pending
    state as in §3), confirm, and it's moved. This is the "scan to move" path, since sprues have no code.
  - Select items here, then **Move to…** (a bin picker) to empty a bin.
  - **Copy list** works here too, so "post everything in B07" is two taps.
- **Pick list.** When a sale is recorded (the existing Sold dialog), the item goes into **Next ▸ To pack**
  with its bin, grouped by bin so one walk covers several orders: "B03: Riverblades · B07: Idol of the Old
  Ones". Tick **Packed** to clear `bin_id` and take it off the list. Sold items with no bin still show,
  marked "no bin recorded", so the list is never wrong by omission.
- **Label sheet** (overflow ▸ Print bin labels). Pick a range ("B01–B24") or "the bins I just made". It
  produces a printable page: a grid of labels sized for plain US Letter, each with the QR, the bin id in
  large type (`--pl-font-size-display`, mono) and the label text. Black on white whatever the console
  theme, because it's paper. **`window.print()` doesn't work in the frame**, because the sandbox has no
  `allow-modals` (verified 2026-11-02). The frame does allow `allow-popups` and
  `allow-popups-to-escape-sandbox`, so **Open label sheet** opens the sheet in a new tab, and he prints
  from there (⌘P, or Safari's share sheet ▸ Print). The new tab is a plugin page that has to work without
  the handshake. So the view first fetches a short-lived signed URL for the sheet through `apiFetch`, and
  opens that. The sheet is the one place that uses fixed print colours, through `@media print`.
- QR generation stays inside the plugin, a small vendored encoder or server-side. No third-party QR
  service, since the hub is on a tailnet.

---

## 5. Listing drafts

### Where they live

- **Next ▸ Ready to list** is the queue: priced, available items with no live listing.
- The **Listing** section of item detail holds the draft itself. There's no separate Listings tab, because
  a draft only makes sense next to its item and its photos.

### Draft contents (agent-written, never posted)

- **Title**, capped at eBay's 80 characters, with a live counter. Example: "Warhammer Warcry Ydrilan
  Riverblades 10 Lumineth Realm-lords New on Sprue NoS".
- **Price**: the item's target. Shown with its basis and **not editable here**. A change goes through the
  Price dialog, so it keeps a basis.
- **Description**: plain text, the condition stated first, then what's included, from the scan photos.
- **Photos**: the item's photos in order, cover first.
- The draft is generated server-side by the same async mechanism as identification (§3), so it shows a
  pending state ("Drafting…") and an `inventory.listing.drafted` event.

### Listing state (extends the existing `listings` table)

`draft → copied → live → ended | sold`.

- `copied` is set when Josh taps a copy button, so Next can show "copied but not confirmed live" a day
  later.
- `live` needs the eBay item URL. That's the one thing that turns a draft into a recorded listing, and
  it's what moves the item to `listed`.
- Recording a sale already closes live listings. Keep that.

### The paste-to-eBay step (on the phone, eBay app or Safari beside it)

1. Item detail ▸ Listing. Three copy rows, each a `pl-btn--sm` with the text beside it: **Copy title** ·
   **Copy price** · **Copy description**. Each copies to the clipboard (`clipboard-write` is allowed in
   the frame) and shows "Copied" on the button for 2s. There's no toast stack in the thumb zone.
2. **Download photos**: fetches the item's photos (already stripped of metadata) through `apiFetch` and
   offers them as a download. On iOS, Safari's download gives him Files ▸ Save to Photos, which the eBay
   app can pick from. If the frame blocks the download, it falls back to a signed URL opened in a new tab
   (`allow-popups`, as with the label sheet). Long-press saving is unproven inside the iframe, so the
   design doesn't rely on it.
3. He lists it on eBay himself.
4. Back in the view: **I listed it**, then paste the eBay URL (a single input, with a paste hint) and
   **Save**. The listing becomes `live` and the item `listed`.

Nothing in this flow posts anywhere, and there's still no publish tool for eBay.

---

## 6. Desktop table (Stock), plus lot detail

### Width tiers

These are measured against the iframe's own width, not the window's, so they use container queries or
`matchMedia` on the frame.

| Frame width | Layout |
|---|---|
| < 640px (390px phone, 495px console panel) | **List rows**, no table. Line 1 is the name, plus a "Check" `pl-badge` if one applies. Line 2, muted: condition · bin · status. The target sits on the right in mono. The row is at least 44px tall. Tapping the row opens item detail. Tapping the checkbox selects it. |
| ≥ 640px | `pl-table` with `pl-tr--interactive` rows and the columns below |

### Columns that stay (≥ 640px)

| Column | Notes |
|---|---|
| Select | Checkbox with `aria-label="Select <name>"`, as today. Header checkbox selects every visible row. |
| Item | The name, which wraps (never `nowrap`, because names run to 70 chars), with condition as a muted sub-line. Category only shows in detail. |
| Bin | `B07`, or a muted "—". Sortable. |
| Status | A read-only `pl-badge`: available (neutral), listed (info), pending (warning), sold (success), planned as "to split" (neutral). **It is not a select.** |
| Target | Mono, right-aligned, whole dollars as the default display. The low–high band appears in the tooltip and in detail. |
| Lot | **Only at ≥ 1000px**, as the lot name, never the id. |

Grouping by system stays, with a group header row per system, ordered the same way Copy list orders them.

### What moves into item detail or behind a selection

| From the 0.4.2 row | Goes to |
|---|---|
| ID column | Detail header, muted mono, with a copy button |
| Low / High columns | Detail ▸ Price, as a band with its basis shown in full (not cut off), date and comps n |
| Basis column | Detail ▸ Price. The row shows a `pl-badge--warning` "Stale" when the price is over 45 days old (threshold set in Settings) |
| Qty inline edit | Detail ▸ Edit. The row shows "×18" after the name when qty > 1 |
| Status `<select>` | Detail actions: **Record sale** (the existing Sold dialog, unchanged), **Keep**, **Withdraw**, **Return to stock** (with the existing un-sell confirm). `listed` is only set through §5, and `sold` only through the Sold dialog. |
| Price / Sold / List / Edit / ✕ buttons | Detail. Delete goes at the bottom of Edit as `pl-btn--danger`, with the existing confirm text |
| Double-click editing | Detail ▸ Edit: one form with visible fields, which works on touch |

### Selection bar (replaces `.selbar`, keeps its job)

When rows are selected, a bar pins to the **bottom** of the list. It's a plain `pl-row`, styled with no
app overrides on DS classes. It shows "N selected", **Copy list** (primary), **Move to bin…**, then
**Clear**, with the `pl-kbd` ⌘C hint at ≥ 640px.

**Copy list is unchanged**: same `chatDoc` / `chatLine` / `chatPrice`, the same server re-read before
copying, the same textarea fallback, the same ⌘C rule (a real text selection or a focused field still
wins). Acceptance test: copying a fixed selection gives byte-identical output in 0.4.2 and in the
redesign.

Bulk **status** changes are deliberately not offered.

### Lots tab and lot detail

- **Lot rows:** name, date (or a muted "no date"), items as "4 · 2 sold", and **projected net** in mono.
  A negative projected net uses `--pl-color-status-error` text plus a "−" sign, so it doesn't rely on
  colour alone (WCAG 1.4.1).
- **Lot detail** (drawer). A `pl-stats` block with **exactly the four numbers Josh reads**:
  1. Lot cost
  2. Remaining at target (low–high as a sub-line)
  3. Realized net
  4. Projected net

  Below that: units grouped by Keep / To split / For sale / Sold, then **Sales** for this lot (date,
  item, channel, net; the old Sales tab's columns live here), then lot actions (Edit, Delete if empty).
- A totals row heads the Lots tab, the same four numbers across all lots. This is the old global stat
  strip, kept only here.

---

## 7. DS and accessibility requirements (for the build turn)

- Only kit classes that exist (`ds_kit_classes`): `pl-tabs`, `pl-segmented`/`pl-seg`, `pl-drawer--right`
  and `--bottom`, `pl-menu`, `pl-popover`, `pl-chip`, `pl-badge--*`, `pl-radiocard`, `pl-callout--*`,
  `pl-skel-lines`, `pl-empty`, `pl-stats`, `pl-table`, `pl-tr--interactive`, `pl-tr--selected`, `pl-kbd`,
  `pl-btn--icon`. No invented classes. Everything else layout-wise is plain flex/grid in app classes.
- `--pl-*` tokens only. Font sizes `--pl-font-size-2xs…xl`, spacing on the 4px grid with half-steps,
  radius `--pl-radius*`. **No overrides on `.pl-*` selectors.** Run `ds_check` on the page before review.
- The one allowed literal-colour exception is the printed label sheet's `@media print`.
- Kit import failure renders a `pl-callout--error` naming `/_ds/plugin-kit.js`. Remove the tokenless
  `fetch` fallback.
- Every data call goes through the one `api()` → `kit.apiFetch` shim, photo uploads included (raw body,
  same shim).
- Keyboard: tabs use arrow keys; drawers trap focus and return it to the row on close; Esc closes;
  the selection bar is reachable by Tab. Pending identification announces through an `aria-live="polite"`
  region ("Identified: Warcry: Pyre and Flood").
- Targets ≥ 44×44px on the phone tier. Status is never shown by colour alone: badges carry text.
- `prefers-reduced-motion`: no skeleton shimmer.
- Toasts sit at the top on the phone tier, clear of the Scan button.

---

## 8. Open questions and dependencies (resolve before or during build)

Answers checked against core origin/main on 2026-11-02 (`inbox/inventory-glowup/ANSWERS.md`).

| # | Question | Resolution |
|---|---|---|
| 1 | Can a plugin start a one-off agent turn? | **Resolved: yes.** `sdk.run_in_session(session_id, prompt, job_id=…)` fires about 1s later. Photos go in through a tool returning `multimodal_tool_result`. The cron fallback is dropped (§3, §5). |
| 2 | Does a console route open a rail view by URL and forward `?bin=`? | **Resolved: no**, so a small core change will add it. Labels encode `/plugins/inventory/b/<id>`. The no-core fallback is Bins ▸ Scan a label (§4). |
| 3 | Do `window.print()` and long-press saving work in the frame? | **Resolved: print doesn't** (no `allow-modals`). The label sheet opens in the operator's browser by a short-lived link (0.10.1): a new tab on the web, the system browser from the desktop app. Long-press is unproven, so **Download photos** replaces it (§4, §5). |
| 5 | Normalize condition to `NoS`? | **Resolved:** a suggestion at entry only. Josh decides on existing rows. |
| 6 | Where do the thresholds live? | **Resolved:** the stale-price and identification-confidence thresholds go in Settings. |

### Suggested build order (one concern per PR)

1. IA reshuffle with no new backend: Next/Stock/Lots, item detail drawer, list rows below 640px, DS
   cleanup, Copy list byte-identical test.
2. Bins model, Bins tab, move, pick list, Scan a label (photo fallback).
3. Label sheet (new-tab page) and the `/b/<id>` redirect. The core deep-link change is a separate
   protoAgent PR, briefed to protoEngineer.
4. Scan flow S1–S6 (`run_in_session` + `multimodal_tool_result`).
5. Listing drafts and listing state.

