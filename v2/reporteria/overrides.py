"""Overrides del operador (REGLAS): atributos antes de clasificar/consultar, valores al final. Esquema de EXCEPCIONES corporativo."""
from __future__ import annotations

import pandas as pd

from . import alertas
from .modelo import vigente


def aplicar_atributos(pos: pd.DataFrame, ov: pd.DataFrame, settle: pd.Timestamp, campos: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica las filas vigentes cuyo Field esté en `campos`. ID_Fund vacío = todos los fondos; SubID vacío = todas las monedas."""
    pos = pos.copy()
    if "Overrides" not in pos.columns:
        pos["Overrides"] = ""
    al = []
    if ov is None or ov.empty:
        return pos, alertas.vacias()
    for _, r in ov[vigente(ov, settle) & ov["Field"].isin(campos)].iterrows():
        m = pos["ID_Instrumento"].eq(r["ID_Instrumento"])
        if pd.notna(r["SubID_Instrumento"]):
            m &= pos["SubID_Instrumento"].eq(int(r["SubID_Instrumento"]))
        if pd.notna(r["ID_Fund"]):
            m &= pos["ID_Fund"].eq(int(r["ID_Fund"]))
        if not m.any():
            al.append(alertas.emitir("OVERRIDE_SIN_POSICION", "INFO", detalle=f"{r['Field']} para {r['ID_Instrumento']}-{r['SubID_Instrumento']} fondo {r['ID_Fund']}: sin posición en el CUBO", ambito="CORRIDA"))
            continue
        valor = "" if str(r["Value"]).upper() in ("SIN_HEDGE", "NINGUNO", "") else r["Value"]
        pos.loc[m, r["Field"]] = valor
        if r["Field"] == "Bucket":
            pos.loc[m, "Bucket_Origen"] = "OVERRIDE"
        pos.loc[m, "Overrides"] = pos.loc[m, "Overrides"].map(lambda s: ";".join(sorted(set(filter(None, s.split(";") + [r["Field"]])))))
    return pos, alertas.juntar(*al)


def aplicar_valores(pos: pd.DataFrame, ov: pd.DataFrame, settle: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """REGLAS/overrides_valor vigentes: pisan Yield (y Duration si viene) por encima de todo. (posiciones, candidatos OVERRIDE, alertas)."""
    pos = pos.copy()
    if "Overrides" not in pos.columns:
        pos["Overrides"] = ""
    if ov is None or ov.empty:
        return pos, pd.DataFrame(), alertas.vacias()
    from .modelo import candidato
    al, filas = [], []
    for _, r in ov[vigente(ov, settle) & ov["Yield"].notna()].iterrows():
        m = pos["ID_Instrumento"].eq(r["ID_Instrumento"])
        if pd.notna(r["SubID_Instrumento"]):
            m &= pos["SubID_Instrumento"].eq(int(r["SubID_Instrumento"]))
        if pd.notna(r["ID_Fund"]):
            m &= pos["ID_Fund"].eq(int(r["ID_Fund"]))
        m &= ~pos["Tratamiento"].eq("EXCLUIR")
        if not m.any():
            al.append(alertas.emitir("OVERRIDE_SIN_POSICION", "INFO", detalle=f"overrides_valor para {r['ID_Instrumento']}-{r['SubID_Instrumento']} fondo {r['ID_Fund']}: sin posición en el CUBO", ambito="CORRIDA"))
            continue
        dur = r["Duration"] if pd.notna(r["Duration"]) else None
        for i in pos.index[m]:
            filas.append(candidato(pos.loc[i], "OVERRIDE", float(r["Yield"]), float(dur if dur is not None else pos.at[i, "Duration"]),
                                   moneda=str(r.get("Moneda", "") or pos.at[i, "Yield_Moneda"]), origen="REGLAS/overrides_valor",
                                   valido=True, detalle=str(r.get("Comentario", "") or "")))
        pos.loc[m, "Yield"] = float(r["Yield"])
        if dur is not None:
            pos.loc[m, "Duration"] = float(dur)
        if str(r.get("Moneda", "")).strip() not in ("", "nan"):
            pos.loc[m, "Yield_Moneda"] = str(r["Moneda"]).strip().upper()
        pos.loc[m, ["Fuente", "Etapa", "Origen", "Estado", "Motivo", "CalcType"]] = ["OVERRIDE", "OVERRIDE", "REGLAS/overrides_valor", "RESUELTO", "", "PROP"]
        if "Conversion" in pos.columns:
            pos.loc[m, "Conversion"] = "OVERRIDE"
        pos.loc[m, "Overrides"] = pos.loc[m, "Overrides"].map(lambda s: ";".join(sorted(set(filter(None, s.split(";") + ["Yield"])))))
    return pos, pd.DataFrame(filas), alertas.juntar(*al)
