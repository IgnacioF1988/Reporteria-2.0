"""Orquestación: lee, clasifica, junta candidatos de cada fuente, elige, escribe. Todo en memoria."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import alertas, cascada, clasificacion, overrides, salida, universo
from .config import RISK_COUNTRY_TO_LOCAL_CCY, STRONG_CCY, SUFIJOS_SERIE
from .adaptadores.bbg import Bloomberg, CacheBloomberg, FixtureBloomberg
from .config import Rutas
from .fuentes.cajas import candidatos_cajas
from .fuentes.facturas import candidatos_facturas
from .lectura import maestros as M
from .lectura.cubo import leer_cubo
from .lectura.manuales import leer_facturas
from .lectura.reglas import leer_reglas
from .log import configurar_log
from .salida import COLS_CARTERA


@dataclass
class Opciones:
    sin_bbg: bool = False
    sin_sql: bool = False
    bbg: Bloomberg | None = None      # inyectable (tests); si None se arma según sin_bbg


@dataclass
class Resultado:
    posiciones: pd.DataFrame
    candidatos: pd.DataFrame
    alertas: pd.DataFrame
    resumen: dict = field(default_factory=dict)
    excel: Path | None = None


def _opcional(nombre, ruta, lector, log, al):
    if ruta is None or not Path(ruta).exists():
        log.warning("%s: no encontrado — se omite", nombre)
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle=f"{nombre}: {ruta}", ambito="CORRIDA"))
        return None
    return lector(ruta)


def _cartera_anterior(rutas: Rutas, log) -> pd.DataFrame | None:
    p = rutas.reporte_anterior
    if p is None or not p.exists():
        log.info("cierre anterior: %s — sin cartera previa (hedge y alertas temporales por regla)", rutas.fecha_ant or "ninguno")
        return None
    ant = pd.read_excel(p, sheet_name="cartera_final")
    log.info("cierre anterior %s: %d posiciones", rutas.fecha_ant, len(ant))
    return ant


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
    rpt = _opcional("FACTURAS", rutas.facturas, leer_facturas, log, al)
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
    cand = pd.concat([x for x in cands if len(x)], ignore_index=True) if any(len(x) for x in cands) else cands[0]

    defaulted = _opcional("DEFAULTED", rutas.defaulted, M.leer_defaulted, log, al)
    pos, cand, a = cascada.elegir(pos, cand, defaulted, reglas.defaulteados, rutas.settle)
    al.append(a)
    yld_flag = M.leer_yld_flag(rutas.bd_yld_flag) if Path(rutas.bd_yld_flag).exists() else {}
    pos["CalcType_exportable"] = pos["CalcType"].map(yld_flag).fillna(pos["CalcType"])

    todas = alertas.juntar(*al)
    resumen = {"fecha": rutas.fecha, "posiciones": len(pos), "fondos": sorted(int(x) for x in pos["ID_Fund"].unique()),
               "estado": pos["Estado"].value_counts().to_dict(),
               "fuente": pos.loc[pos["Estado"].eq("RESUELTO"), "Fuente"].value_counts().to_dict(),
               "bucket": pos["Bucket"].value_counts().to_dict(),
               "alertas": {f"{s}|{n}": int(k) for (s, n), k in todas.groupby(["Severidad", "Nombre"]).size().items()} if len(todas) else {},
               "segundos": round(time.perf_counter() - t0, 1)}
    log.info("estado: %s | fuentes: %s", resumen["estado"], resumen["fuente"])
    hojas = {
        "resumen": pd.DataFrame([(k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in resumen.items()],
                                columns=["Concepto", "Valor"]),
        "cartera_final": pos[[c for c in COLS_CARTERA if c in pos.columns]],
        "candidatos": cand,
        "alertas": todas.sort_values(["Severidad", "Nombre"]) if len(todas) else todas,
    }
    excel = salida.escribir_excel(hojas, rutas.excel_final)
    (rutas.outputs / f"resumen_corrida_{rutas.fecha}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))
    log.info("listo en %.1fs → %s", resumen["segundos"], excel)
    return Resultado(pos, cand, todas, resumen, excel)
