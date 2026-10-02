"""Impacto de la verdad actual sobre los cierres publicados y re-expresión.

`impacto(rutas)` compara, para la última verdad de cada cierre del datamart, lo que esa versión usó (atributos del maestro
guardados en posiciones, hash de dim/REGLAS/código/insumos, cierre anterior) con lo que hay hoy → filas
(cierre, version, Pos_ID, atributo, antes, despues, consecuencia). `recalcular(...)` vuelve a correr un cierre con la
verdad actual y lo publica como REEXPRESADA; `reexpresar_impactados(...)` lo hace en orden cronológico para todos los
cierres con impacto (cadena incluida).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd

from . import datamart as DMV
from . import dim as D
from . import maestros_hist as MH
from .adaptadores import datamart as DM
from .adaptadores import dim as DIM
from .config import Rutas
from .lectura.maestros import COLS_INSTR
from .modelo import CODIGOS, limpiar_txt

COLS_IMPACTO = ["cierre", "version", "fondo", "Pos_ID", "atributo", "antes", "despues", "consecuencia", "accion"]
GRUPO = {"CLASIFICACION": "MAESTRO", "HEDGE": "MAESTRO", "FUENTE": "MAESTRO", "IDENTIDAD": "MAESTRO", "DIM": "DIM", "INSUMO": "INSUMO",
         "CADENA": "CADENA", "CODIGO": "CODIGO", "REGLAS": "REGLAS"}
POLITICA_DEFAULT = {"mensual": "MAESTRO;DIM;INSUMO;CADENA;CODIGO;REGLAS", "diario": "MAESTRO;DIM;INSUMO;CADENA"}
VENTANA_DEFAULT = 60
CONSECUENCIA = {**{c: "CLASIFICACION" for c in CODIGOS + ["Emision_nacional"]},
                "Risk_Currency": "HEDGE", "Risk_Country": "HEDGE", "ISIN": "FUENTE", "Yield_Type": "FUENTE", "Name_Instrumento": "FUENTE",
                "Issue_Currency": "FUENTE", "CompanyName": "FUENTE"}
INSUMOS_FECHADOS = ("CUBO", "JPM", "Carga_Indexes", "CurvasSoberanas")        # los sin fecha (RA_TIR, paridades, jsonl) no son corrección


def _fila(cierre, version, pos_id, atributo, antes, despues, consecuencia, fondo=None):
    """`fondo` = ID_Fund del Pos_ID (`ID_Fund|PK2|BalanceSheet`) o el explícito (REGLAS por fondo); vacío en las filas globales."""
    if fondo is None and pos_id and str(pos_id).split("|")[0].isdigit():
        fondo = int(str(pos_id).split("|")[0])
    return dict(cierre=cierre, version=version, fondo=fondo, Pos_ID=pos_id, atributo=atributo, antes=MH._celda(antes), despues=MH._celda(despues),
                consecuencia=consecuencia, accion="")


def politica_reexpresion(parametros: dict | None, modo: str) -> set[str]:
    """Grupos de consecuencia que se re-expresan solos (`parametros.reexpresar_por`); el resto solo se marca. REGLAS con ID_Fund
    siempre re-expresa ese fondo. Default mensual: todo (decisión H9); diario: MAESTRO;DIM;INSUMO;CADENA (decisión H10)."""
    txt = str((parametros or {}).get("reexpresar_por", "") or POLITICA_DEFAULT.get(modo, POLITICA_DEFAULT["mensual"]))
    return {x.strip().upper() for x in txt.replace(",", ";").split(";") if x.strip()}


def asignar_accion(imp: pd.DataFrame, politica: set[str]) -> pd.DataFrame:
    if imp is None or imp.empty:
        return imp
    imp = imp.copy()
    grupo = imp["consecuencia"].map(GRUPO).fillna(imp["consecuencia"])
    reglas_fondo = imp["consecuencia"].eq("REGLAS") & imp["fondo"].notna()
    imp["accion"] = ["REEXPRESAR" if (g in politica or rf) else "MARCAR" for g, rf in zip(grupo, reglas_fondo)]
    return imp


def _filas_distintas(x: pd.DataFrame | None, y: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(solo en x, solo en y) comparando filas como texto."""
    if x is None or y is None:
        return (x if x is not None else pd.DataFrame()), (y if y is not None else pd.DataFrame())
    tx, ty = x.fillna("").astype(str), y.fillna("").astype(str)
    kx, ky = tx.apply("|".join, axis=1), ty.apply("|".join, axis=1) if len(ty) else pd.Series(dtype=str)
    kx = kx if len(tx) else pd.Series(dtype=str)
    return x[~kx.isin(set(ky))], y[~ky.isin(set(kx))]


