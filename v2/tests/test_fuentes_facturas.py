import pandas as pd

from reporteria.fuentes.facturas import candidatos_facturas


def test_yield_lineal_y_duration_a_vencimiento(settle):
    pos = pd.DataFrame([dict(Pos_ID="20|f-1|Asset", PK2="f-1", Tratamiento="FACTURA", TotalMVal=1000.0),
                        dict(Pos_ID="20|b-1|Asset", PK2="b-1", Tratamiento="CASCADA", TotalMVal=1.0)])
    fac = pd.DataFrame([dict(PK2="f-1", Tasa_Mensual=0.8, Monto=1000.0, Fecha_Vencimiento=settle + pd.Timedelta(days=45))])
    cand, al = candidatos_facturas(pos, fac, settle, tolerancia_monto=0.01)
    assert len(cand) == 1
    c = cand.iloc[0]
    assert abs(c["Yield"] - 0.096) < 1e-12 and abs(c["Duration"] - 45 / 365) < 1e-12
    assert c["Valido"] and c["Fuente"] == "FACTURA"
    assert al.empty


def test_monto_distinto_alerta_y_sin_factura_no_valido(settle):
    pos = pd.DataFrame([dict(Pos_ID="20|f-1|Asset", PK2="f-1", Tratamiento="FACTURA", TotalMVal=1000.0),
                        dict(Pos_ID="20|f-2|Asset", PK2="f-2", Tratamiento="FACTURA", TotalMVal=500.0)])
    fac = pd.DataFrame([dict(PK2="f-1", Tasa_Mensual=1.0, Monto=1030.0, Fecha_Vencimiento=settle + pd.Timedelta(days=10))])
    cand, al = candidatos_facturas(pos, fac, settle, tolerancia_monto=0.01)
    assert (al["Nombre"] == "FACTURA_MONTO_DISTINTO").sum() == 1
    f2 = cand[cand["PK2"] == "f-2"].iloc[0]
    assert not f2["Valido"] and f2["Motivo_Descarte"] == "SIN_FACTURA"
