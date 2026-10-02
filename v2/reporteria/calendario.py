"""Calendario de cierres diarios.

Geneva produce un cierre por **cada día calendario** (T) y lo entrega el siguiente día hábil (T−1 en días hábiles; el lunes
aparecen viernes, sábado y domingo; tras un feriado, todo lo acumulado). Por eso la nocturna corre todos los días y procesa
las fechas pendientes cuyo CUBO ya existe; una fecha sin CUBO solo es anomalía cuando ya pasó el día hábil en que debía
llegar. Hábil = lunes a viernes que no esté en REGLAS/feriados (hoja opcional: Fecha, Mercado, Descripcion; cuentan las filas
con Mercado vacío o CL/CHILE/GLOBAL). El cierre mensual es el fondo-día del último día calendario del mes: no hay caso especial.
"""
from __future__ import annotations

import calendar
import datetime as dt
from pathlib import Path
from typing import Callable

import pandas as pd

MERCADOS_GLOBALES = {"", "CL", "CHILE", "GLOBAL", "*", "TODOS", "NAN", "NONE"}
ESTADOS_FECHA = ("CORRIDA", "LISTA", "SIN_CUBO", "AUN_NO_ESPERADA")


def _d(f) -> dt.date:
    if isinstance(f, dt.datetime):
        return f.date()
    if isinstance(f, dt.date):
        return f
    return pd.Timestamp(str(f)).date()


def txt(f) -> str:
    return _d(f).strftime("%Y%m%d")


def feriados_de(tabla: pd.DataFrame | None) -> set[dt.date]:
    """Fechas de la hoja REGLAS/feriados que aplican al calendario de corrida (Mercado vacío o global)."""
    if tabla is None or tabla.empty or "Fecha" not in tabla.columns:
        return set()
    t = tabla.copy()
    t.columns = [str(c).strip() for c in t.columns]
    mercado = t["Mercado"].astype(str).str.strip().str.upper() if "Mercado" in t.columns else pd.Series("", index=t.index)
    fechas = pd.to_datetime(t["Fecha"], errors="coerce")
    return {f.date() for f, m in zip(fechas, mercado) if pd.notna(f) and m in MERCADOS_GLOBALES}


def es_habil(fecha, feriados: set[dt.date] | None = None) -> bool:
    d = _d(fecha)
    return d.weekday() < 5 and d not in (feriados or set())


def siguiente_habil(fecha, feriados: set[dt.date] | None = None) -> dt.date:
    """Primer día hábil estrictamente posterior: el día en que Geneva entrega el cierre de `fecha`."""
    d = _d(fecha) + dt.timedelta(days=1)
    while not es_habil(d, feriados):
        d += dt.timedelta(days=1)
    return d


def fecha_disponible(fecha, feriados: set[dt.date] | None = None) -> dt.date:
    return siguiente_habil(fecha, feriados)


def fechas_calendario(desde, hasta) -> list[str]:
    a, b = _d(desde), _d(hasta)
    return [txt(a + dt.timedelta(days=i)) for i in range((b - a).days + 1)] if b >= a else []


def es_fin_de_mes(fecha) -> bool:
    d = _d(fecha)
    return d.day == calendar.monthrange(d.year, d.month)[1]


def pendientes(fechas_con_corrida: list[str], hoy, existe_cubo: Callable[[str], bool], feriados: set[dt.date] | None = None,
               inicio: str | None = None, max_dias: int = 400) -> pd.DataFrame:
    """Fechas de cierre hasta hoy−1 que faltan por correr, con su estado:
    LISTA (CUBO disponible) · SIN_CUBO (ya pasó el día hábil en que debía llegar) · AUN_NO_ESPERADA (llega el próximo hábil).
    Arranca el día siguiente a la última fecha con corrida (o en `inicio`); sin ninguna de las dos, en hoy−1."""
    h = _d(hoy)
    ultima = max(fechas_con_corrida) if fechas_con_corrida else None
    if inicio:
        desde = _d(inicio)
    elif ultima:
        desde = _d(ultima) + dt.timedelta(days=1)
    else:
        desde = h - dt.timedelta(days=1)
    desde = max(desde, h - dt.timedelta(days=max_dias))
    filas = []
    for f in fechas_calendario(desde, h - dt.timedelta(days=1)):
        if f in fechas_con_corrida:
            continue
        disponible = fecha_disponible(f, feriados)
        if existe_cubo(f):
            estado = "LISTA"
        elif h >= disponible:
            estado = "SIN_CUBO"
        else:
            estado = "AUN_NO_ESPERADA"
        filas.append(dict(fecha=f, estado=estado, esperada_el=txt(disponible), habil=es_habil(f, feriados), fin_de_mes=es_fin_de_mes(f)))
    return pd.DataFrame(filas, columns=["fecha", "estado", "esperada_el", "habil", "fin_de_mes"])


def existe_cubo_en(cubo_dir: Path) -> Callable[[str], bool]:
    return lambda f: (Path(cubo_dir) / f"CUBO_{f}.xlsx").exists()
