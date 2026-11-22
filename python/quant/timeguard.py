"""TimeTravelEngine：所有数据读取、知识检索、特征生成、模型训练的统一门禁。

核心不变量：
  对 prediction_year=Y 的预测，可访问数据空间 = {d | d.available_time <= Y-1-12-31}。
  该空间通过以下方式物理保证：
    1) 数据按 asof 快照分目录存放，预测进程只挂载对应快照根目录；
    2) 每次访问前用本引擎 validate 数据行/知识条目/模型版本；
    3) 启动预测前用 Rust timeguard 对整个快照做全量 .timeindex 扫描；
    4) 生成 manifest（hash + 时间范围 + 条目数 + sealed），预测后冻结。

Rust 不可用时回退纯 Python 实现（行为一致）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from .contracts import Availability, PredictionSpec, TimeFields, parse_date
from .logging_util import log
from .utils import ROOT, get_config, resolve


class LeakError(RuntimeError):
    """任何未来数据泄漏都抛出此异常，预测必须中止。"""


class TimeTravelEngine:
    def __init__(self, prefer_rust: bool | None = None):
        cfg = get_config()["timeguard"]
        self.admission_field = cfg["admission_field"]
        self.snapshots_root = resolve(cfg["snapshots_root"])
        self.rust_cli = Path(cfg["rust_cli_path"])
        self.prefer_rust = cfg.get("prefer_rust", True) if prefer_rust is None else prefer_rust
        self.strict = get_config()["research"]["strict_point_in_time"]

    # ---------- 单行/批量准入 ----------
    def validate_row(self, row: dict[str, Any], spec: PredictionSpec) -> bool:
        tf = TimeFields.from_row(row)
        if self.strict and tf.availability == Availability.UNKNOWN:
            raise LeakError(f"unknown_availability: {row.get('symbol')} source={tf.source}")
        if tf.available_time and tf.available_time > spec.cutoff_date:
            raise LeakError(
                f"future_data_leak: available_time={tf.available_time} "
                f"> cutoff={spec.cutoff_date} symbol={row.get('symbol')}"
            )
        # v0.3.0 P0：严格模式下估计/未知发布时间的数据禁止使用
        if self.strict:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if isinstance(payload, dict):
                basis = payload.get("time_basis")
                if basis in ("estimated", "unknown", "imputed"):
                    raise LeakError(
                        f"unconfirmed_time_basis: time_basis={basis} "
                        f"symbol={row.get('symbol')} source={tf.source} "
                        f"(strict mode requires real published time)"
                    )
        return True

    def filter_rows(self, rows: Iterable[dict[str, Any]],
                    spec: PredictionSpec) -> list[dict[str, Any]]:
        """返回通过门禁的行；严格模式下遇到 unknown 直接抛错。"""
        out = []
        for r in rows:
            if self.validate_row(r, spec):
                out.append(r)
        return out

    def validate_model(self, model_meta: dict[str, Any], spec: PredictionSpec) -> bool:
        """禁止使用未来年份训练的模型权重。"""
        trained_to = parse_date(model_meta.get("trained_to"))
        built_year = model_meta.get("built_year")
        if trained_to and trained_to > spec.cutoff_date:
            raise LeakError(
                f"future_model_weight: trained_to={trained_to} > cutoff={spec.cutoff_date}"
            )
        if built_year and int(built_year) > spec.prediction_year:
            raise LeakError(
                f"future_model_version: built_year={built_year} > prediction_year={spec.prediction_year}"
            )
        if model_meta.get("knowledge_snapshot_id") != spec.knowledge_snapshot_id:
            raise LeakError(
                f"snapshot_mismatch: model={model_meta.get('knowledge_snapshot_id')} "
                f"spec={spec.knowledge_snapshot_id}"
            )
        return True

    def assert_no_future_in_parquet(self, path: str | Path, spec: PredictionSpec,
                                    column: str = "available_time") -> dict[str, Any]:
        """对 Parquet 做列投影扫描（只读 available_time 一列）。"""
        import pyarrow.parquet as pq
        p = resolve(str(path))
        if not p.exists():
            return {"ok": True, "scanned": 0, "note": "missing"}
        table = pq.read_table(str(p), columns=[column])
        values = table.column(column).to_pylist()
        scanned = 0
        for i, v in enumerate(values):
            if v is None or v == "":
                if self.strict:
                    raise LeakError(f"unknown_availability in {p.name} row {i}")
                continue
            scanned += 1
            d = parse_date(v)
            if d and d > spec.cutoff_date:
                raise LeakError(
                    f"future_data_leak in {p.name} row {i}: {d} > {spec.cutoff_date}"
                )
        return {"ok": True, "scanned": scanned, "file": str(p.relative_to(ROOT))}

    # ---------- Rust 全量审计 ----------
    def rust_available(self) -> bool:
        local = ROOT / "rust_core/target/release/timeguard"
        for c in (self.rust_cli, local):
            if c.exists() and os.access(str(c), os.X_OK):
                return True
        return False

    def _rust_bin(self) -> Path:
        local = ROOT / "rust_core/target/release/timeguard"
        return self.rust_cli if self.rust_cli.exists() else local

    def audit_snapshot(self, snapshot_dir: str | Path, spec: PredictionSpec,
                       manifest_path: Path | None = None,
                       require_sealed: bool = False) -> dict[str, Any]:
        """预测前强制执行（v0.2.0）：严格日期 + 全目录 hash 比对 + sealed 严格解析。"""
        snap = resolve(str(snapshot_dir))
        if not snap.exists():
            raise LeakError(f"snapshot not found: {snap}")
        if manifest_path is None:
            manifest_path = snap / "manifest.json"
        cutoff = spec.cutoff_date.isoformat()
        # 读取 manifest 的 entry_count / id（供 Rust fail-closed 校验）
        entry_count = None
        if manifest_path.exists():
            try:
                mm = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry_count = mm.get("entry_count")
            except Exception:
                entry_count = None
        if self.prefer_rust and self.rust_available():
            cmd = [str(self._rust_bin()), "audit", "--snapshot", str(snap),
                   "--cutoff", cutoff, "--json",
                   "--spec-id", spec.knowledge_snapshot_id,
                   "--cutoff-check", cutoff]
            if manifest_path.exists():
                cmd += ["--manifest", str(manifest_path)]
            if require_sealed:
                cmd += ["--require-sealed"]
            if entry_count is not None:
                cmd += ["--entry-count", str(entry_count)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode not in (0, 2):
                raise LeakError(f"timeguard crashed: {proc.stderr}")
            try:
                result = json.loads(proc.stdout.strip().splitlines()[-1])
            except Exception:
                result = {"raw": proc.stdout}
            if proc.returncode == 2 or not result.get("ok"):
                log.error("TimeGuard Rust 审计失败: %s", result)
                raise LeakError(f"rust audit failed: {result}")
            result["engine"] = "rust"
            return result
        # Python 回退（v0.2.0：与 Rust 语义对齐——严格日期解析 + 全目录 hash）
        from .knowledge.kb import hash_snapshot_dir
        scanned = 0
        ti_files = list(snap.rglob("*.timeindex"))
        if not ti_files:
            raise LeakError("no_timeindex_files")
        for p in ti_files:
            for line in p.read_text(encoding="utf-8").splitlines():
                v = line.strip()
                if not v:
                    continue
                scanned += 1
                if v.lower() in ("unknown", "null", "none"):
                    raise LeakError(f"unknown_availability in {p.name}")
                try:
                    d = parse_date(v)
                except (ValueError, TypeError):
                    raise LeakError(f"invalid_date_format in {p.name}: {v}")
                if d > spec.cutoff_date:
                    raise LeakError(f"future_data_leak in {p.name}: {v}")
        if manifest_path.exists():
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            if require_sealed and m.get("sealed") is not True:
                raise LeakError("manifest not sealed")
            if m.get("cutoff_date") and m["cutoff_date"] != cutoff:
                raise LeakError("manifest cutoff mismatch")
            current = hash_snapshot_dir(snap)
            if current != m.get("file_hashes", {}):
                raise LeakError("manifest hash mismatch")
        return {"ok": True, "engine": "python", "scanned": scanned}

    @staticmethod
    def file_hash(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    def build_snapshot_manifest(self, snapshot_dir: str | Path,
                                spec: PredictionSpec, entry_count: int,
                                file_hashes: dict[str, str],
                                time_range: dict[str, str],
                                vector_index_version: str = "none") -> dict[str, Any]:
        return {
            "prediction_year": spec.prediction_year,
            "cutoff_date": spec.cutoff_date.isoformat(),
            "training_start_date": spec.training_start_date.isoformat(),
            "knowledge_snapshot_id": spec.knowledge_snapshot_id,
            "model_version": spec.model_version,
            "feature_version": spec.feature_version,
            "strict_point_in_time": spec.strict_point_in_time,
            "allow_future_data": spec.allow_future_data,
            "entry_count": entry_count,
            "file_hashes": file_hashes,
            "time_range": time_range,
            "vector_index_version": vector_index_version,
            "sealed": False,
        }

    @staticmethod
    def seal_manifest(manifest: dict[str, Any], path: Path) -> None:
        manifest["sealed"] = True
        manifest["sealed_at"] = date.today().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
