import pandas as pd

from reporteria.escala import EscalaCfg
from reporteria.fuentes.excepciones import candidatos_excepciones
from reporteria.lectura.manuales import leer_excepciones

S = pd.Timestamp("2026-07-31")


def _libro(path, hojas):
    with pd.ExcelWriter(path) as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)
    return path


def test_leer_excepciones_tolera_header_raro_liability_y_duplicados(tmp_path):
    flujo = pd.DataFrame({"PK2": ["1-1"] * 3, "BalanceSheet": ["Asset", "Asset", "Liability"],
                          "Fecha": [S - pd.Timedelta(days=30), S + pd.Timedelta(days=180), S + pd.Timedelta(days=180)],
                          "Flujo": [50_000.0, 1_050_000.0, -999.0]})
    raro = flujo.rename(columns={"PK2": "hor"})
    a = _libro(tmp_path / "EXCEPCIONES_A.xlsx", {"1-1": flujo, "2-1": raro.assign(hor="2-1")})
    b = _libro(tmp_path / "EXCEPCIONES_B.xlsx", {"1-1": flujo, "3-1": pd.DataFrame({"x": [1]})})
    flujos, avisos = leer_excepciones([a, b], S)
    assert set(flujos) == {"1-1", "2-1"}
    assert len(flujos["1-1"]) == 1 and flujos["1-1"]["Flujo"].iloc[0] == 1_050_000.0       # futuro, sin Liability
    assert {"PK2_DUPLICADO", "SIN_COLUMNAS"} <= set(avisos["Nombre"])


def test_yield_por_posicion_con_escala_y_fx():
    """Bono USD a la par en fondo USD: 1 flujo a 1 año de 1.080.000 por 1.000.000 nominal → yield ≈ 8 % efectiva."""
    pos = pd.DataFrame([dict(Pos_ID="16|1-1|Asset", ID_Fund=16, PK2="1-1", Tratamiento="CASCADA", Risk_Currency="USD",
                             FundBaseCurrency="USD", LocalPrice=100.0, Qty=2_000_000.0, OriginalFace=2_000_000.0, Factor=1.0,
                             AI=0.0, MVBook=2_000_000.0, TotalMVal=2_000_000.0),
                        dict(Pos_ID="16|9-1|Asset", ID_Fund=16, PK2="9-1", Tratamiento="CASCADA", Risk_Currency="USD",
                             FundBaseCurrency="USD", LocalPrice=100.0, Qty=1.0, OriginalFace=1.0, Factor=1.0, AI=0.0,
                             MVBook=1.0, TotalMVal=1.0)])
    fechas = [S + pd.Timedelta(days=365)]
    flujos = {"1-1": pd.DataFrame({"Fecha": fechas, "Flujo": [1_080_000.0]}),
              "9-1": pd.DataFrame({"Fecha": fechas, "Flujo": [0.0]})}
    cand, tds, al = candidatos_excepciones(pos, flujos, {"USDUSD": 1.0}, {"USDUSD": 1.0}, S, EscalaCfg())
    c = cand.set_index("PK2")
    assert c.loc["1-1", "Valido"] and abs(c.loc["1-1", "Yield"] - 0.08) < 5e-4 and abs(c.loc["1-1", "Duration"] - 1 / 1.08) < 5e-3
    assert not c.loc["9-1", "Valido"] and c.loc["9-1", "Motivo_Descarte"] == "TD_SIN_FLUJOS"
    assert len(tds) == 1 and abs(tds.iloc[0]["Flujo"] - 2_160_000.0) < 1e-6              # escalado por Q_real/1.000.000


def test_golden_excepciones_vs_legacy(fixtures, rutas, bbg, fx):
    """Las excepciones de julio deben reproducir el legacy (±1 bp en yield, ±0.001 en duration) donde su escala calzó."""
    from reporteria.pipeline import Opciones, correr
    res = correr(rutas, Opciones(sin_bbg=True, sin_sql=True, bbg=bbg, fx=fx))
    gold = pd.read_csv(fixtures / "golden_excepciones_20260731.csv")
    gold = gold[gold["Escalar_flag"] == "OK"]
    exc = res.candidatos[(res.candidatos["Fuente"] == "EXCEPCIONES") & res.candidatos["Valido"]]
    pos = res.posiciones.set_index("Pos_ID")
    exc = exc.assign(ID_Fund=exc["Pos_ID"].map(pos["ID_Fund"]))
    m = gold.merge(exc, on=["ID_Fund", "PK2"], suffixes=("_leg", ""))
    assert len(m) >= 30, len(m)
    dif_y = (m["Yield"] - m["Yield_efectiva"]).abs()
    dif_d = (m["Duration"] - m["ModDur"]).abs()
    malos = m[(dif_y > 1e-4) | (dif_d > 1e-3)][["ID_Fund", "PK2", "Yield_efectiva", "Yield", "ModDur", "Duration"]]
    assert malos.empty, malos.to_string()
