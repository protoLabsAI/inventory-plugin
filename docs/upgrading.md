# Upgrading

Version notes that change behavior, newest first. Installing or updating through protoAgent runs the
database migrations for you: every one is additive, and an older database upgrades in place on first
open. Take a copy of `inventory.db` before a big jump anyway.

## 0.16.2

- **Docs only.** The README and `docs/` follow the protoAgent plugin docs standard, and the agent skill
  and the plugin description match what the plugin does today. No behavior change.

## 0.16.1

- **One scroll area.** Only the list scrolls; the page around it no longer does. If you embed the view
  somewhere unusual, it still fills its frame.
- **The More menu uses the kit's menu items**, with a hover highlight and arrow, Home, End, Enter and
  Escape keys.

## 0.16.0

- **Scan label** sits in the view's header (beside **Scan a box** on a phone, which was called **Scan**).
  It reads an item or bin label with the camera and opens it.
- **A label link now answers with a small page, not a redirect.** A browser that is signed in to the
  console goes straight to the item or bin. One that isn't (on iPhone, a label scanned with the Camera
  app always opens Safari) sees the id and how to open it in the app. Printed labels keep working.

## 0.15.0

- **New per-item fields:** packed weight (stored in ounces) and box size, plus packaging presets. Presets
  start empty: add your own boxes and mailers under **More → Packaging**.
- **Check a bin** can flag items as missing. Missing is a flag, not a status, and clears when the item
  is found, moved, or checked again.
- **Label stocks:** Avery 5160 stays the default. Avery 5163 and two thermal roll sizes are new, and the
  last stock you used is remembered in your browser.

## 0.14.0

- **Aging stock** tiers are settings (`aging_days_1/2/3`, defaults 45, 90 and 120 days). Days count from
  the listing date when there is one, otherwise from when the item became available.
- **Sealed vs split:** a box scan's pricing turn also prices the sealed box. **Sell it sealed instead**
  replaces the planned pieces with one sealed item (audited, and refused once the box is open).

## 0.13.0

- **Put away can use the live camera** where the browser allows it (the console reached through the
  hub). Taking a photo is still there as the fallback.
- **A repeat sale paste reads as already recorded** instead of failing as an unknown item.

## 0.12.0

- **Recording a sale off-eBay no longer silently closes a live eBay listing.** The listing is marked
  `needs_end` and shows under **Next → End these listings** until you end it on eBay and tap
  **I ended it**. An eBay sale still closes its eBay listing.
- **Listing drafts get a Custom label (SKU)** row, bin first (`B03-<item id>`). Moving the item to
  another bin later shows **Update the custom label on eBay**.

## 0.11.0

- **Label first.** The last step of a box scan is **Print labels**, not a bin picker. Pieces stay out of
  any bin until you **Put away**: scan pieces' labels, then the bin's. Putting away makes a planned piece
  available, so binning is what puts it up for sale.

## 0.10.0 and 0.10.1

- **Item labels** (Avery 5160) whose QR opens the item.
- **Label sheets open in your browser** through a short-lived link (15 minutes), in the desktop app too,
  where the old blank-tab sheet did nothing.

## 0.9.1

- **The stale-price threshold is the `stale_price_days` setting**, which the weekly review already used.
  Its default is 30 days, so the view now badges more prices than its old hard-coded 45.
- **Edit no longer changes status.** Use the drawer's Record sale, Keep, Withdraw and Return to stock.

## 0.7.0 and 0.7.1

- **Bin labels with QR codes.** The plugin declares `/plugins/inventory/b/` in `public_paths` so a label
  opens without a sign-in. On a protoAgent older than 0.227.0 (no `sdk.view_url`) the link shows a page
  naming the bin instead of opening it.

## 0.6.0

- **Bins.** Every sale recorded before 0.6.0 is marked packed by the migration, so **To pack** starts
  empty.

## 0.5.0

- **The view was redesigned** around Next, Stock and Lots with one item drawer. Copy list output is
  byte-for-byte what it was.
