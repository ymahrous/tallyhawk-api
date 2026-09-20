import ai_extractor
from database import engine
from typing import List, Optional
from sqlmodel import Session, select
from celery_app import celery_app
from models import Document, Extraction, User
from detection import check_for_duplicates
from vendor.vendor_utils import match_or_create_vendor
import httpx
import os
import re
import asyncio

# Currency conversion setup
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
EXCHANGE_RATE_BASE_URL = "https://v6.exchangerate-api.com/v6"

async def convert_currency(amount: float, from_currency: str, to_currency: str) -> tuple[float, Optional[float]]:
    """
    Convert amount from one currency to another.
    Returns: (converted_amount, exchange_rate). exchange_rate is None when no
    real conversion happened (missing API key, or the API call failed) — as
    opposed to the same-currency case below, where 1.0 is a genuine rate, not
    a fallback. Callers MUST treat rate=None as "not converted" and must not
    persist the returned amount as if it were actually in `to_currency`: a
    fabricated 1.0 rate would be indistinguishable from a real 1:1 conversion
    and would silently mislabel an unconverted amount as converted.
    """
    if from_currency == to_currency:
        return amount, 1.0

    if not EXCHANGE_RATE_API_KEY:
        print("⚠️ No EXCHANGE_RATE_API_KEY set, cannot convert")
        return amount, None

    url = f"{EXCHANGE_RATE_BASE_URL}/{EXCHANGE_RATE_API_KEY}/pair/{from_currency}/{to_currency}/{amount}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("result") == "success":
                converted = data.get("conversion_result", amount)
                rate = data.get("conversion_rate", 1.0)
                return float(converted), float(rate)
            else:
                print(f"⚠️ Exchange rate API error: {data.get('error-type', 'unknown')}")
                return amount, None
        except Exception as e:
            print(f"⚠️ Currency conversion failed: {e}")
            return amount, None


@celery_app.task
def process_document_task(document_id: str):
    print(f"🔥 Celery received job for document: {document_id}")

    with Session(engine) as session:
        document = session.get(Document, document_id)
        if not document:
            return {"error": "Document not found"}

        # Get user's base currency
        user = session.get(User, document.owner_id)
        base_currency = user.base_currency if user else "USD"

        try:
            document.status = "PROCESSING"
            session.add(document)
            session.commit()

            ai_result = ai_extractor.run_ai_extraction(document.s3_url)

            extracted_data = ai_result["data"]
            confidence = ai_result["confidence"]
            category = extracted_data.get("category", "Other")

            # NEW: Extract currency info
            raw_amount_str = extracted_data.get("total_amount", "0")
            original_currency = extracted_data.get("currency", "USD").upper()

            # Parse amount from string (remove symbols, commas)
            clean_amount = re.sub(r'[^\d\.-]', '', raw_amount_str)
            original_amount = float(clean_amount) if clean_amount else 0.0

            # NEW: Convert to user's base currency
            # Note: Celery tasks are sync, so we run async conversion in event loop
            converted_amount, exchange_rate = asyncio.run(
                convert_currency(original_amount, original_currency, base_currency)
            )

            if exchange_rate is not None:
                stored_converted_amount = round(converted_amount, 2)
                stored_converted_currency = base_currency
                stored_exchange_rate = round(exchange_rate, 6)
            else:
                # Conversion unavailable right now (no API key / API failure).
                # Leave these unset like a legacy row rather than claiming the
                # raw amount is in base_currency — every downstream "reliable"
                # check (detection.py, stats/analytics, tax summary) already
                # knows how to exclude/flag a row with no converted_amount,
                # and reconvert_user_extractions() will pick it up on retry.
                stored_converted_amount = None
                stored_converted_currency = None
                stored_exchange_rate = None

            # --- NEW: Vendor Intelligence Matching ---
            vendor_id = None
            raw_vendor_name = extracted_data.get("vendor") # Adjust this key if your Gemini prompt outputs something like "vendor_name"

            if raw_vendor_name:
                # This will fuzzy match an existing vendor OR create a new one automatically
                vendor = match_or_create_vendor(session, document.owner_id, raw_vendor_name)
                if vendor:
                    vendor_id = vendor.id
            # ----------------------------------------

            extraction = Extraction(
                document_id=document_id,
                extracted_data=extracted_data,
                confidence_score=confidence,
                category=category,
                vendor_id=vendor_id, # Save the matched vendor ID
                # Store currency fields
                original_currency=original_currency,
                original_amount=original_amount,
                converted_amount=stored_converted_amount,
                converted_currency=stored_converted_currency,  # snapshot what currency converted_amount is actually in
                exchange_rate=stored_exchange_rate
            )
            session.add(extraction)

            document.status = "COMPLETED"
            session.add(document)
            session.commit()

            if extracted_data.get("vendor") and stored_converted_amount is not None:
                flags = check_for_duplicates(
                    current_user_id=document.owner_id,
                    extraction_data=extracted_data,
                    current_doc_id=document.id,
                    session=session,
                    current_amount=stored_converted_amount,
                    base_currency=base_currency
                )

                if flags:
                    document.flags = ",".join(flags)
                    session.add(document)
                    session.commit()

            print(f"✅ Successfully processed document: {document_id}")
            return {"status": "success", "document_id": document_id}

        except Exception as e:
            document.status = "FAILED"
            session.add(document)
            session.commit()
            print(f"❌ Failed to process document: {e}")
            return {"status": "failed", "error": str(e)}


