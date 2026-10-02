"""H10d: orquestación nocturna sobre el fixture de tres días del mini (modo diario), noche a noche:
sábado sin nada que esperar · lunes con tres fechas LISTA (una PROVISORIO por JPM ausente) · re-corrida idempotente ·
lock ajeno y vencido · martes: JPM tardío destraba el PROVISORIO y un CUBO ausente ya esperado · CUBO inválido que no frena a
las demás fechas · catch-up al llegar el CUBO · Teams (JSON, dry-run, envío) · cierre-mensual a git · limpiar."""
import dataclasses
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from reporteria import datamart as DMV
from reporteria import notificacion as NOT
from reporteria import orquestacion as ORQ
from reporteria.adaptadores import datamart as DM
from reporteria.adaptadores import dim as DIMA
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import Rutas
from reporteria.lectura import maestros as M
from reporteria.pipeline import Opciones
from reporteria.publicacion import OFICIALES, estado_fondos

FIXTURES = Path(__file__).parent / "fixtures" / "mini"
D1, D2, D3, D4, D5 = "20260731", "20260801", "20260802", "20260803", "20260804"     # vie, sáb, dom, lun, mar
SAB, LUN, MAR, MIE, JUE = "20260801", "20260803", "20260804", "20260805", "20260806"  # noches


def _clonar_cache(origen: Path, destino: Path, fecha: str) -> None:
    for p in origen.rglob("*"):
        q = destino / str(p.relative_to(origen)).replace(D1, fecha)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, q)


