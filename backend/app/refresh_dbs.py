"""Reload the fake company databases WITHOUT touching agents, posts or saved keys.

    python -m app.refresh_dbs

Use it after editing app/demo_data.py. Each dataset keeps its existing access key (cdb_...),
so agents stay connected; only the records are replaced. New datasets get a new key.
"""
from __future__ import annotations

import secrets

from sqlalchemy import delete, select

from .agents.tools import key_hash
from .db import SessionLocal, init_db
from .demo_data import DATASETS, record_search_text
from .models import CompanyDataset, CompanyRecord


def refresh() -> dict[str, int]:
    init_db()
    counts: dict[str, int] = {}
    with SessionLocal() as db:
        for ds_id, ds in DATASETS.items():
            row = db.get(CompanyDataset, ds_id)
            if row is None:
                demo_key = f"cdb_{ds_id}_{secrets.token_hex(6)}"
                row = CompanyDataset(id=ds_id, company=ds["company"], description=ds["description"],
                                     key_hash=key_hash(demo_key), demo_key=demo_key, collections=list(ds["collections"]))
                db.add(row)
            else:
                row.description = ds["description"]
                row.collections = list(ds["collections"])
            db.execute(delete(CompanyRecord).where(CompanyRecord.dataset_id == ds_id))
            n = 0
            for coll, records in ds["collections"].items():
                for rec in records:
                    db.add(CompanyRecord(dataset_id=ds_id, collection=coll, data=rec,
                                         search_text=record_search_text(coll, rec)))
                    n += 1
            counts[ds_id] = n
        db.commit()
        assert all(db.scalar(select(CompanyDataset).where(CompanyDataset.id == k)) for k in counts)
    return counts


if __name__ == "__main__":
    c = refresh()
    print(f"Refreshed {len(c)} company databases, {sum(c.values())} records: "
          + ", ".join(f"{k} {v}" for k, v in c.items()))
    print("Agents, posts and saved keys were not changed. Search results may be cached for up to 5 minutes.")
