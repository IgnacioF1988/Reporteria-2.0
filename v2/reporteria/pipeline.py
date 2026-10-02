"""Orquestación: lee, clasifica, junta candidatos de cada fuente, elige, escribe. Todo en memoria."""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import agregados, alertas, cascada, clasificacion, datamart, dim, maestros_hist as MH, overrides, publicacion, salida, universo
from .config import MAX_DIAS_ATRAS_PARIDADES, RISK_COUNTRY_TO_LOCAL_CCY, STRONG_CCY, SUFIJOS_SERIE
from .adaptadores import datamart as DM
from .adaptadores import dim as DIM
from .adaptadores.bbg import Bloomberg, CacheBloomberg, FixtureBloomberg
from .adaptadores.facts import CacheFacts, FixtureFacts, FuenteFacts
from .adaptadores.fx_sql import CacheFx, FixtureFx, FuenteFx
from .conversion import convertir
from .curvas import curvas_drops, leer_curvas_csv
from .escala import EscalaCfg
from .fx import armar_fx
from .indices import asignar_indice
from .config import Rutas
from .fuentes.bbg_yas import candidatos_bbg
from .fuentes.cajas import candidatos_cajas, depurar_sin_regla
from .fuentes.cshf import candidatos_cshf
from .fuentes.jsonl import candidatos_jsonl
from .fuentes.excepciones import candidatos_excepciones
from .fuentes.facturas import candidatos_facturas
from .fuentes.jpm import candidatos_jpm
from .fuentes.ra import candidatos_ra
from .lectura import maestros as M
from .lectura.cubo import leer_cubo
from .lectura.geneva import leer_jsonl
from .lectura.facts import facturas_al_cierre, normalizar_tablas
from .lectura.manuales import leer_excepciones, leer_facturas
from .lectura.mercado import hoja_ra, leer_jpm, leer_paridades, leer_ra
from .lectura.reglas import leer_reglas
from .log import configurar_log
from .modelo import limpiar_txt
from .salida import COLS_CARTERA


@dataclass
class Opciones:
    sin_bbg: bool = False
    sin_sql: bool = False
    sin_facts: bool = False
    bbg: Bloomberg | None = None      # inyectable (tests); si None se arma según sin_bbg
    fx: FuenteFx | None = None
    facts: FuenteFacts | None = None
    conocimiento: str | None = None   # YYYYMMDD: maestros como se conocían ese día (default: hoy)
    sin_cargar_maestros: bool = False # no registrar en el datamart las diferencias del BIX de hoy (solo leer)
    facturas: pd.DataFrame | None = None   # facturas_al_cierre ya calculadas (re-expresión desde insumos/ de una versión)
    motivo: str = ""                  # re-expresión: por qué (queda en corrida.json)
    excel: bool = True                # escribir REPORTE_{F}.xlsx (en modo diario se genera a pedido con `reporte`)
    cubo: pd.DataFrame | None = None  # CUBO ya leído (la nocturna lo valida antes para distinguir CUBO_INVALIDO de otros errores)


@dataclass
class Resultado:
    posiciones: pd.DataFrame
    candidatos: pd.DataFrame
    alertas: pd.DataFrame
    tds: pd.DataFrame = field(default_factory=pd.DataFrame)
    bbg_pedidos: list = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    excel: Path | None = None
    agregados: pd.DataFrame = field(default_factory=pd.DataFrame)
    alertas_resumen: pd.DataFrame = field(default_factory=pd.DataFrame)
    hojas: dict = field(default_factory=dict)
    borrador: Path | None = None


def _desde_maestro(nombre, ruta, tabla: pd.DataFrame, source: str | None, log, al) -> pd.DataFrame | None:
    """HOMOL desde el maestro as-of; vacío y sin archivo → INSUMO_FALTANTE como antes."""
    t = tabla[tabla["Source"].eq(source)] if source and len(tabla) else tabla
    if t is None or t.empty:
        log.warning("%s: sin filas en el datamart ni archivo en %s — se omite", nombre, ruta)
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle=f"{nombre}: {ruta}", ambito="CORRIDA"))
        return None
    return t.reset_index(drop=True)


def _opcional(nombre, ruta, lector, log, al):
    """Insumo opcional: ausente → INSUMO_FALTANTE; ilegible (archivo roto, a medio copiar, columnas cambiadas) → INSUMO_INVALIDO y esa
    fuente se apaga ese día en vez de abortar la corrida (H10e)."""
    if ruta is None or not Path(ruta).exists():
        log.warning("%s: no encontrado — se omite", nombre)
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle=f"{nombre}: {ruta}", ambito="CORRIDA"))
        return None
    try:
        return lector(ruta)
    except Exception as e:                      # noqa: BLE001 — un insumo corrupto no tumba a los fondos que no lo necesitan
        log.error("%s: ilegible (%s: %s) — se omite esa fuente", nombre, type(e).__name__, str(e)[:200])
        al.append(alertas.emitir("INSUMO_INVALIDO", "ALTA", detalle=f"{nombre}: {Path(ruta).name}: {type(e).__name__}: {str(e)[:200]}", ambito="CORRIDA"))
        return None


