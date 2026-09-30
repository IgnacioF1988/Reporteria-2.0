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
