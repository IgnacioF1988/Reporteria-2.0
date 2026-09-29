import pandas as pd

from reporteria.escala import EscalaCfg, candidatos_fx, elegir_escala_fx, evaluar_escala
from reporteria.fx import armar_fx

CFG = EscalaCfg()


def test_empate_de_pq_se_resuelve_por_ratio():
    """(0.001,1000) y (1,1) dan el mismo P×Q; el ratio nominal/MV descarta el que infla Q 1000x."""
    res = elegir_escala_fx(lp=99.676, of=1000, factor=1.0, mv=996.76, cands=[("USDUSD", 1.0)], cfg=CFG)
    assert (res["sP"], res["sQ"], res["Escala_Flag"]) == (0.01, 1, "OK")


def test_precio_por_mil_agrovision():
    """LocalPrice 921.09 por 1.000 de face, Qty 2.240, MVBook 2.063.242 → (0.001, 1000) es legítimo."""
    res = elegir_escala_fx(lp=921.09, of=2240, factor=1.0, mv=2063241.6, cands=[("USDUSD", 1.0)], cfg=CFG)
    assert (res["sP"], res["sQ"], res["Escala_Flag"]) == (0.001, 1000, "OK")


def test_gana_el_fx_que_calza_caso_ars():
    lp, of, mv = 95.0, 1_000_000, 0.95 * 1_000_000 / 1471          # MVBook en USD valorizado con 1471
    cands = [("bee_USDARS", 1382.0), ("par_USDARS", 1471.0)]
    res = elegir_escala_fx(lp, of, 1.0, mv, cands, CFG)
    assert res["FX_Fuente"] == "par_USDARS" and res["Escala_Flag"] == "OK"
    res = elegir_escala_fx(lp, of, 1.0, 0.95 * 1_000_000 / 1382, cands, CFG)
    assert res["FX_Fuente"] == "bee_USDARS"


def test_fallback_prefiere_ratio_valido():
    res = elegir_escala_fx(lp=99.0, of=1000, factor=1.0, mv=900.0, cands=[("USDUSD", 1.0)], cfg=CFG)   # 9 % de desvío
    assert res["Escala_Flag"] == "FALLBACK" and res["FX_Fuente"] == "FALLBACK_USDUSD" and res["sQ"] == 1


def test_candidatos_fx_fondo_clp_divide_por_usdclp():
    fx_bee, fx_par = armar_fx({"USDPEN": 3.3985, "USDCLP": 924.78, "USDCLF": 0.02273}, {"USDCLP": 928.42, "USDARS MAE": 1471.0})
    c = dict(candidatos_fx("PEN", fx_bee, fx_par, base_ccy="CLP"))
    assert abs(c["bee_USDPEN/bee_USDCLP"] - 3.3985 / 924.78) < 1e-12 and "bee_USDPEN/par_USDCLP" in c
    c = dict(candidatos_fx("CLF", fx_bee, fx_par, base_ccy="CLP"))
    assert abs(c["bee_USDCLF/bee_USDCLP"] - 0.02273 / 924.78) < 1e-12
    assert candidatos_fx("USD", fx_bee, fx_par, "USD") == [("USDUSD", 1.0)]
    assert "par_USDARS MAE" in dict(candidatos_fx("ARS", fx_bee, fx_par, "USD"))                     # startswith captura variantes
    assert "CLFCLP" in fx_bee and abs(fx_bee["CLFCLP"] - 924.78 / 0.02273) < 1e-6                     # derivado


def test_evaluar_escala_devuelve_todos_los_intentos():
    intentos = evaluar_escala(100.0, 1000, 1.0, 1000.0, 1.0, CFG)
    assert len(intentos) == len(CFG.candidatos) and sum(i["all_ok"] for i in intentos) == 1
