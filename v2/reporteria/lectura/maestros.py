"""BD_INSTRUMENTOS y BD_FUNDS (fuentes corporativas, se leen donde están)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import limpiar_txt

COLS_INSTR = ["Risk_Currency", "Investment_Type_Code", "Issue_Type_Code", "Name_Instrumento", "ISIN",
              "Risk_Country", "CompanyName"]


def leer_bd_instrumentos(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    hoja = "BD_INSTRUMENTOS" if "BD_INSTRUMENTOS" in xl.sheet_names else xl.sheet_names[0]
    df = xl.parse(hoja)
    df.columns = [str(c).strip() for c in df.columns]
    if "PK2" not in df.columns:
        if not {"ID_Instrumento", "SubID_Instrumento"} <= set(df.columns):
            raise ValueError("BD_INSTRUMENTOS necesita PK2 o (ID_Instrumento, SubID_Instrumento)")
        df["PK2"] = (pd.to_numeric(df["ID_Instrumento"]).astype(int).astype(str) + "-"
                     + pd.to_numeric(df["SubID_Instrumento"]).astype(int).astype(str))
    df["PK2"] = limpiar_txt(df["PK2"])
    for c in COLS_INSTR:
        if c not in df.columns:
            df[c] = None
    for c in ("Risk_Currency", "Name_Instrumento", "ISIN", "Risk_Country", "CompanyName"):
        df[c] = limpiar_txt(df[c])
    df["Risk_Currency"] = df["Risk_Currency"].str.upper()
    for c in ("Investment_Type_Code", "Issue_Type_Code"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df[["PK2"] + COLS_INSTR].drop_duplicates("PK2")


def leer_bd_funds(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    short = next((c for c in df.columns if "shortname" in c.lower().replace("_", "")), None)
    base = next((c for c in df.columns if "basecurrency" in c.lower().replace("_", "")), None)
    if short is None or base is None or "ID_Fund" not in df.columns:
        raise ValueError(f"BD_FUNDS necesita ID_Fund, FundShortName y FundBaseCurrency; tiene {list(df.columns)}")
    out = df[["ID_Fund", short, base]].rename(columns={short: "FundShortName", base: "FundBaseCurrency"})
    out["ID_Fund"] = pd.to_numeric(out["ID_Fund"]).astype(int)
    out["FundShortName"] = limpiar_txt(out["FundShortName"])
    out["FundBaseCurrency"] = limpiar_txt(out["FundBaseCurrency"]).str.upper()
    return out.drop_duplicates("ID_Fund")
