"""Bucket, Ficha_FI, FX_Exposure y Tratamiento de cada posición.

1. dim_clasificacion (dimensionales.duckdb, filas con comodines; ver `dim.resolver`) → Bucket, Ficha_FI y FX_Exposure.
   Sin fila que defina Bucket → SIN_REGLA + alerta. BalSheetKey se conserva solo como columna de auditoría.
2. REGLAS/clasificacion pisa el bucket: criterio más específico gana (PK2/ID_Instrumento > BalSheetKey/Regex >
   Issue_Type > Investment_Type); a igual criterio, regla por fondo gana a global; empate exacto → menor ID + alerta.
   (`Criterio=BalSheetKey` está deprecado: esas filas van a dim_clasificacion con ID_Fund.)
3. REGLAS/buckets da el Tratamiento por bucket; bucket sin fila → CASCADA + alerta BUCKET_SIN_TRATAMIENTO.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import alertas, dim
from .modelo import bal_sheet_key

ESPECIFICIDAD = {"PK2": 4, "ID_Instrumento": 4, "BalSheetKey": 3, "Nombre_Regex": 3, "Issue_Type_Code": 2, "Investment_Type_Code": 1}


def _match(pos: pd.DataFrame, regla: pd.Series) -> pd.Series:
    crit, val = regla["Criterio"], str(regla["Valor"])
    if crit == "Nombre_Regex":
        pat = re.compile(val, re.IGNORECASE)
        m = pos["Name_Instrumento"].astype(str).map(lambda s: bool(pat.search(s)))
    elif crit in ("Issue_Type_Code", "Investment_Type_Code", "ID_Instrumento"):
        m = pd.to_numeric(pos[crit], errors="coerce") == float(val)
    else:
        m = pos[crit].astype(str).str.strip().str.upper() == val.strip().upper()
    if pd.notna(regla["ID_Fund"]):
        m = m & (pos["ID_Fund"] == int(regla["ID_Fund"]))
    return m.fillna(False).astype(bool)


def clasificar(pos: pd.DataFrame, clasif: pd.DataFrame, buckets: pd.DataFrame, reglas: pd.DataFrame,
               settle: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = pos.copy()
    al = []
    pos["BalSheetKey"] = bal_sheet_key(pos)
    res, a = dim.resolver(pos, clasif, settle)
    al.append(a)
    for c in res.columns:
        pos[c] = res[c].to_numpy()

    n = len(pos)
    mejor, idx, ambiguo = np.full(n, -1.0), np.full(n, -1), np.zeros(n, dtype=bool)
    for i, r in reglas.iterrows():
        puntaje = ESPECIFICIDAD[r["Criterio"]] * 10 + (5 if pd.notna(r["ID_Fund"]) else 0)
        m = _match(pos, r).to_numpy()
        gana, empata = m & (puntaje > mejor), m & (puntaje == mejor)
        ambiguo = (ambiguo | empata) & ~gana
        mejor[gana], idx[gana] = puntaje, i
    con_regla = idx >= 0
    elegida = reglas.reindex(idx[con_regla])
    tiene_bucket = elegida["Bucket"].ne("").to_numpy()
    filas = pos.index[con_regla]
    pos.loc[filas[tiene_bucket], "Bucket"] = elegida["Bucket"].to_numpy()[tiene_bucket]
    pos.loc[filas[tiene_bucket], "Bucket_Origen"] = ("REGLA:" + elegida["ID"].astype(str)).to_numpy()[tiene_bucket]
    trat_regla = pd.Series("", index=pos.index, dtype="object")
    if "Tratamiento" in elegida.columns:
        trat_regla.loc[filas] = elegida["Tratamiento"].fillna("").to_numpy()
    if ambiguo.any():
        al.append(alertas.emitir("REGLA_AMBIGUA", "MEDIA", pos[ambiguo], "dos reglas con la misma especificidad; se usó la de menor ID"))

    sin = pos["Bucket"].eq("")
    if sin.any():
        al.append(alertas.emitir("SIN_REGLA", "ALTA", pos[sin], "sin fila que defina Bucket en dim_clasificacion ni en REGLAS/clasificacion",
                                 valor="BalSheetKey"))
        pos.loc[sin, ["Bucket", "Bucket_Origen"]] = ["SIN_REGLA", ""]

    trat = buckets.set_index("Bucket")
    pos["Tratamiento"] = pos["Bucket"].map(trat["Tratamiento"])
    pos["Bucket_Orden"] = pos["Bucket"].map(trat["Orden"]) if "Orden" in trat.columns else np.nan
    sin_trat = pos["Tratamiento"].isna() & pos["Bucket"].ne("SIN_REGLA")
    if sin_trat.any():
        al.append(alertas.emitir("BUCKET_SIN_TRATAMIENTO", "ALTA", pos[sin_trat].drop_duplicates("Bucket"),
                                 "bucket sin fila en REGLAS/buckets; se trata como CASCADA", valor="Bucket"))
    pos["Tratamiento"] = pos["Tratamiento"].fillna("CASCADA")
    pos.loc[trat_regla.ne(""), "Tratamiento"] = trat_regla[trat_regla.ne("")]
    return pos, alertas.juntar(*al)
