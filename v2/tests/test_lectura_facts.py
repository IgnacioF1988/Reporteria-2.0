from pathlib import Path

import pandas as pd
import pytest

from reporteria.adaptadores.facts import FixtureFacts
from reporteria.lectura.facts import facturas_al_cierre, normalizar_tablas

CORP = Path(__file__).parent / "fixtures" / "corporativo" / "facts"


@pytest.fixture(scope="module")
def tablas():
    return normalizar_tablas(FixtureFacts(CORP, "20260902").tablas("20260902"))


def _doc(df, doc):
    return df.set_index("documento_operacion_id").loc[doc]


def test_muestra_se_normaliza(tablas):
    f, p, c = tablas["facturas"], tablas["prorrogas"], tablas["cambios"]
    assert len(f) == 187 and len(p) == 20 and len(c) == 264
    assert f["tasa_mensual"].max() < 0.1 and pd.api.types.is_datetime64_any_dtype(f["fecha_vencimiento"])
    assert set(c["campo"]) >= {"tasa_interes", "fecha_vencimiento", "monto_compra"} and pd.api.types.is_datetime64_any_dtype(c["fecha"])


def test_vencimiento_asof_revierte_cambios_posteriores_al_cierre(tablas):
    # doc 58964: vencía 2026-06-12; el 2026-06-12 se prorrogó al 2026-07-17 (cambio registrado en bi_cambios)
    al_11 = _doc(facturas_al_cierre(tablas, pd.Timestamp("2026-06-11")), 58964)
    assert al_11["fecha_vencimiento"] == pd.Timestamp("2026-06-12") and al_11["vencimiento_origen"] == "CAMBIO_REVERTIDO"
    al_30 = _doc(facturas_al_cierre(tablas, pd.Timestamp("2026-06-30")), 58964)
    assert al_30["fecha_vencimiento"] == pd.Timestamp("2026-07-17") and al_30["tasa_origen"] == "PRORROGA"


def test_pagada_despues_del_cierre_sigue_viva(tablas):
    # doc 59396 pagada el 2026-08-18: viva al 31-07, no al 31-08
    assert 59396 in set(facturas_al_cierre(tablas, pd.Timestamp("2026-07-31"))["documento_operacion_id"])
    assert 59396 not in set(facturas_al_cierre(tablas, pd.Timestamp("2026-08-31"))["documento_operacion_id"])


def test_prorroga_vigente_manda_tasa_y_vencimiento(tablas):
    # doc 68147: original 0,85 % al 28-08; prórroga desde el 28-08 al 02-10 con 0,95 %
    hoy = _doc(facturas_al_cierre(tablas, pd.Timestamp("2026-09-02")), 68147)
    assert abs(hoy["tasa_mensual"] - 0.0095) < 1e-12 and hoy["fecha_vencimiento"] == pd.Timestamp("2026-10-02") and hoy["tasa_origen"] == "PRORROGA"
    antes = _doc(facturas_al_cierre(tablas, pd.Timestamp("2026-08-27")), 68147)
    assert abs(antes["tasa_mensual"] - 0.0085) < 1e-12 and antes["fecha_vencimiento"] == pd.Timestamp("2026-08-28") and antes["tasa_origen"] == "ORIGINAL"


def test_tasa_asof_y_cambio_del_mismo_dia_cuenta():
    f = pd.DataFrame([dict(documento_operacion_id=1, nemotecnico="FAC1", fondo="MRCLP", estado="vigente", monto_compra=100.0,
                           fecha_inversion="2026-05-01", fecha_vencimiento="2026-09-30", fecha_pago=None, tasa_mensual=0.0070, prorrogas=0)])
    c = pd.DataFrame([dict(documento_operacion_id=1, fecha="2026-06-04 10:51", campo="tasa_interes", valor_anterior="0.00660", valor_nuevo="0.00700"),
                      dict(documento_operacion_id=1, fecha="2026-07-10 09:00", campo="monto_compra", valor_anterior="90", valor_nuevo="100")])
    t = normalizar_tablas({"facturas": f, "prorrogas": None, "cambios": c})
    r = _doc(facturas_al_cierre(t, pd.Timestamp("2026-06-03")), 1)
    assert abs(r["tasa_mensual"] - 0.0066) < 1e-12 and r["tasa_origen"] == "CAMBIO_REVERTIDO" and r["monto_compra"] == 90 and r["cambios_revertidos"] == 2
    r = _doc(facturas_al_cierre(t, pd.Timestamp("2026-06-04")), 1)        # cambio del día del cierre: ya está en el cierre
    assert abs(r["tasa_mensual"] - 0.0070) < 1e-12 and r["cambios_revertidos"] == 1
    assert facturas_al_cierre(t, pd.Timestamp("2026-04-30")).empty      # comprada después del cierre: no existía


def test_columnas_faltantes_y_tasa_en_porcentaje():
    with pytest.raises(ValueError, match="bi_facturas"):
        normalizar_tablas({"facturas": pd.DataFrame(columns=["nemotecnico"])})
    f = pd.DataFrame([dict(documento_operacion_id=1, nemotecnico="FAC1", fondo="MRCLP", estado="vigente", monto_compra=100.0,
                           fecha_vencimiento="2026-09-30", fecha_pago=None, tasa_mensual=0.8)])
    with pytest.raises(ValueError, match="decimal"):
        normalizar_tablas({"facturas": f})
