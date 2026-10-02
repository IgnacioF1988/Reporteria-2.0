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
    assert out["Hedge_Origen"].tolist() == ["ANTERIOR", "REGLA"]
    assert set(al.loc[al["Nombre"] == "HEDGE_NUEVO", "PK2"]) == {"7-1"}


def test_cruce_por_id_cuando_el_pk2_cambio_de_moneda():
    import pandas as pd
    from reporteria.lectura.maestros import COLS_INSTR, tipar_bd_instrumentos
    from reporteria.universo import armar_universo
    bd = tipar_bd_instrumentos(pd.DataFrame([dict(ID_Instrumento=5, SubID_Instrumento=41, Name_Instrumento="BONO COP", ISIN="CO5", Risk_Currency="COP", Investment_Type_Code=1),
                                             dict(ID_Instrumento=1, SubID_Instrumento=1, Name_Instrumento="TPL", ISIN="", Risk_Currency="USD", Investment_Type_Code=2)]
                                            ).reindex(columns=["ID_Instrumento", "SubID_Instrumento"] + [c for c in COLS_INSTR if c not in ("ID_Instrumento", "SubID_Instrumento")]))
    cubo = pd.DataFrame([dict(PK2="5-38", ID_Fund=20, ID_Instrumento=5, id_CURR=38, BalanceSheet="Asset", TotalMVal=100.0, LocalPrice=100.0, Qty=1, OriginalFace=1, Factor=1, AI=0, MVBook=100.0),
                         dict(PK2="46023", ID_Fund=20, ID_Instrumento=1, id_CURR=1, BalanceSheet="Asset", TotalMVal=5.0, LocalPrice=1.0, Qty=5, OriginalFace=5, Factor=1, AI=0, MVBook=5.0)])
    funds = pd.DataFrame({"ID_Fund": [20], "FundShortName": ["MRCLP"], "FundBaseCurrency": ["CLP"]})
    pos, al = armar_universo(cubo, bd, funds)
    p = pos.set_index("PK2")
    assert p.loc["5-38", "Risk_Currency"] == "COP" and p.loc["5-38", "PK2_Maestro"] == "5-41" and p.loc["5-38", "Pos_Key"] == "20|5|Asset"
    assert p.loc["5-38", "Pos_ID"] == "20|5-38|Asset"                                   # la identidad sigue siendo la del CUBO
    assert pd.isna(p.loc["46023", "Risk_Currency"]) or p.loc["46023", "Risk_Currency"] == ""   # PK2 malformado: no se cruza por ID
    assert set(al["Nombre"]) == {"IDENTIDAD_PK2", "SIN_MAESTRO"} and al[al["Nombre"].eq("IDENTIDAD_PK2")]["PK2"].tolist() == ["5-38"]
