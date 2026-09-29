"""Elige una Yield y una Duration por posición según su tratamiento; aplica defaults corporativos y del PM."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import alertas
from .modelo import CALC_TYPE_DEF, DURATION_DEF, YIELD_DEF, candidato, vigente

ORDEN = {"CASCADA": ["EXCEPCIONES", "JPM", "RA", "BBG", "CSHF", "JSONL"], "FACTURA": ["FACTURA"], "CAJA": ["CAJA"],
         "CERO": ["CERO"], "EXCLUIR": ["EXCLUIR"]}
CALC_TYPE_FUENTE = {"FACTURA": "PROP", "CAJA": "PROP", "CERO": "", "EXCLUIR": ""}


def _defaults(pos: pd.DataFrame, defaulted: pd.DataFrame | None, reglas_def: pd.DataFrame, settle) -> pd.Series:
    """Estado_DEF por posición: DEFAULTED corporativo vigente → DEF en todos los fondos; REGLAS/defaulteados por fondo."""
    estado = pd.Series("", index=pos.index, dtype="object")
    if defaulted is not None and len(defaulted):
        ids = set(defaulted.loc[vigente(defaulted, settle), "ID_Instrumento"])
        estado[pos["ID_Instrumento"].isin(ids)] = "DEF"
    for _, d in reglas_def[vigente(reglas_def, settle)].iterrows():
        m = pos["ID_Instrumento"].eq(d["ID_Instrumento"])
        if pd.notna(d["ID_Fund"]):
            m &= pos["ID_Fund"].eq(int(d["ID_Fund"]))
        estado[m] = d["Estado"]
    return estado


def elegir(pos: pd.DataFrame, cand: pd.DataFrame, defaulted: pd.DataFrame | None, reglas_def: pd.DataFrame,
           settle: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pos = pos.copy()
    trat = pos.set_index("Pos_ID")["Tratamiento"]
    validos = cand[cand["Valido"]].copy()
    validos["_orden"] = [ORDEN.get(t, []).index(f) if f in ORDEN.get(t, []) else 99
                         for t, f in zip(validos["Pos_ID"].map(trat), validos["Fuente"])]
    g = validos.sort_values("_orden").drop_duplicates("Pos_ID").set_index("Pos_ID").reindex(pos["Pos_ID"])
    pos["Yield"], pos["Duration"] = g["Yield"].to_numpy(), g["Duration"].to_numpy()
    for c in ("Fuente", "Origen", "Yield_Moneda"):
        pos[c] = g[c].fillna("").to_numpy()
    pos["Etapa"] = pos["Fuente"]
    pos["CalcType"] = pos["Fuente"].map(CALC_TYPE_FUENTE).fillna("")

    pos["Estado_DEF"] = _defaults(pos, defaulted, reglas_def, settle)
    es_def = pos["Estado_DEF"].ne("") & pos["Tratamiento"].ne("EXCLUIR")
    pos.loc[es_def, ["Yield", "Duration"]] = [YIELD_DEF, DURATION_DEF]
    pos.loc[es_def, ["Fuente", "Etapa", "Origen"]] = ["REGLA_DEF", "REGLA_DEF", "DEFAULTED/REGLAS"]
    pos.loc[es_def, "CalcType"] = pos.loc[es_def, "Estado_DEF"].map(CALC_TYPE_DEF)
    if es_def.any():
        cand = pd.concat([cand, pd.DataFrame([candidato(p, "REGLA_DEF", YIELD_DEF, DURATION_DEF, origen="DEFAULTED/REGLAS",
                                                        detalle=p["Estado_DEF"]) for _, p in pos[es_def].iterrows()])], ignore_index=True)

    resuelto = pos["Yield"].notna() & pos["Duration"].notna()
    pos["Estado"] = np.where(pos["Tratamiento"].eq("EXCLUIR"), "EXCLUIDO", np.where(resuelto, "RESUELTO", "FALTANTE"))
    pos.loc[pos["Estado"].eq("EXCLUIDO"), ["Yield", "Duration"]] = [0.0, 0.0]
    motivos = cand[~cand["Valido"]].groupby("Pos_ID")["Motivo_Descarte"].agg(lambda s: "; ".join(sorted({x for x in s if x})))
    pos["Motivo"] = np.where(pos["Estado"].eq("FALTANTE"), pos["Pos_ID"].map(motivos).fillna("sin candidato de ninguna fuente"), "")
    falt = pos[pos["Estado"].eq("FALTANTE")]
    al = alertas.emitir("FALTANTE", "ALTA", falt, "sin Yield/Duration: entra al agregado a yield 0", valor="TotalMVal") if len(falt) else alertas.vacias()
    return pos, cand, al
