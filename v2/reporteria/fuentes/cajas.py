"""Caja y equivalentes (y pasivos financieros): yield = nivel del índice de referencia + spread; duration = días/365."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import alertas
from ..adaptadores.bbg import Bloomberg
from ..modelo import candidato, candidatos_vacios

SIN_INDICE = {"", "N.A.", "NA", "N/A", "NAN", "NONE"}


def _regla(reglas: pd.DataFrame, fid: int, pk2: str):
    por_fondo = reglas[(reglas["PK2"] == pk2) & (reglas["ID_Fund"] == fid)]
    if len(por_fondo):
        return por_fondo.iloc[0]
    global_ = reglas[(reglas["PK2"] == pk2) & reglas["ID_Fund"].isna()]
    return global_.iloc[0] if len(global_) else None


def candidatos_cajas(pos: pd.DataFrame, reglas: pd.DataFrame, bbg: Bloomberg, fecha: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    obj = pos[pos["Tratamiento"].eq("CAJA")]
    if obj.empty:
        return candidatos_vacios(), alertas.vacias()
    tickers = sorted(t for t in reglas["Indice_Referencia"].astype(str).str.strip().unique() if t.upper() not in SIN_INDICE)
    niveles = bbg.historico(tickers, "PX_LAST", fecha) if tickers else pd.Series(dtype=float)

    filas, sin_regla, sin_valor, sin_nivel = [], [], [], []
    for _, p in obj.iterrows():
        r = _regla(reglas, int(p["ID_Fund"]), str(p["PK2"]))
        if r is None:
            sin_regla.append(p)
            filas.append(candidato(p, "CAJA", 0.0, 0.0, origen="SIN_REGLA", valido=True, detalle="sin fila en REGLAS/cajas"))
            continue
        idx = str(r["Indice_Referencia"]).strip()
        spread, dias = r["Spread_Anual"], r["Dias"]
        if pd.isna(spread) and pd.isna(dias) and idx.upper() in SIN_INDICE:
            sin_valor.append(p)
        nivel = 0.0
        if idx.upper() not in SIN_INDICE:
            if idx in niveles.index and pd.notna(niveles[idx]):
                nivel = float(niveles[idx]) / 100          # BBG entrega % → decimal
            else:
                sin_nivel.append({**p.to_dict(), "Valor": idx})
        yld = nivel + (0.0 if pd.isna(spread) else float(spread))
        dur = 0.0 if pd.isna(dias) else float(dias) / 365
        filas.append(candidato(p, "CAJA", yld, dur, moneda=str(p.get("Risk_Currency", "")), origen="REGLAS/cajas",
                               detalle=f"indice={idx or 'N.A.'} nivel={nivel:.6f} spread={0.0 if pd.isna(spread) else spread} dias={dias}"))
    al = alertas.juntar(
        alertas.emitir("CAJA_SIN_REGLA", "ALTA", pd.DataFrame(sin_regla), "posición de caja sin fila en REGLAS/cajas: entra a yield 0", valor="TotalMVal") if sin_regla else None,
        alertas.emitir("CAJA_SIN_VALOR", "MEDIA", pd.DataFrame(sin_valor), "fila de REGLAS/cajas sin índice, spread ni días: yield 0", valor="TotalMVal") if sin_valor else None,
        alertas.emitir("INDICE_SIN_NIVEL", "ALTA", pd.DataFrame(sin_nivel), "índice de referencia sin nivel en Bloomberg/caché: se usa solo el spread", valor="Valor") if sin_nivel else None,
    )
    return pd.DataFrame(filas), al


def depurar_sin_regla(al: pd.DataFrame, pos: pd.DataFrame) -> pd.DataFrame:
    """Tras `cascada.elegir`: CAJA_SIN_REGLA solo si ganó el candidato sin regla (si EXCEPCIONES/RA/JPM resolvieron, se calla)."""
    if al is None or al.empty or "Origen" not in pos.columns:
        return al if al is not None else alertas.vacias()
    origen = al["Pos_ID"].map(pos.set_index("Pos_ID")["Origen"])
    return al[~(al["Nombre"].eq("CAJA_SIN_REGLA") & origen.ne("SIN_REGLA"))].reset_index(drop=True)
