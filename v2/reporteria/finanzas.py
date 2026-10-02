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
    t = np.asarray(_anios(fechas, min(pd.Timestamp(f) for f in fechas)), dtype=float)
    c = np.asarray(flujos, dtype=float)

    def vpn(r):
        with np.errstate(all="ignore"):            # flujos a 80+ años: (1+r)**t desborda en los extremos del bracket → inf/nan, no excepción
            return float(np.sum(c * np.power(1.0 + r, -t)))
    try:
        return float(brentq(vpn, -0.9999, 100.0, maxiter=200))
    except (ValueError, RuntimeError, ZeroDivisionError, OverflowError, FloatingPointError):
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


# ── Conversión de yields (todo en decimal) ───────────────────────────────────────────────────────────────────────
def reexpresar_duracion(dur_mod: float, y_origen: float, y_destino: float) -> float:
    """La Macaulay no cambia al cambiar la moneda de la yield (mismos flujos); la Modified sí: Mac/(1+y)."""
    return dur_mod * (1 + y_origen) / (1 + y_destino)


def breakeven(y_real: float, dur_mod: float, r_real: float, r_nom: float) -> tuple[float, float, float]:
    """(yield_local, duration_local, ajuste). ajuste = (1+r_nom)/(1+r_real) − 1 al plazo de la duration."""
    ajuste = (1 + r_nom) / (1 + r_real) - 1
    y_local = (1 + y_real) * (1 + ajuste) - 1
    return y_local, reexpresar_duracion(dur_mod, y_real, y_local), ajuste


def sumar_indice(spread: float, dur_mod: float, nivel: float) -> tuple[float, float]:
    """Flotante con TD propia (solo spread): se compone con el nivel spot del índice. Aproximación: no proyecta la forward."""
    y = (1 + spread) * (1 + nivel) - 1
    return y, reexpresar_duracion(dur_mod, spread, y)


def drop(y_usd: float, r_local: float, r_usd: float, r_basis: float = 0.0) -> tuple[float, float]:
    """(yield_drop, drop): drop = (local + basis) − usd; la yield se traslada aditivamente y la duration no cambia."""
    d = r_local + r_basis - r_usd
    return y_usd + d, d
