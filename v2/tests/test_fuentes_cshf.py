import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg, _ruta_bds
from reporteria.escala import EscalaCfg
from reporteria.fuentes.cshf import candidatos_cshf

S = pd.Timestamp("2026-07-31")


def test_td_bbg_escalada_por_face_derivado(tmp_path):
    p = _ruta_bds(tmp_path, "DES_CASH_FLOW", "US1 Corp"); p.parent.mkdir()
    pd.DataFrame({"Fecha": ["2026-01-31", "2027-07-31"], "Cupon": [40.0, 40.0], "Principal": [0.0, 1000.0], "Flujo": [40.0, 1040.0],
                  "Face": [1000.0, 1000.0]}).to_csv(p, index=False)
    bbg = FixtureBloomberg(tmp_path, "20260731")
    pos = pd.DataFrame([dict(Pos_ID="16|1-1|Asset", ID_Fund=16, PK2="1-1", ISIN="US1", ISIN_Hermanos="", Tratamiento="CASCADA",
                             Risk_Currency="USD", FundBaseCurrency="USD", LocalPrice=100.0, Qty=2_000_000.0, OriginalFace=2_000_000.0,
                             Factor=1.0, AI=0.0, MVBook=2_000_000.0, TotalMVal=2_000_000.0),
                        dict(Pos_ID="16|2-1|Asset", ID_Fund=16, PK2="2-1", ISIN="US2", ISIN_Hermanos="", Tratamiento="CASCADA",
                             Risk_Currency="USD", FundBaseCurrency="USD", LocalPrice=100.0, Qty=1.0, OriginalFace=1.0, Factor=1.0,
                             AI=0.0, MVBook=1.0, TotalMVal=1.0)])
    cand, tds, al = candidatos_cshf(pos, {"16|1-1|Asset", "16|2-1|Asset"}, bbg, {"USDUSD": 1.0}, {"USDUSD": 1.0}, S, EscalaCfg())
    c = cand.set_index("PK2")
    assert c.loc["1-1", "Valido"] and abs(c.loc["1-1", "Yield"] - 0.04) < 5e-4          # solo queda el flujo futuro 1.040 por 1.000
    assert abs(tds["Flujo"].iloc[0] - 2_080_000.0) < 1e-6 and len(tds) == 1             # el pasado (2026-01-31) no entra
    assert not c.loc["2-1", "Valido"] and c.loc["2-1", "Motivo_Descarte"] == "SIN_TD_BBG"
    assert any(t == ("bds:DES_CASH_FLOW", ("US2 Govt",)) for t in bbg.pedidos)          # cascada Corp → Govt
