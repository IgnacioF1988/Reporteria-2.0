import pandas as pd
import pytest

from reporteria import dim
from reporteria.modelo import CODIGOS

CODES = dict(Investment_Type_Code=1, Issuer_Type_Code=1, Issue_Type_Code=3, Coupon_Type_Code=1, Rank_Code=2,
             Cash_Type_Code=0, Bank_Debt_Type_Code=0, Fund_Type_Code=0)
COLS = ["ID", "ID_Fund", "BalanceSheet", "Investment_Type_Code", "Issuer_Type_Code", "Issue_Type_Code", "Emision_nacional",
        "Bucket", "Ficha_FI", "FX_Exposure"]


def _pos(*filas):
    out = []
    for i, kw in enumerate(filas):
        base = dict(Pos_ID=f"p{i}", ID_Fund=20, PK2=f"{i}-1", Name_Instrumento=f"N{i}", BalanceSheet="Asset", Emision_nacional=0, TotalMVal=10.0 * (i + 1), **CODES)
        base.update(kw)
        out.append(base)
    return pd.DataFrame(out)


def _dim(*filas):
    return pd.DataFrame(list(filas), columns=COLS)


def test_resolver_mas_especifica_gana_y_atributos_independientes():
    d = _dim((1, None, "Asset", 1, None, None, None, "Fixed Income", "Loans/CLN", None),
             (2, None, "Asset", 1, None, 3, None, None, "Bonds", None),
             (3, None, None, 1, None, None, None, None, None, "Loan/ Fund"),
             (4, None, None, 1, 2, 3, None, None, None, "Govt. Bond"))
    out, al = dim.resolver(_pos({}, {"Issuer_Type_Code": 2}, {"Issue_Type_Code": 2}), d)
    assert out["Bucket"].tolist() == ["Fixed Income"] * 3 and out["Bucket_Origen"].tolist() == ["DIM:1"] * 3
    assert out["Ficha_FI"].tolist() == ["Bonds", "Bonds", "Loans/CLN"]
    assert out["FX_Exposure"].tolist() == ["Loan/ Fund", "Govt. Bond", "Loan/ Fund"] and out["FX_Origen"].tolist() == ["DIM:3", "DIM:4", "DIM:3"]
    assert al.empty


def test_resolver_fila_del_fondo_gana_a_cualquier_generica():
    d = _dim((1, None, "Asset", 1, 1, 3, None, "Fixed Income", None, None),           # 4 columnas fijas
             (2, 20, "Asset", 1, None, None, None, "Cash, Mutual Funds & Others", None, None))   # 2 fijas + fondo
    out, _ = dim.resolver(_pos({}, {"ID_Fund": 16}), d)
    assert out["Bucket"].tolist() == ["Cash, Mutual Funds & Others", "Fixed Income"]


def test_resolver_empate_alerta_y_usa_menor_id():
    d = _dim((7, None, "Asset", 1, None, 3, None, "Equity", None, None),
             (3, None, "Asset", 1, 1, None, None, "Fixed Income", None, None))
    out, al = dim.resolver(_pos({}, {}), d)
    assert out["Bucket"].tolist() == ["Fixed Income"] * 2 and out["Bucket_Origen"].tolist() == ["DIM:3"] * 2
    assert len(al) == 1 and al.iloc[0]["Nombre"] == "DIM_AMBIGUA" and "Bucket" in al.iloc[0]["Detalle"]   # una por llave, no por posición


def test_resolver_sin_fila_queda_vacio_y_emision_nacional_solo_cuando_esta_fija():
    d = _dim((1, 20, None, 1, None, 3, 0, None, None, "Offshore Bonds"), (2, 20, None, 1, None, 3, 1, None, None, "Chilean Bonds"),
             (3, None, None, 1, None, None, None, None, None, "Loan/ Fund"))
    out, _ = dim.resolver(_pos({}, {"Emision_nacional": 1}, {"Emision_nacional": 1, "ID_Fund": 16}, {"Investment_Type_Code": 2}), d)
    assert out["FX_Exposure"].tolist() == ["Offshore Bonds", "Chilean Bonds", "Loan/ Fund", ""]
    assert out["Bucket"].tolist() == [""] * 4 and out["Bucket_Origen"].tolist() == [""] * 4


