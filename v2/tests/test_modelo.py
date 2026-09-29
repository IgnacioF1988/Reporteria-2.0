import numpy as np
import pandas as pd
import pytest

from reporteria.modelo import limpiar_txt, pos_id, validar_decimal


def test_limpiar_txt_normaliza_nulos_y_espacios():
    s = pd.Series([" a ", None, np.nan, "nan", "None", 3])
    assert limpiar_txt(s).tolist() == ["a", "", "", "", "", "3"]


def test_pos_id_combina_fondo_pk2_balance():
    df = pd.DataFrame({"ID_Fund": [20], "PK2": [" 527-1 "], "BalanceSheet": ["Asset"]})
    assert pos_id(df).tolist() == ["20|527-1|Asset"]


def test_validar_decimal_rechaza_porcentajes():
    ok = pd.DataFrame({"Yield": [0.05, -0.01, np.nan]})
    validar_decimal(ok, "Yield")
    with pytest.raises(ValueError, match="porcentaje"):
        validar_decimal(pd.DataFrame({"Yield": [5.2, 6.1]}), "Yield")
