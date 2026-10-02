"""H10e: pasada BBG en la estación con terminal, cuarentena por fondo (correr_aislado) e INSUMO_INVALIDO en la nocturna,
reporte mensual y modo diario por defecto. Escenario: tres fechas del mini (31-jul, 1-ago, 2-ago) en modo diario."""
import dataclasses
import shutil
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from reporteria import agregados as AGG
from reporteria import datamart as DMV
from reporteria import orquestacion as ORQ
from reporteria.adaptadores import datamart as DM
from reporteria.adaptadores import dim as DIMA
from reporteria.adaptadores.bbg import CacheBloomberg, FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import Rutas
from reporteria.pipeline import Opciones, correr, correr_aislado
from reporteria.publicacion import estado_fondos

D1, D2, D3 = "20260731", "20260801", "20260802"


def _clonar_cache(origen: Path, destino: Path, fecha: str) -> None:
    for p in origen.rglob("*"):
        q = destino / str(p.relative_to(origen)).replace(D1, fecha)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, q)


class TerminalQueAprende:
    """Terminal falsa: responde yield/duration para cualquier ticker que se le pida (el CacheBloomberg lo persiste)."""

    def __init__(self):
        self.pedidos = []
        self.caida = False
        self.errores, self.avisos = [], []

    def bdp(self, tickers, campo, **ov):
        self.pedidos.append((campo, tuple(tickers)))
        val = {"YAS_BOND_YLD": 6.5, "YAS_MOD_DUR": 3.2, "YAS_YLD_MATURITY": 6.5, "YAS_YLD_CALL": 6.5, "YAS_YLD_AVG_LIFE": 6.5}.get(campo)
        return pd.Series({t: val for t in tickers}, dtype=float) if val is not None else pd.Series(dtype=float)

    def historico(self, tickers, campo, fecha):
        return pd.Series(dtype=float)

    def bds(self, ticker, campo, **ov):
        return pd.DataFrame()


@pytest.fixture
def entorno(fixtures, tmp_path):
    fx = tmp_path / "fx"
    shutil.copytree(fixtures, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb", "bbg_cache"))
    shutil.copy(fixtures / "dimensionales.duckdb", fx)
    cubo = pd.read_excel(fx / f"CUBO_{D1}.xlsx")
    en_cubo = set(cubo["ID_Fund"].astype(int))
    d = DIMA.leer(fx / "dimensionales.duckdb")
    d.fondos["Activo_MantenedorFondos"] = d.fondos["ID_Fund"].astype(int).isin(en_cubo).astype(int)
    DIMA.escribir(fx / "dimensionales.duckdb", d)
    with pd.ExcelWriter(fx / "REGLAS.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        par = pd.read_excel(fx / "REGLAS.xlsx", sheet_name="parametros")
        par = pd.concat([par, pd.DataFrame([{"Clave": k, "Valor": v, "Descripcion": "test"} for k, v in
                                            {"cobertura_min_mv_diario": 0, "ventana_reexpresion_dias": 30, "nocturna_desde": D1}.items()])], ignore_index=True)
        par.to_excel(w, sheet_name="parametros", index=False)
    for f in (D1, D2, D3):
        _clonar_cache(fixtures / "bbg_cache", tmp_path / "cache" / f, f)
        if f != D1:
            cubo.to_excel(fx / f"CUBO_{f}.xlsx", index=False)
    # un equity con ISIN de un solo fondo pasa a renta fija: va a CASCADA y no tiene YAS en caché → PENDIENTE_TERMINAL en ese fondo
    bd = pd.read_excel(fx / "BD_INSTRUMENTOS.xlsx")
    r0 = dataclasses.replace(Rutas.para_pruebas(D1, fx, tmp_path), modo="diario", cache=tmp_path / "cache" / D1)
    res0 = correr(r0, Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r0.cache, D1), fx=FixtureFx(fx, D1), excel=False))
    pos = res0.posiciones
    cand = pos[pos["Investment_Type_Code"].eq(2) & pos["ISIN"].astype(str).str.len().gt(5) & pos["BalanceSheet"].eq("Asset")]
    pk2 = next(p for p in cand["PK2"] if pos["PK2"].eq(p).sum() == 1)
    eq = cand[cand["PK2"].eq(pk2)].iloc[0]
    i, sub = (int(x) for x in pk2.split("-"))
    m = (bd["ID_Instrumento"] == i) & (bd["SubID_Instrumento"] == sub)
    bd.loc[m, ["Investment_Type_Code", "Issue_Type_Code", "Issuer_Type_Code", "Coupon_Type_Code", "Rank_Code"]] = [1, 3, 1, 1, 2]
    bd.to_excel(fx / "BD_INSTRUMENTOS.xlsx", index=False)
    shutil.rmtree(tmp_path / "datamart", ignore_errors=True)                           # la corrida de reconocimiento no deja rastro
    shutil.rmtree(tmp_path / "02_OUTPUTS", ignore_errors=True)

    def rutas(hoy):
        return dataclasses.replace(Rutas.para_pruebas(hoy, fx, tmp_path), modo="diario", cache=tmp_path / "cache" / hoy)

    opciones = Opciones(sin_sql=True, sin_facts=True, fx=FixtureFx(fx, D1))
    return dict(tmp=tmp_path, fx=fx, rutas=rutas, opciones=opciones, fondo=int(eq["ID_Fund"]), isin=str(eq["ISIN"]), pos_id=eq["Pos_ID"],
                fondos=sorted(en_cubo), cubo=cubo)


