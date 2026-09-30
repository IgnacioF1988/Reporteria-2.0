import pandas as pd

from reporteria.legado import importar_cache_legacy


def test_importa_cache_desde_outputs_legacy(fixtures, tmp_path):
    n = importar_cache_legacy(fixtures.parent / "corporativo", tmp_path, "20260731")
    assert n["YAS_BOND_YLD"] > 400 and n["DES_CASH_FLOW"] == 33 and n["XCCY_CLP"] > 0
    y = pd.read_csv(tmp_path / "bdp_YAS_BOND_YLD_20260731_settle_dt-20260731.csv")
    assert y["ticker"].str.endswith(" Corp").all() and y["valor"].abs().max() > 1     # en %, como lo entrega BBG
    assert (tmp_path / "bds_DES_CASH_FLOW").is_dir()


def test_importa_curvas_de_drops_y_atributos_de_indice(fixtures, tmp_path):
    n = importar_cache_legacy(fixtures.parent / "corporativo", tmp_path, "20260731")
    assert n["CURVE_TENOR_RATES"] == 13 and n["INFLATION_LINKED_INDICATOR"] > 500 and n["RESET_IDX"] == 7
    c = pd.read_csv(tmp_path / "bds_CURVE_TENOR_RATES" / "YCSW0193 Index.csv")
    assert {"Tenor", "Tenor Ticker", "Mid Yield"} <= set(c.columns) and len(c) == 20
    r = pd.read_csv(tmp_path / "bdp_RESET_IDX_20260731.csv")
    assert set(r["valor"]) == {"BZDIOVRA", "MXIBTIEF", "MXIBTIIE"}
