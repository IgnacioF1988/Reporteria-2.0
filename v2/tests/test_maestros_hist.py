import pandas as pd
import pytest

from reporteria import maestros_hist as MH
from reporteria.adaptadores import datamart as DM
from reporteria.lectura import maestros as M


def _bd(*filas):
    cols = ["ID_Instrumento", "SubID_Instrumento", "Name_Instrumento", "ISIN", "Risk_Currency", "Investment_Type_Code", "Issue_Type_Code"]
    df = pd.DataFrame(list(filas), columns=cols)
    return M.tipar_bd_instrumentos(df.assign(**{c: None for c in M.COLS_INSTR if c not in cols}))


A = _bd((1, 39, "BONO A", "CL1", "CLP", 1, 3), (2, 1, "EQ B", "US2", "USD", 2, 0), (3, 38, "DEP C", "", "CLF", 1, 4))


def test_diff_alta_cambio_baja_y_renombre():
    nuevo = _bd((1, 39, "BONO A", "CL1", "CLP", 1, 3), (2, 1, "EQ B", "US2", "USD", 1, 3),          # 2-1: Equity → FI (dos columnas)
                (3, 41, "DEP C", "", "COP", 1, 4), (4, 1, "NUEVO", "", "USD", 3, 0))                # 3-38 → 3-41 (moneda), 4-1 alta
    c = MH.diff_tablas("bd_instrumentos", A, nuevo, carga="20261001_120000")
    assert c["tipo"].value_counts().to_dict() == {"CAMBIO": 2, "ALTA": 2, "BAJA": 1, "RENOMBRE": 1}
    cam = c[c["tipo"].eq("CAMBIO")].set_index("columna")
    assert cam.loc["Investment_Type_Code", ["llave", "valor_anterior", "valor_nuevo"]].tolist() == ["2-1", "2", "1"]
    assert cam.loc["Issue_Type_Code", "valor_nuevo"] == "3"
    ren = c[c["tipo"].eq("RENOMBRE")].iloc[0]
    assert (ren["llave"], ren["valor_nuevo"]) == ("3-38", "3-41")
    # replay devuelve exactamente el nuevo
    rep = MH.tipar("bd_instrumentos", MH.aplicar_cambios("bd_instrumentos", A, c))
    pd.testing.assert_frame_equal(rep.sort_values("PK2").reset_index(drop=True), nuevo.sort_values("PK2").reset_index(drop=True))
    assert MH.diff_tablas("bd_instrumentos", nuevo, nuevo).empty


def test_diff_homol_llave_compuesta_y_ultima_gana():
    h1 = pd.DataFrame({"SourceInvestment": ["TERMOC", "TERMOC", "X"], "ID_Instrumento": [1, 1, 5], "Source": ["GENEVA", "INFOVIEW", "GENEVA"]})
    h2 = pd.DataFrame({"SourceInvestment": ["TERMOC", "TERMOC", "X", "X"], "ID_Instrumento": [1, 7, 5, 6], "Source": ["GENEVA", "INFOVIEW", "GENEVA", "GENEVA"]})
    c = MH.diff_tablas("homol_instrumentos", h1, h2)
    assert c[["llave", "columna", "valor_anterior", "valor_nuevo", "tipo"]].values.tolist() == [["TERMOC|INFOVIEW", "ID_Instrumento", "1", "7", "CAMBIO"],
                                                                                                 ["X|GENEVA", "ID_Instrumento", "5", "6", "CAMBIO"]]
    est = MH.tipar("homol_instrumentos", MH.aplicar_cambios("homol_instrumentos", h1, c))
    assert est.set_index(["SourceInvestment", "Source"])["ID_Instrumento"].to_dict() == {("TERMOC", "GENEVA"): 1, ("TERMOC", "INFOVIEW"): 7, ("X", "GENEVA"): 6}