def test_pasada_bbg_cierra_pendientes_y_la_segunda_no_pide_nada(entorno, monkeypatch):
    rutas, opciones, fondo = entorno["rutas"], entorno["opciones"], entorno["fondo"]
    raiz = rutas(D2).datamart
    # nocturna del sábado 1-ago: procesa el 31-jul; el fondo con el equity reclasificado queda PROVISORIO por PENDIENTE_TERMINAL
    n = ORQ.diario(rutas(D2), hoy=D2, opciones=opciones)
    assert [(f["fecha"], f["estado"]) for f in n.fechas] == [(D1, "CORRIDA")] and n.codigo == 1
    e1 = estado_fondos(raiz, D1).set_index("ID_Fund")
    assert e1.loc[fondo, "estado"] == "PROVISORIO" and e1.loc[fondo, "bloqueos"].startswith("PENDIENTE_TERMINAL:1")
    assert (e1.drop(index=fondo)["estado"] == "PUBLICADA").all()
    assert ORQ.fechas_con_pendientes(raiz, D2, 30) == [D1]
    # sin xbbg en esta máquina: la pasada se niega con código 2 y no toca nada
    monkeypatch.setattr("reporteria.adaptadores.bbg.xbbg_disponible", lambda: ("no_instalado", ""))
    n0 = ORQ.pasada_bbg(rutas(D2), hoy=D2, todas=True, opciones=opciones)
    assert n0.codigo == 2 and "xbbg" in n0.errores[0] and DM.ultima_corrida(raiz, D1) == 1
    # pasada con una terminal que responde: el CacheBloomberg persiste y el fondo se destraba
    terminales = []

    def factory(rf):
        t = TerminalQueAprende()
        terminales.append(t)
        return CacheBloomberg(t, rf.cache, rf.fecha)
    n1 = ORQ.pasada_bbg(rutas(D2), hoy=D2, todas=True, opciones=opciones, bbg_factory=factory)
    assert n1.codigo == 0 and [(r["fecha"], r["corrida"], r["pendientes"]) for r in n1.reevaluadas] == [(D1, 2, 0)]
    assert any(entorno["isin"] in t for campo, ts in terminales[0].pedidos for t in ts)
    e1 = estado_fondos(raiz, D1).set_index("ID_Fund")
    assert e1.loc[fondo, "estado"] == "PUBLICADA" and e1.loc[fondo, "corrida"] == 2 and e1.loc[fondo, "bloqueos"] == ""
    assert (e1.drop(index=fondo)["corrida"] == 1).all()                                 # los demás no se movieron (sin cambio)
    p2 = DMV.ultima_verdad(raiz, D1).posiciones().set_index("Pos_ID")
    assert p2.loc[entorno["pos_id"], "Estado"] == "RESUELTO" and p2.loc[entorno["pos_id"], "Fuente"] == "BBG"
    assert n1.informe is not None and n1.informe.name == f"estado_diario_{D2}_bbg.md" and (raiz / "estado" / n1.informe.name).exists()
    assert n1.teams["enviado"] is False and Path(n1.teams["archivo"]).exists()
    # segunda pasada explícita: nada que pedir (todo en caché), nadie se re-apunta
    n2 = ORQ.pasada_bbg(rutas(D2), hoy=D2, fechas=[D1], opciones=opciones, bbg_factory=factory)
    assert n2.codigo == 0 and n2.reevaluadas[0]["pedidos"] == 0 and terminales[1].pedidos == []
    assert DM.leer_estado(raiz, D1)["corridas"]["003"]["fondos"]["SIN_CAMBIO"] == len(entorno["fondos"])
    assert ORQ.pasada_bbg(rutas(D2), hoy=D2, todas=True, opciones=opciones, bbg_factory=factory).reevaluadas == []
    # la nocturna siguiente ve la caché cambiada: no hay nada que re-evaluar porque ya está publicado
    n3 = ORQ.diario(rutas(D3), hoy=D3, opciones=opciones)
    assert [(f["fecha"], f["estado"]) for f in n3.fechas] == [(D2, "CORRIDA")] and n3.reevaluadas == []


