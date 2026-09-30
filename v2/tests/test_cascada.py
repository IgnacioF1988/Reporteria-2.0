import pandas as pd

from reporteria.cascada import validar_candidatos
from reporteria.modelo import candidato


def _c(pk2, fuente, y, d, trat="CASCADA"):
    p = pd.Series(dict(Pos_ID=f"20|{pk2}|Asset", PK2=pk2))
    return candidato(p, fuente, y, d)


def test_sanidad_descarta_duration_no_positiva_y_yield_fuera_de_rango():
    cand = pd.DataFrame([_c("1-1", "JPM", 5.14, 0.0), _c("2-1", "BBG", 0.65, 0.5), _c("3-1", "RA", -0.6, 3.0),
                         _c("4-1", "CAJA", 0.0, 0.0), _c("5-1", "JPM", 0.07, 2.0)])
    out, al = validar_candidatos(cand, {"yield_max_proveedor": 1.0, "yield_min_proveedor": -0.5})
    c = out.set_index("PK2")
    assert c.loc["1-1", "Motivo_Descarte"] == "DURATION_NO_POSITIVA" and not c.loc["1-1", "Valido"]
    assert c.loc["2-1", "Valido"]                                                  # 65 % está dentro del rango
    assert c.loc["3-1", "Motivo_Descarte"] == "YIELD_FUERA_RANGO"
    assert c.loc["4-1", "Valido"] and c.loc["5-1", "Valido"]                       # CAJA no pasa por sanidad
    assert (al["Nombre"] == "PROVEEDOR_INVALIDO").sum() == 2


def test_def_pisa_al_proveedor_y_usa_parametros():
    from reporteria.cascada import elegir
    pos = pd.DataFrame([dict(Pos_ID="20|441-1|Asset", PK2="441-1", ID_Fund=20, ID_Instrumento=441, Tratamiento="CASCADA", TotalMVal=100.0, Yield_Type=15),
                        dict(Pos_ID="16|441-1|Asset", PK2="441-1", ID_Fund=16, ID_Instrumento=441, Tratamiento="CASCADA", TotalMVal=100.0, Yield_Type=15),
                        dict(Pos_ID="20|7-1|Asset", PK2="7-1", ID_Fund=20, ID_Instrumento=7, Tratamiento="CASCADA", TotalMVal=100.0, Yield_Type=15)])
    cand = pd.DataFrame([candidato(pos.iloc[0], "CSHF", 0.29, 2.4), candidato(pos.iloc[2], "JPM", 0.06, 3.0)])
    reglas_def = pd.DataFrame([dict(ID_Fund=None, ID_Instrumento=441, Estado="DEF", Fecha_Desde=pd.NaT, Fecha_Fin=pd.NaT)])
    out, c, _ = elegir(pos, cand, None, reglas_def, pd.Timestamp("2026-07-31"))
    o = out.set_index("Pos_ID")
    assert o.loc["20|441-1|Asset", "Fuente"] == "REGLA_DEF" and o.loc["20|441-1|Asset", "Yield"] == 0 and o.loc["20|441-1|Asset", "Duration"] == 0.5
    assert o.loc["16|441-1|Asset", "Estado"] == "RESUELTO" and o.loc["16|441-1|Asset", "Fuente"] == "REGLA_DEF"   # global: también sin candidato
    assert o.loc["20|7-1|Asset", "Fuente"] == "JPM"
    out, _, _ = elegir(pos, cand, None, reglas_def, pd.Timestamp("2026-07-31"), parametros={"duracion_def": 0, "yield_def": 0})
    assert out.set_index("Pos_ID").loc["16|441-1|Asset", "Duration"] == 0
