"""Facturas (base de Facts o RPT en Excel): yield = tasa_mensual × 12 (decimal), duration = días al vencimiento vigente / 365.

Viva al cierre = sin fecha_pago o pagada después del cierre. Cruce: el número del nombre en el CUBO (`FACRCPP59397`,
`FACPP59397`, `FAC68135`) es el `documento_operacion_id` de Facts y manda; HOMOL (nemotécnico → ID_Instrumento) es respaldo
(Geneva antepone `RC` en MRCLP y HOMOL no conoce el nombre de Facts). Morosa (vencida y no pagada al cierre): tasa × 12 y
duration `factura_morosa_duration` (0). El monto de compra se compara contra la cantidad del CUBO (nominal), no contra el MV.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import alertas
from ..modelo import candidato, candidatos_vacios


NUMERO_FACTURA = re.compile(r"^FAC[A-Z]*(\d+)(?:PR\d+)?$")     # FACRCPP58698PR1: Geneva marca la prórroga con sufijo


def _numero(nombre) -> int | None:
    m = NUMERO_FACTURA.match(str(nombre).strip().upper())
    return int(m.group(1)) if m else None


def candidatos_facturas(pos: pd.DataFrame, rpt: pd.DataFrame | None, homol_geneva: dict[str, int], homol_funds: dict[str, int],
                        settle: pd.Timestamp, tolerancia_monto: float = 0.01, parametros: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    dur_morosa = float((parametros or {}).get("factura_morosa_duration", 0.0))
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
    pagada = pd.to_datetime(r["fecha_pago"], errors="coerce")
    comprada = pd.to_datetime(r["fecha_inversion"], errors="coerce") if "fecha_inversion" in r.columns else pd.Series(pd.NaT, index=r.index)
    es_viva = pagada.isna() | (pagada >= settle)        # pagada el día del cierre sigue en el CUBO: viva; fecha_inversion no excluye (el CUBO manda)
    con_fondo = r[r["ID_Fund"].notna()].copy()
    con_fondo["ID_Fund"] = con_fondo["ID_Fund"].astype(int)
    vivas = con_fondo[es_viva[con_fondo.index]].copy()
    no_vivas = con_fondo[~es_viva[con_fondo.index]]
    doc_nv = pd.to_numeric(no_vivas.get("documento_operacion_id"), errors="coerce") if "documento_operacion_id" in no_vivas.columns else pd.Series(float("nan"), index=no_vivas.index)
    por_doc_nv = {(int(f), int(d)): i for i, f, d in zip(no_vivas.index, no_vivas["ID_Fund"], doc_nv) if pd.notna(d)}
    facts = "tasa_origen" in vivas.columns          # solo el lector as-of de Facts la produce (el Excel del RPT también trae documento_operacion_id)
    doc = pd.to_numeric(vivas.get("documento_operacion_id"), errors="coerce") if "documento_operacion_id" in vivas.columns else pd.Series(float("nan"), index=vivas.index)
    por_doc = {(int(f), int(d)): i for i, f, d in zip(vivas.index, vivas["ID_Fund"], doc) if pd.notna(d)}
    con_iid = vivas[vivas["ID_Instrumento"].notna()]
    por_iid = {(int(f), int(k)): i for i, f, k in zip(con_iid.index[::-1], con_iid["ID_Fund"][::-1], con_iid["ID_Instrumento"][::-1])}  # primera aparición gana

    filas, dif, usadas, prorr, rev, morosas, tardias = [], [], set(), [], [], [], []
    for _, p in obj.iterrows():
        fid = int(p["ID_Fund"])
        n = _numero(p.get("Name_Instrumento", ""))
        i = por_doc.get((fid, n)) if n is not None else None
        if i is None and pd.notna(p.get("ID_Instrumento")):
            i = por_iid.get((fid, int(p["ID_Instrumento"])))
        if i is None:
            j = por_doc_nv.get((fid, n)) if n is not None else None
            if j is None:
                filas.append(candidato(p, "FACTURA", valido=False, motivo="SIN_FACTURA"))
            else:                                   # existe en Facts pero no estaba viva al cierre: se explica
                nv = no_vivas.loc[j]
                pag = pd.to_datetime(nv.get("fecha_pago"), errors="coerce")
                filas.append(candidato(p, "FACTURA", valido=False, motivo=f"FACTURA_PAGADA_{pag:%Y%m%d}", detalle=f"Facts estado={nv.get('estado', '')} fecha_pago={pag:%Y-%m-%d}"))
            continue
        f = vivas.loc[i]
        usadas.add(i)
        dias = (f["fecha_vencimiento"] - settle).days if pd.notna(f["fecha_vencimiento"]) else None
        if pd.isna(f["tasa_mensual"]) or dias is None:
            filas.append(candidato(p, "FACTURA", valido=False, motivo="FACTURA_SIN_TASA_O_VENCIMIENTO"))
            continue
        morosa = dias <= 0
        origen = f"{'FACTS' if facts else 'RPT'}:{f['nemotecnico']}"
        extra = (f" tasa_origen={f['tasa_origen']}" if facts and "tasa_origen" in f else "") + (" morosa" if morosa else "")
        filas.append(candidato(p, "FACTURA", float(f["tasa_mensual"]) * 12, dur_morosa if morosa else dias / 365, moneda=str(p.get("Risk_Currency", "")),
                               origen=origen, detalle=f"tasa_mensual={f['tasa_mensual']} dias={dias} monto_compra={f['monto_compra']}{extra}"))
        if morosa:
            morosas.append({**p.to_dict(), "Valor": dias})
        if pd.notna(comprada.get(i)) and comprada[i] > settle:
            tardias.append({**p.to_dict(), "Valor": comprada[i].strftime("%Y-%m-%d")})
        if facts and str(f.get("tasa_origen", "")) == "PRORROGA":
            prorr.append({**p.to_dict(), "Valor": float(f["tasa_mensual"]) * 12})
        if facts and pd.notna(f.get("cambios_revertidos")) and int(f["cambios_revertidos"]) > 0:
            rev.append({**p.to_dict(), "Valor": int(f["cambios_revertidos"])})
        qty = pd.to_numeric(p.get("Qty"), errors="coerce")
        base = qty if pd.notna(qty) and qty else p.get("TotalMVal")     # nominal comprado; el MV va a precio con devengo
        if pd.notna(f["monto_compra"]) and pd.notna(base) and base and abs(f["monto_compra"] - base) / abs(base) > tolerancia_monto:
            dif.append({**p.to_dict(), "Valor": f["monto_compra"] / base - 1})
    if tardias:
        al.append(alertas.emitir("FACTURA_COMPRADA_DESPUES", "INFO", pd.DataFrame(tardias), "fecha_inversion de Facts posterior al cierre (primer pago de nómina); el CUBO ya la tiene", valor="Valor"))
    if morosas:
        al.append(alertas.emitir("FACTURA_MOROSA", "INFO", pd.DataFrame(morosas), f"vencida y no pagada al cierre: tasa × 12 y duration {dur_morosa:g}", valor="Valor"))
    if prorr:
        al.append(alertas.emitir("FACTURA_TASA_PRORROGA", "INFO", pd.DataFrame(prorr), "yield con la tasa de la prórroga vigente al cierre (el RPT usa la original)", valor="Valor"))
    if rev:
        al.append(alertas.emitir("FACTURA_ASOF_REVERTIDA", "INFO", pd.DataFrame(rev), "cambios de Facts posteriores al cierre revertidos (as-of)", valor="Valor"))
    if dif:
        al.append(alertas.emitir("FACTURA_MONTO_DISTINTO", "MEDIA", pd.DataFrame(dif), "monto_compra de Facts/RPT difiere de la cantidad (nominal) del CUBO", valor="Valor"))
    sobr = vivas.loc[[i for i in vivas.index if i not in usadas]]
    if len(sobr):
        filas_sobr = pd.DataFrame({"Pos_ID": "", "ID_Fund": sobr["ID_Fund"].values, "PK2": sobr["nemotecnico"].values, "Name_Instrumento": sobr["nemotecnico"].values,
                                   "Valor": pd.to_numeric(sobr["monto_compra"], errors="coerce").values,
                                   "Detalle": [f"fondo={a} estado={b} vence={pd.to_datetime(c, errors='coerce'):%Y-%m-%d}" for a, b, c in zip(sobr["fondo"], sobr["estado"], sobr["fecha_vencimiento"])]})
        al.append(alertas.emitir("FACTURA_SIN_POSICION", "INFO", filas_sobr, "Detalle", valor="Valor"))
    return pd.DataFrame(filas), alertas.juntar(*al)
