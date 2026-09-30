import pandas as pd

from reporteria.adaptadores.bbg import CacheBloomberg, FixtureBloomberg, _ruta_bds


class Falso:
    def __init__(self):
        self.llamadas = 0

    def bdp(self, tickers, campo, **ov):
        self.llamadas += 1
        return pd.Series({t: 5.0 for t in tickers})

    def historico(self, tickers, campo, fecha):
        return pd.Series(dtype=float)

    def bds(self, ticker, campo, **ov):
        self.llamadas += 1
        return pd.DataFrame({"payment_date": ["2027-01-01"], "coupon_amount": [25.0], "principal_amount": [1000.0]})


def test_fixture_bds_lee_cache_o_vacio(tmp_path):
    p = _ruta_bds(tmp_path, "DES_CASH_FLOW", "US1 Corp"); p.parent.mkdir()
    pd.DataFrame({"Fecha": ["2027-01-01"], "Flujo": [1025.0], "Face": [1000.0]}).to_csv(p, index=False)
    bbg = FixtureBloomberg(tmp_path, "20260731")
    assert len(bbg.bds("US1 Corp", "DES_CASH_FLOW")) == 1 and bbg.bds("US2 Corp", "DES_CASH_FLOW").empty
    assert bbg.pedidos[-1] == ("bds:DES_CASH_FLOW", ("US2 Corp",))


def test_cache_bds_y_bdp_preguntan_una_sola_vez(tmp_path):
    inner = Falso()
    bbg = CacheBloomberg(inner, tmp_path, "20260731")
    assert len(bbg.bds("US1 Corp", "DES_CASH_FLOW", SETTLE_DT="20260731")) == 1
    assert len(bbg.bds("US1 Corp", "DES_CASH_FLOW", SETTLE_DT="20260731")) == 1
    assert inner.llamadas == 1
    assert bbg.bdp(["A Corp", "B Corp"], "YAS_BOND_YLD", settle_dt="20260731").tolist() == [5.0, 5.0]
    assert bbg.bdp(["A Corp"], "YAS_BOND_YLD", settle_dt="20260731").tolist() == [5.0] and inner.llamadas == 2
