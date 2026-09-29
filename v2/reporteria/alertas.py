"""Alertas estructurales (emitidas por el pipeline) y, más adelante, motor por reglas de REGLAS.xlsx."""
from __future__ import annotations

import pandas as pd

from .modelo import COLS_ALERTA

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
        "Detalle": detalle,
    })
    return out[COLS_ALERTA]


def juntar(*partes: pd.DataFrame) -> pd.DataFrame:
    partes = [p for p in partes if p is not None and not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else vacias()