def reconvert_user_extractions(session: Session, user_id: str, base_currency: str) -> int:
    """Re-converts every one of this user's Extraction rows into `base_currency`,
    in place. This is what keeps invoices, dashboard totals, analytics, and tax
    summaries from drifting out of sync after a user changes base_currency —
    without it, converted_amount/converted_currency stay pinned to whatever
    currency was active when each document was originally processed.

    Only rows with a known original_amount/original_currency can be
    reconverted; legacy rows predating multi-currency support (both fields
    None) have no source amount to convert from and are left untouched — see
    the "Legacy extractions" note in README.md.

    Groups rows by original_currency so each distinct currency pair costs one
    exchange-rate API lookup, no matter how many documents share it. Returns
    the number of rows updated.
    """
    rows = session.exec(
        select(Extraction)
        .join(Document, Extraction.document_id == Document.id)
        .where(Document.owner_id == user_id)
        .where(Document.status == "COMPLETED")
        .where(Extraction.original_amount.is_not(None))
        .where(Extraction.original_currency.is_not(None))
    ).all()

    by_currency: dict[str, list[Extraction]] = {}
    for ext in rows:
        if ext.converted_currency == base_currency:
            continue  # already converted into the currency we want
        by_currency.setdefault(ext.original_currency, []).append(ext)

    updated = 0
    for original_currency, exts in by_currency.items():
        _, rate = asyncio.run(convert_currency(1.0, original_currency, base_currency))
        if rate is None:
            # Conversion unavailable right now (no API key / API failure).
            # Leave these rows untouched rather than writing a fabricated
            # rate — they stay correctly flagged as stale/unreliable (their
            # converted_currency still doesn't match base_currency) and will
            # be retried the next time this runs.
            print(f"⚠️ Skipping reconversion of {len(exts)} row(s) from {original_currency} — no rate available")
            continue
        for ext in exts:
            ext.converted_amount = round(ext.original_amount * rate, 2)
            ext.converted_currency = base_currency
            ext.exchange_rate = round(rate, 6)
            session.add(ext)
            updated += 1

    if updated:
        session.commit()

    return updated


@celery_app.task
def reconvert_user_currency_task(user_id: str):
    """Triggered whenever a user changes base_currency (see the PATCH
    /api/v1/auth/settings handler in auth_routes.py). Re-reads base_currency
    from the DB at execution time rather than trusting a value the caller
    passed in, so repeated/rapid currency changes still converge to whatever
    it is *right now* instead of racing an earlier task."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return {"error": "User not found"}

        updated = reconvert_user_extractions(session, user_id, user.base_currency)
        print(f"🔁 Reconverted {updated} extraction(s) for user {user_id} to {user.base_currency}")
        return {"status": "success", "user_id": user_id, "updated": updated}