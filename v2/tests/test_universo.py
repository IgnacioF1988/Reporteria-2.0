import pandas as pd

from reporteria.universo import asignar_hedge, marcar_familias

FONDOS = pd.DataFrame([(20, "A_CLP"), (17, "POR_PAIS")], columns=["ID_Fund", "Politica_Hedge"])


def _pos(**kw):
    base = dict(Pos_ID="20|1-1|Asset", ID_Fund=20, PK2="1-1", ISIN="US1", Name_Instrumento="SAMMIN 9 06/30/2031 REGS",
                Risk_Currency="USD", Risk_Country="BR", BalanceSheet="Asset")
    base.update(kw)
    return base


def test_familias_por_nombre_base():
    pos = pd.DataFrame([_pos(), _pos(Pos_ID="20|2-1|Asset", PK2="2-1", ISIN="US2", Name_Instrumento="SAMMIN 9 06/30/2031 144A"),
                        _pos(Pos_ID="16|2-1|Asset", ID_Fund=16, PK2="2-1", ISIN="US2", Name_Instrumento="SAMMIN 9 06/30/2031 144a"),
                        _pos(Pos_ID="20|3-1|Asset", PK2="3-1", ISIN="US3", Name_Instrumento="OTRO 5 2030 EMTN"),
                        _pos(Pos_ID="20|4-39|Asset", PK2="4-39", ISIN="", Name_Instrumento="SAMMIN 9 06/30/2031 REG S")])
    out = marcar_familias(pos, ("REGS", "REG S", "144A", "EMTN"))
    assert out["Base_Name"].tolist()[:2] == ["SAMMIN 9 06/30/2031"] * 2
    assert out.loc[0, "Familia"] == out.loc[1, "Familia"] == out.loc[2, "Familia"] == "US1"
    assert out.loc[0, "ISIN_Hermanos"] == "US2" and out.loc[1, "ISIN_Hermanos"] == "US1"
    assert out.loc[3, "Familia"] == "" and out.loc[3, "ISIN_Hermanos"] == ""      # solo, sin hermanos
    assert out.loc[4, "Familia"] == "" and out.loc[4, "ISIN_Hermanos"] == ""      # sin ISIN no entra a la familia


def test_hedge_por_politica_de_fondo():
    pos = pd.DataFrame([_pos(),                                                        # A_CLP + USD → CLP
                        _pos(Pos_ID="17|1-1|Asset", ID_Fund=17),                        # POR_PAIS + BR → BRL
                        _pos(Pos_ID="17|5-1|Asset", PK2="5-1", ID_Fund=17, Risk_Country="ZZ"),   # país sin mapeo
                        _pos(Pos_ID="20|6-39|Asset", PK2="6-39", Risk_Currency="CLP"),  # moneda local: sin hedge
                        _pos(Pos_ID="16|1-1|Asset", ID_Fund=16)])                       # fondo sin política
    out, al = asignar_hedge(pos, FONDOS, {"USD", "EUR", "GBP"}, {"BR": "BRL", "CL": "CLP"}, None)
    assert out["Hedge_Currency"].tolist() == ["CLP", "BRL", "", "", ""]
    assert out["Hedge_Origen"].tolist()[:2] == ["REGLA", "REGLA"]
    assert (al["Nombre"] == "HEDGE_PAIS_SIN_MONEDA").sum() == 1
    assert (al["Nombre"] == "HEDGE_NUEVO").sum() == 2                            # los dos hedgeados por regla, sin mes anterior


def test_hedge_hereda_del_cierre_anterior():
    pos = pd.DataFrame([_pos(), _pos(Pos_ID="20|7-1|Asset", PK2="7-1")])
    ant = pd.DataFrame([dict(Pos_ID="20|1-1|Asset", Hedge_Currency="")])          # el analista lo dejó sin hedge
    out, al = asignar_hedge(pos, FONDOS, {"USD"}, {"BR": "BRL"}, ant)
    assert out["Hedge_Currency"].tolist() == ["", "CLP"]
    assert out["Hedge_Origen"].tolist() == ["MES_ANTERIOR", "REGLA"]
    assert set(al.loc[al["Nombre"] == "HEDGE_NUEVO", "PK2"]) == {"7-1"}
