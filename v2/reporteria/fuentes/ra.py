"""RiskAmérica: TIR y duración por nemotécnico chileno. La TIR se usa tal cual (conversión por periodicidad: FUTURO.md)."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios, limpiar_txt


def candidatos_ra(pos: pd.DataFrame, ra: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    if obj.empty or ra is None or ra.empty:
        return candidatos_vacios(), alertas.vacias()
    tabla = ra.set_index("Nemo")
    nemos = limpiar_txt(obj["Name_Instrumento"]).str.upper()
    filas = []
    for (_, p), nemo in zip(obj.iterrows(), nemos):
        if nemo not in tabla.index:
            continue
        r = tabla.loc[nemo]
        completo = pd.notna(r["Yield"]) and pd.notna(r["Duration"])
        filas.append(candidato(p, "RA", r["Yield"], r["Duration"], moneda=str(r.get("Moneda", "")) or str(p.get("Risk_Currency", "")),
                               origen="NEMO", valido=completo, motivo="" if completo else "TUPLA_INCOMPLETA",
                               detalle=f"periodicidad={r.get('Periodicidad', '')} tir_sin_convertir"))
    return pd.DataFrame(filas) if filas else candidatos_vacios(), alertas.vacias()
