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


def test_overrides_valor_pisan_todo_y_dejan_rastro():
    from reporteria.overrides import aplicar_valores
    pos = _pos()
    for c, v in (("Tratamiento", "CASCADA"), ("Yield", 0.05), ("Duration", 3.0), ("Yield_Moneda", "USD"), ("Fuente", "JPM"), ("Etapa", "JPM"),
                 ("Origen", ""), ("Estado", "RESUELTO"), ("Motivo", ""), ("CalcType", "15"), ("Conversion", "XCCY"), ("Name_Instrumento", "x"), ("TotalMVal", 1.0)):
        pos[c] = v
    pos.loc[1, ["Yield", "Duration", "Estado", "Fuente"]] = [float("nan"), float("nan"), "FALTANTE", ""]
    ov = pd.DataFrame([(20, 1, 1, 0.12, 1.0, None, None, "Y", "ajuste PM"),          # fondo 20 → yield y duration
                       (16, 1, 1, 0.09, None, None, None, "", ""),                     # solo yield: la duration se conserva (era NaN)
                       (None, 1, 39, 0.20, 2.0, "2026-01-01", "2026-06-30", "", ""),   # vencido
                       (20, 999, 1, 0.5, 1.0, None, None, "", "")],                    # sin posición
                      columns=["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Yield", "Duration", "Fecha_Desde", "Fecha_Fin", "Moneda", "Comentario"])
    out, cand, al = aplicar_valores(pos, ov, SETTLE)
    o = out.set_index("Pos_ID")
    assert o.loc["20|1-1|Asset", ["Yield", "Duration", "Fuente", "Estado", "Conversion", "CalcType", "Overrides"]].tolist() == [0.12, 1.0, "OVERRIDE", "RESUELTO", "OVERRIDE", "PROP", "Yield"]
    assert o.loc["16|1-1|Asset", "Yield"] == 0.09 and pd.isna(o.loc["16|1-1|Asset", "Duration"]) and o.loc["16|1-1|Asset", "Estado"] == "RESUELTO"
    assert o.loc["20|1-39|Asset", "Yield"] == 0.05 and o.loc["20|1-39|Asset", "Fuente"] == "JPM"
    assert len(cand) == 2 and (cand["Fuente"] == "OVERRIDE").all() and (al["Nombre"] == "OVERRIDE_SIN_POSICION").sum() == 1
