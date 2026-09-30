"""Orquestación: lee, clasifica, junta candidatos de cada fuente, elige, escribe. Todo en memoria."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import agregados, alertas, cascada, clasificacion, overrides, salida, universo
from .config import MAX_DIAS_ATRAS_PARIDADES, RISK_COUNTRY_TO_LOCAL_CCY, STRONG_CCY, SUFIJOS_SERIE
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


def _opcional(nombre, ruta, lector, log, al):
    if ruta is None or not Path(ruta).exists():
        log.warning("%s: no encontrado — se omite", nombre)
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle=f"{nombre}: {ruta}", ambito="CORRIDA"))
        return None
    return lector(ruta)


def _facturas(opciones: Opciones, rutas: Rutas, log, al) -> pd.DataFrame | None:
    """Base de Facts (caché → túnel) y, si no hay tablas, el RPT en Excel; sin nada → INSUMO_FALTANTE."""
    tablas = {}
    try:
        tablas = _facts(opciones, rutas).tablas(rutas.fecha)
    except Exception as e:                     # sin clave, sin ssh, sin red…: se sigue con Excel/caché
        log.warning("Facts no disponible (%s): se usa FACTURAS_%s.xlsx si existe", e, rutas.fecha)
        al.append(alertas.emitir("FACTS_SIN_CONEXION", "ALTA", detalle=f"base de facturas: {e}", ambito="CORRIDA"))
    if tablas:
        try:
            t = normalizar_tablas(tablas)
            rpt = facturas_al_cierre(t, rutas.settle)
        except ValueError as e:                # columnas o unidades inesperadas en la base: se avisa y se sigue con Excel
            log.warning("Facts con tablas inválidas (%s): se usa FACTURAS_%s.xlsx si existe", e, rutas.fecha)
            al.append(alertas.emitir("FACTS_INVALIDO", "ALTA", detalle=str(e), ambito="CORRIDA"))
            return _opcional("FACTURAS", rutas.facturas, leer_facturas, log, al)
        log.info("FACTS: %d facturas, %d prórrogas, %d cambios; vivas al cierre %s: %d (tasa de prórroga: %d, cambios revertidos: %d)",
                 len(t["facturas"]), len(t["prorrogas"]), len(t["cambios"]), rutas.fecha, len(rpt),
                 int(rpt["tasa_origen"].eq("PRORROGA").sum()), int((rpt["cambios_revertidos"] > 0).sum()))
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


def _cartera_anterior(rutas: Rutas, log) -> pd.DataFrame | None:
    p = rutas.reporte_anterior
    if p is None or not p.exists():
        log.info("cierre anterior: %s — sin cartera previa (hedge y alertas temporales por regla)", rutas.fecha_ant or "ninguno")
        return None
    ant = pd.read_excel(p, sheet_name="cartera_final")
    log.info("cierre anterior %s: %d posiciones", rutas.fecha_ant, len(ant))
    return ant


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
        if not Path(ruta).exists():
            raise FileNotFoundError(f"Input obligatorio {nombre} no encontrado: {ruta}")
    al: list[pd.DataFrame] = []

    cubo = leer_cubo(rutas.cubo)
    bd_funds = M.leer_bd_funds(rutas.bd_funds)
    fondos_validos = set(bd_funds["ID_Fund"]) | set(cubo["ID_Fund"])
    reglas = leer_reglas(rutas.reglas, fondos_validos)
    log.info("CUBO: %d filas, fondos %s", len(cubo), sorted(cubo["ID_Fund"].unique()))
    pos, a = universo.armar_universo(cubo, M.leer_bd_instrumentos(rutas.bd_instr), bd_funds, M.leer_bd_monedas(rutas.bd_monedas))
    al.append(a)
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Risk_Currency", "Risk_Country", "Indice"))
    al.append(a)
    sufijos = tuple(x.strip() for x in str(reglas.parametros["sufijos_serie"]).split(";") if x.strip()) if "sufijos_serie" in reglas.parametros else SUFIJOS_SERIE
    pos = universo.marcar_familias(pos, sufijos)
    ant = _cartera_anterior(rutas, log)
    pos, a = universo.asignar_hedge(pos, reglas.fondos, STRONG_CCY, RISK_COUNTRY_TO_LOCAL_CCY, ant)
    al.append(a)
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Hedge_Currency",))
    al.append(a)

    fx = {}
    for p in rutas.fx_exposure:
        fid = M.fondo_de_fx_exposure(p, bd_funds)
        if fid is None:
            al.append(alertas.emitir("FX_EXPOSURE_SIN_FONDO", "INFO", detalle=p.name, ambito="CORRIDA"))
        else:
            fx[fid] = M.leer_fx_exposure(p)
    pos, a = clasificacion.clasificar(pos, M.leer_bd_balance_sheet(rutas.bd_balance), reglas.buckets, reglas.clasificacion, fx)
    al.append(a)
    pos, a = overrides.aplicar_atributos(pos, reglas.overrides_atributo, rutas.settle, ("Bucket",))
    al.append(a)
    trat = reglas.buckets.set_index("Bucket")["Tratamiento"]
    ovb = pos["Bucket_Origen"].eq("OVERRIDE")
    pos.loc[ovb, "Tratamiento"] = pos.loc[ovb, "Bucket"].map(trat).fillna("CASCADA")
    log.info("clasificación: %s", pos["Tratamiento"].value_counts().to_dict())

    bbg = _bloomberg(opciones, rutas)
    cands = []
    c, a = candidatos_cajas(pos, reglas.cajas, bbg, rutas.fecha); cands.append(c); al.append(a)
    rpt = _facturas(opciones, rutas, log, al)
    homol = _opcional("HOMOL_INSTRUMENTOS", rutas.homol, lambda p: M.leer_homol(p, "GENEVA"), log, al)
    homol_funds = _opcional("HOMOL_FUNDS", rutas.homol_funds, M.leer_homol_funds, log, al)
    # El RPT puede nombrar al fondo como en HOMOL_FUNDS (MRentaCLP) o como en BD_FUNDS (MRCLP): se aceptan ambos
    mapa_fondos = {str(k).upper(): int(v) for k, v in zip(bd_funds["FundShortName"], bd_funds["ID_Fund"])}
    if homol_funds is not None:
        mapa_fondos.update({str(k).upper(): int(v) for k, v in zip(homol_funds["Portfolio"], homol_funds["ID_Fund"])})
    c, a = candidatos_facturas(
        pos, rpt, dict(zip(homol["SourceInvestment"], homol["ID_Instrumento"])) if homol is not None else {},
        mapa_fondos, rutas.settle, reglas.parametros.get("factura_tolerancia_monto", 0.01))
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
    if rutas.excepciones:
        flujos, a = leer_excepciones(rutas.excepciones, rutas.settle); al.append(a)
        c, tds, a = candidatos_excepciones(pos, flujos, fx_bee, fx_par, rutas.settle, _escala_cfg(reglas.parametros))
        cands.append(c); al.append(a)
        log.info("EXCEPCIONES: %d PK2 con flujos, %d posiciones evaluadas", len(flujos), len(c))
    else:
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle="EXCEPCIONES*.xlsx: ninguno en MANUALES", ambito="CORRIDA"))
    jpm = _opcional("JPM", rutas.jpm, leer_jpm, log, al)
    c, a = candidatos_jpm(pos, jpm); cands.append(c); al.append(a)
    ra = _opcional("RA_TIR", rutas.ra, lambda p: leer_ra(p, str(reglas.parametros.get("hoja_ra", "")) or hoja_ra(rutas.fecha)), log, al)
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

    pos, cand, a = cascada.elegir(pos, cand, defaulted, reglas.defaulteados, rutas.settle, yt_default)
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
        al_inc = alertas.emitir("AGREGADO_INCONSISTENTE", "CRITICA", detalle="; ".join(inconsistencias["Problema"].astype(str)), ambito="CORRIDA")
        todas = alertas.juntar(todas, al_inc)
    faltantes = pos[pos["Estado"].eq("FALTANTE")]
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
    yld_flag = M.leer_yld_flag(rutas.bd_yld_flag) if Path(rutas.bd_yld_flag).exists() else {}
    pos["CalcType_exportable"] = pos["CalcType"].map(yld_flag).fillna(pos["CalcType"])

    tot = agg[agg["Dimension"].eq("TOTAL")]
    resumen = {"fecha": rutas.fecha, "fecha_ant": rutas.fecha_ant if ant is not None else None, "posiciones": len(pos),
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
    orden_sev = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "INFO": 3}
    hojas = {
        "resumen": pd.DataFrame([(k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in resumen.items()],
                                columns=["Concepto", "Valor"]),
        "agregados": agg,
        "alertas_resumen": alertas_resumen.sort_values("Severidad", key=lambda s: s.map(orden_sev)) if len(alertas_resumen) else alertas_resumen,
        "alertas": todas.sort_values(["Severidad", "Nombre"], key=lambda s: s.map(orden_sev) if s.name == "Severidad" else s) if len(todas) else todas,
        "faltantes": faltantes[[c for c in COLS_CARTERA if c in faltantes.columns]],
        "plantilla_overrides": plantilla,
        "cartera_final": pos[[c for c in COLS_CARTERA if c in pos.columns]],
        "candidatos": cand,
        "conversiones": conversiones,
        "curvas_drop": tabla_curvas,
        "td_detalle": tds,
        "reglas_aplicadas": reglas_aplicadas,
        "insumos": insumos,
    }
    excel = salida.escribir_excel(hojas, rutas.excel_final)
    (rutas.outputs / f"resumen_corrida_{rutas.fecha}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))
    log.info("listo en %.1fs → %s", resumen["segundos"], excel)
    return Resultado(pos, cand, todas, tds, getattr(bbg, 'pedidos', []), resumen, excel, agg, alertas_resumen, hojas)
