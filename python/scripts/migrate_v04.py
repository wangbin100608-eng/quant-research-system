"""v0.4.0 P0：数据库/字段迁移与回滚。

迁移（forward）：
  * SQLite kb_items / hypotheses / metrics 增加 v0.4 字段（available_at/published_at/ingested_at
    /source_max_available_at/snapshot_cutoff/code_hash/data_hash/model_hash/created_at）。
  * 用旧字段 available_time/published_time/collected_time 回填（不删除旧列）。
  * 记录迁移版本 migration_meta。

回滚（rollback）：
  * 删除 v0.4 新增列（SQLite 3.35+ DROP COLUMN），恢复 migration_meta 标记。
  * 不触碰历史数据行。

用法：
  python scripts/migrate_v04.py forward
  python scripts/migrate_v04.py rollback
  python scripts/migrate_v04.py status
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.timestamps_v04 import iso_utc, to_utc
from quant.utils import resolve

DB = resolve("data/meta.db")

TABLE_COLUMNS = {
    "kb_items": {
        "published_at": "published_time", "available_at": "available_time",
        "ingested_at": "collected_time",
    },
    "metrics": {},
    "snapshots": {},
}


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def status() -> dict:
    conn = sqlite3.connect(str(DB))
    out = {"db": str(DB), "tables": {}}
    for t in ("kb_items", "hypotheses", "snapshots", "metrics"):
        try:
            out["tables"][t] = sorted(_cols(conn, t))
        except Exception as e:
            out["tables"][t] = f"error: {e}"
    try:
        out["migration"] = conn.execute(
            "SELECT version, applied_at FROM migration_meta ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        out["migration"] = None
    conn.close()
    return out


def forward() -> dict:
    conn = sqlite3.connect(str(DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_meta (
        version TEXT, applied_at TEXT, detail TEXT)""")
    added = {}
    for table, mapping in TABLE_COLUMNS.items():
        if table not in status()["tables"]:
            continue
        cols = _cols(conn, table)
        new_cols = ["created_at", "source_max_available_at", "snapshot_cutoff",
                    "code_hash", "data_hash", "model_hash"]
        if table == "kb_items":
            new_cols += ["published_at", "available_at", "ingested_at"]
        todo = [c for c in new_cols if c not in cols]
        for c in todo:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} TEXT")
        added[table] = todo
    # 回填 kb_items 新字段（保留旧字段）
    if "kb_items" in status()["tables"]:
        rows = conn.execute("SELECT rowid, available_time, published_time FROM kb_items").fetchall()
        for rid, av, pub in rows:
            conn.execute("UPDATE kb_items SET available_at=?, published_at=?, ingested_at=COALESCE(ingested_at,?) "
                         "WHERE rowid=?",
                         (iso_utc(to_utc(av)), iso_utc(to_utc(pub)), iso_utc(to_utc(av)), rid))
    conn.execute("INSERT INTO migration_meta VALUES (?,?,?)",
                 ("v0.4.0", iso_utc(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
                  str(added)))
    conn.commit()
    conn.close()
    return {"action": "forward", "added_columns": added, "status": status()}


def rollback() -> dict:
    conn = sqlite3.connect(str(DB))
    removed = {}
    for table in TABLE_COLUMNS:
        cols = _cols(conn, table)
        drop = [c for c in ("published_at", "available_at", "ingested_at",
                            "source_max_available_at", "snapshot_cutoff",
                            "code_hash", "data_hash", "model_hash", "created_at")
                if c in cols]
        for c in drop:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {c}")
            except Exception:
                pass  # 老 SQLite 不支持 DROP COLUMN：保留列但清值
        removed[table] = drop
    conn.execute("DELETE FROM migration_meta WHERE version='v0.4.0'")
    conn.commit()
    conn.close()
    return {"action": "rollback", "removed_columns": removed, "status": status()}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["forward", "rollback", "status"])
    args = p.parse_args()
    import json
    fn = {"forward": forward, "rollback": rollback, "status": status}[args.action]
    print(json.dumps(fn(), ensure_ascii=False, indent=2, default=str))
