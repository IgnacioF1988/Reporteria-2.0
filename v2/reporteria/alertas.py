"""Alertas estructurales (emitidas por el pipeline) y motor por reglas de REGLAS/alertas.

Una regla es `Campo Operador Umbral` sobre una columna de `posiciones` o una columna derivada (lista cerrada en
`columnas_derivadas`). `Umbral` acepta un número, texto, `param:<clave>` (REGLAS/parametros) o una lista `a;b;c` para `in`.
Reglas con el mismo Nombre: la que trae ID_Fund manda sobre la global para ese fondo (umbral distinto por fondo).
`Requiere_Anterior=SI` sin cierre previo → INACTIVA. Filas con Campo vacío ajustan severidad/actividad de una alerta
estructural del pipeline con ese Nombre.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .modelo import COLS_ALERTA, limpiar_txt

SEVERIDADES = ("CRITICA", "ALTA", "MEDIA", "INFO")


def vacias() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS_ALERTA)


def emitir(nombre: str, severidad: str, filas: pd.DataFrame | None = None, detalle: str = "",
           ambito: str = "POSICION", valor=None) -> pd.DataFrame:
    """Una alerta por fila de `filas` (o una sola de ámbito CORRIDA si no hay filas)."""
    assert severidad in SEVERIDADES, severidad
    if filas is None or filas.empty:
        return pd.DataFrame([dict(Nombre=nombre, Severidad=severidad, Ambito=ambito, Pos_ID="", ID_Fund=None,
                                  PK2="", Name_Instrumento="", Valor=valor, Detalle=detalle)], columns=COLS_ALERTA)
    out = pd.DataFrame({
        "Nombre": nombre, "Severidad": severidad, "Ambito": ambito,
        "Pos_ID": filas.get("Pos_ID", ""), "ID_Fund": filas.get("ID_Fund"), "PK2": filas.get("PK2", ""),
        "Name_Instrumento": filas.get("Name_Instrumento", ""),
        "Valor": filas[valor] if isinstance(valor, str) and valor in filas.columns else valor,
        "Detalle": filas[detalle] if isinstance(detalle, str) and detalle in filas.columns else detalle,   # columna → detalle por fila
    })
    return out[COLS_ALERTA]


def juntar(*partes: pd.DataFrame) -> pd.DataFrame:
    partes = [p for p in partes if p is not None and not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else vacias()


# ── Motor por reglas ─────────────────────────────────────────────────────────────────────────────────────────────────
COLS_ANT = ("Yield", "Duration", "LocalPrice", "AI")
DERIVADAS = ("Delta_Yield", "Delta_Yield_bps", "Delta_Precio", "Delta_Duration", "Metricas_Iguales_Ant", "Precio_Sube_Yield_Sube",
             "Es_Default", "AI_Positivo", "Default_Con_AI", "Es_Bono", "Bono_Sin_AI", "Falta_Con_MV", "Es_Nueva", "MV_Abs",
             "Cobertura_MV_Fondo", "Resuelta")


def columnas_derivadas(pos: pd.DataFrame, con_anterior: bool) -> pd.DataFrame:
    """Columnas calculadas que las reglas pueden usar además de las de `posiciones`. Las `_ant` ya vienen unidas por Pos_ID."""
    d = pd.DataFrame(index=pos.index)
    y, dur = pd.to_numeric(pos["Yield"], errors="coerce"), pd.to_numeric(pos["Duration"], errors="coerce")
    mv = pd.to_numeric(pos["TotalMVal"], errors="coerce").fillna(0)
    d["MV_Abs"] = mv.abs()
    d["Resuelta"] = pos["Estado"].eq("RESUELTO")
    d["Es_Default"] = limpiar_txt(pos["Estado_DEF"]).ne("") if "Estado_DEF" in pos.columns else False
    d["AI_Positivo"] = pd.to_numeric(pos.get("AI"), errors="coerce").fillna(0) > 0
    d["Es_Bono"] = pd.to_numeric(pos.get("Investment_Type_Code"), errors="coerce").eq(1) & pos["Tratamiento"].eq("CASCADA")
    d["Default_Con_AI"] = d["Es_Default"] & d["AI_Positivo"]
    d["Bono_Sin_AI"] = d["Es_Bono"] & ~d["Es_Default"] & d["Resuelta"] & pd.to_numeric(pos.get("AI"), errors="coerce").fillna(0).eq(0)
    d["Falta_Con_MV"] = pos["Estado"].eq("FALTANTE") & mv.ne(0)
    act = pos[pos["BalanceSheet"].eq("Asset")] if "BalanceSheet" in pos.columns else pos
    mv_f = act.groupby("ID_Fund")["TotalMVal"].sum()
    mv_res = act[act["Estado"].eq("RESUELTO")].groupby("ID_Fund")["TotalMVal"].sum()
    cob = (mv_res / mv_f).reindex(mv_f.index).fillna(0)
    d["Cobertura_MV_Fondo"] = pos["ID_Fund"].map(cob)
    if con_anterior:
        y_a, d_a = pd.to_numeric(pos["Yield_ant"], errors="coerce"), pd.to_numeric(pos["Duration_ant"], errors="coerce")
        p, p_a = pd.to_numeric(pos.get("LocalPrice"), errors="coerce"), pd.to_numeric(pos["LocalPrice_ant"], errors="coerce")
        d["Delta_Yield"] = y - y_a
        d["Delta_Yield_bps"] = d["Delta_Yield"] * 1e4
        d["Delta_Duration"] = dur - d_a
        d["Delta_Precio"] = p - p_a
        d["Metricas_Iguales_Ant"] = y_a.notna() & np.isclose(y.fillna(-9), y_a.fillna(-9), atol=1e-10) & np.isclose(dur.fillna(-9), d_a.fillna(-9), atol=1e-10) \
            & ~pos["Fuente"].isin(["CERO", "REGLA_DEF", "EXCLUIR", "OVERRIDE", ""])
        d["Precio_Sube_Yield_Sube"] = (d["Delta_Precio"] > 0) & (d["Delta_Yield"] > 0)
        d["Es_Nueva"] = ~pos["_en_anterior"].astype(bool) if "_en_anterior" in pos.columns else y_a.isna()
    else:
        for c in ("Delta_Yield", "Delta_Yield_bps", "Delta_Duration", "Delta_Precio"):
            d[c] = np.nan
        for c in ("Metricas_Iguales_Ant", "Precio_Sube_Yield_Sube", "Es_Nueva"):
            d[c] = False
    return d


def unir_anterior(pos: pd.DataFrame, ant: pd.DataFrame | None) -> pd.DataFrame:
    """Agrega Yield_ant, Duration_ant, LocalPrice_ant, AI_ant (por Pos_ID) desde la cartera_final del cierre anterior."""
    pos = pos.copy()
    for c in COLS_ANT:
        pos[f"{c}_ant"] = np.nan
    pos["_en_anterior"] = False
    if ant is None or ant.empty or "Pos_ID" not in ant.columns:
        return pos
    a = ant.drop_duplicates("Pos_ID").set_index("Pos_ID")
    for c in COLS_ANT:
        if c in a.columns:
            pos[f"{c}_ant"] = pos["Pos_ID"].map(pd.to_numeric(a[c], errors="coerce"))
    pos["_en_anterior"] = pos["Pos_ID"].isin(a.index)
    return pos


def _umbral(valor, parametros: dict):
    v = str(valor).strip()
    if v.lower().startswith("param:"):
        clave = v.split(":", 1)[1].strip()
        if clave not in parametros:
            raise ValueError(f"REGLAS/alertas: Umbral 'param:{clave}' no existe en parametros")
        return parametros[clave]
    n = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
    return float(n) if pd.notna(n) else v


def _aplicar(serie: pd.Series, op: str, umbral) -> pd.Series:
    if op in ("es_nulo", "no_nulo"):
        m = serie.isna() | serie.astype(str).str.strip().isin(["", "nan", "None", "<NA>"])
        return m if op == "es_nulo" else ~m
    if op in ("es_verdadero", "es_falso"):
        b = serie.fillna(False).astype(bool)
        return b if op == "es_verdadero" else ~b
    if op == "in":
        lista = [x.strip().upper() for x in str(umbral).split(";") if x.strip()]
        return serie.astype(str).str.strip().str.upper().isin(lista)
    if isinstance(umbral, str):
        s, u = serie.astype(str).str.strip().str.upper(), umbral.strip().upper()
        return s.eq(u) if op == "==" else s.ne(u) if op == "!=" else pd.Series(False, index=serie.index)
    x = pd.to_numeric(serie, errors="coerce")
    if op.startswith("abs"):
        x, op = x.abs(), op[3:]
    return {">=": x >= umbral, "<=": x <= umbral, ">": x > umbral, "<": x < umbral, "==": x == umbral, "!=": x != umbral}[op].fillna(False)


def evaluar(pos: pd.DataFrame, reglas: pd.DataFrame, parametros: dict, con_anterior: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(alertas, resumen). Resumen: una fila por regla con Estado ACTIVA / INACTIVA (sin cierre previo) / DESACTIVADA / SIN_CAMPO."""
    if reglas is None or reglas.empty:
        return vacias(), pd.DataFrame(columns=["ID", "Nombre", "Severidad", "Ambito", "Estado", "N", "MV_afectado", "Regla"])
    reglas = reglas[reglas["Campo"].ne("")]
    tabla = pd.concat([pos.reset_index(drop=True), columnas_derivadas(pos.reset_index(drop=True), con_anterior)], axis=1)
    out, res = [], []
    for nombre, grupo in reglas.groupby("Nombre", sort=False):
        # la fila por fondo pisa a la global para ese fondo
        globales = grupo[grupo["ID_Fund"].isna()]
        por_fondo = grupo[grupo["ID_Fund"].notna()]
        cubiertos = set(por_fondo["ID_Fund"].astype(int))
        for _, r in pd.concat([por_fondo, globales]).iterrows():
            regla = f"{r['Campo']} {r['Operador']} {r['Umbral']}" if r["Operador"] not in ("es_nulo", "no_nulo", "es_verdadero", "es_falso") else f"{r['Campo']} {r['Operador']}"
            fila = dict(ID=r.get("ID", ""), Nombre=nombre, Severidad=r["Severidad"], Ambito=r["Ambito"], N=0, MV_afectado=0.0, Regla=regla,
                        ID_Fund=int(r["ID_Fund"]) if pd.notna(r["ID_Fund"]) else None)
            if not r["Activa"]:
                res.append({**fila, "Estado": "DESACTIVADA"}); continue
            if r["Requiere_Anterior"] and not con_anterior:
                res.append({**fila, "Estado": "INACTIVA — sin cierre anterior"}); continue
            if r["Campo"] not in tabla.columns:
                res.append({**fila, "Estado": f"SIN_CAMPO — {r['Campo']} no existe"})
                out.append(emitir("ALERTA_SIN_CAMPO", "MEDIA", detalle=f"regla {nombre}: campo {r['Campo']} no existe en posiciones ni derivadas", ambito="CORRIDA"))
                continue
            alcance = tabla["ID_Fund"].eq(int(r["ID_Fund"])) if pd.notna(r["ID_Fund"]) else ~tabla["ID_Fund"].isin(cubiertos)
            m = _aplicar(tabla[r["Campo"]], r["Operador"], _umbral(r["Umbral"], parametros)) & alcance
            if r["Ambito"] == "FONDO":       # una alerta por fondo, no por posición
                m &= ~tabla.loc[m, "ID_Fund"].duplicated().reindex(tabla.index, fill_value=False)
            hits = tabla[m]
            res.append({**fila, "Estado": "ACTIVA", "N": int(m.sum()), "MV_afectado": float(pd.to_numeric(hits["TotalMVal"], errors="coerce").abs().sum()) if len(hits) else 0.0})
            if len(hits):
                a = emitir(nombre, r["Severidad"], hits, f"{r.get('Descripcion', '')} [{regla}]".strip(), ambito=r["Ambito"], valor=r["Campo"])
                if r["Ambito"] == "FONDO":
                    a[["Pos_ID", "PK2", "Name_Instrumento"]] = ""
                out.append(a)
    return juntar(*out), pd.DataFrame(res)