def test_resolver_respeta_vigencia():
    d = _dim((1, None, "Asset", 1, None, None, None, "Fixed Income", None, None))
    d["Vigente_Hasta"] = "2026-06-30"
    out, _ = dim.resolver(_pos({}), d, pd.Timestamp("2026-07-31"))
    assert out["Bucket"].tolist() == [""]
    out, _ = dim.resolver(_pos({}), d, pd.Timestamp("2026-06-30"))
    assert out["Bucket"].tolist() == ["Fixed Income"]


def _tabla(filas):
    """Llaves completas (BalanceSheet + 8 códigos) con un atributo."""
    out = []
    for bs, inv, issuer, issue, coupon, val in filas:
        out.append(dict(BalanceSheet=bs, Investment_Type_Code=inv, Issuer_Type_Code=issuer, Issue_Type_Code=issue, Coupon_Type_Code=coupon,
                        Rank_Code=2, Cash_Type_Code=0, Bank_Debt_Type_Code=0, Fund_Type_Code=0, Bucket=val))
    return pd.DataFrame(out)


def test_compactar_suelta_columnas_inutiles_y_generaliza_con_excepciones():
    t = _tabla([("Asset", 1, 1, 3, 1, "FI"), ("Asset", 1, 1, 3, 2, "FI"), ("Asset", 1, 2, 3, 1, "FI"), ("Asset", 1, 1, 4, 1, "FI"),
                ("Asset", 1, 1, 12, 1, "Cash"), ("Asset", 2, 0, 0, 0, "Equity"), ("Liability", 1, 1, 3, 1, "FI Short"), ("Liability", 2, 0, 0, 0, "Eq Short")])
    f = dim.compactar(t, ["BalanceSheet"] + CODIGOS, "Bucket")
    assert f["Coupon_Type_Code"].isna().all() and f["Issuer_Type_Code"].isna().all()         # no cambian el bucket → comodín
    assert f["BalanceSheet"].notna().all()                                                     # Asset/Liability siempre explícito
    fi = f[f["Bucket"].eq("FI")]
    assert len(fi) == 1 and pd.isna(fi.iloc[0]["Issue_Type_Code"])                            # default FI...
    assert (f[f["Bucket"].eq("Cash")]["Issue_Type_Code"] == 12).all()                          # ...con la excepción específica
    assert len(f) == 5
    # lossless: cada llave original vuelve a su valor
    pos = t.assign(Pos_ID=range(len(t)), ID_Fund=1, Emision_nacional=0, TotalMVal=1.0, Name_Instrumento="x", PK2="x")
    out, al = dim.resolver(pos, f.assign(ID=range(1, len(f) + 1), ID_Fund=pd.NA))
    assert out["Bucket"].tolist() == t["Bucket"].tolist() and al.empty


def test_compactar_vacios_heredan_y_conflicto_mantiene_columna():
    t = _tabla([("Asset", 1, 1, 3, 1, "FI"), ("Asset", 1, 1, 3, 2, ""), ("Asset", 1, 1, 4, 1, "Cash"), ("Asset", 1, 1, 4, 2, "FI")])
    f = dim.compactar(t, ["BalanceSheet"] + CODIGOS, "Bucket")
    assert f["Coupon_Type_Code"].notna().any()                                                 # Issue 4 cambia por cupón → se mantiene
    pos = t.assign(Pos_ID=range(4), ID_Fund=1, Emision_nacional=0, TotalMVal=1.0, Name_Instrumento="x", PK2="x")
    out, _ = dim.resolver(pos, f.assign(ID=range(1, len(f) + 1), ID_Fund=pd.NA))
    assert out["Bucket"].tolist() == ["FI", "FI", "Cash", "FI"]                                # la vacía hereda FI


