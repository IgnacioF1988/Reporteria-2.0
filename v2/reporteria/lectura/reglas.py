"""REGLAS.xlsx: el único archivo de parametrización del operador. Se valida completo al leer."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import POLITICAS_HEDGE
from ..modelo import CRITERIOS, ESTADOS_DEF, TRATAMIENTOS, limpiar_txt, validar_decimal

HOJAS = ("fondos", "buckets", "clasificacion", "cajas", "defaulteados", "overrides_valor", "overrides_atributo",
         "alertas", "parametros")
FIELDS_OVERRIDE = ("Hedge_Currency", "Indice", "Bucket", "Risk_Currency", "Risk_Country")


@dataclass(frozen=True)
class Reglas:
    fondos: pd.DataFrame
    buckets: pd.DataFrame
    clasificacion: pd.DataFrame
    cajas: pd.DataFrame
    defaulteados: pd.DataFrame
    overrides_valor: pd.DataFrame
    overrides_atributo: pd.DataFrame
    alertas: pd.DataFrame
    parametros: dict


def _cols(df: pd.DataFrame, hoja: str, req: tuple[str, ...]) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if faltan := [c for c in req if c not in df.columns]:
        raise ValueError(f"REGLAS/{hoja} sin columnas {faltan}")
    return df


def _id_fund(df: pd.DataFrame, hoja: str, validos: set[int] | None) -> pd.Series:
    ids = pd.to_numeric(df["ID_Fund"], errors="coerce") if "ID_Fund" in df.columns else pd.Series(float("nan"), index=df.index)
    if validos is not None:
        malos = ids.dropna()[~ids.dropna().isin(list(validos))]
        if len(malos):
            raise ValueError(f"REGLAS/{hoja}: ID_Fund desconocido {sorted(set(malos.astype(int)))} (no está en BD_FUNDS ni en el CUBO)")
    return ids


def _enum(df: pd.DataFrame, col: str, valores: tuple, hoja: str) -> pd.Series:
    s = limpiar_txt(df[col]).str.upper()
    if malos := sorted(set(s) - set(valores)):
        raise ValueError(f"REGLAS/{hoja}: {col} inválido {malos}; válidos {list(valores)}")
    return s


def _fechas(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("Fecha_Desde", "Fecha_Fin"):
        df[c] = pd.to_datetime(df[c], errors="coerce") if c in df.columns else pd.NaT
    return df


def _fondos(df, validos):
    df = _cols(df, "fondos", ("ID_Fund", "Politica_Hedge"))
    df = df[pd.to_numeric(df["ID_Fund"], errors="coerce").notna()]
    df["ID_Fund"] = _id_fund(df, "fondos", validos).astype(int)
    df["Politica_Hedge"] = _enum(df, "Politica_Hedge", POLITICAS_HEDGE, "fondos")
    return df.drop_duplicates("ID_Fund").reset_index(drop=True)


def _buckets(df):
    df = _cols(df, "buckets", ("Bucket", "Tratamiento"))
    df["Bucket"] = limpiar_txt(df["Bucket"])
    df = df[df["Bucket"].ne("")]
    df["Tratamiento"] = _enum(df, "Tratamiento", TRATAMIENTOS, "buckets")
    df["Orden"] = pd.to_numeric(df["Orden"], errors="coerce") if "Orden" in df.columns else range(1, len(df) + 1)
    return df.drop_duplicates("Bucket").reset_index(drop=True)


def _clasificacion(df, validos, buckets_validos):
    df = _cols(df, "clasificacion", ("ID", "Criterio", "Valor", "Bucket"))
    df = df[limpiar_txt(df["Criterio"]).ne("")]
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    if df["ID"].isna().any() or df["ID"].duplicated().any():
        raise ValueError("REGLAS/clasificacion: la columna ID debe ser numérica y única")
    df["ID"] = df["ID"].astype(int)
    df["ID_Fund"] = _id_fund(df, "clasificacion", validos)
    df["Criterio"] = limpiar_txt(df["Criterio"])
    if malos := sorted(set(df["Criterio"]) - set(CRITERIOS)):
        raise ValueError(f"REGLAS/clasificacion: Criterio inválido {malos}; válidos {list(CRITERIOS)}")
    df["Valor"] = limpiar_txt(df["Valor"])
    for v in df.loc[df["Criterio"].eq("Nombre_Regex"), "Valor"]:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"REGLAS/clasificacion: regex inválida '{v}': {e}") from e
    df["Bucket"] = limpiar_txt(df["Bucket"])
    if malos := sorted(set(df["Bucket"]) - buckets_validos - {""}):
        raise ValueError(f"REGLAS/clasificacion: Bucket {malos} no existe en la hoja buckets")
    df["Tratamiento"] = limpiar_txt(df["Tratamiento"]).str.upper() if "Tratamiento" in df.columns else ""
    if malos := sorted(set(df["Tratamiento"]) - set(TRATAMIENTOS) - {""}):
        raise ValueError(f"REGLAS/clasificacion: Tratamiento inválido {malos}; válidos {list(TRATAMIENTOS)}")
    if (df["Bucket"].eq("") & df["Tratamiento"].eq("")).any():
        raise ValueError("REGLAS/clasificacion: cada regla debe fijar Bucket, Tratamiento o ambos")
    df["Comentario"] = limpiar_txt(df["Comentario"]) if "Comentario" in df.columns else ""
    return df.reset_index(drop=True)


def _cajas(df, validos):
    df = _cols(df, "cajas", ("PK2", "Indice_Referencia", "Spread_Anual", "Dias"))
    df["PK2"] = limpiar_txt(df["PK2"])
    df = df[df["PK2"].ne("")]
    df["ID_Fund"] = _id_fund(df, "cajas", validos)
    df["Indice_Referencia"] = limpiar_txt(df["Indice_Referencia"])
    df["Spread_Anual"] = pd.to_numeric(df["Spread_Anual"], errors="coerce")
    if (df["Spread_Anual"].abs() > 1).any():
        raise ValueError("REGLAS/cajas: Spread_Anual debe ser decimal (0.0429 = 4,29 %); hay valores > 1")
    df["Dias"] = pd.to_numeric(df["Dias"], errors="coerce")
    return df.reset_index(drop=True)


def _defaulteados(df, validos):
    df = _cols(df, "defaulteados", ("ID_Instrumento", "Estado"))
    df = df[pd.to_numeric(df["ID_Instrumento"], errors="coerce").notna()].copy()
    df["ID_Instrumento"] = pd.to_numeric(df["ID_Instrumento"]).astype(int)
    df["ID_Fund"] = _id_fund(df, "defaulteados", validos)
    df["Estado"] = _enum(df, "Estado", ESTADOS_DEF, "defaulteados")
    return _fechas(df).reset_index(drop=True)


def _overrides_valor(df, validos):
    df = _cols(df, "overrides_valor", ("ID_Instrumento", "SubID_Instrumento", "Yield", "Duration"))
    df = df[pd.to_numeric(df["ID_Instrumento"], errors="coerce").notna()].copy()
    df["ID_Instrumento"] = pd.to_numeric(df["ID_Instrumento"]).astype(int)
    df["SubID_Instrumento"] = pd.to_numeric(df["SubID_Instrumento"], errors="coerce")
    df["ID_Fund"] = _id_fund(df, "overrides_valor", validos)
    validar_decimal(df, "Yield")
    df["Yield"] = pd.to_numeric(df["Yield"], errors="coerce")
    df["Duration"] = pd.to_numeric(df["Duration"], errors="coerce")
    return _fechas(df).reset_index(drop=True)


def _overrides_atributo(df, validos):
    df = _cols(df, "overrides_atributo", ("ID_Instrumento", "SubID_Instrumento", "Field", "Value"))
    df = df[pd.to_numeric(df["ID_Instrumento"], errors="coerce").notna()].copy()
    df["ID_Instrumento"] = pd.to_numeric(df["ID_Instrumento"]).astype(int)
    df["SubID_Instrumento"] = pd.to_numeric(df["SubID_Instrumento"], errors="coerce")
    df["ID_Fund"] = _id_fund(df, "overrides_atributo", validos)
    df["Field"] = limpiar_txt(df["Field"])
    if malos := sorted(set(df["Field"]) - set(FIELDS_OVERRIDE)):
        raise ValueError(f"REGLAS/overrides_atributo: Field inválido {malos}; válidos {list(FIELDS_OVERRIDE)}")
    df["Value"] = limpiar_txt(df["Value"])
    return _fechas(df).reset_index(drop=True)


OPERADORES = (">=", "<=", ">", "<", "==", "!=", "abs>=", "abs>", "in", "es_nulo", "no_nulo", "es_verdadero", "es_falso")
SEVERIDADES = ("CRITICA", "ALTA", "MEDIA", "INFO")
AMBITOS = ("POSICION", "FONDO", "CORRIDA")


def _si_no(df, col, hoja, default="NO"):
    s = limpiar_txt(df[col]).str.upper().replace({"": default, "SÍ": "SI", "YES": "SI", "TRUE": "SI", "FALSE": "NO"}) if col in df.columns \
        else pd.Series(default, index=df.index)
    if malos := sorted(set(s) - {"SI", "NO"}):
        raise ValueError(f"REGLAS/{hoja}: {col} inválido {malos}; válidos ['SI', 'NO']")
    return s.eq("SI")


def _alertas(df, validos):
    """Reglas del motor: Campo vacío = ajuste (severidad / Activa) de una alerta estructural del pipeline con ese Nombre."""
    df = _cols(df, "alertas", ("Nombre", "Campo", "Operador", "Umbral", "Severidad"))
    df = df[limpiar_txt(df["Nombre"]).ne("")].copy()
    for c in ("Nombre", "Campo", "Operador", "Descripcion"):
        df[c] = limpiar_txt(df[c]) if c in df.columns else ""
    df["ID_Fund"] = _id_fund(df, "alertas", validos)
    df["Severidad"] = _enum(df, "Severidad", SEVERIDADES, "alertas")
    df["Ambito"] = limpiar_txt(df["Ambito"]).str.upper().replace({"": "POSICION"}) if "Ambito" in df.columns else "POSICION"
    if malos := sorted(set(df["Ambito"]) - set(AMBITOS)):
        raise ValueError(f"REGLAS/alertas: Ambito inválido {malos}; válidos {list(AMBITOS)}")
    df["Activa"] = _si_no(df, "Activa", "alertas", "SI")
    df["Requiere_Anterior"] = _si_no(df, "Requiere_Anterior", "alertas", "NO")
    df["Bloquea_Publicacion"] = _si_no(df, "Bloquea_Publicacion", "alertas", "NO")     # H10: deja PROVISORIO al fondo-día (default NO)
    # "==" escrito en Excel se vuelve fórmula; se aceptan alias en texto
    df["Operador"] = df["Operador"].str.lower().replace({"igual": "==", "eq": "==", "=": "==", "distinto": "!=", "ne": "!=", "abs>=": "abs>=", "abs>": "abs>"})
    con_campo = df["Campo"].ne("")
    if malos := sorted(set(df.loc[con_campo, "Operador"]) - set(OPERADORES)):
        raise ValueError(f"REGLAS/alertas: Operador inválido {malos}; válidos {list(OPERADORES)}")
    sin_umbral = con_campo & ~df["Operador"].isin(("es_nulo", "no_nulo", "es_verdadero", "es_falso")) & limpiar_txt(df["Umbral"]).eq("")
    if sin_umbral.any():
        raise ValueError(f"REGLAS/alertas: reglas sin Umbral {df.loc[sin_umbral, 'Nombre'].tolist()}")
    return df.reset_index(drop=True)


def _parametros(df) -> dict:
    df = _cols(df, "parametros", ("Clave", "Valor"))
    out = {}
    for _, r in df.iterrows():
        k = str(r["Clave"]).strip()
        if not k or k == "nan":
            continue
        v = pd.to_numeric(pd.Series([r["Valor"]]), errors="coerce").iloc[0]
        out[k] = float(v) if pd.notna(v) else str(r["Valor"]).strip()
    return out


def leer_reglas(path: Path, fondos_validos: set[int] | None = None) -> Reglas:
    xl = pd.ExcelFile(path)
    if faltan := [h for h in HOJAS if h not in xl.sheet_names]:
        raise ValueError(f"REGLAS.xlsx sin hojas {faltan}; debe tener {list(HOJAS)}")
    h = {n: xl.parse(n) for n in HOJAS}
    buckets = _buckets(h["buckets"])
    return Reglas(_fondos(h["fondos"], fondos_validos), buckets,
                  _clasificacion(h["clasificacion"], fondos_validos, set(buckets["Bucket"])),
                  _cajas(h["cajas"], fondos_validos), _defaulteados(h["defaulteados"], fondos_validos),
                  _overrides_valor(h["overrides_valor"], fondos_validos),
                  _overrides_atributo(h["overrides_atributo"], fondos_validos),
                  _alertas(h["alertas"], fondos_validos), _parametros(h["parametros"]))