def ajustar_estructurales(al: pd.DataFrame, reglas: pd.DataFrame) -> pd.DataFrame:
    """Filas de REGLAS/alertas sin Campo: Activa=NO silencia la alerta estructural con ese Nombre; Severidad la re-clasifica."""
    if al is None or al.empty or reglas is None or reglas.empty:
        return al if al is not None else vacias()
    al = al.copy()
    for _, r in reglas[reglas["Campo"].eq("")].iterrows():
        m = al["Nombre"].eq(r["Nombre"])
        if pd.notna(r["ID_Fund"]):
            m &= pd.to_numeric(al["ID_Fund"], errors="coerce").eq(int(r["ID_Fund"]))
        if not r["Activa"]:
            al = al[~m]
        else:
            al.loc[m, "Severidad"] = r["Severidad"]
    return al.reset_index(drop=True)


def resumen_estructurales(al: pd.DataFrame, pos: pd.DataFrame) -> pd.DataFrame:
    """Una fila por alerta estructural emitida (Nombre × Severidad) con N y MV afectado, para alertas_resumen."""
    if al is None or al.empty:
        return pd.DataFrame(columns=["ID", "Nombre", "Severidad", "Ambito", "Estado", "N", "MV_afectado", "Regla"])
    mv = pos.set_index("Pos_ID")["TotalMVal"] if "Pos_ID" in pos.columns else pd.Series(dtype=float)
    a = al.assign(_mv=pd.to_numeric(al["Pos_ID"].map(mv), errors="coerce").abs().fillna(0))
    g = a.groupby(["Nombre", "Severidad", "Ambito"], sort=False).agg(N=("Nombre", "size"), MV_afectado=("_mv", "sum")).reset_index()
    return g.assign(ID="", Estado="ESTRUCTURAL", Regla="emitida por el pipeline")[["ID", "Nombre", "Severidad", "Ambito", "Estado", "N", "MV_afectado", "Regla"]]
