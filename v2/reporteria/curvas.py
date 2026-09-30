"""Curvas de tasas: reales/nominales desde los CSV corporativos (Carga_*) y curvas de drops desde Bloomberg (CURVE_TENOR_RATES).

Toda curva sale como DataFrame[dias, tasa] ordenado por plazo, con la tasa en DECIMAL (los CSV y BBG entregan %; basis en bps).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import alertas
from .adaptadores.bbg import Bloomberg
from .config import CURVAS_DROPS, CURVAS_SIN_FALLBACK_AUTO

DIAS = {"D": 1, "W": 7, "M": 30.4375, "Y": 365.25}
CAMPO_CURVA = "CURVE_TENOR_RATES"


def tenor_a_dias(tenor) -> float:
    m = re.match(r"^(\d+)(D|W|M|Y)$", str(tenor).strip().upper())
    return DIAS[m.group(2)] * int(m.group(1)) if m else np.nan


def interpolar(dias: float, curva: pd.DataFrame) -> tuple[float, bool]:
    """(tasa, extrapolado): lineal en el plazo, plana fuera de los extremos. NaN si la curva está vacía."""
    c = curva.dropna(subset=["dias", "tasa"]).sort_values("dias") if len(curva) else curva
    if not len(c):
        return np.nan, False
    d, t = c["dias"].to_numpy(dtype=float), c["tasa"].to_numpy(dtype=float)
    if dias <= d[0]:
        return float(t[0]), dias < d[0]
    if dias >= d[-1]:
        return float(t[-1]), dias > d[-1]
    return float(np.interp(dias, d, t)), False


def leer_curvas_csv(path: Path, settle: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Carga_Indexes / Carga_CurvasSoberanas (sep '|'): {Curve: DataFrame[dias, tasa]} con la fecha más reciente ≤ settle."""
    d = pd.read_csv(path, sep="|", encoding="latin1")
    d.columns = [str(c).strip() for c in d.columns]
    d["Fecha"] = pd.to_datetime(d["Fecha"], errors="coerce")
    d["dias"] = d["tenor"].map(tenor_a_dias)
    d["tasa"] = pd.to_numeric(d["mid_yield"], errors="coerce") / 100
    d = d[(d["Fecha"] <= settle) & d["dias"].notna() & d["tasa"].notna()]
    out = {}
    for nombre, g in d.groupby("Curve"):
        g = g[g["Fecha"] == g["Fecha"].max()]
        out[str(nombre)] = g[["dias", "tasa"]].sort_values("dias").reset_index(drop=True)
    return out


def _normalizar_bds(d: pd.DataFrame, divisor: float) -> pd.DataFrame:
    """CURVE_TENOR_RATES → DataFrame[tenor, ticker, dias, tasa]. Columnas de BBG en cualquier capitalización."""
    if d is None or d.empty:
        return pd.DataFrame(columns=["tenor", "ticker", "dias", "tasa"])
    cols = {str(c).strip().lower(): c for c in d.columns}
    c_ten = cols.get("tenor"); c_mid = next((cols[c] for c in cols if "mid" in c), None)
    c_tkr = next((cols[c] for c in cols if "ticker" in c), None)
    if not (c_ten and c_mid):
        return pd.DataFrame(columns=["tenor", "ticker", "dias", "tasa"])
    out = pd.DataFrame({"tenor": d[c_ten].astype(str).str.strip().str.upper(),
                        "ticker": d[c_tkr].astype(str).str.strip() if c_tkr else "",
                        "tasa": pd.to_numeric(d[c_mid], errors="coerce") / divisor})
    out["dias"] = out["tenor"].map(tenor_a_dias)
    return out.dropna(subset=["dias", "tasa"]).sort_values("dias").reset_index(drop=True)


def _fallback_extremos(bbg: Bloomberg, curva: pd.DataFrame, prefijo: str | None, fecha: str, divisor: float) -> tuple[pd.DataFrame, list[str]]:
    """Si al bulk le faltan 1Y/30Y y los tickers son PREFIJO+AÑO, se piden puntuales (PX_LAST al cierre)."""
    if not prefijo or prefijo in CURVAS_SIN_FALLBACK_AUTO or curva.empty:
        return curva, []
    anios, sufijo = set(), ""
    for t in curva["ticker"]:
        base, _, resto = str(t).partition(" ")
        m = re.match(rf"^{re.escape(prefijo)}(\d+)$", base)
        if m:
            anios.add(int(m.group(1))); sufijo = sufijo or (" " + resto if resto else "")
    if not anios:
        return curva, []
    faltan = [f"{prefijo}{a}{sufijo}" for a in (1, 30) if a not in anios]
    if not faltan:
        return curva, []
    res = bbg.historico(faltan, "PX_LAST", fecha)
    nuevos = []
    for t, v in res.items():
        if pd.isna(v):
            continue
        anio = int(re.match(rf"^{re.escape(prefijo)}(\d+)", t).group(1))
        nuevos.append({"tenor": f"{anio}Y", "ticker": t, "tasa": float(v) / divisor, "dias": anio * 365.25})
    if nuevos:
        curva = pd.concat([curva, pd.DataFrame(nuevos)], ignore_index=True).sort_values("dias").reset_index(drop=True)
    return curva, [n["ticker"] for n in nuevos]


def curvas_drops(bbg: Bloomberg, monedas: set[str], fecha: str) -> tuple[dict[str, dict[str, pd.DataFrame]], pd.DataFrame, pd.DataFrame]:
    """({ccy: {"local": df, "basis": df | None, "usd": df}}, tabla de curvas para el Excel, alertas). Solo monedas de hedge presentes."""
    out, tabla, al = {}, [], []
    for ccy in sorted(m for m in monedas if m):
        cfg = CURVAS_DROPS.get(ccy)
        if not cfg:
            al.append(alertas.emitir("MONEDA_SIN_CURVAS_DROP", "ALTA", detalle=f"{ccy}: sin curvas en config.CURVAS_DROPS; hedgeados a {ccy} sin drop propio", ambito="CORRIDA"))
            continue
        piezas = {}
        for pieza in ("local", "basis", "usd"):
            if pieza not in cfg:
                continue
            divisor = 10_000 if pieza == "basis" else 100
            d = _normalizar_bds(bbg.bds(f"{cfg[pieza]} Index", CAMPO_CURVA, CURVE_DATE=fecha), divisor)
            d, rescatados = _fallback_extremos(bbg, d, cfg.get(f"{pieza}_prefix"), fecha, divisor)
            if d.empty:
                al.append(alertas.emitir("CURVA_SIN_DATOS", "ALTA", detalle=f"{ccy}_{pieza} ({cfg[pieza]} Index): BBG no devolvió CURVE_TENOR_RATES", ambito="CORRIDA"))
            piezas[pieza] = d
            tabla.append(d.assign(Curva=f"{ccy}_{pieza}", Ticker_Curva=cfg[pieza], Fallback=";".join(rescatados)))
        out[ccy] = piezas
    return out, (pd.concat(tabla, ignore_index=True) if tabla else pd.DataFrame()), alertas.juntar(*al)
