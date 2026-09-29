import shutil

import pytest
from typer.testing import CliRunner

from reporteria.cli import app

runner = CliRunner()


@pytest.fixture
def raiz_produccion(tmp_path, fixtures):
    """Layout real: CORPORATIVO (maestros), MERCADO, MANUALES, GENEVA bajo 01_INPUTS."""
    corp, mercado, manuales = (tmp_path / "01_INPUTS" / d for d in ("CORPORATIVO", "MERCADO", "MANUALES"))
    for d in (corp, corp / "DIMENSIONALES", mercado, manuales, tmp_path / "01_INPUTS" / "GENEVA", tmp_path / "04_CACHE" / "20260731"):
        d.mkdir(parents=True)
    for f in ("CUBO_20260731.xlsx", "BD_INSTRUMENTOS.xlsx", "HOMOL_INSTRUMENTOS.xlsx"):
        shutil.copy(fixtures / f, corp)
    for f in ("BD_FUNDS.xlsx", "BD_BalanceSheet.xlsx", "BD_Monedas.xlsx", "BD_YLD_FLAG.xlsx", "DEFAULTED.xlsx",
              "HOMOL_FUNDS.xlsx", "BD_FX_Exposure_MLDL.xlsx", "BD_FX_Exposure_MRCLP.xlsx"):
        shutil.copy(fixtures / f, corp / "DIMENSIONALES")
    shutil.copy(fixtures / "REGLAS.xlsx", manuales)
    shutil.copy(fixtures / "FACTURAS_20260731.xlsx", mercado)
    for f in (fixtures / "bbg_cache").glob("*.csv"):
        shutil.copy(f, tmp_path / "04_CACHE" / "20260731")
    return tmp_path


def test_check_ok_y_correr_deja_excel(raiz_produccion, monkeypatch):
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_RAIZ"):
        monkeypatch.delenv(v, raising=False)
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "REGLAS.xlsx válido" in r.output

    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--sin-bbg"])
    assert r.exit_code in (0, 1), r.output
    assert (raiz_produccion / "02_OUTPUTS" / "20260731" / "REPORTE_20260731.xlsx").exists()
    assert list((raiz_produccion / "03_LOGS" / "20260731").glob("corrida_*.log"))


def test_check_falla_sin_inputs(tmp_path):
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(tmp_path)])
    assert r.exit_code == 2 and "FALTA" in r.output
