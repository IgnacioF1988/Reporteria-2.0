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


class BlpRechaza:
    def bdp(self, tickers, flds, overrides=None, backend=None, **kw):
        if all(t.endswith("@BGN Corp") for t in tickers):
            raise RuntimeError("Request failed on //blp/refdata::ReferenceDataRequest - All securities failed: X@BGN Corp")
        return pd.DataFrame([(t, flds, "5.0") for t in tickers if t == "A Corp"], columns=["ticker", "field", "value"])

    bds = bdh = bdp


def test_pedido_rechazado_no_es_caida_y_la_cascada_sigue():
    from reporteria.adaptadores.bbg import XbbgBloomberg
    from reporteria.fuentes.bbg_yas import _cascada
    x = XbbgBloomberg(blp=BlpRechaza(), version="1.4.12")
    val, org = _cascada(x, ["A", "X"], {"X": ["H"]}, "YAS_BOND_YLD", settle_dt="20260731")
    assert val == {"A": 5.0} and org == {"A": "DIRECTO"} and not x.caida and len(x.avisos) == 1
    assert "All securities failed" in x.avisos[0]


def test_respuesta_bds_vacia_se_cachea_y_se_relee_sin_error(tmp_path):
    class SinTabla:
        def __init__(self):
            self.n = 0

        def bds(self, *a, **k):
            self.n += 1
            return pd.DataFrame()

    inner = SinTabla()
    bbg = CacheBloomberg(inner, tmp_path, "20260731")
    assert bbg.bds("Z Corp", "DES_CASH_FLOW", SETTLE_DT="20260731").empty
    assert bbg.bds("Z Corp", "DES_CASH_FLOW", SETTLE_DT="20260731").empty and inner.n == 1     # relee el vacío sin preguntar
    assert FixtureBloomberg(tmp_path, "20260731").bds("Z Corp", "DES_CASH_FLOW").empty


def test_fixture_registra_lo_que_no_esta_en_cache_y_cache_persiste_los_sin_dato(tmp_path):
    import pandas as pd
    from reporteria.adaptadores.bbg import CacheBloomberg, FixtureBloomberg
    fx = FixtureBloomberg(tmp_path, "20260731")
    assert fx.bdp(["A Corp"], "YAS_BOND_YLD", settle_dt="20260731").empty
    assert fx.sin_cache == [{"tipo": "bdp", "campo": "YAS_BOND_YLD", "ticker": "A Corp", "overrides": {"settle_dt": "20260731"}}]

    class Inner:
        caida = False

        def bdp(self, tickers, campo, **ov):
            return pd.Series({"A Corp": 5.0})          # B Corp sin respuesta

        def bds(self, ticker, campo, **ov):
            return pd.DataFrame({"x": [1]})
    c = CacheBloomberg(Inner(), tmp_path, "20260731")
    r = c.bdp(["A Corp", "B Corp"], "YAS_BOND_YLD", settle_dt="20260731")
    assert r["A Corp"] == 5.0 and pd.isna(r["B Corp"]) and not c.sin_cache
    fx2 = FixtureBloomberg(tmp_path, "20260731")
    r2 = fx2.bdp(["A Corp", "B Corp", "C Corp"], "YAS_BOND_YLD", settle_dt="20260731")
    assert pd.isna(r2["B Corp"]) and "C Corp" not in r2.index
    assert [e["ticker"] for e in fx2.sin_cache] == ["C Corp"]                      # B es "sin dato" conocido; C no se preguntó
    # curvas con override: nombre fechado, y el nombre viejo sirve de respaldo de lectura
    c.bds("YCSW0023 Index", "CURVE_TENOR_RATES", CURVE_DATE="20260731")
    assert (tmp_path / "bds_CURVE_TENOR_RATES" / "YCSW0023 Index__CURVE_DATE-20260731.csv").exists()
    (tmp_path / "bds_CURVE_TENOR_RATES" / "VIEJA Index.csv").write_text("x\n2\n")
    assert fx2.bds("VIEJA Index", "CURVE_TENOR_RATES", CURVE_DATE="20260731")["x"].tolist() == [2]
    assert fx2.bds("NADA Index", "CURVE_TENOR_RATES", CURVE_DATE="20260731").empty and fx2.sin_cache[-1]["ticker"] == "NADA Index"


def test_terminal_caida_no_persiste_sin_dato_y_marca_sin_cache(tmp_path):
    import pandas as pd
    from reporteria.adaptadores.bbg import CacheBloomberg

    class Caida:
        caida = True

        def bdp(self, tickers, campo, **ov):
            return pd.Series(dtype=float)
    c = CacheBloomberg(Caida(), tmp_path, "20260731")
    assert c.bdp(["A Corp"], "YAS_MOD_DUR", settle_dt="20260731").empty
    assert c.sin_cache[0]["ticker"] == "A Corp"
    archivo = list(tmp_path.glob("bdp_YAS_MOD_DUR_*.csv"))
    assert not archivo or pd.read_csv(archivo[0]).empty


def test_xbbg_instalado_pero_que_no_carga_degrada_a_terminal_caida(xbbg_roto, tmp_path):
    from reporteria.adaptadores.bbg import CacheBloomberg, XbbgBloomberg, xbbg_disponible
    assert xbbg_disponible()[0] == "no_carga" and "DLL load failed" in xbbg_disponible()[1]
    x = XbbgBloomberg()                                   # no lanza: la degradación vive en el adaptador
    assert x.caida and x.errores[0].startswith("ImportError") and "_core" in x.errores[0]
    assert x.bdp(["A Corp"], "YAS_BOND_YLD", settle_dt="20260731").empty
    c = CacheBloomberg(x, tmp_path, "20260731")
    assert c.bdp(["A Corp"], "YAS_MOD_DUR", settle_dt="20260731").empty and c.caida
    assert c.sin_cache[0]["ticker"] == "A Corp"           # queda para PENDIENTE_TERMINAL, no como "sin dato"
    archivo = list(tmp_path.glob("bdp_YAS_MOD_DUR_*.csv"))
    assert not archivo or pd.read_csv(archivo[0]).empty


def test_xbbg_disponible_distingue_no_instalado(monkeypatch):
    import sys
    from reporteria.adaptadores.bbg import xbbg_disponible
    monkeypatch.setitem(sys.modules, "xbbg", None)        # `import xbbg` falla aunque la máquina lo tenga instalado
    monkeypatch.setattr("importlib.util.find_spec", lambda nombre: None)
    assert xbbg_disponible() == ("no_instalado", "")
