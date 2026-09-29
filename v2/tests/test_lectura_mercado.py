import pandas as pd

from reporteria.fx import armar_fx
from reporteria.lectura.mercado import hoja_ra, leer_jpm, leer_paridades, leer_ra


def test_jpm_en_decimal_con_fuente(fixtures):
    j = leer_jpm(fixtures / "JPM_CEMBI_GBI_20260731.xlsx")
    assert set(j.columns) >= {"ISIN", "Yield", "Duration", "Fuente"} and set(j["Fuente"]) <= {"CEMBI", "GBI"}
    assert j["Yield"].abs().max() < 1 and j["ISIN"].is_unique
    assert (j["Fuente"] == "CEMBI").sum() > 100


def test_ra_en_decimal_con_moneda(fixtures):
    r = leer_ra(fixtures / "RA_TIR.xlsx", "jul26")
    assert set(r.columns) >= {"Nemo", "Yield", "Duration", "Moneda", "Periodicidad"}
    assert r["Yield"].abs().max() < 1 and r["Nemo"].str.isupper().all()
    assert set(r["Moneda"]) <= {"CLF", "CLP", "USD"}                       # UF se normaliza a CLF


def test_hoja_ra_desde_fecha():
    assert hoja_ra("20260731") == "jul26" and hoja_ra("20261231") == "dic26" and hoja_ra("20270115") == "ene27"


def test_paridades_ultimo_valor_hasta_settle(fixtures, settle):
    crudo = leer_paridades(fixtures / "4- Carga de paridades.xlsx", settle, max_dias=10)
    assert crudo["USDCLP"] > 800 and "USDARS MAE" in crudo and "CLFUSD" in crudo
    _, par = armar_fx({}, crudo)
    assert 0.01 < par["USDCLF"] < 0.05 and abs(par["USDEUR"] * par["EURUSD"] - 1) < 1e-9   # derivados en armar_fx
