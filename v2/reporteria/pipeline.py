"""Orquestación: lee, clasifica, junta candidatos de cada fuente, elige, escribe. Todo en memoria."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import alertas, cascada, clasificacion, salida, universo
from .config import Rutas
from .fuentes.facturas import candidatos_facturas
from .fuentes.reglas_fijas import candidatos_reglas_fijas
from .lectura.cubo import leer_cubo
from .lectura.maestros import leer_bd_funds, leer_bd_instrumentos
from .lectura.manuales import leer_facturas
from .lectura.reglas import leer_reglas
from .log import configurar_log
from .salida import COLS_CARTERA


@dataclass
class Opciones:
    sin_bbg: bool = False
    sin_sql: bool = False


@dataclass
class Resultado:
    posiciones: pd.DataFrame
    candidatos: pd.DataFrame
    alertas: pd.DataFrame
    resumen: dict = field(default_factory=dict)
    excel: Path | None = None


def _insumo_opcional(nombre, ruta, lector, log, al):
    if ruta is None or not Path(ruta).exists():
        log.warning("%s: no encontrado — se omite esa fuente", nombre)
        al.append(alertas.emitir("INSUMO_FALTANTE", "ALTA", detalle=f"{nombre}: {ruta}", ambito="CORRIDA"))
        return None
    return lector(ruta)


def correr(rutas: Rutas, opciones: Opciones | None = None) -> Resultado:
    opciones = opciones or Opciones()
    t0 = time.perf_counter()
    log = configurar_log(rutas.logs)
    log.info("Reportería | fecha=%s | raiz=%s", rutas.fecha, rutas.raiz)
    for nombre, ruta in rutas.obligatorias().items():
        if not Path(ruta).exists():
            raise FileNotFoundError(f"Input obligatorio {nombre} no encontrado: {ruta}")

    al: list[pd.DataFrame] = []
    reglas = leer_reglas(rutas.reglas)
    cubo = leer_cubo(rutas.cubo)
    log.info("CUBO: %d filas, %d fondos", len(cubo), cubo["ID_Fund"].nunique())
    pos, a = universo.armar_universo(cubo, leer_bd_instrumentos(rutas.bd_instr), leer_bd_funds(rutas.bd_funds))
    al.append(a)
    pos, a = clasificacion.clasificar(pos, reglas.clasificacion)
    al.append(a)
    log.info("clasificación: %s", pos["Tratamiento"].value_counts().to_dict())

    facturas = _insumo_opcional("FACTURAS", rutas.facturas, leer_facturas, log, al)
    cands = []
    c, a = candidatos_reglas_fijas(pos); cands.append(c); al.append(a)
    c, a = candidatos_facturas(pos, facturas, rutas.settle, reglas.parametros.get("factura_tolerancia_monto", 0.01))
    cands.append(c); al.append(a)
    cand = pd.concat([x for x in cands if len(x)], ignore_index=True) if any(len(x) for x in cands) else cands[0]

    pos, cand, a = cascada.elegir(pos, cand, reglas.defaulteados)
    al.append(a)
    todas = alertas.juntar(*al)
    resumen = {
        "fecha": rutas.fecha, "posiciones": len(pos),
        "estado": pos["Estado"].value_counts().to_dict(),
        "fuente": pos.loc[pos["Estado"].eq("RESUELTO"), "Fuente"].value_counts().to_dict(),
        "alertas": {f"{sev}|{nom}": int(n) for (sev, nom), n in todas.groupby(["Severidad", "Nombre"]).size().items()} if len(todas) else {},
        "segundos": round(time.perf_counter() - t0, 1),
    }
    log.info("estado: %s | fuentes: %s", resumen["estado"], resumen["fuente"])

    hojas = {
        "resumen": pd.DataFrame([(k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
                                 for k, v in resumen.items()], columns=["Concepto", "Valor"]),
        "cartera_final": pos[[c for c in COLS_CARTERA if c in pos.columns]],
        "candidatos": cand,
        "alertas": todas.sort_values(["Severidad", "Nombre"]) if len(todas) else todas,
    }
    excel = salida.escribir_excel(hojas, rutas.excel_final)
    (rutas.outputs / f"resumen_corrida_{rutas.fecha}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))
    log.info("listo en %.1fs → %s", resumen["segundos"], excel)
    return Resultado(pos, cand, todas, resumen, excel)
