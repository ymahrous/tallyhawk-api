# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, etc.) when working with code in this repository.

## Commands

Always create and work inside a venv — never install packages or run Python globally:
```bash
python3 -m venv venv
source venv/bin/activate   # activate before running ANY command in this repo, every new shell
```

```bash
make install   # pip install -r requirements.txt
make run       # uvicorn main:app --reload      (FastAPI dev server, http://localhost:8000/docs)
make worker    # celery -A celery_app.celery_app worker --loglevel=info
make test      # pytest
make clean     # remove __pycache__/*.pyc/*.egg-info
```

Run a single test file or test:
```bash
pytest tests/test_currency.py
pytest tests/test_currency.py::test_convert_currency_same_currency -v
```

Local dev needs `.env` (copy from `.env.example`) with a real Postgres URL, Redis (Upstash), Supabase Storage, and a Gemini API key — see `.env.example` for the full variable list. CI (`.github/workflows/ci-cd.yml`) spins up a throwaway Postgres service container and fake values for everything else, then runs `pytest tests/ -v --tb=short`.

One-time data repair (only needed once, against a database that predates the currency-reconversion fix — see Multi-currency below):
```bash
python scripts/backfill_currency_conversions.py
```

Tests never touch the real Postgres/Redis: `conftest.py` overrides the `get_session` dependency with an in-memory SQLite engine per test (autouse fixture, tables dropped after each test), and stub AI/storage keys from CI env vars are enough since `ai_extractor`/`storage_client` calls aren't exercised by the current suite.

## Architecture

Producer-consumer split to work around the ~30s HTTP timeout that AI vision extraction (10-30s) would otherwise blow through:

```
FastAPI (producer)          Celery worker (consumer)
  upload → Supabase Storage    pulls job from Redis
  → row in Postgres (PENDING)  → downloads file, rasterizes PDF→PNG (PyMuPDF)
  → push job to Redis          → Gemini 2.5 Flash vision extraction → JSON
  → responds ~100ms            → currency conversion, vendor match, duplicate/anomaly flags
                                → writes Extraction row, sets Document.status COMPLETED/FAILED
```

