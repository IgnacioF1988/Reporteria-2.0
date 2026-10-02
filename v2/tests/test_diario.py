"""H10c: tres días consecutivos derivados del mini en modo diario: cierre anterior por fondo (aunque un fondo falte un día),
parámetros `_diario`, RA fechado, SIN_HISTORIA, CADENA por contenido con re-apunte solo del fondo afectado, política
`reexpresar_por` (código y REGLAS globales solo marcan; REGLAS por ID_Fund re-expresa ese fondo) y ventana de impacto."""
import dataclasses
import shutil

import pandas as pd
import pytest

from reporteria import calendario as C
from reporteria import datamart as DMV
from reporteria import impacto as IMP
from reporteria.adaptadores import datamart as DM
from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import Rutas
from reporteria.pipeline import Opciones, correr

D1, D2, D3 = "20260731", "20260801", "20260802"


def _clonar_cache(origen, destino, fecha):
    """Copia la caché del 31-07 renombrando fecha en archivos y carpetas (settle_dt, CURVE_DATE, facts_*)."""
    for p in origen.rglob("*"):
        rel = str(p.relative_to(origen)).replace(D1, fecha)
        q = destino / rel
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, q)


@pytest.fixture
def entorno(fixtures, tmp_path):
    fx = tmp_path / "fx"
    shutil.copytree(fixtures, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb", "bbg_cache"))
    shutil.copy(fixtures / "dimensionales.duckdb", fx)
    for f in (D1, D2, D3):
        _clonar_cache(fixtures / "bbg_cache", tmp_path / "cache" / f, f)
    cubo = pd.read_excel(fx / f"CUBO_{D1}.xlsx")
    from reporteria.adaptadores import dim as DIMA
    activos = set(DIMA.leer(fx / "dimensionales.duckdb").fondos.query("Activo_MantenedorFondos == 1")["ID_Fund"].astype(int))
    conteo = cubo[cubo["ID_Fund"].isin(activos)]["ID_Fund"].value_counts()
    w, x, z = (int(f) for f in conteo.index[:3])                      # W: el de más posiciones (cadena); X falta el día 2; Z falta el día 1
    cubo[cubo["ID_Fund"].ne(z)].to_excel(fx / f"CUBO_{D1}.xlsx", index=False)
    cubo[cubo["ID_Fund"].ne(x)].to_excel(fx / f"CUBO_{D2}.xlsx", index=False)
    cubo.to_excel(fx / f"CUBO_{D3}.xlsx", index=False)
    shutil.copy(fx / "RA_TIR.xlsx", fx / f"RA_TIR_{D2}.xlsx")          # RA fechado solo el día 2
    # parámetros diarios: cobertura mínima 0,5 en modo diario; ventana 30 días
    with pd.ExcelWriter(fx / "REGLAS.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w_:
        par = pd.read_excel(fx / "REGLAS.xlsx", sheet_name="parametros")
        par = pd.concat([par, pd.DataFrame([{"Clave": "cobertura_min_mv_diario", "Valor": 0.5, "Descripcion": "test"},
                                            {"Clave": "ventana_reexpresion_dias", "Valor": 30, "Descripcion": "test"}])], ignore_index=True)
        par.to_excel(w_, sheet_name="parametros", index=False)

    def rutas(fecha):
        r = Rutas.para_pruebas(fecha, fx, tmp_path)
        return dataclasses.replace(r, modo="diario", cache=tmp_path / "cache" / fecha)

    def opc(r):
        return Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, r.fecha), fx=FixtureFx(fx, D1), excel=False)
    return fx, rutas, opc, (w, x, z)


