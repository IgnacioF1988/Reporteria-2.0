"""Maestros y dimensiones corporativas (se leen donde están). Una función pura por archivo."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import CODIGOS, limpiar_txt

COLS_INSTR = ["ID_Instrumento", "SubID_Instrumento", "Name_Instrumento", "ISIN", "CompanyName", "Risk_Country",
              "Risk_Currency", "Issue_Currency", "Yield_Type", "Emision_nacional"] + CODIGOS


def _hoja(path: Path, preferida: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    df = xl.parse(preferida if preferida in xl.sheet_names else xl.sheet_names[0])
    df.columns = [str(c).strip() for c in df.columns]
    return df


def leer_bd_instrumentos(path: Path) -> pd.DataFrame:
    df = _hoja(path, "BD_INSTRUMENTOS")
    if not {"ID_Instrumento", "SubID_Instrumento"} <= set(df.columns):
        raise ValueError("BD_INSTRUMENTOS necesita ID_Instrumento y SubID_Instrumento")
    for c in COLS_INSTR:
        if c not in df.columns:
            df[c] = None
    df = df.dropna(subset=["ID_Instrumento", "SubID_Instrumento"]).copy()
    df["ID_Instrumento"] = df["ID_Instrumento"].astype(int)
    df["SubID_Instrumento"] = df["SubID_Instrumento"].astype(int)
    df["PK2"] = df["ID_Instrumento"].astype(str) + "-" + df["SubID_Instrumento"].astype(str)
    for c in ("Name_Instrumento", "ISIN", "CompanyName", "Risk_Country", "Risk_Currency", "Issue_Currency"):
        df[c] = limpiar_txt(df[c])
    df["Risk_Currency"] = df["Risk_Currency"].str.upper()
    for c in CODIGOS + ["Yield_Type", "Emision_nacional"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df[["PK2"] + COLS_INSTR].drop_duplicates("PK2").reset_index(drop=True)


def leer_bd_funds(path: Path) -> pd.DataFrame:
    df = _hoja(path, "BD_FUNDS")
    short = next((c for c in df.columns if "shortname" in c.lower().replace("_", "")), None)
    base = next((c for c in df.columns if "basecurrency" in c.lower().replace("_", "")), None)
    if short is None or base is None or "ID_Fund" not in df.columns:
        raise ValueError(f"BD_FUNDS necesita ID_Fund, FundShortName y FundBaseCurrency; tiene {list(df.columns)}")
    out = df[["ID_Fund", short, base]].rename(columns={short: "FundShortName", base: "FundBaseCurrency"})
    out = out.dropna(subset=["ID_Fund"]).copy()
    out["ID_Fund"] = out["ID_Fund"].astype(int)
    out["FundShortName"] = limpiar_txt(out["FundShortName"])
    out["FundBaseCurrency"] = limpiar_txt(out["FundBaseCurrency"]).str.upper()
    return out.drop_duplicates("ID_Fund").reset_index(drop=True)


def leer_bd_balance_sheet(path: Path) -> pd.DataFrame:
    """BalSheetKey → Bucket (Investment_Type_CarteraFI) y Ficha_FI. La variante _MRCLP se ignora: va a REGLAS."""
    df = _hoja(path, "BalSheet")
    req = {"BalSheetKey", "Investment_Type_CarteraFI"}
    if not req <= set(df.columns):
        raise ValueError(f"BD_BalanceSheet necesita {req}; tiene {list(df.columns)}")
    out = pd.DataFrame({"BalSheetKey": limpiar_txt(df["BalSheetKey"]),
                        "Bucket": limpiar_txt(df["Investment_Type_CarteraFI"]),
                        "Ficha_FI": limpiar_txt(df["Investment_Type_Ficha_FI"]) if "Investment_Type_Ficha_FI" in df.columns else ""})
    return out[out["BalSheetKey"].ne("")].drop_duplicates("BalSheetKey").reset_index(drop=True)


def leer_bd_monedas(path: Path) -> pd.DataFrame:
    df = _hoja(path, "Monedas")
    out = pd.DataFrame({"id_CURR": pd.to_numeric(df["id_CURR"], errors="coerce"), "Code": limpiar_txt(df["Code"]).str.upper(),
                        "Supramoneda": limpiar_txt(df["Code_Supramoneda"]).str.upper() if "Code_Supramoneda" in df.columns else ""})
    return out.dropna(subset=["id_CURR"]).astype({"id_CURR": int}).drop_duplicates("id_CURR").reset_index(drop=True)


def leer_yld_flag(path: Path) -> dict[str, str]:
    df = _hoja(path, "BD_YLD_FLAG")
    return dict(zip(limpiar_txt(df["CalcType_final"]), limpiar_txt(df["CalcType_exportable"])))


def leer_defaulted(path: Path) -> pd.DataFrame:
    df = _hoja(path, "DEFAULTED")
    out = pd.DataFrame({"ID_Instrumento": pd.to_numeric(df["ID_Instrumento"], errors="coerce"),
                        "Fecha_Desde": pd.to_datetime(df.get("Fecha_Desde"), errors="coerce"),
                        "Fecha_Fin": pd.to_datetime(df.get("Fecha_Fin"), errors="coerce")})
    return out.dropna(subset=["ID_Instrumento"]).astype({"ID_Instrumento": int}).reset_index(drop=True)


def leer_homol(path: Path, source: str | None = None) -> pd.DataFrame:
    df = _hoja(path, "Hoja1")
    out = pd.DataFrame({"SourceInvestment": limpiar_txt(df["SourceInvestment"]),
                        "ID_Instrumento": pd.to_numeric(df["ID_Instrumento"], errors="coerce"),
                        "Source": limpiar_txt(df["Source"]).str.upper()})
    out = out.dropna(subset=["ID_Instrumento"]).astype({"ID_Instrumento": int})
    return out[out["Source"].eq(source.upper())] if source else out


def leer_homol_funds(path: Path) -> pd.DataFrame:
    df = _hoja(path, "HOMOL_FUNDS")
    out = pd.DataFrame({"Portfolio": limpiar_txt(df["Portfolio"]), "ID_Fund": pd.to_numeric(df["ID_Fund"], errors="coerce"),
                        "Source": limpiar_txt(df["Source"]).str.upper()})
    return out.dropna(subset=["ID_Fund"]).astype({"ID_Fund": int}).reset_index(drop=True)


_FX_RENOMBRES = {"FUND_TYPE": "Fund_Type_Code", "BANK_DEBT_TYPE": "Bank_Debt_Type_Code", "ISSUE TYPE": "Issue_Type_Code",
                 "ISSUER TYPE": "Issuer_Type_Code", "Emision_Nacional": "Emision_nacional"}


def leer_fx_exposure(path: Path) -> pd.DataFrame:
    """Tabla por fondo: columnas de código (nombres canónicos) + FX_Exposure. Las columnas Key_* se descartan."""
    df = _hoja(path, "")
    df = df.rename(columns=_FX_RENOMBRES)
    etiqueta = next((c for c in df.columns if c.startswith("Investment_Type_FXExp")), None)
    if etiqueta is None:
        raise ValueError(f"{path.name}: falta la columna Investment_Type_FXExp*")
    cols = [c for c in df.columns if c in CODIGOS + ["Emision_nacional"]]
    out = df[cols].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    out["FX_Exposure"] = limpiar_txt(df[etiqueta]).str.replace("\n", " ", regex=False)
    return out.drop_duplicates(cols).reset_index(drop=True)


def fondo_de_fx_exposure(path: Path, bd_funds: pd.DataFrame) -> int | None:
    """BD_FX_Exposure_MRCLP.xlsx → ID_Fund del FundShortName 'MRCLP'."""
    sufijo = path.stem.replace("BD_FX_Exposure_", "").strip().upper()
    m = bd_funds[bd_funds["FundShortName"].str.upper() == sufijo]
    return int(m["ID_Fund"].iloc[0]) if len(m) else None
