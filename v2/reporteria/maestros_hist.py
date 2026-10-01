"""Maestros bitemporales: BD_INSTRUMENTOS y HOMOL como base + deltas por carga (tiempo de conocimiento) y
declaraciones de vigencia (tiempo válido).

Estado a un conocimiento = base + replay de `cambios` con carga ≤ conocimiento. Maestro as-of un cierre = ese estado,
revirtiendo las declaraciones vigentes con `vigente_desde > cierre` a su `valor_anterior` (de la más nueva a la más vieja).
Sin declaración, todo cambio es corrección retroactiva (vale para toda la historia). BAJA nunca es retroactiva: solo se
replica como estado a partir de su carga. Todo valor se compara y guarda como texto (`_celda`); los tipos se restituyen al final.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .lectura.maestros import COLS_INSTR, tipar_bd_instrumentos
from .modelo import limpiar_txt

LLAVES = {"bd_instrumentos": ["PK2"], "homol_instrumentos": ["SourceInvestment", "Source"], "homol_funds": ["Portfolio", "Source"]}
COLUMNAS = {"bd_instrumentos": ["PK2"] + COLS_INSTR, "homol_instrumentos": ["SourceInvestment", "Source", "ID_Instrumento"],
            "homol_funds": ["Portfolio", "Source", "ID_Fund"]}
TABLAS = tuple(LLAVES)
COLS_CAMBIO = ["carga", "tabla", "llave", "columna", "valor_anterior", "valor_nuevo", "tipo"]
TIPOS = ("ALTA", "CAMBIO", "BAJA", "RENOMBRE")
COLS_VIGENCIA = ["ID", "tabla", "llave", "columna", "valor", "valor_anterior", "vigente_desde", "declarado_en", "anulada_en", "comentario"]
SEP = "|"


def _celda(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA or x is pd.NaT:
        return ""
    if isinstance(x, (bool, np.bool_)):
        return str(bool(x))
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)):
        return str(int(x)) if float(x).is_integer() else repr(float(x))
    return str(x).strip()


def llave_txt(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    return df[cols].apply(lambda col: col.map(_celda)).agg(SEP.join, axis=1) if len(df) else pd.Series(dtype=object)


def normalizar_tabla(nombre: str, df: pd.DataFrame | None) -> pd.DataFrame:
    """Columnas canónicas, todo texto, una fila por llave (la última gana, como el `dict` que usa el pipeline), ordenada por llave."""
    cols = COLUMNAS[nombre]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols).astype(object)
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols].apply(lambda col: col.map(_celda)).astype(object)
    out = out.drop_duplicates(LLAVES[nombre], keep="last")
    return out.sort_values(LLAVES[nombre], kind="stable").reset_index(drop=True)


def tipar(nombre: str, df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve la tabla con los tipos que el pipeline espera (como la entregan `lectura.maestros.leer_*`)."""
    out = df.copy()
    if nombre == "bd_instrumentos":
        out = out.replace({"": None})
        return tipar_bd_instrumentos(out).reset_index(drop=True)
    col = "ID_Instrumento" if nombre == "homol_instrumentos" else "ID_Fund"
    out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=[col]).copy()
    out[col] = out[col].astype(int)
    for c in out.columns:
        if c != col:
            out[c] = limpiar_txt(out[c])
    if "Source" in out.columns:
        out["Source"] = out["Source"].str.upper()
    return out.reset_index(drop=True)


