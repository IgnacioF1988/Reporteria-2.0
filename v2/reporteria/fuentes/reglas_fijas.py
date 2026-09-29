"""Fuente para tratamientos FIJO / CERO / EXCLUIR: la métrica sale de la propia regla de clasificación."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios


def candidatos_reglas_fijas(pos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    filas, sin_valor = [], []
    for _, p in pos[pos["Tratamiento"].isin(["FIJO", "CERO", "EXCLUIR"])].iterrows():
        if p["Tratamiento"] == "FIJO":
            y, d = p["Regla_Yield"], p["Regla_Duration"]
            if pd.isna(y) or pd.isna(d):
                sin_valor.append(p)
                y, d = 0.0, 0.0
            filas.append(candidato(p, "REGLA_FIJA", y, d, moneda=str(p.get("Risk_Currency", "")),
                                   origen=f"REGLA:{p['Regla_ID']}", valido=True))
        else:
            filas.append(candidato(p, p["Tratamiento"], 0.0, 0.0, origen=f"REGLA:{p['Regla_ID']}", valido=True))
    cand = pd.DataFrame(filas) if filas else candidatos_vacios()
    al = alertas.emitir("REGLA_SIN_VALOR", "ALTA", pd.DataFrame(sin_valor),
                        "regla FIJO sin Yield/Duration: entra a yield 0; completar en REGLAS/clasificacion",
                        valor="TotalMVal") if sin_valor else alertas.vacias()
    return cand, al
