"""H9c: impacto de la verdad actual sobre lo publicado y re-expresión (mini, sin terminal)."""
import shutil

import pandas as pd
import pytest

from reporteria import datamart as DMV
from reporteria import impacto as IMP
from reporteria.adaptadores import datamart as DM
from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import Rutas
from reporteria.pipeline import Opciones, correr


@pytest.fixture
def entorno(fixtures, tmp_path):
    fx = tmp_path / "fx"
    shutil.copytree(fixtures, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb"))
    shutil.copy(fixtures / "dimensionales.duckdb", fx)
    shutil.copytree(fixtures / "bbg_cache", tmp_path / "cache" / "20260731")

    def rutas(fecha="20260731"):
        r = Rutas.para_pruebas(fecha, fx, tmp_path)
        import dataclasses
        return dataclasses.replace(r, cache=tmp_path / "cache" / fecha)

    def opc(r, **k):
        return Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, r.fecha), fx=FixtureFx(fx, "20260731"), **k)
    return fx, rutas, opc


def test_impacto_vacio_tras_publicar_y_detecta_maestro_dim_reglas_codigo_cadena(entorno, monkeypatch):
    fx, rutas, opc = entorno
    r = rutas()
    res = correr(r, opc(r))
    v1 = DMV.publicar(r.datamart, "20260731", res.borrador)
    assert IMP.impacto(r).empty

    # maestro: un bono cambia de moneda y un equity pasa a renta fija
    pos = res.posiciones.set_index("Pos_ID")
    bono = pos[pos["Fuente"].eq("JPM")].iloc[0]
    eq = pos[pos["Investment_Type_Code"].eq(2) & pos["ISIN"].astype(str).str.len().gt(5)].iloc[0]
    bd = pd.read_excel(fx / "BD_INSTRUMENTOS.xlsx")
    for pk, col, val in ((bono["PK2"], "Risk_Currency", "EUR"), (eq["PK2"], "Investment_Type_Code", 1), (eq["PK2"], "Issue_Type_Code", 3)):
        i, sub = (int(x) for x in pk.split("-"))
        bd.loc[(bd["ID_Instrumento"] == i) & (bd["SubID_Instrumento"] == sub), col] = val
    bd.to_excel(fx / "BD_INSTRUMENTOS.xlsx", index=False)
    correr(r, opc(r))                                               # registra la carga de cambios
    imp = IMP.impacto(r)
    por = imp.set_index(["Pos_ID", "atributo"])
    assert por.loc[(bono.name, "Risk_Currency"), "consecuencia"] == "HEDGE" and por.loc[(bono.name, "Risk_Currency"), "despues"] == "EUR"
    assert por.loc[(eq.name, "Investment_Type_Code"), "consecuencia"] == "CLASIFICACION"
    assert set(imp["consecuencia"]) == {"HEDGE", "CLASIFICACION"}
    assert imp["fondo"].notna().all() and (imp["fondo"] == imp["Pos_ID"].str.split("|").str[0].astype(int)).all()      # H10b: impacto por fondo
    res_imp = IMP.resumen_impacto(imp)
    assert res_imp["N"].sum() == len(imp) and (res_imp["version"] == 1).all()

    # dim: pactos a Repo (no hay pactos en el mini → sin impacto) y un cambio que sí toca: Equity → Ficha distinta
    from reporteria.adaptadores import dim as DIMA
    d = DIMA.leer(r.dim)
    c = d.clasificacion
    m = c["Bucket"].eq("Equity") & c["BalanceSheet"].eq("Asset")
    c.loc[m, "Ficha_FI"] = "Acciones"
    DIMA.escribir(r.dim, d, csv_dir=fx / "csv")
    imp = IMP.impacto(r)
    assert (imp["consecuencia"] == "DIM").any() and set(imp.loc[imp["consecuencia"].eq("DIM"), "atributo"]) == {"Ficha_FI"}

    # REGLAS: una fila más en overrides_valor
    hojas = pd.read_excel(r.reglas, sheet_name=None)
    hojas["parametros"].loc[len(hojas["parametros"])] = ["prueba_h9c", 1, "x"]
    with pd.ExcelWriter(r.reglas) as w:
        for h, df in hojas.items():
            df.to_excel(w, sheet_name=h, index=False)
    imp = IMP.impacto(r)
    assert set(imp.loc[imp["consecuencia"].eq("REGLAS"), "atributo"]) == {"REGLAS/parametros"}

    # código: hash distinto
    monkeypatch.setattr(DM, "hash_codigo", lambda *a, **k: "0000000000000000")
    imp = IMP.impacto(r)
    assert (imp["consecuencia"] == "CODIGO").sum() == 1
    monkeypatch.undo()

    # cadena: aparece un cierre anterior publicado después
    hojas_v, pos_v, c_v = DM.leer_version(v1.ruta)
    DM.escribir_version(fx.parent / "ant", hojas_v, {**c_v, "fecha": "20260630"}, pos_v)
    DMV.publicar(r.datamart, "20260630", fx.parent / "ant")
    imp = IMP.impacto(r)
    cad = imp[imp["consecuencia"].eq("CADENA")]
    assert len(cad) == 1 and cad.iloc[0]["cierre"] == "20260731" and cad.iloc[0]["despues"] == "20260630/v001"


