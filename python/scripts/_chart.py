"""静态图表与表格 SVG 辅助（自包含，无外部依赖）。"""
from __future__ import annotations


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar_chart_svg(data, width=460, height=180, color="#4f8cff") -> str:
    if not data:
        return '<p style="color:#888">无数据</p>'
    n = len(data)
    bw = width / max(n, 1) * 0.6
    gap = width / max(n, 1)
    maxv = max(abs(float(v)) for _, v in data) or 1
    parts = [f'<svg viewBox="0 0 {width} {height+40}" width="100%" style="max-width:{width}px">']
    mid = height / 2
    for i, (label, v) in enumerate(data):
        v = float(v)
        x = i * gap + (gap - bw) / 2
        h = abs(v) / maxv * (height / 2 - 10)
        y = mid - h if v >= 0 else mid
        fill = color if v >= 0 else "#e35b5b"
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{fill}" rx="3"/>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{height+18}" font-size="11" text-anchor="middle" fill="#9fb3c8">{esc(label)}</text>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{mid+(-8 if v>=0 else 16):.1f}" font-size="10" text-anchor="middle" fill="#d7e3f0">{v:.3f}</text>')
    parts.append(f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#33475e"/>')
    parts.append('</svg>')
    return "".join(parts)


def line_chart_svg(points, width=460, height=180, color="#37d39b") -> str:
    if len(points) < 2:
        return '<p style="color:#888">数据点不足</p>'
    vals = [float(v) for _, v in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    sx = width / (len(points) - 1)
    sy = height / (hi - lo)
    coords = [(i * sx, height - (v - lo) * sy) for i, v in enumerate(vals)]
    parts = [f'<svg viewBox="0 0 {width} {height+40}" width="100%" style="max-width:{width}px">']
    parts.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in coords) +
                 f'" fill="none" stroke="{color}" stroke-width="2.5"/>')
    for (label, _), (x, y) in zip(points, coords):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
        parts.append(f'<text x="{x:.1f}" y="{height+18}" font-size="11" text-anchor="middle" fill="#9fb3c8">{esc(label)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def table(headers, rows) -> str:
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"