def impacto_version(rutas: Rutas, v: DMV.Version, maestros: dict[str, pd.DataFrame], dims, hash_codigo: str,
                    anterior_actual: pd.DataFrame | None) -> pd.DataFrame:
    """`anterior_actual`: la cartera anterior que la corrida usaría HOY (última verdad de F−1, por fondo en modo diario): CADENA se
    detecta por contenido (hedge heredado distinto), no por número de versión."""
    pos = v.posiciones()
    filas = []
    c = v.corrida
    cierre, numero = v.cierre, v.numero
    if pos is None or pos.empty:
        return pd.DataFrame(columns=COLS_IMPACTO)
    # 1) maestro de instrumentos: atributos usados vs as-of hoy (respetando overrides de atributo aplicados en esa corrida)
    bd = maestros.get("bd_instrumentos")
    if bd is not None and len(bd):
        m = bd.set_index("PK2")
        por_id = bd.groupby("ID_Instrumento").filter(lambda g: len(g) == 1).set_index("ID_Instrumento")
        attrs = [a for a in COLS_INSTR if a in pos.columns and a in m.columns and a not in ("ID_Instrumento", "SubID_Instrumento")]
        for r in pos.itertuples(index=False):
            d = r._asdict()
            pk = str(d.get("PK2", ""))
            if pk in m.index:
                fila = m.loc[pk]
            elif str(d.get("PK2_Maestro", "")) in m.index:
                fila = m.loc[str(d["PK2_Maestro"])]
            elif pd.notna(d.get("ID_Instrumento")) and int(d["ID_Instrumento"]) in por_id.index and pk == f"{int(d['ID_Instrumento'])}-{pk.split('-')[-1]}" \
                    and pk.replace("-", "").isdigit() and pk.count("-") == 1:                       # misma regla que universo._cruce_por_id
                fila = por_id.loc[int(d["ID_Instrumento"])]
                filas.append(_fila(cierre, numero, d["Pos_ID"], "PK2", pk, fila["PK2"], "IDENTIDAD"))
            else:
                continue                                                    # sin fila hoy (baja posterior: no es retroactiva)
            for a in attrs:
                if a in str(d.get("Overrides", "")):                       # ese atributo venía de REGLAS/overrides_atributo
                    continue
                antes, despues = MH._celda(d.get(a)), MH._celda(fila[a])
                if antes != despues:
                    filas.append(_fila(cierre, numero, d["Pos_ID"], a, antes, despues, CONSECUENCIA.get(a, "FUENTE")))
    # 2) HOMOL: cambios posteriores al conocimiento de la versión que tocan algo que la corrida usó
    # (se cubre por hash de maestros: si la base/cargas cambiaron y el Origen cita HOMOL, se marca FUENTE)
    # 3) dim: re-resolver con las dimensionales actuales
    if dims is not None and c.get("hashes", {}).get("dim") and c["hashes"]["dim"] != DM.hash_archivo(rutas.dim):
        res, _ = D.resolver(pos, dims.clasificacion, pd.Timestamp(cierre))
        for a, origen in (("Bucket", "Bucket_Origen"), ("Ficha_FI", "Ficha_Origen"), ("FX_Exposure", "FX_Origen")):
            if a not in pos.columns:
                continue
            usa_dim = pos[origen].astype(str).str.startswith("DIM:") | pos[origen].astype(str).eq("") if origen in pos.columns else pd.Series(True, index=pos.index)
            if a == "Bucket":
                usa_dim &= ~pos["Bucket_Origen"].astype(str).str.startswith(("REGLA", "OVERRIDE"))
            antes, despues = limpiar_txt(pos[a]), limpiar_txt(res[a])
            if a == "Bucket":
                antes = antes.replace("SIN_REGLA", "")
            dif = usa_dim & antes.ne(despues)
            for i in pos.index[dif]:
                filas.append(_fila(cierre, numero, pos.at[i, "Pos_ID"], a, antes[i], despues[i], "DIM"))
    # 4) REGLAS por hoja
    reglas_copia = v.ruta / DM.INSUMOS / "REGLAS.xlsx"
    if c.get("hashes", {}).get("reglas") and c["hashes"]["reglas"] != DM.hash_archivo(rutas.reglas) and reglas_copia.exists() and Path(rutas.reglas).exists():
        a, b = pd.read_excel(reglas_copia, sheet_name=None), pd.read_excel(rutas.reglas, sheet_name=None)
        for hoja in sorted(set(a) | set(b)):
            x, y = a.get(hoja), b.get(hoja)
            if x is not None and y is not None and x.fillna("").astype(str).reset_index(drop=True).equals(y.fillna("").astype(str).reset_index(drop=True)):
                continue
            sx, sy = _filas_distintas(x, y)
            dif = pd.concat([sx, sy], ignore_index=True)
            if len(dif) and "ID_Fund" in dif.columns and pd.to_numeric(dif["ID_Fund"], errors="coerce").notna().all():
                for fid in sorted(set(pd.to_numeric(dif["ID_Fund"]).astype(int))):     # solo filas de fondos concretos: impacto por fondo
                    filas.append(_fila(cierre, numero, "", f"REGLAS/{hoja}", f"{len(sx[pd.to_numeric(sx['ID_Fund'], errors='coerce').eq(fid)]) if len(sx) else 0} filas",
                                       f"{len(sy[pd.to_numeric(sy['ID_Fund'], errors='coerce').eq(fid)]) if len(sy) else 0} filas", "REGLAS", fondo=fid))
            else:
                filas.append(_fila(cierre, numero, "", f"REGLAS/{hoja}", f"{0 if x is None else len(x)} filas", f"{0 if y is None else len(y)} filas", "REGLAS"))
    # 5) código
    if c.get("hashes", {}).get("codigo") and c["hashes"]["codigo"] != hash_codigo:
        filas.append(_fila(cierre, numero, "", "codigo", c["hashes"]["codigo"], hash_codigo, "CODIGO"))
    # 6) insumos fechados re-entregados
    hp = v.ruta / DM.INSUMOS / "hashes.json"
    if hp.exists():
        import json
        h = json.loads(hp.read_text(encoding="utf-8"))
        actuales = {"CUBO": rutas.cubo, "JPM": rutas.jpm, "Carga_Indexes": rutas.indexes, "CurvasSoberanas": rutas.curvas_sob}
        if Path(rutas.ra).name != "RA_TIR.xlsx":                                        # RA_TIR_{F}.xlsx fechado: sí es corrección
            actuales["RA_TIR"] = rutas.ra
        for nombre in INSUMOS_FECHADOS + ("RA_TIR",):
            if nombre in h and actuales.get(nombre) and Path(actuales[nombre]).exists():
                nuevo = DM.hash_archivo(actuales[nombre])
                if nuevo != h[nombre]["hash"]:
                    filas.append(_fila(cierre, numero, "", nombre, h[nombre]["hash"], nuevo, "INSUMO"))
    # 7) cadena por contenido: el hedge que hoy heredaría del cierre anterior difiere del que usó
    if anterior_actual is not None and len(anterior_actual) and {"Hedge_Currency", "Hedge_Origen"} <= set(pos.columns) and "Hedge_Currency" in anterior_actual.columns:
        prev = limpiar_txt(anterior_actual.drop_duplicates("Pos_ID").set_index("Pos_ID")["Hedge_Currency"])
        heredado = pos["Pos_ID"].map(prev)
        if "Pos_Key" in anterior_actual.columns and "Pos_Key" in pos.columns:
            prev_k = limpiar_txt(anterior_actual.drop_duplicates("Pos_Key").set_index("Pos_Key")["Hedge_Currency"])
            heredado = heredado.where(heredado.notna(), pos["Pos_Key"].map(prev_k))
        con_politica = pos["Hedge_Origen"].astype(str).isin(("REGLA", "ANTERIOR", "MES_ANTERIOR"))
        actual = limpiar_txt(pos["Hedge_Currency"])
        esperado = limpiar_txt(heredado.where(heredado.notna(), actual))
        dif = con_politica & heredado.notna() & esperado.ne(actual)
        for i in pos.index[dif]:
            filas.append(_fila(cierre, numero, pos.at[i, "Pos_ID"], "Hedge_Currency", actual[i], esperado[i], "CADENA"))
    return pd.DataFrame(filas, columns=COLS_IMPACTO)


