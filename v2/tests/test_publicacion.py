"""H10b: readiness y publicación por (fecha, fondo); puntero por fondo; vistas publicadas / ultima_verdad."""
import pandas as pd
import pytest

from reporteria import datamart as DMV
from reporteria import publicacion as PUB
from reporteria.adaptadores import datamart as DM


def _pos(fondos, estado="RESUELTO", yld=0.05):
    filas = []
    for f in fondos:
        for i in range(2):
            filas.append(dict(Pos_ID=f"{f}|{i}-1|Asset", ID_Fund=f, Fondo=f"F{f}", PK2=f"{i}-1", BalanceSheet="Asset", Estado=estado,
                              Yield=yld, Duration=1.0, Fuente="JPM", Conversion="", TotalMVal=100.0))
    return pd.DataFrame(filas)


def _reglas_alertas(filas):
    return pd.DataFrame(filas, columns=["ID", "Nombre", "Severidad", "ID_Fund", "Ambito", "Bloquea_Publicacion"])


def test_evaluar_bloquea_solo_al_fondo_afectado():
    pos = _pos([1, 2, 3])
    pos.loc[pos["ID_Fund"].eq(2) & pos["PK2"].eq("0-1"), "Estado"] = "PENDIENTE_TERMINAL"
    resumen = {"cobertura_mv_activos": {1: 0.99, 2: 0.99, 3: 0.80}}
    alertas = pd.DataFrame([dict(Nombre="AGREGADO_INCONSISTENTE", Severidad="CRITICA", Ambito="FONDO", ID_Fund=1, Pos_ID=""),
                            dict(Nombre="YIELD_ALTA", Severidad="CRITICA", Ambito="POSICION", ID_Fund=1, Pos_ID="1|0-1|Asset"),
                            dict(Nombre="YIELD_ALTA", Severidad="CRITICA", Ambito="POSICION", ID_Fund=2, Pos_ID="2|0-1|Asset"),
                            dict(Nombre="INSUMO_FALTANTE", Severidad="ALTA", Ambito="CORRIDA", ID_Fund=None, Pos_ID="")])
    reglas = _reglas_alertas([("A01", "YIELD_ALTA", "CRITICA", None, "POSICION", False), ("A01", "YIELD_ALTA", "CRITICA", 2, "POSICION", True)])
    insumos = pd.DataFrame({"Insumo": ["CUBO", "JPM"], "Ruta": ["", ""], "Estado": ["OK", "OPCIONAL_AUSENTE"]})
    r = PUB.evaluar(pos, alertas, resumen, insumos, reglas, {"cobertura_min_mv": 0.95}, esperados_=[1, 2, 3, 4]).set_index("ID_Fund")
    assert r.loc[1, "Bloqueos"] == "AGREGADO_INCONSISTENTE" and not r.loc[1, "Listo"]           # YIELD_ALTA no bloquea al fondo 1 (regla global NO)
    assert r.loc[2, "Bloqueos"] == "PENDIENTE_TERMINAL:1;ALERTA:YIELD_ALTA" and r.loc[2, "N_Pendientes"] == 1
    assert r.loc[3, "Bloqueos"] == "COBERTURA:0.8000<0.95" and r.loc[3, "Cobertura"] == 0.80
    assert r.loc[4, "Bloqueos"] == "FONDO_SIN_POSICIONES" and r.loc[4, "Fondo"] == ""
    assert list(r.index) == [1, 2, 3, 4] and r["Listo"].sum() == 0
    # sin bloqueos: todos listos; insumo obligatorio ausente: nadie
    todo = PUB.evaluar(_pos([1, 2]), None, {"cobertura_mv_activos": {1: 1.0, 2: 1.0}}, insumos, reglas, {})
    assert todo["Listo"].all() and (todo["Bloqueos"] == "").all()
    nadie = PUB.evaluar(_pos([1, 2]), None, {}, insumos, reglas, {"insumos_obligatorios": "CUBO;JPM"})
    assert (nadie["Bloqueos"] == "INSUMO_FALTANTE:JPM").all()


