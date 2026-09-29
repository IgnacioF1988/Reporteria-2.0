"""JPM (CEMBI / GBI): métrica ya calculada por ISIN; si falta, por el ISIN hermano de la familia."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios


def candidatos_jpm(pos: pd.DataFrame, jpm: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    if obj.empty or jpm is None or jpm.empty:
        return candidatos_vacios(), alertas.vacias()
    tabla = jpm.set_index("ISIN")
    filas, via_hermano = [], []
    for _, p in obj.iterrows():
        isin, origen = str(p.get("ISIN", "")).strip(), "DIRECTO"
        if isin not in tabla.index:
            hermanos = [h for h in str(p.get("ISIN_Hermanos", "")).split(";") if h and h in tabla.index]
            if not hermanos:
                continue
            isin, origen = hermanos[0], f"HERMANO:{hermanos[0]}"
            via_hermano.append(p)
        r = tabla.loc[isin]
        completo = pd.notna(r["Yield"]) and pd.notna(r["Duration"])
        filas.append(candidato(p, "JPM", r["Yield"], r["Duration"], moneda=str(p.get("Risk_Currency", "")), origen=origen,
                               valido=completo, motivo="" if completo else "TUPLA_INCOMPLETA", detalle=f"fuente={r['Fuente']} isin={isin}"))
    al = alertas.emitir("FAMILIA_INFERIDA", "INFO", pd.DataFrame(via_hermano), "métrica tomada del ISIN hermano (misma familia por nombre)") if via_hermano else alertas.vacias()
    return pd.DataFrame(filas) if filas else candidatos_vacios(), al
