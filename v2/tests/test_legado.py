import pandas as pd

from reporteria.legado import importar_cache_legacy


def test_importa_cache_desde_outputs_legacy(fixtures, tmp_path):
    n = importar_cache_legacy(fixtures.parent / "corporativo", tmp_path, "20260731")
    assert n["YAS_BOND_YLD"] > 400 and n["DES_CASH_FLOW"] == 33 and n["XCCY_CLP"] > 0
    y = pd.read_csv(tmp_path / "bdp_YAS_BOND_YLD_20260731_settle_dt-20260731.csv")
    assert y["ticker"].str.endswith(" Corp").all() and y["valor"].abs().max() > 1     # en %, como lo entrega BBG
    assert (tmp_path / "bds_DES_CASH_FLOW").is_dir()
