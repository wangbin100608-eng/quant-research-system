"""Streamlit 轻量管理界面：数据覆盖、数据源、质量、快照、预测、误差、规则、资源、日志。

启动: streamlit run python/dashboard/app.py --server.port 8501
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.evaluation import PredictionStore  # noqa: E402
from quant.storage import MetadataDB, DuckDBStore  # noqa: E402
from quant.utils import resolve, get_config  # noqa: E402
from quant.timeguard import TimeTravelEngine  # noqa: E402
from quant.knowledge import KnowledgeBase  # noqa: E402

st.set_page_config(page_title="量化研究系统", page_icon="📊", layout="wide")
st.title("📊 A股 / 美股量化研究系统")
st.caption("严格点位时间隔离 · 历史模拟预测 · 知识持续更新")


@st.cache_resource
def db():
    return MetadataDB()


meta = db()
store = PredictionStore()
tabs = st.tabs([
    "总览", "数据覆盖与质量", "知识库快照", "预测与评估",
    "决策规则", "时间隔离审计", "任务日志与资源", "运行控制",
    "v0.2真实数据", "v0.2模型对比", "v0.2性能", "v0.2验收",
])


def _load_report(rel, default=None):
    p = resolve(rel)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else (default or {})


# ---------- 总览 ----------
with tabs[0]:
    cfg = get_config()
    cov_path = resolve("data/reports/coverage_report.json")
    cov = json.loads(cov_path.read_text()) if cov_path.exists() else {}
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("原始数据行数", cov.get("total_rows", 0))
    col2.metric("时间隔离快照", len(meta.query("SELECT * FROM snapshots")))
    col3.metric("冻结预测记录", len(meta.query("SELECT * FROM predictions")))
    col4.metric("决策规则", len(meta.query("SELECT * FROM decision_rules")))
    integrity = store.verify_all()
    st.success("预测记录哈希校验通过，全部不可变") if integrity["ok"] else st.error("检测到预测篡改")
    st.markdown(f"""