def test_maestro_asof_retroactivo_por_defecto_declarado_cadena_y_baja():
    c1 = MH.diff_tablas("bd_instrumentos", A, _bd((1, 39, "BONO A", "CL1", "CLP", 1, 3), (2, 1, "EQ B", "US2", "USD", 1, 3), (3, 38, "DEP C", "", "CLF", 1, 4)), "20260815_100000")
    c2 = MH.diff_tablas("bd_instrumentos", MH.aplicar_cambios("bd_instrumentos", A, c1),
                        _bd((1, 39, "BONO A", "CL1", "CLP", 1, 3), (2, 1, "EQ B", "US2", "USD", 6, 0)), "20260915_100000")     # 2-1 → fondo; 3-38 baja
    cambios = pd.concat([c1, c2], ignore_index=True)
    inv = lambda t, k: int(t.set_index("PK2").loc[k, "Investment_Type_Code"])
    # sin declaración: el último valor vale para toda la historia; la BAJA no es retroactiva
    t = MH.maestro_asof("bd_instrumentos", A, cambios, None, "20260731")
    assert inv(t, "2-1") == 6 and "3-38" in set(t["PK2"])
    t = MH.maestro_asof("bd_instrumentos", A, cambios, None, "20260930")
    assert inv(t, "2-1") == 6 and "3-38" not in set(t["PK2"])      # la BAJA (carga 20260915) sí aplica a cierres posteriores
    # conocimiento: al 20260820 solo se sabía el primer cambio
    t = MH.maestro_asof("bd_instrumentos", A, cambios, None, "20260731", conocimiento="20260820")
    assert inv(t, "2-1") == 1 and "3-38" in set(t["PK2"])
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, None, "20260731", conocimiento="20260801"), "2-1") == 2
    # declaraciones: Equity→FI rige desde 20260831 y FI→fondo desde 20260930
    vig = MH.declarar(None, cambios, "bd_instrumentos", "2-1", "Investment_Type_Code", "1", "20260831", declarado_en="20260901_000000")
    vig = MH.declarar(vig, cambios, "bd_instrumentos", "2-1", "Investment_Type_Code", "6", "20260930", declarado_en="20261001_000000")
    assert vig["ID"].tolist() == [1, 2] and vig["valor_anterior"].tolist() == ["2", "1"]
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig, "20260731"), "2-1") == 2       # cadena: antes de ambas
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig, "20260831"), "2-1") == 1
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig, "20260930"), "2-1") == 6
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig, "20260831", conocimiento="20260914"), "2-1") == 1   # la carga del 15 aún no se conocía
    # la declaración 2 no se conocía al 20260920: vale la 1 y el estado conocido entonces (FI)
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig, "20260731", conocimiento="20260920"), "2-1") == 2
    # anular la 2 → al 20260731 sigue 2 (por la 1); al 20260831 pasa a 6 (retro de nuevo)
    vig2 = MH.anular(vig, 2, "20261002_000000")
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig2, "20260831"), "2-1") == 6
    assert inv(MH.maestro_asof("bd_instrumentos", A, cambios, vig2, "20260731"), "2-1") == 2
    with pytest.raises(ValueError, match="anulada"):
        MH.anular(vig2, 2)
    # declarar sin cambio registrado exige valor_anterior
    with pytest.raises(ValueError, match="valor-anterior"):
        MH.declarar(vig, cambios, "bd_instrumentos", "1-39", "Risk_Currency", "USD", "20260831")
    v3 = MH.declarar(vig, cambios, "bd_instrumentos", "1-39", "Risk_Currency", "USD", "20260831", valor_anterior="CLP")
    assert v3.iloc[-1]["valor_anterior"] == "CLP"
    with pytest.raises(ValueError, match="columna"):
        MH.declarar(vig, cambios, "bd_instrumentos", "1-39", "PK2", "x", "20260831", valor_anterior="y")