def _facturas(opciones: Opciones, rutas: Rutas, log, al) -> pd.DataFrame | None:
    """Base de Facts (caché → túnel) y, si no hay tablas, el RPT en Excel; sin nada → INSUMO_FALTANTE."""
    if opciones.facturas is not None:
        log.info("FACTURAS: %d filas inyectadas (insumos de la versión publicada)", len(opciones.facturas))
        return opciones.facturas
    tablas = {}
    try:
        tablas = _facts(opciones, rutas).tablas(rutas.fecha)
    except Exception as e:                     # sin clave, sin ssh, sin red…: se sigue con Excel/caché
        log.warning("Facts no disponible (%s): se usa FACTURAS_%s.xlsx si existe", e, rutas.fecha)
        al.append(alertas.emitir("FACTS_SIN_CONEXION", "ALTA", detalle=f"base de facturas: {e}", ambito="CORRIDA"))
    if tablas:
        try:
            t = normalizar_tablas(tablas)
            rpt = facturas_al_cierre(t, rutas.settle, incluir_no_vivas=True)
        except ValueError as e:                # columnas o unidades inesperadas en la base: se avisa y se sigue con Excel
            log.warning("Facts con tablas inválidas (%s): se usa FACTURAS_%s.xlsx si existe", e, rutas.fecha)
            al.append(alertas.emitir("FACTS_INVALIDO", "ALTA", detalle=str(e), ambito="CORRIDA"))
            return _opcional("FACTURAS", rutas.facturas, leer_facturas, log, al)
        viva = rpt[rpt["viva"]]
        log.info("FACTS: %d facturas, %d prórrogas, %d cambios; vivas al cierre %s: %d (tasa de prórroga: %d, cambios revertidos: %d)",
                 len(t["facturas"]), len(t["prorrogas"]), len(t["cambios"]), rutas.fecha, len(viva),
                 int(viva["tasa_origen"].eq("PRORROGA").sum()), int((viva["cambios_revertidos"] > 0).sum()))
        return rpt
    return _opcional("FACTURAS", rutas.facturas, leer_facturas, log, al)


def _escala_cfg(par: dict) -> EscalaCfg:
    base = EscalaCfg()
    return EscalaCfg(umbral_pct=float(par.get("escala_umbral_pct", base.umbral_pct)), umbral_tc=float(par.get("escala_umbral_tc", base.umbral_tc)),
                     ratio_min=float(par.get("escala_ratio_min", base.ratio_min)), ratio_max=float(par.get("escala_ratio_max", base.ratio_max)))


def _reglas_aplicadas(pos: pd.DataFrame, reglas) -> pd.DataFrame:
    """Qué filas de REGLAS actuaron en la corrida y sobre cuántas posiciones (para auditar el manual)."""
    filas = []
    origen = pos["Bucket_Origen"].astype(str)
    for _, r in reglas.clasificacion.iterrows():
        n = int(origen.eq(f"REGLA:{r['ID']}").sum()) if "ID" in r else 0
        filas.append(("clasificacion", r.get("ID", ""), f"{r['Criterio']}={r['Valor']} → {r.get('Bucket', '')}/{r.get('Tratamiento', '')}", n))
    caja = pos[pos["Fuente"].eq("CAJA")]
    for _, r in reglas.cajas.iterrows():
        m = caja["PK2"].eq(str(r["PK2"])) & (caja["ID_Fund"].eq(int(r["ID_Fund"])) if pd.notna(r["ID_Fund"]) else True)
        spread = f"{r['Spread_Anual']:+.4f}" if pd.notna(r["Spread_Anual"]) else "(sin spread)"
        dias = f"{r['Dias']:.0f}d" if pd.notna(r["Dias"]) else "(sin días)"
        filas.append(("cajas", "", f"{r['PK2']} fondo {r['ID_Fund']}: {r['Indice_Referencia'] or '(sin índice)'} {spread} / {dias}", int(m.sum())))
    for _, r in reglas.defaulteados.iterrows():
        filas.append(("defaulteados", "", f"{r['ID_Instrumento']} fondo {r['ID_Fund']} {r['Estado']}", int((pos["ID_Instrumento"].eq(r["ID_Instrumento"]) & pos["Estado_DEF"].ne("")).sum())))
    for hoja, df, campo in (("overrides_valor", reglas.overrides_valor, "Yield"), ("overrides_atributo", reglas.overrides_atributo, None)):
        for _, r in df.iterrows():
            f = campo or r["Field"]
            m = pos["ID_Instrumento"].eq(r["ID_Instrumento"]) & pos["Overrides"].astype(str).str.contains(f, regex=False)
            filas.append((hoja, "", f"{r['ID_Instrumento']}-{r['SubID_Instrumento']} fondo {r['ID_Fund']}: {f}={r.get('Value', r.get('Yield', ''))}", int(m.sum())))
    return pd.DataFrame(filas, columns=["Hoja", "ID", "Regla", "Posiciones"])


