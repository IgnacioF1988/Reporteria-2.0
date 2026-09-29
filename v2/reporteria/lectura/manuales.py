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
