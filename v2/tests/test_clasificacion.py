import pandas as pd

from reporteria.clasificacion import clasificar

CODES = dict(Investment_Type_Code=1, Issuer_Type_Code=1, Issue_Type_Code=3, Coupon_Type_Code=1, Rank_Code=2,
             Cash_Type_Code=0, Bank_Debt_Type_Code=0, Fund_Type_Code=0)


def _pos(**kw):
    base = dict(Pos_ID="20|x-39|Asset", ID_Fund=20, PK2="x-39", ID_Instrumento=1, Name_Instrumento="BONO",
                BalanceSheet="Asset", Emision_nacional=0, TotalMVal=100.0, **CODES)
    base.update(kw)
    return pd.DataFrame([base])


def _dim(*filas):
    """(ID, ID_Fund, BalanceSheet, Investment_Type_Code, Issue_Type_Code, Emision_nacional, Bucket, Ficha_FI, FX_Exposure)"""
    cols = ["ID", "ID_Fund", "BalanceSheet", "Investment_Type_Code", "Issue_Type_Code", "Emision_nacional", "Bucket", "Ficha_FI", "FX_Exposure"]
    return pd.DataFrame(list(filas), columns=cols)


DIM = _dim((1, None, "Asset", 1, None, None, "Fixed Income", "Bonds", None),
           (2, None, "Asset", 2, None, None, "Equity", "Other Assets", None),
           (3, None, None, 1, 3, None, None, None, "Corp. Bond"))
BUCKETS = pd.DataFrame([("Fixed Income", "CASCADA", 1), ("Equity", "CERO", 2), ("Cash, Mutual Funds & Others", "CAJA", 3)],
                       columns=["Bucket", "Tratamiento", "Orden"])


def _reglas(*filas):
    df = pd.DataFrame(list(filas), columns=["ID", "ID_Fund", "Criterio", "Valor", "Bucket", "Comentario"])
    df["Tratamiento"] = ""
    return df


def test_regla_de_tratamiento_sin_cambiar_bucket():
    r = _reglas((1, None, "Issue_Type_Code", "5", "", ""))
    r["Tratamiento"] = "FACTURA"
    out, _ = clasificar(_pos(Issue_Type_Code=5), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket"] == "Fixed Income" and out.iloc[0]["Bucket_Origen"] == "DIM:1"
    assert out.iloc[0]["Tratamiento"] == "FACTURA"


def test_dim_asigna_bucket_ficha_fx_y_tratamiento():
    out, al = clasificar(_pos(), DIM, BUCKETS, _reglas())
    f = out.iloc[0]
    assert (f["BalSheetKey"], f["Bucket"], f["Ficha_FI"], f["FX_Exposure"], f["Tratamiento"]) == \
           ("Asset11312000", "Fixed Income", "Bonds", "Corp. Bond", "CASCADA")
    assert (f["Bucket_Origen"], f["Ficha_Origen"], f["FX_Origen"]) == ("DIM:1", "DIM:1", "DIM:3")
    assert al.empty


def test_fila_del_fondo_en_dim_pisa_la_generica_solo_en_ese_fondo():
    d = pd.concat([DIM, _dim((4, 20, "Asset", 1, 4, None, "Cash, Mutual Funds & Others", None, None))])
    out, _ = clasificar(_pos(Issue_Type_Code=4), d, BUCKETS, _reglas())
    assert out.iloc[0]["Bucket"] == "Cash, Mutual Funds & Others" and out.iloc[0]["Tratamiento"] == "CAJA"
    assert out.iloc[0]["Bucket_Origen"] == "DIM:4" and out.iloc[0]["Ficha_FI"] == "Bonds"      # Ficha sigue de la genérica
    out, _ = clasificar(_pos(Issue_Type_Code=4, ID_Fund=16, Pos_ID="16|x-39|Asset"), d, BUCKETS, _reglas())
    assert out.iloc[0]["Bucket"] == "Fixed Income"


def test_regla_por_fondo_pisa_la_dim_y_solo_en_ese_fondo():
    r = _reglas((1, 20, "BalSheetKey", "Asset11412000", "Cash, Mutual Funds & Others", ""))
    out, _ = clasificar(_pos(Issue_Type_Code=4), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket"] == "Cash, Mutual Funds & Others" and out.iloc[0]["Tratamiento"] == "CAJA"
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:1"
    out, _ = clasificar(_pos(Issue_Type_Code=4, ID_Fund=16, Pos_ID="16|x-39|Asset"), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket"] == "Fixed Income"


def test_pk2_gana_a_balsheetkey_y_regex():
    r = _reglas((1, None, "BalSheetKey", "Asset11312000", "Equity", ""),
                (2, None, "Nombre_Regex", "^BONO", "Cash, Mutual Funds & Others", ""),
                (3, None, "PK2", "x-39", "Fixed Income", ""))
    out, al = clasificar(_pos(), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:3"
    assert (al["Nombre"] == "REGLA_AMBIGUA").sum() == 0          # el PK2 desempata


def test_empate_gana_menor_id_aunque_la_hoja_venga_desordenada():
    r = _reglas((7, None, "Nombre_Regex", "^BONO", "Equity", ""),
                (3, None, "Nombre_Regex", "BONO$", "Cash, Mutual Funds & Others", ""))
    out, al = clasificar(_pos(), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:3" and (al["Nombre"] == "REGLA_AMBIGUA").sum() == 1


def test_empate_con_el_mismo_resultado_no_es_ambiguo():
    r = _reglas((1, None, "Nombre_Regex", "^BONO", "Equity", ""),
                (2, None, "Nombre_Regex", "BONO$", "Equity", ""))
    out, al = clasificar(_pos(), DIM, BUCKETS, r)
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:1" and (al["Nombre"] == "REGLA_AMBIGUA").sum() == 0


def test_sin_fila_y_bucket_sin_tratamiento_alertan():
    out, al = clasificar(_pos(Investment_Type_Code=9), DIM, BUCKETS, _reglas())
    assert out.iloc[0]["Bucket"] == "SIN_REGLA" and out.iloc[0]["Tratamiento"] == "CASCADA"
    assert (al["Nombre"] == "SIN_REGLA").any()
    d = pd.concat([DIM, _dim((5, None, "Asset", 9, None, None, "Repo", "Financial Debt", None))])
    out, al = clasificar(_pos(Investment_Type_Code=9), d, BUCKETS, _reglas())
    assert out.iloc[0]["Bucket"] == "Repo" and (al["Nombre"] == "BUCKET_SIN_TRATAMIENTO").any()


def test_fx_exposure_por_fondo_con_emision_nacional():
    d = pd.concat([DIM, _dim((6, 20, None, 1, 3, 0, None, None, "Offshore Bonds"), (7, 20, None, 1, 3, 1, None, None, "Chilean Bonds"))])
    out, _ = clasificar(_pos(), d, BUCKETS, _reglas())
    assert out.iloc[0]["FX_Exposure"] == "Offshore Bonds"
    out, _ = clasificar(_pos(Emision_nacional=1), d, BUCKETS, _reglas())
    assert out.iloc[0]["FX_Exposure"] == "Chilean Bonds"
    out, _ = clasificar(_pos(ID_Fund=16, Pos_ID="16|x-39|Asset"), d, BUCKETS, _reglas())
    assert out.iloc[0]["FX_Exposure"] == "Corp. Bond"                                           # la genérica aplica a todos los fondos