**研究配置**
- 首次预测年份：{cfg['research']['first_prediction_year']}，末次：{cfg['research']['last_prediction_year']}
- 严格点位时间隔离：`{cfg['research']['strict_point_in_time']}`
- 允许未来数据：`{cfg['research']['allow_future_data']}`
- 数据源模式：{cov.get('sources', ['未加载'])}
- 合成数据：{cov.get('synthetic', '未知')}
    """)
    if cov.get("synthetic"):
        st.warning("当前使用 fixture 合成数据，仅用于端到端验收；真实覆盖率需按数据源报告单独验证。")


# ---------- 数据覆盖 ----------
with tabs[1]:
    sources = meta.query("SELECT * FROM source_runs ORDER BY started_at DESC LIMIT 30")
    if sources:
        st.dataframe(pd.DataFrame(sources), use_container_width=True)
    else:
        st.info("尚无数据源运行记录。先执行 `python scripts/data_pipeline.py fetch --sources fixture`。")
    if cov_path.exists():
        st.subheader("覆盖率报告")
        st.json(cov)


# ---------- 快照 ----------
with tabs[2]:
    snaps = meta.query("SELECT * FROM snapshots ORDER BY asof_year")
    if snaps:
        df = pd.DataFrame(snaps)
        df["sealed"] = df["sealed"].map({1: "✅ 已冻结", 0: "未冻结"})
        st.dataframe(df[["snapshot_id", "cutoff_date", "entry_count",
                         "sealed", "model_version"]], use_container_width=True)
        sel = st.selectbox("查看快照 manifest", [s["snapshot_id"] for s in snaps])
        mp = resolve(f"data/knowledge/snapshots/{sel}/manifest.json")
        if mp.exists():
            st.json(json.loads(mp.read_text()))
    else:
        st.info("尚无快照。运行年度循环后生成。")


# ---------- 预测与评估 ----------
with tabs[3]:
    preds = meta.query("SELECT * FROM predictions ORDER BY prediction_year")
    if preds:
        for p in preds:
            with st.expander(f"{p['prediction_year']} 年预测 · {p['pred_id']}"):
                rec = store.load(p["prediction_year"])[0]
                st.json(rec["result"])
                st.caption(f"SHA-256: `{rec['hash']}`")
                ck = resolve(f"data/checkpoints/year_{p['prediction_year']}.json")
                if ck.exists():
                    m = json.loads(ck.read_text()).get("artifacts", {}).get("metrics")
                    if m:
                        st.subheader("评估指标")
                        st.dataframe(pd.DataFrame([m]), use_container_width=True)
    else:
        st.info("尚无预测记录。")


# ---------- 决策规则 ----------
with tabs[4]:
    rules = meta.query("SELECT * FROM decision_rules")
    if rules:
        st.dataframe(pd.DataFrame(rules), use_container_width=True)
    else:
        st.info("尚无决策规则。")
    st.caption("规则状态：pending（待验证）/ validated（通过样本外验证）/ deprecated（已废弃）")


# ---------- 时间隔离审计 ----------
with tabs[5]:
    tg = TimeTravelEngine()
    st.write(f"Rust 核心引擎：{'可用' if tg.rust_available() else '不可用（Python 回退）'}")
    years = st.multiselect("选择快照审计", [2001, 2002, 2003, 2004], default=[2001, 2002, 2003])
    if st.button("执行全量时间索引审计"):
        from quant.contracts import PredictionSpec
        for y in years:
            spec = PredictionSpec.for_year(y + 1, 2000, f"asof_{y}", "audit", "feature_v1")
            try:
                r = tg.audit_snapshot(resolve(f"data/knowledge/snapshots/asof_{y}"), spec)
                st.success(f"asof_{y}: PASS (扫描 {r.get('scanned', '?')} 行, 引擎 {r.get('engine')})")
            except Exception as e:
                st.error(f"asof_{y}: FAIL - {e}")


# ---------- 日志与资源 ----------
with tabs[6]:
    tasks = meta.query("SELECT * FROM tasks ORDER BY ts DESC LIMIT 50")
    if tasks:
        st.dataframe(pd.DataFrame(tasks), use_container_width=True)
    perf = meta.query("SELECT * FROM metrics ORDER BY id DESC LIMIT 50")
    if perf:
        st.subheader("性能记录")
        df = pd.DataFrame(perf)
        st.dataframe(df[["task", "elapsed_seconds", "mem_peak_mb", "disk_bytes"]], use_container_width=True)
        st.line_chart(df.set_index("task")["mem_peak_mb"])
    logp = resolve("data/logs/system.log")
    if logp.exists():
        with st.expander("系统日志（尾部 100 行）"):
            st.code("\n".join(logp.read_text().splitlines()[-100:]))


# ---------- 运行控制 ----------
with tabs[7]:
    st.markdown("""
