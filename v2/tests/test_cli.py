import shutil

import pytest
from typer.testing import CliRunner

from reporteria.cli import app

runner = CliRunner()


@pytest.fixture
def raiz_produccion(tmp_path, fixtures):
    """Layout real: CORPORATIVO (maestros), MERCADO, MANUALES, GENEVA bajo 01_INPUTS."""
    corp, mercado, manuales = (tmp_path / "01_INPUTS" / d for d in ("CORPORATIVO", "MERCADO", "MANUALES"))
    for d in (corp, corp / "DIMENSIONALES", mercado, manuales, tmp_path / "01_INPUTS" / "GENEVA", tmp_path / "04_CACHE" / "20260731", tmp_path / "dim"):
        d.mkdir(parents=True)
    for f in ("CUBO_20260731.xlsx", "BD_INSTRUMENTOS.xlsx", "HOMOL_INSTRUMENTOS.xlsx"):
        shutil.copy(fixtures / f, corp)
    for f in ("BD_FUNDS.xlsx", "BD_BalanceSheet.xlsx", "BD_Monedas.xlsx", "BD_YLD_FLAG.xlsx", "DEFAULTED.xlsx",
              "HOMOL_FUNDS.xlsx", "BD_FX_Exposure_MLDL.xlsx", "BD_FX_Exposure_MRCLP.xlsx"):
        shutil.copy(fixtures / f, corp / "DIMENSIONALES")
    for f in fixtures.glob("BD_*_TYPE.xlsx"):
        shutil.copy(f, corp / "DIMENSIONALES")
    for f in ("BD_RANK.xlsx",):
        shutil.copy(fixtures / f, corp / "DIMENSIONALES")
    shutil.copy(fixtures / "dimensionales.duckdb", tmp_path / "dim")            # construido por conftest desde esos mismos BD_*
    shutil.copy(fixtures / "REGLAS.xlsx", manuales)
    shutil.copy(fixtures / "FACTURAS_20260731.xlsx", mercado)
    for f in (fixtures / "bbg_cache").glob("*.csv"):
        shutil.copy(f, tmp_path / "04_CACHE" / "20260731")
    return tmp_path


@pytest.fixture
def env_dim(raiz_produccion, monkeypatch):
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_RAIZ"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("REPORTERIA_DIM", str(raiz_produccion / "dim" / "dimensionales.duckdb"))
    return raiz_produccion


def test_check_ok_y_correr_deja_excel(env_dim, monkeypatch):
    raiz_produccion = env_dim
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "REGLAS.xlsx válido" in r.output and "dimensionales dimensionales.duckdb: clasificacion=" in r.output and "dim validar: sin problemas" in r.output

    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--sin-bbg"])
    assert r.exit_code in (0, 1), r.output
    assert (raiz_produccion / "02_OUTPUTS" / "20260731" / "REPORTE_20260731.xlsx").exists()
    assert list((raiz_produccion / "03_LOGS" / "20260731").glob("corrida_*.log"))


def test_check_falla_sin_inputs(tmp_path):
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(tmp_path)])
    assert r.exit_code == 2 and "FALTA" in r.output


def test_check_diagnostica_entorno_y_manuales_legacy(env_dim, monkeypatch, fixtures):
    raiz_produccion = env_dim
    shutil.copy(fixtures.parent / "corporativo" / "legacy_manuales" / "FIP.xlsx", raiz_produccion / "01_INPUTS" / "MANUALES")
    shutil.copy(fixtures / "bond_schedule.jsonl", raiz_produccion / "01_INPUTS" / "GENEVA")
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "REGLAS.xlsx válido: clasificacion=" in r.output and "alertas=" in r.output
    assert ".env" in r.output and "xbbg" in r.output and "--sin-bbg" in r.output
    assert "caché 20260731:" in r.output and "con yields YAS" in r.output
    assert "bond_schedule.jsonl con" in r.output
    assert "manuales legacy sin migrar: ['FIP.xlsx']" in r.output and "migrar-manuales" in r.output


