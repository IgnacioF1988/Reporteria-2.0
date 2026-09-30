import pandas as pd
import pytest

from reporteria.lectura.reglas import HOJAS, leer_reglas

FONDOS = set(range(1, 80))


def _escribir(path, fixtures, **cambios):
    base = {h: pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name=h) for h in HOJAS}
    base.update(cambios)
    with pd.ExcelWriter(path) as w:
        for h, df in base.items():
            df.to_excel(w, sheet_name=h, index=False)
    return path


def test_lee_reglas_validas(fixtures):
    r = leer_reglas(fixtures / "REGLAS.xlsx", FONDOS)
    assert len(r.buckets) == 15 and set(r.buckets["Tratamiento"]) <= {"CASCADA", "CAJA", "CERO", "EXCLUIR", "FACTURA"}
    assert len(r.clasificacion) == 7 and r.clasificacion["ID_Fund"].eq(20).sum() == 6
    assert len(r.cajas) == 68 and r.cajas["Spread_Anual"].abs().max() < 1
    assert r.parametros["yield_type_default"] == 15
    assert r.fondos.set_index("ID_Fund").loc[17, "Politica_Hedge"] == "POR_PAIS"


def test_falta_hoja(tmp_path, fixtures):
    base = {h: pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name=h) for h in HOJAS if h != "cajas"}
    p = tmp_path / "r.xlsx"
    with pd.ExcelWriter(p) as w:
        for h, df in base.items():
            df.to_excel(w, sheet_name=h, index=False)
    with pytest.raises(ValueError, match="cajas"):
        leer_reglas(p, FONDOS)


def test_bucket_de_clasificacion_debe_existir_en_buckets(tmp_path, fixtures):
    clas = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name="clasificacion")
    clas.loc[0, "Bucket"] = "Cosa Rara"
    with pytest.raises(ValueError, match="Bucket"):
        leer_reglas(_escribir(tmp_path / "r.xlsx", fixtures, clasificacion=clas), FONDOS)


@pytest.mark.parametrize("hoja,col,valor,msg", [
    ("buckets", "Tratamiento", "MAGIA", "Tratamiento"),
    ("clasificacion", "Criterio", "Color", "Criterio"),
    ("clasificacion", "ID_Fund", 999, "ID_Fund"),
    ("cajas", "Spread_Anual", 4.29, "decimal"),
    ("defaulteados", "Estado", "QUIEBRA", "Estado"),
    ("overrides_atributo", "Field", "Color", "Field"),
])
def test_valores_invalidos(tmp_path, fixtures, hoja, col, valor, msg):
    df = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name=hoja)
    if df.empty:
        df = pd.DataFrame([{c: None for c in df.columns}])
        df.loc[0, "ID_Instrumento"] = 1
        if hoja == "overrides_atributo":
            df.loc[0, "Value"] = "x"
    df.loc[0, col] = valor
    with pytest.raises(ValueError, match=msg):
        leer_reglas(_escribir(tmp_path / "r.xlsx", fixtures, **{hoja: df}), FONDOS)


def test_regex_invalida(tmp_path, fixtures):
    clas = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name="clasificacion")
    clas.loc[0, ["Criterio", "Valor"]] = ["Nombre_Regex", "["]
    with pytest.raises(ValueError, match="regex"):
        leer_reglas(_escribir(tmp_path / "r.xlsx", fixtures, clasificacion=clas), FONDOS)


@pytest.mark.parametrize("fila,msg", [
    (("Z", "X", "Yield", "~", 0, "ALTA", None, "SI", "NO", "POSICION", ""), "Operador"),
    (("Z", "X", "Yield", ">", 0, "URGENTE", None, "SI", "NO", "POSICION", ""), "Severidad"),
    (("Z", "X", "Yield", ">", 0, "ALTA", None, "TALVEZ", "NO", "POSICION", ""), "Activa"),
    (("Z", "X", "Yield", ">", 0, "ALTA", None, "SI", "NO", "PLANETA", ""), "Ambito"),
    (("Z", "X", "Yield", ">", "", "ALTA", None, "SI", "NO", "POSICION", ""), "sin Umbral"),
    (("Z", "X", "Yield", ">", 0, "ALTA", 999, "SI", "NO", "POSICION", ""), "ID_Fund"),
])
def test_alertas_invalidas(tmp_path, fixtures, fila, msg):
    import pandas as pd
    from reporteria.lectura.reglas import leer_reglas
    hojas = pd.read_excel(fixtures / "REGLAS.xlsx", sheet_name=None)
    hojas["alertas"] = pd.DataFrame([fila], columns=hojas["alertas"].columns)
    p = tmp_path / "REGLAS.xlsx"
    with pd.ExcelWriter(p) as w:
        for n, d in hojas.items():
            d.to_excel(w, sheet_name=n, index=False)
    with pytest.raises(ValueError, match=msg):
        leer_reglas(p, None if fila[6] is None else set(range(1, 200)))


def test_alertas_alias_de_igual_y_ajustes_sin_campo(fixtures):
    from reporteria.lectura.reglas import leer_reglas
    a = leer_reglas(fixtures / "REGLAS.xlsx").alertas
    assert (a.loc[a["ID"] == "A23", "Operador"] == "==").all()
    assert (a.loc[a["Campo"] == "", "Nombre"].tolist() == ["PROVEEDOR_INVALIDO", "YIELD_TYPE_DEFAULT"])
    assert a["Activa"].dtype == bool and a.loc[a["ID"] == "E02", "Activa"].eq(False).all()
