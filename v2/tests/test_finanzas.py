import numpy as np
import pandas as pd

from reporteria.finanzas import duracion, xirr

S = pd.Timestamp("2026-07-31")


def _fechas(n, meses):
    return [S + pd.DateOffset(months=meses * i) for i in range(1, n + 1)]


def test_xirr_bono_a_la_par_semestral():
    fechas = _fechas(4, 6)                                  # 2 años, cupón 2.5 % semestral
    flujos = [2.5, 2.5, 2.5, 102.5]
    y = xirr([-100] + flujos, [S] + fechas)
    assert abs(y - ((1 + 0.025) ** 2 - 1)) < 2e-4           # ≈ 5.06 % efectiva anual (ACT/365.25 vs meses exactos)


def test_duracion_cero_cupon_igual_al_plazo():
    fechas = [S + pd.Timedelta(days=1461)]                  # 4 años exactos en ACT/365.25
    y = xirr([-80, 100], [S] + fechas)
    mac, mod = duracion([100], fechas, S, y)
    assert abs(mac - 4.0) < 1e-9 and abs(mod - 4.0 / (1 + y)) < 1e-9


def test_xirr_sin_flujos_o_todo_cero_es_nan():
    assert np.isnan(xirr([-100], [S]))
    assert np.isnan(xirr([-100, 0, 0], [S] + _fechas(2, 6)))
    assert all(np.isnan(v) for v in duracion([], [], S, 0.05))


def test_xirr_con_flujo_a_un_siglo_no_lanza():
    """Un flujo a 100 años hace que (1+r)**t desborde en los extremos del bracket: antes ZeroDivisionError, ahora número o NaN."""
    from reporteria.finanzas import xirr
    y = xirr([-100.0, 5.0, 105.0], ["2026-07-31", "2027-07-31", "2126-07-31"])
    assert y != y or -0.9999 < y < 100
    y2 = xirr([-100.0, 150.0], ["2026-07-31", "2126-07-31"])
    assert abs(y2 - (1.5 ** (1 / 100.0) - 1)) < 1e-6