def rutas_de(rutas: Rutas, fecha: str) -> Rutas:
    """Rutas del mismo entorno para otro cierre (raíz, dim, datamart y BIX iguales)."""
    from .config import _detectar_fecha_ant, _uno
    mercado = rutas.ra.parent
    return dataclasses.replace(rutas, fecha=fecha, cubo=rutas.cubo.parent / f"CUBO_{fecha}.xlsx",
                               ra=_uno(str(mercado / f"RA_TIR_{fecha}*.xlsx")) or mercado / "RA_TIR.xlsx",
                               facturas=_uno(str(mercado / f"FACTURAS_{fecha}*.xlsx")), jpm=_uno(str(mercado / f"JPM_CEMBI_GBI_{fecha}*.xlsx")),
                               indexes=_uno(str(mercado / f"Carga_Indexes_{fecha}*.csv")), curvas_sob=_uno(str(mercado / f"Carga_CurvasSoberanas_{fecha}*.csv")),
                               outputs=rutas.outputs.parent / fecha, logs=rutas.logs.parent / fecha, cache=rutas.cache.parent / fecha,
                               fecha_ant=_detectar_fecha_ant(rutas.outputs.parent, fecha, rutas.datamart))


def _parametros(rutas: Rutas) -> dict:
    try:
        from .lectura.reglas import leer_reglas
        return leer_reglas(rutas.reglas).parametros if Path(rutas.reglas).exists() else {}
    except Exception:                       # noqa: BLE001 — REGLAS inválido no impide evaluar el impacto
        return {}


