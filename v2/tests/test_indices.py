import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.indices import asignar_indice, normalizar_indice


def _pos(*filas):
    cols = ["Pos_ID", "PK2", "ISIN", "Risk_Currency", "Risk_Country", "Indice", "Estado", "Fuente", "ISIN_Hermanos", "ID_Fund", "Name_Instrumento", "TotalMVal"]
    return pd.DataFrame([dict(zip(cols, f + (1, "x", 1.0))) for f in filas])


def test_normalizar_alias():
    assert normalizar_indice("CLCPI") == "UF" and normalizar_indice("uvr coster") == "UVR" and normalizar_indice("nan") == ""


def test_cascada_override_moneda_bbg_default(tmp_path):
    pd.DataFrame({"ticker": ["AR1 Corp", "BR1 Corp", "MX1 Corp"], "valor": ["Y", "N", "N"]}).to_csv(tmp_path / "bdp_INFLATION_LINKED_INDICATOR_20260731.csv", index=False)
    pd.DataFrame({"ticker": ["BR1 Corp", "MX1 Corp"], "valor": ["FLOATING", "FLOATING"]}).to_csv(tmp_path / "bdp_CPN_TYP_20260731.csv", index=False)
    pd.DataFrame({"ticker": ["BR1 Corp", "MX1 Corp"], "valor": ["BZDIOVRA", "RARO"]}).to_csv(tmp_path / "bdp_RESET_IDX_20260731.csv", index=False)
    bbg = FixtureBloomberg(tmp_path, "20260731")
    pos = _pos(("a", "1-38", "CL1", "CLF", "CL", "", "RESUELTO", "RA", ""),            # moneda que declara el índice
               ("b", "2-11", "AR1", "ARS", "AR", "", "RESUELTO", "BBG", ""),           # BBG: inflation linked → BONCER
               ("c", "3-29", "BR1", "BRL", "BR", "", "RESUELTO", "JSONL", ""),         # BBG: flotante CDI
               ("d", "4-123", "MX1", "MXN", "MX", "", "RESUELTO", "BBG", ""),          # RESET_IDX sin mapear → NUEVO + alerta
               ("e", "5-1", "US1", "USD", "US", "", "RESUELTO", "JPM", ""),            # USD: no se consulta
               ("f", "6-11", "AR1", "ARS", "AR", "CER", "RESUELTO", "BBG", ""),        # override (alias) manda
               ("g", "7-29", "BR9", "BRL", "BR", "", "FALTANTE", "", ""))              # faltante: no se consulta
    out, al = asignar_indice(pos, bbg, "20260731")
    assert out.set_index("Pos_ID")["Indice"].to_dict() == {"a": "UF", "b": "BONCER", "c": "CDI", "d": "NUEVO:RARO", "e": "NOMINAL", "f": "BONCER", "g": "NOMINAL"}
    assert out.set_index("Pos_ID")["Indice_Origen"].to_dict() == {"a": "MONEDA", "b": "BBG", "c": "BBG", "d": "BBG", "e": "DEFAULT", "f": "OVERRIDE", "g": "DEFAULT"}
    assert (al["Nombre"] == "INDICE_NUEVO").sum() == 1
    pedidos = {t for campo, ts in bbg.pedidos for t in ts if campo == "INFLATION_LINKED_INDICATOR"}
    assert "US1 Corp" not in pedidos and "BR9 Corp" not in pedidos and "AR1 Corp" in pedidos


def test_sin_bloomberg_queda_moneda_o_nominal():
    out, al = asignar_indice(_pos(("a", "1-38", "CL1", "CLF", "CL", "", "RESUELTO", "RA", ""), ("b", "2-11", "AR1", "ARS", "AR", "", "RESUELTO", "BBG", "")), None, "20260731")
    assert out["Indice"].tolist() == ["UF", "NOMINAL"] and al.empty