def _parametros(fx: Path, nuevos: dict) -> None:
    with pd.ExcelWriter(fx / "REGLAS.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        par = pd.read_excel(fx / "REGLAS.xlsx", sheet_name="parametros")
        par = pd.concat([par, pd.DataFrame([{"Clave": k, "Valor": v, "Descripcion": "test"} for k, v in nuevos.items()])], ignore_index=True)
        par.to_excel(w, sheet_name="parametros", index=False)


@pytest.fixture(scope="module")
def escenario(tmp_path_factory, dim_fixtures):
    tmp = tmp_path_factory.mktemp("orq")
    fx = tmp / "fx"
    shutil.copytree(FIXTURES, fx, ignore=shutil.ignore_patterns("csv", "dimensionales.duckdb", "bbg_cache"))
    shutil.copy(FIXTURES / "dimensionales.duckdb", fx)
    cubo = pd.read_excel(fx / f"CUBO_{D1}.xlsx")
    en_cubo = set(cubo["ID_Fund"].astype(int))
    d = DIMA.leer(fx / "dimensionales.duckdb")                                     # esperados = los 14 fondos del mini
    d.fondos["Activo_MantenedorFondos"] = d.fondos["ID_Fund"].astype(int).isin(en_cubo).astype(int)
    DIMA.escribir(fx / "dimensionales.duckdb", d)
    _parametros(fx, {"cobertura_min_mv_diario": 0, "insumos_obligatorios": "CUBO;JPM", "ventana_reexpresion_dias": 30, "nocturna_desde": D1})
    for f in (D1, D2, D3, D4, D5):
        _clonar_cache(FIXTURES / "bbg_cache", tmp / "cache" / f, f)
    for f in (D3, D4, D5):                                                          # JPM del día 2 llega tarde
        shutil.copy(fx / f"JPM_CEMBI_GBI_{D1}.xlsx", fx / f"JPM_CEMBI_GBI_{f}.xlsx")
    x = int(cubo["ID_Fund"].value_counts().index[1])                                # X: falta del CUBO del 3 de agosto

    def rutas(hoy):
        return dataclasses.replace(Rutas.para_pruebas(hoy, fx, tmp), modo="diario", cache=tmp / "cache" / hoy)

    def noche(hoy, **kw):
        return ORQ.diario(rutas(hoy), hoy=hoy, opciones=Opciones(sin_sql=True, sin_facts=True, fx=FixtureFx(fx, D1)), **kw)

    R, S, lecturas_bix = {}, {}, []
    raiz = rutas(D1).datamart
    leer_real = M.leer_bd_instrumentos
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(M, "leer_bd_instrumentos", lambda p: (lecturas_bix.append(str(p)), leer_real(p))[1])
        # ── sábado 1-ago: el CUBO del viernes aún no llegó y no se espera hasta el lunes → silencio
        (fx / f"CUBO_{D1}.xlsx").rename(fx / "CUBO_pendiente.xlsx")
        R["sabado"] = noche(SAB)
        S["sabado"] = _snap(raiz)
        (fx / "CUBO_pendiente.xlsx").rename(fx / f"CUBO_{D1}.xlsx")
        # ── lunes 3-ago: viernes, sábado y domingo LISTA; el sábado sin JPM (obligatorio) queda PROVISORIO
        cubo.to_excel(fx / f"CUBO_{D2}.xlsx", index=False)
        cubo.to_excel(fx / f"CUBO_{D3}.xlsx", index=False)
        R["lunes"] = noche(LUN)
        S["lunes"] = _snap(raiz)
        R["bix_lunes"] = list(lecturas_bix)
        lecturas_bix.clear()
        R["lunes_bis"] = noche(LUN)                                                  # misma noche otra vez: nada que hacer
        S["lunes_bis"] = _snap(raiz)
        # ── martes 4-ago: lock ajeno vigente → 3; vencido → corre. JPM del sábado llegó; el CUBO del lunes 3 no.
        lock = rutas(MAR).datamart / ".lock"
        lock.write_text(json.dumps({"host": "otro", "pid": 1, "epoch": __import__("time").time(), "nombre": "diario"}), encoding="utf-8")
        R["lock"] = noche(MAR)
        S["lock"] = _snap(raiz)
        R["lock_quedo"] = lock.exists()
        lock.write_text(json.dumps({"host": "otro", "pid": 1, "epoch": 0, "nombre": "diario"}), encoding="utf-8")
        shutil.copy(fx / f"JPM_CEMBI_GBI_{D1}.xlsx", fx / f"JPM_CEMBI_GBI_{D2}.xlsx")
        R["martes"] = noche(MAR)
        S["martes"] = _snap(raiz)
        # ── miércoles 5-ago: CUBO del lunes 3 inválido (sin columnas), CUBO del martes 4 bien → el 4 se publica igual
        pd.DataFrame({"Foo": [1]}).to_excel(fx / f"CUBO_{D4}.xlsx", index=False)
        cubo.to_excel(fx / f"CUBO_{D5}.xlsx", index=False)
        R["miercoles"] = noche(MIE)
        S["miercoles"] = _snap(raiz)
        # ── jueves 6-ago: llega el CUBO del 3 (sin X) → catch-up; el del miércoles 5 no llegó
        cubo[cubo["ID_Fund"].ne(x)].to_excel(fx / f"CUBO_{D4}.xlsx", index=False)
        R["jueves"] = noche(JUE)
        S["jueves"] = _snap(raiz)
    return dict(tmp=tmp, fx=fx, rutas=rutas, noche=noche, R=R, S=S, x=x, fondos=sorted(en_cubo))


def _conteo(raiz, fecha):
    return estado_fondos(raiz, fecha)["estado"].value_counts().to_dict()


def _snap(raiz):
    """Foto del datamart al terminar una noche (los tests leen la foto, no el estado final del escenario)."""
    fechas = DM.fechas(raiz)
    return dict(fechas=fechas, corridas={f: DM.ultima_corrida(raiz, f) for f in fechas}, estado={f: estado_fondos(raiz, f) for f in fechas},
                conteo={f: _conteo(raiz, f) for f in fechas}, json={f: DM.leer_estado(raiz, f) for f in fechas})


def test_sabado_sin_nada_que_esperar_es_silencio(escenario):
    n = escenario["R"]["sabado"]
    assert n.codigo == 0 and [(f["fecha"], f["estado"]) for f in n.fechas] == [(D1, "AUN_NO_ESPERADA")]
    assert not n.reevaluadas and not n.reexpresadas and n.backlog.empty
    assert escenario["S"]["sabado"]["fechas"] == []                                        # no deja estado.json ni carpetas
    assert n.teams["enviado"] is False and "nada" in n.teams["motivo"]
    assert n.informe is not None and n.informe.exists()


def test_lunes_tres_fechas_una_provisoria_y_maestros_una_vez(escenario):
    n, S, raiz = escenario["R"]["lunes"], escenario["S"]["lunes"], escenario["rutas"](LUN).datamart
    assert n.codigo == 1
    assert [(f["fecha"], f["estado"], f["corrida"]) for f in n.fechas] == [(D1, "CORRIDA", 1), (D2, "CORRIDA", 1), (D3, "CORRIDA", 1)]
    assert S["conteo"][D1] == {"PUBLICADA": 14} and S["conteo"][D3] == {"PUBLICADA": 14} and S["fechas"] == [D1, D2, D3]
    e2 = S["estado"][D2]
    assert S["conteo"][D2] == {"PROVISORIO": 14} and set(e2["bloqueos"]) == {"INSUMO_FALTANTE:JPM"} and e2["publicada"].isna().all()
    assert n.fechas[1]["fondos"] == {"PROVISORIO": 14}
    assert len(escenario["R"]["bix_lunes"]) == 1                                      # el BIX se lee una sola vez por noche
    assert sorted(n.backlog["fecha"].unique()) == [D2] and len(n.backlog) == 14
    assert n.reexpresadas == [] and n.errores == []
    # informe en 03_LOGS y en el datamart; JSON de Teams guardado aunque no haya URL
    logs = escenario["rutas"](LUN).logs.parent
    assert (logs / f"estado_diario_{LUN}.md").exists() and (raiz / "estado" / f"estado_diario_{LUN}.md").exists()
    md = (logs / f"estado_diario_{LUN}.md").read_text(encoding="utf-8")
    assert D2 in md and "INSUMO_FALTANTE:JPM" in md and "PROVISORIO" in md
    assert n.teams["enviado"] is False and "URL" in n.teams["motivo"] and Path(n.teams["archivo"]).exists()
    carga = json.loads(Path(n.teams["archivo"]).read_text(encoding="utf-8"))
    assert carga["type"] == "message" and carga["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    cuerpo = json.dumps(carga["attachments"][0]["content"], ensure_ascii=False)
    assert "14 provisorios" in cuerpo.lower() or "provisorio" in cuerpo.lower()
    assert D2 in cuerpo and "JPM" in cuerpo


def test_misma_noche_otra_vez_no_hace_nada(escenario):
    n, S = escenario["R"]["lunes_bis"], escenario["S"]["lunes_bis"]
    assert n.codigo == 1 and n.fechas == [] and n.reevaluadas == [] and n.reexpresadas == []
    assert S["corridas"] == {D1: 1, D2: 1, D3: 1} and S["conteo"][D2] == {"PROVISORIO": 14}


def test_lock_ajeno_vigente_sale_3_y_vencido_corre(escenario):
    n = escenario["R"]["lock"]
    assert n.codigo == 3 and n.fechas == [] and escenario["R"]["lock_quedo"] and escenario["S"]["lock"]["corridas"] == escenario["S"]["lunes_bis"]["corridas"]
    assert escenario["R"]["martes"].codigo != 3 and not (escenario["rutas"](MAR).datamart / ".lock").exists()


def test_martes_insumo_tardio_destraba_y_cubo_ausente_es_sin_corrida(escenario):
    n, S = escenario["R"]["martes"], escenario["S"]["martes"]
    assert [(f["fecha"], f["estado"]) for f in n.fechas] == [(D4, "SIN_CUBO")]
    e4 = S["estado"][D4]
    assert S["conteo"][D4] == {"SIN_CORRIDA": 14} and set(e4["bloqueos"]) == {"SIN_CUBO"} and e4["corrida"].isna().all()
    assert S["corridas"][D4] == 0 and S["json"][D4]["esperados"] == escenario["fondos"]
    assert [(r["fecha"], r["disparadores"], r["corrida"]) for r in n.reevaluadas] == [(D2, ["INSUMO_FALTANTE:JPM"], 2)]
    assert S["conteo"][D2] == {"PUBLICADA": 14} and S["corridas"][D2] == 2 and (S["estado"][D2]["publicada"] == 2).all()
    assert n.reexpresadas == [] and S["corridas"][D3] == 1                            # el hedge heredado no cambió: sin CADENA
    assert n.codigo == 1 and sorted(n.backlog["fecha"].unique()) == [D4]


def test_miercoles_cubo_invalido_no_frena_a_las_demas(escenario):
    n, S, raiz = escenario["R"]["miercoles"], escenario["S"]["miercoles"], escenario["rutas"](MIE).datamart
    assert [(f["fecha"], f["estado"]) for f in n.fechas] == [(D4, "SIN_CORRIDA"), (D5, "CORRIDA")]
    e4 = S["estado"][D4]
    assert S["conteo"][D4] == {"SIN_CORRIDA": 14} and all(b.startswith("CUBO_INVALIDO") for b in e4["bloqueos"]) and S["corridas"][D4] == 0
    assert S["conteo"][D5] == {"PUBLICADA": 14}
    assert len(n.errores) == 1 and D4 in n.errores[0] and "CUBO" in n.errores[0]
    assert n.codigo == 1
    # el martes 4 tomó el anterior de cada fondo del domingo 2 (el lunes 3 no existe)
    v5 = DMV.ultima_verdad(raiz, D5)
    assert all(f["cierre"] == D3 for f in v5.corrida["anterior_version"]["fondos"].values())


def test_jueves_catch_up_del_cubo_tardio_y_fondo_sin_posiciones(escenario):
    n, S, raiz, x = escenario["R"]["jueves"], escenario["S"]["jueves"], escenario["rutas"](JUE).datamart, escenario["x"]
    assert [(f["fecha"], f["estado"]) for f in n.fechas] == [(D4, "CORRIDA"), ("20260805", "SIN_CUBO")]
    assert S["conteo"][D4] == {"PUBLICADA": 13, "SIN_CORRIDA": 1} and S["corridas"][D4] == 1
    e4 = S["estado"][D4].set_index("ID_Fund")
    assert e4.loc[x, "estado"] == "SIN_CORRIDA" and e4.loc[x, "bloqueos"] == "FONDO_SIN_POSICIONES"
    hist = S["json"][D4]["fondos"][str(escenario["fondos"][0])]["historial"]
    assert [h["estado"] for h in hist] == ["SIN_CORRIDA", "SIN_CORRIDA", "PUBLICADA"]
    assert hist[0]["bloqueos"] == ["SIN_CUBO"] and hist[1]["bloqueos"][0].startswith("CUBO_INVALIDO")
    assert n.reexpresadas == [] and n.codigo == 1
    assert sorted(n.backlog["fecha"].unique()) == [D4, "20260805"]
    # la vista `estado` ve toda la semana
    con = DM.vistas(raiz)
    assert con.execute("SELECT count(DISTINCT fecha) FROM estado").fetchone()[0] == 6


def test_dry_run_solo_planifica(escenario):
    raiz = escenario["rutas"](JUE).datamart
    antes = {f: DM.ultima_corrida(raiz, f) for f in DM.fechas(raiz)}
    (escenario["fx"] / "CUBO_20260805.xlsx").write_bytes((escenario["fx"] / f"CUBO_{D5}.xlsx").read_bytes())
    n = escenario["noche"]("20260807", dry_run=True)
    (escenario["fx"] / "CUBO_20260805.xlsx").unlink()
    assert [(f["fecha"], f["estado"]) for f in n.fechas] == [("20260805", "LISTA"), ("20260806", "SIN_CUBO")]
    assert {f: DM.ultima_corrida(raiz, f) for f in DM.fechas(raiz)} == antes and "20260806" not in DM.fechas(raiz)
    assert n.teams["enviado"] is False and Path(n.teams["archivo"]).exists()
    assert not (raiz / ".lock").exists()


def test_teams_envia_con_urllib_y_no_rompe_si_falla(escenario, monkeypatch, tmp_path):
    resumen = escenario["R"]["martes"].resumen()
    assert resumen["hoy"] == MAR and resumen["codigo"] == 1 and resumen["fechas"][0]["fecha"] == D4
    tarjeta = NOT.tarjeta(resumen)
    assert tarjeta["attachments"][0]["content"]["type"] == "AdaptiveCard" and tarjeta["attachments"][0]["content"]["body"]
    # dry-run: JSON y nada más
    r = NOT.teams(resumen, "https://example.invalid/hook", tmp_path, dry_run=True)
    assert r["enviado"] is False and r["motivo"] == "dry-run" and json.loads(Path(r["archivo"]).read_text(encoding="utf-8")) == tarjeta
    # envío real: POST con urllib
    enviados = []

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"1"
    import urllib.request as UR
    monkeypatch.setattr(UR, "urlopen", lambda req, timeout=30: (enviados.append((req.full_url, req.get_header("Content-type"), json.loads(req.data))), _Resp())[1])
    r = NOT.teams(resumen, "https://example.invalid/hook", tmp_path)
    assert r["enviado"] is True and r["estado"] == 202 and enviados[0][0] == "https://example.invalid/hook"
    assert enviados[0][1].startswith("application/json") and enviados[0][2] == tarjeta
    # sin URL: aviso; URL que falla: aviso, nunca excepción

    def _falla(req, timeout=30):
        raise OSError("sin red")
    monkeypatch.setattr(UR, "urlopen", _falla)
    r = NOT.teams(resumen, "https://example.invalid/hook", tmp_path)
    assert r["enviado"] is False and "sin red" in r["motivo"]
    r = NOT.teams(resumen, None, tmp_path)
    assert r["enviado"] is False and "URL" in r["motivo"]


def test_cierre_mensual_copia_la_ultima_corrida_al_layout_de_git(escenario):
    rutas, raiz = escenario["rutas"](JUE), escenario["rutas"](JUE).datamart
    git = escenario["tmp"] / "git"
    with pytest.raises(ValueError, match="último día"):
        ORQ.cierre_mensual(rutas, D2, git)
    # un fondo no oficial: se niega salvo --forzar
    est = DM.leer_estado(raiz, D1)
    original = json.dumps(est)
    est["fondos"][str(escenario["fondos"][0])]["estado"] = "PROVISORIO"
    DM.escribir_estado(raiz, D1, est)
    with pytest.raises(ValueError, match=str(escenario["fondos"][0])):
        ORQ.cierre_mensual(rutas, D1, git)
    DM.escribir_estado(raiz, D1, json.loads(original))
    r = ORQ.cierre_mensual(rutas, D1, git)
    assert r["copiada"] and r["version"] == 1 and r["corrida"] == 1
    v = DMV.versiones_de(git, D1)
    assert len(v) == 1 and v[0].estado == "PUBLICADA" and v[0].ruta == DM.dir_version(git, D1, 1) and v[0].corrida["_layout"] == "cierres"
    assert v[0].posiciones() is not None and len(v[0].posiciones()) == len(DMV.ultima_verdad(raiz, D1).posiciones())
    pub = DM.leer_publicacion(git, D1)
    assert pub[0]["origen"]["corrida"] == 1 and pub[0]["fondos"]["PUBLICADA"] == 14 and "cierre mensual" in pub[0]["motivo"]
    assert DM.leer_estado(raiz, D1)["cierre_mensual"] is True
    r2 = ORQ.cierre_mensual(rutas, D1, git)                                          # idempotente
    assert not r2["copiada"] and len(DMV.versiones_de(git, D1)) == 1
    runner = CliRunner()
    env = {"REPORTERIA_DATAMART": str(raiz), "REPORTERIA_MODO": "diario", "REPORTERIA_DIM": str(rutas.dim), "RUTA_CUBO_DIR": str(escenario["fx"])}
    out = runner.invoke(app_cli(), ["cierre-mensual", "--fecha", D1, "--destino", str(git), "--raiz", str(escenario["tmp"])], env=env)
    assert out.exit_code == 0 and "ya" in out.output, out.output


def test_limpiar_retiene_lo_apuntado_y_lo_oficial(escenario):
    rutas, raiz, tmp = escenario["rutas"](JUE), escenario["rutas"](JUE).datamart, escenario["tmp"]
    plan = ORQ.limpiar(rutas, dias=100, hoy="20261231", dry_run=True)
    assert plan["corridas"] == [str(DM.dir_corrida(raiz, D2, 1))]                     # superada: todos pasaron a la 2
    assert set(plan["cache"]) == {str(tmp / "cache" / f) for f in (D1, D2, D3, D4, D5)}
    assert all(Path(p).exists() for p in plan["corridas"] + plan["cache"]) and len(plan["borradores"]) >= 5
    assert ORQ.limpiar(rutas, dias=400, hoy="20260901", dry_run=True) == {"corridas": [], "cache": [], "borradores": [], "tmp": []}
    hecho = ORQ.limpiar(rutas, dias=100, hoy="20261231")
    assert hecho == plan and not DM.dir_corrida(raiz, D2, 1).exists() and not (tmp / "cache" / D1).exists()
    assert DMV.ultima_verdad(raiz, D2).numero == 2 and _conteo(raiz, D2) == {"PUBLICADA": 14} and DM.ultima_corrida(raiz, D1) == 1
    runner = CliRunner()
    env = {"REPORTERIA_DATAMART": str(raiz), "REPORTERIA_MODO": "diario", "REPORTERIA_DIM": str(rutas.dim), "REPORTERIA_CACHE": str(tmp / "cache")}
    out = runner.invoke(app_cli(), ["limpiar", "--dias", "100", "--hoy", "20261231", "--dry-run", "--raiz", str(tmp)], env=env)
    assert out.exit_code == 0 and "0 corridas" in out.output, out.output


def test_cli_diario_requiere_modo_diario(escenario):
    runner = CliRunner()
    out = runner.invoke(app_cli(), ["diario", "--hoy", JUE, "--dry-run", "--raiz", str(escenario["tmp"])],
                        env={"REPORTERIA_DATAMART": str(escenario["rutas"](JUE).datamart), "REPORTERIA_MODO": "mensual"})
    assert out.exit_code == 2 and "REPORTERIA_MODO" in out.output


def app_cli():
    from reporteria.cli import app
    return app