def ventana_de(rutas: Rutas, parametros: dict | None = None) -> int | None:
    """Días hacia atrás que se evalúan solos (`parametros.ventana_reexpresion_dias`, default 60) en modo diario; mensual: todo."""
    if rutas.modo != "diario":
        return None
    p = parametros if parametros is not None else _parametros(rutas)
    return int(float(p.get("ventana_reexpresion_dias", VENTANA_DEFAULT)))


def anterior_actual_de(rutas: Rutas, fecha: str) -> pd.DataFrame | None:
    """La cartera anterior que `correr` usaría hoy para `fecha`: por fondo en modo diario, última verdad de F−1 en mensual."""
    if rutas.modo == "diario":
        ant, _ = DMV.anterior_por_fondo(rutas.datamart, fecha)
        return ant
    prev = [x for x in DM.fechas(rutas.datamart) if x < fecha]
    v = DMV.ultima_verdad(rutas.datamart, prev[-1]) if prev else None
    return v.posiciones() if v is not None else None


def impacto(rutas: Rutas, cierres: list[str] | None = None, conocimiento: str | None = None, todos: bool = False,
            hoy: str | None = None) -> pd.DataFrame:
    """Impacto sobre la última verdad de cada cierre del datamart: los indicados, o los de la ventana (`todos=True`: sin ventana).
    Cada fila trae `accion` REEXPRESAR | MARCAR según `reexpresar_por`."""
    import datetime as dt
    fechas = sorted(cierres or DM.fechas(rutas.datamart))
    parametros = _parametros(rutas)
    ventana = None if todos or cierres else ventana_de(rutas, parametros)
    if ventana is not None:
        desde = (pd.Timestamp(hoy) if hoy else pd.Timestamp(dt.date.today())) - pd.Timedelta(days=ventana)
        fechas = [f for f in fechas if pd.Timestamp(f) >= desde]
    if not fechas:
        return pd.DataFrame(columns=COLS_IMPACTO)
    dims = DIM.leer(rutas.dim) if Path(rutas.dim).exists() else None
    hc = DM.hash_codigo()
    partes = []
    for f in fechas:
        v = DMV.ultima_verdad(rutas.datamart, f)
        if v is None:
            continue
        maestros = MH.desde_datamart(rutas.datamart, f, conocimiento)
        partes.append(impacto_version(rutas_de(rutas, f), v, maestros, dims, hc, anterior_actual_de(rutas, f)))
    imp = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_IMPACTO)
    return asignar_accion(imp, politica_reexpresion(parametros, rutas.modo))


def resumen_impacto(imp: pd.DataFrame) -> pd.DataFrame:
    if imp is None or imp.empty:
        return pd.DataFrame(columns=["cierre", "version", "consecuencia", "accion", "N"])
    t = imp.copy()
    if "accion" not in t.columns:
        t["accion"] = ""
    return t.groupby(["cierre", "version", "consecuencia", "accion"]).size().reset_index(name="N")


