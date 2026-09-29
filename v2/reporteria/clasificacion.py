"""Asigna Bucket y Tratamiento a cada posición según REGLAS/clasificacion.

Precedencia: criterio más específico gana (PK2 > Nombre_Regex > Issue_Type > Investment_Type > Source/BalanceSheet);
a igual criterio, regla por fondo gana a regla global; empate exacto → menor ID + alerta REGLA_AMBIGUA.
Sin match → SIN_REGLA / CASCADA + alerta, para que nada quede fuera en silencio.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import alertas

ESPECIFICIDAD = {"PK2": 4, "Nombre_Regex": 3, "Issue_Type_Code": 2, "Investment_Type_Code": 1, "Source": 1, "BalanceSheet": 1}


def _match(pos: pd.DataFrame, regla: pd.Series) -> pd.Series:
    crit, val = regla["Criterio"], str(regla["Valor"])
    if crit == "Nombre_Regex":
        pat = re.compile(val, re.IGNORECASE)
        m = pos["Name_Instrumento"].astype(str).map(lambda s: bool(pat.search(s)))
    elif crit in ("Issue_Type_Code", "Investment_Type_Code"):
        m = pd.to_numeric(pos[crit], errors="coerce") == float(val)
    else:
        m = pos[crit].astype(str).str.strip().str.upper() == val.strip().upper()
    if pd.notna(regla["ID_Fund"]):
        m = m & (pos["ID_Fund"] == int(regla["ID_Fund"]))
    return m.fillna(False).astype(bool)


def clasificar(pos: pd.DataFrame, reglas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = pos.copy()
    n = len(pos)
    mejor = np.full(n, -1.0)
    idx = np.full(n, -1)
    ambiguo = np.zeros(n, dtype=bool)
    for i, r in reglas.iterrows():
        puntaje = ESPECIFICIDAD[r["Criterio"]] * 10 + (5 if pd.notna(r["ID_Fund"]) else 0)
        m = _match(pos, r).to_numpy()
        gana = m & (puntaje > mejor)
        empata = m & (puntaje == mejor)
        ambiguo |= empata
        ambiguo &= ~gana
        mejor[gana], idx[gana] = puntaje, i

    elegida = reglas.reindex(idx).reset_index(drop=True)
    pos["Bucket"] = elegida["Bucket"].fillna("SIN_REGLA").to_numpy()
    pos["Tratamiento"] = elegida["Tratamiento"].fillna("CASCADA").to_numpy()
    pos["Regla_ID"] = elegida["ID"].astype("Int64").to_numpy()
    pos["Regla_Yield"] = elegida["Yield"].to_numpy()
    pos["Regla_Duration"] = elegida["Duration"].to_numpy()

    al = []
    if (idx < 0).any():
        al.append(alertas.emitir("SIN_REGLA", "ALTA", pos[idx < 0], "ninguna fila de REGLAS/clasificacion aplica; va a la cascada"))
    if ambiguo.any():
        al.append(alertas.emitir("REGLA_AMBIGUA", "MEDIA", pos[ambiguo], "dos reglas con la misma especificidad; se usó la de menor ID"))
    return pos, alertas.juntar(*al)