def diff_tablas(nombre: str, ant: pd.DataFrame, nuevo: pd.DataFrame, carga: str = "") -> pd.DataFrame:
    """Cambios para pasar de `ant` a `nuevo`: ALTA/BAJA (columna '*', fila completa en JSON), CAMBIO por columna,
    y RENOMBRE (bd_instrumentos: mismo ID_Instrumento sale con un SubID y entra con otro = corrección de moneda)."""
    ant, nuevo = normalizar_tabla(nombre, ant), normalizar_tabla(nombre, nuevo)
    llaves = LLAVES[nombre]
    ka, kn = llave_txt(ant, llaves), llave_txt(nuevo, llaves)
    a, n = ant.set_index(ka.to_numpy()) if len(ant) else ant, nuevo.set_index(kn.to_numpy()) if len(nuevo) else nuevo
    filas = []
    bajas = sorted(set(a.index) - set(n.index))
    altas = sorted(set(n.index) - set(a.index))
    for k in bajas:
        filas.append((carga, nombre, k, "*", json.dumps(a.loc[k].to_dict(), ensure_ascii=False), "", "BAJA"))
    for k in altas:
        filas.append((carga, nombre, k, "*", "", json.dumps(n.loc[k].to_dict(), ensure_ascii=False), "ALTA"))
    comunes = a.index.intersection(n.index)
    if len(comunes):
        x, y = a.loc[comunes], n.loc[comunes]
        for c in [c for c in COLUMNAS[nombre] if c not in llaves]:
            m = (x[c].to_numpy() != y[c].to_numpy())
            for k in comunes[m]:
                filas.append((carga, nombre, k, c, x.at[k, c], y.at[k, c], "CAMBIO"))
    if nombre == "bd_instrumentos" and bajas and altas:
        id_b = {k: a.at[k, "ID_Instrumento"] for k in bajas}
        id_a: dict[str, list[str]] = {}
        for k in altas:
            id_a.setdefault(n.at[k, "ID_Instrumento"], []).append(k)
        for k, i in id_b.items():
            if i in id_a and len(id_a[i]) == 1 and sum(1 for v in id_b.values() if v == i) == 1:
                filas.append((carga, nombre, k, "*", k, id_a[i][0], "RENOMBRE"))
    return pd.DataFrame(filas, columns=COLS_CAMBIO)


def aplicar_cambios(nombre: str, base: pd.DataFrame, cambios: pd.DataFrame, cierre: str | None = None) -> pd.DataFrame:
    """Replay en orden de carga sobre la base (texto). Devuelve tabla normalizada (texto).

    `cierre`: una BAJA registrada en una carga posterior al cierre NO se aplica (los cierres anteriores conservan la fila);
    ALTA y CAMBIO sí son retroactivos (corrección por defecto)."""
    t = normalizar_tabla(nombre, base)
    llaves = LLAVES[nombre]
    if cambios is None or cambios.empty:
        return t
    c = cambios[cambios["tabla"].eq(nombre)].sort_values("carga", kind="stable")
    if c.empty:
        return t
    filas = {k: dict(zip(t.columns, r)) for k, r in zip(llave_txt(t, llaves), t.itertuples(index=False, name=None))}
    for r in c.itertuples(index=False):
        if r.tipo == "BAJA":
            if cierre and str(r.carga)[:8] > str(cierre)[:8]:
                continue
            filas.pop(r.llave, None)
        elif r.tipo == "ALTA":
            filas[r.llave] = {k: _celda(v) for k, v in json.loads(r.valor_nuevo).items()}
        elif r.tipo == "CAMBIO":
            if r.llave in filas:
                filas[r.llave][r.columna] = _celda(r.valor_nuevo)
    out = pd.DataFrame(list(filas.values()), columns=COLUMNAS[nombre]).astype(object)
    return normalizar_tabla(nombre, out)


def filtrar_conocimiento(cambios: pd.DataFrame, conocimiento: str | None) -> pd.DataFrame:
    """Cargas con fecha (YYYYMMDD, primeros 8 caracteres del id) ≤ conocimiento."""
    if cambios is None or cambios.empty or not conocimiento:
        return cambios if cambios is not None else pd.DataFrame(columns=COLS_CAMBIO)
    return cambios[cambios["carga"].astype(str).str[:8] <= str(conocimiento)[:8]]


def estado(nombre: str, base: pd.DataFrame, cambios: pd.DataFrame, conocimiento: str | None = None) -> pd.DataFrame:
    return aplicar_cambios(nombre, base, filtrar_conocimiento(cambios, conocimiento))


def vigencias_vacias() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS_VIGENCIA)


def normalizar_vigencias(v: pd.DataFrame | None) -> pd.DataFrame:
    if v is None or v.empty:
        return vigencias_vacias()
    out = v.copy()
    for c in COLS_VIGENCIA:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS_VIGENCIA]
    out["ID"] = pd.to_numeric(out["ID"], errors="coerce").fillna(0).astype(int)
    for c in COLS_VIGENCIA[1:]:
        out[c] = out[c].map(_celda)
    return out.reset_index(drop=True)