def recalcular(rutas: Rutas, motivo: str, con_terminal: bool = False, refrescar: set[str] | None = None, refrescar_facts: bool = False,
               bbg=None, fx=None, facts=None, log=None, sin_cargar_maestros: bool = False) -> DMV.Version:
    """Vuelve a correr `rutas.fecha` con la verdad actual (maestros as-of, dim, REGLAS, código) y los insumos sin fecha de la
    versión vigente (salvo `refrescar`), y lo publica como REEXPRESADA. Sin terminal, lo no cacheado queda PENDIENTE_TERMINAL.
    `sin_cargar_maestros`: no abrir el BIX (la nocturna ya registró la carga; la estación con terminal nunca escribe maestros)."""
    from .pipeline import Opciones, correr
    v = DMV.ultima_verdad(rutas.datamart, rutas.fecha)
    if v is None:
        raise ValueError(f"el cierre {rutas.fecha} no tiene versión publicada que re-expresar")
    refrescar = refrescar or set()
    ins = v.ruta / DM.INSUMOS
    cambios = {}
    for nombre, campo in (("RA_TIR", "ra"), ("paridades", "paridades"), ("bond_schedule", "jsonl")):
        copia = ins / Path(getattr(rutas, campo)).name
        if nombre not in refrescar and copia.exists():
            cambios[campo] = copia
    r2 = dataclasses.replace(rutas, **cambios)
    facturas = None
    if not refrescar_facts and (ins / "facturas_al_cierre.parquet").exists():
        facturas = DM.leer_tabla(ins / "facturas_al_cierre.parquet")
        for c in ("fecha_vencimiento", "fecha_pago", "fecha_inversion"):
            if c in facturas.columns:
                facturas[c] = pd.to_datetime(facturas[c], errors="coerce")
    opc = Opciones(sin_bbg=not con_terminal, sin_sql=True, sin_facts=facturas is not None, bbg=bbg, fx=fx, facts=facts,
                   facturas=facturas, motivo=motivo, sin_cargar_maestros=sin_cargar_maestros)
    res = correr(r2, opc)
    return DMV.publicar(rutas.datamart, rutas.fecha, res.borrador, motivo, reexpresar=True, modo=rutas.modo)


def _a_reexpresar(imp: pd.DataFrame | None) -> pd.DataFrame:
    if imp is None or imp.empty:
        return pd.DataFrame(columns=COLS_IMPACTO)
    return imp[imp["accion"].eq("REEXPRESAR")] if "accion" in imp.columns else imp


def reexpresar_impactados(rutas: Rutas, imp: pd.DataFrame, excluir: set[str] | None = None, con_terminal: bool = False,
                          sin_cadena: bool = False, log=None, bbg_factory=None, fx=None, hoy: str | None = None,
                          sin_cargar_maestros: bool = False) -> list[DMV.Version]:
    """Re-expresa en orden cronológico cada cierre con impacto de acción REEXPRESAR (menos `excluir`); tras cada uno vuelve a
    evaluar la cadena. Lo marcado (MARCAR: código, REGLAS globales fuera de la política) se deja para `recalcular --desde`."""
    hechos = []
    imp = _a_reexpresar(imp)
    pendientes = sorted(set(imp["cierre"]) - set(excluir or ())) if len(imp) else []
    vistos = set()
    while pendientes:
        f = pendientes.pop(0)
        if f in vistos:
            continue
        vistos.add(f)
        motivo = "; ".join(f"{c}×{n}" for c, n in imp[imp["cierre"].eq(f)].groupby("consecuencia").size().items()) or "cadena"
        rf = rutas_de(rutas, f)
        v = recalcular(rf, f"auto: {motivo}", con_terminal, bbg=bbg_factory(rf) if bbg_factory else None, fx=fx, log=log,
                       sin_cargar_maestros=sin_cargar_maestros)
        hechos.append(v)
        sin_cargar_maestros = True                      # la primera re-expresión ya registró la carga del BIX
        if log:
            log.info("re-expresado %s (%s)", v.etiqueta, v.completitud)
        if not sin_cadena:
            imp = _a_reexpresar(impacto(rutas, hoy=hoy))
            for g in sorted(set(imp.loc[imp["cierre"] > f, "cierre"]) - set(excluir or ()) - vistos):
                if g not in pendientes:
                    pendientes.append(g)
            pendientes.sort()
    return hechos


def recalcular_desde(rutas: Rutas, desde: str, motivo: str, con_terminal: bool = False, bbg_factory=None, fx=None, log=None,
                     hasta: str | None = None) -> list[DMV.Version]:
    """Re-expresa en orden todas las fechas del datamart ≥ `desde` (≤ `hasta`): para lo marcado (código, REGLAS globales) o fuera
    de ventana. En modo diario cada fecha re-apunta solo los fondos cuya cartera cambió."""
    hechos = []
    for f in [x for x in DM.fechas(rutas.datamart) if x >= desde and (hasta is None or x <= hasta)]:
        rf = rutas_de(rutas, f)
        v = recalcular(rf, motivo, con_terminal, bbg=bbg_factory(rf) if bbg_factory else None, fx=fx, log=log)
        hechos.append(v)
        if log:
            log.info("re-expresado %s (%s)", v.etiqueta, v.completitud)
    return hechos
