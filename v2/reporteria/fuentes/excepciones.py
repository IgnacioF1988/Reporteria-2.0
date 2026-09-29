"""Flujos entregados por el PM (base 1.000.000): se escalan con Q_real = Qty × sQ y se calcula XIRR/duration por posición.

Flujo inicial = −(P_ef × Q_real) − AI_local, en moneda del papel. (sP, sQ, FX) se identifican calzando contra MVBook.
"""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..escala import EscalaCfg, candidatos_fx, elegir_escala_fx
from ..finanzas import duracion, xirr
from ..modelo import candidato, candidatos_vacios

FACE = 1_000_000.0


def candidatos_excepciones(pos: pd.DataFrame, flujos: dict[str, pd.DataFrame], fx_bee: dict, fx_par: dict,
                           settle: pd.Timestamp, cfg: EscalaCfg) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Devuelve (candidatos, tds escaladas, alertas)."""
    obj = pos[pos["PK2"].isin(flujos) & pos["Tratamiento"].ne("EXCLUIR")]
    if obj.empty:
        return candidatos_vacios(), pd.DataFrame(), alertas.vacias()
    filas, tds, fallback = [], [], []
    for _, p in obj.iterrows():
        td = flujos[p["PK2"]]
        if td.empty or not (td["Flujo"].abs() > 1e-9).any():
            filas.append(candidato(p, "EXCEPCIONES", valido=False, motivo="TD_SIN_FLUJOS")); continue
        lp, qty = pd.to_numeric(p.get("LocalPrice"), errors="coerce"), pd.to_numeric(p.get("Qty"), errors="coerce")
        of, fac = pd.to_numeric(p.get("OriginalFace"), errors="coerce"), pd.to_numeric(p.get("Factor"), errors="coerce")
        mvb, ai = pd.to_numeric(p.get("MVBook"), errors="coerce"), pd.to_numeric(p.get("AI"), errors="coerce")
        fac, ai = (1.0 if pd.isna(fac) else fac), (0.0 if pd.isna(ai) else ai)
        if pd.isna(lp) or pd.isna(mvb) or mvb == 0 or pd.isna(qty) or qty == 0:
            filas.append(candidato(p, "EXCEPCIONES", valido=False, motivo="SIN_PRECIO_O_CANTIDAD")); continue
        cands = candidatos_fx(str(p.get("Risk_Currency", "")), fx_bee, fx_par, str(p.get("FundBaseCurrency", "USD")) or "USD")
        of_base = of if pd.notna(of) and of != 0 else qty
        e = elegir_escala_fx(lp, of_base, fac, mvb, cands, cfg)
        if e["Escala_Flag"] == "SIN_FX":
            filas.append(candidato(p, "EXCEPCIONES", valido=False, motivo="SIN_FX")); continue
        if e["Escala_Flag"] == "FALLBACK":
            fallback.append(p)
        q_real, p_ef = qty * e["sQ"], lp * e["sP"]
        ai_local = ai * e["FX"]
        fi = -(p_ef * q_real) - ai_local
        esc = q_real / FACE
        ff, fe = (td["Flujo"] * esc).tolist(), td["Fecha"].tolist()
        y = xirr([fi] + ff, [settle] + fe)
        mac, mod = duracion(ff, fe, settle, y)
        filas.append(candidato(p, "EXCEPCIONES", y, mod, moneda=str(p.get("Risk_Currency", "")), origen="PM",
                               valido=pd.notna(y) and pd.notna(mod), motivo="" if pd.notna(y) else "XIRR_SIN_SOLUCION",
                               detalle=f"sP={e['sP']} sQ={e['sQ']} fx={e['FX']:.6g}({e['FX_Fuente']}) flag={e['Escala_Flag']} "
                                       f"q_real={q_real:.2f} fi={fi:.2f} n={len(ff)} mac={mac:.4f}"))
        tds.append(pd.DataFrame({"Pos_ID": p["Pos_ID"], "Fuente": "EXCEPCIONES", "Fecha": fe, "Flujo": ff}))
    al = alertas.emitir("ESCALA_FALLBACK", "MEDIA", pd.DataFrame(fallback), "escala/FX no calzó con MVBook al 0,5 %: se usó la mejor aproximación",
                        valor="TotalMVal") if fallback else alertas.vacias()
    return pd.DataFrame(filas), (pd.concat(tds, ignore_index=True) if tds else pd.DataFrame()), al
