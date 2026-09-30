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


def test_check_diagnostica_entorno_y_manuales_legacy(raiz_produccion, monkeypatch, fixtures):
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_RAIZ"):
        monkeypatch.delenv(v, raising=False)
    shutil.copy(fixtures.parent / "corporativo" / "legacy_manuales" / "FIP.xlsx", raiz_produccion / "01_INPUTS" / "MANUALES")
    shutil.copy(fixtures / "bond_schedule.jsonl", raiz_produccion / "01_INPUTS" / "GENEVA")
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "REGLAS.xlsx válido: clasificacion=" in r.output and "alertas=" in r.output
    assert ".env" in r.output and "xbbg" in r.output and "--sin-bbg" in r.output
    assert "caché 20260731:" in r.output and "con yields YAS" in r.output
    assert "bond_schedule.jsonl con" in r.output
    assert "manuales legacy sin migrar: ['FIP.xlsx']" in r.output and "migrar-manuales" in r.output


def test_migrar_manuales_y_comparar(raiz_produccion, monkeypatch, fixtures):
    import pandas as pd
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_RAIZ"):
        monkeypatch.delenv(v, raising=False)
    legacy = fixtures.parent / "corporativo" / "legacy_manuales"
    manuales = raiz_produccion / "01_INPUTS" / "MANUALES"
    r = runner.invoke(app, ["migrar-manuales", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--legacy", str(legacy)])
    assert r.exit_code == 0, r.output
    mig = list(manuales.glob("REGLAS_migracion_*.xlsx"))
    assert len(mig) == 1 and set(pd.ExcelFile(mig[0]).sheet_names) == {"clasificacion", "overrides_atributo", "informe"}
    r = runner.invoke(app, ["migrar-manuales", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--legacy", str(legacy), "--aplicar"])
    assert r.exit_code == 0, r.output
    assert list(manuales.glob("REGLAS_backup_*.xlsx")) and "overrides_atributo': 9" in r.output
    assert len(pd.read_excel(manuales / "REGLAS.xlsx", sheet_name="overrides_atributo")) == 9

    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--sin-bbg", "--sin-sql"])
    assert r.exit_code in (0, 1), r.output
    rep = raiz_produccion / "02_OUTPUTS" / "20260731" / "REPORTE_20260731.xlsx"
    r = runner.invoke(app, ["comparar", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--otro", str(rep)])
    assert r.exit_code == 0 and "0 diferencias" in r.output, r.output
    otro = raiz_produccion / "otro.xlsx"
    hojas = pd.read_excel(rep, sheet_name=None)
    hojas["cartera_final"].loc[0, "Yield"] = 0.99
    hojas["cartera_final"] = hojas["cartera_final"].iloc[:-1]
    with pd.ExcelWriter(otro) as w:
        for n, d in hojas.items():
            d.to_excel(w, sheet_name=n, index=False)
    r = runner.invoke(app, ["comparar", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--otro", str(otro)])
    assert r.exit_code == 1 and "2 diferencias" in r.output, r.output
    assert list((raiz_produccion / "02_OUTPUTS" / "20260731").glob("comparacion_*.csv"))
