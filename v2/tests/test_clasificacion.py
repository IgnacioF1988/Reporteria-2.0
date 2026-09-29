import pandas as pd
import pytest

from reporteria.clasificacion import clasificar


def _pos(**kw):
    base = dict(Pos_ID="20|x|Asset", ID_Fund=20, PK2="x", Name_Instrumento="BONO", Investment_Type_Code=1,
                Issue_Type_Code=3, Source="GENEVA", BalanceSheet="Asset")
    base.update(kw)
    return pd.DataFrame([base])


def _reglas(*filas):
    cols = ["ID", "ID_Fund", "Criterio", "Valor", "Bucket", "Tratamiento", "Yield", "Duration", "Comentario"]
    return pd.DataFrame(list(filas), columns=cols)


def test_regla_por_fondo_gana_a_global():
    r = _reglas((1, None, "Issue_Type_Code", "4", "RENTA_FIJA", "CASCADA", None, None, ""),
                (2, 20, "Issue_Type_Code", "4", "CAJA", "FIJO", 0, 0, ""))
    out, al = clasificar(_pos(Issue_Type_Code=4), r)
    assert out.iloc[0]["Bucket"] == "CAJA" and out.iloc[0]["Regla_ID"] == 2
    out, _ = clasificar(_pos(Issue_Type_Code=4, ID_Fund=11, Pos_ID="11|x|Asset"), r)
    assert out.iloc[0]["Bucket"] == "RENTA_FIJA"


def test_criterio_mas_especifico_gana():
    r = _reglas((1, None, "Investment_Type_Code", "5", "BANK_DEBT", "CASCADA", None, None, ""),
                (2, None, "Nombre_Regex", "^SIM_", "PACTOS_SIMULTANEAS", "FIJO", None, None, ""),
                (3, None, "PK2", "sim-1", "CAJA", "FIJO", 0, 0, ""))
    out, _ = clasificar(_pos(Investment_Type_Code=5, Name_Instrumento="SIM_LV", PK2="otro"), r)
    assert out.iloc[0]["Bucket"] == "PACTOS_SIMULTANEAS"
    out, _ = clasificar(_pos(Investment_Type_Code=5, Name_Instrumento="SIM_LV", PK2="sim-1"), r)
    assert out.iloc[0]["Bucket"] == "CAJA"


def test_sin_regla_y_ambigua_generan_alerta():
    r = _reglas((1, None, "Issue_Type_Code", "3", "A", "CASCADA", None, None, ""),
                (2, None, "Investment_Type_Code", "1", "B", "CASCADA", None, None, ""))
    out, al = clasificar(_pos(Issue_Type_Code=99, Investment_Type_Code=99), r)
    assert out.iloc[0]["Bucket"] == "SIN_REGLA" and out.iloc[0]["Tratamiento"] == "CASCADA"
    assert (al["Nombre"] == "SIN_REGLA").any()

    r2 = _reglas((1, None, "Issue_Type_Code", "3", "A", "CASCADA", None, None, ""),
                 (2, None, "Issue_Type_Code", "3", "B", "CASCADA", None, None, ""))
    out, al = clasificar(_pos(), r2)
    assert out.iloc[0]["Bucket"] == "A" and (al["Nombre"] == "REGLA_AMBIGUA").any()
