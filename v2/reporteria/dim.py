"""Dimensionales locales (DuckDB): resolución de Bucket / Ficha_FI / FX_Exposure por filas con comodines.

Una fila de `dim_clasificacion` fija algunas columnas de llave (NULL = cualquiera) y define uno o más atributos
(NULL = no lo define). Por atributo gana la fila candidata más específica: n° de columnas fijas, +100 si ID_Fund
está fijo (regla propia del fondo). Empate con valores distintos → menor ID + alerta DIM_AMBIGUA.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import alertas
from .modelo import CODIGOS, limpiar_txt, vigente

COLS_LLAVE = ["BalanceSheet"] + CODIGOS + ["Emision_nacional"]
ATRIBUTOS = ("Bucket", "Ficha_FI", "FX_Exposure")
ORIGEN = {"Bucket": "Bucket_Origen", "Ficha_FI": "Ficha_Origen", "FX_Exposure": "FX_Origen"}
COLS_TEXTO = ["Comentario", "Origen_Migracion"]
COLS_CLASIF = ["ID", "ID_Fund"] + COLS_LLAVE + list(ATRIBUTOS) + COLS_TEXTO + ["Vigente_Desde", "Vigente_Hasta"]
BONO_FONDO = 100
CATALOGO_DE = {c: "dim_" + c.replace("_Code", "").lower() for c in CODIGOS}      # Investment_Type_Code → dim_investment_type


def tabla_vacia() -> pd.DataFrame:
    return normalizar(pd.DataFrame(columns=COLS_CLASIF))


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Tipos canónicos: ID int, ID_Fund/códigos Int64 (NA = comodín), textos '' → NA, fechas."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    for c in COLS_CLASIF:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[COLS_CLASIF]
    for c in ["ID_Fund"] + CODIGOS + ["Emision_nacional"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    for c in ["BalanceSheet"] + list(ATRIBUTOS) + COLS_TEXTO:
        s = limpiar_txt(out[c])
        if c != "FX_Exposure":
            s = s.str.replace("\n", " ", regex=False) if c == "FX_Exposure" else s
        out[c] = s.str.replace("\n", " ", regex=False).where(s.ne(""), pd.NA).astype("object")
    for c in ("Vigente_Desde", "Vigente_Hasta"):
        out[c] = pd.to_datetime(out[c], errors="coerce")
    ids = pd.to_numeric(out["ID"], errors="coerce")
    if ids.isna().any():
        nuevo = int(ids.max()) + 1 if ids.notna().any() else 1
        faltan = ids.isna()
        ids[faltan] = range(nuevo, nuevo + int(faltan.sum()))
    out["ID"] = ids.astype(int)
    return out.reset_index(drop=True)


def especificidad(clasif: pd.DataFrame) -> pd.Series:
    return clasif[COLS_LLAVE].notna().sum(axis=1) + BONO_FONDO * clasif["ID_Fund"].notna()


def _llaves(pos: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {"BalanceSheet": limpiar_txt(pos["BalanceSheet"]).to_numpy(dtype=object)}
    for c in CODIGOS + ["Emision_nacional"]:
        s = pos[c] if c in pos.columns else pd.Series(0, index=pos.index)
        out[c] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int).to_numpy()
    out["ID_Fund"] = pd.to_numeric(pos["ID_Fund"], errors="coerce").fillna(-1).astype(int).to_numpy()
    return out


def _mascara(llaves: dict[str, np.ndarray], fila: pd.Series) -> np.ndarray:
    m = np.ones(len(llaves["ID_Fund"]), dtype=bool)
    if pd.notna(fila["ID_Fund"]):
        m &= llaves["ID_Fund"] == int(fila["ID_Fund"])
    if pd.notna(fila["BalanceSheet"]):
        m &= llaves["BalanceSheet"] == str(fila["BalanceSheet"])
    for c in CODIGOS + ["Emision_nacional"]:
        if pd.notna(fila[c]):
            m &= llaves[c] == int(fila[c])
    return m


def llave_txt(pos: pd.DataFrame) -> pd.Series:
    """Texto legible de la llave de cada posición: Asset|1|1|3|1|2|0|0|0|E0."""
    ll = _llaves(pos)
    cods = ["|".join(str(ll[c][i]) for c in CODIGOS) for i in range(len(pos))]
    return pd.Series([f"{b}|{k}|E{e}" for b, k, e in zip(ll["BalanceSheet"], cods, ll["Emision_nacional"])], index=pos.index)


def resolver(pos: pd.DataFrame, clasif: pd.DataFrame, settle: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (DataFrame con Bucket, Ficha_FI, FX_Exposure y *_Origen = 'DIM:{ID}', alertas DIM_AMBIGUA)."""
    clasif = normalizar(clasif)
    if settle is not None and len(clasif):
        clasif = clasif[vigente(clasif.rename(columns={"Vigente_Desde": "Fecha_Desde", "Vigente_Hasta": "Fecha_Fin"}), settle).to_numpy()]
    n = len(pos)
    llaves = _llaves(pos)
    esp = especificidad(clasif).to_numpy() if len(clasif) else np.array([])
    valor = {a: np.full(n, "", dtype=object) for a in ATRIBUTOS}
    idx = {a: np.full(n, -1) for a in ATRIBUTOS}
    mejor = {a: np.full(n, -1) for a in ATRIBUTOS}
    ambiguo = {a: np.zeros(n, dtype=bool) for a in ATRIBUTOS}
    orden = np.argsort(clasif["ID"].to_numpy(), kind="stable") if len(clasif) else []
    for j in orden:
        fila = clasif.iloc[j]
        m = None
        for a in ATRIBUTOS:
            if pd.isna(fila[a]):
                continue
            if m is None:
                m = _mascara(llaves, fila)
                if not m.any():
                    break
            gana = m & (esp[j] > mejor[a])
            empata = m & (esp[j] == mejor[a]) & (valor[a] != fila[a])
            ambiguo[a] = (ambiguo[a] | empata) & ~gana
            valor[a][gana], idx[a][gana], mejor[a][gana] = fila[a], int(fila["ID"]), esp[j]
    out = pd.DataFrame(index=pos.index)
    for a in ATRIBUTOS:
        out[a] = valor[a]
        out[ORIGEN[a]] = np.where(idx[a] >= 0, "DIM:" + pd.Series(idx[a]).astype(str).to_numpy(), "")
    al = []
    for a in ATRIBUTOS:
        if ambiguo[a].any():
            filas = pos[ambiguo[a]].assign(_Llave=llave_txt(pos[ambiguo[a]])).drop_duplicates(["ID_Fund", "_Llave"])
            al.append(alertas.emitir("DIM_AMBIGUA", "MEDIA", filas, f"{a}: dos filas de dim_clasificacion con la misma especificidad; se usó la de menor ID",
                                     valor="_Llave"))
    return out, alertas.juntar(*al)


