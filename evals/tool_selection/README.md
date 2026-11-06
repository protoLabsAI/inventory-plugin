# Tool-selection eval

A fixed benchmark of the first tool a model picks for a reseller's request. It never runs a tool:
each prompt is an independent turn, and only the first call is scored. Mirrors
careercoach-plugin's `evals/tool_selection/`.

- **`prompts.yaml`**: about 50 operator prompts across every capability (P&L, find, intake, price,
  sale, listing, bins, packing, review, CSV, delete), plus questions that need no tool and traps
  (post to eBay, set sold by hand, message a buyer, end a live listing), whose right answer is no
  tool. The decisions behind the expectations are written at the top of the file. Ids, prices
  and URLs are invented.
- **The surface** is every tool the plugin registers, schemas taken from registration on a
  throwaway database, plus four host decoys a merchant agent's chat also has (`web_search`,
  `fetch_url`, `memory_recall`, `ebay_price_check`). The plugin's own turn-only tools (scan, draft
  and sale-paste) are registered too, and picking one for an operator prompt is always wrong.

## Run it

```sh
pip install -r requirements-dev.txt
python evals/tool_selection/run_eval.py --label smoke --dry-run     # plumbing only, no network
EVAL_BASE_URL=https://<gateway>/v1 EVAL_API_KEY=… EVAL_MODEL=<model> \
  python evals/tool_selection/run_eval.py --label baseline         # a real model
```

A live run posts each prompt to `<EVAL_BASE_URL>/chat/completions` with the tools and
`tool_choice: auto`, at temperature 0. Results go to `evals/tool_selection/results/<label>.json`
(gitignored), and a per-capability table is printed. `--config KEY=VALUE` passes plugin settings;
only the keys are written to the results. A live run stops on the first network or response error and writes no partial results; re-run it. Dry-run picks are deterministic noise for checking the
plumbing, not a measure of anything.

`tests/test_tool_eval.py` keeps the corpus honest: every expected tool exists, no prompt expects a
turn-only tool, every operator-facing tool has at least two prompts, and the dry run produces a
complete result.

## Baseline (2026-11-06)

Through the protoLabs gateway, before any description tuning:

| Model | Overall | Weakest |
|---|---|---|
| `protolabs/fast` | 43/53 (81.1%) | sale 0/5, trap 2/4 |
| `protolabs/smart` | 48/53 (90.6%) | sale 1/5, trap 3/4 |

**Finding: the turn-only `inventory_sale_candidates` pulls operator sale requests.** Both models
picked it first for "sold the X for $Y" prompts, instead of `inventory_mark_sold` or a lookup with
`inventory_list` / `inventory_get`. It's read-only, so the harm is an extra step rather than a wrong
write, but its description should steer chat to `inventory_list` and `inventory_mark_sold`. That's
the next thing to tune and re-run.

## After tuning `inventory_sale_candidates` (0.16.4)

Its description now opens "ONLY for a paste-a-sale turn the Inventory view starts" and sends a sale
the operator mentions in chat to `inventory_list` / `inventory_get` and `inventory_mark_sold`.

| Model | Overall | Sale | Turn-only picks |
|---|---|---|---|
| `protolabs/fast` | 46/53 (86.8%), up from 81.1% | 3/5, up from 0/5 | 0 |
| `protolabs/smart` | 48/53 (90.6%), unchanged | 3/5, up from 1/5 | 0 |

Every remaining miss on both models is a read before acting: `inventory_get` or `inventory_list`
first, such as checking an item before recording a sale on it or before declining a trap. That costs
a step and writes nothing. smart's trap score moved from 3/4 to 2/4 for that reason, not because it
did what a trap asked. Single runs at temperature defaults; treat a one-prompt move as noise.
