"""Lectura del CUBO completo: todas las posiciones, todos los tipos, ambos BalanceSheet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import COLS_VALOR, limpiar_txt

REQUERIDAS = ["PK2", "ID_Fund", "BalanceSheet", "TotalMVal"]


def leer_cubo(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    faltan = [c for c in REQUERIDAS if c not in df.columns]
    if faltan:
        raise ValueError(f"CUBO sin columnas {faltan}. Columnas: {list(df.columns)}")
    df["PK2"] = limpiar_txt(df["PK2"])
    df["ID_Fund"] = pd.to_numeric(df["ID_Fund"], errors="coerce").astype("Int64")
    df["BalanceSheet"] = limpiar_txt(df["BalanceSheet"]).str.capitalize()
    df["Source"] = limpiar_txt(df["Source"]) if "Source" in df.columns else ""
    for c in COLS_VALOR:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else float("nan")
    df = df[df["PK2"].ne("") & df["ID_Fund"].notna()].copy()
    df["ID_Fund"] = df["ID_Fund"].astype(int)
    return df
