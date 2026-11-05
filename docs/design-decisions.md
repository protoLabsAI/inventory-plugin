# Design decisions

Why Inventory is built the way it is. What it does is in [how-it-works.md](how-it-works.md).

## One database is the record, not a spreadsheet

The inventory started as CSVs in an agent's workspace. They drifted: two copies of a sheet disagreed, a price
lost the reason it was set, and a sale typed into chat never reached the sheet. A record is only a record if
every change goes through one place with a history, so the plugin owns a SQLite file, every write is one
transaction, and every change is audited with who made it. Spreadsheets stay as import and export.

## A re-import never clobbers

Importing a sheet again fills in what's new and leaves the rest alone. A blank cell keeps the current value. A
sheet can't walk a status back from listed, pending or sold, and a sheet without ids matches rows by lot and
name. An old export dropped back in shouldn't undo a week of work.

## Money in cents, parsed strictly

Floats round wrong, and loose parsing reads `54%` as $54 and `2026-07` as $2,026. Amounts are stored as
integer cents, and anything that isn't clearly an amount is refused rather than guessed.

## A price without a reason isn't a price

The first estimates on real stock came from active asks and retail, presented as if they were sold prices.
So a target can't be set without a basis, the comps are stored with it in the same write, and a price that's
gone stale says so. When you ask why something is $54, there's an answer.

## Sold comps from a browser, not an API

eBay's sold-listings data (Marketplace Insights) is closed to new developers, and a seller account's own APIs
need developer keys and OAuth for every agent. The ebay plugin reads sold listings in a signed-in browser
instead. It's slower and it's paced, but it needs nothing but your own eBay login.

## Nothing posts for you

There's no eBay write access. A listing draft is text you paste, a pasted sale is a proposal you check, and
the weekly review reports stale listings without touching them. Listing, pricing in public and contacting
buyers stay your decisions. It also keeps a wrong guess from ever reaching a marketplace.

## Sold only through a sale record

An item can't be set to sold by editing its status. Recording a sale captures the price, shipping, fees and
postage that make the P&L true, closes the right listing, and puts the item on the pick list. Every way into
"sold" goes through that one path.

## The double-sell guard flags instead of closing

When an item sells on one channel while it's listed on another, quietly closing the other listing would leave
it live on the marketplace with nobody knowing. So the listing is flagged `needs_end` and stays on Next until
you've ended it there.

## The plugin's turns are its own, and narrow

Identifying a box, writing a draft and reading a pasted sale each run in a session of their own, never your
chat, so they can't clutter it or read it. Each has a short tool list and hard limits (no eBay where it isn't
needed, no subagents, a cap on web searches), because an early pricing session that leaned on general web
search ran to hundreds of tool calls and several dollars for a single lot.

## The agent never invents item facts

A guessed model count once made the agent doubt its own correct comps. So unknown counts stay 0, a box's
contents are what the agent can confirm, an unsure identification offers choices and a typing field, and your
correction is kept on the scan.

## Label first, then put away

Binning used to be the last step of a scan. In practice you sort pieces into bins after they're labelled, and
a piece already in a bin with no label is the one you can't find later. So a scan ends by printing labels, and
a piece goes into a bin by scanning its label and then the bin's.

## The view reads its own labels, because of iOS

iOS never hands a scanned link to a Home Screen web app: a QR read by the Camera app always opens in Safari,
which keeps its own storage and isn't signed in. So the view has **Scan label**, and the public label links
serve a page that sends a signed-in browser straight on and tells anyone else how to open it in the app.

## Labels encode the plugin's address, not the console's

A label's QR points at the plugin's own `/b/` or `/i/` route, which forwards to wherever the console opens
views today. Printed paper can't be updated, so only the plugin's stable address is ever printed.

## A live camera where it's allowed, a photo everywhere

A viewfinder that reads label after label is much faster than a photo per label, and it works where the view
shares an origin with the console. Where it doesn't, or the camera is refused, the phone's own camera and a
photo always work. QR decoding happens in the page, so photos of your shelves never leave it.

## Copy list is the operator's own format

The for-sale post was first built as Markdown with sections and bullets. The operator hand-edited it to what
a group chat actually wants: the game system as a plain header, then `Name NoS — $20` per line, nothing else.
That edit became the format, and a test pins it byte for byte.

## Settings in Settings, not on the view

Thresholds such as stale-price days, aging tiers and scan confidence are Settings, not controls on the work
view. The view stays about the work.
