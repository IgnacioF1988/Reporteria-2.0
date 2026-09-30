import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.fuentes.bbg_yas import candidatos_bbg


def _pos(pk2, isin, herm="", hedge="", yt=15, trat="CASCADA"):
    return dict(Pos_ID=f"20|{pk2}|Asset", ID_Fund=20, PK2=pk2, ISIN=isin, ISIN_Hermanos=herm, Hedge_Currency=hedge,
                Yield_Type=yt, Tratamiento=trat, Risk_Currency="USD", Name_Instrumento="B")


def _cache(tmp, campo, valores, **ov):
    from reporteria.adaptadores.bbg import _archivo
    p = _archivo(tmp, campo, "20260731", ov)
    pd.DataFrame({"ticker": list(valores), "valor": list(valores.values())}).to_csv(p, index=False)


def test_cascada_de_tickers_campo_por_yield_type_y_xccy(tmp_path):
    _cache(tmp_path, "YAS_BOND_YLD", {"US1 Corp": 6.5, "US2@BGN Corp": 7.0, "US9 Corp": 8.0}, settle_dt="20260731")
    _cache(tmp_path, "YAS_YLD_MATURITY", {"US4 Corp": 5.0}, settle_dt="20260731")
    _cache(tmp_path, "YAS_MOD_DUR", {"US1 Corp": 2.0, "US2@BGN Corp": 3.0, "US9 Corp": 4.0, "US4 Corp": 1.5}, settle_dt="20260731")
    _cache(tmp_path, "YAS_XCCY_FIXED_COUPON_EQUIVALENT", {"US1 Corp": 7.1}, settle_dt="20260731", YAS_XCCY_FOREIGN_CURRENCY="CLP")
    bbg = FixtureBloomberg(tmp_path, "20260731")
    pos = pd.DataFrame([_pos("1-1", "US1", hedge="CLP"), _pos("2-1", "US2"), _pos("3-1", "US3", herm="US9"),
                        _pos("4-1", "US4", yt=1), _pos("5-1", "US5"), _pos("6-1", "US1", trat="CERO")])
    pend = {"20|1-1|Asset", "20|2-1|Asset", "20|3-1|Asset", "20|4-1|Asset", "20|5-1|Asset"}
    cand, xccy, al = candidatos_bbg(pos, pend, bbg, "20260731", yield_type_default=15)
    c = cand.set_index("PK2")
    assert abs(c.loc["1-1", "Yield"] - 0.065) < 1e-12 and c.loc["1-1", "Origen"] == "DIRECTO" and c.loc["1-1", "Valido"]
    assert abs(c.loc["2-1", "Yield"] - 0.07) < 1e-12 and c.loc["2-1", "Origen"] == "BGN"
    assert abs(c.loc["3-1", "Yield"] - 0.08) < 1e-12 and c.loc["3-1", "Origen"] == "HERMANO:US9"
    assert abs(c.loc["4-1", "Yield"] - 0.05) < 1e-12 and "YAS_YLD_MATURITY" in c.loc["4-1", "Detalle"]
    assert "5-1" not in c.index and "6-1" not in c.index                                  # sin dato / no pendiente
    assert xccy.set_index("Pos_ID").loc["20|1-1|Asset", "Yield_XCCY"] == 0.071 and len(xccy) == 1
    pedidos = [t for campo, ts in bbg.pedidos for t in ts]
    assert "US1 Corp" in pedidos and not any(t.startswith("US6") for t in pedidos)
    assert (al["Nombre"] == "FAMILIA_INFERIDA").sum() == 1
