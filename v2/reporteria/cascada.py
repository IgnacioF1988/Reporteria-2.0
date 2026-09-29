"""Elige una Yield y una Duration por posición a partir de los candidatos, según el tratamiento."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import alertas
from .modelo import DURATION_DEF, YIELD_DEF, candidato

ORDEN = {
    "CASCADA": ["EXCEPCIONES", "JPM", "RA", "BBG", "CSHF", "JSONL"],
    "FACTURA": ["FACTURA"],
    "FIJO": ["REGLA_FIJA"],
    "CERO": ["CERO"],
    "EXCLUIR": ["EXCLUIR"],
}


def elegir(pos: pd.DataFrame, cand: pd.DataFrame, defaulteados: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Devuelve (posiciones, candidatos con la fila REGLA_DEF agregada, alertas)."""
    pos = pos.copy()
    validos = cand[cand["Valido"]].copy()
    validos["_orden"] = [ORDEN.get(t, []).index(f) if f in ORDEN.get(t, []) else 99
                         for t, f in zip(validos["Pos_ID"].map(pos.set_index("Pos_ID")["Tratamiento"]), validos["Fuente"])]
    ganador = validos.sort_values("_orden").drop_duplicates("Pos_ID").set_index("Pos_ID")

    g = ganador.reindex(pos["Pos_ID"])
    pos["Yield"] = g["Yield"].to_numpy()
    pos["Duration"] = g["Duration"].to_numpy()
    pos["Fuente"] = g["Fuente"].fillna("").to_numpy()
    pos["Origen"] = g["Origen"].fillna("").to_numpy()
    pos["Yield_Moneda"] = g["Yield_Moneda"].fillna("").to_numpy()
    pos["Etapa"] = pos["Fuente"]

    # Defaulteados: pisan cualquier fuente, salvo EXCLUIR. ID_Fund vacío = todos los fondos.
    es_def = pd.Series(False, index=pos.index)
    estado_def = pd.Series("", index=pos.index, dtype="object")
    for _, d in defaulteados.iterrows():
        m = pos["PK2"].eq(d["PK2"]) & pos["Tratamiento"].ne("EXCLUIR")
        if pd.notna(d["ID_Fund"]):
            m &= pos["ID_Fund"].eq(int(d["ID_Fund"]))
        es_def |= m
        estado_def[m] = d["Estado"]
    pos.loc[es_def, ["Yield", "Duration"]] = [YIELD_DEF, DURATION_DEF]
    pos.loc[es_def, ["Fuente", "Etapa", "Origen"]] = ["REGLA_DEF", "REGLA_DEF", "REGLAS/defaulteados"]
    pos["Estado_DEF"] = estado_def
    if es_def.any():
        filas_def = pd.DataFrame([candidato(p, "REGLA_DEF", YIELD_DEF, DURATION_DEF, origen="REGLAS/defaulteados",
                                            detalle=p["Estado_DEF"]) for _, p in pos[es_def].iterrows()])
        cand = pd.concat([cand, filas_def], ignore_index=True)

    resuelto = pos["Yield"].notna() & pos["Duration"].notna()
    pos["Estado"] = np.where(pos["Tratamiento"].eq("EXCLUIR"), "EXCLUIDO", np.where(resuelto, "RESUELTO", "FALTANTE"))
    pos.loc[pos["Estado"].eq("EXCLUIDO"), ["Yield", "Duration"]] = [0.0, 0.0]

    motivos = (cand[~cand["Valido"]].groupby("Pos_ID")["Motivo_Descarte"]
               .agg(lambda s: "; ".join(sorted(set(x for x in s if x)))))
    pos["Motivo"] = np.where(pos["Estado"].eq("FALTANTE"),
                             pos["Pos_ID"].map(motivos).fillna("sin candidato de ninguna fuente"), "")

    falt = pos[pos["Estado"].eq("FALTANTE")]
    al = alertas.emitir("FALTANTE", "ALTA", falt, "sin Yield/Duration: entra al agregado a yield 0",
                        valor="TotalMVal") if len(falt) else alertas.vacias()
    return pos, cand, al