def _maestros(rutas: Rutas, opciones: Opciones, log, al: list) -> tuple[dict[str, pd.DataFrame], dict]:
    """BD_INSTRUMENTOS / HOMOL bitemporales: carga automática al datamart (base la primera vez, deltas después) y
    tabla as-of del cierre según lo conocido a `opciones.conocimiento`. Sin BIX a mano se usa el estado del datamart."""
    raiz = rutas.datamart
    base_id, base = DM.leer_base(raiz, opciones.conocimiento)
    info = {"base": base_id, "carga": None, "cambios": {}, "conocimiento": opciones.conocimiento}
    vivos = {}
    if opciones.sin_cargar_maestros and base_id is not None:      # solo lectura: el BIX (240k+370k filas) no se abre
        log.info("MAESTROS: sin leer el BIX (sin_cargar_maestros); estado del datamart, base %s", base_id)
    else:
        if Path(rutas.bd_instr).exists():
            vivos["bd_instrumentos"] = M.leer_bd_instrumentos(rutas.bd_instr)
        if Path(rutas.homol).exists():
            vivos["homol_instrumentos"] = M.leer_homol(rutas.homol)
        if Path(rutas.homol_funds).exists():
            vivos["homol_funds"] = M.leer_homol_funds(rutas.homol_funds)
    if base_id is None:
        if "bd_instrumentos" not in vivos:
            raise FileNotFoundError(f"Input obligatorio BD_INSTRUMENTOS no encontrado: {rutas.bd_instr} (y el datamart no tiene base de maestros)")
        if opciones.conocimiento and DM.bases(raiz):
            raise ValueError(f"no hay base de maestros conocida al {opciones.conocimiento} (primera: {DM.bases(raiz)[0]})")
        carga = DM.id_carga()
        base = {t: MH.normalizar_tabla(t, vivos.get(t)) for t in MH.TABLAS}
        DM.escribir_base(raiz, carga, base)
        base_id = carga
        info["base"] = info["carga"] = carga
        log.info("MAESTROS: base %s escrita en el datamart (%s)", carga, {t: len(df) for t, df in base.items()})
    cambios = DM.leer_cambios(raiz, base_id, opciones.conocimiento)
    if vivos and not opciones.sin_cargar_maestros and not opciones.conocimiento and info["carga"] is None:
        nuevos = pd.concat([MH.diff_tablas(t, MH.estado(t, base.get(t), cambios), vivos[t]) for t in vivos], ignore_index=True)
        if len(nuevos):
            carga = DM.id_carga()
            DM.escribir_cambios(raiz, carga, nuevos)
            cambios = DM.leer_cambios(raiz, base_id, opciones.conocimiento)
            info["carga"], info["cambios"] = carga, MH.resumen_cambios(nuevos)
            log.info("MAESTROS: carga %s con %d cambios %s", carga, len(nuevos), info["cambios"])
            al.append(alertas.emitir("MAESTRO_CAMBIOS", "INFO", detalle=f"carga {carga}: {info['cambios']}", ambito="CORRIDA"))
        else:
            log.info("MAESTROS: BIX igual al estado del datamart (base %s, %d cambios previos)", base_id, len(cambios))
    elif not vivos and not opciones.sin_cargar_maestros:
        log.warning("MAESTROS: BIX no accesible; se usa el estado del datamart (base %s, %d cambios)", base_id, len(cambios))
    vig = DM.leer_vigencias(raiz)
    tablas = {t: MH.maestro_asof(t, base.get(t), cambios, vig, rutas.fecha, opciones.conocimiento) for t in MH.TABLAS}
    nv = int(len(MH.vigentes(vig, opciones.conocimiento)))
    info["declaraciones"] = nv
    log.info("MAESTROS as-of %s (conocimiento %s): %s; declaraciones vigentes %d", rutas.fecha, opciones.conocimiento or "hoy",
             {t: len(df) for t, df in tablas.items()}, nv)
    return tablas, info


def _cartera_anterior(rutas: Rutas, log) -> tuple[pd.DataFrame | None, dict | None]:
    """Modo diario: última verdad **por fondo** (puede ser una fecha distinta por fondo). Modo mensual: última verdad del cierre
    anterior en el datamart; si no hay versión, el REPORTE Excel de 02_OUTPUTS (como antes)."""
    if rutas.modo == "diario":
        ant, info = datamart.anterior_por_fondo(rutas.datamart, rutas.fecha)
        if ant is not None:
            por_fecha = ant.groupby("Fecha_Ant")["ID_Fund"].nunique().to_dict()
            log.info("cierre anterior por fondo: %d posiciones de %d fondos %s", len(ant), len(info["fondos"]), por_fecha)
            return ant, info
    v = datamart.ultima_verdad(rutas.datamart, rutas.fecha_ant)
    if v is not None and (ant := v.posiciones()) is not None:
        log.info("cierre anterior %s: %d posiciones desde el datamart (%s)", rutas.fecha_ant, len(ant), v.etiqueta)
        return ant, {"origen": "DATAMART", "cierre": v.cierre, "version": v.numero, "estado": v.estado, "ts": v.ts}
    p = rutas.reporte_anterior
    if p is None or not p.exists():
        log.info("cierre anterior: %s — sin cartera previa (hedge y alertas temporales por regla)", rutas.fecha_ant or "ninguno")
        return None, None
    ant = pd.read_excel(p, sheet_name="cartera_final")
    log.info("cierre anterior %s: %d posiciones desde %s", rutas.fecha_ant, len(ant), p.name)
    return ant, {"origen": "EXCEL", "cierre": rutas.fecha_ant, "ruta": str(p)}


