"""CSHF: tabla de desarrollo de Bloomberg (DES_CASH_FLOW) + XIRR/duración propia, para lo que YAS no resolvió."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..adaptadores.bbg import Bloomberg
from ..escala import EscalaCfg, candidatos_fx, elegir_escala_fx
from ..finanzas import duracion, xirr
from ..modelo import candidato, candidatos_vacios
from ..td import normalizar_td_bbg

FACE_PEDIDO = 1000.0


def _td(bbg: Bloomberg, isin: str, hermanos: list[str], fecha: str) -> tuple[pd.DataFrame, float, str]:
    for tick, etq in [(f"{isin} Corp", "DIRECTO"), (f"{isin} Govt", "GOVT")] + [(f"{h} Corp", f"HERMANO:{h}") for h in hermanos]:
        raw = bbg.bds(tick, "DES_CASH_FLOW", SETTLE_DT=fecha, BQ_FACE_AMT=FACE_PEDIDO)
        td, face = normalizar_td_bbg(raw, FACE_PEDIDO)
        if len(td):
            return td, face, etq
    return pd.DataFrame(), FACE_PEDIDO, ""


def candidatos_cshf(pos: pd.DataFrame, pendientes: set[str], bbg: Bloomberg, fx_bee: dict, fx_par: dict, settle: pd.Timestamp,
                    cfg: EscalaCfg) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    isin = pos["ISIN"].astype(str).str.strip()
    obj = pos[pos["Pos_ID"].isin(pendientes) & isin.ne("") & pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    if obj.empty:
        return candidatos_vacios(), pd.DataFrame(), alertas.vacias()
    fecha = settle.strftime("%Y%m%d")
    cache_td: dict[str, tuple] = {}
    filas, tds, fallback, via_h = [], [], [], []
    for _, p in obj.iterrows():
        i = str(p["ISIN"]).strip()
        if i not in cache_td:
            cache_td[i] = _td(bbg, i, [h for h in str(p.get("ISIN_Hermanos", "")).split(";") if h], fecha)
        td, face, etq = cache_td[i]
        if td.empty:
            filas.append(candidato(p, "CSHF", valido=False, motivo="SIN_TD_BBG")); continue
        fut = td[td["Fecha"] > settle]
        if fut.empty or not (fut["Flujo"].abs() > 1e-9).any():
            filas.append(candidato(p, "CSHF", valido=False, motivo="TD_SIN_FLUJOS_FUTUROS")); continue
        lp, qty = pd.to_numeric(p.get("LocalPrice"), errors="coerce"), pd.to_numeric(p.get("Qty"), errors="coerce")
        of, fac = pd.to_numeric(p.get("OriginalFace"), errors="coerce"), pd.to_numeric(p.get("Factor"), errors="coerce")
        mvb, ai = pd.to_numeric(p.get("MVBook"), errors="coerce"), pd.to_numeric(p.get("AI"), errors="coerce")
        fac, ai = (1.0 if pd.isna(fac) else fac), (0.0 if pd.isna(ai) else ai)
        if pd.isna(lp) or pd.isna(mvb) or mvb == 0 or pd.isna(qty) or qty == 0:
            filas.append(candidato(p, "CSHF", valido=False, motivo="SIN_PRECIO_O_CANTIDAD")); continue
        of_base = of if pd.notna(of) and of != 0 else qty
        cands = candidatos_fx(str(p.get("Risk_Currency", "")), fx_bee, fx_par, str(p.get("FundBaseCurrency", "USD")) or "USD")
        e = elegir_escala_fx(lp, of_base, fac, mvb, cands, cfg)
        if e["Escala_Flag"] == "SIN_FX":
            filas.append(candidato(p, "CSHF", valido=False, motivo="SIN_FX")); continue
        if e["Escala_Flag"] == "FALLBACK":
            fallback.append(p)
        if etq.startswith("HERMANO"):
            via_h.append(p)
        q_real, p_ef = of_base * e["sQ"] * fac, lp * e["sP"]
        fi = -(p_ef * q_real) - ai * e["FX"]
        esc = q_real / face
        ff, fe = (fut["Flujo"] * esc).tolist(), fut["Fecha"].tolist()
        y = xirr([fi] + ff, [settle] + fe)
        mac, mod = duracion(ff, fe, settle, y)
        filas.append(candidato(p, "CSHF", y, mod, moneda=str(p.get("Risk_Currency", "")), origen=etq,
                               valido=pd.notna(y) and pd.notna(mod), motivo="" if pd.notna(y) else "XIRR_SIN_SOLUCION",
                               detalle=f"face={face:.2f} sP={e['sP']} sQ={e['sQ']} fx={e['FX']:.6g}({e['FX_Fuente']}) flag={e['Escala_Flag']} "
                                       f"q_real={q_real:.2f} fi={fi:.2f} n={len(ff)} mac={mac:.4f}"))
        tds.append(pd.DataFrame({"Pos_ID": p["Pos_ID"], "Fuente": "CSHF", "Fecha": fe, "Flujo": ff}))
    al = alertas.juntar(
        alertas.emitir("ESCALA_FALLBACK", "MEDIA", pd.DataFrame(fallback), "escala/FX no calzó con MVBook: mejor aproximación", valor="TotalMVal") if fallback else None,
        alertas.emitir("FAMILIA_INFERIDA", "INFO", pd.DataFrame(via_h), "TD de Bloomberg tomada del ISIN hermano") if via_h else None)
    return pd.DataFrame(filas), (pd.concat(tds, ignore_index=True) if tds else pd.DataFrame()), al