# ---------------------------------------------------------------- migración / validación ----------------------------------------------------------------

def _como_posiciones(tabla: pd.DataFrame) -> pd.DataFrame:
    pos = pd.DataFrame(index=tabla.index)
    for c in COLS_LLAVE:
        pos[c] = tabla[c] if c in tabla.columns else (pd.NA if c == "BalanceSheet" else 0)
    pos["BalanceSheet"] = limpiar_txt(pos["BalanceSheet"])
    pos["ID_Fund"] = -1
    return pos


def _reproduce(filas: pd.DataFrame, originales: pd.DataFrame, atributo: str, base: pd.DataFrame | None, fondo: int | None) -> bool:
    """¿`base` + `filas` (con comodines) devuelve para cada llave original el valor original, sin ambigüedad?"""
    f = normalizar(pd.concat([base, filas], ignore_index=True) if base is not None else filas)
    pos = _como_posiciones(originales)
    if fondo is not None:
        pos["ID_Fund"] = fondo
    out, al = resolver(pos, f)
    if len(al) or solapes(f, atributo):               # ni ambigüedad en las llaves presentes ni latente para llaves nuevas
        return False
    esperado = limpiar_txt(originales[atributo]).to_numpy()
    con_valor = esperado != ""
    return bool((out[atributo].to_numpy()[con_valor] == esperado[con_valor]).all())


