"""Facturas: yield lineal desde la tasa mensual (% mensual) y duration a vencimiento."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios


def candidatos_facturas(pos: pd.DataFrame, facturas: pd.DataFrame | None, settle: pd.Timestamp,
                        tolerancia_monto: float = 0.01) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].eq("FACTURA")]
    if obj.empty:
        return candidatos_vacios(), alertas.vacias()
    fac = (facturas.set_index("PK2") if facturas is not None and not facturas.empty else pd.DataFrame())
    filas, dif = [], []
    for _, p in obj.iterrows():
        if p["PK2"] not in fac.index:
            filas.append(candidato(p, "FACTURA", valido=False, motivo="SIN_FACTURA"))
            continue
        f = fac.loc[p["PK2"]]
        dias = (f["Fecha_Vencimiento"] - settle).days
        if pd.isna(f["Tasa_Mensual"]) or pd.isna(f["Fecha_Vencimiento"]) or dias <= 0:
            filas.append(candidato(p, "FACTURA", valido=False, motivo="FACTURA_VENCIDA_O_SIN_TASA"))
            continue
        yld = f["Tasa_Mensual"] * 12 / 100          # lineal, decisión del PM
        filas.append(candidato(p, "FACTURA", yld, dias / 365, moneda=str(p.get("Risk_Currency", "")), origen="PK2",
                               detalle=f"tasa_mensual={f['Tasa_Mensual']} dias={dias} monto={f['Monto']}"))
        mv = p.get("TotalMVal")
        if pd.notna(f["Monto"]) and pd.notna(mv) and mv and abs(f["Monto"] - mv) / abs(mv) > tolerancia_monto:
            dif.append({**p.to_dict(), "Valor": f["Monto"] / mv - 1})
    al = alertas.emitir("FACTURA_MONTO_DISTINTO", "MEDIA", pd.DataFrame(dif),
                        "Monto de FACTURAS difiere del TotalMVal del CUBO", valor="Valor") if dif else alertas.vacias()
    return pd.DataFrame(filas), al
