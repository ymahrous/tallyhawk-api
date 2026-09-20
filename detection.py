import models
from typing import Optional
from sqlmodel import Session, select
from datetime import datetime, timedelta


def _effective_amount(ext: models.Extraction, base_currency: str) -> Optional[float]:
    """Returns ext's amount in `base_currency`, or None if it can't be reliably
    compared against `current_amount` (also in `base_currency`). Only trusts
    converted_amount when it was actually converted into the CALLER'S CURRENT
    base_currency — otherwise it's in some other currency (original extracted
    currency, or a stale base_currency from before the user changed their
    setting) and must not be compared as if it were. Mirrors the is_reliable
    pattern in stats_routes.py / analytics_routes.py."""
    if ext.converted_amount is not None and ext.converted_currency == base_currency:
        return ext.converted_amount
    return None


def check_for_duplicates(current_user_id: str, extraction_data: dict, current_doc_id: str, session: Session, current_amount: float, base_currency: str) -> list[str]:
    flags = []

    vendor = extraction_data.get("vendor", "")
    amount = current_amount  # NEW: caller passes the already-converted (base-currency) amount

    if not vendor or amount == 0.0:
        return flags

    # 1. DUPLICATE CHECK: Same vendor, same amount, within the last 30 days
    thirty_days_ago = datetime.now() - timedelta(days=30)

    existing_docs = session.exec(
        select(models.Document, models.Extraction)
        .join(models.Extraction, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user_id)
        .where(models.Document.id != current_doc_id) # Don't compare against itself
        .where(models.Document.status == "COMPLETED")
        .where(models.Document.created_at >= thirty_days_ago)
    ).all()

    for doc, ext in existing_docs:
        ext_vendor = str(ext.extracted_data.get("vendor", "")).lower()
        ext_amount = _effective_amount(ext, base_currency)
        if ext_amount is None:
            continue  # different/stale currency — not safely comparable

        # DEBUG PRINTS
        print(f"--- Comparing Docs ---")
        print(f"Current: Vendor='{vendor.lower()}', Amount={amount}")
        print(f"Existing: Vendor='{ext_vendor}', Amount={ext_amount}")

        # Fuzzy match vendor (one contains the other) and exact match amount
        vendor_match = (vendor.lower() in ext_vendor or ext_vendor in vendor.lower())
        amount_match = (amount == ext_amount)

        print(f"Vendor Match: {vendor_match} | Amount Match: {amount_match}")

        if vendor_match and amount_match:
            flags.append("possible_duplicate")
            break

    # 2. ANOMALY CHECK: Amount is 3x higher than the historical average for this vendor
    all_vendor_docs = session.exec(
        select(models.Extraction)
        .join(models.Document, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user_id)
        .where(models.Document.id != current_doc_id)
        .where(models.Document.status == "COMPLETED")
    ).all()

    vendor_amounts = []
    for ext in all_vendor_docs:
        ext_vendor = str(ext.extracted_data.get("vendor", "")).lower()
        if vendor.lower() in ext_vendor or ext_vendor in vendor.lower():
            ext_amount = _effective_amount(ext, base_currency)
            if ext_amount is not None:
                vendor_amounts.append(ext_amount)

    if len(vendor_amounts) >= 2: # Need at least 2 past purchases to establish a pattern
        avg_amount = sum(vendor_amounts) / len(vendor_amounts)
        if avg_amount > 0 and amount > (avg_amount * 3):
            flags.append("price_anomaly")

    return flags