import numpy as np
import pandas as pd

from reporteria.agregados import aw_dw, verificar


def _pos():
    return pd.DataFrame([
        dict(Pos_ID="1|a|Asset", ID_Fund=1, Fondo="F", BalanceSheet="Asset", Estado="RESUELTO", Yield=0.10, Duration=2.0, TotalMVal=600.0, Bucket="Fixed Income"),
        dict(Pos_ID="1|b|Asset", ID_Fund=1, Fondo="F", BalanceSheet="Asset", Estado="RESUELTO", Yield=0.05, Duration=4.0, TotalMVal=200.0, Bucket="Fixed Income"),
        dict(Pos_ID="1|c|Asset", ID_Fund=1, Fondo="F", BalanceSheet="Asset", Estado="FALTANTE", Yield=np.nan, Duration=np.nan, TotalMVal=200.0, Bucket="Equity"),
        dict(Pos_ID="1|d|Liability", ID_Fund=1, Fondo="F", BalanceSheet="Liability", Estado="RESUELTO", Yield=0.08, Duration=1.0, TotalMVal=-100.0, Bucket="Financial Debt"),
        dict(Pos_ID="1|e|Asset", ID_Fund=1, Fondo="F", BalanceSheet="Asset", Estado="EXCLUIDO", Yield=0.0, Duration=0.0, TotalMVal=999.0, Bucket="Equity"),
    ])


def test_activos_pasivos_patrimonio():
    agg = aw_dw(_pos(), dimensiones=("Bucket",))
    t = agg[agg["Dimension"] == "TOTAL"].set_index("Nivel")
    # ACTIVOS: MV 1000; faltante entra a yield 0 y dur 0
    assert t.loc["ACTIVOS", "MV"] == 1000 and abs(t.loc["ACTIVOS", "AW"] - (60 + 10) / 1000) < 1e-12
    assert abs(t.loc["ACTIVOS", "DW"] - (0.10 * 600 * 2 + 0.05 * 200 * 4) / (600 * 2 + 200 * 4)) < 1e-12
    assert abs(t.loc["ACTIVOS", "Duration_AW"] - 2000 / 1000) < 1e-12 and abs(t.loc["ACTIVOS", "Cobertura"] - 0.8) < 1e-12
    # PASIVOS con su métrica; PATRIMONIO = A − P
    assert t.loc["PASIVOS", "MV"] == 100 and t.loc["PASIVOS", "AW"] == 0.08
    assert t.loc["PATRIMONIO", "MV"] == 900 and abs(t.loc["PATRIMONIO", "AW"] - (70 - 8) / 900) < 1e-12
    assert abs(t.loc["PATRIMONIO", "DW"] - (0.10 * 1200 + 0.05 * 800 - 0.08 * 100) / (1200 + 800 - 100)) < 1e-12
    # excluidos fuera; pesos por bucket suman 1 en cada nivel
    assert t["N"].tolist() == [3, 1, 4]
    b = agg[(agg["Dimension"] == "Bucket") & (agg["Nivel"] == "ACTIVOS")].set_index("Grupo")
    assert abs(b["Peso_MV"].sum() - 1) < 1e-12 and b.loc["Equity", "AW"] == 0 and abs(b.loc["Fixed Income", "Peso_MV"] - 0.8) < 1e-12
    assert verificar(agg).empty


def test_verificar_detecta_inconsistencia():
    agg = aw_dw(_pos(), dimensiones=("Bucket",))
    agg.loc[(agg["Dimension"] == "TOTAL") & (agg["Nivel"] == "PATRIMONIO"), "MV"] += 1
    assert verificar(agg)["Problema"].str.contains("MV_PAT").any()
