"""Siembra la caché Bloomberg / FX a partir de los outputs del pipeline legacy (para correr un cierre sin terminal).

    reporteria importar-cache-legacy --fecha 20260731 --legacy <carpeta con METRICAS_/CSHF_/CURVAS_DROPS_ o LEGACY_*>
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .adaptadores.bbg import _archivo, _ruta_bds
from .modelo import limpiar_txt

CAMPO_XCCY = "YAS_XCCY_FIXED_COUPON_EQUIVALENT"


def _buscar(legacy: Path, nombre: str, fecha: str) -> Path | None:
    for cand in (legacy / f"{nombre}_{fecha}.xlsx", legacy / f"LEGACY_{nombre}_{fecha}.xlsx",
                 legacy / "02_OUTPUTS" / fecha / f"{nombre}_{fecha}.xlsx"):
        if cand.exists():
            return cand
    return None


def _ticker(isin: str, origen: str) -> str:
    o = str(origen).strip().upper()
    if o.startswith("HERMANO:"):
        return f"{o.split(':', 1)[1].strip()} Corp"
    return f"{isin}@BGN Corp" if o == "BGN" else f"{isin} Corp"


def _guardar_bdp(cache: Path, campo: str, fecha: str, overrides: dict, valores: dict[str, float]) -> Path:
    p = _archivo(cache, campo, fecha, overrides)
    if p.exists():
        prev = pd.read_csv(p)
        valores = {**dict(zip(prev["ticker"].astype(str), prev["valor"])), **valores}
    cache.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": list(valores), "valor": list(valores.values())}).to_csv(p, index=False)
    return p


def importar_cache_legacy(legacy: Path, cache: Path, fecha: str) -> dict[str, int]:
    """Devuelve conteos por archivo generado. Los valores quedan como los entrega BBG (yields en %)."""
    legacy, cache, out = Path(legacy), Path(cache), {}
    met = _buscar(legacy, "METRICAS", fecha)
    if met:
        xl = pd.ExcelFile(met)
        m = pd.concat([xl.parse(h) for h in ("con_ISIN", "sin_ISIN") if h in xl.sheet_names], ignore_index=True)
        m["ISIN"] = limpiar_txt(m["ISIN"])
        m = m[m["ISIN"].ne("")].drop_duplicates("ISIN")
        yld, dur, xccy = {}, {}, {}
        for _, r in m.iterrows():
            if pd.notna(r.get("BBG_Yield")):
                yld[_ticker(r["ISIN"], r.get("Origen_BBG_Yield", ""))] = float(r["BBG_Yield"])
            if pd.notna(r.get("BBG_Duration")):
                dur[_ticker(r["ISIN"], r.get("Origen_BBG_Duration", ""))] = float(r["BBG_Duration"])
            if pd.notna(r.get("BBG_XCCY_Yield")) and str(r.get("Hedge_Currency", "")).strip():
                xccy.setdefault(str(r["Hedge_Currency"]).strip().upper(), {})[_ticker(r["ISIN"], r.get("Origen_BBG_XCCY", ""))] = float(r["BBG_XCCY_Yield"])
        ov = {"settle_dt": fecha}
        out["YAS_BOND_YLD"] = len(yld); _guardar_bdp(cache, "YAS_BOND_YLD", fecha, ov, yld)
        out["YAS_MOD_DUR"] = len(dur); _guardar_bdp(cache, "YAS_MOD_DUR", fecha, ov, dur)
        for ccy, vals in xccy.items():
            out[f"XCCY_{ccy}"] = len(vals)
            _guardar_bdp(cache, CAMPO_XCCY, fecha, {**ov, "YAS_XCCY_FOREIGN_CURRENCY": ccy}, vals)
    cshf = _buscar(legacy, "CSHF", fecha)
    if cshf:
        td = pd.read_excel(cshf, sheet_name="td_detalle")
        td["ISIN"] = limpiar_txt(td["ISIN"])
        n = 0
        for isin, g in td[td["ISIN"].ne("")].groupby("ISIN"):
            g = g.drop_duplicates("Fecha").sort_values("Fecha")
            p = _ruta_bds(cache, "DES_CASH_FLOW", f"{isin} Corp")
            p.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"Fecha": pd.to_datetime(g["Fecha"]).dt.strftime("%Y-%m-%d"), "Cupon": float("nan"),
                          "Principal": float("nan"), "Flujo": g["Flujo_baseFACE"].astype(float),
                          "Face": g["face_bbg"].astype(float)}).to_csv(p, index=False)
            n += 1
        out["DES_CASH_FLOW"] = n
    return out
