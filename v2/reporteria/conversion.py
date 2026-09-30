"""Conversión de la yield del papel a la moneda en que el fondo la mira: breakeven (indexados), suma de índice (flotantes
propios) y XCCY / drop (papeles USD swapeados). `Yield`/`Duration` quedan finales; el valor del proveedor se conserva en
`Yield_Papel`/`Duration_Papel` y el detalle numérico va a la hoja `conversiones`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import alertas
from .config import FUENTES_TD_PROPIA, INDICES, INDICES_SIN_CONVERSION, POLITICAS_XCCY
from .curvas import interpolar
from .finanzas import breakeven, drop, sumar_indice
from .modelo import limpiar_txt

NO_CONVERTIBLES = {"CERO", "REGLA_DEF", "OVERRIDE", "EXCLUIR", ""}
COLS_DETALLE = ["Pos_ID", "PK2", "Tipo", "Indice", "Hedge_Currency", "Fuente", "Yield_Papel", "Duration_Papel", "plazo_dias",
                "r_real", "r_nom", "ajuste", "r_local", "r_basis", "r_usd", "drop", "Yield_Local", "Yield_Drop", "Yield_XCCY",
                "Dif_XCCY_Drop_bps", "Yield", "Duration", "Extrapolado", "Resultado"]


def _fila(p, tipo, **kw):
    base = dict(Pos_ID=p["Pos_ID"], PK2=p["PK2"], Tipo=tipo, Indice=p.get("Indice", ""), Hedge_Currency=p.get("Hedge_Currency", ""),
                Fuente=p["Fuente"], Yield_Papel=p["Yield"], Duration_Papel=p["Duration"])
    return {**{c: np.nan for c in COLS_DETALLE}, **base, **kw}


def convertir(pos: pd.DataFrame, curvas_real: dict, curvas_nom: dict, curvas_drop: dict, parametros: dict
              ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(posiciones, detalle, alertas). Solo toca posiciones RESUELTAS por proveedor/TD/caja/factura."""
    pos = pos.copy()
    politica = str(parametros.get("politica_hedge", POLITICAS_XCCY[0])).strip().upper() or POLITICAS_XCCY[0]
    if politica not in POLITICAS_XCCY:
        raise ValueError(f"REGLAS/parametros: politica_hedge={politica!r}; válidas {list(POLITICAS_XCCY)}")
    tope_bps = float(parametros.get("xccy_drop_max_bps", 50))
    ymax, ymin = float(parametros.get("yield_max_proveedor", 1.0)), float(parametros.get("yield_min_proveedor", -0.5))
    for c in ("Yield_Papel", "Duration_Papel", "Yield_Drop", "Dif_XCCY_Drop_bps"):
        pos[c] = np.nan
    if "Yield_XCCY" not in pos.columns:
        pos["Yield_XCCY"] = np.nan
    pos["Conversion"], pos["Extrapolado"] = "", False
    pos["Yield_Papel"], pos["Duration_Papel"] = pos["Yield"], pos["Duration"]
    ind = pos["Indice"].astype(str).str.upper() if "Indice" in pos.columns else pd.Series("", index=pos.index)
    hedge = limpiar_txt(pos["Hedge_Currency"]).str.upper()
    ccy = limpiar_txt(pos["Risk_Currency"]).str.upper()
    aplica = pos["Estado"].eq("RESUELTO") & ~pos["Fuente"].isin(NO_CONVERTIBLES) & pos["Yield"].notna() & pos["Duration"].notna()
    det, al_be, al_hd, al_dif, al_prov, al_ccy, al_sc, al_xr = [], [], [], [], [], [], [], []
    for i in pos.index[aplica]:
        p = pos.loc[i]
        idx, dias = ind[i], float(p["Duration"]) * 365.25
        y, d = float(p["Yield"]), float(p["Duration"])
        # ── indexados ──
        if idx not in INDICES_SIN_CONVERSION and not idx.startswith("NUEVO"):
            cfg = INDICES.get(idx)
            if cfg is None:
                al_be.append((i, f"índice {idx} sin curvas en config.INDICES"))
                det.append(_fila(p, "BREAKEVEN", Resultado=f"SIN_CURVAS:{idx}")); continue
            if cfg["cat"] == "RATE" and cfg["real"] is None:      # índice conocido sin curva: la yield ya es nominal local
                pos.loc[i, "Conversion"] = "PROVEEDOR_NOMINAL"
                al_sc.append(i)
                det.append(_fila(p, "SUMA_INDICE", Yield=y, Duration=d, Resultado="PROVEEDOR_NOMINAL")); continue
            r_real, ext = interpolar(dias, curvas_real.get(cfg["real"], pd.DataFrame()))
            if cfg["cat"] == "REAL":
                r_nom, ext2 = interpolar(dias, curvas_nom.get(cfg["nom"], pd.DataFrame()))
                if pd.isna(r_real) or pd.isna(r_nom):
                    falta = cfg["real"] if pd.isna(r_real) else cfg["nom"]
                    al_be.append((i, f"curva {falta} sin datos al cierre"))
                    det.append(_fila(p, "BREAKEVEN", plazo_dias=round(dias), r_real=r_real, r_nom=r_nom, Resultado=f"SIN_CURVA:{falta}")); continue
                y_loc, d_loc, aj = breakeven(y, d, r_real, r_nom)
                pos.loc[i, ["Yield", "Duration", "Yield_Moneda", "Conversion", "Extrapolado"]] = [y_loc, d_loc, cfg["ccy"], "BREAKEVEN", ext or ext2]
                det.append(_fila(p, "BREAKEVEN", plazo_dias=round(dias), r_real=r_real, r_nom=r_nom, ajuste=aj, Yield_Local=y_loc,
                                 Yield=y_loc, Duration=d_loc, Extrapolado=ext or ext2, Resultado="OK"))
            else:                                   # RATE
                if p["Fuente"] not in FUENTES_TD_PROPIA:
                    pos.loc[i, "Conversion"] = "PROVEEDOR_NOMINAL"
                    al_prov.append(i)
                    det.append(_fila(p, "SUMA_INDICE", Yield=y, Duration=d, Resultado="PROVEEDOR_NOMINAL")); continue
                if pd.isna(r_real):
                    al_be.append((i, f"curva {cfg['real']} sin datos al cierre"))
                    det.append(_fila(p, "SUMA_INDICE", plazo_dias=round(dias), Resultado=f"SIN_CURVA:{cfg['real']}")); continue
                y_loc, d_loc = sumar_indice(y, d, r_real)
                pos.loc[i, ["Yield", "Duration", "Yield_Moneda", "Conversion", "Extrapolado"]] = [y_loc, d_loc, cfg["ccy"], "SUMA_INDICE", ext]
                det.append(_fila(p, "SUMA_INDICE", plazo_dias=round(dias), r_real=r_real, Yield_Local=y_loc, Yield=y_loc, Duration=d_loc,
                                 Extrapolado=ext, Resultado="OK"))
            continue
        # ── hedgeados (papel USD swapeado a moneda local nominal) ──
        if hedge[i] and hedge[i] != ccy[i]:
            xccy = float(p["Yield_XCCY"]) if pd.notna(p.get("Yield_XCCY")) else np.nan
            fila = _fila(p, "HEDGE", Yield_XCCY=xccy)
            if pd.notna(xccy) and not (ymin <= xccy <= ymax):       # mismo rango de sanidad que los proveedores
                al_xr.append(i); xccy = np.nan
            y_drop, r_l, r_b, r_u, dr, ext = np.nan, np.nan, np.nan, np.nan, np.nan, False
            if ccy[i] != "USD":
                al_ccy.append(i)
            elif hedge[i] in curvas_drop and curvas_drop[hedge[i]].get("local") is not None and len(curvas_drop[hedge[i]].get("local", ())) \
                    and len(curvas_drop[hedge[i]].get("usd", ())):
                c = curvas_drop[hedge[i]]
                r_l, e1 = interpolar(dias, c["local"]); r_u, e2 = interpolar(dias, c["usd"])
                r_b, e3 = interpolar(dias, c["basis"]) if c.get("basis") is not None and len(c.get("basis", ())) else (0.0, False)
                if "basis" in c and not len(c["basis"]):
                    r_b = np.nan
                if pd.notna(r_l) and pd.notna(r_u) and pd.notna(r_b):
                    y_drop, dr = drop(y, r_l, r_u, r_b); ext = e1 or e2 or e3
            dif = (xccy - y_drop) * 1e4 if pd.notna(xccy) and pd.notna(y_drop) else np.nan
            pos.loc[i, ["Yield_Drop", "Dif_XCCY_Drop_bps", "Extrapolado"]] = [y_drop, dif, ext]
            if pd.notna(dif) and abs(dif) > tope_bps:
                al_dif.append(i)
            if politica == "XCCY_SI_EXISTE" and pd.notna(xccy):
                y_fin, res = xccy, "XCCY"
            elif pd.notna(y_drop):
                y_fin, res = y_drop, "DROP"
            elif pd.notna(xccy):
                y_fin, res = xccy, "XCCY"
            else:
                al_hd.append(i)
                det.append({**fila, "Yield": y, "Duration": d, "Resultado": "SIN_CONVERSION"}); continue
            pos.loc[i, ["Yield", "Yield_Moneda", "Conversion"]] = [y_fin, hedge[i], res]
            det.append({**fila, "plazo_dias": round(dias), "r_local": r_l, "r_basis": r_b, "r_usd": r_u, "drop": dr, "Yield_Drop": y_drop,
                        "Dif_XCCY_Drop_bps": dif, "Yield": y_fin, "Duration": d, "Extrapolado": ext, "Resultado": res})
    al = []
    if al_be:
        idx_be = [i for i, _ in al_be]
        al.append(alertas.emitir("SIN_BREAKEVEN", "ALTA", pos.loc[idx_be].assign(Valor=[m for _, m in al_be]),
                                 "yield real sin convertir a moneda local: queda la yield del papel", valor="Valor"))
    if al_hd:
        al.append(alertas.emitir("SIN_CONVERSION_HEDGE", "ALTA", pos.loc[al_hd], "hedgeado sin XCCY ni curvas de drop: queda la yield en USD", valor="Hedge_Currency"))
    if al_ccy:
        al.append(alertas.emitir("HEDGE_PAPEL_NO_USD", "MEDIA", pos.loc[al_ccy], "las curvas de drop asumen papel en USD; solo se usa XCCY si existe", valor="Risk_Currency"))
    if al_dif:
        al.append(alertas.emitir("XCCY_VS_DROP", "MEDIA", pos.loc[al_dif], f"|XCCY − drop propio| > {tope_bps:.0f} bps", valor="Dif_XCCY_Drop_bps"))
    if al_prov:
        al.append(alertas.emitir("FLOTANTE_PROVEEDOR", "INFO", pos.loc[al_prov], "flotante con yield de proveedor: se asume ya nominal en moneda local (supuesto no verificado)", valor="Indice"))
    if al_sc:
        al.append(alertas.emitir("INDICE_SIN_CURVA", "INFO", pos.loc[al_sc], "índice flotante sin curva en config.INDICES: la yield de la TD se toma como nominal local", valor="Indice"))
    if al_xr:
        al.append(alertas.emitir("XCCY_FUERA_RANGO", "MEDIA", pos.loc[al_xr], f"Yield_XCCY fuera de [{ymin:g}, {ymax:g}]: se ignora y se usa el drop propio", valor="Yield_XCCY"))
    if pos["Extrapolado"].any():
        al.append(alertas.emitir("CURVA_EXTRAPOLADA", "INFO", pos[pos["Extrapolado"].astype(bool)], "plazo fuera del rango de tenores: tasa plana del extremo", valor="Duration_Papel"))
    return pos, pd.DataFrame(det, columns=COLS_DETALLE), alertas.juntar(*al)
