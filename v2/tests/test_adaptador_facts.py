import pandas as pd
import pytest

from reporteria.adaptadores.facts import CacheFacts, FactsSql, FixtureFacts


class Stub:
    def __init__(self):
        self.llamadas = 0

    def tablas(self, fecha):
        self.llamadas += 1
        return {"facturas": pd.DataFrame([dict(documento_operacion_id=1, nemotecnico="FAC1", fecha_vencimiento="2026-09-30")]),
                "prorrogas": pd.DataFrame(columns=["documento_operacion_id", "fecha_inicio"]), "cambios": pd.DataFrame(columns=["documento_operacion_id", "fecha"])}


def test_fixture_vacio_y_cache_escribe_una_sola_vez(tmp_path):
    assert FixtureFacts(tmp_path, "20260731").tablas("20260731") == {}
    inner = Stub()
    c = CacheFacts(inner, tmp_path, "20260731")
    t = c.tablas("20260731")
    assert set(t) == {"facturas", "prorrogas", "cambios"} and len(t["facturas"]) == 1 and inner.llamadas == 1
    assert {p.name for p in tmp_path.glob("facts_*_20260731.csv")} == {"facts_facturas_20260731.csv", "facts_prorrogas_20260731.csv", "facts_cambios_20260731.csv"}
    t2 = c.tablas("20260731")
    assert inner.llamadas == 1 and len(t2["facturas"]) == 1 and t2["prorrogas"].empty and list(t2["cambios"].columns) == ["documento_operacion_id", "fecha"]


def test_sql_sin_clave_falla_claro_sin_abrir_tunel(monkeypatch):
    monkeypatch.delenv("MONEDA_BI_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="MONEDA_BI_PASSWORD"):
        FactsSql().tablas("20260731")
    monkeypatch.setenv("MONEDA_BI_PASSWORD", "x")
    monkeypatch.setenv("MONEDA_BI_SSH_KEY", "/no/existe/id_ed25519")
    with pytest.raises(RuntimeError, match="MONEDA_BI_SSH_KEY"):
        FactsSql().tablas("20260731")
