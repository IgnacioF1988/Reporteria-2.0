import pandas as pd

from reporteria.overrides import aplicar_atributos

SETTLE = pd.Timestamp("2026-07-31")


def _ov(*filas):
    return pd.DataFrame(list(filas), columns=["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Field", "Value",
                                              "Fecha_Desde", "Fecha_Fin"])


def _pos():
    return pd.DataFrame([dict(Pos_ID="20|1-1|Asset", ID_Fund=20, PK2="1-1", ID_Instrumento=1, SubID_Instrumento=1,
                              Hedge_Currency="CLP", Risk_Currency="USD", Bucket="Fixed Income"),
                         dict(Pos_ID="16|1-1|Asset", ID_Fund=16, PK2="1-1", ID_Instrumento=1, SubID_Instrumento=1,
                              Hedge_Currency="", Risk_Currency="USD", Bucket="Fixed Income"),
                         dict(Pos_ID="20|1-39|Asset", ID_Fund=20, PK2="1-39", ID_Instrumento=1, SubID_Instrumento=39,
                              Hedge_Currency="", Risk_Currency="CLP", Bucket="Fixed Income")])


def test_aplica_por_fondo_todos_los_fondos_y_todas_las_monedas():
    ov = _ov((20, 1, 1, "Hedge_Currency", "SIN_HEDGE", None, None),          # solo fondo 20, PK2 1-1
             (None, 1, None, "Risk_Country", "CL", None, None),               # todos los fondos y sub-IDs del instrumento 1
             (16, 1, 1, "Hedge_Currency", "BRL", "2026-01-01", "2026-06-30"))  # vencido: no aplica
    out, al = aplicar_atributos(_pos(), ov, SETTLE, ("Hedge_Currency", "Risk_Country"))
    assert out.set_index("Pos_ID")["Hedge_Currency"].tolist() == ["", "", ""]
    assert (out["Risk_Country"] == "CL").all()
    assert out.set_index("Pos_ID").loc["20|1-1|Asset", "Overrides"] == "Hedge_Currency;Risk_Country"
    assert al.empty


def test_override_sin_posicion_avisa():
    ov = _ov((20, 999, 1, "Bucket", "Equity", None, None))
    out, al = aplicar_atributos(_pos(), ov, SETTLE, ("Bucket",))
    assert (out["Bucket"] == "Fixed Income").all() and (al["Nombre"] == "OVERRIDE_SIN_POSICION").sum() == 1