def test_esperados_desde_dim_fondos():
    f = pd.DataFrame({"ID_Fund": [1, 2, 3], "Activo_MantenedorFondos": [1, 0, 1]})
    assert PUB.esperados(f) == [1, 3] and PUB.esperados(f.drop(columns="Activo_MantenedorFondos")) == [1, 2, 3] and PUB.esperados(None) == []


def test_fondos_cambiados_compara_por_fondo():
    a, b = _pos([1, 2, 3]), _pos([1, 2, 4])
    b.loc[b["ID_Fund"].eq(2) & b["PK2"].eq("1-1"), "Yield"] = 0.07
    assert PUB.fondos_cambiados(b, a) == {2, 3, 4} and PUB.fondos_cambiados(a, a) == set() and PUB.fondos_cambiados(a, None) == {1, 2, 3}


def _corrida(tmp_path, nombre, pos, readiness, fecha="20260731"):
    return DM.escribir_version(tmp_path / "b" / nombre, {"cartera_final": pos, "publicacion": readiness},
                               {"fecha": fecha, "ts": "2026-08-01 10:00:00", "estado": "BORRADOR", "completitud": "COMPLETA"}, pos)


def test_publicar_diario_apunta_por_fondo_y_solo_mueve_los_cambiados(tmp_path):
    raiz = tmp_path / "dm"
    pos1 = _pos([1, 2, 3])
    rd1 = PUB.evaluar(pos1, None, {"cobertura_mv_activos": {1: 1.0, 2: 0.5, 3: 1.0}}, None, None, {}, [1, 2, 3, 4])
    v1 = DMV.publicar(raiz, "20260731", _corrida(tmp_path, "c1", pos1, rd1), modo="diario")
    est = DM.leer_estado(raiz, "20260731")
    assert est["fondos"]["1"] | {} == est["fondos"]["1"] and est["fondos"]["1"]["estado"] == "PUBLICADA" and est["fondos"]["1"]["publicada"] == 1
    assert est["fondos"]["2"]["estado"] == "PROVISORIO" and est["fondos"]["2"]["bloqueos"] == ["COBERTURA:0.5000<0.95"] and "publicada" not in est["fondos"]["2"]
    assert est["fondos"]["4"]["estado"] == "SIN_CORRIDA" and est["corridas"]["001"]["fondos"] == {"PUBLICADA": 2, "REEXPRESADA": 0, "PROVISORIO": 1, "SIN_CORRIDA": 1, "SIN_CAMBIO": 0}
    assert v1.estado == "PUBLICADA"

    # segunda corrida: fondo 2 ya tiene cobertura (primera publicación), fondo 3 cambió (re-expresión), fondo 1 idéntico (se queda en la 1)
    pos2 = _pos([1, 2, 3])
    pos2.loc[pos2["ID_Fund"].eq(3) & pos2["PK2"].eq("0-1"), "Yield"] = 0.09
    rd2 = PUB.evaluar(pos2, None, {"cobertura_mv_activos": {1: 1.0, 2: 1.0, 3: 1.0}}, None, None, {}, [1, 2, 3, 4])
    v2 = DMV.publicar(raiz, "20260731", _corrida(tmp_path, "c2", pos2, rd2), motivo="llegó JPM", reexpresar=True, modo="diario")
    est = DM.leer_estado(raiz, "20260731")
    f = est["fondos"]
    assert (f["1"]["corrida"], f["1"]["estado"], f["1"]["publicada"]) == (1, "PUBLICADA", 1) and len(f["1"]["historial"]) == 1
    assert (f["2"]["corrida"], f["2"]["estado"], f["2"]["publicada"]) == (2, "PUBLICADA", 2) and f["2"]["bloqueos"] == []
    assert (f["3"]["corrida"], f["3"]["estado"], f["3"]["publicada"]) == (2, "REEXPRESADA", 2) and f["3"]["historial"][-1]["motivo"] == "llegó JPM"
    assert est["corridas"]["002"]["fondos"]["SIN_CAMBIO"] == 1 and v2.numero == 2

    # tercera: fondo 1 queda PROVISORIO (pendiente) → apunta a la 3 como última verdad, pero su publicada sigue siendo la 1
    pos3 = _pos([1, 2, 3])
    pos3.loc[pos3["ID_Fund"].eq(3) & pos3["PK2"].eq("0-1"), "Yield"] = 0.09
    pos3.loc[pos3["ID_Fund"].eq(1) & pos3["PK2"].eq("0-1"), ["Estado", "Yield"]] = ["PENDIENTE_TERMINAL", float("nan")]
    rd3 = PUB.evaluar(pos3, None, {"cobertura_mv_activos": {1: 1.0, 2: 1.0, 3: 1.0}}, None, None, {}, [1, 2, 3, 4])
    DMV.publicar(raiz, "20260731", _corrida(tmp_path, "c3", pos3, rd3), motivo="re", reexpresar=True, modo="diario")
    f = DM.leer_estado(raiz, "20260731")["fondos"]
    assert (f["1"]["corrida"], f["1"]["estado"], f["1"]["publicada"]) == (3, "PROVISORIO", 1) and f["1"]["bloqueos"] == ["PENDIENTE_TERMINAL:1"]
    assert (f["2"]["corrida"], f["3"]["corrida"]) == (2, 2)

    # estado por fondo y vistas
    t = PUB.estado_fondos(raiz, "20260731").set_index("ID_Fund")
    assert t.loc[1, "estado"] == "PROVISORIO" and t.loc[1, "publicada"] == 1 and t.loc[4, "estado"] == "SIN_CORRIDA" and pd.isna(t.loc[4, "corrida"])
    con = DM.vistas(raiz)
    assert con.execute("SELECT count(*) FROM corridas").fetchone()[0] == 3
    pub = con.execute("SELECT ID_Fund, corrida, count(*) AS n FROM publicadas GROUP BY 1, 2 ORDER BY 1").df()
    assert pub[["ID_Fund", "corrida"]].values.tolist() == [[1, 1], [2, 2], [3, 2]] and (pub["n"] == 2).all()
    uv = con.execute("SELECT ID_Fund, corrida, estado_fondo FROM ultima_verdad GROUP BY ALL ORDER BY 1").df()
    assert uv.values.tolist() == [[1, 3, "PROVISORIO"], [2, 2, "PUBLICADA"], [3, 2, "REEXPRESADA"]]
    assert con.execute("SELECT count(*) FROM ultima_verdad WHERE Estado = 'PENDIENTE_TERMINAL'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM publicadas WHERE Estado = 'PENDIENTE_TERMINAL'").fetchone()[0] == 0


