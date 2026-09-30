"""Agregados por fondo: AW = Σ(y·MV)/ΣMV y DW = Σ(y·MV·D)/Σ(MV·D) a nivel ACTIVOS, PASIVOS y PATRIMONIO (A − P).

Todo lo que no tiene métrica entra con yield 0 y duration 0 (decisión del levantamiento). Pasivos conservan su métrica.
PATRIMONIO se obtiene con MV con signo (Asset +, Liability −), de modo que MV_PAT = MV_ACT − MV_PAS y
y_PAT = (A·y_A − P·y_P)/(A − P).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMENSIONES = ("Bucket", "Ficha_FI", "FX_Exposure", "Risk_Country", "Risk_Currency", "Fuente", "Conversion")
NIVELES = ("ACTIVOS", "PASIVOS", "PATRIMONIO")
COLS = ["ID_Fund", "Fondo", "Nivel", "Dimension", "Grupo", "N", "MV", "Peso_MV", "MV_Resuelto", "Cobertura", "Duration_AW", "AW", "DW"]


def _fila(sub: pd.DataFrame, mv_tot: float, **ids) -> dict:
    mv, y, d = sub["_mv"], sub["_y"], sub["_d"]
    s_mv, s_mvd = mv.sum(), (mv * d).sum()
    return {**ids, "N": len(sub), "MV": s_mv, "Peso_MV": s_mv / mv_tot if mv_tot else np.nan,
            "MV_Resuelto": mv[sub["Estado"].eq("RESUELTO")].sum(), "Cobertura": mv[sub["Estado"].eq("RESUELTO")].sum() / s_mv if s_mv else np.nan,
            "Duration_AW": s_mvd / s_mv if s_mv else np.nan,
            "AW": (y * mv).sum() / s_mv if s_mv else np.nan,
            "DW": (y * mv * d).sum() / s_mvd if s_mvd else np.nan}


def aw_dw(pos: pd.DataFrame, dimensiones: tuple[str, ...] = DIMENSIONES) -> pd.DataFrame:
    """Una fila por fondo × nivel × (TOTAL + cada grupo de cada dimensión disponible)."""
    p = pos[~pos["Estado"].eq("EXCLUIDO")].copy()
    p["_y"] = pd.to_numeric(p["Yield"], errors="coerce").fillna(0.0)
    p["_d"] = pd.to_numeric(p["Duration"], errors="coerce").fillna(0.0)
    mv = pd.to_numeric(p["TotalMVal"], errors="coerce").fillna(0.0)
    es_pasivo = p["BalanceSheet"].eq("Liability")
    p["_mv_act"] = mv.where(~es_pasivo, 0.0)
    p["_mv_pas"] = mv.abs().where(es_pasivo, 0.0)
    p["_mv_pat"] = mv.where(~es_pasivo, -mv.abs())
    dims = [d for d in dimensiones if d in p.columns]
    out = []
    for fid, pf in p.groupby("ID_Fund", sort=True):
        fondo = str(pf["Fondo"].iloc[0]) if "Fondo" in pf.columns else ""
        for nivel, col, filtro in (("ACTIVOS", "_mv_act", ~es_pasivo), ("PASIVOS", "_mv_pas", es_pasivo), ("PATRIMONIO", "_mv_pat", None)):
            sub = pf if filtro is None else pf[filtro.loc[pf.index]]
            if sub.empty:
                continue
            sub = sub.assign(_mv=sub[col])
            tot = sub["_mv"].sum()
            out.append(_fila(sub, tot, ID_Fund=fid, Fondo=fondo, Nivel=nivel, Dimension="TOTAL", Grupo="TOTAL"))
            for dim in dims:
                g = sub[dim].astype(str).str.strip().replace({"": "SIN DATO", "nan": "SIN DATO"})
                for grupo, sg in sub.groupby(g, sort=False):
                    out.append(_fila(sg, tot, ID_Fund=fid, Fondo=fondo, Nivel=nivel, Dimension=dim, Grupo=grupo))
    df = pd.DataFrame(out, columns=COLS)
    return df.sort_values(["ID_Fund", "Nivel", "Dimension", "MV"], ascending=[True, True, True, False], key=lambda s: s.map({"ACTIVOS": 0, "PASIVOS": 1, "PATRIMONIO": 2}) if s.name == "Nivel" else s).reset_index(drop=True)


def verificar(agg: pd.DataFrame) -> pd.DataFrame:
    """Por fondo: MV_PAT == MV_ACT − MV_PAS y los pesos de cada dimensión suman 1. Devuelve las violaciones."""
    tot = agg[agg["Dimension"].eq("TOTAL")].pivot_table(index="ID_Fund", columns="Nivel", values="MV", aggfunc="sum").fillna(0)
    malos = []
    for fid, r in tot.iterrows():
        if not np.isclose(r.get("PATRIMONIO", 0), r.get("ACTIVOS", 0) - r.get("PASIVOS", 0), rtol=1e-9, atol=1e-6):
            malos.append(dict(ID_Fund=fid, Problema="MV_PAT != MV_ACT - MV_PAS", Valor=r.get("PATRIMONIO", 0) - (r.get("ACTIVOS", 0) - r.get("PASIVOS", 0))))
    pesos = agg[~agg["Dimension"].eq("TOTAL")].groupby(["ID_Fund", "Nivel", "Dimension"])["Peso_MV"].sum()
    for (fid, niv, dim), s in pesos.items():
        if pd.notna(s) and not np.isclose(s, 1.0, atol=1e-9):
            malos.append(dict(ID_Fund=fid, Problema=f"pesos {niv}/{dim} suman {s:.6f}", Valor=s))
    return pd.DataFrame(malos, columns=["ID_Fund", "Problema", "Valor"])
