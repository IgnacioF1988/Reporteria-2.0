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
    # 176 filas por fondo → 89 instrumentos globales (el legacy aplicaba DEF/PROPDEF por PK2 en todos los fondos)
    assert len(d) == 89 and set(d["Estado"]) == {"DEF", "PROPDEF"} and d["ID_Fund"].isna().all() and d["ID_Instrumento"].is_unique
    adasa = d[d["ID_Instrumento"] == 441].iloc[0]
    assert adasa["Estado"] == "DEF" and "ALTURAS II" in adasa["Comentario"] and "MDLAT" in adasa["Comentario"]
    # fusionar: no duplica al aplicar dos veces y el resultado es un REGLAS válido
    salida = tmp_path / "REGLAS.xlsx"
    n1 = fusionar_reglas(fixtures / "REGLAS.xlsx", nuevas, salida)
    assert n1 == {"clasificacion": 0, "overrides_atributo": 9, "cajas": 0}           # FIP y cajas ya estaban en el fixture
    n2 = fusionar_reglas(salida, nuevas, salida)
    assert n2 == {"clasificacion": 0, "overrides_atributo": 0, "cajas": 0}
    rg = leer_reglas(salida)
    assert len(rg.overrides_atributo) == 9 and rg.clasificacion["ID"].tolist() == list(range(1, len(rg.clasificacion) + 1))


def test_migrar_dimensionales_reproduce_balance_sheet_y_fx(fixtures):
    """Las filas compactadas dan exactamente lo mismo que BD_BalanceSheet / BD_FX_Exposure_* para cada llave y cada posición del CUBO."""
    import numpy as np
    from reporteria import dim, universo
    from reporteria.legado import migrar_dimensionales
    from reporteria.lectura import maestros as M
    from reporteria.lectura.cubo import leer_cubo
    from reporteria.modelo import CODIGOS, bal_sheet_key, limpiar_txt
    corp = fixtures.parent / "corporativo"
    dims, informe = migrar_dimensionales([corp])
    c = dims.clasificacion
    assert len(c) < 80 and (c["ID_Fund"] == 20).sum() <= 20 and c["ID"].is_unique
    assert set(informe["Fuente"]) >= {"BD_BalanceSheet.Investment_Type_CarteraFI", "BD_BalanceSheet.Investment_Type_CarteraFI_MRCLP",
                                      "BD_FX_Exposure_MLDL.xlsx", "BD_FX_Exposure_MRCLP.xlsx", "BD_FUNDS", "BD_Monedas", "BD_ISSUE_TYPE.xlsx"}
    assert dim.validar(c, dims.catalogos, fondos=set(dims.bd_funds()["ID_Fund"])).empty

    # 1) cada BalSheetKey de BD_BalanceSheet: Bucket genérico, Ficha_FI (donde la tenía) y variante MRCLP
    bs = pd.read_excel(corp / "BD_BalanceSheet.xlsx", sheet_name="BalSheet")
    pos = bs.rename(columns={"ASSET_TYPE": "BalanceSheet"}).assign(Pos_ID=range(len(bs)), ID_Fund=16, Emision_nacional=0, TotalMVal=1.0, PK2="x", Name_Instrumento="x")
    out, al = dim.resolver(pos, c)
    assert al.empty
    assert out["Bucket"].tolist() == limpiar_txt(bs["Investment_Type_CarteraFI"]).tolist()
    ficha = limpiar_txt(bs["Investment_Type_Ficha_FI"])
    assert (out["Ficha_FI"][ficha.ne("")] == ficha[ficha.ne("")]).all()
    assert out["Ficha_FI"].eq("").sum() < ficha.eq("").sum()                                              # parte de las vacías hereda
    out20, _ = dim.resolver(pos.assign(ID_Fund=20), c)
    assert out20["Bucket"].tolist() == limpiar_txt(bs["Investment_Type_CarteraFI_MRCLP"]).tolist()

    # 2) cada posición del CUBO: igual que el camino antiguo (tabla por BalSheetKey + BD_FX_Exposure por fondo)
    cubo = leer_cubo(corp / "CUBO_20260731.xlsx")
    bd_funds = M.leer_bd_funds(corp / "BD_FUNDS.xlsx")
    p, _ = universo.armar_universo(cubo, M.leer_bd_instrumentos(corp / "BD_INSTRUMENTOS.xlsx"), bd_funds, M.leer_bd_monedas(corp / "BD_Monedas.xlsx"))
    nuevo, _ = dim.resolver(p, c)
    viejo = M.leer_bd_balance_sheet(corp / "BD_BalanceSheet.xlsx").set_index("BalSheetKey")
    key = bal_sheet_key(p)
    bucket_viejo = key.map(viejo["Bucket"]).fillna("")
    mrclp_viejo = key.map(bs.set_index("BalSheetKey")["Investment_Type_CarteraFI_MRCLP"]).fillna("")      # antes: REGLAS/clasificacion 1–5
    es20 = p["ID_Fund"].eq(20)
    assert (nuevo["Bucket"][~es20] == bucket_viejo[~es20]).all() and (nuevo["Bucket"][es20] == mrclp_viejo[es20]).all()
    ficha_vieja = key.map(viejo["Ficha_FI"]).fillna("")
    assert (nuevo["Ficha_FI"][ficha_vieja.ne("")] == ficha_vieja[ficha_vieja.ne("")]).all()
    for nombre in ("MRCLP", "MLDL"):
        fid = M.fondo_de_fx_exposure(corp / f"BD_FX_Exposure_{nombre}.xlsx", bd_funds)
        tabla = M.leer_fx_exposure(corp / f"BD_FX_Exposure_{nombre}.xlsx")
        cols = [x for x in tabla.columns if x != "FX_Exposure"]
        sub = p[p["ID_Fund"] == fid]
        llaves = sub[cols].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
        fx_viejo = llaves.merge(tabla, on=cols, how="left")["FX_Exposure"].fillna("").to_numpy()
        assert (nuevo.loc[sub.index, "FX_Exposure"].to_numpy() == fx_viejo).all(), nombre
    # 3) los fondos sin tabla propia ahora reciben la genérica (decisión H8)
    otros = p[~p["ID_Fund"].isin([20, M.fondo_de_fx_exposure(corp / "BD_FX_Exposure_MLDL.xlsx", bd_funds)])]
    assert nuevo.loc[otros.index, "FX_Exposure"].ne("").mean() > 0.9