def test_compactar_diferencial_solo_lo_que_difiere_de_la_base():
    gen = _tabla([("Asset", 1, 1, 3, 1, "FI"), ("Asset", 1, 1, 4, 1, "FI"), ("Asset", 1, 1, 4, 3, "FI"), ("Asset", 1, 2, 8, 1, "FI")])
    base = dim.normalizar(dim.compactar(gen, ["BalanceSheet"] + CODIGOS, "Bucket").assign(ID=[1]))
    fondo = gen.copy()
    fondo.loc[fondo["Issue_Type_Code"].isin([4, 8]), "Bucket"] = "Cash"
    f = dim.compactar(fondo, ["BalanceSheet"] + CODIGOS, "Bucket", base=base, fondo=20)
    assert (f["ID_Fund"] == 20).all() and set(f["Bucket"]) == {"Cash"} and len(f) <= 2
    pos = gen.assign(Pos_ID=range(4), ID_Fund=20, Emision_nacional=0, TotalMVal=1.0, Name_Instrumento="x", PK2="x")
    todo = dim.fusionar_filas([base, f])
    out, _ = dim.resolver(pos, todo)
    assert out["Bucket"].tolist() == ["FI", "Cash", "Cash", "Cash"]
    out, _ = dim.resolver(pos.assign(ID_Fund=16), todo)
    assert out["Bucket"].tolist() == ["FI"] * 4


def test_fusionar_filas_une_atributos_de_igual_llave_y_renumera():
    a = _dim((9, None, "Asset", 1, None, None, None, "FI", None, None)).drop(columns="ID")
    b = _dim((9, None, "Asset", 1, None, None, None, None, "Bonds", None)).drop(columns="ID")
    c = _dim((9, None, None, 1, None, None, None, None, None, "Loan")).drop(columns="ID")
    f = dim.fusionar_filas([a, b, c])
    assert len(f) == 2 and f["ID"].tolist() == [1, 2]
    fila = f[f["BalanceSheet"].eq("Asset")].iloc[0]
    assert (fila["Bucket"], fila["Ficha_FI"]) == ("FI", "Bonds") and pd.isna(fila["FX_Exposure"])


def test_validar_detecta_problemas():
    cat = {"dim_issue_type": pd.DataFrame({"Issue_Type_Code": [0, 3, 4]})}
    d = _dim((1, None, "Asset", 1, None, 3, None, "FI", None, None),
             (1, None, "Asset", 1, None, 99, None, "FI", None, None),              # ID duplicado + código fuera de catálogo
             (3, None, "Asset", 1, None, None, None, None, None, None),            # sin atributos
             (4, None, "Pasivo", 1, None, None, None, "FI", None, None),           # BalanceSheet inválido
             (5, 99, None, 1, None, None, None, "Otro", None, None),               # fondo y bucket desconocidos
             (6, None, "Asset", 1, 1, None, None, "Equity", None, None),           # solapa a igual especificidad con...
             (7, None, "Asset", 1, None, 3, None, "Cash", None, None))             # ...esta (ambas 3 fijas, distinto Bucket)
    prob = dim.validar(d, cat, buckets={"FI", "Equity", "Cash"}, fondos={20})
    txt = " || ".join(prob["Problema"])
    for esperado in ("ID duplicado", "Issue_Type_Code=99", "no define Bucket", "debe ser Asset o Liability", "ID_Fund=99", "Bucket 'Otro'", "DIM_AMBIGUA"):
        assert esperado in txt, esperado
    assert dim.validar(_dim((1, None, "Asset", 1, None, 3, None, "FI", None, None)), cat, {"FI"}, {20}).empty


def test_plantilla_sin_dim_agrupa_por_llave():
    pos = _pos({"Bucket": "SIN_REGLA", "Ficha_FI": "", "FX_Exposure": "Loan"}, {"Bucket": "SIN_REGLA", "Ficha_FI": "", "FX_Exposure": "Loan", "ID_Fund": 16},
               {"Bucket": "FI", "Ficha_FI": "Bonds", "FX_Exposure": ""}, {"Bucket": "FI", "Ficha_FI": "Bonds", "FX_Exposure": "Loan", "Issue_Type_Code": 4})
    pos.loc[2, "Investment_Type_Code"] = 2
    t = dim.plantilla_sin_dim(pos)
    assert len(t) == 2
    f = t.iloc[0]
    assert (f["_N_Posiciones"], f["_Fondos"], f["_Falta"], f["Bucket"], f["FX_Exposure"]) == (2, "16;20", "Bucket, Ficha_FI", "", "Loan")
    assert t.iloc[1]["_Falta"] == "FX_Exposure" and t.iloc[1]["Bucket"] == "FI"
