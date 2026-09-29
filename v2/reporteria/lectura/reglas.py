"""REGLAS.xlsx: el único archivo de parametrización del operador. Se valida completo al leer."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import FONDOS
from ..modelo import CRITERIOS, ESTADOS_DEF, TRATAMIENTOS, limpiar_txt, validar_decimal

HOJAS = ("clasificacion", "defaulteados", "overrides_valor", "overrides_atributo", "alertas", "parametros")
ATRIBUTOS_OVERRIDE = ("Hedge_Currency", "Indice", "Estado_DEF", "Bucket", "Tratamiento")


@dataclass(frozen=True)
class Reglas:
    clasificacion: pd.DataFrame
    defaulteados: pd.DataFrame
    overrides_valor: pd.DataFrame
    overrides_atributo: pd.DataFrame
    alertas: pd.DataFrame
    parametros: dict


def _id_fund(df: pd.DataFrame, hoja: str) -> pd.Series:
    ids = pd.to_numeric(df["ID_Fund"], errors="coerce") if "ID_Fund" in df.columns else pd.Series(float("nan"), index=df.index)
    malos = ids.dropna()[~ids.dropna().isin(list(FONDOS))]
    if len(malos):
        raise ValueError(f"REGLAS/{hoja}: ID_Fund desconocido {sorted(set(malos.astype(int)))}; válidos {sorted(FONDOS)}")
    return ids


def _enum(df: pd.DataFrame, col: str, valores: tuple, hoja: str, permitir_vacio=False) -> pd.Series:
    s = limpiar_txt(df[col]).str.upper()
    malos = sorted(set(s[~s.isin(valores) & ~(permitir_vacio & s.eq(""))]))
    if malos:
        raise ValueError(f"REGLAS/{hoja}: {col} inválido {malos}; válidos {list(valores)}")
    return s


def _clasificacion(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    req = ["ID", "Criterio", "Valor", "Bucket", "Tratamiento", "Yield", "Duration"]
    if faltan := [c for c in req if c not in df.columns]:
        raise ValueError(f"REGLAS/clasificacion sin columnas {faltan}")
    df = df[limpiar_txt(df["Criterio"]).ne("")]
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    if df["ID"].isna().any() or df["ID"].duplicated().any():
        raise ValueError("REGLAS/clasificacion: la columna ID debe ser numérica y única")
    df["ID"] = df["ID"].astype(int)
    df["ID_Fund"] = _id_fund(df, "clasificacion")
    df["Criterio"] = limpiar_txt(df["Criterio"])
    if malos := sorted(set(df["Criterio"]) - set(CRITERIOS)):
        raise ValueError(f"REGLAS/clasificacion: Criterio inválido {malos}; válidos {list(CRITERIOS)}")
    df["Valor"] = limpiar_txt(df["Valor"])
    for v in df.loc[df["Criterio"].eq("Nombre_Regex"), "Valor"]:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"REGLAS/clasificacion: regex inválida '{v}': {e}") from e
    df["Bucket"] = limpiar_txt(df["Bucket"]).str.upper()
    df["Tratamiento"] = _enum(df, "Tratamiento", TRATAMIENTOS, "clasificacion")
    validar_decimal(df, "Yield")
    df["Yield"] = pd.to_numeric(df["Yield"], errors="coerce")
    df["Duration"] = pd.to_numeric(df["Duration"], errors="coerce")
    df["Comentario"] = limpiar_txt(df["Comentario"]) if "Comentario" in df.columns else ""
    return df.reset_index(drop=True)


def _defaulteados(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in ("PK2", "Estado") if c not in df.columns]:
        raise ValueError(f"REGLAS/defaulteados sin columnas {faltan}")
    df["PK2"] = limpiar_txt(df["PK2"])
    df = df[df["PK2"].ne("")]
    df["ID_Fund"] = _id_fund(df, "defaulteados")
    df["Estado"] = _enum(df, "Estado", ESTADOS_DEF, "defaulteados")
    return df.reset_index(drop=True)


def _overrides_valor(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in ("PK2", "Yield", "Duration") if c not in df.columns]:
        raise ValueError(f"REGLAS/overrides_valor sin columnas {faltan}")
    df["PK2"] = limpiar_txt(df["PK2"])
    df = df[df["PK2"].ne("")]
    df["ID_Fund"] = _id_fund(df, "overrides_valor")
    validar_decimal(df, "Yield")
    df["Yield"] = pd.to_numeric(df["Yield"], errors="coerce")
    df["Duration"] = pd.to_numeric(df["Duration"], errors="coerce")
    for c in ("Vigente_Desde", "Vigente_Hasta"):
        df[c] = pd.to_datetime(df[c], errors="coerce") if c in df.columns else pd.NaT
    return df.reset_index(drop=True)


def _overrides_atributo(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in ("PK2", "Atributo", "Valor") if c not in df.columns]:
        raise ValueError(f"REGLAS/overrides_atributo sin columnas {faltan}")
    df["PK2"] = limpiar_txt(df["PK2"])
    df = df[df["PK2"].ne("")]
    df["ID_Fund"] = _id_fund(df, "overrides_atributo")
    df["Atributo"] = limpiar_txt(df["Atributo"])
    if malos := sorted(set(df["Atributo"]) - set(ATRIBUTOS_OVERRIDE)):
        raise ValueError(f"REGLAS/overrides_atributo: Atributo inválido {malos}; válidos {list(ATRIBUTOS_OVERRIDE)}")
    df["Valor"] = limpiar_txt(df["Valor"])
    return df.reset_index(drop=True)


def _alertas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "Nombre" in df.columns:
        df = df[limpiar_txt(df["Nombre"]).ne("")]
        df["ID_Fund"] = _id_fund(df, "alertas")
    return df.reset_index(drop=True)


def _parametros(df: pd.DataFrame) -> dict:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in ("Clave", "Valor") if c not in df.columns]:
        raise ValueError(f"REGLAS/parametros sin columnas {faltan}")
    out = {}
    for _, r in df.iterrows():
        k = str(r["Clave"]).strip()
        if not k or k == "nan":
            continue
        v = pd.to_numeric(pd.Series([r["Valor"]]), errors="coerce").iloc[0]
        out[k] = float(v) if pd.notna(v) else str(r["Valor"]).strip()
    return out


def leer_reglas(path: Path) -> Reglas:
    xl = pd.ExcelFile(path)
    if faltan := [h for h in HOJAS if h not in xl.sheet_names]:
        raise ValueError(f"REGLAS.xlsx sin hojas {faltan}; debe tener {list(HOJAS)}")
    h = {n: xl.parse(n) for n in HOJAS}
    return Reglas(_clasificacion(h["clasificacion"]), _defaulteados(h["defaulteados"]),
                  _overrides_valor(h["overrides_valor"]), _overrides_atributo(h["overrides_atributo"]),
                  _alertas(h["alertas"]), _parametros(h["parametros"]))
