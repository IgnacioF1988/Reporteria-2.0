"""H10c: calendario de cierres diarios (una fecha por día calendario, entregada el siguiente hábil)."""
import datetime as dt

import pandas as pd

from reporteria import calendario as C


def test_habiles_feriados_y_siguiente_habil():
    fer = C.feriados_de(pd.DataFrame({"Fecha": ["2026-08-17", "2026-08-18"], "Mercado": ["", "US"], "Descripcion": ["feriado CL", "solo NY"]}))
    assert fer == {dt.date(2026, 8, 17)}                          # el de otro mercado no cuenta
    assert C.es_habil("20260731", fer) and not C.es_habil("20260801", fer) and not C.es_habil("20260817", fer)
    assert C.siguiente_habil("20260731", fer) == dt.date(2026, 8, 3)      # viernes → lunes
    assert C.siguiente_habil("20260801", fer) == dt.date(2026, 8, 3)      # sábado → lunes
    assert C.siguiente_habil("20260814", fer) == dt.date(2026, 8, 18)     # viernes + fin de semana + feriado del lunes 17 → martes 18
    assert C.fecha_disponible("20260803") == dt.date(2026, 8, 4)
    assert C.es_fin_de_mes("20260731") and C.es_fin_de_mes("20260228") and not C.es_fin_de_mes("20260730")
    assert C.fechas_calendario("20260730", "20260802") == ["20260730", "20260731", "20260801", "20260802"]
    assert C.feriados_de(None) == set() and C.feriados_de(pd.DataFrame()) == set()


def test_pendientes_lunes_cierra_viernes_a_domingo():
    cubos = {"20260731", "20260801", "20260802"}
    t = C.pendientes(["20260730"], "20260803", lambda f: f in cubos).set_index("fecha")
    assert list(t.index) == ["20260731", "20260801", "20260802"] and (t["estado"] == "LISTA").all()
    assert t.loc["20260731", "fin_de_mes"] and not t.loc["20260801", "habil"] and (t["esperada_el"] == "20260803").all()
    # el sábado 1 de agosto: el cierre del viernes aún no se espera (llega el lunes)
    t = C.pendientes(["20260730"], "20260801", lambda f: False)
    assert t["estado"].tolist() == ["AUN_NO_ESPERADA"] and t["fecha"].tolist() == ["20260731"]
    # el lunes sin CUBO del sábado: anomalía
    t = C.pendientes(["20260730"], "20260803", lambda f: f != "20260801").set_index("fecha")
    assert t.loc["20260801", "estado"] == "SIN_CUBO" and t.loc["20260731", "estado"] == "LISTA"
    # sin ninguna corrida: arranca en hoy−1; con `inicio`: desde ahí; nada pendiente → vacío
    assert C.pendientes([], "20260804", lambda f: True)["fecha"].tolist() == ["20260803"]
    assert C.pendientes([], "20260804", lambda f: True, inicio="20260801")["fecha"].tolist() == ["20260801", "20260802", "20260803"]
    assert C.pendientes(["20260803"], "20260804", lambda f: True).empty
