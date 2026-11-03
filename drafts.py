"""Listing drafts: the agent turn behind "Draft listing". The plugin starts it with
``sdk.run_in_session`` into the item's OWN session (``inventory-draft-<id>``) — a draft turn
carries the item's photos as images, so a shared session would resend every earlier item's
photos. The operator's chat is never used, and nothing is ever posted: the operator pastes the
copy into eBay and records the listing's URL.
"""

from __future__ import annotations

from .scans import enqueue

#: Bulk drafting from Next ▸ Ready to list: at most this many per click, one after another.
MAX_BULK = 10
#: Spacing between the turns of a bulk run, so they run one after another, not all at once.
BULK_SPACING_S = 45.0

DRAFT = """Inventory listing draft for item {item_id}. This is an automated turn owned by the inventory plugin; keep it cheap.

1. Call inventory_draft_item("{item_id}") to see the item record and its photos.
2. Do NOT use eBay, Amazon or any pricing tool, and do NOT start subagents. Use a web search only if the record lacks the basics of what this kit is, and at most one.
3. Call inventory_draft_write("{item_id}", title, description) once.
   - title: at most 80 characters, the way a buyer searches: the game or brand, the product name, the model count when the record has it, and condition words such as "New on Sprue" or "NIB". No emoji, no price, no shipping, no "L@@K" or filler.
   - description: plain text, no HTML. The first line states the condition. Then what is included, taken ONLY from the item record and what the photos show; never invent contents, counts or extras, and write "as pictured" where you are unsure. No price, no shipping or returns terms (eBay sets those).
4. If there isn't enough to write an honest listing, call inventory_draft_fail("{item_id}", reason) instead.

Do not message the operator; the Inventory view shows the draft."""


def session_for(item_id: str) -> str:
    return f"inventory-draft-{item_id}"


def draft_prompt(item_id: str) -> str:
    return DRAFT.format(item_id=item_id)


def start_draft(store, item_id: str, *, delay_seconds: float = 0.0, sdk=None) -> dict:
    """Enqueue one draft turn for an item already marked ``drafting``. On failure the draft is
    marked failed with the reason, so the view shows Try again instead of a spinner."""
    res = enqueue(session_for(item_id), draft_prompt(item_id), f"draft-{item_id}", delay_seconds=delay_seconds, sdk=sdk)
    if not res.get("ok"):
        return store.fail_draft(item_id, res.get("message") or "Couldn't start the draft.", actor="console")
    return store.get_draft(item_id)
