import numpy as np
import pandas as pd

from reporteria.salida import comparar_carteras


def test_comparar_carteras():
    a = pd.DataFrame(dict(Pos_ID=["p1", "p2", "p3"], Yield=[0.05, np.nan, 0.07], Duration=[1.0, 2.0, 3.0], Fuente=["JPM", "", "BBG"], Conversion=["", "", "XCCY"], Estado=["RESUELTO", "FALTANTE", "RESUELTO"]))
    b = a.copy()
    assert comparar_carteras(a, b).empty
    b.loc[0, "Yield"] = 0.05 + 1e-12                       # dentro de la tolerancia
    b.loc[2, ["Yield", "Fuente"]] = [0.071, "CSHF"]
    b = pd.concat([b.iloc[:2], b.iloc[2:], pd.DataFrame(dict(Pos_ID=["p4"], Yield=[0.1], Duration=[1.0], Fuente=["RA"], Conversion=[""], Estado=["RESUELTO"]))])
    d = comparar_carteras(a, b)
    assert sorted(zip(d["Pos_ID"], d["Campo"])) == [("p3", "Fuente"), ("p3", "Yield"), ("p4", "Pos_ID")]


def test_plantilla_cajas_sugiere_sin_inventar():
    from reporteria.salida import plantilla_cajas
    settle = pd.Timestamp("2026-07-31")
    pos = pd.DataFrame([
        dict(Pos_ID="20|1247-1|Asset", ID_Fund=20, Fondo="MRCLP", PK2="1247-1", Name_Instrumento="JPMCC - 102-33838-2 Margin", Risk_Currency="USD", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=100.0),
        dict(Pos_ID="20|1247-61|Asset", ID_Fund=20, Fondo="MRCLP", PK2="1247-61", Name_Instrumento="JPMCC - 102-33838-2 Margin", Risk_Currency="EUR", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=50.0),
        dict(Pos_ID="20|875-1|Asset", ID_Fund=20, Fondo="MRCLP", PK2="875-1", Name_Instrumento="JPMULCD LX Equity", Risk_Currency="USD", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=900.0),
        dict(Pos_ID="19|221558-39|Liability", ID_Fund=19, Fondo="MRV", PK2="221558-39", Name_Instrumento="CR_CLP_SCOTIA_20260909_0.4800", Risk_Currency="CLP", Bucket="Financial Debt", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=-4000.0),
        dict(Pos_ID="20|1278-39|Asset", ID_Fund=20, Fondo="MRCLP", PK2="1278-39", Name_Instrumento="SCOTIABANK", Risk_Currency="CLP", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=10.0),
        dict(Pos_ID="36|1178-1|Asset", ID_Fund=36, Fondo="CARLYLE I", PK2="1178-1", Name_Instrumento="01-30-103486-1 BICE", Risk_Currency="USD", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="SIN_REGLA", TotalMVal=8.0),
        dict(Pos_ID="13|1245-1|Asset", ID_Fund=13, Fondo="MDLAT", PK2="1245-1", Name_Instrumento="JPMCC - 102-21946-2 Margin", Risk_Currency="USD", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="REGLAS/cajas", TotalMVal=5.0),
        dict(Pos_ID="13|1245-61|Asset", ID_Fund=13, Fondo="MDLAT", PK2="1245-61", Name_Instrumento="JPMCC - 102-21946-2 Margin", Risk_Currency="EUR", Bucket="Cash, Mutual Funds & Others", Fuente="CAJA", Origen="REGLAS/cajas", TotalMVal=5.0),
    ])
    cajas = pd.DataFrame([(13, "1245-1", "OBFR01 Index", -0.02, 1, ""), (13, "1245-61", "ESTRON Index", -0.02, 1, ""), (59, "875-1", "N.A.", 0.0429, 1, ""),
                          (17, "875-1", None, None, None, ""), (17, "1278-39", None, None, None, "")],      # filas vacías: no son referencia
                         columns=["ID_Fund", "PK2", "Indice_Referencia", "Spread_Anual", "Dias", "Comentario"]).astype({"Indice_Referencia": "str"})
    t = plantilla_cajas(pos, cajas, settle).set_index("PK2")
    assert len(t) == 6 and list(t.columns[:5]) == ["ID_Fund", "Indice_Referencia", "Spread_Anual", "Dias", "Comentario"]
    assert t.loc["875-1", "Indice_Referencia"] == "N.A." and t.loc["875-1", "Spread_Anual"] == 0.0429 and t.loc["875-1", "_Sugerencia"] == "COPIA_PK2"     # mismo PK2 en otro fondo
    assert t.loc["1247-1", "Indice_Referencia"] == "OBFR01 Index" and t.loc["1247-1", "Spread_Anual"] == -0.02 and t.loc["1247-1", "_Sugerencia"] == "POR_MONEDA"
    assert t.loc["1247-61", "Indice_Referencia"] == "ESTRON Index" and t.loc["1247-61", "_Sugerencia"] == "POR_MONEDA"
    assert t.loc["221558-39", "Indice_Referencia"] == "N.A." and abs(t.loc["221558-39", "Spread_Anual"] - 0.0576) < 1e-12 and t.loc["221558-39", "Dias"] == 40 and t.loc["221558-39", "_Sugerencia"] == "NOMBRE"
    assert pd.isna(t.loc["1278-39", "Indice_Referencia"]) and pd.isna(t.loc["1278-39", "Spread_Anual"]) and t.loc["1278-39", "_Sugerencia"] == ""    # cuenta corriente CLP: nada que sugerir
    assert t.loc["1178-1", "_Sugerencia"] == "" and pd.isna(t.loc["1178-1", "Indice_Referencia"])        # cuenta bancaria USD: otra familia, no hereda el OBFR01 de JPMCC
    assert t["_TotalMVal"].abs().is_monotonic_decreasing
