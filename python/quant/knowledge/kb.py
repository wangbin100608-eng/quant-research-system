"""知识库与决策库：按年份物理隔离快照，冻结后不可变。

目录结构：
  knowledge/snapshots/asof_YYYY/
    manifest.json          元数据 + hash + 条目统计 + sealed
    knowledge.parquet      该快照允许的研究知识条目
    clean_data.parquet     该快照允许的清洗数据（列投影，仅 <= cutoff）
    feature_index.parquet  特征处理版本与缓存引用
    model_registry.json    允许使用的模型版本登记
    *.timeindex            供 Rust timeguard 审计的时间索引
    audit/                 审计日志
任何写入已 sealed 快照的尝试都会抛错。
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..contracts import PredictionSpec, parse_date
from ..logging_util import log
from ..timeguard import LeakError, TimeTravelEngine
from ..utils import ROOT, get_config, resolve
from ..storage import MetadataDB


class SnapshotSealedError(RuntimeError):
    pass


class KnowledgeBase:
    def __init__(self, timeguard: TimeTravelEngine | None = None):
        cfg = get_config()["knowledge"]
        self.root = resolve(cfg["root"])
        self.snapshots_dir = resolve(cfg["snapshots_dir"])
        self.audit_dir = resolve(cfg["audit_logs_dir"])
        for p in (self.root, self.snapshots_dir, self.audit_dir,
                  resolve(cfg["raw_documents_dir"]), resolve(cfg["metadata_dir"])):
            p.mkdir(parents=True, exist_ok=True)
        self.tg = timeguard or TimeTravelEngine()
        self.meta = MetadataDB()

    def snapshot_path(self, year: int) -> Path:
        return self.snapshots_dir / f"asof_{year}"

    def is_sealed(self, year: int) -> bool:
        m = self.snapshot_path(year) / "manifest.json"
        if not m.exists():
            return False
        try:
            return bool(json.loads(m.read_text(encoding="utf-8")).get("sealed"))
        except Exception:
            return False

    # ---------- 创建快照 ----------
    def create_snapshot(self, raw_parquet: str | Path, spec: PredictionSpec,
                        knowledge_rows: list[dict[str, Any]] | None = None,
                        model_registry: dict[str, Any] | None = None) -> dict[str, Any]:
        snap = self.snapshot_path(spec.cutoff_date.year)
        if self.is_sealed(spec.cutoff_date.year):
            raise SnapshotSealedError(f"快照 asof_{spec.cutoff_date.year} 已冻结，禁止修改")
        if snap.exists():
            shutil.rmtree(snap)
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "audit").mkdir(exist_ok=True)

        raw = resolve(str(raw_parquet))
        table = pq.read_table(str(raw))
        strict = get_config()["research"]["strict_point_in_time"]
        # 物理过滤：只保留 available_time <= cutoff；非法日期 fail-closed
        avail = table.column("available_time").to_pylist()
        keep: list[int] = []
        for i, v in enumerate(avail):
            if v is None or v == "":
                if strict:
                    raise LeakError(f"snapshot build: row {i} unknown_availability")
                continue
            try:
                d = parse_date(v)
            except (ValueError, TypeError) as e:
                if strict:
                    raise LeakError(f"snapshot build: row {i} invalid_date {v!r}: {e}")
                continue
            if d is not None and d <= spec.cutoff_date:
                keep.append(i)
        clean = table.take(pa.array(keep, type=pa.int64()))
        clean_path = snap / "clean_data.parquet"
        pq.write_table(clean, str(clean_path), compression="zstd")

        # 知识条目（研究结论、新闻摘要等）—— create 内部强制时间过滤，不信任调用方
        krows = knowledge_rows or []
        filtered_krows: list[dict[str, Any]] = []
        for kr in krows:
            av = kr.get("available_time")
            if not av:
                if strict:
                    raise LeakError(f"knowledge {kr.get('entry_id')} unknown_availability")
                continue
            try:
                kd = parse_date(av)
            except (ValueError, TypeError):
                if strict:
                    raise LeakError(f"knowledge {kr.get('entry_id')} invalid_date {av!r}")
                continue
            if kd is not None and kd <= spec.cutoff_date:
                filtered_krows.append(kr)
        ktable = (pa.Table.from_pylist(filtered_krows, schema=_KNOWLEDGE_SCHEMA)
                  if filtered_krows else _empty_knowledge())
        kpath = snap / "knowledge.parquet"
        pq.write_table(ktable, str(kpath), compression="zstd")

        # 模型登记表：built_year <= prediction_year 且 trained_to <= cutoff；缺 trained_to 严格拒绝
        registry = model_registry or {}
        allowed_models = {}
        for k, v in registry.items():
            built_ok = int(v.get("built_year", 9999)) <= spec.prediction_year
            tt = v.get("trained_to")
            if not tt:
                if strict:
                    continue  # 无训练截止日的模型严格模式拒绝
                tt_ok = True
            else:
                try:
                    tt_ok = parse_date(tt) <= spec.cutoff_date
                except (ValueError, TypeError):
                    tt_ok = False if strict else True
            if built_ok and tt_ok:
                allowed_models[k] = v
        (snap / "model_registry.json").write_text(
            json.dumps(allowed_models, ensure_ascii=False, indent=2), encoding="utf-8")

        # 时间索引（Rust 审计输入）
        with open(snap / "clean_data.timeindex", "w", encoding="utf-8") as f:
            for v in clean.column("available_time").to_pylist():
                f.write(f"{v}\n")
        with open(snap / "knowledge.timeindex", "w", encoding="utf-8") as f:
            for v in ktable.column("available_time").to_pylist():
                f.write(f"{v or 'unknown'}\n")

        # 审计日志（必须在算 hash 之前写完，否则 audit/create.json 不在 manifest 覆盖内）
        audit = {
            "event": "snapshot_created", "snapshot_id": spec.knowledge_snapshot_id,
            "cutoff": spec.cutoff_date.isoformat(), "raw_rows": table.num_rows,
            "kept_rows": clean.num_rows, "dropped_future": table.num_rows - len(keep),
            "unknown_availability": 0,  # strict 模式遇 unknown 已抛错；非 strict 已剔除
            "knowledge_rows": ktable.num_rows, "allowed_models": list(allowed_models),
            "ts": datetime.utcnow().isoformat(),
        }
        (snap / "audit" / "create.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.audit_dir / f"{spec.knowledge_snapshot_id}.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        # 文件哈希（v0.2.0：全目录覆盖，含 .timeindex / model_registry / audit）
        hashes = hash_snapshot_dir(snap)
        manifest = self.tg.build_snapshot_manifest(
            snap, spec, entry_count=clean.num_rows + ktable.num_rows,
            file_hashes=hashes,
            time_range={"min": str(min(clean.column("available_time").to_pylist() or ["-"])),
                        "max": str(max(clean.column("available_time").to_pylist() or ["-"]))},
        )
        (snap / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self.meta.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
            (spec.knowledge_snapshot_id, spec.cutoff_date.year,
             spec.cutoff_date.isoformat(), datetime.utcnow().isoformat(),
             manifest["entry_count"], json.dumps(hashes), spec.model_version,
             spec.feature_version, 0),
        )
        log.info("快照 %s 创建: 保留 %d 行, 剔除未来 %d 行, 知识 %d 条",
                 spec.knowledge_snapshot_id, clean.num_rows,
                 table.num_rows - len(keep), ktable.num_rows)
        return {"snapshot": str(snap), "manifest": manifest, "audit": audit}

    # ---------- 冻结 ----------
    def seal_snapshot(self, spec: PredictionSpec, final_hashes: dict[str, str] | None = None) -> dict[str, Any]:
        """冻结快照。v0.2.0：始终重新全目录哈希（忽略调用方过时入参），原子写 manifest。"""
        snap = self.snapshot_path(spec.cutoff_date.year)
        mpath = snap / "manifest.json"
        if not mpath.exists():
            raise FileNotFoundError(mpath)
        seal_path = snap / "audit" / "seal.json"
        if self.is_sealed(spec.cutoff_date.year) and seal_path.exists():
            log.info("快照 %s 已冻结（幂等返回）", spec.knowledge_snapshot_id)
            return json.loads(seal_path.read_text(encoding="utf-8"))
        m = json.loads(mpath.read_text(encoding="utf-8"))
        # v0.2.0：以全目录真实哈希为准，确保 features/models 写入后一并覆盖
        m["file_hashes"] = hash_snapshot_dir(snap)
        m["schema_version"] = "v0.2.0"
        self.tg.seal_manifest(m, mpath)
        self.meta.execute(
            "UPDATE snapshots SET sealed=1, file_hash=? WHERE snapshot_id=?",
            (json.dumps(m["file_hashes"]), spec.knowledge_snapshot_id),
        )
        audit = {"event": "snapshot_sealed", "snapshot_id": spec.knowledge_snapshot_id,
                 "ts": datetime.utcnow().isoformat(), "hashes": m["file_hashes"]}
        seal_path.parent.mkdir(parents=True, exist_ok=True)
        seal_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("快照 %s 已冻结（%d 文件哈希）", spec.knowledge_snapshot_id,
                 len(m["file_hashes"]))
        return audit

    # ---------- v0.2.0 恢复与重新验证 ----------
    def verify_snapshot(self, year: int, spec: PredictionSpec | None = None,
                        require_sealed: bool = True) -> dict[str, Any]:
        """独立重新验证快照：sealed（可选）+ 全目录哈希 + 时间边界，不改任何文件。"""
        snap = self.snapshot_path(year)
        mpath = snap / "manifest.json"
        result: dict[str, Any] = {"year": year, "snapshot": str(snap), "checks": {}}
        if not mpath.exists():
            result["ok"] = False; result["error"] = "manifest missing"
            return result
        m = json.loads(mpath.read_text(encoding="utf-8"))
        result["checks"]["sealed"] = bool(m.get("sealed"))
        # 全目录哈希比对
        current = hash_snapshot_dir(snap)
        expected = m.get("file_hashes", {})
        mismatches = [k for k in set(current) | set(expected)
                      if current.get(k) != expected.get(k)]
        result["checks"]["hashes_match"] = not mismatches
        result["checks"]["mismatched_files"] = mismatches
        # 时间边界
        if spec is not None:
            ti_files = list(snap.rglob("*.timeindex"))
            future = 0
            for tf in ti_files:
                for line in tf.read_text(encoding="utf-8").splitlines():
                    v = line.strip()
                    if v and v.lower() not in ("unknown", "null", "none"):
                        try:
                            if parse_date(v) > spec.cutoff_date:
                                future += 1
                        except (ValueError, TypeError):
                            future += 1
            result["checks"]["future_rows"] = future
            result["checks"]["time_boundary_ok"] = future == 0
        sealed_ok = (not require_sealed) or result["checks"]["sealed"]
        result["ok"] = (sealed_ok
                        and result["checks"]["hashes_match"]
                        and result.get("checks", {}).get("time_boundary_ok", True))
        return result

    def restore_snapshot_from_manifest(self, year: int, backup_root: str | Path) -> dict[str, Any]:
        """从备份恢复快照后用 manifest 重新验证（备份目录为 backup/<ts>/asof_YYYY）。"""
        src = resolve(str(backup_root)) / f"asof_{year}"
        dst = self.snapshot_path(year)
        if not src.exists():
            raise FileNotFoundError(src)
        if self.is_sealed(year):
            raise SnapshotSealedError(f"asof_{year} 已冻结，拒绝覆盖；如需恢复请先移动到备份")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return self.verify_snapshot(year)

    # ---------- 读取 ----------
    def load_snapshot(self, spec: PredictionSpec) -> dict[str, Any]:
        """只返回该时点允许访问的数据；验证 sealed 与 manifest 一致。"""
        snap = self.snapshot_path(spec.cutoff_date.year)
        mpath = snap / "manifest.json"
        if not mpath.exists():
            raise FileNotFoundError(f"snapshot {snap} not found")
        m = json.loads(mpath.read_text(encoding="utf-8"))
        if not m.get("sealed"):
            raise LeakError("拒绝加载未冻结的快照")
        if m.get("knowledge_snapshot_id") != spec.knowledge_snapshot_id:
            raise LeakError("snapshot id mismatch")
        # 哈希校验（v0.2.0：全目录双向校验，检测新增/删除/修改）
        current = hash_snapshot_dir(snap)
        expected = m.get("file_hashes", {})
        if current != expected:
            diff = [k for k in set(current) | set(expected) if current.get(k) != expected.get(k)]
            raise LeakError(f"snapshot tampered / incomplete: {sorted(diff)[:5]}")
        clean = pq.read_table(str(snap / "clean_data.parquet"))
        knowledge = pq.read_table(str(snap / "knowledge.parquet"))
        registry = json.loads((snap / "model_registry.json").read_text(encoding="utf-8"))
        return {
            "path": snap, "manifest": m, "clean": clean,
            "knowledge": knowledge, "model_registry": registry,
        }

    # ---------- 知识条目池（跨年累积，按快照过滤）----------
    def add_raw_knowledge(self, entries: list[dict[str, Any]]) -> Path:
        """写入全局 raw_documents，不影响任何已冻结快照。"""
        p = resolve(get_config()["knowledge"]["raw_documents_dir"]) / "knowledge_pool.parquet"
        if p.exists():
            old = pq.read_table(str(p))
            table = pa.concat_tables([old, pa.Table.from_pylist(entries, schema=_KNOWLEDGE_SCHEMA)])
        else:
            table = pa.Table.from_pylist(entries, schema=_KNOWLEDGE_SCHEMA)
        pq.write_table(table, str(p), compression="zstd")
        return p

    def collect_knowledge_for_snapshot(self, spec: PredictionSpec) -> list[dict[str, Any]]:
        """从全局知识池挑选 available_time <= cutoff 的条目，进入新快照。"""
        p = resolve(get_config()["knowledge"]["raw_documents_dir"]) / "knowledge_pool.parquet"
        if not p.exists():
            return []
        t = pq.read_table(str(p))
        rows = t.to_pylist()
        return [r for r in rows if r.get("available_time")
                and parse_date(r["available_time"]) <= spec.cutoff_date]

    # ---------- 决策库 ----------
    def add_decision_rule(self, rule: dict[str, Any]) -> None:
        status = rule.get("status", "pending")
        if status == "validated" and not rule.get("oos_performance"):
            raise ValueError("未经样本外验证的规则不能升级为 validated")
        self.meta.execute(
            "INSERT OR REPLACE INTO decision_rules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rule["rule_id"], rule["content"], rule.get("market"),
             rule.get("valid_from"), rule.get("valid_to"), rule.get("source"),
             rule.get("method"), rule.get("sample_size", 0),
             json.dumps(rule.get("oos_performance"), ensure_ascii=False),
             json.dumps(rule.get("invalidation"), ensure_ascii=False),
             status, datetime.utcnow().isoformat()),
        )

    def list_rules(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self.meta.query("SELECT * FROM decision_rules WHERE status=?", (status,))
        return self.meta.query("SELECT * FROM decision_rules")


def hash_snapshot_dir(snap: Path, exclude_self_proof: bool = True) -> dict[str, str]:
    """v0.2.0：对快照目录内所有文件计算 SHA-256（相对路径为 key）。

    排除 manifest.json（校验基准自身）与 audit/seal.json（冻结证明，写入时 manifest 已定稿，
    属于自指关系，纳入会导致永恒不一致）。其余文件含 .timeindex/model_registry/audit/create.json 全覆盖。
    """
    out: dict[str, str] = {}
    for p in sorted(snap.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(snap).as_posix()
        if rel == "manifest.json" or (exclude_self_proof and rel == "audit/seal.json"):
            continue
        out[rel] = TimeTravelEngine.file_hash(p)
    return out


_KNOWLEDGE_SCHEMA = pa.schema([
    ("entry_id", pa.string()), ("domain", pa.string()), ("symbol", pa.string()),
    ("event_time", pa.string()), ("published_time", pa.string()),
    ("available_time", pa.string()), ("collected_time", pa.string()),
    ("revision_time", pa.string()), ("source", pa.string()), ("content", pa.string()),
])


def _empty_knowledge() -> pa.Table:
    return pa.Table.from_pylist([], schema=_KNOWLEDGE_SCHEMA)
