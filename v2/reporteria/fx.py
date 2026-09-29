"""Tipos de cambio: beemining (SQL) y paridades (Excel), con los pares derivados que usa el negocio."""
from __future__ import annotations

# Moneda de riesgo del papel → par FX a buscar (USD por unidad de la moneda del papel)
RISK_CCY_TO_FX = {"CLF": "USDCLF", "UVR COSTER": "USDUVR", "UDI": "USDUDI", "UI CURNCY": "USDUYU", "UF": "USDCLF"}
INSTRUMENTOS_BEE = ["USDMXN Curncy", "USDBRL Curncy", "CLFXDOOB_sindesf", "USDCLF Curncy", "USDUVR Curncy", "USDPEN Curncy",
                    "USDPYG Curncy", "USDUYU Curncy", "USDCOP Curncy", "USDARS Curncy", "USDGBP Curncy", "USDEUR Curncy",
                    "USDUDI Curncy", "USDDOP Curncy"]


def nominal_bee(codigo: str) -> str:
    return "USDCLP" if codigo == "CLFXDOOB_sindesf" else codigo.replace(" Curncy", "").strip()


def _derivar(d: dict) -> dict:
    d = {k: float(v) for k, v in d.items() if v is not None and float(v) > 0}
    d.setdefault("USDUSD", 1.0)
    for inv, directo in (("CLFUSD", "USDCLF"), ("EURUSD", "USDEUR"), ("GBPUSD", "USDGBP"), ("UVR CurncyUSD", "USDUVR")):
        if d.get(inv) and directo not in d:
            d[directo] = 1 / d[inv]
    if d.get("USDCLF") and d.get("USDCLP"):
        d["CLFCLP"] = d["USDCLP"] / d["USDCLF"]
    return d


def armar_fx(bee: dict, paridades: dict) -> tuple[dict, dict]:
    """(fx_bee, fx_par) con pares derivados. Ambos compiten en la búsqueda de escala: gana el que calza con MVBook."""
    return _derivar(dict(bee or {})), _derivar(dict(paridades or {}))