def test_base_cambios_y_vigencias_en_el_datamart(tmp_path):
    raiz = tmp_path / "dm"
    assert DM.leer_base(raiz) == (None, {})
    DM.escribir_base(raiz, "20260801_090000", {"bd_instrumentos": MH.normalizar_tabla("bd_instrumentos", A),
                                                "homol_instrumentos": MH.normalizar_tabla("homol_instrumentos", None),
                                                "homol_funds": MH.normalizar_tabla("homol_funds", None)})
    with pytest.raises(FileExistsError):
        DM.escribir_base(raiz, "20260801_090000", {})
    bid, tablas = DM.leer_base(raiz)
    assert bid == "20260801_090000" and set(tablas) == {"bd_instrumentos", "homol_instrumentos", "homol_funds"}
    pd.testing.assert_frame_equal(MH.tipar("bd_instrumentos", tablas["bd_instrumentos"]), A)
    c = MH.diff_tablas("bd_instrumentos", A, _bd((1, 39, "BONO A", "CL1", "USD", 1, 3), (2, 1, "EQ B", "US2", "USD", 2, 0), (3, 38, "DEP C", "", "CLF", 1, 4)))
    DM.escribir_cambios(raiz, "20260901_100000", c)
    DM.escribir_cambios(raiz, "20261001_100000", MH.diff_tablas("bd_instrumentos", A, A).iloc[:0])
    todos = DM.leer_cambios(raiz, bid)
    assert todos["carga"].tolist() == ["20260901_100000"] and todos.iloc[0]["valor_nuevo"] == "USD"
    assert DM.leer_cambios(raiz, bid, conocimiento="20260815").empty and DM.cargas_cambios(raiz) == ["20260901_100000", "20261001_100000"]
    assert DM.leer_base(raiz, conocimiento="20260731") == (None, {})
    assert DM.leer_vigencias(raiz).empty
    vig = MH.declarar(None, todos, "bd_instrumentos", "1-39", "Risk_Currency", "USD", "20260831", comentario="cambió la moneda de emisión")
    DM.escribir_vigencias(raiz, vig)
    v2 = DM.leer_vigencias(raiz)
    assert v2.iloc[0][["ID", "llave", "valor", "valor_anterior", "vigente_desde", "comentario"]].tolist() == [1, "1-39", "USD", "CLP", "20260831", "cambió la moneda de emisión"]
    t = MH.maestro_asof("bd_instrumentos", tablas["bd_instrumentos"], todos, v2, "20260731")
    assert t.set_index("PK2").loc["1-39", "Risk_Currency"] == "CLP"


def test_replay_reproduce_el_maestro_mini(fixtures):
    bd = M.leer_bd_instrumentos(fixtures / "BD_INSTRUMENTOS.xlsx")
    base = MH.normalizar_tabla("bd_instrumentos", bd)
    pd.testing.assert_frame_equal(MH.tipar("bd_instrumentos", base), bd.sort_values("PK2").reset_index(drop=True))
    h = M.leer_homol(fixtures / "HOMOL_INSTRUMENTOS.xlsx")
    est = MH.tipar("homol_instrumentos", MH.normalizar_tabla("homol_instrumentos", h))
    assert len(est) == len(h.drop_duplicates(["SourceInvestment", "Source"]))
    assert dict(zip(est["SourceInvestment"], est["ID_Instrumento"])) == dict(zip(h["SourceInvestment"], h["ID_Instrumento"]))   # la última gana


@pytest.mark.slow
def test_replay_reproduce_el_maestro_corporativo(fixtures):
    bd = M.leer_bd_instrumentos(fixtures.parent / "corporativo" / "BD_INSTRUMENTOS.xlsx")
    base = MH.normalizar_tabla("bd_instrumentos", bd)
    pd.testing.assert_frame_equal(MH.tipar("bd_instrumentos", base), bd.sort_values("PK2").reset_index(drop=True))
    assert MH.diff_tablas("bd_instrumentos", base, bd).empty


