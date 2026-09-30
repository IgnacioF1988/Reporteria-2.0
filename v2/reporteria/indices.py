"""Índice de cada posición (UF, UDI, UVR, IPCA, UI, BONCER, VAC, CDI, TIIE o NOMINAL) y su origen.

Cascada: override del operador (REGLAS/overrides_atributo Field=Indice) → moneda que lo declara (CLF, UDI, UVR COSTER, UI CURNCY)
→ Bloomberg para papeles con ISIN en moneda local ambigua (INFLATION_LINKED_INDICATOR / CPN_TYP + RESET_IDX) → NOMINAL.
"""
from __future__ import annotations

import pandas as pd

from . import alertas
from .adaptadores.bbg import Bloomberg
from .config import ALIAS_INDICE, CCY_A_INDICE, CCY_AMBIGUA, INDICES, INFLACION_PAIS, RESET_IDX_A_INDICE
from .fuentes.bbg_yas import _cascada
from .modelo import limpiar_txt

CAMPOS_BBG = ("INFLATION_LINKED_INDICATOR", "CPN_TYP", "RESET_IDX")


def normalizar_indice(valor) -> str:
    v = str(valor).strip().upper()
    v = ALIAS_INDICE.get(v, v)
    return "" if v in ("", "NAN", "NONE", "NO") else v


def asignar_indice(pos: pd.DataFrame, bbg: Bloomberg | None, fecha: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Completa Indice / Indice_Origen. Consulta Bloomberg solo para lo resuelto con ISIN en moneda ambigua y sin índice."""
    pos = pos.copy()
    ind = pos["Indice"].map(normalizar_indice) if "Indice" in pos.columns else pd.Series("", index=pos.index)
    origen = pd.Series("", index=pos.index, dtype=object)
    origen[ind.ne("")] = "OVERRIDE"
    ccy = limpiar_txt(pos["Risk_Currency"]).str.upper()
    por_moneda = ind.eq("") & ccy.isin(CCY_A_INDICE)
    ind[por_moneda], origen[por_moneda] = ccy[por_moneda].map(CCY_A_INDICE), "MONEDA"
    al = []
    isin = limpiar_txt(pos["ISIN"]) if "ISIN" in pos.columns else pd.Series("", index=pos.index)
    consultar = ind.eq("") & ccy.isin(CCY_AMBIGUA) & isin.ne("") & pos["Estado"].eq("RESUELTO") \
        & ~pos["Fuente"].isin(["CERO", "REGLA_DEF", "CAJA", "FACTURA"])
    if bbg is not None and consultar.any():
        hermanos = {i: [h for h in str(hs).split(";") if h] for i, hs in zip(isin, pos.get("ISIN_Hermanos", pd.Series("", index=pos.index)).astype(str)) if hs}
        isins = sorted(set(isin[consultar]))
        campos = {c: _cascada(bbg, isins, hermanos, c)[0] for c in CAMPOS_BBG}
        nuevos = []
        for i in pos.index[consultar]:
            k = isin[i]
            infl = str(campos["INFLATION_LINKED_INDICATOR"].get(k, "")).strip().upper()
            cpn = str(campos["CPN_TYP"].get(k, "")).strip().upper()
            reset = str(campos["RESET_IDX"].get(k, "")).strip().upper()
            pais = str(pos.at[i, "Risk_Country"]).strip().upper()
            if infl == "Y":
                ind[i], origen[i] = INFLACION_PAIS.get(pais, f"INFLACION:{pais}"), "BBG"
                if ind[i] not in INDICES:
                    al.append(alertas.emitir("INDICE_SIN_CURVA", "ALTA", pos.loc[[i]], f"papel indexado a inflación de {pais} sin índice/curva en config.INDICES"))
            elif cpn == "FLOATING" and reset:
                if reset in RESET_IDX_A_INDICE:
                    ind[i], origen[i] = RESET_IDX_A_INDICE[reset], "BBG"
                else:
                    ind[i], origen[i] = f"NUEVO:{reset}", "BBG"
                    nuevos.append(i)
            elif k in campos["INFLATION_LINKED_INDICATOR"] or k in campos["CPN_TYP"]:
                ind[i], origen[i] = "NOMINAL", "BBG"
        if nuevos:
            al.append(alertas.emitir("INDICE_NUEVO", "MEDIA", pos.loc[nuevos], "RESET_IDX de Bloomberg sin mapear en config.RESET_IDX_A_INDICE: se trata como nominal"))
    resto = ind.eq("")
    ind[resto], origen[resto] = "NOMINAL", "DEFAULT"
    pos["Indice"], pos["Indice_Origen"] = ind.values, origen.values
    return pos, alertas.juntar(*al)
