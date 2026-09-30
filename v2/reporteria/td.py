"""Tablas de desarrollo: normalización de la TD de Bloomberg y construcción de la TD propia desde Geneva.

Geneva (validado en el legacy con casos reales):
  · PerShareAmount viene en BASE 100 del nominal ORIGINAL y el Factor YA está incorporado en los flujos futuros
    → se escala con OF_ef/100, nunca con el nominal vigente (aplicaría el Factor dos veces).
  · 'Coupon Frequency' viene siempre 2: la periodicidad se infiere de las fechas de los eventos Interest.
  · El devengo se ancla al ÚLTIMO CUPÓN PAGADO: el primer cupón se cobra completo (el AI ya se pagó en el flujo inicial).
  · El cupón se recalcula sobre el saldo vigente (Geneva lo trae sobre el nominal original).
  · Brasil dc=252: el test fijo/flotante es ambiguo → revisión manual.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RATIO_FIJO = (0.85, 1.15)
NO_MODELABLES = {"FLOTANTE", "REVISAR_252", "SIN_TASA", "SIN_FLUJOS"}
MOTIVO = {"FLOTANTE": "Flotante: Interest Rate no explica los cupones pagados (falta índice CDI/TIIE/TAB)",
          "REVISAR_252": "Brasil dc=252 (convención CDI): test fijo/flotante ambiguo, requiere atributos",
          "SIN_TASA": "Sin Interest Rate en el jsonl", "SIN_FLUJOS": "Sin eventos de flujo en el jsonl"}


def normalizar_td_bbg(raw: pd.DataFrame, face_default: float = 1000.0) -> tuple[pd.DataFrame, float]:
    """DES_CASH_FLOW (o su caché) → (DataFrame[Fecha, Cupon, Principal, Flujo], face). Face = Σ principal; si no viene,
    la columna Face del caché; si tampoco, face_default."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Fecha", "Cupon", "Principal", "Flujo"]), face_default
    t = raw.copy()
    t.columns = [str(c).strip().lower().replace(" ", "_") for c in t.columns]
    cf = next((c for c in ("fecha", "payment_date", "date") if c in t.columns), None)
    if cf is None:
        return pd.DataFrame(columns=["Fecha", "Cupon", "Principal", "Flujo"]), face_default
    cupon = pd.to_numeric(t.get("cupon", t.get("coupon_amount", t.get("interest_amount"))), errors="coerce")
    princ = pd.to_numeric(t.get("principal", t.get("principal_amount")), errors="coerce")
    flujo = pd.to_numeric(t["flujo"], errors="coerce") if "flujo" in t.columns else cupon.fillna(0) + princ.fillna(0)
    td = pd.DataFrame({"Fecha": pd.to_datetime(t[cf], errors="coerce"), "Cupon": cupon, "Principal": princ, "Flujo": flujo})
    td = td.dropna(subset=["Fecha", "Flujo"]).sort_values("Fecha").reset_index(drop=True)
    face = float(td["Principal"].sum()) if td["Principal"].notna().any() and td["Principal"].sum() > 0 else float("nan")
    if pd.isna(face) and "face" in t.columns and pd.to_numeric(t["face"], errors="coerce").notna().any():
        face = float(pd.to_numeric(t["face"], errors="coerce").dropna().iloc[0])
    return td, (face if pd.notna(face) and face > 0 else face_default)


def _intereses(rec: dict) -> list[tuple[pd.Timestamp, float]]:
    ev = rec.get("events") or []
    return sorted({pd.to_datetime(e["EventDate"]): float(e["PerShareAmount"])
                   for e in ev if e.get("Type") == "Interest" and "PerShareAmount" in e}.items())


def clasificar_geneva(rec: dict, settle: pd.Timestamp) -> tuple[str, float, dict]:
    """(tipo, tasa, diag): ZERO_COUPON | SINKABLE | BULLET | FLOTANTE | REVISAR_252 | SIN_TASA | SIN_FLUJOS."""
    bs, ev = rec.get("bond_specific") or {}, rec.get("events") or []
    rate, dc = bs.get("Interest Rate"), str(bs.get("Accrual Days/Year", "")).strip()
    sinks = [e for e in ev if e.get("Type") == "Sink"]
    ints = _intereses(rec)
    nz = [v for _, v in ints if abs(v) > 1e-9]
    diag = {"n_int": len(ints), "dc": dc, "Interest_Rate": rate, "ratio_obs_teo": np.nan, "n_pasados": 0}
    if not nz:
        return ("ZERO_COUPON", 0.0, diag) if (sinks or any(e.get("Type") == "Mature" for e in ev)) else ("SIN_FLUJOS", np.nan, diag)
    if dc == "252":
        return "REVISAR_252", np.nan, diag
    pasados = [x for x in ints if x[0] <= settle]
    diag["n_pasados"] = len(pasados)
    if len(pasados) >= 2 and rate:
        ratios = []
        for (d0, _), (d1, v) in zip(pasados, pasados[1:]):
            dias = (d1 - d0).days
            if dias > 0 and rate * dias / 365.0 > 0:
                ratios.append(v / (rate * dias / 365.0))
        if ratios:
            diag["ratio_obs_teo"] = float(np.median(ratios))
            if not RATIO_FIJO[0] <= diag["ratio_obs_teo"] <= RATIO_FIJO[1]:
                return "FLOTANTE", np.nan, diag
    elif not rate:
        return "SIN_TASA", np.nan, diag
    return ("SINKABLE" if sinks else "BULLET"), float(rate), diag


