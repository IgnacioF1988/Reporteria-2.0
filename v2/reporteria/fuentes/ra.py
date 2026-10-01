"""RiskAmérica: TIR y duración por nemotécnico chileno. Periodicidad UNICO (depósitos y pagarés) trae TIR base 30 días y se
anualiza × `ra_unico_factor` (12, nominal 30/360); el resto se usa tal cual (equivalente efectiva por periodicidad: FUTURO.md)."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios, limpiar_txt


def candidatos_ra(pos: pd.DataFrame, ra: pd.DataFrame | None, parametros: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].isin(["CASCADA", "CAJA", "FACTURA"])]    # FACTURA: respaldo para FNCHI y similares sin fila en Facts
    if obj.empty or ra is None or ra.empty:
        return candidatos_vacios(), alertas.vacias()
    factor = float((parametros or {}).get("ra_unico_factor", 12))
    tabla = ra.set_index("Nemo")
    nemos = limpiar_txt(obj["Name_Instrumento"]).str.upper()
    filas, mensuales = [], []
    for (_, p), nemo in zip(obj.iterrows(), nemos):
        if nemo not in tabla.index:
            continue
        r = tabla.loc[nemo]
        completo = pd.notna(r["Yield"]) and pd.notna(r["Duration"])
        per = str(r.get("Periodicidad", "")).strip().upper()
        yld, nota = r["Yield"], "tir_sin_convertir"
        if per == "UNICO" and factor != 1 and pd.notna(yld):
            yld, nota = float(yld) * factor, f"tir_mensual_x{factor:g}"
            mensuales.append({**p.to_dict(), "Valor": yld})
        filas.append(candidato(p, "RA", yld, r["Duration"], moneda=str(r.get("Moneda", "")) or str(p.get("Risk_Currency", "")),
                               origen="NEMO", valido=completo, motivo="" if completo else "TUPLA_INCOMPLETA",
                               detalle=f"periodicidad={per} {nota}"))
    al = alertas.emitir("RA_TIR_MENSUAL", "INFO", pd.DataFrame(mensuales), f"periodicidad UNICO: TIR base 30 días anualizada × {factor:g}",
                        valor="Valor") if mensuales else alertas.vacias()
    return pd.DataFrame(filas) if filas else candidatos_vacios(), al
