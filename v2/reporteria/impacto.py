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

COLS_IMPACTO = ["cierre", "version", "fondo", "Pos_ID", "atributo", "antes", "despues", "consecuencia"]
CONSECUENCIA = {**{c: "CLASIFICACION" for c in CODIGOS + ["Emision_nacional"]},
                "Risk_Currency": "HEDGE", "Risk_Country": "HEDGE", "ISIN": "FUENTE", "Yield_Type": "FUENTE", "Name_Instrumento": "FUENTE",
                "Issue_Currency": "FUENTE", "CompanyName": "FUENTE"}
INSUMOS_FECHADOS = ("CUBO", "JPM", "Carga_Indexes", "CurvasSoberanas")        # los sin fecha (RA_TIR, paridades, jsonl) no son corrección


def _fila(cierre, version, pos_id, atributo, antes, despues, consecuencia):
    """`fondo` = ID_Fund del Pos_ID (`ID_Fund|PK2|BalanceSheet`); vacío en las filas globales (REGLAS, código, insumos, cadena)."""
    fondo = int(str(pos_id).split("|")[0]) if pos_id and str(pos_id).split("|")[0].isdigit() else None
    return dict(cierre=cierre, version=version, fondo=fondo, Pos_ID=pos_id, atributo=atributo, antes=MH._celda(antes), despues=MH._celda(despues),
                consecuencia=consecuencia)


def impacto_version(rutas: Rutas, v: DMV.Version, maestros: dict[str, pd.DataFrame], dims, hash_codigo: str, anterior: DMV.Version | None) -> pd.DataFrame:
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
            if x is None or y is None or not x.fillna("").astype(str).reset_index(drop=True).equals(y.fillna("").astype(str).reset_index(drop=True)):
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
        for nombre in INSUMOS_FECHADOS:
            if nombre in h and actuales.get(nombre) and Path(actuales[nombre]).exists():
                nuevo = DM.hash_archivo(actuales[nombre])
                if nuevo != h[nombre]["hash"]:
                    filas.append(_fila(cierre, numero, "", nombre, h[nombre]["hash"], nuevo, "INSUMO"))
    # 7) cadena: el cierre anterior usado ya no es la última verdad de F-1
    usado = c.get("anterior_version") or {}
    if anterior is not None:
        if usado.get("origen") != "DATAMART" or int(usado.get("version") or 0) != anterior.numero:
            filas.append(_fila(cierre, numero, "", "anterior_version", f"{usado.get('cierre', '')}/v{usado.get('version', '') or '-'}" if usado else "(ninguna)",
                               f"{anterior.cierre}/v{anterior.numero:03d}", "CADENA"))
    return pd.DataFrame(filas, columns=COLS_IMPACTO)


def rutas_de(rutas: Rutas, fecha: str) -> Rutas:
    """Rutas del mismo entorno para otro cierre (raíz, dim, datamart y BIX iguales)."""
    from .config import _detectar_fecha_ant, _uno
    mercado = rutas.ra.parent
    return dataclasses.replace(rutas, fecha=fecha, cubo=rutas.cubo.parent / f"CUBO_{fecha}.xlsx",
                               facturas=_uno(str(mercado / f"FACTURAS_{fecha}*.xlsx")), jpm=_uno(str(mercado / f"JPM_CEMBI_GBI_{fecha}*.xlsx")),
                               indexes=_uno(str(mercado / f"Carga_Indexes_{fecha}*.csv")), curvas_sob=_uno(str(mercado / f"Carga_CurvasSoberanas_{fecha}*.csv")),
                               outputs=rutas.outputs.parent / fecha, logs=rutas.logs.parent / fecha, cache=rutas.cache.parent / fecha,
                               fecha_ant=_detectar_fecha_ant(rutas.outputs.parent, fecha, rutas.datamart))


def impacto(rutas: Rutas, cierres: list[str] | None = None, conocimiento: str | None = None) -> pd.DataFrame:
    """Impacto sobre la última verdad de cada cierre del datamart (todos, o los indicados)."""
    todos = cierres or DM.fechas(rutas.datamart)
    if not todos:
        return pd.DataFrame(columns=COLS_IMPACTO)
    dims = DIM.leer(rutas.dim) if Path(rutas.dim).exists() else None
    hc = DM.hash_codigo()
    partes = []
    for f in sorted(todos):
        v = DMV.ultima_verdad(rutas.datamart, f)
        if v is None:
            continue
        maestros = MH.desde_datamart(rutas.datamart, f, conocimiento)
        prev = [x for x in DM.fechas(rutas.datamart) if x < f]
        anterior = DMV.ultima_verdad(rutas.datamart, prev[-1]) if prev else None
        partes.append(impacto_version(rutas_de(rutas, f), v, maestros, dims, hc, anterior))
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_IMPACTO)


def resumen_impacto(imp: pd.DataFrame) -> pd.DataFrame:
    if imp is None or imp.empty:
        return pd.DataFrame(columns=["cierre", "version", "consecuencia", "N"])
    return imp.groupby(["cierre", "version", "consecuencia"]).size().reset_index(name="N")


def recalcular(rutas: Rutas, motivo: str, con_terminal: bool = False, refrescar: set[str] | None = None, refrescar_facts: bool = False,
               bbg=None, fx=None, facts=None, log=None) -> DMV.Version:
    """Vuelve a correr `rutas.fecha` con la verdad actual (maestros as-of, dim, REGLAS, código) y los insumos sin fecha de la
    versión vigente (salvo `refrescar`), y lo publica como REEXPRESADA. Sin terminal, lo no cacheado queda PENDIENTE_TERMINAL."""
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
                   facturas=facturas, motivo=motivo)
    res = correr(r2, opc)
    return DMV.publicar(rutas.datamart, rutas.fecha, res.borrador, motivo, reexpresar=True)


def reexpresar_impactados(rutas: Rutas, imp: pd.DataFrame, excluir: set[str] | None = None, con_terminal: bool = False,
                          sin_cadena: bool = False, log=None, bbg_factory=None, fx=None) -> list[DMV.Version]:
    """Re-expresa en orden cronológico cada cierre con impacto (menos `excluir`); tras cada uno vuelve a evaluar la cadena."""
    hechos = []
    pendientes = sorted(set(imp["cierre"]) - set(excluir or ())) if imp is not None and len(imp) else []
    vistos = set()
    while pendientes:
        f = pendientes.pop(0)
        if f in vistos:
            continue
        vistos.add(f)
        motivo = "; ".join(f"{c}×{n}" for c, n in imp[imp["cierre"].eq(f)].groupby("consecuencia").size().items()) or "cadena"
        rf = rutas_de(rutas, f)
        v = recalcular(rf, f"auto: {motivo}", con_terminal, bbg=bbg_factory(rf) if bbg_factory else None, fx=fx, log=log)
        hechos.append(v)
        if log:
            log.info("re-expresado %s (%s)", v.etiqueta, v.completitud)
        if not sin_cadena:
            imp = impacto(rutas)
            for g in sorted(set(imp.loc[imp["cierre"] > f, "cierre"]) - set(excluir or ()) - vistos):
                if g not in pendientes:
                    pendientes.append(g)
            pendientes.sort()
    return hechos