def inferir_periodicidad(ints) -> int | None:
    """Meses entre cupones desde las fechas reales (mediana de días). None si no alcanza."""
    if len(ints) < 3:
        return None
    med = float(np.median(np.diff([d.value for d, _ in ints]) / 8.64e13))
    if med <= 0:
        return None
    freq = min([1, 2, 4, 12], key=lambda f: abs(f - round(365.25 / med)))
    return max(1, 12 // freq)


def construir_td_geneva(rec: dict, of_ef: float, tipo: str, tasa: float, settle: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """TD futura escalada a of_ef (nominal ORIGINAL en unidades del papel). Columnas: Fecha, Cupon_base100, Capital_base100,
    Saldo_ini_base100, dias_periodo, Cupon, Capital, Flujo, Origen."""
    ev, bs = rec.get("events") or [], rec.get("bond_specific") or {}
    escala = of_ef / 100.0
    sinks = [(pd.to_datetime(e["EventDate"]), float(e.get("Percent", e.get("PerShareAmount")))) for e in ev
             if e.get("Type") == "Sink" and e.get("Percent", e.get("PerShareAmount")) is not None]
    mature = next((pd.to_datetime(e["EventDate"]) for e in ev if e.get("Type") == "Mature"), None)
    mat = pd.to_datetime(bs.get("Maturity Date"), errors="coerce")
    mat = mature if pd.isna(mat) else mat
    if mat is None or pd.isna(mat):
        return pd.DataFrame(), {}
    sink_fut, sink_pasado = {}, 0.0
    for f, pct in sinks:
        if f <= settle:
            sink_pasado += pct
        else:
            sink_fut[f.normalize()] = sink_fut.get(f.normalize(), 0.0) + pct
    cap_vigente = max(100.0 - sink_pasado, 0.0)
    if tipo == "ZERO_COUPON":
        df = pd.DataFrame([{"Fecha": mat, "Cupon_base100": 0.0, "Capital_base100": cap_vigente, "Saldo_ini_base100": cap_vigente,
                            "dias_periodo": (mat - settle).days, "Cupon": 0.0, "Capital": cap_vigente * escala,
                            "Flujo": cap_vigente * escala, "Origen": "PROYECTADO"}])
        return df, {"cupones_geneva": 0, "cupones_proyectados": 0, "meses_cupon": None, "cap_vigente_ini": cap_vigente, "ultimo_cupon_pagado": None}
    ints = _intereses(rec)
    cpn_geneva = {d.normalize(): v for d, v in ints if d > settle}
    meses = inferir_periodicidad(ints) or max(1, int(12 // (bs.get("Coupon Frequency") or 2)))
    fechas, dt = [], mat
    while dt > settle:
        fechas.append(dt.normalize())
        dt = dt - pd.DateOffset(months=meses)
    todas = sorted(set(fechas) | set(sink_fut))
    if not todas:
        return pd.DataFrame(), {}
    pagados = [d for d, _ in ints if d <= settle]
    prev = max(pagados) if pagados else settle
    filas, cap_rem, n_gen, n_proy = [], cap_vigente, 0, 0
    for f in todas:
        frac = (f - prev).days / 365.0
        if pd.notna(tasa) and tasa > 0 and frac > 0:
            cpn = tasa * (cap_rem / 100.0) * frac
            origen = "GENEVA" if f in cpn_geneva else "PROYECTADO"
            n_gen, n_proy = n_gen + (origen == "GENEVA"), n_proy + (origen != "GENEVA")
        elif f in cpn_geneva:
            cpn, origen, n_gen = cpn_geneva[f], "GENEVA_RAW", n_gen + 1
        else:
            cpn, origen = 0.0, "SINK"
        pri = cap_rem if f == mat.normalize() else sink_fut.get(f, 0.0)
        filas.append({"Fecha": f, "Cupon_base100": cpn, "Capital_base100": pri, "Saldo_ini_base100": cap_rem,
                      "dias_periodo": (f - prev).days, "Cupon": cpn * escala, "Capital": pri * escala,
                      "Flujo": (cpn + pri) * escala, "Origen": origen})
        cap_rem, prev = max(cap_rem - pri, 0.0), f
    return pd.DataFrame(filas), {"cupones_geneva": n_gen, "cupones_proyectados": n_proy, "meses_cupon": meses,
                                 "cap_vigente_ini": cap_vigente, "ultimo_cupon_pagado": (max(pagados) if pagados else None)}
