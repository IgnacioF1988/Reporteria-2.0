import shutil

import pytest
from typer.testing import CliRunner

from reporteria.cli import app

runner = CliRunner()


@pytest.fixture
def raiz_produccion(tmp_path, fixtures):
    """Layout real: 01_INPUTS/{CORPORATIVO,MERCADO,MANUALES,GENEVA}. Sin .env, todo bajo la raíz."""
    corp, mercado, manuales = (tmp_path / "01_INPUTS" / d for d in ("CORPORATIVO", "MERCADO", "MANUALES"))
    for d in (corp, corp / "DIMENSIONALES", mercado, manuales, tmp_path / "01_INPUTS" / "GENEVA"):
        d.mkdir(parents=True)
    shutil.copy(fixtures / "CUBO_20260731.xlsx", corp)
    shutil.copy(fixtures / "BD_INSTRUMENTOS.xlsx", corp)
    shutil.copy(fixtures / "BD_FUNDS.xlsx", corp / "DIMENSIONALES")
    shutil.copy(fixtures / "REGLAS.xlsx", manuales)
    shutil.copy(fixtures / "FACTURAS_20260731.xlsx", mercado)
    return tmp_path


def test_check_ok_y_correr_deja_excel(raiz_produccion, monkeypatch):
    monkeypatch.delenv("RUTA_CUBO_DIR", raising=False)
    monkeypatch.delenv("RUTA_BIX", raising=False)
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "REGLAS.xlsx válido" in r.output

    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--sin-bbg"])
    assert r.exit_code == 0, r.output
    assert (raiz_produccion / "02_OUTPUTS" / "20260731" / "REPORTE_20260731.xlsx").exists()
    assert list((raiz_produccion / "03_LOGS" / "20260731").glob("corrida_*.log"))


def test_check_falla_sin_inputs(tmp_path):
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(tmp_path)])
    assert r.exit_code == 2
    assert "FALTA" in r.output
