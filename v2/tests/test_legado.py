import pandas as pd

from reporteria.legado import importar_cache_legacy


def test_importa_cache_desde_outputs_legacy(fixtures, tmp_path):
    n = importar_cache_legacy(fixtures.parent / "corporativo", tmp_path, "20260731")
    assert n["YAS_BOND_YLD"] > 400 and n["DES_CASH_FLOW"] == 33 and n["XCCY_CLP"] > 0
    y = pd.read_csv(tmp_path / "bdp_YAS_BOND_YLD_20260731_settle_dt-20260731.csv")
    assert y["ticker"].str.endswith(" Corp").all() and y["valor"].abs().max() > 1     # en %, como lo entrega BBG
    assert (tmp_path / "bds_DES_CASH_FLOW").is_dir()


def test_importa_curvas_de_drops_y_atributos_de_indice(fixtures, tmp_path):
    n = importar_cache_legacy(fixtures.parent / "corporativo", tmp_path, "20260731")
    assert n["CURVE_TENOR_RATES"] == 13 and n["INFLATION_LINKED_INDICATOR"] > 500 and n["RESET_IDX"] == 7
    c = pd.read_csv(tmp_path / "bds_CURVE_TENOR_RATES" / "YCSW0193 Index.csv")
    assert {"Tenor", "Tenor Ticker", "Mid Yield"} <= set(c.columns) and len(c) == 20
    r = pd.read_csv(tmp_path / "bdp_RESET_IDX_20260731.csv")
    assert set(r["valor"]) == {"BZDIOVRA", "MXIBTIEF", "MXIBTIIE"}


def test_migrar_manuales_fip_atributos_y_defaulteados(fixtures, tmp_path):
    from reporteria.legado import _fondos_alias, fusionar_reglas, migrar_manuales
    from reporteria.lectura.reglas import leer_reglas
    corp = fixtures.parent / "corporativo"
    alias = _fondos_alias(corp / "BD_FUNDS.xlsx", corp / "HOMOL_FUNDS.xlsx")
    assert alias["MRENTACLP"] == 20 and alias["MRCLP"] == 20 and alias["MLDL"] == 17 and alias["ALTURAS II"] == 2
    ids = {176142, 218892, 228178}
    nuevas, informe = migrar_manuales(corp / "legacy_manuales", alias, ids, incluir_defaulteados=False, template_cajas=corp / "Template_Cajas.xlsx")
    clas = nuevas["clasificacion"]
    assert clas[["ID_Fund", "Criterio", "Valor", "Bucket"]].values.tolist() == [[20, "PK2", "176142-38", "Equity"]]     # ISLA HOSTE (Bond) no genera fila
    atr = nuevas["overrides_atributo"]
    assert (atr["Field"] == "Indice").all() and (atr["ID_Fund"] == 17).all() and len(atr) == 9
    assert set(atr["Value"]) == {"BONCER", "UVR", "UI", "TIIE", "CDI"}
    assert atr.set_index("ID_Instrumento").loc[218892, "Value"] == "UVR" and atr.set_index("ID_Instrumento").loc[221555, "Value"] == "BONCER"
    assert "defaulteados" not in nuevas and "omitido" in informe.set_index("Archivo").loc["DEFAULTEADOS.xlsx", "Avisos"]
    assert len(nuevas["cajas"]) == 68 and informe.set_index("Archivo").loc["FIP.xlsx", "Filas_generadas"] == 1
    assert "no está en BD_INSTRUMENTOS" in informe.set_index("Archivo").loc["Atributos_MLDL.xlsx", "Avisos"]
    nuevas2, informe2 = migrar_manuales(corp / "legacy_manuales", alias, None, incluir_defaulteados=True)
    d = nuevas2["defaulteados"]
    assert len(d) == 176 and set(d["Estado"]) == {"DEF", "PROPDEF"} and 2 in set(d[d["ID_Instrumento"] == 441]["ID_Fund"])
    # fusionar: no duplica al aplicar dos veces y el resultado es un REGLAS válido
    salida = tmp_path / "REGLAS.xlsx"
    n1 = fusionar_reglas(fixtures / "REGLAS.xlsx", nuevas, salida)
    assert n1 == {"clasificacion": 0, "overrides_atributo": 9, "cajas": 0}           # FIP y cajas ya estaban en el fixture
    n2 = fusionar_reglas(salida, nuevas, salida)
    assert n2 == {"clasificacion": 0, "overrides_atributo": 0, "cajas": 0}
    rg = leer_reglas(salida)
    assert len(rg.overrides_atributo) == 9 and rg.clasificacion["ID"].tolist() == list(range(1, len(rg.clasificacion) + 1))