运行命令（在容器内或本地）:
```
python scripts/data_pipeline.py fetch --sources fixture
python scripts/run.py run --start 2002 --end 2004
PYTHONPATH=python python3 -m pytest python/tests -v
python scripts/build_dashboard.py
```
    """)
    c1, c2, c3 = st.columns(3)
    if c1.button("刷新数据"):
        st.info("请在终端执行数据管道命令。")
    if c2.button("运行年度循环"):
        st.info("请在终端执行 run.py。")
    if c3.button("重新生成仪表盘"):
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "python/scripts/build_dashboard.py")])
        st.success("已重新生成 web/dashboard.html")

# ================= v0.2.0 新增面板 =================
with tabs[8]:
    st.header("v0.2.0 真实数据覆盖")
    cov_real = _load_report("data/reports/coverage_report_real.json")
    cov_fix = _load_report("data/reports/coverage_report.json")
    c1, c2, c3 = st.columns(3)
    c1.metric("真实数据行", cov_real.get("total_rows", 0))
    c2.metric("合成数据行(隔离)", cov_fix.get("total_rows", 0))
    c3.metric("synthetic", str(cov_real.get("synthetic", "-")))
    st.caption(f"真实来源: {cov_real.get('sources', [])}")
    cells = cov_real.get("by_market_year_domain", [])
    if cells:
        st.dataframe(pd.DataFrame(cells), use_container_width=True)
        # 每市场每年行情缺失率
        price = [c for c in cells if c["domain"] == "price"]
        dfp = pd.DataFrame(price)
        if not dfp.empty:
            dfp["缺失率%"] = ((1 - dfp["rows"] / 250).clip(lower=0) * 100).round(1)
            st.subheader("行情数据缺失率（基准 250 交易日/年）")
            st.dataframe(dfp[["market", "year", "rows", "缺失率%"]], use_container_width=True)
    st.subheader("明确缺口（未用合成补齐）")
    st.dataframe(pd.DataFrame(cov_real.get("missing_ranges", [])), use_container_width=True)

with tabs[9]:
    st.header("v0.2.0 模型对比（统一历史边界）")
    mc = _load_report("data/reports/model_comparison.json")
    st.caption(f"数据源: {mc.get('data_source')} ｜ 训练: {mc.get('train_range')} ｜ 测试: {mc.get('test_range')} ｜ trained_to: {mc.get('trained_to')}")
    res = mc.get("results", {})
    if res:
        rows = [{"模型": k, "版本": v.get("version"), "MAE": v.get("mae"),
                 "RMSE": v.get("rmse"), "方向准确率": v.get("direction_accuracy"),
                 "耗时s": v.get("elapsed_seconds"), "状态": v.get("status")}
                for k, v in res.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        best = min((v for v in res.values() if v.get("status") == "ok"),
                   key=lambda x: x.get("mae", 9e9), default=None)
        if best:
            st.success(f"最低 MAE: {best.get('version')} = {best.get('mae')}")
    else:
        st.info("先运行 python/scripts/model_benchmark.py")

with tabs[10]:
    st.header("v0.2.0 性能与内存")
    pr = _load_report("data/reports/performance_report.json")
    if pr:
        st.subheader("加速比与一致性")
        st.json(pr.get("summary", {}))
        st.write("结果一致性:", "✅ 全部通过" if pr.get("consistency", {}).get("all_consistent") else "✗ 失败")
        st.subheader("逐项基准（5 轮中位数）")
        rows = [{"任务": k, "耗时s": v["elapsed_seconds"],
                 "Python峰值MB": v["mem_peak_mb"], "进程RSS_MB": v.get("rss_mb")}
                for k, v in pr.get("results", {}).items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption("注：tracemalloc 不计量 Arrow/Polars/DuckDB 的 C++ 分配，进程 RSS(ru_maxrss) 为真实峰值。")
    else:
        st.info("先运行 python/scripts/perf_benchmark.py")

with tabs[11]:
    st.header("v0.2.0 验收与防泄漏")
    tr = _load_report("data/reports/test_report.json")
    st.write("测试:", tr.get("pytest", {}).get("summary", "未运行"))
    acc = tr.get("acceptance", {})
    st.dataframe(pd.DataFrame([{"检查项": k, "结果": "通过" if v else "失败"}
                               for k, v in acc.items()]), use_container_width=True)
    st.subheader("快照 Hash 校验与冻结状态")
    kb = KnowledgeBase()
    from quant.contracts import PredictionSpec as _PS
    for s in meta.query("SELECT * FROM snapshots ORDER BY asof_year"):
        spec = _PS.for_year(s["asof_year"] + 1, 2000, s["snapshot_id"], "dash", "feature_v1")
        try:
            vr = kb.verify_snapshot(s["asof_year"], spec)
            st.write(f"- {s['snapshot_id']}: 冻结={'是' if s['sealed'] else '否'}, "
                     f"Hash={'一致' if vr['checks'].get('hashes_match') else '不符'}, "
                     f"未来行={vr['checks'].get('future_rows', '-')}")
        except Exception as e:
            st.write(f"- {s['snapshot_id']}: 校验异常 {e}")
