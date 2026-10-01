"""Esquema canónico y utilidades. Todo yield es DECIMAL (0.05 = 5 %)."""
from __future__ import annotations

import numpy as np
import pandas as pd

CODIGOS = ["Investment_Type_Code", "Issuer_Type_Code", "Issue_Type_Code", "Coupon_Type_Code", "Rank_Code",
           "Cash_Type_Code", "Bank_Debt_Type_Code", "Fund_Type_Code"]
TRATAMIENTOS = ("CASCADA", "CAJA", "FACTURA", "CERO", "EXCLUIR")
ESTADOS = ("RESUELTO", "FALTANTE", "PENDIENTE_TERMINAL", "EXCLUIDO")
CRITERIOS = ("PK2", "ID_Instrumento", "BalSheetKey", "Nombre_Regex", "Issue_Type_Code", "Investment_Type_Code")
ESTADOS_DEF = ("DEF", "PROPDEF")
CALC_TYPE_DEF = {"DEF": "DEF", "PROPDEF": "PROP NP"}
YIELD_DEF, DURATION_DEF = 0.0, 0.5

COLS_VALOR = ["LocalPrice", "Qty", "OriginalFace", "Factor", "AI", "MVBook", "TotalMVal"]
COLS_CANDIDATO = ["Pos_ID", "PK2", "Fuente", "Yield", "Duration", "Yield_Moneda", "Origen",
                  "Tupla_Completa", "Valido", "Motivo_Descarte", "Detalle"]
COLS_ALERTA = ["Nombre", "Severidad", "Ambito", "Pos_ID", "ID_Fund", "PK2", "Name_Instrumento", "Valor", "Detalle"]


def limpiar_txt(s: pd.Series) -> pd.Series:
    """Texto sin espacios, con '' para cualquier forma de nulo. Robusto a StringDtype de pandas 3."""
    out = s.astype("object").where(s.notna(), "")
    out = out.map(lambda x: str(x).strip())
    return out.replace({"nan": "", "None": "", "NaN": "", "<NA>": "", "NaT": ""}).astype("object")


def pos_id(df: pd.DataFrame) -> pd.Series:
    return (df["ID_Fund"].astype(int).astype(str) + "|" + limpiar_txt(df["PK2"]) + "|" + limpiar_txt(df["BalanceSheet"]))


def bal_sheet_key(df: pd.DataFrame) -> pd.Series:
    """ASSET_TYPE + los 8 códigos concatenados SIN relleno (así está construida BD_BalanceSheet)."""
    codes = df[CODIGOS].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int).astype(str)
    return limpiar_txt(df["BalanceSheet"]) + codes.agg("".join, axis=1)


def vigente(df: pd.DataFrame, settle: pd.Timestamp) -> pd.Series:
    """True si settle ∈ [Fecha_Desde, Fecha_Fin]; extremos vacíos = abiertos."""
    desde = pd.to_datetime(df.get("Fecha_Desde"), errors="coerce") if "Fecha_Desde" in df.columns else pd.Series(pd.NaT, index=df.index)
    hasta = pd.to_datetime(df.get("Fecha_Fin"), errors="coerce") if "Fecha_Fin" in df.columns else pd.Series(pd.NaT, index=df.index)
    return (desde.isna() | (desde <= settle)) & (hasta.isna() | (hasta >= settle))


def validar_decimal(df: pd.DataFrame, col: str, tope: float = 1.5) -> None:
    v = pd.to_numeric(df[col], errors="coerce").abs()
    if (v > tope).any():
        raise ValueError(f"'{col}' parece estar en porcentaje (hay valores > {tope}); debe ser decimal (0.05 = 5 %)")


def candidatos_vacios() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS_CANDIDATO)


def candidato(pos: pd.Series, fuente: str, yld=np.nan, dur=np.nan, moneda: str = "", origen: str = "",
              valido: bool | None = None, motivo: str = "", detalle: str = "") -> dict:
    completo = pd.notna(yld) and pd.notna(dur)
    if valido is None:
        valido = completo
    return dict(Pos_ID=pos["Pos_ID"], PK2=pos["PK2"], Fuente=fuente, Yield=yld, Duration=dur, Yield_Moneda=moneda,
                Origen=origen, Tupla_Completa=completo, Valido=bool(valido), Motivo_Descarte=motivo if not valido else "",
                Detalle=detalle)