def test_estado_fondos_del_layout_mensual(tmp_path):
    raiz = tmp_path / "dm"
    pos = _pos([1, 2])
    DMV.publicar(raiz, "20260630", _corrida(tmp_path, "m1", pos, PUB.evaluar(pos, None, {}, None, None, {}), fecha="20260630"))
    t = PUB.estado_fondos(raiz, "20260630")
    assert t["estado"].tolist() == ["PUBLICADA", "PUBLICADA"] and t["corrida"].tolist() == [1, 1] and t["publicada"].tolist() == [1, 1]
    con = DM.vistas(raiz)
    assert con.execute("SELECT count(*) FROM publicadas").fetchone()[0] == 4 and con.execute("SELECT layout FROM corridas").fetchone()[0] == "cierres"


def test_mini_un_isin_sin_cache_deja_un_fondo_provisorio(fixtures, tmp_path):
    """e2e mini: una posición que pasa a CASCADA sin YAS en caché → PENDIENTE_TERMINAL solo en su fondo → ese fondo PROVISORIO,
    el resto PUBLICADA (si su cobertura lo permite); `estado` lo muestra."""
    import dataclasses
    import shutil
    from typer.testing import CliRunner
    from reporteria.adaptadores.bbg import FixtureBloomberg
    from reporteria.adaptadores.fx_sql import FixtureFx
    from reporteria.cli import app
    from reporteria.config import Rutas
    from reporteria.pipeline import Opciones, correr

    fx = tmp_path / "fx"
    shutil.copytree(fixtures, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb"))
    shutil.copy(fixtures / "dimensionales.duckdb", fx)
    r = dataclasses.replace(Rutas.para_pruebas("20260731", fx, tmp_path), modo="diario", cache=fixtures / "bbg_cache")
    opc = Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fx, "20260731"), excel=False)
    res = correr(r, opc)
    rd = res.hojas["publicacion"].set_index("ID_Fund")
    assert rd["N_Pendientes"].sum() == 0 and set(res.posiciones["ID_Fund"]) < set(rd.index)        # esperados sin posiciones también aparecen
    assert (rd.loc[~rd.index.isin(res.posiciones["ID_Fund"]), "Bloqueos"] == "FONDO_SIN_POSICIONES").all()
    sin_bloqueo_cobertura = set(rd.index[rd["Listo"]])
    assert len(sin_bloqueo_cobertura) >= 5
    # un equity con ISIN de un solo fondo (de los publicables) pasa a renta fija: va a CASCADA y no tiene YAS en caché
    pos = res.posiciones
    cand = pos[pos["Investment_Type_Code"].eq(2) & pos["ISIN"].astype(str).str.len().gt(5) & pos["BalanceSheet"].eq("Asset")
               & pos["ID_Fund"].isin(sin_bloqueo_cobertura)]
    pk2 = next(p for p in cand["PK2"] if pos["PK2"].eq(p).sum() == 1)
    eq = cand[cand["PK2"].eq(pk2)].iloc[0]
    bd = pd.read_excel(fx / "BD_INSTRUMENTOS.xlsx")
    i, sub = (int(x) for x in pk2.split("-"))
    m = (bd["ID_Instrumento"] == i) & (bd["SubID_Instrumento"] == sub)
    bd.loc[m, ["Investment_Type_Code", "Issue_Type_Code", "Issuer_Type_Code", "Coupon_Type_Code", "Rank_Code"]] = [1, 3, 1, 1, 2]
    bd.to_excel(fx / "BD_INSTRUMENTOS.xlsx", index=False)
    res2 = correr(r, opc)
    rd2 = res2.hojas["publicacion"].set_index("ID_Fund")
    fid = int(eq["ID_Fund"])
    assert rd2.loc[fid, "N_Pendientes"] == 1 and rd2.loc[fid, "Bloqueos"].startswith("PENDIENTE_TERMINAL:1") and not rd2.loc[fid, "Listo"]
    assert (rd2.drop(index=fid)["N_Pendientes"] == 0).all()
    assert res2.resumen["publicacion"]["bloqueados"][fid].startswith("PENDIENTE_TERMINAL")

    v = DMV.publicar(r.datamart, "20260731", res2.borrador, modo="diario")
    est = DM.leer_estado(r.datamart, "20260731")
    assert est["fondos"][str(fid)]["estado"] == "PROVISORIO" and est["fondos"][str(fid)]["corrida"] == 1
    publicados = [f for f, e in est["fondos"].items() if e["estado"] == "PUBLICADA"]
    assert set(int(f) for f in publicados) == sin_bloqueo_cobertura - {fid} and v.estado == "PUBLICADA"
    runner = CliRunner()
    out = runner.invoke(app, ["estado", "--fecha", "20260731", "--raiz", str(tmp_path)], env={"REPORTERIA_DATAMART": str(r.datamart), "REPORTERIA_MODO": "diario",
                                                                                           "REPORTERIA_DIM": str(r.dim)})
    assert out.exit_code == 1 and f"{fid:4}  PROVISORIO" in out.output and "PENDIENTE_TERMINAL:1" in out.output, out.output
    out1 = runner.invoke(app, ["estado", "--fecha", "20260731", "--fondo", str(sorted(publicados)[0]), "--raiz", str(tmp_path)],
                         env={"REPORTERIA_DATAMART": str(r.datamart), "REPORTERIA_MODO": "diario", "REPORTERIA_DIM": str(r.dim)})
    assert out1.exit_code == 0 and "PUBLICADA" in out1.output
