"""Bucket, Ficha_FI, FX_Exposure y Tratamiento de cada posición.

1. BalSheetKey (8 códigos del maestro) → BD_BalanceSheet → Bucket base y Ficha_FI. Sin mapeo → SIN_REGLA + alerta.
2. REGLAS/clasificacion pisa el bucket: criterio más específico gana (PK2/ID_Instrumento > BalSheetKey/Regex >
   Issue_Type > Investment_Type); a igual criterio, regla por fondo gana a global; empate exacto → menor ID + alerta.
3. REGLAS/buckets da el Tratamiento por bucket; bucket sin fila → CASCADA + alerta BUCKET_SIN_TRATAMIENTO.
4. BD_FX_Exposure_{fondo}: match por las columnas de código que la tabla tenga.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import alertas
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


def _fx_exposure(pos: pd.DataFrame, tablas: dict[int, pd.DataFrame]) -> pd.Series:
    out = pd.Series("", index=pos.index, dtype="object")
    for fid, tabla in tablas.items():
        cols = [c for c in tabla.columns if c != "FX_Exposure"]
        sub = pos[pos["ID_Fund"] == fid]
        if sub.empty or not cols:
            continue
        llaves = sub[cols].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
        m = llaves.merge(tabla.drop_duplicates(cols), on=cols, how="left")
        out.loc[sub.index] = m["FX_Exposure"].fillna("").to_numpy()
    return out


def clasificar(pos: pd.DataFrame, balance: pd.DataFrame, buckets: pd.DataFrame, reglas: pd.DataFrame,
               fx_tablas: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = pos.copy()
    al = []
    pos["BalSheetKey"] = bal_sheet_key(pos)
    tabla = balance.set_index("BalSheetKey")
    pos["Bucket"] = pos["BalSheetKey"].map(tabla["Bucket"]).fillna("")
    pos["Ficha_FI"] = pos["BalSheetKey"].map(tabla["Ficha_FI"]).fillna("") if "Ficha_FI" in tabla.columns else ""
    pos["Bucket_Origen"] = np.where(pos["Bucket"].ne(""), "TABLA", "")

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
        al.append(alertas.emitir("SIN_REGLA", "ALTA", pos[sin], "BalSheetKey sin mapeo en BD_BalanceSheet ni en REGLAS/clasificacion",
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
    pos["FX_Exposure"] = _fx_exposure(pos, fx_tablas)
    return pos, alertas.juntar(*al)
