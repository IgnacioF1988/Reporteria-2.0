"""Overrides del operador (REGLAS): atributos antes de clasificar/consultar, valores al final. Esquema de EXCEPCIONES corporativo."""
from __future__ import annotations

import pandas as pd

from . import alertas
from .modelo import vigente


def aplicar_atributos(pos: pd.DataFrame, ov: pd.DataFrame, settle: pd.Timestamp, campos: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica las filas vigentes cuyo Field esté en `campos`. ID_Fund vacío = todos los fondos; SubID vacío = todas las monedas."""
    pos = pos.copy()
    if "Overrides" not in pos.columns:
        pos["Overrides"] = ""
    al = []
    if ov is None or ov.empty:
        return pos, alertas.vacias()
    for _, r in ov[vigente(ov, settle) & ov["Field"].isin(campos)].iterrows():
        m = pos["ID_Instrumento"].eq(r["ID_Instrumento"])
        if pd.notna(r["SubID_Instrumento"]):
            m &= pos["SubID_Instrumento"].eq(int(r["SubID_Instrumento"]))
        if pd.notna(r["ID_Fund"]):
            m &= pos["ID_Fund"].eq(int(r["ID_Fund"]))
        if not m.any():
            al.append(alertas.emitir("OVERRIDE_SIN_POSICION", "INFO", detalle=f"{r['Field']} para {r['ID_Instrumento']}-{r['SubID_Instrumento']} fondo {r['ID_Fund']}: sin posición en el CUBO", ambito="CORRIDA"))
            continue
        valor = "" if str(r["Value"]).upper() in ("SIN_HEDGE", "NINGUNO", "") else r["Value"]
        pos.loc[m, r["Field"]] = valor
        if r["Field"] == "Bucket":
            pos.loc[m, "Bucket_Origen"] = "OVERRIDE"
        pos.loc[m, "Overrides"] = pos.loc[m, "Overrides"].map(lambda s: ";".join(sorted(set(filter(None, s.split(";") + [r["Field"]])))))
    return pos, alertas.juntar(*al)