def test_tres_dias_anterior_por_fondo_cadena_y_politica(entorno, monkeypatch):
    fx, rutas, opc, (w, x, z) = entorno
    # ── día 1 (sin Z): sin cartera previa
    r1 = rutas(D1)
    res1 = correr(r1, opc(r1))
    assert res1.resumen["anterior_version"] is None and z not in set(res1.posiciones["ID_Fund"])
    DMV.publicar(r1.datamart, D1, res1.borrador, modo="diario")
    assert DM.leer_estado(r1.datamart, D1)["fondos"][str(z)]["estado"] == "SIN_CORRIDA"

    # ── día 2 (sin X): anterior por fondo desde el día 1; Z sin historia; RA fechado; cobertura mínima diaria 0,5
    r2 = rutas(D2)
    assert r2.ra.name == f"RA_TIR_{D2}.xlsx"
    res2 = correr(r2, opc(r2))
    av = res2.resumen["anterior_version"]
    assert av["origen"] == "DATAMART_POR_FONDO" and all(v["cierre"] == D1 for v in av["fondos"].values()) and str(z) not in av["fondos"] and z not in av["fondos"]
    assert (res2.posiciones["Hedge_Origen"] == "ANTERIOR").sum() >= 40
    sh = res2.alertas[res2.alertas["Nombre"].eq("SIN_HISTORIA")]
    assert len(sh) == 1 and int(sh.iloc[0]["ID_Fund"]) == z
    assert res2.hojas["insumos"].set_index("Insumo").loc["RA_TIR", "Ruta"].endswith(f"RA_TIR_{D2}.xlsx")
    rd2 = res2.hojas["publicacion"].set_index("ID_Fund")
    assert all(float(b.split(":")[1].split("<")[0]) < 0.5 for b in rd2["Bloqueos"] if b.startswith("COBERTURA"))       # umbral diario
    assert any("<0.5" in b for b in rd2["Bloqueos"]) or (rd2.loc[rd2.index.isin(res2.posiciones["ID_Fund"]), "Listo"]).all()
    DMV.publicar(r2.datamart, D2, res2.borrador, motivo="día 2", reexpresar=True, modo="diario")
    assert DM.leer_estado(r2.datamart, D2)["fondos"][str(x)]["estado"] == "SIN_CORRIDA"

    # calendario: el lunes 3 de agosto, con corridas del 31 y del 1, queda listo el 2
    pend = C.pendientes(DM.fechas(r2.datamart), "20260803", C.existe_cubo_en(fx))
    assert pend["fecha"].tolist() == [D3] and pend["estado"].tolist() == ["LISTA"]

    # ── día 3 (todos): X hereda del día 1, el resto del día 2; nadie sin historia
    r3 = rutas(D3)
    res3 = correr(r3, opc(r3))
    av = res3.resumen["anterior_version"]["fondos"]
    assert av[x]["cierre"] == D1 and av[w]["cierre"] == D2 and av[z]["cierre"] == D2
    assert res3.alertas[res3.alertas["Nombre"].eq("SIN_HISTORIA")].empty
    px = res3.posiciones[res3.posiciones["ID_Fund"].eq(x)]
    assert px["Yield_ant"].notna().sum() > 0 and px["_en_anterior"].mean() > 0.8        # X heredó del día 1 (saltando el día 2)
    DMV.publicar(r3.datamart, D3, res3.borrador, motivo="día 3", reexpresar=True, modo="diario")
    assert IMP.impacto(r3, hoy="20260803").empty

    # ── CADENA por contenido: el día 2 se re-expresa con otro hedge en 3 posiciones de W → impacto solo en W del día 3
    v2 = DMV.ultima_verdad(r2.datamart, D2)
    hojas_v, pos_v, c_v = DM.leer_version(v2.ruta)
    idx = pos_v.index[pos_v["ID_Fund"].eq(w) & pos_v["Hedge_Origen"].isin(["REGLA", "ANTERIOR"])][:3]
    assert len(idx) == 3
    pos_v.loc[idx, ["Hedge_Currency", "Hedge_Origen"]] = ["EUR", "OVERRIDE"]        # hedge forzado en el día 2: el día 3 debería heredarlo
    hojas_v["cartera_final"] = pos_v
    DM.escribir_version(fx.parent / "b_hedge", hojas_v, {**c_v, "fecha": D2}, pos_v)
    DMV.publicar(r2.datamart, D2, fx.parent / "b_hedge", motivo="hedge W", reexpresar=True, modo="diario")
    est2 = DM.leer_estado(r2.datamart, D2)
    assert est2["fondos"][str(w)]["corrida"] == 2 and est2["corridas"]["002"]["fondos"]["SIN_CAMBIO"] >= 5       # solo W se re-apuntó
    imp = IMP.impacto(r3, hoy="20260803")
    assert set(imp["consecuencia"]) == {"CADENA"} and set(imp["cierre"]) == {D3} and set(imp["fondo"]) == {w} and len(imp) == 3
    assert (imp["accion"] == "REEXPRESAR").all()
    hechos = IMP.reexpresar_impactados(r3, imp, bbg_factory=lambda rf: FixtureBloomberg(rf.cache, rf.fecha), fx=FixtureFx(fx, D1), hoy="20260803")
    assert [h.cierre for h in hechos] == [D3] and hechos[0].numero == 2
    est3 = DM.leer_estado(r3.datamart, D3)
    assert est3["fondos"][str(w)]["corrida"] == 2 and est3["fondos"][str(w)]["estado"] == "REEXPRESADA"
    otros = [f for f in est3["fondos"] if f != str(w) and est3["fondos"][f]["estado"] in ("PUBLICADA", "REEXPRESADA")]
    assert all(est3["fondos"][f]["corrida"] == 1 for f in otros) and len(otros) >= 5
    p3 = hechos[0].posiciones().set_index("Pos_ID")
    assert (p3.loc[pos_v.loc[idx, "Pos_ID"], "Hedge_Currency"] == "EUR").all()
    assert IMP.impacto(r3, hoy="20260803").empty

    # ── política diaria: código y REGLAS globales solo marcan; REGLAS con ID_Fund re-expresa ese fondo; ventana
    monkeypatch.setattr(DM, "hash_codigo", lambda *a, **k: "0000000000000000")
    imp = IMP.impacto(r3, hoy="20260803")
    assert set(imp["consecuencia"]) == {"CODIGO"} and (imp["accion"] == "MARCAR").all() and len(imp) == 3
    assert IMP.reexpresar_impactados(r3, imp, hoy="20260803") == []
    monkeypatch.undo()
    with pd.ExcelWriter(fx / "REGLAS.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w_:
        ov = pd.read_excel(fx / "REGLAS.xlsx", sheet_name="overrides_valor")
        fila = {c: None for c in ov.columns}
        pk = res3.posiciones[res3.posiciones["ID_Fund"].eq(x)].iloc[0]
        fila.update(ID_Fund=x, ID_Instrumento=int(pk["ID_Instrumento"]), SubID_Instrumento=int(pk["SubID_Instrumento"]), Yield=0.01, Duration=1.0,
                    Fecha_Desde="2026-01-01", Comentario="test")
        pd.concat([ov, pd.DataFrame([fila])], ignore_index=True).to_excel(w_, sheet_name="overrides_valor", index=False)
    imp = IMP.impacto(r3, hoy="20260803")
    reg = imp[imp["consecuencia"].eq("REGLAS")]
    assert set(reg["atributo"]) == {"REGLAS/overrides_valor"} and set(reg["fondo"]) == {x} and (reg["accion"] == "REEXPRESAR").all()
    assert IMP.impacto(r3, hoy="20261001").empty and not IMP.impacto(r3, hoy="20261001", todos=True).empty          # ventana de 30 días
    assert IMP.ventana_de(r3) == 30 and IMP.ventana_de(dataclasses.replace(r3, modo="mensual")) is None
    assert IMP.politica_reexpresion({}, "diario") == {"MAESTRO", "DIM", "INSUMO", "CADENA"} and "CODIGO" in IMP.politica_reexpresion({}, "mensual")
