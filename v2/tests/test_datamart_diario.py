"""H10a: layout diario, escrituras atómicas, candado, migración, caché compartida."""
import json

import pandas as pd
import pytest

from reporteria import datamart as DMV
from reporteria.adaptadores import datamart as DM


def _hojas(fondos=(20, 16)):
    pos = pd.DataFrame({"Pos_ID": [f"{f}|{i}-1|Asset" for i, f in enumerate(fondos)], "ID_Fund": list(fondos), "Yield": [0.05] * len(fondos),
                        "Estado": ["RESUELTO"] * len(fondos), "Fuente": ["JPM"] * len(fondos), "TotalMVal": [100.0] * len(fondos)})
    return {"cartera_final": pos, "alertas": pd.DataFrame({"Nombre": ["A"]})}, pos


def _borrador(tmp_path, nombre, fecha="20260731"):
    hojas, pos = _hojas()
    return DM.escribir_version(tmp_path / "b" / nombre, hojas, {"fecha": fecha, "ts": "2026-08-01 10:00:00", "estado": "BORRADOR", "completitud": "COMPLETA"}, pos)


def test_escritura_atomica_y_tmp_ignorado(tmp_path):
    d = _borrador(tmp_path, "x")
    assert d.exists() and not d.with_name("x.tmp").exists() and (d / "corrida.json").exists()
    raiz = tmp_path / "dm"
    DMV.publicar(raiz, "20260731", d, modo="diario")
    huerfana = DM.dir_fecha(raiz, "20260731") / "corrida=002.tmp"
    huerfana.mkdir()
    (huerfana / "posiciones.parquet").write_bytes(b"")
    sin_json = DM.dir_fecha(raiz, "20260731") / "corrida=003"
    sin_json.mkdir()
    assert DM.listar_versiones(raiz)["version"].tolist() == [1]
    assert DM.limpiar_tmp(raiz) == [str(huerfana)] and not huerfana.exists()


def test_publicar_en_layout_diario_escribe_estado_por_fondo(tmp_path):
    raiz = tmp_path / "dm"
    v1 = DMV.publicar(raiz, "20260731", _borrador(tmp_path, "b1"), modo="diario")
    assert v1.ruta == DM.dir_corrida(raiz, "20260731", 1) and v1.estado == "PUBLICADA"
    est = DM.leer_estado(raiz, "20260731")
    assert est["corridas"]["001"]["estado"] == "PUBLICADA" and set(est["fondos"]) == {"20", "16"}
    assert est["fondos"]["20"]["corrida"] == 1 and est["fondos"]["20"]["historial"][0]["estado"] == "PUBLICADA"
    v2 = DMV.publicar(raiz, "20260731", _borrador(tmp_path, "b2"), motivo="x", reexpresar=True, modo="diario")
    assert v2.numero == 2 and DM.leer_estado(raiz, "20260731")["fondos"]["16"]["corrida"] == 2
    assert DMV.resolver_version(raiz, "20260731", publicada=True).numero == 1 and DMV.ultima_verdad(raiz, "20260731").numero == 2
    assert DMV.versiones_de(raiz, "20260731")[0].corrida["_layout"] == "diario"
    assert DM.fechas(raiz) == ["20260731"] and DM.cierres(raiz) == [] and DM.fechas_diario(raiz) == ["20260731"]
    # ambos layouts conviven: una versión mensual de otra fecha
    DMV.publicar(raiz, "20260630", _borrador(tmp_path, "b3", "20260630"))
    assert DM.fechas(raiz) == ["20260630", "20260731"]
    t = DM.listar_versiones(raiz)
    assert t.set_index("cierre")["layout"].to_dict() == {"20260630": "cierres", "20260731": "diario"}


def test_migrar_a_diario_es_idempotente(tmp_path):
    git = tmp_path / "git"
    DMV.publicar(git, "20260731", _borrador(tmp_path, "m1"))
    DMV.publicar(git, "20260731", _borrador(tmp_path, "m2"), motivo="re", reexpresar=True)
    DM.escribir_base(git, "20260801_000000", {"bd_instrumentos": pd.DataFrame({"PK2": ["1-1"]})})
    share = tmp_path / "share"
    hechos = DMV.migrar_a_diario(git, share)
    assert hechos == ["20260731/v001", "20260731/v002", "maestros"]
    est = DM.leer_estado(share, "20260731")
    assert set(est["corridas"]) == {"001", "002"} and est["fondos"]["20"]["corrida"] == 2 and est["corridas"]["002"]["estado"] == "REEXPRESADA"
    assert DM.bases(share) == ["20260801_000000"]
    assert DMV.migrar_a_diario(git, share) == ["maestros"]                 # segunda vez: nada nuevo
    assert DMV.ultima_verdad(share, "20260731").numero == 2 and DMV.resolver_version(share, "20260731", publicada=True).estado == "PUBLICADA"


def test_lock_excluye_y_vence(tmp_path):
    raiz = tmp_path / "dm"
    with DM.Lock(raiz) as l1:
        assert l1.adquirido and (raiz / ".lock").exists()
        with pytest.raises(RuntimeError, match="bloqueado"):
            with DM.Lock(raiz):
                pass
    assert not (raiz / ".lock").exists()
    (raiz / ".lock").write_text(json.dumps({"host": "x", "pid": 1, "epoch": 0}), encoding="utf-8")       # candado viejo
    with DM.Lock(raiz, horas=1) as l2:
        assert l2.adquirido


def test_rutas_cache_modo_y_bloquea_publicacion(monkeypatch, tmp_path, fixtures):
    from reporteria.config import Rutas
    from reporteria.lectura.reglas import leer_reglas
    for v in ("RUTA_CUBO_DIR", "RUTA_BIX", "REPORTERIA_DIM", "REPORTERIA_DATAMART"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("REPORTERIA_CACHE", str(tmp_path / "cache_share"))
    monkeypatch.setenv("REPORTERIA_MODO", "diario")
    r = Rutas.desde_env("20260731")
    assert r.cache == tmp_path / "cache_share" / "20260731" and r.modo == "diario"
    monkeypatch.setenv("REPORTERIA_MODO", "semanal")
    with pytest.raises(ValueError, match="REPORTERIA_MODO"):
        Rutas.desde_env("20260731")
    monkeypatch.delenv("REPORTERIA_MODO")
    monkeypatch.delenv("REPORTERIA_CACHE")
    r = Rutas.desde_env("20260731")
    assert r.modo == "mensual" and r.cache.parent.name == "04_CACHE"
    rg = leer_reglas(fixtures / "REGLAS.xlsx")
    assert "Bloquea_Publicacion" in rg.alertas.columns and not rg.alertas["Bloquea_Publicacion"].astype(bool).any()     # default NO (como Activa, queda bool)


def test_correr_sin_excel(fixtures, tmp_path):
    from reporteria.adaptadores.bbg import FixtureBloomberg
    from reporteria.adaptadores.fx_sql import FixtureFx
    from reporteria.config import Rutas
    from reporteria.pipeline import Opciones, correr
    r = Rutas.para_pruebas("20260731", fixtures, tmp_path)
    res = correr(r, Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fixtures, "20260731"), excel=False))
    assert res.excel is None and not r.excel_final.exists() and res.borrador is not None and (r.outputs / "resumen_corrida_20260731.json").exists()
