from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configurar_log(dir_logs: Path, nombre: str = "reporteria", prefijo: str = "corrida") -> logging.Logger:
    dir_logs.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(nombre)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(dir_logs / f"{prefijo}_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log
