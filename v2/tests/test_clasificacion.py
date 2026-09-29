import pandas as pd

from reporteria.clasificacion import clasificar

CODES = dict(Investment_Type_Code=1, Issuer_Type_Code=1, Issue_Type_Code=3, Coupon_Type_Code=1, Rank_Code=2,
             Cash_Type_Code=0, Bank_Debt_Type_Code=0, Fund_Type_Code=0)


def _pos(**kw):
    base = dict(Pos_ID="20|x-39|Asset", ID_Fund=20, PK2="x-39", ID_Instrumento=1, Name_Instrumento="BONO",
                BalanceSheet="Asset", Emision_nacional=0, **CODES)
    base.update(kw)
    return pd.DataFrame([base])


BALANCE = pd.DataFrame([("Asset11312000", "Fixed Income", "Bonds"), ("Asset11412000", "Fixed Income", "Bonds"),
                        ("Asset20000000", "Equity", "Other Assets")],
                       columns=["BalSheetKey", "Bucket", "Ficha_FI"])
BUCKETS = pd.DataFrame([("Fixed Income", "CASCADA", 1), ("Equity", "CERO", 2), ("Cash, Mutual Funds & Others", "CAJA", 3)],
                       columns=["Bucket", "Tratamiento", "Orden"])


def _reglas(*filas):
    df = pd.DataFrame(list(filas), columns=["ID", "ID_Fund", "Criterio", "Valor", "Bucket", "Comentario"])
    df["Tratamiento"] = ""
    return df


def test_regla_de_tratamiento_sin_cambiar_bucket():
    r = _reglas((1, None, "Issue_Type_Code", "5", "", ""))
    r["Tratamiento"] = "FACTURA"
    bal = pd.concat([BALANCE, pd.DataFrame([("Asset11512000", "Fixed Income", "Loans/CLN")], columns=BALANCE.columns)])
    out, _ = clasificar(_pos(Issue_Type_Code=5), bal, BUCKETS, r, {})
    assert out.iloc[0]["Bucket"] == "Fixed Income" and out.iloc[0]["Bucket_Origen"] == "TABLA"
    assert out.iloc[0]["Tratamiento"] == "FACTURA"


def test_tabla_asigna_bucket_ficha_y_tratamiento():
    out, al = clasificar(_pos(), BALANCE, BUCKETS, _reglas(), {})
    f = out.iloc[0]
    assert (f["BalSheetKey"], f["Bucket"], f["Ficha_FI"], f["Tratamiento"], f["Bucket_Origen"]) == \
           ("Asset11312000", "Fixed Income", "Bonds", "CASCADA", "TABLA")
    assert al.empty


def test_regla_por_fondo_pisa_la_tabla_y_solo_en_ese_fondo():
    r = _reglas((1, 20, "BalSheetKey", "Asset11412000", "Cash, Mutual Funds & Others", ""))
    out, _ = clasificar(_pos(Issue_Type_Code=4), BALANCE, BUCKETS, r, {})
    assert out.iloc[0]["Bucket"] == "Cash, Mutual Funds & Others" and out.iloc[0]["Tratamiento"] == "CAJA"
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:1"
    out, _ = clasificar(_pos(Issue_Type_Code=4, ID_Fund=16, Pos_ID="16|x-39|Asset"), BALANCE, BUCKETS, r, {})
    assert out.iloc[0]["Bucket"] == "Fixed Income"


def test_pk2_gana_a_balsheetkey_y_regex():
    r = _reglas((1, None, "BalSheetKey", "Asset11312000", "Equity", ""),
                (2, None, "Nombre_Regex", "^BONO", "Cash, Mutual Funds & Others", ""),
                (3, None, "PK2", "x-39", "Fixed Income", ""))
    out, al = clasificar(_pos(), BALANCE, BUCKETS, r, {})
    assert out.iloc[0]["Bucket_Origen"] == "REGLA:3"
    assert (al["Nombre"] == "REGLA_AMBIGUA").sum() == 0          # el PK2 desempata


def test_sin_mapeo_y_bucket_sin_tratamiento_alertan():
    out, al = clasificar(_pos(Issue_Type_Code=9), BALANCE, BUCKETS, _reglas(), {})
    assert out.iloc[0]["Bucket"] == "SIN_REGLA" and out.iloc[0]["Tratamiento"] == "CASCADA"
    assert (al["Nombre"] == "SIN_REGLA").any()
    bal2 = pd.concat([BALANCE, pd.DataFrame([("Asset11912000", "Repo", "Financial Debt")], columns=BALANCE.columns)])
    out, al = clasificar(_pos(Issue_Type_Code=9), bal2, BUCKETS, _reglas(), {})
    assert out.iloc[0]["Bucket"] == "Repo" and (al["Nombre"] == "BUCKET_SIN_TRATAMIENTO").any()


def test_fx_exposure_por_fondo():
    fx20 = pd.DataFrame([dict(Investment_Type_Code=1, Fund_Type_Code=0, Bank_Debt_Type_Code=0, Issue_Type_Code=3,
                              Emision_nacional=0, FX_Exposure="Offshore Bonds"),
                         dict(Investment_Type_Code=1, Fund_Type_Code=0, Bank_Debt_Type_Code=0, Issue_Type_Code=3,
                              Emision_nacional=1, FX_Exposure="Chilean Bonds")])
    out, _ = clasificar(_pos(), BALANCE, BUCKETS, _reglas(), {20: fx20})
    assert out.iloc[0]["FX_Exposure"] == "Offshore Bonds"
    out, _ = clasificar(_pos(Emision_nacional=1), BALANCE, BUCKETS, _reglas(), {20: fx20})
    assert out.iloc[0]["FX_Exposure"] == "Chilean Bonds"
    out, _ = clasificar(_pos(ID_Fund=16, Pos_ID="16|x-39|Asset"), BALANCE, BUCKETS, _reglas(), {20: fx20})
    assert out.iloc[0]["FX_Exposure"] == ""
