import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.fuentes.cajas import candidatos_cajas


def _pos(pk2, fondo=16, trat="CAJA"):
    return dict(Pos_ID=f"{fondo}|{pk2}|Asset", ID_Fund=fondo, PK2=pk2, Tratamiento=trat, Risk_Currency="USD", TotalMVal=100.0)


def _reglas(*filas):
    return pd.DataFrame(list(filas), columns=["ID_Fund", "PK2", "Indice_Referencia", "Spread_Anual", "Dias", "Comentario"])


def test_indice_mas_spread_y_dias(tmp_path):
    pd.DataFrame([("OBFR01 Index", 4.33)], columns=["ticker", "valor"]).to_csv(tmp_path / "bdh_PX_LAST_20260731.csv", index=False)
    bbg = FixtureBloomberg(tmp_path, "20260731")
    pos = pd.DataFrame([_pos("1248-1"), _pos("875-1"), _pos("9-9"), _pos("b-1", trat="CASCADA")])
    reglas = _reglas((16, "1248-1", "OBFR01 Index", -0.02, 1, ""),
                     (None, "875-1", "N.A.", 0.0429, 1, ""),
                     (None, "9-9", "", None, None, ""))
    cand, al = candidatos_cajas(pos, reglas, bbg, "20260731")
    c = cand.set_index("PK2")
    assert abs(c.loc["1248-1", "Yield"] - (0.0433 - 0.02)) < 1e-12 and abs(c.loc["1248-1", "Duration"] - 1 / 365) < 1e-12
    assert abs(c.loc["875-1", "Yield"] - 0.0429) < 1e-12                  # sin índice: el spread es la yield
    assert c.loc["9-9", "Yield"] == 0 and c.loc["9-9", "Duration"] == 0   # fila sin valores → 0 + alerta
    assert "b-1" not in c.index and cand["Valido"].all()
    assert (al["Nombre"] == "CAJA_SIN_VALOR").sum() == 1


def test_sin_regla_y_sin_nivel(tmp_path):
    bbg = FixtureBloomberg(tmp_path, "20260731")                          # caché vacía
    pos = pd.DataFrame([_pos("1248-1"), _pos("zzz-1")])
    cand, al = candidatos_cajas(pos, _reglas((16, "1248-1", "OBFR01 Index", -0.02, 1, "")), bbg, "20260731")
    c = cand.set_index("PK2")
    assert abs(c.loc["1248-1", "Yield"] - (-0.02)) < 1e-12               # sin nivel del índice: solo spread + alerta
    assert c.loc["zzz-1", "Yield"] == 0 and c.loc["zzz-1", "Valido"]
    assert {"CAJA_SIN_REGLA", "INDICE_SIN_NIVEL"} <= set(al["Nombre"])


def test_caja_sin_regla_se_calla_si_otra_fuente_resolvio():
    from reporteria.fuentes.cajas import depurar_sin_regla
    from reporteria import alertas
    al = alertas.juntar(alertas.emitir("CAJA_SIN_REGLA", "ALTA", pd.DataFrame([_pos("dap-39"), _pos("caja-39")])),
                        alertas.emitir("INDICE_SIN_NIVEL", "ALTA", pd.DataFrame([_pos("dap-39")])))
    pos = pd.DataFrame([{**_pos("dap-39"), "Fuente": "RA", "Origen": "NEMO"},          # el DAP lo resolvió RA
                        {**_pos("caja-39"), "Fuente": "CAJA", "Origen": "SIN_REGLA"}])  # la caja sigue a yield 0
    out = depurar_sin_regla(al, pos)
    assert list(out["Nombre"]) == ["CAJA_SIN_REGLA", "INDICE_SIN_NIVEL"] and out["PK2"].iloc[0] == "caja-39"
