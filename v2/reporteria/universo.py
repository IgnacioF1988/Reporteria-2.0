"""Universo = CUBO completo × maestro × fondos. Una fila por Pos_ID (fondo | PK2 | BalanceSheet)."""
from __future__ import annotations

import pandas as pd

from . import alertas
from .lectura.maestros import COLS_INSTR
from .modelo import CODIGOS, COLS_VALOR, limpiar_txt, pos_id


def armar_universo(cubo: pd.DataFrame, bd_instr: pd.DataFrame, bd_funds: pd.DataFrame,
                   monedas: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    inst = bd_instr.drop(columns=[c for c in ("ID_Instrumento",) if c in bd_instr.columns and c in cubo.columns])
    pos = cubo.merge(inst, on="PK2", how="left").merge(bd_funds, on="ID_Fund", how="left")
    if "ID_Instrumento" not in pos.columns:
        pos["ID_Instrumento"] = pd.to_numeric(pos["PK2"].str.split("-").str[0], errors="coerce")
    pos["Pos_ID"] = pos_id(pos)
    al = []

    dup = pos["Pos_ID"].duplicated(keep=False)
    if dup.any():
        al.append(alertas.emitir("CUBO_DUPLICADO", "MEDIA", pos[dup].drop_duplicates("Pos_ID"),
                                 "misma posición repetida en el CUBO; se suman sus valores"))
        agg = {c: "sum" for c in COLS_VALOR if c in pos.columns}
        agg.update({c: "first" for c in pos.columns if c not in agg and c != "Pos_ID"})
        pos = pos.groupby("Pos_ID", as_index=False, sort=False).agg(agg)

    sin_maestro = pos["Risk_Currency"].isna() | limpiar_txt(pos["Risk_Currency"]).eq("")
    if sin_maestro.any():
        al.append(alertas.emitir("SIN_MAESTRO", "ALTA", pos[sin_maestro], "PK2 sin match en BD_INSTRUMENTOS", valor="TotalMVal"))
    sin_fondo = pos["FundShortName"].isna()
    if sin_fondo.any():
        al.append(alertas.emitir("SIN_FONDO", "ALTA", pos[sin_fondo].drop_duplicates("ID_Fund"), "ID_Fund sin match en BD_FUNDS"))

    pos["Fondo"] = limpiar_txt(pos["FundShortName"]).where(~sin_fondo, pos["ID_Fund"].astype(str))
    pos["FundBaseCurrency"] = limpiar_txt(pos["FundBaseCurrency"])
    if monedas is not None and "id_CURR" in pos.columns:
        pos["Moneda_PK2"] = pos["id_CURR"].map(dict(zip(monedas["id_CURR"], monedas["Code"]))).fillna("")
    for c in COLS_INSTR:
        if c in CODIGOS + ["Yield_Type", "Emision_nacional"]:
            pos[c] = pd.to_numeric(pos[c], errors="coerce").astype("Int64")
        elif c in pos.columns and c not in ("ID_Instrumento", "SubID_Instrumento"):
            pos[c] = limpiar_txt(pos[c])
    for c in ("Yield", "Duration"):
        pos[c] = float("nan")
    for c in ("Fuente", "Origen", "Etapa", "Estado", "Motivo", "Yield_Moneda", "CalcType", "Estado_DEF"):
        pos[c] = ""
    return pos.reset_index(drop=True), alertas.juntar(*al)