def test_recalcular_reexpresa_con_pendientes_y_cadena(entorno):
    fx, rutas, opc = entorno
    r = rutas()
    res = correr(r, opc(r))
    DMV.publicar(r.datamart, "20260731", res.borrador)
    pos = res.posiciones.set_index("Pos_ID")
    eq = pos[pos["Investment_Type_Code"].eq(2) & pos["ISIN"].astype(str).str.len().gt(5) & pos["BalanceSheet"].eq("Asset")].iloc[0]
    bd = pd.read_excel(fx / "BD_INSTRUMENTOS.xlsx")
    i, sub = (int(x) for x in eq["PK2"].split("-"))
    m = (bd["ID_Instrumento"] == i) & (bd["SubID_Instrumento"] == sub)
    bd.loc[m, ["Investment_Type_Code", "Issue_Type_Code", "Issuer_Type_Code", "Coupon_Type_Code", "Rank_Code"]] = [1, 3, 1, 1, 2]
    bd.to_excel(fx / "BD_INSTRUMENTOS.xlsx", index=False)
    correr(r, opc(r))                                               # carga el cambio al datamart
    imp = IMP.impacto(r)
    assert (imp["Pos_ID"].eq(eq.name) & imp["consecuencia"].eq("CLASIFICACION")).any()

    v2 = IMP.recalcular(r, "equity → renta fija", con_terminal=False, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fx, "20260731"))
    assert (v2.numero, v2.estado, v2.completitud) == (2, "REEXPRESADA", "PARCIAL") and v2.motivo == "equity → renta fija"
    p2 = v2.posiciones().set_index("Pos_ID")
    assert p2.loc[eq.name, "Estado"] == "PENDIENTE_TERMINAL" and p2.loc[eq.name, "Bucket"] == "Fixed Income"
    assert "YAS_BOND_YLD settle_dt=20260731" in p2.loc[eq.name, "Pedido_BBG"] and pd.isna(p2.loc[eq.name, "Yield"])
    assert p2.loc[eq.name, "Motivo"].startswith("SIN_CACHE_BBG")
    hojas = v2.hojas()
    assert len(hojas["pendientes"]) == 1 and hojas["pendientes"].iloc[0]["Pos_ID"] == eq.name
    assert v2.corrida["n_pendientes"] == 1 and v2.corrida["opciones"]["sin_bbg"] is True
    assert IMP.impacto(r).empty                                     # ya no hay impacto abierto
    # con el dato en caché → re-expresión completa
    import csv
    cache = r.cache / "bdp_YAS_BOND_YLD_20260731_settle_dt-20260731.csv"
    rows = list(csv.reader(cache.open())) if cache.exists() else [["ticker", "valor"]]
    rows.append([f"{eq['ISIN']} Corp", "6.5"])
    csv.writer(cache.open("w", newline="")).writerows(rows)
    cache_d = r.cache / "bdp_YAS_MOD_DUR_20260731_settle_dt-20260731.csv"
    rows = list(csv.reader(cache_d.open())) if cache_d.exists() else [["ticker", "valor"]]
    rows.append([f"{eq['ISIN']} Corp", "3.2"])
    csv.writer(cache_d.open("w", newline="")).writerows(rows)
    v3 = IMP.recalcular(r, "terminal", con_terminal=False, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fx, "20260731"))
    p3 = v3.posiciones().set_index("Pos_ID")
    assert v3.numero == 3 and v3.completitud == "COMPLETA" and p3.loc[eq.name, "Estado"] == "RESUELTO" and abs(p3.loc[eq.name, "Yield"] - 0.065) < 1e-9
    # cadena: publicar un cierre anterior nuevo deja CADENA en 20260731 y reexpresar_impactados lo cierra
    hojas_v, pos_v, c_v = DM.leer_version(v3.ruta)
    DM.escribir_version(fx.parent / "ant", hojas_v, {**c_v, "fecha": "20260630"}, pos_v)
    DMV.publicar(r.datamart, "20260630", fx.parent / "ant")
    imp = IMP.impacto(r)
    assert set(imp["consecuencia"]) == {"CADENA"}
    hechos = IMP.reexpresar_impactados(r, imp, bbg_factory=lambda rf: FixtureBloomberg(rf.cache, rf.fecha), fx=FixtureFx(fx, "20260731"))
    assert [h.cierre for h in hechos] == ["20260731"] and hechos[0].numero == 4 and hechos[0].corrida["anterior_version"]["cierre"] == "20260630"
    assert IMP.impacto(r).empty
    assert (hechos[0].posiciones()["Hedge_Origen"] == "MES_ANTERIOR").sum() >= 40
