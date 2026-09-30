"""Facturas desde la base de Facts (bi_facturas / bi_prorrogas / bi_cambios): estado de cada factura AL CIERRE.

La base está viva y el cierre se corre días después: se revierten con bi_cambios los cambios de tasa, vencimiento y monto
posteriores al cierre; una factura pagada después del cierre sigue viva al cierre; si hay prórroga iniciada al cierre o
antes, mandan su tasa y su vencimiento (el RPT usa la tasa original: aquí se documenta la diferencia en `tasa_origen`).
"""
from __future__ import annotations

import pandas as pd

from ..modelo import limpiar_txt

COLS_MIN = {"facturas": ("documento_operacion_id", "nemotecnico", "fondo", "estado", "monto_compra", "fecha_vencimiento", "fecha_pago", "tasa_mensual"),
            "prorrogas": ("documento_operacion_id", "fecha_inicio", "fecha_vencimiento_nueva", "tasa_mensual"),
            "cambios": ("documento_operacion_id", "fecha", "campo", "valor_anterior", "valor_nuevo")}
FECHAS = {"facturas": ("fecha_inversion", "fecha_vencimiento_original", "fecha_vencimiento", "fecha_pago", "fecha_vencimiento_prorroga"),
          "prorrogas": ("fecha_inicio", "fecha_vencimiento_nueva", "fecha_registro"), "cambios": ("fecha",)}
NUMEROS = {"facturas": ("monto_compra", "tasa_mensual", "tasa_mensual_prorroga", "prorrogas"), "prorrogas": ("tasa_mensual",), "cambios": ()}
CAMPOS_ASOF = {"tasa_interes": "tasa_mensual", "fecha_vencimiento": "fecha_vencimiento", "monto_compra": "monto_compra"}
SALIDA = ["documento_operacion_id", "nemotecnico", "fondo", "estado", "monto_compra", "fecha_vencimiento", "fecha_pago", "tasa_mensual",
          "tasa_origen", "vencimiento_origen", "cambios_revertidos"]


def normalizar_tablas(t: dict) -> dict[str, pd.DataFrame]:
    """Tipos y textos limpios; valida columnas mínimas y que la tasa venga decimal (0.008 = 0,8 % mensual)."""
    out = {}
    for tabla, cols in COLS_MIN.items():
        df = t.get(tabla)
        df = pd.DataFrame(columns=cols) if df is None else df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        if faltan := [c for c in cols if c not in df.columns]:
            raise ValueError(f"FACTS/bi_{tabla} sin columnas {faltan}")
        for c in FECHAS[tabla]:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        for c in NUMEROS[tabla]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df["documento_operacion_id"] = pd.to_numeric(df["documento_operacion_id"], errors="coerce").astype("Int64")
        out[tabla] = df
    f = out["facturas"]
    for c in ("nemotecnico", "fondo", "estado"):
        f[c] = limpiar_txt(f[c])
    f["estado"] = f["estado"].str.lower()
    if (f["tasa_mensual"].abs() > 0.1).any():
        raise ValueError("FACTS/bi_facturas: tasa_mensual debe venir decimal (0.008 = 0,8 % mensual); hay valores > 0.1")
    out["cambios"]["campo"] = limpiar_txt(out["cambios"]["campo"]).str.lower()
    return out


def facturas_al_cierre(t: dict[str, pd.DataFrame], settle: pd.Timestamp) -> pd.DataFrame:
    """Una fila por factura viva al cierre con tasa, vencimiento y monto vigentes a esa fecha (ver docstring del módulo)."""
    f = t["facturas"].copy()
    f["tasa_origen"], f["vencimiento_origen"], f["cambios_revertidos"] = "ORIGINAL", "ORIGINAL", 0
    f = f[f["documento_operacion_id"].notna()]
    if "fecha_inversion" in f.columns:
        f = f[f["fecha_inversion"].isna() | (f["fecha_inversion"] <= settle)]
    f = f[f["fecha_pago"].isna() | (f["fecha_pago"] > settle)].set_index("documento_operacion_id")
    # a. cambios posteriores al cierre: vale el valor_anterior del más antiguo después del cierre (los del día del cierre ya están)
    c = t["cambios"]
    post = c[(c["fecha"].dt.normalize() > settle) & c["campo"].isin(CAMPOS_ASOF) & c["documento_operacion_id"].isin(f.index)]
    post = post.sort_values("fecha").drop_duplicates(["documento_operacion_id", "campo"], keep="first")
    for _, r in post.iterrows():
        doc, col, ant = r["documento_operacion_id"], CAMPOS_ASOF[r["campo"]], r["valor_anterior"]
        if pd.isna(ant) or str(ant).strip() == "":
            continue
        f.loc[doc, col] = pd.to_datetime(ant, errors="coerce") if col == "fecha_vencimiento" else pd.to_numeric(ant, errors="coerce")
        f.loc[doc, "cambios_revertidos"] += 1
        if col == "tasa_mensual":
            f.loc[doc, "tasa_origen"] = "CAMBIO_REVERTIDO"
        elif col == "fecha_vencimiento":
            f.loc[doc, "vencimiento_origen"] = "CAMBIO_REVERTIDO"
    p = t["prorrogas"][t["prorrogas"]["documento_operacion_id"].isin(f.index)]
    # b. prórroga posterior al cierre sin cambio registrado: vuelve al vencimiento original
    solo_post = p.groupby("documento_operacion_id")["fecha_inicio"].min()
    solo_post = solo_post[solo_post > settle].index
    if len(solo_post) and "fecha_vencimiento_original" in f.columns:
        m = f.index.isin(solo_post) & f["vencimiento_origen"].eq("ORIGINAL") & f["fecha_vencimiento_original"].notna()
        f.loc[m, "fecha_vencimiento"] = f.loc[m, "fecha_vencimiento_original"]
    # c. prórroga vigente (iniciada al cierre o antes): mandan su tasa y su vencimiento
    vig = p[p["fecha_inicio"] <= settle].sort_values(["fecha_inicio"] + (["fecha_registro"] if "fecha_registro" in p.columns else []))
    vig = vig.drop_duplicates("documento_operacion_id", keep="last").set_index("documento_operacion_id")
    for doc, r in vig.iterrows():
        if pd.notna(r["tasa_mensual"]):
            f.loc[doc, ["tasa_mensual", "tasa_origen"]] = [float(r["tasa_mensual"]), "PRORROGA"]
        if pd.notna(r["fecha_vencimiento_nueva"]):
            f.loc[doc, ["fecha_vencimiento", "vencimiento_origen"]] = [r["fecha_vencimiento_nueva"], "PRORROGA"]
    f = f.reset_index()
    return f[[c for c in SALIDA if c in f.columns]].reset_index(drop=True)
