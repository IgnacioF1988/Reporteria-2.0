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
