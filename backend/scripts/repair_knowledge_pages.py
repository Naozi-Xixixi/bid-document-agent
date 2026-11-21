"""Preview (default) or apply source-page repairs in the selected company DB.

Run from backend: python scripts/repair_knowledge_pages.py [--company-id ID] [--apply]
No original documents, images, chunk IDs, or AI results are deleted.
"""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.tenant_context import TenantContext, set_current_tenant, reset_current_tenant
from app.modules.knowledge import page_repair, service
from app.services import auth_service, storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = None
    if args.company_id:
        company = next((c for c in auth_service.list_companies() if c["company_id"] == args.company_id), None)
        if not company:
            parser.error("Company not found")
        token = set_current_tenant(TenantContext(
            user_id=0, username="page-repair", role="admin", company_id=company["company_id"],
            company_name=company["company_name"], database_file=Path(company["database_path"]),
            upload_dir=Path(company["upload_path"]),
        ))
    try:
        with storage.connect() as conn:
            ids = [int(r["item_id"]) for r in conn.execute("SELECT item_id FROM knowledge_item WHERE status='completed'").fetchall()]
        plans = [page_repair.plan(item_id) for item_id in ids]
        backup_path = None
        if args.apply and any(p["changes"] for p in plans):
            if settings.db_backend.lower() != "sqlite":
                parser.error("Automatic backup currently supports SQLite only; use your database backup workflow first")
            backup_dir = settings.database_file.parent / "maintenance-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"before-page-repair-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
            with closing(sqlite3.connect(str(settings.database_file))) as source, closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
            plans = [page_repair.apply(p["item_id"]) if p["changes"] else {**p, "applied": 0} for p in plans]
        print(json.dumps({"mode": "apply" if args.apply else "preview", "backup": str(backup_path) if backup_path else None,
                          "items": plans, "vector_sync": "Changed items will be reindexed on next backend startup"},
                         ensure_ascii=False, indent=2))
    finally:
        if token is not None:
            reset_current_tenant(token)


if __name__ == "__main__":
    main()
