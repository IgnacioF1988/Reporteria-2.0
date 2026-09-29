from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FECHA = "20260731"


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def rutas(tmp_path):
    """Rutas apuntando a los fixtures mini; outputs/logs/cache en tmp."""
    from reporteria.config import Rutas
    return Rutas.para_pruebas(FECHA, FIXTURES, tmp_path)


@pytest.fixture
def reglas(fixtures):
    from reporteria.lectura.reglas import leer_reglas
    return leer_reglas(FIXTURES / "REGLAS.xlsx")


@pytest.fixture
def settle():
    return pd.Timestamp("2026-07-31")