def test_cuarentena_por_fondo_e_insumo_invalido(entorno, monkeypatch):
    rutas, opciones, fondos = entorno["rutas"], entorno["opciones"], entorno["fondos"]
    raiz = rutas(D2).datamart
    culpable = fondos[-1]
    original = AGG.aw_dw

    def revienta(pos, *a, **k):
        if pos["ID_Fund"].eq(culpable).any():
            raise RuntimeError(f"bug sintético en el fondo {culpable}")
        return original(pos, *a, **k)
    monkeypatch.setattr(AGG, "aw_dw", revienta)
    # correr_aislado: el frame completo falla, bisecta, aísla al culpable y corre el resto
    r1 = rutas(D1)
    res, culpables = correr_aislado(r1, dataclasses.replace(opciones, sin_bbg=True, excel=False, bbg=FixtureBloomberg(r1.cache, D1)))
    assert list(culpables) == [culpable] and "bug sintético" in culpables[culpable]
    assert culpable not in set(res.posiciones["ID_Fund"]) and len(set(res.posiciones["ID_Fund"])) == len(fondos) - 1
    shutil.rmtree(raiz, ignore_errors=True)
    # en la nocturna: el fondo queda SIN_CORRIDA(ERROR_FONDO) y los demás se publican; JPM corrupto → INSUMO_INVALIDO, no abort
    (entorno["fx"] / f"JPM_CEMBI_GBI_{D1}.xlsx").write_bytes(b"esto no es un xlsx")
    n = ORQ.diario(rutas(D2), hoy=D2, opciones=opciones)
    assert [(f["fecha"], f["estado"], f["cuarentena"]) for f in n.fechas] == [(D1, "CORRIDA", [culpable])]
    e1 = estado_fondos(raiz, D1).set_index("ID_Fund")
    assert e1.loc[culpable, "estado"] == "SIN_CORRIDA" and e1.loc[culpable, "bloqueos"].startswith("ERROR_FONDO: RuntimeError")
    assert pd.isna(e1.loc[culpable, "corrida"]) and (e1.drop(index=culpable)["estado"] != "SIN_CORRIDA").all()
    assert any("cuarentena" in e and str(culpable) in e for e in n.errores) and n.codigo == 1
    al = DMV.ultima_verdad(raiz, D1).hojas()["alertas"]
    inv = al[al["Nombre"].eq("INSUMO_INVALIDO")]
    assert len(inv) == 1 and inv.iloc[0]["Detalle"].startswith("JPM:")                 # la cuarentena vive en estado.json, no en las alertas
    hist = DM.leer_estado(raiz, D1)["fondos"][str(culpable)]["historial"]
    assert [h["estado"] for h in hist] == ["SIN_CORRIDA", "SIN_CORRIDA"] and hist[0]["bloqueos"] == ["FONDO_SIN_POSICIONES"]
    assert DM.leer_estado(raiz, D1)["esperados"] == fondos
    # al día siguiente, sin el bug, el fondo se publica
    monkeypatch.setattr(AGG, "aw_dw", original)
    n2 = ORQ.diario(rutas(D3), hoy=D3, opciones=opciones)
    assert estado_fondos(raiz, D2).set_index("ID_Fund").loc[culpable, "estado"] == "PUBLICADA"


