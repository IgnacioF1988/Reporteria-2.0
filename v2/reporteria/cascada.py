"""Elige una Yield y una Duration por posición según su tratamiento; aplica defaults corporativos y del PM."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import alertas
from .modelo import CALC_TYPE_DEF, DURATION_DEF, YIELD_DEF, candidato, vigente

# EXCEPCIONES es decisión explícita del PM: va primero en todo tratamiento salvo EXCLUIR
ORDEN = {"CASCADA": ["EXCEPCIONES", "JPM", "RA", "BBG", "CSHF", "JSONL"], "FACTURA": ["EXCEPCIONES", "FACTURA", "RA"],
         "CAJA": ["EXCEPCIONES", "RA", "JPM", "CAJA"], "CERO": ["EXCEPCIONES", "CERO"], "EXCLUIR": ["EXCLUIR"]}
PROVEEDORES = {"JPM", "RA", "BBG", "CSHF", "JSONL", "EXCEPCIONES"}
CALC_TYPE_FUENTE = {"FACTURA": "PROP", "CAJA": "PROP", "EXCEPCIONES": "PROP", "CSHF": "PROP", "JSONL": "PROP", "RA": "1", "CERO": "", "EXCLUIR": ""}


def _con_valido_bool(cand: pd.DataFrame) -> pd.DataFrame:
    """Tras concatenar frames vacíos, Valido puede quedar object: se fuerza a bool para poder filtrar y negar."""
    cand = cand.copy()
    if "Valido" in cand:
        cand["Valido"] = cand["Valido"].fillna(False).astype(bool)
    return cand


def validar_candidatos(cand: pd.DataFrame, parametros: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sanidad sobre métricas de proveedor: duration no positiva o yield fuera de rango invalidan el candidato."""
    cand = _con_valido_bool(cand)
    if cand.empty:
        return cand, alertas.vacias()
    ymax, ymin = float(parametros.get("yield_max_proveedor", 1.0)), float(parametros.get("yield_min_proveedor", -0.5))
    prov = cand["Fuente"].isin(PROVEEDORES) & cand["Valido"]
    dur_mala = prov & (pd.to_numeric(cand["Duration"], errors="coerce") <= 0)
    yld_mala = prov & ~dur_mala & ~pd.to_numeric(cand["Yield"], errors="coerce").between(ymin, ymax)
    cand.loc[dur_mala, ["Valido", "Motivo_Descarte"]] = [False, "DURATION_NO_POSITIVA"]
    cand.loc[yld_mala, ["Valido", "Motivo_Descarte"]] = [False, "YIELD_FUERA_RANGO"]
    malos = cand[dur_mala | yld_mala]
    al = alertas.emitir("PROVEEDOR_INVALIDO", "ALTA", malos.assign(Valor=malos["Fuente"] + ":" + malos["Motivo_Descarte"]),
                        "métrica de proveedor descartada por sanidad; la posición sigue a la siguiente fuente", valor="Valor") if len(malos) else alertas.vacias()
    return cand, al


def pendientes_tras(pos: pd.DataFrame, cand: pd.DataFrame, es_def: pd.Series) -> set[str]:
    """Pos_ID de tratamiento CASCADA sin candidato válido todavía y que no son default: lo único que consume terminal."""
    con_valido = set(cand.loc[cand["Valido"].astype(bool), "Pos_ID"]) if len(cand) else set()
    m = pos["Tratamiento"].eq("CASCADA") & ~pos["Pos_ID"].isin(con_valido) & ~es_def
    return set(pos.loc[m, "Pos_ID"])


def defaults(pos: pd.DataFrame, defaulted: pd.DataFrame | None, reglas_def: pd.DataFrame, settle) -> pd.Series:
    return _defaults(pos, defaulted, reglas_def, settle)


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
           settle: pd.Timestamp, yield_type_default: int = 15, parametros: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pos = pos.copy()
    par = parametros or {}
    y_def, d_def = float(par.get("yield_def", YIELD_DEF)), float(par.get("duracion_def", DURATION_DEF))
    trat = pos.set_index("Pos_ID")["Tratamiento"]
    cero = pos[pos["Tratamiento"].eq("CERO")]
    if len(cero):        # entran al agregado con yield 0 y duration 0; deben quedar trazados como candidato
        cand = pd.concat([cand, pd.DataFrame([candidato(p, "CERO", 0.0, 0.0, origen="REGLAS/buckets", valido=True)
                                              for _, p in cero.iterrows()])], ignore_index=True)
    cand = _con_valido_bool(cand)
    validos = cand[cand["Valido"]].copy()
    validos["_orden"] = [ORDEN.get(t, []).index(f) if f in ORDEN.get(t, []) else 99
                         for t, f in zip(validos["Pos_ID"].map(trat), validos["Fuente"])]
    g = validos.sort_values("_orden").drop_duplicates("Pos_ID").set_index("Pos_ID").reindex(pos["Pos_ID"])
    pos["Yield"], pos["Duration"] = g["Yield"].to_numpy(), g["Duration"].to_numpy()
    for c in ("Fuente", "Origen", "Yield_Moneda"):
        pos[c] = g[c].fillna("").to_numpy()
    pos["Etapa"] = pos["Fuente"]
    pos["CalcType"] = pos["Fuente"].map(CALC_TYPE_FUENTE).fillna("")
    yt = pd.to_numeric(pos.get("Yield_Type"), errors="coerce").fillna(0).astype(int).where(lambda s: s > 0, yield_type_default)
    de_yield_type = pos["Fuente"].isin(["JPM", "BBG"])
    pos.loc[de_yield_type, "CalcType"] = yt[de_yield_type].astype(str)

    pos["Estado_DEF"] = _defaults(pos, defaulted, reglas_def, settle)
    es_def = pos["Estado_DEF"].ne("") & pos["Tratamiento"].ne("EXCLUIR")
    pos.loc[es_def, ["Yield", "Duration"]] = [y_def, d_def]
    pos.loc[es_def, ["Fuente", "Etapa", "Origen"]] = ["REGLA_DEF", "REGLA_DEF", "DEFAULTED/REGLAS"]
    pos.loc[es_def, "CalcType"] = pos.loc[es_def, "Estado_DEF"].map(CALC_TYPE_DEF)
    if es_def.any():
        cand = pd.concat([cand, pd.DataFrame([candidato(p, "REGLA_DEF", y_def, d_def, origen="DEFAULTED/REGLAS",
                                                        detalle=p["Estado_DEF"]) for _, p in pos[es_def].iterrows()])], ignore_index=True)

    resuelto = pos["Yield"].notna() & pos["Duration"].notna()
    pos["Estado"] = np.where(pos["Tratamiento"].eq("EXCLUIR"), "EXCLUIDO", np.where(resuelto, "RESUELTO", "FALTANTE"))
    pos.loc[pos["Estado"].eq("EXCLUIDO"), ["Yield", "Duration"]] = [0.0, 0.0]
    motivos = cand[~cand["Valido"]].groupby("Pos_ID")["Motivo_Descarte"].agg(lambda s: "; ".join(sorted({x for x in s if x})))
    pos["Motivo"] = np.where(pos["Estado"].eq("FALTANTE"), pos["Pos_ID"].map(motivos).fillna("sin candidato de ninguna fuente"), "")
    falt = pos[pos["Estado"].eq("FALTANTE")]
    al = alertas.emitir("FALTANTE", "ALTA", falt, "sin Yield/Duration: entra al agregado a yield 0", valor="TotalMVal") if len(falt) else alertas.vacias()
    return pos, cand, al
