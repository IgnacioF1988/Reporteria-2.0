import numpy as np
import pandas as pd
import pytest

from reporteria.modelo import bal_sheet_key, limpiar_txt, pos_id, validar_decimal, vigente


def test_limpiar_txt_normaliza_nulos_y_espacios():
    s = pd.Series([" a ", None, np.nan, "nan", "None", 3])
    assert limpiar_txt(s).tolist() == ["a", "", "", "", "", "3"]


def test_pos_id_combina_fondo_pk2_balance():
    df = pd.DataFrame({"ID_Fund": [20], "PK2": [" 527-1 "], "BalanceSheet": ["Asset"]})
    assert pos_id(df).tolist() == ["20|527-1|Asset"]


def test_validar_decimal_rechaza_porcentajes():
    validar_decimal(pd.DataFrame({"Yield": [0.05, -0.01, np.nan]}), "Yield")
    with pytest.raises(ValueError, match="porcentaje"):
        validar_decimal(pd.DataFrame({"Yield": [5.2, 6.1]}), "Yield")


def test_bal_sheet_key_concatena_sin_relleno():
    df = pd.DataFrame([dict(BalanceSheet="Asset", Investment_Type_Code=1, Issuer_Type_Code=1, Issue_Type_Code=10,
                            Coupon_Type_Code=5, Rank_Code=5, Cash_Type_Code=0, Bank_Debt_Type_Code=0, Fund_Type_Code=0),
                       dict(BalanceSheet="Asset", Investment_Type_Code=3, Issuer_Type_Code=0, Issue_Type_Code=0,
                            Coupon_Type_Code=0, Rank_Code=0, Cash_Type_Code=2, Bank_Debt_Type_Code=0, Fund_Type_Code=0)])
    assert bal_sheet_key(df).tolist() == ["Asset111055000", "Asset30000200"]


def test_vigente_con_fechas_abiertas():
    df = pd.DataFrame({"Fecha_Desde": [pd.Timestamp("2026-01-01"), pd.NaT, pd.Timestamp("2026-09-01")],
                       "Fecha_Fin": [pd.Timestamp("2999-12-31"), pd.NaT, pd.NaT]})
    assert vigente(df, pd.Timestamp("2026-07-31")).tolist() == [True, True, False]
