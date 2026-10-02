import json

import numpy as np
import pandas as pd
import pytest

from reporteria import datamart as DMV
from reporteria.adaptadores import datamart as DM


def _hojas():
    pos = pd.DataFrame({"Pos_ID": ["20|1-39|Asset", "16|2-1|Asset"], "Investment_Type_Code": pd.array([1, None], dtype="Int64"),
                        "Yield": [0.05, np.nan], "Fuente": ["JPM", ""], "Fecha": pd.to_datetime(["2026-07-31", None]), "Flag": [True, False]})
    alertas = pd.DataFrame({"Nombre": ["A", "B"], "Valor": [1.5, "texto"], "Detalle": ["", None]})      # columna mixta → texto
    return {"cartera_final": pos, "alertas": alertas, "vacia": pd.DataFrame()}, pos


def test_escribir_y_leer_version_conserva_tipos_y_nulos(tmp_path):
    hojas, pos = _hojas()
    d = DM.escribir_version(tmp_path / "v", hojas, {"fecha": "20260731", "ts": "2026-08-01 10:00:00"}, pos)
    h2, pos2, corrida = DM.leer_version(d)
    assert corrida["hojas"] == ["cartera_final", "alertas", "vacia"] and corrida["fecha"] == "20260731"
    assert str(pos2["Investment_Type_Code"].dtype) == "Int64" and pd.isna(pos2.loc[1, "Investment_Type_Code"])
    assert np.isnan(pos2.loc[1, "Yield"]) and pos2.loc[0, "Fuente"] == "JPM" and pos2.loc[1, "Flag"] == False     # noqa: E712
    assert pd.isna(pos2.loc[1, "Fecha"]) and pos2.loc[0, "Fecha"] == pd.Timestamp("2026-07-31")
    assert h2["alertas"]["Valor"].tolist() == ["1.5", "texto"] and h2["vacia"].empty
    assert (d / "posiciones.parquet").exists()


def _borrador(tmp_path, nombre, fecha="20260731", ts="2026-08-01 10:00:00"):
    hojas, pos = _hojas()
    return DM.escribir_version(tmp_path / "borradores" / nombre, hojas, {"fecha": fecha, "ts": ts, "estado": "BORRADOR",
                                                                        "hashes": {"codigo": "abc"}, "completitud": "COMPLETA"}, pos)


def test_publicar_numera_y_exige_reexpresar(tmp_path):
    raiz = tmp_path / "datamart"
    b1 = _borrador(tmp_path, "borrador_1")
    v1 = DMV.publicar(raiz, "20260731", b1)
    assert (v1.numero, v1.estado) == (1, "PUBLICADA") and (raiz / "cierres" / "cierre=20260731" / "version=001" / "posiciones.parquet").exists()
    with pytest.raises(ValueError, match="reexpresar"):
        DMV.publicar(raiz, "20260731", _borrador(tmp_path, "borrador_2"))
    with pytest.raises(ValueError, match="motivo"):
        DMV.publicar(raiz, "20260731", _borrador(tmp_path, "borrador_2"), reexpresar=True)
    v2 = DMV.publicar(raiz, "20260731", _borrador(tmp_path, "borrador_2"), motivo="corrección Risk_Currency", reexpresar=True)
    assert (v2.numero, v2.estado) == (2, "REEXPRESADA")
    hist = json.loads((raiz / "cierres" / "cierre=20260731" / "publicacion.json").read_text())
    assert [h["version"] for h in hist] == [1, 2] and hist[1]["motivo"] == "corrección Risk_Currency"
    with pytest.raises(ValueError, match="cierre"):
        DMV.publicar(raiz, "20260831", _borrador(tmp_path, "borrador_3"))       # borrador de otro cierre
    t = DM.listar_versiones(raiz)
    assert t["version"].tolist() == [1, 2] and t["estado"].tolist() == ["PUBLICADA", "REEXPRESADA"]
    assert DM.cierres(raiz) == ["20260731"]


def test_resolver_version_publicada_ultima_numero_y_conocimiento(tmp_path):
    raiz = tmp_path / "datamart"
    v1 = DMV.publicar(raiz, "20260731", _borrador(tmp_path, "b1"))
    import time
    time.sleep(1.1)
    corte = pd.Timestamp.now().strftime("%Y%m%d")
    v2 = DMV.publicar(raiz, "20260731", _borrador(tmp_path, "b2"), motivo="x", reexpresar=True)
    assert DMV.resolver_version(raiz, "20260731").numero == 2                       # última verdad
    assert DMV.resolver_version(raiz, "20260731", publicada=True).numero == 1
    assert DMV.resolver_version(raiz, "20260731", version=1).numero == 1
    assert DMV.resolver_version(raiz, "20260731", version=9) is None
    assert DMV.resolver_version(raiz, "20260731", conocimiento=corte).numero == 2       # mismo día: fin del día incluye ambas
    ayer = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%Y%m%d")
    assert DMV.resolver_version(raiz, "20260731", conocimiento=ayer) is None
    assert DMV.resolver_version(raiz, "20260630") is None and DMV.ultima_verdad(raiz, None) is None
    assert v1.etiqueta.endswith("PUBLICADA") and "REEXPRESADA" in v2.etiqueta
    dif = DMV.comparar_versiones(v1, v2)
    assert dif.empty


