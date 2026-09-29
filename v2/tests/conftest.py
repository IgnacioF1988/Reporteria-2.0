from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "mini"
FECHA = "20260731"


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def rutas(tmp_path):
    from reporteria.config import Rutas
    return Rutas.para_pruebas(FECHA, FIXTURES, tmp_path)


@pytest.fixture
def reglas(fixtures):
    from reporteria.lectura.reglas import leer_reglas
    return leer_reglas(fixtures / "REGLAS.xlsx")


@pytest.fixture
def settle():
    return pd.Timestamp("2026-07-31")


@pytest.fixture
def bbg(fixtures):
    from reporteria.adaptadores.bbg import FixtureBloomberg
    return FixtureBloomberg(fixtures / "bbg_cache", FECHA)


@pytest.fixture
def fx(fixtures):
    from reporteria.adaptadores.fx_sql import FixtureFx
    return FixtureFx(fixtures, FECHA)
