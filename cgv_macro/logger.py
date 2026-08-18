"""파일 + 콘솔 로깅 설정."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(log_dir: str, level: str = "INFO") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("cgv_macro")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:  # 재호출 시 중복 핸들러 방지
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 장시간 실행 대비 회전 로그(파일당 5MB, 5개 보관)
    fh = RotatingFileHandler(
        os.path.join(log_dir, "cgv_macro.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger
