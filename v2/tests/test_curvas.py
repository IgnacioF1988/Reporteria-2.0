import pandas as pd
import pytest

from reporteria.curvas import curvas_drops, interpolar, leer_curvas_csv, tenor_a_dias
from reporteria.adaptadores.bbg import FixtureBloomberg


def test_tenor_a_dias():
    assert tenor_a_dias("1Y") == 365.25 and tenor_a_dias("6M") == 6 * 30.4375 and tenor_a_dias("3D") == 3
    assert pd.isna(tenor_a_dias("PERP"))


def test_interpolar_lineal_y_plana_en_los_extremos():
    c = pd.DataFrame({"dias": [365.25, 730.5, 1826.25], "tasa": [0.04, 0.05, 0.06]})
    assert interpolar(547.875, c) == (0.045, False)
    assert interpolar(100, c) == (0.04, True) and interpolar(5000, c) == (0.06, True)
    assert interpolar(365.25, c) == (0.04, False)
    assert pd.isna(interpolar(100, pd.DataFrame(columns=["dias", "tasa"]))[0])


def test_curvas_csv_en_decimal_y_por_fecha(fixtures, settle):
    real = leer_curvas_csv(next(fixtures.glob("Carga_Indexes_20260731*.csv")), settle)
    nom = leer_curvas_csv(next(fixtures.glob("Carga_CurvasSoberanas_20260731*.csv")), settle)
    assert {"BTUCHILE", "UDIMEXICO", "UVRCOLOMBIA", "CDIBRAZIL"} <= set(real) and {"LCCHILE", "LCMEXICO"} <= set(nom)
    dias = 4.305099 * 365.25                                                       # BAARA-B: duration × 365.25
    r, _ = interpolar(dias, real["BTUCHILE"]); n, _ = interpolar(dias, nom["LCCHILE"])
    assert abs(r - 0.02163191) < 1e-6 and abs(n - 0.05437086) < 1e-6      # BAARA-B en el legacy (r_real_% 2.163191, r_nom_% 5.437086)
    assert (real["BTUCHILE"]["dias"].diff().dropna() > 0).all()
    assert not leer_curvas_csv(next(fixtures.glob("Carga_Indexes_20260731*.csv")), pd.Timestamp("2026-07-01"))   # nada antes del cierre


def test_curvas_drops_desde_cache_y_moneda_sin_curvas(bbg):
    cur, tabla, al = curvas_drops(bbg, {"CLP", "BRL", "ARS"}, "20260731")
    assert set(cur) == {"CLP", "BRL"} and set(cur["CLP"]) == {"local", "basis", "usd"} and set(cur["BRL"]) == {"local", "usd"}
    dias = 4.074062 * 365.25
    rl, _ = interpolar(dias, cur["CLP"]["local"]); rb, _ = interpolar(dias, cur["CLP"]["basis"]); ru, _ = interpolar(dias, cur["CLP"]["usd"])
    assert abs(rl - 0.05097036) < 1e-7 and abs(rb - (-0.0044796408)) < 1e-8 and abs(ru - 0.04153185) < 1e-7   # AES 2034 CLP legacy
    assert (al["Nombre"] == "MONEDA_SIN_CURVAS_DROP").sum() == 1 and "ARS" in al.iloc[0]["Detalle"]
    assert set(tabla["Curva"]) == {"CLP_local", "CLP_basis", "CLP_usd", "BRL_local", "BRL_usd"}


def test_curvas_drops_fallback_de_extremos(tmp_path):
    (tmp_path / "bds_CURVE_TENOR_RATES").mkdir()
    pd.DataFrame({"Tenor": ["2Y", "5Y"], "Tenor Ticker": ["CHSWNI2 BGN Curncy", "CHSWNI5 BGN Curncy"], "Mid Yield": [5.0, 5.5]}).to_csv(
        tmp_path / "bds_CURVE_TENOR_RATES" / "YCSW0193 Index.csv", index=False)
    pd.DataFrame({"ticker": ["CHSWNI1 BGN Curncy", "CHSWNI30 BGN Curncy"], "valor": [4.8, 6.0]}).to_csv(tmp_path / "bdh_PX_LAST_20260731.csv", index=False)
    bbg = FixtureBloomberg(tmp_path, "20260731")
    cur, tabla, _ = curvas_drops(bbg, {"CLP"}, "20260731")
    loc = cur["CLP"]["local"]
    assert loc["tenor"].tolist() == ["1Y", "2Y", "5Y", "30Y"] and loc["tasa"].tolist() == [0.048, 0.05, 0.055, 0.06]
    assert tabla.loc[tabla["Curva"] == "CLP_local", "Fallback"].iloc[0] == "CHSWNI1 BGN Curncy;CHSWNI30 BGN Curncy"
    assert cur["CLP"]["basis"].empty and cur["CLP"]["usd"].empty                  # vacías: se reporta CURVA_SIN_DATOS
