import shutil
from pathlib import Path

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
    for f in fixtures.glob("EXCEPCIONES*.xlsx"):
        shutil.copy(f, manuales)
    shutil.copy(fixtures / "fx_beemining_20260731.csv", tmp_path / "04_CACHE" / "20260731")
    for f in ("FACTURAS_20260731.xlsx", "JPM_CEMBI_GBI_20260731.xlsx", "RA_TIR.xlsx", "4- Carga de paridades.xlsx",
              "Carga_Indexes_20260731_20260810.csv", "Carga_CurvasSoberanas_20260731_20260810.csv"):
        shutil.copy(fixtures / f, mercado)
    shutil.copy(fixtures / "bond_schedule.jsonl", tmp_path / "01_INPUTS" / "GENEVA")
    shutil.copytree(fixtures / "bbg_cache", tmp_path / "04_CACHE" / "20260731", dirs_exist_ok=True)     # bdp/bdh y carpetas bds
    return tmp_path


@pytest.fixture
def env_dim(raiz_produccion, monkeypatch):
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_RAIZ"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("REPORTERIA_DIM", str(raiz_produccion / "dim" / "dimensionales.duckdb"))
    monkeypatch.setenv("REPORTERIA_DATAMART", str(raiz_produccion / "datamart"))        # nunca el datamart real del repo
    monkeypatch.setenv("REPORTERIA_MODO", "mensual")                                       # H10e: el default pasó a diario
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


def test_publicar_versiones_reporte_y_comparar_versiones(env_dim, monkeypatch):
    import pandas as pd
    raiz = env_dim
    dm = raiz / "datamart"
    monkeypatch.setenv("REPORTERIA_DATAMART", str(dm))
    r = runner.invoke(app, ["publicar", "--fecha", "20260731", "--raiz", str(raiz)])
    assert r.exit_code == 2 and "no hay borradores" in r.output
    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz), "--sin-bbg", "--sin-sql", "--sin-facts"])
    assert r.exit_code in (0, 1), r.output
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz)])
    assert "borradores sin publicar: 1" in r.output and "sin publicar → reporteria publicar" in r.output
    r = runner.invoke(app, ["publicar", "--fecha", "20260731", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "v001 PUBLICADA" in r.output and "git add" in r.output, r.output
    assert (dm / "cierres" / "cierre=20260731" / "version=001" / "corrida.json").exists()
    r = runner.invoke(app, ["publicar", "--fecha", "20260731", "--raiz", str(raiz)])
    assert r.exit_code == 2 and "--reexpresar" in r.output
    r = runner.invoke(app, ["publicar", "--fecha", "20260731", "--raiz", str(raiz), "--reexpresar", "--motivo", "prueba"])
    assert r.exit_code == 0 and "v002 REEXPRESADA" in r.output
    r = runner.invoke(app, ["versiones", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "v001  PUBLICADA" in r.output and "v002  REEXPRESADA" in r.output and "prueba" in r.output
    r = runner.invoke(app, ["reporte", "--fecha", "20260731", "--raiz", str(raiz), "--publicada"])
    assert r.exit_code == 0, r.output
    rep = raiz / "02_OUTPUTS" / "20260731" / "REPORTE_20260731_v001.xlsx"
    assert rep.exists() and set(pd.ExcelFile(rep).sheet_names) >= {"resumen", "cartera_final", "alertas"}
    r = runner.invoke(app, ["reporte", "--fecha", "20260731", "--raiz", str(raiz), "--version", "7"])
    assert r.exit_code == 2
    r = runner.invoke(app, ["comparar", "--fecha", "20260731", "--raiz", str(raiz), "--version-a", "1", "--version-b", "2"])
    assert r.exit_code == 0 and "0 diferencias" in r.output, r.output
    r = runner.invoke(app, ["comparar", "--fecha", "20260731", "--raiz", str(raiz), "--version-b", "1"])
    assert r.exit_code == 0 and "0 diferencias" in r.output, r.output
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz)])
    assert "cierre 20260731: v002 REEXPRESADA" in r.output


def test_raiz_fija_en_el_paquete_e_ignora_reporteria_raiz(monkeypatch, tmp_path):
    from reporteria.config import RAIZ_PAQUETE, Rutas
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_DIM", "REPORTERIA_DATAMART"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("REPORTERIA_RAIZ", str(tmp_path))                      # ya no se lee
    r = Rutas.desde_env("20260731")
    assert r.raiz == RAIZ_PAQUETE
    for ruta in (r.reglas, r.ra, r.jsonl, r.paridades, r.outputs, r.logs, r.cache, r.dim, r.datamart, r.cubo, r.bd_instr):
        assert RAIZ_PAQUETE in Path(ruta).resolve().parents, ruta
    assert r.reglas == RAIZ_PAQUETE / "01_INPUTS" / "MANUALES" / "REGLAS.xlsx" and r.reglas.exists()
    assert r.jsonl.exists() and r.ra.exists() and r.paridades.exists()         # insumos versionados dentro de v2
    from reporteria.cli import _estado_raiz
    lineas = "\n".join(_estado_raiz(r))
    assert "raíz" in lineas and "REPORTERIA_RAIZ ya no se usa" in lineas
    monkeypatch.delenv("REPORTERIA_RAIZ")
    assert "REPORTERIA_RAIZ" not in "\n".join(_estado_raiz(Rutas.desde_env("20260731")))


def test_check_no_avisa_manuales_legacy_ya_migrados(env_dim, fixtures):
    import pandas as pd
    raiz = env_dim
    manuales = raiz / "01_INPUTS" / "MANUALES"
    shutil.copy(fixtures.parent / "corporativo" / "legacy_manuales" / "FIP.xlsx", manuales)
    hojas = pd.read_excel(manuales / "REGLAS.xlsx", sheet_name=None)
    c = hojas["clasificacion"]
    c.loc[len(c)] = {**{k: None for k in c.columns}, "ID": int(c["ID"].max()) + 1, "ID_Fund": 20, "Criterio": "PK2", "Valor": "176142-38",
                     "Bucket": "Equity", "Comentario": "migrado de FIP.xlsx: FIP ALZA Treatment Equity"}
    with pd.ExcelWriter(manuales / "REGLAS.xlsx") as w:
        for h, df in hojas.items():
            df.to_excel(w, sheet_name=h, index=False)
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz)])
    assert r.exit_code == 0, r.output
    assert "manuales legacy sin migrar" not in r.output


