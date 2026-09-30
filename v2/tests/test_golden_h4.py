"""H4: las fórmulas de conversión reproducen los 243 breakeven y los 106 drops del legacy de julio (±1 bp) con las mismas
curvas; sobre la muestra, el pipeline completo reproduce los que caen en ella y reexpresa la duration."""
import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import INDICES, Rutas
from reporteria.curvas import curvas_drops, interpolar, leer_curvas_csv
from reporteria.finanzas import breakeven, drop
from reporteria.pipeline import Opciones, correr


def test_breakeven_reproduce_los_243_del_legacy(fixtures, settle):
    gold = pd.read_csv(fixtures / "golden_breakeven_20260731.csv")
    real = leer_curvas_csv(next(fixtures.glob("Carga_Indexes_20260731*.csv")), settle)
    nom = leer_curvas_csv(next(fixtures.glob("Carga_CurvasSoberanas_20260731*.csv")), settle)
    malos = []
    for _, g in gold.iterrows():
        cfg = INDICES[g["Index_Type"]]
        dias = g["Duration_original"] * 365.25
        r_real, _ = interpolar(dias, real[cfg["real"]]); r_nom, _ = interpolar(dias, nom[cfg["nom"]])
        y, d, _ = breakeven(g["Yield_original"] / 100, g["Duration_original"], r_real, r_nom)
        if abs(y - g["Yield_local"]) > 1e-4 or abs(r_real * 100 - g["r_real_%"]) > 1e-6 or not d < g["Duration_original"]:
            malos.append((g["PK2"], g["Yield_local"], y))
    assert len(gold) == 243 and not malos, malos[:5]


def test_drops_reproducen_los_106_del_legacy(fixtures, bbg):
    gold = pd.read_csv(fixtures / "golden_drops_20260731.csv")
    cur, _, _ = curvas_drops(bbg, set(gold["Hedge_Currency"]), "20260731")
    malos = []
    for _, g in gold.iterrows():
        c, dias = cur[g["Hedge_Currency"]], g["Duration_original"] * 365.25
        rl, _ = interpolar(dias, c["local"]); ru, _ = interpolar(dias, c["usd"])
        rb, _ = interpolar(dias, c["basis"]) if "basis" in c else (0.0, False)
        y, _ = drop(g["Yield_USD"] / 100, rl, ru, rb)
        if abs(y - g["Yield_Drop"] / 100) > 1e-4:
            malos.append((g["PK2"], g["Hedge_Currency"], g["Yield_Drop"] / 100, y))
    assert len(gold) == 106 and not malos, malos[:5]


def test_pipeline_convierte_la_muestra(fixtures, tmp_path):
    r = Rutas.para_pruebas("20260731", fixtures, tmp_path)
    res = correr(r, Opciones(sin_bbg=True, sin_sql=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fixtures, "20260731")))
    pos = res.posiciones
    n = pos["Conversion"].value_counts()
    assert n["BREAKEVEN"] >= 12 and n["XCCY"] >= 10 and n["DROP"] >= 5 and n["OVERRIDE"] == 1      # muestra de julio
    conv = pos[pos["Conversion"].isin(["BREAKEVEN", "XCCY", "DROP"])]
    assert (conv["Yield"] != conv["Yield_Papel"]).all() and conv["Yield_Moneda"].isin(["CLP", "COP"]).all()   # UVR (fondo 74) → COP
    be = pos[pos["Conversion"] == "BREAKEVEN"]
    assert (be["Duration"] < be["Duration_Papel"]).all() and be["Risk_Currency"].isin(["CLF", "UVR COSTER"]).all()
    gb = pd.read_csv(fixtures / "golden_breakeven_20260731.csv")
    gb["Pos_ID"] = gb["ID_Fund"].astype(str) + "|" + gb["PK2"] + "|Asset"
    m = gb.merge(be, on="Pos_ID")
    assert len(m) >= 12 and ((m["Yield"] - m["Yield_local"]).abs() < 1e-4).all()
    gd = pd.read_csv(fixtures / "golden_drops_20260731.csv")
    gd["Pos_ID"] = gd["ID_Fund"].astype(str) + "|" + gd["PK2"] + "|Asset"
    m = gd.merge(pos[pos["Conversion"].isin(["XCCY", "DROP"])], on="Pos_ID")
    assert len(m) >= 15 and ((m["Yield_Drop_y"] - m["Yield_Drop_x"] / 100).abs() < 1e-4).all()
    con_xccy = m[m["BBG_XCCY_Yield"].notna()]
    assert (con_xccy["Conversion"] == "XCCY").all() and ((con_xccy["Yield"] - con_xccy["BBG_XCCY_Yield"] / 100).abs() < 1e-9).all()
    assert (res.alertas["Nombre"] == "XCCY_VS_DROP").any()                            # LATOFF: XCCY 29.7 % vs drop 39.5 %
    ov = pos[pos["Fuente"] == "OVERRIDE"].iloc[0]
    assert ov["PK2"] == "168617-1" and ov["Yield"] == 0.12 and ov["Duration"] == 1.0 and ov["Estado"] == "RESUELTO"
    assert (res.candidatos["Fuente"] == "OVERRIDE").sum() == 1
    xl = pd.ExcelFile(res.excel)
    assert {"conversiones", "curvas_drop"} <= set(xl.sheet_names)
    det = xl.parse("conversiones")
    assert (det["Resultado"] == "OK").sum() >= 12 and det["Tipo"].isin(["BREAKEVEN", "HEDGE"]).all()