def test_reporte_mensual_y_cli(entorno):
    rutas, opciones = entorno["rutas"], entorno["opciones"]
    raiz = rutas(D3).datamart
    ORQ.diario(rutas(D3), hoy=D3, opciones=opciones)                                    # 31-jul y 1-ago
    hojas = DMV.reporte_mensual(raiz, "202608")
    assert hojas["resumen"]["Fecha"].tolist() == [D2] and hojas["resumen"].iloc[0]["PUBLICADA"] + hojas["resumen"].iloc[0]["PROVISORIO"] == len(entorno["fondos"])
    assert set(hojas["cartera"]["Fecha"]) == {D2} and len(hojas["cartera"]) > 0 and "Yield" in hojas["cartera"].columns
    assert entorno["fondo"] not in set(hojas["cartera"]["ID_Fund"])                      # el PROVISORIO no entra en lo publicado
    assert set(hojas["agregados"]["Fecha"]) == {D2} and hojas["estado"]["fecha"].eq(D2).all()
    assert DMV.reporte_mensual(raiz, "202607")["resumen"]["Fecha"].tolist() == [D1]
    assert DMV.reporte_mensual(raiz, "202609")["resumen"].empty
    runner = CliRunner()
    env = {"REPORTERIA_DATAMART": str(raiz), "REPORTERIA_MODO": "diario", "REPORTERIA_DIM": str(rutas(D3).dim)}
    out = runner.invoke(_app(), ["reporte", "--mes", "202608", "--raiz", str(entorno["tmp"])], env=env)
    assert out.exit_code == 0 and (entorno["tmp"] / "02_OUTPUTS" / "REPORTE_MES_202608.xlsx").exists(), out.output
    assert set(pd.ExcelFile(entorno["tmp"] / "02_OUTPUTS" / "REPORTE_MES_202608.xlsx").sheet_names) == {"resumen", "estado", "agregados", "cartera"}
    out = runner.invoke(_app(), ["reporte", "--mes", "202609", "--raiz", str(entorno["tmp"])], env=env)
    assert out.exit_code == 1
    out = runner.invoke(_app(), ["pasada-bbg", "--raiz", str(entorno["tmp"])], env=env)
    assert out.exit_code == 2 and "--todas" in out.output


def test_modo_diario_por_defecto_y_aviso_en_check(tmp_path, monkeypatch):
    for v in ("REPORTERIA_MODO", "REPORTERIA_DATAMART", "RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_DIM", "REPORTERIA_CACHE"):
        monkeypatch.delenv(v, raising=False)
    assert Rutas.desde_env("20260731", tmp_path).modo == "diario"
    monkeypatch.setenv("REPORTERIA_MODO", "mensual")
    assert Rutas.desde_env("20260731", tmp_path).modo == "mensual"
    monkeypatch.delenv("REPORTERIA_MODO")
    out = CliRunner().invoke(_app(), ["check", "--fecha", "20260731", "--raiz", str(tmp_path)])
    assert "modo diario (default desde H10e)" in out.output and "REPORTERIA_MODO=mensual" in out.output


def _app():
    from reporteria.cli import app
    return app
