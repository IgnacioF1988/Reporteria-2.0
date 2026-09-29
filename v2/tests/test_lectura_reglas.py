import pandas as pd
import pytest

from reporteria.lectura.reglas import HOJAS, leer_reglas


def _escribir(path, **cambios):
    base = {h: pd.read_excel(pytest.FIXTURES / "REGLAS.xlsx", sheet_name=h) for h in HOJAS}
    base.update(cambios)
    with pd.ExcelWriter(path) as w:
        for h, df in base.items():
            df.to_excel(w, sheet_name=h, index=False)
    return path


@pytest.fixture(autouse=True)
def _fixtures_path(fixtures):
    pytest.FIXTURES = fixtures


def test_lee_reglas_validas(fixtures):
    r = leer_reglas(fixtures / "REGLAS.xlsx")
    assert len(r.clasificacion) == 12
    assert r.clasificacion["ID_Fund"].dtype.kind in "if"          # entero con nulos → float
    assert r.parametros["yield_max_proveedor"] == 1.0
    assert r.defaulteados.iloc[0]["PK2"] == "928-1"


def test_falta_hoja(tmp_path, fixtures):
    base = {h: pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name=h) for h in HOJAS if h != "alertas"}
    p = tmp_path / "r.xlsx"
    with pd.ExcelWriter(p) as w:
        for h, df in base.items():
            df.to_excel(w, sheet_name=h, index=False)
    with pytest.raises(ValueError, match="alertas"):
        leer_reglas(p)


@pytest.mark.parametrize("col,valor,msg", [
    ("Criterio", "Color", "Criterio"),
    ("Tratamiento", "MAGIA", "Tratamiento"),
    ("Yield", 5.0, "porcentaje"),
    ("ID_Fund", 999, "ID_Fund"),
    ("Valor", "[", "regex"),
])
def test_clasificacion_invalida(tmp_path, fixtures, col, valor, msg):
    clas = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name="clasificacion")
    fila = 8 if col == "Valor" else 1          # fila 8 es la regla Nombre_Regex
    clas.loc[fila, col] = valor
    with pytest.raises(ValueError, match=msg):
        leer_reglas(_escribir(tmp_path / "r.xlsx", clasificacion=clas))


def test_defaulteado_estado_invalido(tmp_path, fixtures):
    d = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name="defaulteados")
    d.loc[0, "Estado"] = "QUIEBRA"
    with pytest.raises(ValueError, match="Estado"):
        leer_reglas(_escribir(tmp_path / "r.xlsx", defaulteados=d))


def test_ids_duplicados(tmp_path, fixtures):
    clas = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name="clasificacion")
    clas.loc[1, "ID"] = clas.loc[0, "ID"]
    with pytest.raises(ValueError, match="ID"):
        leer_reglas(_escribir(tmp_path / "r.xlsx", clasificacion=clas))
