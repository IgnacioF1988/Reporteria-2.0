import pandas as pd

from reporteria import dim
from reporteria.adaptadores import dim as DIM


def _dims():
    clasif = dim.normalizar(pd.DataFrame([dict(ID=1, BalanceSheet="Asset", Investment_Type_Code=1, Bucket="Fixed Income"),
                                          dict(ID=2, ID_Fund=20, Investment_Type_Code=1, Issue_Type_Code=3, Emision_nacional=1, FX_Exposure="Chilean\nBonds")]))
    return DIM.Dimensionales(clasificacion=clasif, catalogos={"dim_issue_type": pd.DataFrame({"Issue_Type_Code": [0, 3], "Issue_Type": ["No Aplica", "Bond"]})},
                             fondos=pd.DataFrame({"ID_Fund": [20], "FundShortName": ["MRCLP"], "FundBaseCurrency": ["CLP"], "FundName": ["Renta CLP"]}),
                             monedas=pd.DataFrame({"id_CURR": [1], "Code": ["USD"], "Code_Supramoneda": ["USD"]}),
                             yld_flag=pd.DataFrame({"CalcType_final": ["PROPDEF"], "CalcType_exportable": ["PROP NP"]}), meta={"importado": "hoy"})


def test_escribir_y_leer_idempotente_con_nulos(tmp_path):
    d = _dims()
    p = DIM.escribir(tmp_path / "d" / "dimensionales.duckdb", d)
    d2 = DIM.leer(p)
    pd.testing.assert_frame_equal(d2.clasificacion, d.clasificacion)
    assert pd.isna(d2.clasificacion.loc[0, "ID_Fund"]) and d2.clasificacion.loc[1, "ID_Fund"] == 20
    assert d2.clasificacion.loc[1, "FX_Exposure"] == "Chilean Bonds"             # salto de línea normalizado
    assert d2.bd_funds().iloc[0].tolist() == [20, "MRCLP", "CLP"] and d2.bd_monedas().iloc[0]["Code"] == "USD"
    assert d2.yld_flag_dict() == {"PROPDEF": "PROP NP"} and d2.meta["importado"] == "hoy"
    assert (tmp_path / "d" / "csv" / "dim_clasificacion.csv").exists() and DIM.csv_al_dia(tmp_path / "d" / "csv", d2)
    assert d2.resumen()["dim_clasificacion"] == 2


def test_exportar_e_importar_excel_idempotente(tmp_path):
    d = _dims()
    x = DIM.exportar_excel(d, tmp_path / "DIM.xlsx")
    d2 = DIM.importar_excel(x)
    pd.testing.assert_frame_equal(d2.clasificacion, d.clasificacion)
    assert d2.catalogos["dim_issue_type"]["Issue_Type"].tolist() == ["No Aplica", "Bond"]
    assert len(d2.fondos) == 1 and len(d2.monedas) == 1


def test_leer_inexistente_explica_como_crear(tmp_path):
    try:
        DIM.leer(tmp_path / "no.duckdb")
    except FileNotFoundError as e:
        assert "dim importar" in str(e)
    else:
        raise AssertionError
