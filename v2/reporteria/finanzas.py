"""Matemática financiera pura. Yields en decimal, tiempos en años ACT/365.25."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq


def _anios(fechas, desde) -> list[float]:
    return [(pd.Timestamp(f) - pd.Timestamp(desde)).days / 365.25 for f in fechas]


def xirr(flujos: list[float], fechas: list) -> float:
    """TIR efectiva anual de una serie de flujos fechados. NaN si no hay solución (todo cero, sin cambio de signo…)."""
    if len(flujos) < 2 or not any(abs(f) > 1e-12 for f in flujos[1:]):
        return float("nan")
    t = _anios(fechas, min(pd.Timestamp(f) for f in fechas))

    def vpn(r):
        return sum(c / (1 + r) ** ti for c, ti in zip(flujos, t))
    try:
        return float(brentq(vpn, -0.9999, 100.0, maxiter=200))
    except (ValueError, RuntimeError):
        return float("nan")


def duracion(flujos_fut: list[float], fechas_fut: list, settle, y_ef: float) -> tuple[float, float]:
    """(Macaulay, Modified) de los flujos futuros descontados a la propia yield efectiva."""
    if not flujos_fut or pd.isna(y_ef):
        return float("nan"), float("nan")
    t = _anios(fechas_fut, settle)
    pv = [cf / (1 + y_ef) ** ti for cf, ti in zip(flujos_fut, t)]
    tot = sum(pv)
    if tot <= 0:
        return float("nan"), float("nan")
    mac = sum(ti * p for ti, p in zip(t, pv)) / tot
    return mac, mac / (1 + y_ef)
