"""Bloomberg YAS: yield (campo según Yield_Type) y modified duration por ISIN, solo para lo pendiente. XCCY para hedgeados."""
from __future__ import annotations

import pandas as pd

from .. import alertas
from ..adaptadores.bbg import Bloomberg
from ..config import YIELD_TYPE_BBG
from ..modelo import candidato, candidatos_vacios

CAMPO_DUR = "YAS_MOD_DUR"
CAMPO_XCCY = "YAS_XCCY_FIXED_COUPON_EQUIVALENT"


def _valor(v):
    """Campos numéricos → float; de texto (CPN_TYP, RESET_IDX, INFLATION_LINKED_INDICATOR) → str."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v).strip()


def _cascada(bbg: Bloomberg, isins: list[str], hermanos: dict[str, list[str]], campo: str, **ov) -> tuple[dict, dict]:
    """{isin: valor}, {isin: origen} probando '{ISIN} Corp' → '{ISIN}@BGN Corp' → hermanos."""
    val, org = {}, {}
    if not isins:
        return val, org
    res = bbg.bdp([f"{i} Corp" for i in isins], campo, **ov)
    for i in isins:
        if f"{i} Corp" in res.index and pd.notna(res[f"{i} Corp"]):
            val[i], org[i] = _valor(res[f"{i} Corp"]), "DIRECTO"
    pend = [i for i in isins if i not in val]
    if pend:
        res = bbg.bdp([f"{i}@BGN Corp" for i in pend], campo, **ov)
        for i in pend:
            if f"{i}@BGN Corp" in res.index and pd.notna(res[f"{i}@BGN Corp"]):
                val[i], org[i] = _valor(res[f"{i}@BGN Corp"]), "BGN"
    pend = [i for i in pend if i not in val and hermanos.get(i)]
    if pend:
        tick = sorted({f"{h} Corp" for i in pend for h in hermanos[i]})
        res = bbg.bdp(tick, campo, **ov)
        for i in pend:
            for h in hermanos[i]:
                if f"{h} Corp" in res.index and pd.notna(res[f"{h} Corp"]):
                    val[i], org[i] = _valor(res[f"{h} Corp"]), f"HERMANO:{h}"
                    break
    return val, org


def candidatos_bbg(pos: pd.DataFrame, pendientes: set[str], bbg: Bloomberg, fecha: str, yield_type_default: int = 15
                   ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(candidatos, xccy[Pos_ID, Yield_XCCY], alertas). Yield/XCCY vienen en % → decimal."""
    isin = pos["ISIN"].astype(str).str.strip()
    hermanos = {i: [h for h in str(hs).split(";") if h] for i, hs in zip(isin, pos["ISIN_Hermanos"].astype(str)) if hs}
    ov = {"settle_dt": fecha}
    obj = pos[pos["Pos_ID"].isin(pendientes) & isin.ne("") & pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    yt = pd.to_numeric(obj["Yield_Type"], errors="coerce").fillna(0).astype(int)
    al = []
    if (yt <= 0).any():
        al.append(alertas.emitir("YIELD_TYPE_DEFAULT", "INFO", obj[yt <= 0], f"Yield_Type 0/vacío en el maestro: se pide el default ({yield_type_default})"))
    yt = yt.where(yt > 0, yield_type_default)
    yld, org_y, campo_de = {}, {}, {}
    for codigo, grupo in obj.groupby(yt):
        campo = YIELD_TYPE_BBG.get(int(codigo), YIELD_TYPE_BBG[yield_type_default])
        v, o = _cascada(bbg, sorted(set(grupo["ISIN"].astype(str))), hermanos, campo, **ov)
        yld.update(v); org_y.update(o); campo_de.update({i: campo for i in v})
    dur, org_d = _cascada(bbg, sorted(set(obj["ISIN"].astype(str))), hermanos, CAMPO_DUR, **ov)
    filas, via_hermano = [], []
    for _, p in obj.iterrows():
        i = str(p["ISIN"]).strip()
        if i not in yld and i not in dur:
            continue
        y, d = yld.get(i, float("nan")), dur.get(i, float("nan"))
        origen = org_y.get(i, org_d.get(i, ""))
        if origen.startswith("HERMANO"):
            via_hermano.append(p)
        completo = pd.notna(y) and pd.notna(d)
        filas.append(candidato(p, "BBG", y / 100 if pd.notna(y) else y, d, moneda=str(p.get("Risk_Currency", "")), origen=origen,
                               valido=completo, motivo="" if completo else "TUPLA_INCOMPLETA",
                               detalle=f"campo={campo_de.get(i, '')} dur_origen={org_d.get(i, '')}"))
    if via_hermano:
        al.append(alertas.emitir("FAMILIA_INFERIDA", "INFO", pd.DataFrame(via_hermano), "métrica BBG tomada del ISIN hermano"))
    # XCCY: todos los hedgeados con ISIN (no solo pendientes): H4 decide XCCY vs drop
    hedged = pos[isin.ne("") & pos["Hedge_Currency"].astype(str).str.strip().ne("") & pos["Tratamiento"].isin(["CASCADA", "CAJA"])]
    xccy_filas = []
    for ccy, g in hedged.groupby(hedged["Hedge_Currency"].astype(str).str.strip().str.upper()):
        v, _ = _cascada(bbg, sorted(set(g["ISIN"].astype(str))), hermanos, CAMPO_XCCY, YAS_XCCY_FOREIGN_CURRENCY=ccy, **ov)
        for _, p in g.iterrows():
            if str(p["ISIN"]) in v:
                xccy_filas.append({"Pos_ID": p["Pos_ID"], "Yield_XCCY": v[str(p["ISIN"])] / 100})
    xccy = pd.DataFrame(xccy_filas, columns=["Pos_ID", "Yield_XCCY"])
    return (pd.DataFrame(filas) if filas else candidatos_vacios()), xccy, alertas.juntar(*al)