def test_hash_codigo_y_archivo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    h1 = DM.hash_codigo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")
    assert DM.hash_codigo(tmp_path) != h1 and len(h1) == 16
    assert DM.hash_archivo(tmp_path / "no.txt") == "" and DM.hash_archivo(tmp_path / "a.py")
    assert DM.hash_carpeta(tmp_path)[1] == 1 and DM.hash_carpeta(tmp_path / "nada") == ("", 0)


def test_hash_carpeta_por_stat_no_lee_contenido(tmp_path, monkeypatch):
    """La huella de la caché cambia con nombre, tamaño o mtime, y nunca abre los archivos (share lento)."""
    import os
    (tmp_path / "bdp_a.csv").write_text("ticker,valor\nA,1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.csv").write_text("x\n")
    h1, n = DM.hash_carpeta(tmp_path)
    assert n == 2 and len(h1) == 16
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe leer contenido")))
    assert DM.hash_carpeta(tmp_path) == (h1, 2)
    monkeypatch.undo()
    os.utime(tmp_path / "bdp_a.csv", ns=(1, 1))
    assert DM.hash_carpeta(tmp_path)[0] != h1


def test_paquete_desactiva_el_handler_fortran_de_ctrl_c():
    import os
    import reporteria  # noqa: F401
    assert os.environ.get("FOR_DISABLE_CONSOLE_CTRL_HANDLER") == "1"


def test_correr_deja_borrador_publicar_reporte_y_anterior_desde_datamart(fixtures, tmp_path):
    """e2e mini: la corrida escribe un borrador completo; publicar lo lleva al datamart; reporte reproduce el Excel hoja por hoja;
    una versión en el datamart para el cierre anterior reemplaza al Excel como cartera previa."""
    import dataclasses
    from reporteria.adaptadores.bbg import FixtureBloomberg
    from reporteria.adaptadores.fx_sql import FixtureFx
    from reporteria.config import Rutas
    from reporteria.pipeline import Opciones, correr
    from reporteria.salida import escribir_excel

    r = Rutas.para_pruebas("20260731", fixtures, tmp_path)
    res = correr(r, Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fixtures, "20260731")))
    b = res.borrador
    assert b is not None and b.parent == r.borradores and (b / "corrida.json").exists() and (b / "posiciones.parquet").exists()
    corrida = DM.leer_corrida(b)
    assert corrida["estado"] == "BORRADOR" and corrida["hojas"] == list(res.hojas) and corrida["hashes"]["codigo"] and corrida["hashes"]["cubo"]
    assert corrida["anterior_version"] is None and corrida["completitud"] == "COMPLETA"
    hashes = json.loads((b / "insumos" / "hashes.json").read_text())
    assert {"REGLAS", "CUBO", "CACHE", "DIMENSIONALES"} <= set(hashes) and (b / "insumos" / "REGLAS.xlsx").exists()
    assert (b / "insumos" / "facturas_al_cierre.parquet").exists() and hashes["facturas_al_cierre"]["filas"] > 0
    pos_b = DM.leer_posiciones(b)
    assert len(pos_b) == len(res.posiciones) and "Emision_nacional" in pos_b.columns        # todas las columnas, no solo las del Excel

    v = DMV.publicar(r.datamart, "20260731", b)
    assert v.numero == 1 and (r.datamart / "cierres" / "cierre=20260731" / "version=001" / "insumos" / "hashes.json").exists()
    regen = tmp_path / "regen.xlsx"
    escribir_excel(v.hojas(), regen)
    orig, nuevo = pd.read_excel(res.excel, sheet_name=None), pd.read_excel(regen, sheet_name=None)
    assert list(orig) == list(nuevo)

    def norm(x):
        if isinstance(x, (bool, np.bool_)):
            return str(bool(x))
        try:
            f = float(x)
            return "" if np.isnan(f) else f"{f:.10g}"
        except (TypeError, ValueError):
            t = str(x).strip()
            return "" if t in ("nan", "None", "<NA>", "NaT") else t
    for hoja in orig:
        a, c = orig[hoja], nuevo[hoja]
        assert list(a.columns) == list(c.columns) and len(a) == len(c), hoja
        for col in a.columns:
            assert a[col].map(norm).tolist() == c[col].map(norm).tolist(), (hoja, col)

    # versión publicada del cierre anterior: la segunda corrida la usa en vez del Excel
    hojas, pos, c = DM.leer_version(v.ruta)
    DM.escribir_version(tmp_path / "ant", hojas, {**c, "fecha": "20260630"}, pos)
    DMV.publicar(r.datamart, "20260630", tmp_path / "ant")
    r2 = Rutas.para_pruebas("20260731", fixtures, tmp_path)                      # fecha_ant se detecta desde el datamart
    assert r2.fecha_ant == "20260630"
    res2 = correr(r2, Opciones(sin_bbg=True, sin_sql=True, sin_facts=True, bbg=FixtureBloomberg(r2.cache, "20260731"), fx=FixtureFx(fixtures, "20260731")))
    assert res2.resumen["anterior_version"] == {"origen": "DATAMART", "cierre": "20260630", "version": 1, "estado": "PUBLICADA", "ts": res2.resumen["anterior_version"]["ts"]}
    assert (res2.posiciones["Hedge_Origen"] == "MES_ANTERIOR").sum() >= 40
    assert DM.leer_corrida(res2.borrador)["anterior_version"]["origen"] == "DATAMART"
    assert len(DMV.borradores(r.borradores, "20260731")) == 2