def compactar(tabla: pd.DataFrame, columnas: list[str], atributo: str, fijas: tuple[str, ...] = ("BalanceSheet",),
              base: pd.DataFrame | None = None, fondo: int | None = None) -> pd.DataFrame:
    """Mínimas filas con comodines que reproducen `atributo` para todas las llaves de `tabla` (fijas en `columnas`).

    Genérico (`base=None`): 1) suelta columnas completas que no cambian el atributo; 2) por grupo de llaves que solo
    difieren en una columna, reemplaza el grupo por una fila comodín con el valor mayoritario (estricto, ≥ 2 filas) y
    deja como filas específicas las excepciones (gana la más específica). Diferencial (`base`, `fondo`): parte solo de
    las llaves cuyo valor difiere de lo que ya resuelve `base` para ese fondo y generaliza igual; devuelve filas con
    `ID_Fund=fondo`. Cada paso se acepta solo si `_reproduce` sigue siendo cierto. Las columnas en `fijas` nunca se
    generalizan (Asset/Liability siempre explícito). Llaves sin valor heredan el de sus vecinas.
    """
    t = tabla[columnas + [atributo]].copy().reset_index(drop=True)
    t[atributo] = limpiar_txt(t[atributo]).where(limpiar_txt(t[atributo]).ne(""), pd.NA).astype("object")
    for c in columnas:
        t[c] = limpiar_txt(t[c]) if c == "BalanceSheet" else pd.to_numeric(t[c], errors="coerce").astype("Int64")
    originales = t[t[atributo].notna()].reset_index(drop=True)
    if base is None:
        activas = list(columnas)
        for c in reversed(columnas):
            resto = [x for x in activas if x != c]
            if c not in fijas and resto and not t[atributo].groupby([t[x] for x in resto], dropna=False).nunique(dropna=True).gt(1).any():
                activas = resto
        filas = t.groupby(activas, dropna=False, sort=True)[[atributo]].first().reset_index()
        for c in columnas:
            if c not in activas:
                filas[c] = pd.NA
    else:
        pos = _como_posiciones(originales)
        pos["ID_Fund"] = fondo
        ya, _ = resolver(pos, base)
        filas = originales[ya[atributo].to_numpy() != originales[atributo].to_numpy()].copy()
    filas = filas[columnas + [atributo]].reset_index(drop=True)          # las llaves sin valor siguen aquí para heredar en el paso 2
    if fondo is not None:
        filas["ID_Fund"] = fondo
    cambio = True
    while cambio:
        cambio = False
        for c in reversed(columnas):
            if c in fijas:
                continue
            otras = [x for x in columnas if x != c]
            con_c = filas[filas[c].notna()]
            for _, g in con_c.groupby([con_c[x].astype("string").fillna("*") for x in otras], dropna=False, sort=False):
                conteo = g[atributo].value_counts(dropna=True)          # mayoría estricta entre las definidas; las vacías heredan
                if len(g) < 2 or conteo.empty or (len(conteo) > 1 and conteo.iloc[0] == conteo.iloc[1]) or (len(conteo) == 1 and g[atributo].notna().sum() < 2 and g[atributo].notna().all()):
                    continue
                mayor = conteo.index[0]
                comodin = g.iloc[[0]].copy()
                comodin[c] = pd.NA
                comodin[atributo] = mayor
                excepciones = g[g[atributo].notna() & g[atributo].ne(mayor)]
                cand = pd.concat([filas.drop(g.index), comodin, excepciones], ignore_index=True)
                if _reproduce(cand, originales, atributo, base, fondo):
                    filas, cambio = cand, True
                    break
            if cambio:
                break
    filas = filas.dropna(subset=[atributo]).reset_index(drop=True)
    assert _reproduce(filas, originales, atributo, base, fondo), "compactar: la tabla compactada no reproduce la original"
    return filas.sort_values(columnas, na_position="first", kind="stable").reset_index(drop=True)


