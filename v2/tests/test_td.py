import json

import pandas as pd
import pytest

from reporteria.lectura.geneva import leer_jsonl
from reporteria.td import clasificar_geneva, construir_td_geneva, normalizar_td_bbg

S = pd.Timestamp("2026-07-31")


@pytest.fixture(scope="module")
def recs(fixtures_mod):
    return leer_jsonl(fixtures_mod / "bond_schedule.jsonl")


@pytest.fixture(scope="module")
def fixtures_mod():
    from pathlib import Path
    return Path(__file__).parent / "fixtures" / "mini"


def test_normalizar_td_bbg_deriva_face():
    raw = pd.DataFrame({"Payment Date": ["2026-12-01", "2027-06-01"], "Coupon Amount": [25.0, 25.0], "Principal Amount": [0.0, 1000.0]})
    td, face = normalizar_td_bbg(raw)
    assert face == 1000.0 and td["Flujo"].tolist() == [25.0, 1025.0] and td["Fecha"].is_monotonic_increasing
    cache = pd.DataFrame({"Fecha": ["2027-06-01"], "Cupon": [float("nan")], "Principal": [float("nan")], "Flujo": [1025.0], "Face": [1188801.17]})
    td, face = normalizar_td_bbg(cache)
    assert abs(face - 1188801.17) < 1e-6 and td["Flujo"].iloc[0] == 1025.0


def test_clasificacion_geneva(recs):
    assert clasificar_geneva(recs["AGROVISION 0% 19/09/2026"], S)[0] == "ZERO_COUPON"
    assert clasificar_geneva(recs["SOLFACIL 16.48 01/15/34"], S)[0] == "REVISAR_252"
    assert clasificar_geneva(recs["CASH URUGUA 13.50 09/20/26"], S)[0] == "SINKABLE"
    tipo, tasa, _ = clasificar_geneva(recs["RECARR 9.67 08/10/38 19"], S)
    assert tipo in ("BULLET", "SINKABLE") and abs(tasa - 9.67) < 1e-9


def test_cash_urugua_factor_ya_viene_en_los_flujos(recs):
    """OF 84.250.000 × sQ 1, Factor 0.3152: Σ Capital_base100 × OF_ef/100 debe dar exactamente el nominal vigente."""
    tipo, tasa, _ = clasificar_geneva(recs["CASH URUGUA 13.50 09/20/26"], S)
    td, info = construir_td_geneva(recs["CASH URUGUA 13.50 09/20/26"], of_ef=84_250_000, tipo=tipo, tasa=tasa, settle=S)
    assert abs(td["Capital"].sum() - 84_250_000 * 0.3152) < 1.0
    assert (td["Fecha"] > S).all() and info["cupones_geneva"] + info["cupones_proyectados"] >= 1


def test_recarr_primer_cupon_completo_anclado_al_ultimo_pagado(recs):
    tipo, tasa, _ = clasificar_geneva(recs["RECARR 9.67 08/10/38 19"], S)
    td, info = construir_td_geneva(recs["RECARR 9.67 08/10/38 19"], of_ef=100, tipo=tipo, tasa=tasa, settle=S)
    primero = td.iloc[0]
    assert 170 <= primero["dias_periodo"] <= 200 and primero["Cupon_base100"] > 4.0   # cupón completo, no 0.26 desde el settle
    assert info["ultimo_cupon_pagado"] == pd.Timestamp("2026-01-31")
