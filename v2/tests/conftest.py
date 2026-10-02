from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "mini"
CORPORATIVO = Path(__file__).parent / "fixtures" / "corporativo"
FECHA = "20260731"


def construir_dim(fixtures: Path) -> Path:
    """dimensionales.duckdb de la carpeta de fixtures (no versionado): se migra desde sus BD_* si falta o está viejo."""
    from reporteria.adaptadores import dim as DIM
    from reporteria.legado import migrar_dimensionales
    destino = fixtures / "dimensionales.duckdb"
    codigo = Path(__file__).parents[1] / "reporteria"
    fuentes = list(fixtures.glob("BD_*.xlsx")) + [codigo / "dim.py", codigo / "legado.py", codigo / "adaptadores" / "dim.py"]
    if destino.exists() and destino.stat().st_mtime > max(f.stat().st_mtime for f in fuentes):
        return destino
    dims, _ = migrar_dimensionales([fixtures])
    return DIM.escribir(destino, dims, csv_dir=fixtures / "csv")


@pytest.fixture(scope="session", autouse=True)
def dim_fixtures():
    """Toda carpeta de fixtures con BD_BalanceSheet.xlsx recibe su dimensionales.duckdb (mini, corporativo, casos_legacy…)."""
    return {d.name: construir_dim(d) for d in FIXTURES.parent.iterdir() if (d / "BD_BalanceSheet.xlsx").exists()}


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def rutas(tmp_path):
    from reporteria.config import Rutas
    return Rutas.para_pruebas(FECHA, FIXTURES, tmp_path)


@pytest.fixture
def reglas(fixtures):
    from reporteria.lectura.reglas import leer_reglas
    return leer_reglas(fixtures / "REGLAS.xlsx")


@pytest.fixture
def settle():
    return pd.Timestamp("2026-07-31")


@pytest.fixture
def bbg(fixtures):
    from reporteria.adaptadores.bbg import FixtureBloomberg
    return FixtureBloomberg(fixtures / "bbg_cache", FECHA)


@pytest.fixture
def fx(fixtures):
    from reporteria.adaptadores.fx_sql import FixtureFx
    return FixtureFx(fixtures, FECHA)


@pytest.fixture
def xbbg_roto(tmp_path, monkeypatch):
    """Un paquete `xbbg` instalado pero que no carga (como xbbg ≥ 1.0 sin blpapi en Python 3.14: DLL de _core)."""
    import importlib
    import sys
    pkg = tmp_path / "xbbg_roto_site" / "xbbg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('raise ImportError("DLL load failed while importing _core: No se puede encontrar el módulo")\n',
                                     encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg.parent))
    monkeypatch.delitem(sys.modules, "xbbg", raising=False)
    importlib.invalidate_caches()
    return pkg