- `main.py` — FastAPI app setup, CORS, router registration, DB init on startup. All business routes live in per-domain router modules, not here.
- `database.py` — engine + `get_session()` dependency (rewrites `postgresql://` → `postgresql+psycopg://`).
- `celery_app.py` — Celery app over Upstash Redis (`rediss://`, TLS).
- `tasks.py` — `process_document_task`, the main Celery consumer. Runs the whole per-document pipeline synchronously inside one task (extraction → currency conversion → vendor matching → duplicate/anomaly detection → status update). Async currency conversion is bridged into this sync task via `asyncio.run(...)`. Also has `reconvert_user_currency_task`, fired by `auth_routes.py` whenever a user changes `base_currency` (see Multi-currency below), and the plain function `reconvert_user_extractions()` it wraps, which is reused by `scripts/backfill_currency_conversions.py`.
- `ai_extractor.py` — downloads the file, rasterizes PDF first page to PNG via PyMuPDF (images pass through unchanged), sends to Gemini via the Interactions API, parses the JSON response (`vendor`, `total_amount`, `currency`, `date`, `category`). No fallback extraction strategy — failure marks the document `FAILED`.
- `storage_client.py` — Supabase Storage upload/delete (upsert on upload).
- `detection.py` — post-processing over a completed extraction: flags `possible_duplicate` (same vendor + same converted amount within 30 days) and `price_anomaly` (>3x the vendor's historical average), writing into `Document.flags` as a comma-joined string. Only compares amounts whose `converted_currency` matches the current `base_currency`; other-currency/stale rows are excluded from comparison rather than blended in as if equal (see Multi-currency below).
- `vendor/vendor_utils.py` — fuzzy-matches a raw extracted vendor string against a user's existing `Vendor.aliases`/`canonical_name` (via `difflib.get_close_matches`, cutoff 0.8) or creates a new `Vendor`. Alias list grows automatically on partial matches.
- `dependencies.py` — `get_current_user()` (JWT bearer → `User`) and `increment_usage()` (per-user-per-month `UsageRecord`, used for free-tier gating).
- `auth.py` — JWT create/decode (HS256, `python-jose`) and password hashing (raw `bcrypt`, not `passlib`, chosen deliberately for cross-platform compatibility — see README "Key Decisions").
- Route modules are grouped by domain, each an `APIRouter` mounted in `main.py`: `auth_routes.py` (signup/login/change-password/delete/forgot-reset-password/settings), `document_routes.py` (upload/list/delete/extraction/category/tax-summary CSV export), `billing_routes.py` (Stripe checkout + webhook + usage/subscription), `quickbooks_routes.py` (OAuth connect/callback, expense sync, sync status), `admin/admin_routes.py`, `analytics/analytics_routes.py`, `stats/stats_routes.py`, `vendor/vendor_routes.py`, `feedback_routes.py`.
- All routes are prefixed `/api/v1` and document/extraction routes are scoped to `owner_id == current_user.id` at the query level — there is no row-level DB policy enforcing this, so any new query against `Document`/`Extraction` must filter by the authenticated user explicitly.

### Admin auth is separate from user auth

There is no admin signup endpoint or separate admin table. The admin identity is a single manually-seeded row in the `user` table (`username=ADMIN_USERNAME` env var). `admin/admin_routes.py` has its own login that only accepts that one username, and issues a JWT with `role: "admin"`; `get_current_admin` checks that claim. Regular `get_current_user` never checks `role`, and the admin token isn't usable against normal user routes' data scoping.

### Multi-currency

Extraction returns an ISO 4217 `currency` guessed by Gemini (defaults `USD` if unclear). `tasks.py` converts that into the user's `base_currency` via exchangerate-api.com and persists **both** the original and converted values on `Extraction` (`original_currency`, `original_amount`, `converted_amount`, `converted_currency`, `exchange_rate`).

`convert_currency()` returns `(amount, rate)` where `rate` is `None` whenever no real conversion happened — no `EXCHANGE_RATE_API_KEY`, or the API call failed — as opposed to the genuine same-currency case, where `1.0` is a real rate. This distinction matters: every caller (`process_document_task`, `reconvert_user_extractions`) must treat `rate is None` as "not converted" and leave `converted_amount`/`converted_currency`/`exchange_rate` as `None` (exactly like a legacy row) rather than writing a fabricated `1.0` rate. A fabricated rate would be indistinguishable from a real 1:1 conversion and would satisfy every `converted_currency == base_currency` "reliable" check elsewhere in the codebase while actually being wrong — this exact failure mode corrupted a row in production during development of this feature (an unconverted EUR amount got silently written as if it were USD) and was manually repaired once the fix landed; the value it should have converted to had to be recomputed from `original_amount`/`original_currency` after the API key issue was resolved.

**Changing `base_currency` reconverts existing data, it isn't just a display setting.** `PATCH /api/v1/auth/settings` (`auth_routes.py`) diffs the incoming value against the user's current `base_currency`, and if it actually changed, fires `tasks.reconvert_user_currency_task.delay(user.id)`. That Celery task re-reads `base_currency` from the DB at execution time (not whatever was true when it was queued, so rapid repeated changes still converge correctly) and calls `tasks.reconvert_user_extractions()`, which re-converts every `Extraction` with a known `original_amount`/`original_currency` into the new `base_currency` in place — grouped by `original_currency` so each distinct currency only costs one exchange-rate lookup no matter how many documents share it. Rows with neither field set (true legacy extractions, predating multi-currency support entirely) have no source amount to convert from and are left alone — that's the one case that's still a real, unfixable gap, not a bug. `scripts/backfill_currency_conversions.py` runs this once across every user, for data that went stale before this reconversion hook existed; it doesn't need to run again since new currency changes now repair themselves.

Because the write path now keeps `converted_amount`/`converted_currency` in sync, any read path can usually trust them directly (e.g. `GET /extraction/{id}`, the "invoice" view). But there's still a short async window between the settings PATCH returning and the Celery task finishing — during that window `converted_currency` may not yet equal the new `base_currency`. Anything that *aggregates or compares* `converted_amount` across multiple rows (as opposed to just displaying one row) must not trust it blindly in that window: check `converted_currency == current_user.base_currency` (or the equivalent `base_currency` parameter) first, and exclude the row rather than blending in a mismatched-currency number. This "reliable" pattern is implemented consistently in `stats_routes.py`/`analytics_routes.py`'s `get_effective_amount()`, `detection.py`'s `_effective_amount()`, and the `stale_conversion` handling in `document_routes.py`'s tax-summary endpoint (whose CSV also gives each row its own "Original Currency" column rather than assuming one currency for the whole export).

### Data model (`models.py`)

SQLModel (Pydantic + SQLAlchemy in one class) for every table. Key tables: `User`, `Document` (status: `PENDING`/`PROCESSING`/`COMPLETED`/`FAILED`; `flags` is a comma-joined string, not a list column), `Extraction` (one-to-one with `Document`, holds the AI output plus currency fields and an optional `Vendor` relationship), `Subscription`/`UsageRecord` (billing/quota), `QuickBooksConnection`, `Vendor`, `Feedback`, `PasswordResetToken`. `ExtractionWithVendor` / `UserSettingsRead` / `UserSettingsUpdate` are plain Pydantic response/request models (not tables) layered over the SQLModel tables.

## Conventions

- Conventional Commits (`feat(scope): ...`, `fix(scope): ...`, etc.), imperative mood, first line < 72 chars. Branch prefixes: `feat/`, `fix/`, `refactor/`, `docs/`, `perf/`, `chore/`.
- New functions and public APIs should be type-hinted; prefer SQLModel/Pydantic models over ad hoc dicts for stable response shapes.
- Reuse `dependencies.py` (`get_current_user`) and `database.py` (`get_session`) rather than re-deriving auth/session logic in a new route module.
- One concern per PR.

## Known limitations (don't "fix" silently — these are documented tradeoffs)

- No retry/fallback if Gemini extraction fails — the document is simply marked `FAILED`.
- CORS origins come from the required `ALLOWED_ORIGINS` env var (comma-separated); there's no wildcard fallback, so it must be set for the app to start.
- `DELETE /api/v1/documents/{id}` has no partial-failure recovery if the storage delete and the DB delete don't both succeed.
