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
    for c in ("Fuente", "Origen", "Etapa", "Estado", "Motivo", "Yield_Moneda", "CalcType", "Estado_DEF", "Indice"):
        pos[c] = ""
    return pos.reset_index(drop=True), alertas.juntar(*al)


def marcar_familias(pos: pd.DataFrame, sufijos: tuple[str, ...]) -> pd.DataFrame:
    """Familia = ISINs cuyo nombre sin sufijo de serie coincide. Familia = menor ISIN del grupo; ISIN_Hermanos = los demás."""
    import re
    pos = pos.copy()
    pat = re.compile(r"\s+(" + "|".join(re.escape(s) for s in sorted(sufijos, key=len, reverse=True)) + r")\s*$", re.IGNORECASE)
    pos["Base_Name"] = limpiar_txt(pos["Name_Instrumento"]).map(lambda n: pat.sub("", n).strip())
    isin = limpiar_txt(pos["ISIN"])
    con_isin = pos[isin.ne("")].assign(ISIN=isin[isin.ne("")]).drop_duplicates(["Base_Name", "ISIN"])
    grupos = con_isin.groupby("Base_Name")["ISIN"].agg(lambda s: sorted(set(s)))
    grupos = grupos[grupos.map(len) > 1]
    fam, herm = {}, {}
    for isins in grupos:
        for i in isins:
            fam[i], herm[i] = isins[0], ";".join(x for x in isins if x != i)
    pos["Familia"] = isin.map(fam).fillna("")
    pos["ISIN_Hermanos"] = isin.map(herm).fillna("")
    return pos


def asignar_hedge(pos: pd.DataFrame, fondos: pd.DataFrame, strong_ccy: set[str], pais_a_ccy: dict[str, str],
                  cartera_ant: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hedge_Currency por política del fondo (REGLAS/fondos); hereda lo decidido en el cierre anterior si existe."""
    pos = pos.copy()
    politica = pos["ID_Fund"].map(dict(zip(fondos["ID_Fund"], fondos["Politica_Hedge"]))).fillna("")
    rc = limpiar_txt(pos["Risk_Currency"]).str.upper()
    fuerte = rc.isin(strong_ccy)
    por_pais = limpiar_txt(pos["Risk_Country"]).str.upper().map(pais_a_ccy).fillna("") if "Risk_Country" in pos.columns else pd.Series("", index=pos.index)
    hedge = pd.Series("", index=pos.index, dtype="object")
    hedge[fuerte & politica.eq("A_CLP")] = "CLP"
    hedge[fuerte & politica.eq("POR_PAIS")] = por_pais[fuerte & politica.eq("POR_PAIS")]
    origen = pd.Series("", index=pos.index, dtype="object")
    origen[hedge.ne("")] = "REGLA"
    al = []
    sin_pais = fuerte & politica.eq("POR_PAIS") & por_pais.eq("")
    if sin_pais.any():
        al.append(alertas.emitir("HEDGE_PAIS_SIN_MONEDA", "MEDIA", pos[sin_pais], "Risk_Country sin moneda local en RISK_COUNTRY_TO_LOCAL_CCY", valor="Risk_Country"))
    con_politica = politica.ne("") & fuerte
    if cartera_ant is not None and len(cartera_ant) and "Hedge_Currency" in cartera_ant.columns:
        prev = cartera_ant.drop_duplicates("Pos_ID").set_index("Pos_ID")["Hedge_Currency"]
        prev = limpiar_txt(prev)
        en_prev = pos["Pos_ID"].isin(prev.index) & con_politica
        hedge[en_prev] = pos.loc[en_prev, "Pos_ID"].map(prev).to_numpy()
        origen[en_prev] = "MES_ANTERIOR"
    else:
        en_prev = pd.Series(False, index=pos.index)
    nuevos = con_politica & ~en_prev & hedge.ne("")
    if nuevos.any():
        al.append(alertas.emitir("HEDGE_NUEVO", "MEDIA", pos[nuevos], "hedge asignado por regla (no estaba en el cierre anterior): revisar", valor="TotalMVal"))
    igual = hedge.ne("") & hedge.eq(rc)
    if igual.any():
        al.append(alertas.emitir("HEDGE_IGUAL_MONEDA", "INFO", pos[igual], "Hedge_Currency igual a la moneda del papel: se toma como sin hedge"))
        hedge[igual], origen[igual] = "", ""
    pos["Hedge_Currency"], pos["Hedge_Origen"] = hedge, origen
    return pos, alertas.juntar(*al)