def test_maestros_cargar_cambios_estado_y_declarar(env_dim, monkeypatch):
    import pandas as pd
    raiz = env_dim
    dm = raiz / "datamart"
    monkeypatch.setenv("REPORTERIA_DATAMART", str(dm))
    r = runner.invoke(app, ["maestros", "cargar", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "base " in r.output and "bd_instrumentos=" in r.output, r.output
    r = runner.invoke(app, ["maestros", "cargar", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "sin cambios" in r.output
    bix = raiz / "01_INPUTS" / "CORPORATIVO" / "BD_INSTRUMENTOS.xlsx"
    bd = pd.read_excel(bix)
    bd.loc[0, "Risk_Currency"] = "ZZZ"
    pk = f"{int(bd.loc[0, 'ID_Instrumento'])}-{int(bd.loc[0, 'SubID_Instrumento'])}"
    bd.to_excel(bix, index=False)
    r = runner.invoke(app, ["maestros", "cargar", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "1 cambios" in r.output, r.output
    r = runner.invoke(app, ["maestros", "cambios", "--raiz", str(raiz), "--llave", pk])
    assert r.exit_code == 0 and "Risk_Currency" in r.output and "ZZZ" in r.output
    r = runner.invoke(app, ["maestros", "estado", "--raiz", str(raiz), "--llave", pk])
    assert r.exit_code == 0 and "ZZZ" in r.output
    r = runner.invoke(app, ["declarar", "--raiz", str(raiz), "--llave", pk, "--columna", "Risk_Currency", "--valor", "ZZZ", "--desde", "20260831"])
    assert r.exit_code == 0 and "declaración 1" in r.output and (dm / "declaraciones" / "vigencias.csv").exists(), r.output
    r = runner.invoke(app, ["maestros", "estado", "--raiz", str(raiz), "--llave", pk, "--cierre", "20260731"])
    assert r.exit_code == 0 and "ZZZ" not in r.output
    r = runner.invoke(app, ["declarar", "--raiz", str(raiz), "--llave", pk, "--columna", "Risk_Currency", "--valor", "QQQ", "--desde", "20260831"])
    assert r.exit_code == 2 and "--valor-anterior" in r.output
    r = runner.invoke(app, ["declarar", "--raiz", str(raiz), "--anular", "1"])
    assert r.exit_code == 0 and "anulada" in r.output
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(raiz)])
    assert "maestros: base" in r.output and "1 carga(s) de cambios (1 cambios), 0 declaración(es)" in r.output, r.output


def test_impacto_recalcular_pendientes_y_correr_sin_recalcular(env_dim, monkeypatch, fixtures):
    import pandas as pd
    raiz = env_dim
    dm = raiz / "datamart"
    monkeypatch.setenv("REPORTERIA_DATAMART", str(dm))
    r = runner.invoke(app, ["impacto", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "sin impacto" in r.output
    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz), "--sin-bbg", "--sin-sql", "--sin-facts"])
    assert r.exit_code in (0, 1) and "siguen vigentes" in r.output or "impacto" in r.output, r.output
    r = runner.invoke(app, ["publicar", "--fecha", "20260731", "--raiz", str(raiz)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["pendientes", "--raiz", str(raiz)])
    assert r.exit_code == 0 and "0 pendientes" in r.output
    bix = raiz / "01_INPUTS" / "CORPORATIVO" / "BD_INSTRUMENTOS.xlsx"
    bd = pd.read_excel(bix)
    cubo = pd.read_excel(raiz / "01_INPUTS" / "CORPORATIVO" / "CUBO_20260731.xlsx")
    eq = bd[bd["Investment_Type_Code"].eq(2) & bd["ISIN"].astype(str).str.len().gt(5) & bd["PK2"].isin(cubo["PK2"])].iloc[0] if "PK2" in bd.columns else None
    if eq is None:
        bd["_pk"] = bd["ID_Instrumento"].astype(int).astype(str) + "-" + bd["SubID_Instrumento"].astype(int).astype(str)
        eq = bd[bd["Investment_Type_Code"].eq(2) & bd["ISIN"].astype(str).str.len().gt(5) & bd["_pk"].isin(cubo["PK2"].astype(str))].iloc[0]
        bd = bd.drop(columns="_pk")
    m = (bd["ID_Instrumento"] == eq["ID_Instrumento"]) & (bd["SubID_Instrumento"] == eq["SubID_Instrumento"])
    bd.loc[m, ["Investment_Type_Code", "Issue_Type_Code", "Issuer_Type_Code", "Coupon_Type_Code", "Rank_Code"]] = [1, 3, 1, 1, 2]
    bd.to_excel(bix, index=False)
    r = runner.invoke(app, ["correr", "--fecha", "20260731", "--raiz", str(raiz), "--sin-bbg", "--sin-sql", "--sin-facts", "--sin-recalcular"])
    assert r.exit_code in (0, 1) and "impacto 20260731 v001: CLASIFICACION" in r.output and "se re-expresa con `publicar --reexpresar" in r.output, r.output
    r = runner.invoke(app, ["impacto", "--raiz", str(raiz), "--detalle"])
    assert r.exit_code == 1 and "CLASIFICACION" in r.output and "Investment_Type_Code" in r.output
    r = runner.invoke(app, ["recalcular", "--fecha", "20260731", "--raiz", str(raiz), "--motivo", "equity a FI"])
    assert r.exit_code == 0 and "v002 REEXPRESADA" in r.output and "PARCIAL" in r.output, r.output
    r = runner.invoke(app, ["pendientes", "--fecha", "20260731", "--raiz", str(raiz)])
    import re
    assert r.exit_code == 1 and int(re.search(r"(\d+) pendientes", r.output).group(1)) >= 1       # el instrumento puede estar en más de un fondo
    assert "YAS_BOND_YLD" in r.output and "--con-terminal" in r.output
    r = runner.invoke(app, ["versiones", "--fecha", "20260731", "--raiz", str(raiz)])
    assert "v002  REEXPRESADA  PARCIAL" in r.output and "equity a FI" in r.output
    r = runner.invoke(app, ["impacto", "--raiz", str(raiz)])
    assert r.exit_code == 0


def test_check_distingue_xbbg_instalado_que_no_carga(env_dim, xbbg_roto):
    r = runner.invoke(app, ["check", "--fecha", "20260731", "--raiz", str(env_dim)])
    assert r.exit_code == 0, r.output
    assert "[AVISO] xbbg" in r.output and "instalado pero no carga" in r.output and "DLL load failed" in r.output
    assert "--sin-bbg" in r.output and "py -3.12" in r.output