def fusionar_filas(partes: list[pd.DataFrame]) -> pd.DataFrame:
    """Une tablas (una por atributo) y funde las filas con exactamente la misma llave (ID_Fund + columnas fijas)."""
    todo = normalizar(pd.concat(partes, ignore_index=True))
    llave = todo[["ID_Fund"] + COLS_LLAVE].astype("string").fillna("*").agg("|".join, axis=1)
    agg = {a: "first" for a in ATRIBUTOS}
    agg.update({c: "first" for c in COLS_TEXTO})
    out = todo.groupby(llave, sort=False).agg({**{c: "first" for c in ["ID_Fund"] + COLS_LLAVE}, **agg}).reset_index(drop=True)
    out["_bs"] = out["BalanceSheet"].fillna("~")                 # genéricas de Bucket/Ficha (Asset, Liability) antes que las FX
    out = out.sort_values(["ID_Fund", "_bs"] + COLS_LLAVE[1:], na_position="first", kind="stable").drop(columns="_bs").reset_index(drop=True)
    out.insert(0, "ID", range(1, len(out) + 1))
    return normalizar(out)


def redundantes(propias: pd.DataFrame, genericas: pd.DataFrame, atributo: str) -> pd.Series:
    """True si la fila propia (de un fondo) da exactamente lo que ya darían las genéricas para cualquier posición que la calce.

    Condición conservadora: existe al menos una genérica compatible (columnas fijas en ambas iguales) cuyo conjunto de
    columnas fijas ⊆ el de la propia, y todas las compatibles definen el mismo valor que la propia.
    """
    out = []
    gen = genericas[genericas[atributo].notna()]
    for _, p in propias.iterrows():
        fijas_p = {c for c in COLS_LLAVE if pd.notna(p[c])}
        compat, segura = [], False
        for _, g in gen.iterrows():
            fijas_g = {c for c in COLS_LLAVE if pd.notna(g[c])}
            if all(g[c] == p[c] for c in fijas_g & fijas_p):
                compat.append(g[atributo])
                segura = segura or fijas_g <= fijas_p
        out.append(bool(compat) and segura and all(v == p[atributo] for v in compat))
    return pd.Series(out, index=propias.index, dtype=bool)


def solapes(clasif: pd.DataFrame, atributo: str) -> list[tuple[int, int]]:
    """Pares de filas (ID_a, ID_b) con igual especificidad, valores distintos del atributo y llaves compatibles (ambigüedad latente)."""
    c = normalizar(clasif)
    d = c[c[atributo].notna()]
    esp = especificidad(d)
    out = []
    for _, g in d.groupby(esp):
        if len(g) < 2:
            continue
        filas = g.to_dict("records")
        for i in range(len(filas)):
            for j in range(i + 1, len(filas)):
                p, q = filas[i], filas[j]
                if p[atributo] == q[atributo]:
                    continue
                comunes = [k for k in ["ID_Fund"] + COLS_LLAVE if pd.notna(p[k]) and pd.notna(q[k])]
                if all(p[k] == q[k] for k in comunes):
                    out.append((int(p["ID"]), int(q["ID"])))
    return out


