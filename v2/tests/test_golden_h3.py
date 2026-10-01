"""H3: BBG desde caché sembrada con el legacy, CSHF y JSONL reproducen los valores del legacy de julio."""
from pathlib import Path

import pandas as pd
import pytest

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.config import Rutas
from reporteria.config import MAX_DIAS_ATRAS_PARIDADES
from reporteria.escala import EscalaCfg
from reporteria.fuentes.cshf import candidatos_cshf
from reporteria.fx import armar_fx
from reporteria.lectura.mercado import leer_paridades
from reporteria.pipeline import Opciones, correr

CASOS = Path(__file__).parent / "fixtures" / "casos_legacy"


def _correr(base, tmp):
    r = Rutas.para_pruebas("20260731", base, tmp)
    return correr(r, Opciones(sin_bbg=True, sin_sql=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(base, "20260731")))


def _cshf_iguales(m: pd.DataFrame) -> None:
    malos = m[((m["Yield"] - m["CSHF_Yield_efec"]).abs() > 1e-4) | ((m["Duration"] - m["CSHF_ModDur"]).abs() > 1e-3)]
    assert malos.empty, malos[["Pos_ID", "CSHF_Yield_efec", "Yield", "CSHF_ModDur", "Duration"]].to_string()


def test_bbg_y_cshf_sobre_la_muestra(fixtures, tmp_path):
    res = _correr(fixtures, tmp_path)
    pos = res.posiciones
    # 155 pendientes tras EXCEPCIONES/JPM/RA; la caché legacy cubre 93 (87 válidas + 6 descartadas por sanidad)
    assert (pos["Fuente"] == "BBG").sum() >= 85
    # XCCY solo para posiciones hedgeadas del fondo (el legacy lo mapeaba por ISIN y contagiaba fondos sin hedge: 62 vs 12 reales)
    hedged = pos["Hedge_Currency"].astype(str).str.strip().ne("")
    assert pos["Yield_XCCY"].notna().sum() >= 10
    assert not (pos["Yield_XCCY"].notna() & ~hedged).any()
    assert (pos["CalcType_exportable"][pos["Fuente"] == "BBG"] == "YTW").all()
    rf = pos[(pos["Bucket"] == "Fixed Income") & pos["Estado"].isin(["FALTANTE", "PENDIENTE_TERMINAL"])]
    assert len(rf) < 70, len(rf)
    # lo que sigue faltante con ISIN en caché es exactamente lo que la sanidad descartó (bonos vencidos / yields absurdas)
    cache = pd.read_csv(Path(fixtures) / "bbg_cache" / "bdp_YAS_BOND_YLD_20260731_settle_dt-20260731.csv")
    en_cache = cache.loc[pd.to_numeric(cache["valor"], errors="coerce").notna(), "ticker"]          # con valor (las filas vacías son "sin dato")
    en_cache = set(en_cache.str.replace("@BGN", "", regex=False).str.replace(" Corp", "", regex=False))
    falt_cache = rf[rf["ISIN"].isin(en_cache)]
    descartados = set(res.candidatos[(res.candidatos["Fuente"] == "BBG") & ~res.candidatos["Valido"]]["Pos_ID"])
    assert set(falt_cache["Pos_ID"]) <= descartados, falt_cache[["Pos_ID", "ISIN", "Motivo"]].to_string()
    # CSHF en la cascada: solo llega a lo que EXCEPCIONES/JPM/RA/BBG no cubrieron (2 de las 6 posiciones legacy en la muestra)
    gold = pd.read_csv(fixtures / "golden_cshf_20260731.csv")
    gold = gold[gold["Escalar_flag"] == "OK"].copy()
    gold["Pos_ID"] = gold["ID_Fund"].astype(str) + "|" + gold["PK2"].astype(str) + "|Asset"
    cs = res.candidatos[(res.candidatos["Fuente"] == "CSHF") & res.candidatos["Valido"]]
    m = gold.merge(cs, on="Pos_ID")
    assert len(m) >= 2, len(m)
    _cshf_iguales(m)
    # el motor CSHF reproduce también las que en la cascada ganó EXCEPCIONES: se le fuerzan como pendientes
    en_muestra = set(gold["Pos_ID"]) & set(pos["Pos_ID"])
    assert len(en_muestra) >= 6, en_muestra
    r = Rutas.para_pruebas("20260731", fixtures, tmp_path)
    fx_bee, fx_par = armar_fx(FixtureFx(fixtures, "20260731").ultimos("20260731"), leer_paridades(r.paridades, r.settle, MAX_DIAS_ATRAS_PARIDADES))
    c2, tds, _ = candidatos_cshf(pos, en_muestra, FixtureBloomberg(r.cache, "20260731"), fx_bee, fx_par, r.settle, EscalaCfg())
    m2 = gold.merge(c2[c2["Valido"].astype(bool)], on="Pos_ID")
    assert len(m2) >= 5, (len(m2), c2[["Pos_ID", "Valido", "Motivo_Descarte", "Detalle"]].to_string())
    _cshf_iguales(m2)
    assert tds["Pos_ID"].nunique() == len(m2)
    espia = [t for campo, ts in res.bbg_pedidos for t in ts if campo == "YAS_BOND_YLD"]
    resueltos_antes = pos[pos["Fuente"].isin(["JPM", "RA", "EXCEPCIONES", "REGLA_DEF"]) & pos["ISIN"].ne("")]["ISIN"]
    assert not any(f"{i} Corp" in espia for i in resueltos_antes)                       # no gasta terminal en lo ya resuelto


def test_jsonl_reproduce_casos_legacy(tmp_path):
    res = _correr(CASOS, tmp_path)
    pos = res.posiciones
    gold = pd.read_csv(CASOS / "golden_jsonl_20260731.csv")
    gold = gold[gold["Escalar_flag"] == "OK"]
    js = res.candidatos[(res.candidatos["Fuente"] == "JSONL") & res.candidatos["Valido"]].copy()
    js["ID_Fund"] = js["Pos_ID"].map(pos.set_index("Pos_ID")["ID_Fund"])
    m = gold.merge(js, on=["ID_Fund", "PK2"])
    assert len(m) >= 7, (len(m), js[["PK2", "Yield", "Motivo_Descarte"]].to_string(), res.candidatos[res.candidatos["Fuente"] == "JSONL"][["PK2", "Motivo_Descarte", "Detalle"]].to_string())
    malos = m[((m["Yield"] - m["Yield_efectiva"]).abs() > 1e-4) | ((m["Duration"] - m["ModDur"]).abs() > 1e-3)]
    assert malos.empty, malos[["ID_Fund", "PK2", "Yield_efectiva", "Yield", "ModDur", "Duration"]].to_string()
    assert res.tds[res.tds["Fuente"] == "JSONL"]["Pos_ID"].nunique() >= 7
