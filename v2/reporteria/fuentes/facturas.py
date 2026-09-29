"""Facturas desde el RPT de Facts: yield = tasa_mensual × 12 (decimal), duration = días al vencimiento vigente / 365."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios


def candidatos_facturas(pos: pd.DataFrame, rpt: pd.DataFrame | None, homol_geneva: dict[str, int], homol_funds: dict[str, int],
                        settle: pd.Timestamp, tolerancia_monto: float = 0.01) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].eq("FACTURA")]
    if obj.empty:
        return candidatos_vacios(), alertas.vacias()
    al = []
    if rpt is None or rpt.empty:
        filas = [candidato(p, "FACTURA", valido=False, motivo="SIN_FACTURA") for _, p in obj.iterrows()]
        return pd.DataFrame(filas), alertas.vacias()

    r = rpt.copy()
    r["ID_Instrumento"] = r["nemotecnico"].map(homol_geneva)
    r["ID_Fund"] = r["fondo"].str.upper().map({str(k).upper(): v for k, v in homol_funds.items()})
    sin_fondo = r["ID_Fund"].isna()
    if sin_fondo.any():
        al.append(alertas.emitir("FACTURA_FONDO_DESCONOCIDO", "MEDIA", detalle=f"fondos del RPT sin match en HOMOL_FUNDS: {sorted(set(r.loc[sin_fondo, 'fondo']))}", ambito="CORRIDA"))
    vivas = r[r["estado"].ne("pagado") & r["fecha_pago"].isna() & r["ID_Instrumento"].notna() & r["ID_Fund"].notna()].copy()
    vivas["ID_Instrumento"] = vivas["ID_Instrumento"].astype(int)
    vivas["ID_Fund"] = vivas["ID_Fund"].astype(int)
    vivas = vivas.drop_duplicates(["ID_Fund", "ID_Instrumento"]).set_index(["ID_Fund", "ID_Instrumento"])

    filas, dif, usadas = [], [], set()
    for _, p in obj.iterrows():
        k = (int(p["ID_Fund"]), int(p["ID_Instrumento"]))
        if k not in vivas.index:
            filas.append(candidato(p, "FACTURA", valido=False, motivo="SIN_FACTURA"))
            continue
        f = vivas.loc[k]
        usadas.add(k)
        dias = (f["fecha_vencimiento"] - settle).days if pd.notna(f["fecha_vencimiento"]) else -1
        if pd.isna(f["tasa_mensual"]) or dias <= 0:
            filas.append(candidato(p, "FACTURA", valido=False, motivo="FACTURA_VENCIDA_O_SIN_TASA"))
            continue
        filas.append(candidato(p, "FACTURA", float(f["tasa_mensual"]) * 12, dias / 365, moneda=str(p.get("Risk_Currency", "")),
                               origen=f"RPT:{f['nemotecnico']}", detalle=f"tasa_mensual={f['tasa_mensual']} dias={dias} monto_compra={f['monto_compra']}"))
        mv = p.get("TotalMVal")
        if pd.notna(f["monto_compra"]) and pd.notna(mv) and mv and abs(f["monto_compra"] - mv) / abs(mv) > tolerancia_monto:
            dif.append({**p.to_dict(), "Valor": f["monto_compra"] / mv - 1})
    if dif:
        al.append(alertas.emitir("FACTURA_MONTO_DISTINTO", "MEDIA", pd.DataFrame(dif), "monto_compra del RPT difiere del TotalMVal del CUBO", valor="Valor"))
    sobrantes = [k for k in vivas.index if k not in usadas]
    if sobrantes:
        al.append(alertas.emitir("FACTURA_SIN_POSICION", "INFO", detalle=f"{len(sobrantes)} facturas vivas del RPT sin posición en el CUBO (ej. {sobrantes[:3]})", ambito="CORRIDA"))
    return pd.DataFrame(filas), alertas.juntar(*al)
