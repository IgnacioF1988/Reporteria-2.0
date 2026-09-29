import pandas as pd

from reporteria.fuentes.jpm import candidatos_jpm
from reporteria.fuentes.ra import candidatos_ra


def _pos(pk2, isin, herm="", trat="CASCADA", nombre="BONO", ccy="USD"):
    return dict(Pos_ID=f"20|{pk2}|Asset", ID_Fund=20, PK2=pk2, ISIN=isin, ISIN_Hermanos=herm, Tratamiento=trat,
                Name_Instrumento=nombre, Risk_Currency=ccy, Yield_Type=15)


JPM = pd.DataFrame([("US1", 0.065, 2.5, "CEMBI"), ("US2", 0.07, float("nan"), "CEMBI"), ("US9", 0.05, 4.0, "GBI")],
                   columns=["ISIN", "Yield", "Duration", "Fuente"])


def test_jpm_directo_hermano_e_incompleto():
    pos = pd.DataFrame([_pos("1-1", "US1"), _pos("2-1", "US2"), _pos("3-1", "US3", herm="US9"), _pos("4-1", "US4"),
                        _pos("5-1", "US1", trat="CAJA")])
    cand, al = candidatos_jpm(pos, JPM)
    c = cand.set_index("PK2")
    assert c.loc["1-1", "Valido"] and c.loc["1-1", "Origen"] == "DIRECTO" and abs(c.loc["1-1", "Yield"] - 0.065) < 1e-12
    assert not c.loc["2-1", "Valido"] and c.loc["2-1", "Motivo_Descarte"] == "TUPLA_INCOMPLETA"
    assert c.loc["3-1", "Valido"] and c.loc["3-1", "Origen"] == "HERMANO:US9" and "GBI" in c.loc["3-1", "Detalle"]
    assert "4-1" not in c.index and "5-1" in c.index                                      # sin dato; CAJA también consulta
    assert (al["Nombre"] == "FAMILIA_INFERIDA").sum() == 1


RA = pd.DataFrame([("BADAL-B", 0.031178, 4.302474, "CLF", "SEMESTRAL"), ("BTP0281033", 0.055124, 6.41, "CLP", "SEMESTRAL")],
                  columns=["Nemo", "Yield", "Duration", "Moneda", "Periodicidad"])


def test_ra_por_nemotecnico_con_moneda_de_ra():
    pos = pd.DataFrame([_pos("1-38", "", nombre="badal-b ", ccy="CLF"), _pos("2-39", "", nombre="BTP0281033", ccy="CLP"),
                        _pos("3-39", "", nombre="OTRO", ccy="CLP")])
    cand, al = candidatos_ra(pos, RA)
    c = cand.set_index("PK2")
    assert abs(c.loc["1-38", "Yield"] - 0.031178) < 1e-12 and c.loc["1-38", "Yield_Moneda"] == "CLF" and c.loc["1-38", "Valido"]
    assert "SEMESTRAL" in c.loc["2-39", "Detalle"] and "3-39" not in c.index and al.empty
