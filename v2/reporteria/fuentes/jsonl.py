"""TD propia desde los eventos de Geneva (bond_schedule.jsonl) para lo que ningún proveedor cubrió."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..escala import EscalaCfg, candidatos_fx, elegir_escala_fx
from ..finanzas import duracion, xirr
from ..modelo import candidato, candidatos_vacios, limpiar_txt
from ..td import MOTIVO, NO_MODELABLES, clasificar_geneva, construir_td_geneva


def _record(recs: dict, codigos: list[str], isin: str, nombre: str):
    for c in codigos:
        if c in recs:
            return recs[c], c, "HOMOL"
    if isin and isin in recs:
        return recs[isin], isin, "ISIN"
    if nombre in recs:
        return recs[nombre], nombre, "NOMBRE"
    return None, "", ""


def candidatos_jsonl(pos: pd.DataFrame, pendientes: set[str], recs: dict, homol_geneva: pd.DataFrame | None, fx_bee: dict,
                     fx_par: dict, settle: pd.Timestamp, cfg: EscalaCfg) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Pos_ID"].isin(pendientes) & pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    if obj.empty or not recs:
        return candidatos_vacios(), pd.DataFrame(), alertas.vacias()
    id2codes: dict[int, list[str]] = {}
    if homol_geneva is not None and len(homol_geneva):
        for iid, c in zip(homol_geneva["ID_Instrumento"], homol_geneva["SourceInvestment"].astype(str)):
            id2codes.setdefault(int(iid), []).append(c)
    filas, tds, fallback, ai_raro = [], [], [], []
    for _, p in obj.iterrows():
        iid = int(p["ID_Instrumento"]) if pd.notna(p.get("ID_Instrumento")) else -1
        rec, code, via = _record(recs, id2codes.get(iid, []), str(p.get("ISIN", "")).strip(), str(p.get("Name_Instrumento", "")).strip())
        if rec is None:
            filas.append(candidato(p, "JSONL", valido=False, motivo="SIN_RECORD_GENEVA", detalle="con código HOMOL" if id2codes.get(iid) else "sin código GENEVA en HOMOL"))
            continue
        tipo, tasa, diag = clasificar_geneva(rec, settle)
        if tipo in NO_MODELABLES:
            filas.append(candidato(p, "JSONL", valido=False, motivo=tipo, detalle=f"{MOTIVO[tipo]} | ratio={diag.get('ratio_obs_teo')}"))
            continue
        lp, of = pd.to_numeric(p.get("LocalPrice"), errors="coerce"), pd.to_numeric(p.get("OriginalFace"), errors="coerce")
        fac, mvb = pd.to_numeric(p.get("Factor"), errors="coerce"), pd.to_numeric(p.get("MVBook"), errors="coerce")
        ai = pd.to_numeric(p.get("AI"), errors="coerce")
        fac, ai = (1.0 if pd.isna(fac) else fac), (0.0 if pd.isna(ai) else ai)
        if pd.isna(lp) or pd.isna(mvb) or mvb == 0 or pd.isna(of) or of == 0:
            filas.append(candidato(p, "JSONL", valido=False, motivo="SIN_PRECIO_O_CANTIDAD")); continue
        cands = candidatos_fx(str(p.get("Risk_Currency", "")), fx_bee, fx_par, str(p.get("FundBaseCurrency", "USD")) or "USD")
        e = elegir_escala_fx(lp, of, fac, mvb, cands, cfg)
        if e["Escala_Flag"] == "SIN_FX":
            filas.append(candidato(p, "JSONL", valido=False, motivo="SIN_FX")); continue
        if e["Escala_Flag"] == "FALLBACK":
            fallback.append(p)
        of_ef = of * e["sQ"]
        td, info = construir_td_geneva(rec, of_ef, tipo, tasa, settle)
        if td.empty:
            filas.append(candidato(p, "JSONL", valido=False, motivo="SIN_FLUJOS_FUTUROS")); continue
        q_real, p_ef, ai_local = of_ef * fac, lp * e["sP"], ai * e["FX"]
        fi = -(p_ef * q_real) - ai_local
        ff, fe = td["Flujo"].tolist(), td["Fecha"].tolist()
        y = xirr([fi] + ff, [settle] + fe)
        mac, mod = duracion(ff, fe, settle, y)
        ult = info.get("ultimo_cupon_pagado")
        if pd.notna(tasa) and tasa and ult is not None and ai_local > 0:
            teorico = tasa * ((settle - pd.Timestamp(ult)).days / 365.0) * (info.get("cap_vigente_ini", 100.0) / 100.0) * of_ef / 100.0
            if teorico and (ai_local / teorico > 3 or ai_local / teorico < 0.33):
                ai_raro.append({**p.to_dict(), "Valor": ai_local / teorico})
        filas.append(candidato(p, "JSONL", y, mod, moneda=str(p.get("Risk_Currency", "")), origen=f"{via}:{code}",
                               valido=pd.notna(y) and pd.notna(mod), motivo="" if pd.notna(y) else "XIRR_SIN_SOLUCION",
                               detalle=f"tipo={tipo} tasa={tasa} sP={e['sP']} sQ={e['sQ']} fx={e['FX']:.6g}({e['FX_Fuente']}) flag={e['Escala_Flag']} "
                                       f"q_real={q_real:.2f} fi={fi:.2f} cupones_geneva={info.get('cupones_geneva')} proyectados={info.get('cupones_proyectados')} mac={mac:.4f}"))
        tds.append(td.assign(Pos_ID=p["Pos_ID"], Fuente="JSONL")[["Pos_ID", "Fuente", "Fecha", "Flujo", "Cupon", "Capital", "Origen"]])
    al = alertas.juntar(
        alertas.emitir("ESCALA_FALLBACK", "MEDIA", pd.DataFrame(fallback), "escala/FX no calzó con MVBook: mejor aproximación", valor="TotalMVal") if fallback else None,
        alertas.emitir("AI_SOSPECHOSO", "MEDIA", pd.DataFrame(ai_raro), "AI del CUBO muy distinto del devengado teórico (ratio AI/teórico)", valor="Valor") if ai_raro else None)
    return pd.DataFrame(filas), (pd.concat(tds, ignore_index=True) if tds else pd.DataFrame()), al
