"""Escala de precio/cantidad y FX de cada posición: no se asume, se prueba y gana lo que reproduce MVBook.

LocalPrice y Qty/OriginalFace del CUBO vienen en unidades variables por papel. Se prueban los candidatos (sP, sQ) y los
FX disponibles y gana la combinación que cumple tres checks contra MVBook (moneda del fondo):
  1. |P_ef × OF_ef × Factor / FX − MVBook| / |MVBook| <= umbral_pct
  2. TC implícito dentro de ±umbral_tc del FX (salvo FX = 1)
  3. ratio OF_ef × Factor / FX / |MVBook| en [ratio_min, ratio_max]  (distingue escalas que dan el mismo P×Q)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .fx import RISK_CCY_TO_FX


@dataclass(frozen=True)
class EscalaCfg:
    candidatos: tuple = ((0.001, 1000), (0.01, 1000), (0.01, 100), (0.01, 1), (1, 1))
    umbral_pct: float = 0.005
    umbral_tc: float = 0.20
    ratio_min: float = 0.10
    ratio_max: float = 8.0


def candidatos_fx(ccy: str, fx_bee: dict, fx_par: dict, base_ccy: str) -> list[tuple[str, float]]:
    """(etiqueta, unidades del papel por unidad de moneda del fondo). Sin prioridad: todos compiten."""
    c = str(ccy).strip().upper()
    if c == base_ccy.upper():
        return [(f"{c}{c}", 1.0)]
    par_papel = RISK_CCY_TO_FX.get(c, f"USD{c}")
    papel = _con_prefijo(par_papel, fx_bee, fx_par) if c != "USD" else [("USDUSD", 1.0)]
    if base_ccy.upper() == "USD":
        return papel
    fondo = _con_prefijo(f"USD{base_ccy.upper()}", fx_bee, fx_par)
    out, vistos = [], set()
    for lf, vf in fondo:
        for lp, vp in papel:
            v = vp / vf
            if round(v, 12) not in vistos:
                out.append((f"{lp}/{lf}", v)); vistos.add(round(v, 12))
    return out or papel


def _con_prefijo(par: str, fx_bee: dict, fx_par: dict) -> list[tuple[str, float]]:
    out, vistos = [], set()
    for etq, d in (("bee", fx_bee), ("par", fx_par)):
        for k, v in d.items():
            if k.startswith(par) and pd.notna(v) and v > 0 and round(v, 6) not in vistos:
                out.append((f"{etq}_{k}", float(v))); vistos.add(round(v, 6))
    return out


def evaluar_escala(p: float, of: float, factor: float, mv: float, fx: float, cfg: EscalaCfg) -> list[dict]:
    res = []
    for sP, sQ in cfg.candidatos:
        p_ef, of_ef = p * sP, of * sQ
        pq = p_ef * of_ef * factor / fx
        diff = abs(pq - mv) / abs(mv) if mv else float("nan")
        tc = (p_ef * of_ef * factor) / abs(mv) if mv else float("nan")
        sanity = fx == 1.0 or (pd.notna(tc) and abs(tc - fx) / fx <= cfg.umbral_tc)
        ratio = (of_ef * factor / fx) / abs(mv) if (fx and mv) else float("nan")
        ratio_ok = pd.notna(ratio) and cfg.ratio_min <= ratio <= cfg.ratio_max
        res.append(dict(sP=sP, sQ=sQ, diff_pct=diff, tc=tc, sanity_ok=bool(sanity), ratio=ratio, ratio_ok=bool(ratio_ok),
                        all_ok=bool(pd.notna(diff) and diff <= cfg.umbral_pct and sanity and ratio_ok)))
    return res


def elegir_escala_fx(lp: float, of: float, factor: float, mv: float, cands: list[tuple[str, float]], cfg: EscalaCfg) -> dict:
    """Primer (FX, sP, sQ) que pasa los tres checks; si ninguno, el mejor por (ratio_ok, diff) marcado FALLBACK."""
    for etq, fx in cands:
        ok = next((i for i in evaluar_escala(lp, of, factor, mv, fx, cfg) if i["all_ok"]), None)
        if ok:
            return dict(sP=ok["sP"], sQ=ok["sQ"], FX=fx, FX_Fuente=etq, Escala_Flag="OK", Escala_Diff=ok["diff_pct"])
    mejor = None
    for etq, fx in cands:
        for i in evaluar_escala(lp, of, factor, mv, fx, cfg):
            if pd.isna(i["diff_pct"]):
                continue
            clave = (not i["ratio_ok"], i["diff_pct"])
            if mejor is None or clave < mejor[0]:
                mejor = (clave, i, etq, fx)
    if mejor is None:
        return dict(sP=float("nan"), sQ=float("nan"), FX=float("nan"), FX_Fuente="SIN_FX", Escala_Flag="SIN_FX", Escala_Diff=float("nan"))
    _, i, etq, fx = mejor
    return dict(sP=i["sP"], sQ=i["sQ"], FX=fx, FX_Fuente=f"FALLBACK_{etq}", Escala_Flag="FALLBACK", Escala_Diff=i["diff_pct"])
