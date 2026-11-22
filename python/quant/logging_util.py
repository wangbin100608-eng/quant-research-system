"""日志：轮转文件 + 控制台，按任务阶段可追踪。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .utils import ROOT, get_config


def setup_logger(name: str = "quant") -> logging.Logger:
    cfg = get_config()["runtime"]
    log_dir = ROOT / cfg["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    level = getattr(logging, cfg.get("log_level", "INFO"))
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = RotatingFileHandler(
        log_dir / "system.log",
        maxBytes=cfg.get("log_rotation_mb", 10) * 1024 * 1024,
        backupCount=cfg.get("log_backup_count", 5),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


log = setup_logger()
