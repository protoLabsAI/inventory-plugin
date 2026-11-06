# Configuration

Most settings are in **Settings → Plugins → Inventory**. In YAML they sit under `inventory:` in your
agent's `config/langgraph-config.yaml`. One key, `db_path`, is YAML-only. The plugin has no secrets.

`tests/test_docs_reference.py` fails if the manifest gains or drops a key without this page changing.

```yaml
plugins:
  enabled: [inventory]
inventory:
  workspace_dir: /path/to/agent/workspace
  weekly_review: true
  review_timezone: America/Los_Angeles
```

## Where things live

| Key | Default | In Settings | What it does |
|---|---|---|---|
| `db_path` | `""` | no | Where the SQLite file lives. Blank uses the host's instance-scoped plugin store, so every agent and the dev sandbox get their own database. Set it only to point at an existing file. Scan photos are kept beside the database |
| `workspace_dir` | `""` | Workspace directory | Relative paths given to `inventory_import_csv` and `inventory_export_csv` resolve here. Blank uses the process working directory. Point it at the agent workspace |

## Weekly review

A recurring agent turn the plugin schedules on the host. It re-prices stale evidence from eBay sold comps,
reports stale listings with a recommendation (it never changes a listing itself) and ends with the per-lot
P&L. Turning the plugin off cancels it.

| Key | Default | In Settings | What it does |
|---|---|---|---|
| `weekly_review` | `false` | Weekly review | Arms the recurring turn |
| `weekly_review_cron` | `"0 9 * * 1"` | Weekly review cron | A 5-field cron, read in the review timezone. The default is Monday 09:00 |
| `review_timezone` | `""` | Review timezone | An IANA name such as `America/Los_Angeles`. Blank means UTC |
| `review_max_items` | `15` | Items re-priced per run | The most items one run re-prices. This paces eBay searches; the rest wait for the next run |
| `stale_listing_days` | `14` | Stale listing (days) | A live listing older than this is reported in the review |

## Stale prices and aging stock

| Key | Default | In Settings | What it does |
|---|---|---|---|
| `stale_price_days` | `30` | Stale price (days) | Price evidence older than this is stale. The weekly review re-checks it, the view badges it and Next ▸ Housekeeping counts it. Bounded 1 to 365 |
| `aging_days_1` | `45` | Aging: first nudge (days) | Days unsold before Next ▸ Aging stock suggests an offer to watchers or a 10% drop. Bounded 1 to 730 |
| `aging_days_2` | `90` | Aging: second nudge (days) | Days before it suggests another 10% drop or a bundle |
| `aging_days_3` | `120` | Aging: decide (days) | Days before it asks you to decide: clearance, bundle or pull it |

The three aging tiers are sorted if you set them out of order. A missing or bad value falls back to its
default.

## Labels

| Key | Default | In Settings | What it does |
|---|---|---|---|
| `label_base_url` | `""` | Bin label address | The address printed in every label's QR code, up to (not including) `/plugins/inventory`, for example `https://hub.example.ts.net/agents/<agent-id>`. Blank works it out: the agent's public URL (a fleet member's hub address), else its console. A malformed value is refused rather than guessed around |

Labels you've already printed keep the address they were printed with, so set this before your first
sheet.

## Scan intake

| Key | Default | In Settings | What it does |
|---|---|---|---|
| `scan_confidence_threshold` | `0.7` | Scan: sure enough at | 0 to 1. Below this, a scan shows "Not sure. Pick one or type it" with the agent's other guesses instead of offering Looks right |
| `scan_timeout_minutes` | `10` | Scan: give up after (minutes) | An identification that hasn't answered by then shows as failed, with Retry. Pricing gets three times as long |

## Kept in the browser, not in Settings

The label stock you last printed on is remembered per kind (bin or item) in the browser's `localStorage`,
because it's about the printer next to you, not the agent.
