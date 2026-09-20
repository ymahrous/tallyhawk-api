<div align="center">

# Tallyhawk

**Asynchronous AI-powered document processing API.**

*Transform unstructured invoices and receipts into structured JSON, at scale, without timeouts.*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Celery](https://img.shields.io/badge/Celery-5.3-green?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-2.5%20Flash-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

</div>

---

## The Problem

Standard web APIs have a hard limit: if a server doesn't respond within ~30 seconds, the connection is dropped. AI document extraction can easily take 10–30 seconds per file. Naive implementations fail under real-world load.

Tallyhawk solves this with an asynchronous producer-consumer architecture: the API responds in ~100ms, and a background worker does the heavy lifting independently.

---

## How it works

```
Client uploads file
       │
       ▼
┌──────────────┐    saves file     ┌─────────────────┐
│   FastAPI    │ ─────────────────▶│ Supabase Storage│
│  (Producer)  │                   └─────────────────┘
│              │    writes PENDING ┌─────────────────┐
│              │ ─────────────────▶│  Neon Postgres  │
│              │                   └─────────────────┘
│              │    pushes job     ┌─────────────────┐
└──────────────┘ ─────────────────▶│  Upstash Redis  │
                                   └────────┬────────┘
                                            │ pulls job
                                            ▼
                                   ┌─────────────────┐
                                   │  Celery Worker  │
                                   │  (Consumer)     │
                                   │                 │
                                   │  1. Download    │
                                   │  2. Rasterize   │──▶ PyMuPDF (PDF → PNG)
                                   │  3. AI Extract  │──▶ Gemini 2.5 flash (vision)
                                   │  4. COMPLETED   │
                                   └─────────────────┘
```

### AI Extraction Pipeline

The worker downloads the uploaded file from object storage. If it's a PDF, the first page is rasterized into a high-resolution PNG using PyMuPDF; image files pass through unchanged. The resulting image is sent to **Google Gemini 2.5 flash** via the Interactions API for vision-based extraction, returning strictly typed JSON (`vendor`, `total_amount`, `date`).

If extraction fails for any reason, the document's status is set to `FAILED` and the error is logged — there is currently no secondary extraction strategy.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **API Framework** | FastAPI | RESTful endpoints, automatic OpenAPI docs |
| **ORM** | SQLModel | Typed models over SQLAlchemy |
| **Task Queue** | Celery 5.3 | Distributed async workers |
| **Message Broker** | Upstash Redis | Serverless Redis over TLS |
| **Database** | Neon (Serverless Postgres) | Managed relational storage |
| **Object Storage** | Supabase Storage | S3-compatible file store |
| **AI Inference** | Google Gemini 2.5 Flash | Multimodal vision extraction |
| **PDF Processing** | PyMuPDF (fitz) | Rasterizes PDF pages to PNG |
| **Auth** | python-jose (JWT) + bcrypt | Stateless bearer-token authentication |
| **Logging** | structlog | Structured JSON logs |
| **Hosting** | Railway | Docker-based, auto-deploy on push |
| **Testing** | pytest | Unit and integration tests |

---

## API Reference

All routes are prefixed with `/api/v1`.

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/signup` | Create an account. Returns a bearer token. |
| `POST` | `/auth/login` | Authenticate. Returns a bearer token. |
| `POST` | `/auth/change-password` | Change password. Requires current password. |
| `POST` | `/auth/forgot-password` | Request a password-reset email (always returns the same message, regardless of whether the account exists, to prevent email enumeration). |
| `POST` | `/auth/reset-password` | Reset a password using the token from the forgot-password email. |
| `DELETE` | `/auth/delete` | Permanently delete the authenticated user and their documents/extractions/vendors/usage/subscription/feedback records. |

### Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/settings` | Get the authenticated user's settings, including `base_currency`. |
| `PATCH` | `/auth/settings` | Update settings. Currently supports changing `base_currency` (any valid ISO 4217 code). |

### Documents

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload/` | Upload a document (multipart). Returns immediately; processing happens in the background. |
| `GET` | `/documents/` | List all documents owned by the authenticated user. |
| `DELETE` | `/documents/{document_id}` | Delete a document, its extraction, and its file in storage. |
| `GET` | `/extraction/{document_id}` | Retrieve the structured extraction for a completed document, including original and converted currency amounts. 404 if not yet processed. |
| `PATCH` | `/extraction/{document_id}/category` | Pro-only. Manually override the extracted spend category. |
| `GET` | `/reports/tax-summary?year=YYYY` | Pro-only. Streams a CSV with per-category totals and per-document currency detail for the given year. |

All document and extraction routes require an `Authorization: Bearer <token>` header and are scoped to the authenticated user via `owner_id`.

---

## Multi-Currency Support

Tallyhawk extracts the original currency directly from each document and converts it to the user's preferred base currency, so spend reports and tax exports are always comparable across vendors that bill in different currencies.

### How it works

1. **Extraction**: Along with `vendor`, `total_amount`, and `date`, the Gemini extraction prompt also returns a `currency` field — an ISO 4217 code (`USD`, `EUR`, `GBP`, etc.) inferred from the currency symbol or document context. If Gemini can't infer it, this defaults to `USD`.
2. **Conversion**: The Celery worker (`tasks.py`) calls `convert_currency()`, which hits the [exchangerate-api.com](https://www.exchangerate-api.com/) `/pair/{from}/{to}/{amount}` endpoint to convert the extracted amount into the user's `base_currency`.
3. **Storage**: Both the original and converted values are persisted on the `Extraction` row, so nothing is lost even if exchange rates move later:

   | Field | Description |
   |---|---|
   | `original_currency` | ISO 4217 code as extracted from the document |
   | `original_amount` | The amount in its original currency |
   | `converted_amount` | The amount converted into `converted_currency` |
   | `converted_currency` | Which currency `converted_amount` is actually denominated in — a snapshot of `base_currency` *at conversion time*, kept in sync automatically when `base_currency` changes (see below) |
   | `exchange_rate` | The rate used for the conversion (`original → converted_currency`) |

4. **Display & export**: `GET /extraction/{document_id}` returns all five fields (via `ExtractionWithVendor`). The `/reports/tax-summary` CSV export gives each row its own Original Amount, Original Currency, and Converted Amount columns (rather than assuming one currency for the whole export), plus a converted total per category, so a user can audit exactly how each foreign-currency expense was translated into their base currency. Any row whose `converted_amount` isn't reliably in the user's *current* `base_currency` — mid-reconversion, or a legacy row with nothing to convert from — is excluded from the category totals and listed separately in a "needs reconversion" note instead of being silently blended in. The same reliability check protects the dashboard (`/api/v1/stats/dashboard`), the analytics endpoints, and duplicate/price-anomaly detection.

### User base currency

Every `User` has a `base_currency` field (default `"USD"`). Users can view and change it via:

```bash
GET /api/v1/auth/settings
PATCH /api/v1/auth/settings   { "base_currency": "EUR" }
```

`base_currency` is validated against a fixed set of ISO 4217 codes (see `UserSettingsUpdate.validate_currency` in `models.py`) before being upper-cased and saved.

**Changing it reconverts your existing documents, not just future ones.** When `base_currency` actually changes, a Celery task re-converts every existing `Extraction` that has a known `original_amount`/`original_currency` into the new currency, grouped by original currency so it costs one exchange-rate lookup per distinct currency rather than one per document. This runs asynchronously, so there's a brief window right after the change where some rows may still reflect the old currency — during that window they're excluded from totals/comparisons rather than shown incorrectly (see the reliability note above). Only true legacy rows with no recorded original amount/currency at all (predating multi-currency support) can never be reconverted, since there's no source amount to convert from.

### Fallback behavior

- **Same currency**: if `original_currency == base_currency`, the amount is returned as-is with a genuine rate of `1.0` (no API call is made).
- **No `EXCHANGE_RATE_API_KEY` set, or the exchange-rate API call fails**: conversion cannot happen. Rather than fabricating a `1.0` rate (which would be indistinguishable from a real 1:1 conversion and would silently mislabel an unconverted amount as converted), `converted_amount`/`converted_currency`/`exchange_rate` are left unset — exactly like a legacy row — so every downstream check correctly treats the document as unreliable/needing reconversion instead of trusting a fake number. A warning is logged either way, and the document still processes successfully.
- **Legacy extractions**: documents processed before multi-currency support was added have `null` values for all currency fields; `/reports/tax-summary` falls back to parsing `total_amount` directly and assumes the user's current `base_currency` in that case.

### One-time data repair

`scripts/backfill_currency_conversions.py` re-runs the same reconversion logic across every user in one pass. It's only needed once, for data written before the automatic-reconversion behavior above existed; going forward, changing `base_currency` repairs itself. Safe to re-run — already-correct rows are skipped.

### Environment variable

| Variable | Description |
|---|---|
| `EXCHANGE_RATE_API_KEY` | API key for [exchangerate-api.com](https://www.exchangerate-api.com/). Required for live currency conversion; without it, conversion silently no-ops (see Fallback behavior above). |

---

## Security Model

**Authentication**: Stateless JWTs signed with HS256, verified on every request via FastAPI's `HTTPBearer` dependency. No server-side sessions.

**Data Isolation**: Every document query is filtered by `owner_id == current_user.id` at the query level. Users cannot list or fetch documents they don't own.

**Password Hashing**: Uses the `bcrypt` library directly (not `passlib`), generating a fresh salt per password via `bcrypt.gensalt()`.

---

## Project Structure

```
tallyhawk-api/
├── main.py                    # FastAPI app, CORS, router registration, DB init on startup
├── auth.py                    # JWT creation/decoding, password hashing
├── auth_routes.py             # Signup, login, change-password, delete, forgot/reset-password, settings
├── document_routes.py         # Upload, list, delete, extraction, category, tax-summary CSV export
├── billing_routes.py          # Stripe checkout, webhook, usage, subscription
├── quickbooks_routes.py       # QuickBooks OAuth connect/callback, expense sync, sync status
├── admin/admin_routes.py      # Admin-only overview/timeseries/users/feedback (separate admin auth)
├── analytics/analytics_routes.py # Spend-by-category, spend-by-vendor, monthly-trend (Pro)
├── stats/stats_routes.py      # Dashboard totals (processed, synced, month spend)
├── vendor/                    # Vendor fuzzy-matching, rename, merge
├── feedback_routes.py         # User feedback submission
├── dependencies.py            # get_current_user() auth dependency, increment_usage()
├── models.py                  # SQLModel: User, Document, Extraction, Subscription, Vendor, etc.
├── database.py                # Engine setup, session dependency
├── storage_client.py          # Supabase Storage upload/delete client
├── celery_app.py              # Celery app config, Upstash Redis broker
├── tasks.py                   # process_document_task — the Celery consumer; currency conversion + reconversion
├── ai_extractor.py            # PDF rasterization + Gemini extraction pipeline
├── detection.py               # Duplicate / price-anomaly detection
├── scripts/                   # One-off maintenance scripts (e.g. currency-conversion backfill)
├── tests/                     # pytest suite
├── conftest.py
├── pytest.ini
├── requirements.txt
├── Procfile                   # Railway process definitions (web, worker)
├── railway.toml
├── Makefile
├── AGENTS.md                  # Instructions for AI coding agents working in this repo
├── CLAUDE.md                  # Points to AGENTS.md
└── .env.example
```

---

## Local Development

### Prerequisites

- Python 3.11+
- A Postgres database (e.g. [Neon](https://neon.tech))
- A Redis instance (e.g. [Upstash](https://upstash.com))
- A [Supabase](https://supabase.com) project with a storage bucket
- A [Google AI Studio](https://aistudio.google.com) API key

### Setup

```bash
git clone https://github.com/ymahrous/tallyhawk-api.git
cd tallyhawk-api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your database, Redis, Supabase, and Gemini credentials.

### Running

Run the API:
```bash
uvicorn main:app --reload
```

Run the Celery worker (separate terminal):
```bash
celery -A celery_app worker --loglevel=info
```

Or use the `Makefile` if targets are defined for both.

API docs are available at `http://localhost:8000/docs` (FastAPI's automatic Swagger UI).

---

## Testing

```bash
pytest
```

Test configuration lives in `pytest.ini`, with shared fixtures in `conftest.py`.

---

## Deployment

1. Connect this repository to [Railway](https://railway.app).
2. Railway reads `Procfile` and deploys two services from it: `web` (FastAPI) and `worker` (Celery).
3. Set environment variables in the Railway dashboard — see `.env.example` for the full list.
4. `railway.toml` configures build/deploy behavior.

### Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string (Neon) |
| `SECRET_KEY` | JWT signing secret |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon/public API key |
| `SUPABASE_BUCKET_NAME` | Storage bucket for uploaded documents |
| `UPSTASH_REDIS_ENDPOINT` | Upstash Redis host |
| `UPSTASH_REDIS_PASSWORD` | Upstash Redis password |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `EXCHANGE_RATE_API_KEY` | [exchangerate-api.com](https://www.exchangerate-api.com/) key, used for multi-currency conversion (see [Multi-Currency Support](#multi-currency-support)) |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins. Required — the app won't start without it, there's no wildcard fallback. |

---

## Key Decisions

**Why Celery + Redis instead of background threads or `asyncio.create_task`?** Long-running AI inference work needs to survive process restarts and scale independently of the API's request-handling capacity. A dedicated worker process, decoupled via a message broker, allows the `web` and `worker` Railway services to scale separately.

**Why raw `bcrypt` instead of `passlib`?** `passlib`'s bcrypt backend has had compatibility issues on some platforms. Calling `bcrypt` directly removes a dependency layer and keeps the hashing logic explicit.

**Why rasterize PDFs instead of extracting text directly?** Gemini's vision capability handles messy, non-machine-readable invoice layouts (scanned documents, photos of receipts) far better than raw text extraction, which fails on receipts with unusual formatting or embedded images.

**Why SQLModel over plain SQLAlchemy?** SQLModel combines Pydantic validation with SQLAlchemy's ORM in a single model definition, reducing duplication between API schemas and database tables in a small codebase like this one.

---

## Limitations

- No retry or fallback strategy if Gemini extraction fails — the document is simply marked `FAILED`
- CORS origins are read from the `ALLOWED_ORIGINS` env var (comma-separated) — there's no wildcard fallback, so it must be set for the app to start
- `DELETE /documents/{id}` has no partial-failure recovery if the storage delete and the DB delete don't both succeed
- True legacy extractions (processed before multi-currency support existed, with no recorded original amount/currency) can never be reconverted — there's no source amount to convert from

---

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change. See [CONTRIBUTING.md](./CONTRIBUTING.md).

---

## License

[MIT](./LICENSE)

---

<div align="center">

Built with FastAPI · Celery · Google Gemini · Supabase · Neon · Upstash · Railway

</div>