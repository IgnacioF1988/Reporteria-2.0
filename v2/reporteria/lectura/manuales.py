"""Archivos mantenidos por el analista o exportados de otros sistemas: facturas (RPT de Facts), excepciones, atributos."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import limpiar_txt

COLS_FACTURAS = ("nemotecnico", "fondo", "estado", "monto_compra", "fecha_vencimiento", "tasa_mensual")


def leer_facturas(path: Path) -> pd.DataFrame:
    """Hoja `Facturas` del RPT de Facts: una fila por factura comprada. tasa_mensual viene DECIMAL (0.008 = 0,8 %)."""
    xl = pd.ExcelFile(path)
    df = xl.parse("Facturas" if "Facturas" in xl.sheet_names else xl.sheet_names[0])
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in COLS_FACTURAS if c not in df.columns]:
        raise ValueError(f"FACTURAS (RPT) sin columnas {faltan}")
    for c in ("nemotecnico", "fondo", "estado"):
        df[c] = limpiar_txt(df[c])
    df["estado"] = df["estado"].str.lower()
    df["tasa_mensual"] = pd.to_numeric(df["tasa_mensual"], errors="coerce")
    df["monto_compra"] = pd.to_numeric(df["monto_compra"], errors="coerce")
    df["fecha_vencimiento"] = pd.to_datetime(df["fecha_vencimiento"], errors="coerce")
    df["fecha_pago"] = pd.to_datetime(df["fecha_pago"], errors="coerce") if "fecha_pago" in df.columns else pd.NaT
    if (df["tasa_mensual"].abs() > 0.1).any():
        raise ValueError("FACTURAS: tasa_mensual debe venir decimal (0.008 = 0,8 % mensual); hay valores > 0.1")
    return df[df["nemotecnico"].ne("")].reset_index(drop=True)


def leer_excepciones(paths, settle: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """EXCEPCIONES*.xlsx: una pestaña por PK2 con Fecha / Flujo (base 1.000.000), opcional BalanceSheet.

    Devuelve {PK2: flujos futuros (Asset)} y una tabla de avisos: PK2_DUPLICADO (se usa la primera aparición),
    SIN_COLUMNAS (pestaña sin Fecha/Flujo). El PK2 sale de la columna PK2 si existe; si no, del nombre de la pestaña.
    """
    from .. import alertas
    flujos, origen, avisos = {}, {}, []
    for ruta in paths:
        xl = pd.ExcelFile(ruta)
        for hoja in xl.sheet_names:
            df = xl.parse(hoja)
            df.columns = [str(c).strip() for c in df.columns]
            if not {"Fecha", "Flujo"} <= set(df.columns):
                avisos.append(alertas.emitir("SIN_COLUMNAS", "MEDIA", detalle=f"{Path(ruta).name}/{hoja}: faltan Fecha/Flujo", ambito="CORRIDA"))
                continue
            pk2 = limpiar_txt(df["PK2"]).replace("", pd.NA).dropna().iloc[0] if "PK2" in df.columns and limpiar_txt(df["PK2"]).ne("").any() else hoja.strip()
            if pk2 in flujos:
                avisos.append(alertas.emitir("PK2_DUPLICADO", "ALTA", detalle=f"{pk2} en {origen[pk2]} y {Path(ruta).name}: se usa {origen[pk2]}", ambito="CORRIDA"))
                continue
            df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
            df["Flujo"] = pd.to_numeric(df["Flujo"], errors="coerce")
            df = df.dropna(subset=["Fecha", "Flujo"])
            if "BalanceSheet" in df.columns:
                df = df[limpiar_txt(df["BalanceSheet"]).str.upper().ne("LIABILITY")]
            flujos[pk2] = df[df["Fecha"] > settle][["Fecha", "Flujo"]].sort_values("Fecha").reset_index(drop=True)
            origen[pk2] = Path(ruta).name
    return flujos, alertas.juntar(*avisos)
