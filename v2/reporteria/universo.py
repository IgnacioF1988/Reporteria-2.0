"""Universo = CUBO completo cruzado con maestros. Una fila por Pos_ID (fondo | PK2 | BalanceSheet)."""
from __future__ import annotations

import pandas as pd

from . import alertas
from .config import FONDOS
from .lectura.maestros import COLS_INSTR
from .modelo import COLS_VALOR, pos_id


def armar_universo(cubo: pd.DataFrame, bd_instr: pd.DataFrame, bd_funds: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = cubo.merge(bd_instr, on="PK2", how="left").merge(bd_funds, on="ID_Fund", how="left")
    pos["Pos_ID"] = pos_id(pos)
    al = []

    dup = pos["Pos_ID"].duplicated(keep=False)
    if dup.any():
        al.append(alertas.emitir("CUBO_DUPLICADO", "MEDIA", pos[dup].drop_duplicates("Pos_ID"),
                                 "misma posición repetida en el CUBO; se suman sus valores"))
        agg = {c: "sum" for c in COLS_VALOR if c in pos.columns}
        agg.update({c: "first" for c in pos.columns if c not in agg and c != "Pos_ID"})
        pos = pos.groupby("Pos_ID", as_index=False, sort=False).agg(agg)

    sin_maestro = pos["Risk_Currency"].isna() | pos["Risk_Currency"].eq("")
    if sin_maestro.any():
        al.append(alertas.emitir("SIN_MAESTRO", "ALTA", pos[sin_maestro], "PK2 sin match en BD_INSTRUMENTOS"))
    sin_fondo = pos["FundShortName"].isna()
    if sin_fondo.any():
        al.append(alertas.emitir("SIN_FONDO", "ALTA", pos[sin_fondo].drop_duplicates("ID_Fund"), "ID_Fund sin match en BD_FUNDS"))

    pos["Fondo"] = pos["ID_Fund"].map(lambda i: FONDOS[i].alias[0] if i in FONDOS else "").where(
        pos["ID_Fund"].isin(list(FONDOS)), pos["FundShortName"])
    for c in COLS_INSTR:
        if c in ("Investment_Type_Code", "Issue_Type_Code"):
            pos[c] = pd.to_numeric(pos[c], errors="coerce").astype("Int64")
        else:
            pos[c] = pos[c].fillna("").astype("object")
    for c in ("Yield", "Duration"):
        pos[c] = float("nan")
    for c in ("Fuente", "Origen", "Etapa", "Estado", "Motivo", "Yield_Moneda"):
        pos[c] = ""
    return pos.reset_index(drop=True), alertas.juntar(*al)