def test_migrar_manuales_y_comparar(env_dim, monkeypatch, fixtures):
    import pandas as pd
    raiz_produccion = env_dim
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


def test_check_diagnostica_facts_y_correr_sin_facts_usa_cache(env_dim, monkeypatch):
    raiz_produccion = env_dim
    monkeypatch.delenv("MONEDA_BI_PASSWORD", raising=False)
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz_produccion)])
    assert r.exit_code == 0, r.output
    assert "Facts (facturas)" in r.output and "MONEDA_BI_PASSWORD FALTA" in r.output and "caché Facts 20260731: 302 facturas" in r.output
    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz_produccion), "--sin-bbg", "--sin-facts"])
    assert r.exit_code in (0, 1), r.output
    log = next((raiz_produccion / "03_LOGS" / "20260731").glob("corrida_*.log")).read_text(encoding="utf-8")
    assert "FACTS: 302 facturas" in log and "vivas al cierre 20260731: 301" in log


def test_facts_probar_sin_clave_sale_3(monkeypatch):
    monkeypatch.delenv("MONEDA_BI_PASSWORD", raising=False)
    r = runner.invoke(app, ["facts-probar"])
    assert r.exit_code == 3 and "MONEDA_BI_PASSWORD" in r.output


def test_dim_importar_exportar_editar_validar_probar(env_dim, tmp_path):
    import pandas as pd
    raiz = env_dim
    destino = tmp_path / "nuevo" / "dimensionales.duckdb"
    bix = raiz / "01_INPUTS" / "CORPORATIVO"
    r = runner.invoke(app, ["dim", "importar", "--bix", str(bix), "--dim", str(destino)])
    assert r.exit_code == 0, r.output
    assert destino.exists() and (destino.parent / "csv" / "dim_clasificacion.csv").exists()
    assert "BD_BalanceSheet.Investment_Type_CarteraFI" in r.output and "BD_FX_Exposure_MLDL.xlsx" in r.output
    r = runner.invoke(app, ["dim", "importar", "--bix", str(bix), "--dim", str(destino)])
    assert r.exit_code == 2 and "--reemplazar" in r.output
    r = runner.invoke(app, ["dim", "validar", "--fecha", "20260731", "--dim", str(destino), "--raiz", str(raiz)])
    assert r.exit_code == 0 and "sin problemas" in r.output and "Criterio=BalSheetKey" in r.output      # REGLAS 1–5 deprecadas

    salida = tmp_path / "DIM.xlsx"
    r = runner.invoke(app, ["dim", "exportar", "--dim", str(destino), "--salida", str(salida)])
    assert r.exit_code == 0 and salida.exists()
    hojas = pd.read_excel(salida, sheet_name=None)
    c = hojas["dim_clasificacion"]
    nueva = {k: None for k in c.columns}
    nueva.update(ID=c["ID"].max() + 1, BalanceSheet="Asset", Investment_Type_Code=1, Issue_Type_Code=7, Bucket="Repo", Comentario="pactos")
    hojas["dim_clasificacion"] = pd.concat([c, pd.DataFrame([nueva])], ignore_index=True)
    with pd.ExcelWriter(salida) as w:
        for h, df in hojas.items():
            df.to_excel(w, sheet_name=h, index=False)
    r = runner.invoke(app, ["dim", "importar", "--excel", str(salida), "--dim", str(destino)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["dim", "probar", "--fecha", "20260731", "--dim", str(destino), "--raiz", str(raiz)])
    assert r.exit_code == 0, r.output
    assert "Bucket:" in r.output and "Repo" in r.output and "combinaciones sin fila" in r.output

    mala = hojas["dim_clasificacion"].copy()
    mala.loc[mala.index[-1], "Issue_Type_Code"] = 99
    with pd.ExcelWriter(salida) as w:
        for h, df in {**hojas, "dim_clasificacion": mala}.items():
            df.to_excel(w, sheet_name=h, index=False)
    r = runner.invoke(app, ["dim", "importar", "--excel", str(salida), "--dim", str(destino)])
    assert r.exit_code == 2 and "Issue_Type_Code=99" in r.output and "no se escribió nada" in r.output
