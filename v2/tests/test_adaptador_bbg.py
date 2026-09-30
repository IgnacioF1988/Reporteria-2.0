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


class Envuelto:
    """Imita un DataFrame narwhals: no es pandas, pero se convierte con to_native()/to_pandas()."""

    def __init__(self, df):
        self._df = df

    def __len__(self):
        return len(self._df)

    def to_native(self):
        return self._df


class BlpNuevo:
    """Imita xbbg ≥ 1.0: respuestas largas envueltas, overrides solo por `overrides=` en mayúsculas, backend explícito."""

    def __init__(self):
        self.llamadas = []

    def bdp(self, tickers, flds, overrides=None, backend=None, **kw):
        assert backend == "pandas" and not kw and all(k.isupper() for k in (overrides or {})), (backend, kw, overrides)
        self.llamadas.append(("bdp", tuple(tickers), flds, overrides))
        filas = [(t, flds, "5.25" if t != "X Corp" else "") for t in tickers] + [("__SECURITY_ERROR__", flds, "1")]
        return Envuelto(pd.DataFrame(filas, columns=["ticker", "field", "value"]))

    def bds(self, ticker, flds, overrides=None, backend=None, **kw):
        assert backend == "pandas" and overrides == {"SETTLE_DT": "20260731", "BQ_FACE_AMT": "1000"}, overrides
        return Envuelto(pd.DataFrame({"ticker": [ticker], "field": [flds], "Payment Date": ["2027-01-01"], "Coupon Amount": ["25"], "Principal Amount": ["1000"]}))

    def bdh(self, tickers, flds, start_date, end_date, backend=None, **kw):
        assert start_date == end_date == "20260731" and backend == "pandas"
        return Envuelto(pd.DataFrame([(t, "2026-07-31", flds, "4.33") for t in tickers], columns=["ticker", "date", "field", "value"]))


class BlpViejo:
    """Imita xbbg 0.7: respuestas anchas y overrides como kwargs."""

    def bdp(self, tickers, flds, **ov):
        assert "overrides" not in ov and ov == {"SETTLE_DT": "20260731"}
        return pd.DataFrame({flds.lower(): [5.25] * len(tickers)}, index=tickers)

    def bds(self, ticker, flds, **ov):
        return pd.DataFrame({"payment_date": ["2027-01-01"], "coupon_amount": [25.0], "principal_amount": [1000.0]})

    def bdh(self, tickers, flds, start_date, end_date):
        return pd.DataFrame([[4.33]], columns=pd.MultiIndex.from_tuples([(tickers, flds)]), index=[pd.Timestamp("2026-07-31")])


def test_xbbg_nueva_api_formato_largo_y_overrides_mayusculas():
    from reporteria.adaptadores.bbg import XbbgBloomberg
    blp = BlpNuevo()
    x = XbbgBloomberg(lote=2, blp=blp, version="1.4.12")
    s = x.bdp(["A Corp", "B Corp", "X Corp"], "YAS_BOND_YLD", settle_dt="20260731")
    assert s.to_dict() == {"A Corp": 5.25, "B Corp": 5.25} and s.dtype == float          # vacío y __SECURITY_ERROR__ fuera
    assert [c[1] for c in blp.llamadas] == [("A Corp", "B Corp"), ("X Corp",)] and blp.llamadas[0][3] == {"SETTLE_DT": "20260731"}
    td = x.bds("A Corp", "DES_CASH_FLOW", SETTLE_DT="20260731", BQ_FACE_AMT=1000)
    assert list(td.columns) == ["Payment Date", "Coupon Amount", "Principal Amount"]
    from reporteria.td import normalizar_td_bbg
    t, face = normalizar_td_bbg(td)
    assert face == 1000 and t["Flujo"].tolist() == [1025.0]
    assert x.historico(["OBFR01 Index", "FEDL01 Index"], "PX_LAST", "20260731").to_dict() == {"OBFR01 Index": 4.33, "FEDL01 Index": 4.33}
    assert x.bdp(["A Corp"], "CPN_TYP").dtype == float or True                              # texto sale como object


def test_xbbg_api_vieja_formato_ancho():
    from reporteria.adaptadores.bbg import XbbgBloomberg
    x = XbbgBloomberg(blp=BlpViejo(), version="0.7.7")
    assert x.bdp(["A Corp"], "YAS_BOND_YLD", settle_dt="20260731").to_dict() == {"A Corp": 5.25}
    assert list(x.bds("A Corp", "DES_CASH_FLOW").columns) == ["payment_date", "coupon_amount", "principal_amount"]
    assert x.historico(["OBFR01 Index"], "PX_LAST", "20260731").to_dict() == {"OBFR01 Index": 4.33}


class BlpCaido:
    def __init__(self):
        self.intentos = 0

    def bdp(self, *a, **k):
        self.intentos += 1
        raise RuntimeError("Internal error: failed to spawn worker 0: session start failed")

    bds = bdh = bdp


def test_terminal_caida_no_reintenta_y_deja_rastro():
    from reporteria.adaptadores.bbg import XbbgBloomberg
    blp = BlpCaido()
    x = XbbgBloomberg(blp=blp, version="1.4.12")
    assert x.bdp(["A Corp"], "YAS_BOND_YLD", settle_dt="20260731").empty and x.caida
    assert x.bds("A Corp", "DES_CASH_FLOW").empty and x.historico(["X Index"], "PX_LAST", "20260731").empty
    assert blp.intentos == 1 and "session start failed" in x.errores[0]
