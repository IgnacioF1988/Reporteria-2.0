import pandas as pd

from reporteria.fuentes.facturas import candidatos_facturas

SETTLE = pd.Timestamp("2026-07-31")


def _pos(pk2, iid, fondo=20, trat="FACTURA", mv=1000.0):
    return dict(Pos_ID=f"{fondo}|{pk2}|Asset", ID_Fund=fondo, PK2=pk2, ID_Instrumento=iid, Tratamiento=trat,
                Risk_Currency="CLP", TotalMVal=mv)


def _rpt(**kw):
    base = dict(nemotecnico="FAC1", fondo="MRCLP", estado="vigente", fecha_pago=pd.NaT, monto_compra=1000.0,
                fecha_vencimiento=SETTLE + pd.Timedelta(days=45), tasa_mensual=0.008)
    base.update(kw)
    return base


HOMOL = {"FAC1": 101, "FAC2": 102, "FAC3": 103}
FONDOS = {"MRCLP": 20}


def test_yield_lineal_duration_y_cruce_por_homol():
    pos = pd.DataFrame([_pos("101-39", 101), _pos("102-39", 102), _pos("103-39", 103), _pos("9-39", 9, trat="CASCADA")])
    rpt = pd.DataFrame([_rpt(), _rpt(nemotecnico="FAC2", estado="pagado", fecha_pago=SETTLE - pd.Timedelta(days=1)),
                        _rpt(nemotecnico="FAC3", monto_compra=1030.0, fecha_vencimiento=SETTLE + pd.Timedelta(days=120),
                             tasa_mensual=0.01)])
    cand, al = candidatos_facturas(pos, rpt, HOMOL, FONDOS, SETTLE, tolerancia_monto=0.01)
    c = cand.set_index("PK2")
    assert abs(c.loc["101-39", "Yield"] - 0.096) < 1e-12 and abs(c.loc["101-39", "Duration"] - 45 / 365) < 1e-12
    assert not c.loc["102-39", "Valido"] and c.loc["102-39", "Motivo_Descarte"] == "SIN_FACTURA"   # pagada no cuenta
    assert abs(c.loc["103-39", "Yield"] - 0.12) < 1e-12 and abs(c.loc["103-39", "Duration"] - 120 / 365) < 1e-12
    assert (al["Nombre"] == "FACTURA_MONTO_DISTINTO").sum() == 1 and "9-39" not in c.index


def test_factura_sin_posicion_y_fondo_desconocido_avisan():
    pos = pd.DataFrame([_pos("101-39", 101)])
    rpt = pd.DataFrame([_rpt(), _rpt(nemotecnico="FAC2"), _rpt(fondo="OTRO")])
    cand, al = candidatos_facturas(pos, rpt, HOMOL, FONDOS, SETTLE, tolerancia_monto=0.01)
    assert len(cand) == 1 and cand.iloc[0]["Valido"]
    assert {"FACTURA_SIN_POSICION", "FACTURA_FONDO_DESCONOCIDO"} <= set(al["Nombre"])


def test_frame_de_facts_origen_tasa_prorroga_y_viva_por_fecha_pago():
    pos = pd.DataFrame([_pos("101-39", 101), _pos("102-39", 102), _pos("103-39", 103)])
    rpt = pd.DataFrame([_rpt(documento_operacion_id=1, tasa_origen="PRORROGA", vencimiento_origen="PRORROGA", cambios_revertidos=0),
                        _rpt(documento_operacion_id=2, nemotecnico="FAC2", estado="pagado", fecha_pago=SETTLE + pd.Timedelta(days=5),
                             tasa_origen="ORIGINAL", vencimiento_origen="ORIGINAL", cambios_revertidos=1),
                        _rpt(documento_operacion_id=3, nemotecnico="FAC3", estado="pagado", fecha_pago=SETTLE - pd.Timedelta(days=1),
                             tasa_origen="ORIGINAL", vencimiento_origen="ORIGINAL", cambios_revertidos=0)])
    cand, al = candidatos_facturas(pos, rpt, HOMOL, FONDOS, SETTLE)
    c = cand.set_index("PK2")
    assert c.loc["101-39", "Origen"] == "FACTS:FAC1" and "tasa_origen=PRORROGA" in c.loc["101-39", "Detalle"]
    assert c.loc["102-39", "Valido"]                                   # pagada después del cierre: viva al cierre
    assert not c.loc["103-39", "Valido"] and c.loc["103-39", "Motivo_Descarte"] == "SIN_FACTURA"
    assert (al["Nombre"] == "FACTURA_TASA_PRORROGA").sum() == 1 and (al["Nombre"] == "FACTURA_ASOF_REVERTIDA").sum() == 1
