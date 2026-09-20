"""
One-time data repair: re-converts every existing user's Extraction rows into
their CURRENT base_currency.

Why this is needed: before this fix, changing base_currency (PATCH
/api/v1/auth/settings) never touched existing Extraction rows, so
converted_amount/converted_currency stayed pinned to whatever currency was
active when each document was originally processed. Any user who ever
changed their base_currency has stale, mismatched-currency data sitting in
the database right now — this script repairs it in place, once.

Going forward no further backfill is needed: changing base_currency now
triggers tasks.reconvert_user_currency_task automatically per user. This
script exists only to fix rows written under the old (never-reconverted)
behavior.

This is a plain data-repair script rather than a schema migration because it
doesn't add or alter any columns — Extraction already has converted_amount /
converted_currency / exchange_rate; this just brings their values back in
sync with each user's current base_currency. The project has no
schema-migration framework (database.py's init_db() only ever calls
SQLModel.metadata.create_all()), so a schema change would need one written by
hand; this script does not require that.

Usage (inside the project venv, with a real .env loaded — see AGENTS.md):
    source venv/bin/activate
    python scripts/backfill_currency_conversions.py

Safe to re-run: rows already converted into a user's current base_currency
are skipped (see reconvert_user_extractions's `continue` check).
"""
import sys
from pathlib import Path

# Running this file directly only puts scripts/ on sys.path, not the project
# root where database.py/models.py/tasks.py live — add it explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from database import engine
from models import User
from tasks import reconvert_user_extractions


def main():
    with Session(engine) as session:
        users = session.exec(select(User)).all()

        total_updated = 0
        for user in users:
            updated = reconvert_user_extractions(session, user.id, user.base_currency)
            if updated:
                print(f"  {user.username}: reconverted {updated} extraction(s) to {user.base_currency}")
            total_updated += updated

        print(f"Done. Reconverted {total_updated} extraction(s) across {len(users)} user(s).")


if __name__ == "__main__":
    main()
