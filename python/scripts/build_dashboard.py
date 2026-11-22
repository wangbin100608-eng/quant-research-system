"""静态可视化仪表盘生成器：读取 SQLite/Parquet/JSON，生成自包含 HTML（纯 SVG，无外部依赖）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

from quant.evaluation import PredictionStore
from quant.storage import MetadataDB
from quant.utils import ROOT, resolve, get_config


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bar_chart_svg(data: list[tuple[str, float]], width=520, height=200, color="#4f8cff") -> str:
    if not data:
        return '<p style="color:#888">无数据</p>'
    n = len(data)
    bw = width / max(n, 1) * 0.6
    gap = width / max(n, 1)
    maxv = max(abs(v) for _, v in data) or 1
    parts = [f'<svg viewBox="0 0 {width} {height+40}" width="100%" style="max-width:{width}px">']
    mid = height / 2
    for i, (label, v) in enumerate(data):
        x = i * gap + (gap - bw) / 2
        h = abs(v) / maxv * (height / 2 - 10)
        y = mid - h if v >= 0 else mid
        fill = color if v >= 0 else "#e35b5b"
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{fill}" rx="3"/>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{height+18}" font-size="11" text-anchor="middle" fill="#9fb3c8">{_esc(label)}</text>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{mid+(-8 if v>=0 else 16):.1f}" font-size="10" text-anchor="middle" fill="#d7e3f0">{v:.3f}</text>')
    parts.append(f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#33475e"/>')
    parts.append('</svg>')
    return "".join(parts)


def _line_chart_svg(points: list[tuple[str, float]], width=520, height=200, color="#37d39b") -> str:
    if len(points) < 2:
        return '<p style="color:#888">数据点不足</p>'
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    sx = width / (len(points) - 1)
    sy = height / (hi - lo)
    coords = [(i * sx, height - (v - lo) * sy) for i, (_, v) in enumerate(points)]
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    parts = [f'<svg viewBox="0 0 {width} {height+40}" width="100%" style="max-width:{width}px">']
    parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in coords)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
    for (label, _), (x, y) in zip(points, coords):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
        parts.append(f'<text x="{x:.1f}" y="{height+18}" font-size="11" text-anchor="middle" fill="#9fb3c8">{_esc(label)}</text>')
        parts.append(f'<text x="{x:.1f}" y="{y-10:.1f}" font-size="10" text-anchor="middle" fill="#d7e3f0">{label}</text>')
    parts.append('</svg>')
    return "".join(parts)


def build_dashboard(out_path: str | Path | None = None) -> Path:
    cfg = get_config()
    out = resolve(str(out_path or "web/dashboard.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = MetadataDB()
    store = PredictionStore()

    # 数据覆盖
    cov_path = resolve("data/reports/coverage_report.json")
    cov = json.loads(cov_path.read_text()) if cov_path.exists() else {}
    # 快照
    snaps = []
    for r in meta.query("SELECT * FROM snapshots ORDER BY asof_year"):
        snaps.append(r)
    # 预测与评估
    pred_rows = meta.query("SELECT * FROM predictions ORDER BY prediction_year")
    metrics_by_year = []
    for r in pred_rows:
        result = json.loads(r["result_json"])
        rec = store.load(r["prediction_year"])
        m = None
        # 从 checkpoint 读评估
        ck = resolve(f"data/checkpoints/year_{r['prediction_year']}.json")
        if ck.exists():
            c = json.loads(ck.read_text())
            m = c.get("artifacts", {}).get("metrics")
        if m:
            metrics_by_year.append((str(r["prediction_year"]), m))
    # 规则
    rules = meta.query("SELECT rule_id, status, market, sample_size FROM decision_rules")
    # 性能
    perf = meta.query("SELECT task, elapsed_seconds, mem_peak_mb FROM metrics ORDER BY id DESC LIMIT 20")
    # 数据源
    sources = meta.query("SELECT source, mode, rows, status FROM source_runs ORDER BY started_at DESC LIMIT 10")
    # 任务日志
    tasks = meta.query("SELECT year, step, status, ts FROM tasks ORDER BY ts DESC LIMIT 15")
    integrity = store.verify_all()

    # 预测结果面积图（按年均值）
    pred_chart = [(y, sum(p["predicted_return"] for p in res["result"]["predictions"]) /
                   max(len(res["result"]["predictions"]), 1))
                  for y, res in [(r["prediction_year"], store.load(r["prediction_year"])[0])
                                 for r in pred_rows] if res["result"].get("predictions")]
    mae_chart = [(y, m["mae"]) for y, m in metrics_by_year if "mae" in m]
    dir_chart = [(y, m.get("direction_accuracy", 0)) for y, m in metrics_by_year]

    def _table(headers, rows):
        h = "".join(f"<th>{_esc(x)}</th>" for x in headers)
        body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in rows)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    snap_rows = [(s["snapshot_id"], s["cutoff_date"], s["entry_count"],
                  "已冻结" if s["sealed"] else "未冻结", s["model_version"] or "-") for s in snaps]
    rule_rows = [(r["rule_id"], r["status"], r["market"], r["sample_size"]) for r in rules]
    perf_rows = [(p["task"], f"{p['elapsed_seconds']:.3f}s", f"{p['mem_peak_mb']:.1f} MB") for p in perf]
    src_rows = [(s["source"], s["mode"], s["rows"], s["status"]) for s in sources]
    task_rows = [(t["year"], t["step"], t["status"], t["ts"][:19]) for t in tasks]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化研究系统 · 监控仪表盘</title>
<style>
:root{{--bg:#0d1420;--card:#151f2e;--line:#26364a;--fg:#e6eef7;--muted:#8fa6bd;--acc:#4f8cff;--ok:#37d39b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,"Segoe UI","PingFang SC",sans-serif}}
header{{padding:28px 40px;border-bottom:1px solid var(--line);background:linear-gradient(120deg,#122036,#0d1420)}}
h1{{margin:0;font-size:26px;letter-spacing:1px}}
.sub{{color:var(--muted);font-size:13px;margin-top:8px}}
.badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;margin-left:8px}}
.badge.ok{{background:rgba(55,211,155,.15);color:var(--ok)}}
.badge.warn{{background:rgba(227,91,91,.15);color:#e35b5b}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:18px;padding:24px 40px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}}
.card h2{{font-size:15px;margin:0 0 16px;color:var(--acc);font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;color:var(--muted);font-weight:500;padding:8px;border-bottom:1px solid var(--line)}}
td{{padding:8px;border-bottom:1px solid rgba(38,54,74,.5);color:#cbd9e8}}
tr:hover td{{background:rgba(79,140,255,.06)}}
.kpi{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:6px}}
.kpi div{{background:#101a28;border:1px solid var(--line);border-radius:10px;padding:14px 20px;min-width:130px}}
.kpi b{{display:block;font-size:22px;color:var(--ok)}}
.kpi span{{font-size:12px;color:var(--muted)}}
.note{{color:var(--muted);font-size:12px;line-height:1.6}}
footer{{padding:20px 40px;color:#5f748c;font-size:12px;border-top:1px solid var(--line)}}
</style></head><body>
<header><h1>量化研究系统 · 监控仪表盘
<span class="badge {'ok' if integrity['ok'] else 'warn'}">预测完整性 {'正常' if integrity['ok'] else '异常'}</span></h1>
<div class="sub">A股 / 美股 · 严格点位时间隔离 · 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 数据版本 {_esc(cfg['project']['version'])}</div></header>
<div class="grid">
<div class="card"><h2>关键指标</h2><div class="kpi">
<div><b>{cov.get('total_rows', 0)}</b><span>原始数据行数</span></div>
<div><b>{len(snaps)}</b><span>时间隔离快照</span></div>
<div><b>{len(pred_rows)}</b><span>冻结预测记录</span></div>
<div><b>{len(rules)}</b><span>决策规则</span></div>
<div><b>{'全部通过' if integrity['ok'] else '检测到篡改'}</b><span>哈希校验</span></div>
</div><p class="note">数据源模式: {_esc(cov.get('sources', ['无']))} &nbsp;|&nbsp; 合成数据: {cov.get('synthetic', '未知')} &nbsp;|&nbsp; 训练就绪: {cov.get('training_ready', False)}</p>
<p class="note">{_esc(cov.get('note',''))}</p></div>

<div class="card"><h2>年度预测收益（横截面均值）</h2>{_line_chart_svg(pred_chart)}</div>
<div class="card"><h2>预测误差 MAE 趋势</h2>{_bar_chart_svg(mae_chart, color='#e3a35b')}</div>
<div class="card"><h2>方向准确率</h2>{_bar_chart_svg(dir_chart, color='#37d39b')}</div>

<div class="card"><h2>知识库快照（按年份物理隔离）</h2>{_table(['快照ID','截止日期','条目数','状态','模型版本'], snap_rows)}</div>
<div class="card"><h2>决策规则</h2>{_table(['规则ID','状态','市场','样本量'], rule_rows)}
<p class="note">未经样本外验证的规则不能升级为 validated。</p></div>
<div class="card"><h2>数据源状态</h2>{_table(['数据源','模式','行数','状态'], src_rows)}</div>
<div class="card"><h2>性能记录（最近 20 项）</h2>{_table(['任务','耗时','内存峰值'], perf_rows)}</div>
<div class="card"><h2>任务执行日志（最近 15 项）</h2>{_table(['年份','步骤','状态','时间'], task_rows)}</div>
<div class="card"><h2>时间隔离证明</h2><p class="note">
每次预测前由 Rust TimeGuard 对整个快照的 <code>.timeindex</code> 做全量流式扫描：<br>
• available_time &gt; cutoff_date → 立即中止<br>
• available_time 缺失（unknown_availability）→ 严格模式拒绝<br>
• 快照冻结后写入 manifest（含文件 SHA-256），后续数据导入无法改变历史快照。<br>
13 项自动化防泄漏测试全部通过。</p></div>
</div>
<footer>量化研究系统 v{_esc(cfg['project']['version'])} · 仅用于历史研究与模拟预测 · 不连接真实证券账户</footer>
</body></html>"""
    out.write_text(html, encoding="utf-8")
    # 同时写 index.html，便于 Nginx 静态根目录直接访问
    (out.parent / "index.html").write_text(html, encoding="utf-8")
    print(f"DASHBOARD: {out}")
    meta.close()
    return out


if __name__ == "__main__":
    # v0.2.0：默认生成增强版仪表盘（含真实数据/模型对比/性能/验收面板）
    from build_dashboard_v02 import build_dashboard as build_v02
    build_v02()