def vigentes(vig: pd.DataFrame, conocimiento: str | None = None) -> pd.DataFrame:
    v = normalizar_vigencias(vig)
    v = v[v["anulada_en"].eq("")]
    if conocimiento:
        v = v[v["declarado_en"].str[:8] <= str(conocimiento)[:8]]
    return v


def maestro_asof(nombre: str, base: pd.DataFrame, cambios: pd.DataFrame, vig: pd.DataFrame | None, cierre: str,
                 conocimiento: str | None = None) -> pd.DataFrame:
    """Tabla tipada como era válida en `cierre` según lo conocido a `conocimiento`."""
    t = aplicar_cambios(nombre, base, filtrar_conocimiento(cambios, conocimiento), cierre)
    v = vigentes(vig, conocimiento)
    v = v[v["tabla"].eq(nombre) & (v["vigente_desde"].str[:8] > str(cierre)[:8])].sort_values("vigente_desde", ascending=False, kind="stable")
    if len(v) and len(t):
        idx = pd.Series(range(len(t)), index=llave_txt(t, LLAVES[nombre]).to_numpy())
        for r in v.itertuples(index=False):
            if r.llave in idx.index and r.columna in t.columns:
                t.iat[int(idx[r.llave]), t.columns.get_loc(r.columna)] = _celda(r.valor_anterior)
    return tipar(nombre, t)


def declarar(vig: pd.DataFrame | None, cambios: pd.DataFrame | None, tabla: str, llave: str, columna: str, valor: str, desde: str,
             valor_anterior: str | None = None, comentario: str = "", declarado_en: str = "") -> pd.DataFrame:
    """Nueva declaración: el valor rige desde el cierre `desde`; antes vale `valor_anterior` (se toma de `cambios` si no se da)."""
    if tabla not in LLAVES:
        raise ValueError(f"tabla desconocida {tabla}; válidas {list(LLAVES)}")
    if columna not in COLUMNAS[tabla] or columna in LLAVES[tabla]:
        raise ValueError(f"columna {columna} no es un atributo de {tabla}")
    v = normalizar_vigencias(vig)
    if valor_anterior is None:
        c = cambios[cambios["tabla"].eq(tabla) & cambios["llave"].eq(llave) & cambios["columna"].eq(columna) & cambios["tipo"].eq("CAMBIO")
                    & cambios["valor_nuevo"].map(_celda).eq(_celda(valor))] if cambios is not None and len(cambios) else pd.DataFrame()
        if c.empty:
            raise ValueError(f"no hay un cambio registrado de {tabla}[{llave}].{columna} → {valor}: indique --valor-anterior")
        valor_anterior = c.sort_values("carga").iloc[0]["valor_anterior"]
    nuevo = dict(ID=int(v["ID"].max()) + 1 if len(v) else 1, tabla=tabla, llave=llave, columna=columna, valor=_celda(valor),
                 valor_anterior=_celda(valor_anterior), vigente_desde=str(desde)[:8], declarado_en=declarado_en or pd.Timestamp.now().strftime("%Y%m%d_%H%M%S"),
                 anulada_en="", comentario=comentario)
    return pd.concat([v, pd.DataFrame([nuevo])], ignore_index=True)


def anular(vig: pd.DataFrame, id_: int, cuando: str = "") -> pd.DataFrame:
    v = normalizar_vigencias(vig)
    m = v["ID"].eq(int(id_))
    if not m.any():
        raise ValueError(f"declaración {id_} no existe")
    if (v.loc[m, "anulada_en"] != "").any():
        raise ValueError(f"declaración {id_} ya estaba anulada")
    v.loc[m, "anulada_en"] = cuando or pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    return v


def resumen_cambios(cambios: pd.DataFrame) -> dict[str, int]:
    if cambios is None or cambios.empty:
        return {}
    return {f"{t}:{k}": int(n) for (t, k), n in cambios.groupby(["tabla", "tipo"]).size().items()}
