"""Archivos mantenidos por el analista: facturas (nuevo), excepciones, atributos."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import limpiar_txt


def leer_facturas(path: Path) -> pd.DataFrame:
    """FACTURAS_{FECHA}.xlsx: PK2 | Tasa_Mensual (% mensual, 1.2 = 1,2 %) | Monto | Fecha_Vencimiento."""
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in ("PK2", "Tasa_Mensual", "Monto", "Fecha_Vencimiento") if c not in df.columns]:
        raise ValueError(f"FACTURAS sin columnas {faltan}")
    df["PK2"] = limpiar_txt(df["PK2"])
    df["Tasa_Mensual"] = pd.to_numeric(df["Tasa_Mensual"], errors="coerce")
    df["Monto"] = pd.to_numeric(df["Monto"], errors="coerce")
    df["Fecha_Vencimiento"] = pd.to_datetime(df["Fecha_Vencimiento"], errors="coerce")
    if (df["Tasa_Mensual"].abs() > 5).any():
        raise ValueError("FACTURAS: Tasa_Mensual debe venir en % mensual (ej. 1.2); hay valores > 5")
    return df[df["PK2"].ne("")].drop_duplicates("PK2").reset_index(drop=True)