def validar(clasif: pd.DataFrame, catalogos: dict[str, pd.DataFrame] | None = None, buckets: set[str] | None = None,
            fondos: set[int] | None = None) -> pd.DataFrame:
    """Problemas (Tabla, ID, Problema). Vacío = válida."""
    c = normalizar(clasif)
    prob = []
    dup = c["ID"].duplicated(keep=False)
    for i in c.index[dup]:
        prob.append(("dim_clasificacion", int(c.at[i, "ID"]), "ID duplicado"))
    sin_attr = c[list(ATRIBUTOS)].isna().all(axis=1)
    for i in c.index[sin_attr]:
        prob.append(("dim_clasificacion", int(c.at[i, "ID"]), "la fila no define Bucket, Ficha_FI ni FX_Exposure"))
    malos_bs = c["BalanceSheet"].notna() & ~c["BalanceSheet"].isin(["Asset", "Liability"])
    for i in c.index[malos_bs]:
        prob.append(("dim_clasificacion", int(c.at[i, "ID"]), f"BalanceSheet '{c.at[i, 'BalanceSheet']}' debe ser Asset o Liability"))
    if catalogos:
        for col, tabla in CATALOGO_DE.items():
            if tabla in catalogos and col in catalogos[tabla].columns:
                validos = set(pd.to_numeric(catalogos[tabla][col], errors="coerce").dropna().astype(int))
                malos = c[col].notna() & ~c[col].isin(list(validos))
                for i in c.index[malos]:
                    prob.append(("dim_clasificacion", int(c.at[i, "ID"]), f"{col}={c.at[i, col]} no existe en {tabla}"))
    if buckets is not None:
        malos = c["Bucket"].notna() & ~c["Bucket"].isin(list(buckets))
        for i in c.index[malos]:
            prob.append(("dim_clasificacion", int(c.at[i, "ID"]), f"Bucket '{c.at[i, 'Bucket']}' no existe en REGLAS/buckets"))
    if fondos is not None:
        malos = c["ID_Fund"].notna() & ~c["ID_Fund"].isin(list(fondos))
        for i in c.index[malos]:
            prob.append(("dim_clasificacion", int(c.at[i, "ID"]), f"ID_Fund={c.at[i, 'ID_Fund']} no existe en dim_fondos"))
    llave = c[["ID_Fund"] + COLS_LLAVE].astype("string").fillna("*").agg("|".join, axis=1)
    for k, g in c.groupby(llave):
        if len(g) < 2:
            continue
        for a in ATRIBUTOS:
            vals = g[a].dropna().unique()
            if len(vals) > 1:
                prob.append(("dim_clasificacion", int(g["ID"].min()), f"filas {sorted(g['ID'])} con la misma llave {k} definen {a} distinto: {sorted(vals)}"))
    for a in ATRIBUTOS:
        for ida, idb in solapes(c, a):
            prob.append(("dim_clasificacion", ida, f"solapa con la fila {idb} a igual especificidad y {a} distinto → DIM_AMBIGUA"))
    return pd.DataFrame(prob, columns=["Tabla", "ID", "Problema"]).drop_duplicates().reset_index(drop=True)


def plantilla_sin_dim(pos: pd.DataFrame) -> pd.DataFrame:
    """Combinaciones de llave sin Bucket / Ficha_FI / FX_Exposure, listas para pegar como filas de dim_clasificacion."""
    falta = pd.DataFrame({"Bucket": pos["Bucket"].eq("SIN_REGLA"), "Ficha_FI": limpiar_txt(pos["Ficha_FI"]).eq(""),
                          "FX_Exposure": limpiar_txt(pos["FX_Exposure"]).eq("")}, index=pos.index)
    sub = pos[falta.any(axis=1)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["ID", "ID_Fund"] + COLS_LLAVE + list(ATRIBUTOS) + ["Comentario", "_Falta", "_N_Posiciones", "_TotalMVal", "_Fondos", "_Ejemplo"])
    ll = _llaves(sub)
    for c in CODIGOS + ["Emision_nacional"]:
        sub[c] = ll[c]
    sub["BalanceSheet"] = ll["BalanceSheet"]
    sub["_Falta"] = falta.loc[sub.index].apply(lambda r: ", ".join(a for a in ATRIBUTOS if r[a]), axis=1)
    g = sub.groupby(COLS_LLAVE, dropna=False, sort=False)
    out = g.agg(_N_Posiciones=("Pos_ID", "size"), _TotalMVal=("TotalMVal", lambda s: float(s.abs().sum())),
                _Fondos=("ID_Fund", lambda s: ";".join(str(int(x)) for x in sorted(set(s)))),
                _Ejemplo=("Name_Instrumento", "first"), _Falta=("_Falta", "first"),
                Bucket=("Bucket", lambda s: "" if s.eq("SIN_REGLA").any() else s.iloc[0]),
                Ficha_FI=("Ficha_FI", "first"), FX_Exposure=("FX_Exposure", "first")).reset_index()
    out.insert(0, "ID_Fund", "")
    out.insert(0, "ID", "")
    out["Comentario"] = ""
    cols = ["ID", "ID_Fund"] + COLS_LLAVE + list(ATRIBUTOS) + ["Comentario", "_Falta", "_N_Posiciones", "_TotalMVal", "_Fondos", "_Ejemplo"]
    return out[cols].sort_values("_TotalMVal", ascending=False).reset_index(drop=True)
