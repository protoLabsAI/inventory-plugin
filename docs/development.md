# Development

Contributor notes: the file layout, the tests, checking the view by hand, and releasing.

## Layout

| Path | What it is |
|---|---|
| `__init__.py` | `register()`: opens the store, registers the tools, the two routers, the skill and the weekly review |
| `protoagent.plugin.yaml` | The manifest: config defaults, settings, the view, public paths, events |
| `store.py` | The SQLite store: schema and migrations, every write in one transaction, the audit trail, money in cents |
| `tools.py` | The agent tools |
| `api.py` | The bearer-gated operator API under `/api/plugins/inventory` |
| `view.py` | The console view (one vanilla-JS page on the kit) and the public routes: `/view`, the label landing pages, sheets |
| `automations.py` | The weekly review job, the re-price plan, and the stale-price and aging settings |
| `scans.py` | The scan identification and pricing turns |
| `drafts.py` | The listing-draft turn |
| `sales.py` | The pasted-sale turn |
| `photos.py` | Photo intake: sniff, convert HEIC, strip metadata |
| `labels.py` | Label addresses, the landing pages, the sheets and label stocks |
| `sheetlinks.py` | Short-lived links to rendered label sheets |
| `csvio.py` | CSV import and export |
| `vendor/qrcodegen.py` | Project Nayuki's QR encoder (MIT), unmodified |
| `vendor/jsqr` | jsQR 1.4.0 (Apache-2.0), the QR decoder the view inlines |
| `skills/inventory-ops` | The agent's skill: the rules and the routines |
| `docs/` | These pages, plus `design/` (the view redesign spec) |
| `tests/` | The host-free suite |

## Tests

```bash
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements-dev.txt ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
```

The suite is host-free: no protoAgent, no database beyond a temp file. A stub `graph.sdk` stands in for the
host where a module needs one. CI runs the same three commands on Python 3.12. Some view tests run the page's
JavaScript under node (`node --check` plus small harnesses); they skip if node isn't installed.

Tests worth knowing about:

| Test | What it guards |
|---|---|
| `tests/test_view.py` | The view's four rules (one fetch shim, every call through `apiFetch`, no `--pl-*` definitions, no outside URLs), the public routes (exactly `/view`, `/b/`, `/i/` and `/sheet/`, all GET), and Copy list byte for byte |
| `tests/test_docs_reference.py` | These docs: every route, tool and setting is documented, nothing removed is, defaults match, links resolve |
| `tests/test_plugin.py` | The manifest: the version matches `pyproject.toml`, it ships disabled, the skill is found, and runtime dependencies come from the host |
| `tests/test_hardening.py` | Atomic writes, the sold guard, strict money, non-clobbering import |

## Checking the view by hand

The view has no build step. To see a change, mount the real routers on a copy of a database and borrow the
design kit from a running agent:

1. Copy a database (never work on a live one), or seed an invented one.
2. Start a small FastAPI app that calls `register()` with a fake registry, includes the routers it collects,
   and proxies `/_ds/*` to a running protoAgent (for example `http://127.0.0.1:7870/_ds/`).
3. Stub `graph.sdk.run_in_session` to play the agent: call the store's `propose_scan`, `write_draft` or
   proposal methods after a short delay.
4. Open `http://127.0.0.1:<port>/plugins/inventory/view` and check it at 390, 495 and 1200 px.

### A fake camera

To exercise the live viewfinder without a phone, feed Chromium a video of labels:

```bash
ffmpeg -loop 1 -i label.jpg -t 4 -vf "scale=1920:1440,format=yuvj420p" -r 10 -c:v mjpeg labels.mjpeg
chromium --use-fake-device-for-media-stream --use-fake-ui-for-media-stream \
         --use-file-for-fake-video-capture=labels.mjpeg
```

The view only gets a live camera where it shares an origin with the console. A standalone harness page is its
own origin, so the camera works there too.

## Releasing

Versions are bumped in feature PRs and cut by a separate release PR, the same ritual as the other
fleet plugins.

1. **In the PR that changes behavior**, bump `version` in `protoagent.plugin.yaml` and `pyproject.toml`
   together, and add a note to [upgrading.md](upgrading.md) if existing users need to know.
2. **To cut a release**, merge a PR titled `chore: release vX.Y.Z` whose version matches the manifest.
   `.github/workflows/release.yml` calls `protoLabsAI/release-tools`' reusable `plugin-release.yml`,
   which tags `vX.Y.Z`, writes the release notes, creates the GitHub release and posts the fleet's
   Discord embed. It skips every other push, and it's idempotent if the tag already exists. Don't
   create releases by hand with `gh release create`.
3. **Agents pick it up** when the plugin is updated: `POST /api/plugins/inventory/update` on the agent
   re-installs from the default branch and hot-reloads. Back up `inventory.db` first if the release
   migrates the database.

A behavior change worth a note for existing users goes in [upgrading.md](upgrading.md).
