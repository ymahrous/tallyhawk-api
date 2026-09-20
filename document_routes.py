import re
import io
import csv
import structlog
import storage_client
import database, models
from sqlalchemy import delete
from sqlmodel import Session, select
from tasks import process_document_task
from datetime import datetime, timezone
from fastapi.responses import StreamingResponse
from dependencies import get_current_user, increment_usage
from fastapi import APIRouter, Depends, HTTPException, Request, Header, status, UploadFile, File

router = APIRouter(prefix="/api/v1", tags=["document"])

@router.post("/upload/")
def test_upload(
    file: UploadFile = File(...), 
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # --- FREE TIER LIMIT ENFORCEMENT ---
    FREE_TIER_LIMIT = 10
    if current_user.plan == "free":
        current_month = datetime.now(timezone.utc).replace(day=1)
        usage = session.exec(
            select(models.UsageRecord)
            .where(models.UsageRecord.user_id == current_user.id)
            .where(models.UsageRecord.month == current_month)
        ).first()
        
        docs_processed = usage.documents_processed if usage else 0
        if docs_processed >= FREE_TIER_LIMIT:
            raise HTTPException(
                status_code=403, 
                detail={
                    "error": "limit_exceeded", 
                    "message": "Free tier limit reached. Upgrade to Pro for unlimited uploads.",
                    "limit": FREE_TIER_LIMIT
                }
            )

    file_bytes = file.file.read()
    filename = file.filename
    public_url = storage_client.upload_to_storage(file_bytes, filename)
    
    db_doc = models.Document(
        filename=filename,
        s3_url=public_url,
        status="PENDING",
        owner_id=current_user.id
    )
    session.add(db_doc)
    session.commit()
    session.refresh(db_doc)
    
    process_document_task.delay(db_doc.id)
    
    # NEW: Increment usage after successful upload dispatch
    increment_usage(current_user.id)

    return {
        "message": "Document received!",
        "document_id": db_doc.id,
        "status": db_doc.status
    }

@router.get("/documents/")
def get_documents(
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # Only select documents where owner_id matches the logged-in user
    docs = session.exec(
        select(models.Document)
        .where(models.Document.owner_id == current_user.id)
        .order_by(models.Document.created_at.desc())
    ).all()
    return docs


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    document = session.exec(
        select(models.Document).where(models.Document.id == document_id)
    ).first()

    if not document or document.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    session.exec(
        delete(models.Extraction).where(models.Extraction.document_id == document_id)
    )
    session.flush()

    session.delete(document)
    session.commit()

    storage_client.delete_from_storage(document.filename)

    return None

@router.get("/extraction/{document_id}", response_model=models.ExtractionWithVendor)
def get_extraction(
    document_id: str, 
    session: Session = Depends(database.get_session),
    current_user: str = Depends(get_current_user)
):
    extraction = session.exec(
        select(models.Extraction).where(models.Extraction.document_id == document_id)
    ).first()
    
    if not extraction:
        raise HTTPException(status_code=404, detail="Extraction not found or still processing.")
        
    # Because of Relationship(), extraction.vendor is automatically populated!
    # FastAPI uses ExtractionWithVendor to serialize it perfectly.
    return extraction 

@router.patch("/extraction/{document_id}/category")
def update_category(
    document_id: str, 
    category_update: dict, 
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # Pro Gate
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Tax categorization is a Pro feature.")
        
    extraction = session.exec(
        select(models.Extraction).where(models.Extraction.document_id == document_id)
    ).first()
    
    if not extraction:
        raise HTTPException(status_code=404, detail="Extraction not found.")
        
    # Validate category against our standard list
    valid_categories = ["Travel", "Meals", "Software", "Office Supplies", "Equipment", "Marketing", "Utilities", "Rent", "Insurance", "Professional Services", "Other"]
    new_category = category_update.get("category")
    
    if new_category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {valid_categories}")
        
    extraction.category = new_category
    session.add(extraction)
    session.commit()
    
    return {"message": "Category updated", "category": new_category}


# --- TAX SUMMARY EXPORT ---
@router.get("/reports/tax-summary")
def get_tax_summary(
    year: int,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Tax export is a Pro feature.")

    docs = session.exec(
        select(models.Document, models.Extraction)
        .join(models.Extraction, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
        .where(models.Extraction.category != None)
    ).all()

    summary = {}
    needs_reconversion = []  # rows whose converted_amount predates a base_currency change
    detail_rows = []  # Store detailed rows for CSV

    for doc, ext in docs:
        raw_date = str(ext.extracted_data.get("date", ""))
        try:
            doc_year = int(raw_date.split("-")[0])
        except:
            continue

        if doc_year == year:
            cat = ext.category

            # Only trust converted_amount if it was actually converted into the
            # user's CURRENT base_currency. If base_currency was changed after this
            # document was processed, converted_amount is stale (in the old currency)
            # and must not be blended with correctly-converted rows.
            stale_conversion = (
                ext.converted_amount is not None
                and ext.converted_currency is not None
                and ext.converted_currency != current_user.base_currency
            )

            if ext.converted_amount is not None and not stale_conversion:
                amount = ext.converted_amount
                original_amount = ext.original_amount
                original_currency = ext.original_currency
            else:
                # Fallback for legacy extractions AND stale (pre-currency-change) rows.
                # Report the ORIGINAL extracted amount/currency rather than mislabeling
                # a stale converted_amount as the current base_currency. There is no
                # valid converted-to-base-currency figure for these rows, so `amount`
                # is left as None rather than fabricated from the raw (original-currency)
                # total — it must never be summed into `summary` or displayed as if it
                # were in current_user.base_currency.
                raw_amount = str(ext.extracted_data.get("total_amount", "0"))
                clean_amount = re.sub(r'[^\d\.-]', '', raw_amount)
                parsed_amount = float(clean_amount) if clean_amount else 0.0
                amount = None
                original_amount = ext.original_amount if ext.original_amount is not None else parsed_amount
                original_currency = ext.original_currency if ext.original_currency else current_user.base_currency

            if amount is None:
                needs_reconversion.append({
                    "date": raw_date,
                    "vendor": ext.extracted_data.get("vendor", ""),
                    "original_amount": original_amount,
                    "original_currency": original_currency,
                })
            else:
                summary[cat] = round(summary.get(cat, 0.0) + amount, 2)

            # Store detail for CSV
            detail_rows.append({
                "date": raw_date,
                "vendor": ext.extracted_data.get("vendor", ""),
                "category": cat,
                "original_amount": original_amount,
                "original_currency": original_currency,
                "converted_amount": amount,
                "base_currency": current_user.base_currency,
                "exchange_rate": ext.exchange_rate
            })

    output = io.StringIO()
    writer = csv.writer(output)

    # Original currency varies per row (each document keeps its own extracted
    # currency), so it must be its own column rather than folded into a single
    # header derived from one row — a single header would mislabel every row
    # that wasn't in that one row's currency.
    writer.writerow([
        "Date", "Vendor", "Category",
        "Original Amount", "Original Currency",
        f"Converted Amount ({current_user.base_currency})",
        "Exchange Rate"
    ])

    for row in sorted(detail_rows, key=lambda x: x["date"]):
        writer.writerow([
            row["date"],
            row["vendor"],
            row["category"],
            f"{row['original_amount']:,.2f}",
            row["original_currency"],
            f"{row['converted_amount']:,.2f}" if row["converted_amount"] is not None else "N/A",
            f"{row['exchange_rate']:.6f}" if row['exchange_rate'] else "N/A"
        ])

    # Summary section
    writer.writerow([])  # Blank row
    writer.writerow(["SUMMARY BY CATEGORY"])
    writer.writerow(["Category", f"Total ({current_user.base_currency})"])
    for cat, total in sorted(summary.items()):
        writer.writerow([cat, f"{total:,.2f}"])

    if needs_reconversion:
        writer.writerow([])
        writer.writerow([f"NOTE: {len(needs_reconversion)} document(s) excluded from the summary above"])
        writer.writerow(["because your base currency changed after they were processed."])
        writer.writerow(["Date", "Vendor", "Original Amount", "Original Currency"])
        for row in needs_reconversion:
            writer.writerow([row["date"], row["vendor"], f"{row['original_amount']:,.2f}", row["original_currency"]])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=Tallyhawk_Tax_Summary_{year}.csv"}
    )