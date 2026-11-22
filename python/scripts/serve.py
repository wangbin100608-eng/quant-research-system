"""容器默认入口：自动初始化（若数据缺失）后启动 Web 服务。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("QUANT_ROOT", "/app"))


def run(cmd: list[str]) -> int:
    print("RUN:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def main():
    raw = ROOT / "data/raw/raw_all.parquet"
    if not raw.exists():
        print("首次启动：抓取数据并运行年度循环", flush=True)
        run([sys.executable, str(ROOT / "python/scripts/data_pipeline.py"),
             "fetch", "--sources", "fixture"])
        run([sys.executable, str(ROOT / "python/scripts/run.py"),
             "run", "--start", "2002", "--end", "2004"])
        run([sys.executable, str(ROOT / "python/scripts/build_dashboard.py")])
    os.execv(sys.executable, [
        sys.executable, "-m", "streamlit", "run",
        str(ROOT / "python/dashboard/app.py"),
        "--server.port", os.environ.get("STREAMLIT_PORT", "8501"),
        "--server.address", "0.0.0.0", "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    main()