def _fx(opciones: Opciones, rutas: Rutas) -> FuenteFx:
    if opciones.fx is not None:
        return opciones.fx
    if opciones.sin_sql:
        return FixtureFx(rutas.cache, rutas.fecha)
    from .adaptadores.fx_sql import BeeminingFx
    return CacheFx(BeeminingFx(), rutas.cache, rutas.fecha)


def _facts(opciones: Opciones, rutas: Rutas) -> FuenteFacts:
    if opciones.facts is not None:
        return opciones.facts
    if opciones.sin_facts:
        return FixtureFacts(rutas.cache, rutas.fecha)
    from .adaptadores.facts import FactsSql
    return CacheFacts(FactsSql(), rutas.cache, rutas.fecha)


def _bloomberg(opciones: Opciones, rutas: Rutas) -> Bloomberg:
    if opciones.bbg is not None:
        return opciones.bbg
    if opciones.sin_bbg:
        return FixtureBloomberg(rutas.cache, rutas.fecha)
    from .adaptadores.bbg import XbbgBloomberg
    return CacheBloomberg(XbbgBloomberg(), rutas.cache, rutas.fecha)


def correr(rutas: Rutas, opciones: Opciones | None = None) -> Resultado:
    opciones = opciones or Opciones()
    t0 = time.perf_counter()
    log = configurar_log(rutas.logs)
    log.info("Reportería | fecha=%s | raiz=%s", rutas.fecha, rutas.raiz)
    for nombre, ruta in rutas.obligatorias().items():
        if not Path(ruta).exists() and not (nombre == "BD_INSTRUMENTOS" and DM.bases(rutas.datamart)):
            raise FileNotFoundError(f"Input obligatorio {nombre} no encontrado: {ruta}")
    al: list[pd.DataFrame] = []

    cubo = opciones.cubo if opciones.cubo is not None else leer_cubo(rutas.cubo)
    dims = DIM.leer(rutas.dim)
    bd_funds = dims.bd_funds()
    nf = int(dims.clasificacion["ID_Fund"].notna().sum())
    log.info("DIM %s: clasificacion %d filas (%d genéricas, %d por fondo), importado %s", rutas.dim.name, len(dims.clasificacion),
             len(dims.clasificacion) - nf, nf, dims.meta.get("importado", "?"))
    fondos_validos = set(bd_funds["ID_Fund"]) | set(cubo["ID_Fund"])
    reglas = leer_reglas(rutas.reglas, fondos_validos)
    if rutas.modo == "diario":                      # `clave_diario` pisa a `clave` (umbrales de un día vs de un mes)
        diarios = {k[:-7]: v for k, v in reglas.parametros.items() if k.endswith("_diario")}
        if diarios:
            reglas = dataclasses.replace(reglas, parametros={**reglas.parametros, **diarios})
            log.info("parámetros diarios: %s", diarios)
    log.info("CUBO: %d filas, fondos %s", len(cubo), sorted(cubo["ID_Fund"].unique()))
    maestros, info_maestros = _maestros(rutas, opciones, log, al)
    pos, a = universo.armar_universo(cubo, maestros["bd_instrumentos"], bd_funds, dims.bd_monedas())
    al.append(a)
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Risk_Currency", "Risk_Country", "Indice"))
    al.append(a)
    sufijos = tuple(x.strip() for x in str(reglas.parametros["sufijos_serie"]).split(";") if x.strip()) if "sufijos_serie" in reglas.parametros else SUFIJOS_SERIE
    pos = universo.marcar_familias(pos, sufijos)
    ant, anterior_version = _cartera_anterior(rutas, log)
    pos, a = universo.asignar_hedge(pos, reglas.fondos, STRONG_CCY, RISK_COUNTRY_TO_LOCAL_CCY, ant)
    al.append(a)
    if ant is not None and len(ant):
        sin_historia = pos[~pos["ID_Fund"].isin(set(ant["ID_Fund"]))].drop_duplicates("ID_Fund")
        if len(sin_historia):
            al.append(alertas.emitir("SIN_HISTORIA", "INFO", sin_historia, "fondo sin cartera anterior: hedge por regla y alertas temporales no aplican", ambito="FONDO"))
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Hedge_Currency",))
    al.append(a)

    pos, a = clasificacion.clasificar(pos, dims.clasificacion, reglas.buckets, reglas.clasificacion, rutas.settle)
    al.append(a)
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Bucket",))
    al.append(a)
    trat = reglas.buckets.set_index("Bucket")["Tratamiento"]
    ovb = pos["Bucket_Origen"].eq("OVERRIDE")
    pos.loc[ovb, "Tratamiento"] = pos.loc[ovb, "Bucket"].map(trat).fillna("CASCADA")
    log.info("clasificación: %s", pos["Tratamiento"].value_counts().to_dict())

    bbg = _bloomberg(opciones, rutas)
    if getattr(getattr(bbg, "inner", bbg), "caida", False):
        log.error("BBG: terminal no disponible (%s): solo caché; lo que falte queda PENDIENTE_TERMINAL", getattr(bbg, "inner", bbg).errores[0])
    cands = []
    c, a = candidatos_cajas(pos, reglas.cajas, bbg, rutas.fecha); cands.append(c); al.append(a)
    rpt = _facturas(opciones, rutas, log, al)
    homol = _desde_maestro("HOMOL_INSTRUMENTOS", rutas.homol, maestros["homol_instrumentos"], "GENEVA", log, al)
    homol_funds = _desde_maestro("HOMOL_FUNDS", rutas.homol_funds, maestros["homol_funds"], None, log, al)
    # El RPT puede nombrar al fondo como en HOMOL_FUNDS (MRentaCLP) o como en BD_FUNDS (MRCLP): se aceptan ambos
    mapa_fondos = {str(k).upper(): int(v) for k, v in zip(bd_funds["FundShortName"], bd_funds["ID_Fund"])}
    if homol_funds is not None:
        mapa_fondos.update({str(k).upper(): int(v) for k, v in zip(homol_funds["Portfolio"], homol_funds["ID_Fund"])})
    c, a = candidatos_facturas(
        pos, rpt, dict(zip(homol["SourceInvestment"], homol["ID_Instrumento"])) if homol is not None else {},
        mapa_fondos, rutas.settle, reglas.parametros.get("factura_tolerancia_monto", 0.01), reglas.parametros)
    cands.append(c); al.append(a)
    # ── Fuentes de archivo para la cascada: EXCEPCIONES (PM), JPM, RA ──
    try:
        fx_bee = _fx(opciones, rutas).ultimos(rutas.fecha)
    except Exception as e:                     # sin pyodbc, sin red, credenciales…: se sigue con paridades y caché
        log.warning("beemining no disponible (%s): se usan solo paridades y caché", e)
        fx_bee = FixtureFx(rutas.cache, rutas.fecha).ultimos(rutas.fecha)
    if not fx_bee:
        al.append(alertas.emitir("FX_SIN_BEEMINING", "ALTA", detalle="sin FX de beemining ni caché: solo paridades", ambito="CORRIDA"))
    par = _opcional("Paridades", rutas.paridades, lambda p: leer_paridades(p, rutas.settle, MAX_DIAS_ATRAS_PARIDADES), log, al) or {}
    fx_bee, fx_par = armar_fx(fx_bee, par)
    tds = pd.DataFrame()
    flujos = None
    if rutas.excepciones:
        try:
            flujos, a = leer_excepciones(rutas.excepciones, rutas.settle); al.append(a)
        except Exception as e:                  # noqa: BLE001 — EXCEPCIONES corrupto: se apaga la fuente, no la corrida
            log.error("EXCEPCIONES: ilegible (%s: %s) — se omite esa fuente", type(e).__name__, str(e)[:200])
            al.append(alertas.emitir("INSUMO_INVALIDO", "ALTA", detalle=f"EXCEPCIONES: {type(e).__name__}: {str(e)[:200]}", ambito="CORRIDA"))
    if flujos is not None:
        c, tds, a = candidatos_excepciones(pos, flujos, fx_bee, fx_par, rutas.settle, _escala_cfg(reglas.parametros))
        cands.append(c); al.append(a)
        log.info("EXCEPCIONES: %d PK2 con flujos, %d posiciones evaluadas", len(flujos), len(c))
    elif not rutas.excepciones:
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle="EXCEPCIONES*.xlsx: ninguno en MANUALES", ambito="CORRIDA"))
    jpm = _opcional("JPM", rutas.jpm, leer_jpm, log, al)
    c, a = candidatos_jpm(pos, jpm); cands.append(c); al.append(a)
    hoja = str(reglas.parametros.get("hoja_ra", "")) or (None if rutas.ra.name != "RA_TIR.xlsx" else hoja_ra(rutas.fecha))   # fechado: primera hoja
    ra = _opcional("RA_TIR", rutas.ra, lambda p: leer_ra(p, hoja, respaldo=rutas.modo == "diario"), log, al)
    c, a = candidatos_ra(pos, ra, reglas.parametros); cands.append(c); al.append(a)
    cand = pd.concat([x for x in cands if len(x)], ignore_index=True) if any(len(x) for x in cands) else cands[0]
    cand, a = cascada.validar_candidatos(cand, reglas.parametros); al.append(a)

    # ── Terminal y TD propias: solo para lo que sigue pendiente (ahorro de consultas) ──
    defaulted = _opcional("DEFAULTED", rutas.defaulted, M.leer_defaulted, log, al)
    es_def = cascada.defaults(pos, defaulted, reglas.defaulteados, rutas.settle).ne("")
    yt_default = int(reglas.parametros.get("yield_type_default", 15))
    pend = cascada.pendientes_tras(pos, cand, es_def)
    log.info("pendientes para Bloomberg YAS: %d", len(pend))
    c, xccy, a = candidatos_bbg(pos, pend, bbg, rutas.fecha, yt_default); al.append(a)
    c, a2 = cascada.validar_candidatos(c, reglas.parametros); al.append(a2)
    cand = pd.concat([cand, c], ignore_index=True) if len(c) else cand
    pos["Yield_XCCY"] = pos["Pos_ID"].map(xccy.set_index("Pos_ID")["Yield_XCCY"]) if len(xccy) else float("nan")
    pend = cascada.pendientes_tras(pos, cand, es_def)
    log.info("pendientes para CSHF (DES_CASH_FLOW): %d", len(pend))
    c, t, a = candidatos_cshf(pos, pend, bbg, fx_bee, fx_par, rutas.settle, _escala_cfg(reglas.parametros)); al.append(a)
    c, a2 = cascada.validar_candidatos(c, reglas.parametros); al.append(a2)
    cand = pd.concat([cand, c], ignore_index=True) if len(c) else cand
    tds = pd.concat([tds, t], ignore_index=True) if len(t) else tds
    pend = cascada.pendientes_tras(pos, cand, es_def)
    log.info("pendientes para JSONL (Geneva): %d", len(pend))
    recs = _opcional("bond_schedule", rutas.jsonl, leer_jsonl, log, al) or {}
    c, t, a = candidatos_jsonl(pos, pend, recs, homol, fx_bee, fx_par, rutas.settle, _escala_cfg(reglas.parametros)); al.append(a)
    c, a2 = cascada.validar_candidatos(c, reglas.parametros); al.append(a2)
    cand = pd.concat([cand, c], ignore_index=True) if len(c) else cand
    tds = pd.concat([tds, t], ignore_index=True) if len(t) else tds

    pos, cand, a = cascada.elegir(pos, cand, defaulted, reglas.defaulteados, rutas.settle, yt_default, reglas.parametros)
    al.append(a)
    pos, a = cascada.marcar_pendientes_terminal(pos, getattr(bbg, "sin_cache", []), rutas.fecha)
    if (n := int(pos["Estado"].eq("PENDIENTE_TERMINAL").sum())):
        log.warning("PENDIENTE_TERMINAL: %d posiciones sin caché Bloomberg al %s (ver hoja pendientes; recalcular --con-terminal)", n, rutas.fecha)
    al.append(a)

    # ── H4: índice, conversión a moneda del fondo (breakeven / XCCY / drop) y overrides de valor ──
    pos, a = asignar_indice(pos, bbg, rutas.fecha); al.append(a)
    curvas_real = _opcional("Carga_Indexes", rutas.indexes, lambda p: leer_curvas_csv(p, rutas.settle), log, al) or {}
    curvas_nom = _opcional("CurvasSoberanas", rutas.curvas_sob, lambda p: leer_curvas_csv(p, rutas.settle), log, al) or {}
    hedged = pos[limpiar_txt(pos["Hedge_Currency"]).ne("") & pos["Estado"].eq("RESUELTO") & pos["Risk_Currency"].eq("USD")]
    monedas_drop = set(limpiar_txt(hedged["Hedge_Currency"]).str.upper())
    cdrop, tabla_curvas, a = curvas_drops(bbg, monedas_drop, rutas.fecha); al.append(a)
    pos, conversiones, a = convertir(pos, curvas_real, curvas_nom, cdrop, reglas.parametros); al.append(a)
    log.info("conversiones: %s", conversiones["Resultado"].value_counts().to_dict() if len(conversiones) else {})
    pos, c, a = overrides.aplicar_valores(pos, reglas.overrides_valor, rutas.settle); al.append(a)
    cand = pd.concat([cand, c], ignore_index=True) if len(c) else cand

    # ── H5: alertas por reglas, agregados y salida completa ──
    pos = alertas.unir_anterior(pos, ant)
    al_reglas, res_reglas = alertas.evaluar(pos, reglas.alertas, reglas.parametros, ant is not None)
    avisos_bbg = getattr(getattr(bbg, "inner", bbg), "avisos", [])
    if avisos_bbg:
        log.warning("Bloomberg rechazó %d pedidos (tickers inválidos u overrides no aceptados); quedan sin dato", len(avisos_bbg))
        al.append(alertas.emitir("BBG_PEDIDO_RECHAZADO", "INFO", detalle=f"{len(avisos_bbg)} pedidos; primero: {avisos_bbg[0]}", ambito="CORRIDA"))
    if getattr(getattr(bbg, "inner", bbg), "caida", False):
        errores = getattr(bbg, "inner", bbg).errores
        log.error("Bloomberg no respondió (%s): lo pendiente de BBG/CSHF queda FALTANTE y las curvas de drop vacías", errores[0])
        al.append(alertas.emitir("BBG_SIN_CONEXION", "CRITICA", detalle=f"terminal sin sesión/API en la estación: {errores[0]}", ambito="CORRIDA"))
    estructurales = alertas.ajustar_estructurales(depurar_sin_regla(alertas.juntar(*al), pos), reglas.alertas)
    todas = alertas.juntar(estructurales, al_reglas)
    alertas_resumen = pd.concat([res_reglas, alertas.resumen_estructurales(estructurales, pos)], ignore_index=True)
    log.info("alertas: %s", {k: int(v) for k, v in todas["Severidad"].value_counts().items()} if len(todas) else {})
    agg = agregados.aw_dw(pos)
    inconsistencias = agregados.verificar(agg)
    if len(inconsistencias):
        al_inc = alertas.emitir("AGREGADO_INCONSISTENTE", "CRITICA", inconsistencias, detalle="Problema", ambito="FONDO", valor="Valor")
        todas = alertas.juntar(todas, al_inc)
    faltantes = pos[pos["Estado"].isin(["FALTANTE", "PENDIENTE_TERMINAL"])]
    pendientes = pos.loc[pos["Estado"].eq("PENDIENTE_TERMINAL"), ["Pos_ID", "ID_Fund", "Fondo", "PK2", "Name_Instrumento", "ISIN", "Bucket", "TotalMVal", "Pedido_BBG", "Motivo"]]
    plantilla = pd.DataFrame({
        "ID_Fund": faltantes["ID_Fund"].values, "ID_Instrumento": faltantes["ID_Instrumento"].values,
        "SubID_Instrumento": faltantes["SubID_Instrumento"].values, "Yield": float("nan"), "Duration": float("nan"),
        "Fecha_Desde": rutas.settle.strftime("%Y-%m-%d"), "Fecha_Fin": "", "Fuente": "", "Comentario": "",
        "_PK2": faltantes["PK2"].values, "_Nombre": faltantes["Name_Instrumento"].values, "_Bucket": faltantes["Bucket"].values,
        "_Risk_Currency": faltantes["Risk_Currency"].values, "_TotalMVal": faltantes["TotalMVal"].values, "_Motivo": faltantes["Motivo"].values,
    }).sort_values("_TotalMVal", key=lambda s: s.abs(), ascending=False)
    insumos = pd.DataFrame([(k, str(v), "OK" if Path(v).exists() else "FALTA") for k, v in rutas.obligatorias().items()] +
                           [(k, str(v) if v else "", "OK" if v and Path(v).exists() else "OPCIONAL_AUSENTE") for k, v in rutas.opcionales().items()],
                           columns=["Insumo", "Ruta", "Estado"])
    reglas_aplicadas = _reglas_aplicadas(pos, reglas)
    yld_flag = dims.yld_flag_dict()
    pos["CalcType_exportable"] = pos["CalcType"].map(yld_flag).fillna(pos["CalcType"])

    tot = agg[agg["Dimension"].eq("TOTAL")]
    resumen = {"fecha": rutas.fecha, "fecha_ant": rutas.fecha_ant if ant is not None else None, "anterior_version": anterior_version, "maestros": info_maestros, "posiciones": len(pos),
               "fondos": sorted(int(x) for x in pos["ID_Fund"].unique()),
               "estado": pos["Estado"].value_counts().to_dict(),
               "fuente": pos.loc[pos["Estado"].eq("RESUELTO"), "Fuente"].value_counts().to_dict(),
               "conversion": pos.loc[pos["Conversion"].ne(""), "Conversion"].value_counts().to_dict(),
               "bucket": pos["Bucket"].value_counts().to_dict(),
               "cobertura_mv_activos": {int(r["ID_Fund"]): round(float(r["Cobertura"]), 4) for _, r in tot[tot["Nivel"].eq("ACTIVOS")].iterrows()},
               "aw_dw_patrimonio": {int(r["ID_Fund"]): [round(float(r["AW"]), 6), round(float(r["DW"]), 6) if pd.notna(r["DW"]) else None]
                                    for _, r in tot[tot["Nivel"].eq("PATRIMONIO")].iterrows()},
               "alertas": {f"{s}|{n}": int(k) for (s, n), k in todas.groupby(["Severidad", "Nombre"]).size().items()} if len(todas) else {},
               "alertas_inactivas": res_reglas.loc[~res_reglas["Estado"].eq("ACTIVA"), "Nombre"].tolist() if len(res_reglas) else [],
               "segundos": round(time.perf_counter() - t0, 1)}
    log.info("estado: %s | fuentes: %s", resumen["estado"], resumen["fuente"])
    readiness = publicacion.evaluar(pos, todas, resumen, insumos, reglas.alertas, reglas.parametros, publicacion.esperados(dims.fondos))
    resumen["publicacion"] = {"listos": [int(x) for x in readiness.loc[readiness["Listo"], "ID_Fund"]],
                              "bloqueados": {int(r["ID_Fund"]): r["Bloqueos"] for _, r in readiness[~readiness["Listo"]].iterrows()}}
    log.info("publicación por fondo: %d listos, %d con bloqueos %s", len(resumen["publicacion"]["listos"]), len(resumen["publicacion"]["bloqueados"]),
             resumen["publicacion"]["bloqueados"] if resumen["publicacion"]["bloqueados"] else "")
    orden_sev = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "INFO": 3}
    hojas = {
        "resumen": pd.DataFrame([(k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in resumen.items()],
                                columns=["Concepto", "Valor"]),
        "agregados": agg,
        "alertas_resumen": alertas_resumen.sort_values("Severidad", key=lambda s: s.map(orden_sev)) if len(alertas_resumen) else alertas_resumen,
        "alertas": todas.sort_values(["Severidad", "Nombre"], key=lambda s: s.map(orden_sev) if s.name == "Severidad" else s) if len(todas) else todas,
        "faltantes": faltantes[[c for c in COLS_CARTERA if c in faltantes.columns]],
        "plantilla_overrides": plantilla,
        "plantilla_cajas": salida.plantilla_cajas(pos, reglas.cajas, rutas.settle),
        "plantilla_dim": dim.plantilla_sin_dim(pos),
        "pendientes": pendientes,
        "cartera_final": pos[[c for c in COLS_CARTERA if c in pos.columns]],
        "candidatos": cand,
        "conversiones": conversiones,
        "curvas_drop": tabla_curvas,
        "td_detalle": tds,
        "reglas_aplicadas": reglas_aplicadas,
        "insumos": insumos,
        "publicacion": readiness,
    }
    excel = salida.escribir_excel(hojas, rutas.excel_final) if opciones.excel else None
    rutas.outputs.mkdir(parents=True, exist_ok=True)
    (rutas.outputs / f"resumen_corrida_{rutas.fecha}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))
    borrador = _escribir_borrador(rutas, opciones, hojas, pos, resumen, anterior_version, rpt, dims, log)
    log.info("listo en %.1fs → %s", resumen["segundos"], excel)
    return Resultado(pos, cand, todas, tds, getattr(bbg, 'pedidos', []), resumen, excel, agg, alertas_resumen, hojas, borrador)


def _escribir_borrador(rutas: Rutas, opciones: Opciones, hojas: dict, pos: pd.DataFrame, resumen: dict, anterior_version: dict | None,
                       facturas: pd.DataFrame | None, dims, log) -> Path:
    """Versión completa de la corrida (formato del datamart) en 02_OUTPUTS/{F}/borradores/borrador_{ts}/: `publicar` la copia al datamart."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    dir_ = rutas.borradores / f"borrador_{ts}"
    log.info("escribiendo borrador %s (hojas Parquet, copia de insumos, huella de la caché)…", dir_.name)
    pendientes = int(pos["Estado"].eq("PENDIENTE_TERMINAL").sum()) if "Estado" in pos.columns else 0
    corrida = {"fecha": rutas.fecha, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "estado": "BORRADOR", "motivo": opciones.motivo, "version": None,
               "completitud": "PARCIAL" if pendientes else "COMPLETA", "n_pendientes": pendientes,
               "opciones": {"sin_bbg": opciones.sin_bbg, "sin_sql": opciones.sin_sql, "sin_facts": opciones.sin_facts},
               "anterior_version": anterior_version, "fecha_ant": resumen.get("fecha_ant"), "maestros": resumen.get("maestros"),
               "hashes": {"codigo": DM.hash_codigo(), "cubo": DM.hash_archivo(rutas.cubo), "reglas": DM.hash_archivo(rutas.reglas),
                          "dim": DM.hash_archivo(rutas.dim), "dim_importado": dims.meta.get("importado", "")},
               "resumen": {k: resumen[k] for k in ("posiciones", "estado", "fuente", "segundos") if k in resumen}}
    DM.escribir_version(dir_, hojas, corrida, pos)
    DM.copiar_insumos(rutas, dir_, {"facturas_al_cierre": facturas} if facturas is not None else None)
    log.info("borrador %s (publicar con: reporteria publicar --fecha %s)", dir_.name, rutas.fecha)
    return dir_


# ── aislamiento por fondo (nocturna): un fondo que revienta la corrida no frena a los demás ────────────────────────────
def _bisectar(rutas: Rutas, opciones: Opciones, cubo: pd.DataFrame, fondos: list[int], log) -> dict[int, str]:
    """Busca por bisección los fondos cuyo subconjunto hace fallar `correr`. Devuelve {ID_Fund: error}."""
    sub = cubo[cubo["ID_Fund"].isin(fondos)]
    try:
        correr(rutas, dataclasses.replace(opciones, cubo=sub, sin_cargar_maestros=True, excel=False))
        return {}
    except Exception as e:                      # noqa: BLE001
        if len(fondos) == 1:
            if log:
                log.error("fondo %s en cuarentena: %s: %s", fondos[0], type(e).__name__, str(e)[:200])
            return {int(fondos[0]): f"{type(e).__name__}: {str(e)[:200]}"}
        mitad = len(fondos) // 2
        return {**_bisectar(rutas, opciones, cubo, fondos[:mitad], log), **_bisectar(rutas, opciones, cubo, fondos[mitad:], log)}


def correr_aislado(rutas: Rutas, opciones: Opciones | None = None, log=None) -> tuple[Resultado, dict[int, str]]:
    """`correr` con cuarentena por fondo: si el frame completo falla, bisecta por fondo, aísla a los culpables y vuelve a correr el
    resto. Devuelve (Resultado, {ID_Fund culpable: error}). Si falla todo (CUBO/REGLAS/dim) o no hay un culpable aislable, relanza."""
    opciones = opciones or Opciones()
    cubo = opciones.cubo if opciones.cubo is not None else leer_cubo(rutas.cubo)
    opciones = dataclasses.replace(opciones, cubo=cubo)
    try:
        return correr(rutas, opciones), {}
    except Exception as e:                      # noqa: BLE001
        fondos = sorted(int(x) for x in cubo["ID_Fund"].dropna().unique())
        if len(fondos) <= 1:
            raise
        if log:
            log.error("corrida completa falló (%s: %s): bisectando por fondo", type(e).__name__, str(e)[:200])
        culpables = _bisectar(rutas, opciones, cubo, fondos, log)
        if not culpables or len(culpables) == len(fondos):
            raise
        resto = cubo[~cubo["ID_Fund"].isin(culpables)]
        res = correr(rutas, dataclasses.replace(opciones, cubo=resto, sin_cargar_maestros=True))   # la cuarentena queda en estado.json (ERROR_FONDO)
        return res, culpables
