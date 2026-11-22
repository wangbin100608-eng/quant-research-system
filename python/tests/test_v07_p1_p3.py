"""v0.7.0 P1-P3：架构配置、Provider 接口、PIT 基础测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT))


def test_p1_src_structure():
    for d in ("data/providers", "features", "labels", "hypothesis", "experiments",
              "statistics", "validation", "research_memory", "models", "scheduler",
              "forecasting", "shadow_mode", "monitoring", "reporting"):
        assert (ROOT / "src" / d).is_dir(), d
        assert (ROOT / "src" / d / "__init__.py").exists(), d


def test_p1_configs_valid():
    names = ("system", "data_sources", "data_quality", "feature_weights",
             "model_config", "validation", "resource_policy", "shadow_mode")
    for n in names:
        p = ROOT / "config" / f"{n}.yaml"
        assert p.exists(), n
        data = yaml.safe_load(p.read_text())
        assert isinstance(data, dict) and data, n


def test_p1_weights_sum_to_one():
    w = yaml.safe_load((ROOT / "config/feature_weights.yaml").read_text())
    for h in ("horizon_1d", "horizon_5d", "horizon_20d"):
        total = sum(w[h].values())
        assert abs(total - 1.0) < 1e-6, (h, total)


def test_p2_provider_interface():
    from src.data.providers.base import DataProvider
    class Dummy(DataProvider):
        def get_data(self, request):
            return []
    p = Dummy(name="test")
    assert p.get_metadata()["name"] == "test"
    assert "status" in p.health_check()
    assert "get_coverage" in dir(p) and "get_rate_limit" in dir(p)


def test_p3_manifest_schema():
    from src.data.manifests.schema import MANIFEST_SCHEMA
    for k in ("market", "provider", "dataset", "source_sha256", "ingested_at"):
        assert k in MANIFEST_SCHEMA