def test_correr_crea_base_registra_cambios_y_usa_asof(fixtures, tmp_path):
    """e2e mini: 1ª corrida escribe la base; 2ª sin cambios no escribe; BD editado → carga de cambios y maestro as-of con declaración."""
    import shutil
    from reporteria.adaptadores.bbg import FixtureBloomberg
    from reporteria.adaptadores.fx_sql import FixtureFx
    from reporteria.config import Rutas
    from reporteria.pipeline import Opciones, correr
    fx = tmp_path / "fx"
    shutil.copytree(fixtures, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb"))
    shutil.copy(fixtures / "dimensionales.duckdb", fx)
    r = Rutas.para_pruebas("20260731", fx, tmp_path)
    opc = lambda **k: Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fx, "20260731"), **k)
    res = correr(r, opc())
    assert DM.bases(r.datamart) and not DM.cargas_cambios(r.datamart)
    assert res.resumen["maestros"]["base"] == DM.bases(r.datamart)[0] and res.resumen["maestros"]["cambios"] == {}
    pos1 = res.posiciones.set_index("Pos_ID")
    res2 = correr(r, opc())
    assert not DM.cargas_cambios(r.datamart) and res2.resumen["maestros"]["carga"] is None
    # cambio en el BIX: un bono pasa de CLP a USD y un equity a renta fija
    bd = pd.read_excel(fx / "BD_INSTRUMENTOS.xlsx")
    pk = pos1[pos1["Fuente"].eq("JPM")].iloc[0]["PK2"]
    i, sub = (int(x) for x in pk.split("-"))
    m = (bd["ID_Instrumento"] == i) & (bd["SubID_Instrumento"] == sub)
    bd.loc[m, "Risk_Currency"] = "EUR"
    eq = pos1[pos1["Investment_Type_Code"].eq(2)].iloc[0]["PK2"]
    i2, sub2 = (int(x) for x in eq.split("-"))
    bd.loc[(bd["ID_Instrumento"] == i2) & (bd["SubID_Instrumento"] == sub2), "Investment_Type_Code"] = 1
    bd.to_excel(fx / "BD_INSTRUMENTOS.xlsx", index=False)
    res3 = correr(r, opc())
    cargas = DM.cargas_cambios(r.datamart)
    assert len(cargas) == 1 and res3.resumen["maestros"]["carga"] == cargas[0]
    assert res3.resumen["maestros"]["cambios"] == {"bd_instrumentos:CAMBIO": 2}
    assert (res3.alertas["Nombre"] == "MAESTRO_CAMBIOS").any()
    pos3 = res3.posiciones.set_index("Pos_ID")
    assert pos3.loc[pos1.index[pos1["PK2"].eq(pk)][0], "Risk_Currency"] == "EUR"                 # retroactivo por defecto
    # declarado desde 20260831: el cierre 20260731 vuelve a ver CLP; conocimiento anterior a la carga también
    vig = MH.declarar(None, DM.leer_cambios(r.datamart, DM.bases(r.datamart)[0]), "bd_instrumentos", pk, "Risk_Currency", "EUR", "20260831")
    DM.escribir_vigencias(r.datamart, vig)
    res4 = correr(r, opc())
    assert res4.posiciones.set_index("Pos_ID").loc[pos1.index[pos1["PK2"].eq(pk)][0], "Risk_Currency"] != "EUR"
    assert res4.resumen["maestros"]["declaraciones"] == 1 and not DM.cargas_cambios(r.datamart)[1:]      # no registra una carga nueva
    ayer = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%Y%m%d")
    with pytest.raises(ValueError, match="base de maestros"):
        correr(r, opc(conocimiento=ayer))
    # sin BIX a mano: corre desde el datamart
    (fx / "BD_INSTRUMENTOS.xlsx").unlink()
    res5 = correr(r, opc())
    assert len(res5.posiciones) == len(res.posiciones) and res5.resumen["maestros"]["carga"] is None
