"""H5: corrida completa sobre la muestra — alertas por regla, agregados consistentes, Excel completo y segunda corrida con cierre anterior."""
import dataclasses
import shutil

import numpy as np
import pandas as pd

from reporteria.adaptadores.bbg import FixtureBloomberg
from reporteria.adaptadores.fx_sql import FixtureFx
from reporteria.agregados import verificar
from reporteria.config import Rutas
from reporteria.pipeline import Opciones, correr

HOJAS = ["resumen", "agregados", "alertas_resumen", "alertas", "faltantes", "plantilla_overrides", "plantilla_cajas", "cartera_final", "candidatos",
         "conversiones", "curvas_drop", "td_detalle", "reglas_aplicadas", "insumos"]


def _correr(r, fixtures):
    return correr(r, Opciones(sin_bbg=True, sin_sql=True, bbg=FixtureBloomberg(r.cache, "20260731"), fx=FixtureFx(fixtures, "20260731")))


def test_corrida_completa_y_segunda_con_cierre_anterior(fixtures, tmp_path):
    r = Rutas.para_pruebas("20260731", fixtures, tmp_path)
    res = _correr(r, fixtures)
    pos, agg, resu = res.posiciones, res.agregados, res.alertas_resumen
    assert res.resumen["segundos"] < 180
    # agregados: MV_PAT = MV_ACT − MV_PAS, pesos suman 1, todos los fondos del CUBO, todo entra (nada RESUELTO/FALTANTE fuera)
    assert verificar(agg).empty
    tot = agg[(agg["Dimension"] == "TOTAL")]
    assert set(tot["ID_Fund"]) == set(pos["ID_Fund"])
    act = tot[tot["Nivel"] == "ACTIVOS"].set_index("ID_Fund")
    mv_cubo = pos[pos["BalanceSheet"].eq("Asset") & pos["Estado"].ne("EXCLUIDO")].groupby("ID_Fund")["TotalMVal"].sum()
    assert np.allclose(act["MV"], mv_cubo.reindex(act.index), rtol=1e-9, atol=1e-3)
    assert 0.7 < act.loc[20, "Cobertura"] < 0.9 and act.loc[16, "Cobertura"] > 0.99 and act.loc[16, "AW"] > 0.05
    assert set(agg["Dimension"]) >= {"TOTAL", "Bucket", "Ficha_FI", "FX_Exposure", "Risk_Country", "Risk_Currency"}
    # alertas por regla: A01 dispara, A05–A07 inactivas sin cierre previo, A09 en el fondo 20, estructurales ajustadas
    est = resu.set_index(resu["ID"].astype(str) + "|" + resu["Nombre"])
    assert (resu.loc[resu["ID"].isin(["A05", "A06", "A07"]), "Estado"].str.startswith("INACTIVA")).all()
    assert resu.loc[resu["Nombre"] == "YIELD_ALTA", "N"].sum() >= 10 and resu.loc[resu["ID"] == "A16", "Estado"].eq("DESACTIVADA").all()
    al = res.alertas
    cob = al[al["Nombre"] == "COBERTURA_BAJA"]
    assert cob["ID_Fund"].tolist() == [20] and cob["Ambito"].tolist() == ["FONDO"]
    assert (al.loc[al["Nombre"] == "PROVEEDOR_INVALIDO", "Severidad"] == "MEDIA").all()          # E01 bajó la severidad
    assert not (al["Nombre"] == "YIELD_TYPE_DEFAULT").any()                                       # E02 la silenció
    assert set(al["Severidad"]) <= {"CRITICA", "ALTA", "MEDIA", "INFO"}
    # Excel completo y plantilla de overrides con los faltantes
    xl = pd.ExcelFile(res.excel)
    assert xl.sheet_names == HOJAS
    pl = xl.parse("plantilla_overrides")
    assert len(pl) == (pos["Estado"] == "FALTANTE").sum() and list(pl.columns[:5]) == ["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Yield", "Duration"]
    assert pl["_TotalMVal"].abs().is_monotonic_decreasing
    ins = xl.parse("insumos")
    assert (ins.loc[ins["Insumo"] == "CUBO", "Estado"] == "OK").all() and (ins["Estado"].isin(["OK", "OPCIONAL_AUSENTE"])).all()
    ra = xl.parse("reglas_aplicadas")
    assert ra.loc[ra["Hoja"] == "overrides_valor", "Posiciones"].sum() == 1 and (ra["Hoja"] == "cajas").sum() > 50
    assert json_ok(res.resumen)

    # segunda corrida con la primera como cierre anterior: temporales activas, hedge heredado, métricas idénticas detectadas
    ant_dir = tmp_path / "02_OUTPUTS" / "20260630"
    ant_dir.mkdir(parents=True)
    shutil.copy(res.excel, ant_dir / "REPORTE_20260630.xlsx")
    res2 = _correr(dataclasses.replace(r, fecha_ant="20260630"), fixtures)
    r2 = res2.alertas_resumen
    assert (r2.loc[r2["ID"].isin(["A05", "A06", "A07"]), "Estado"] == "ACTIVA").all()
    assert r2.loc[r2["ID"] == "A06", "N"].iloc[0] > 400 and r2.loc[r2["ID"].isin(["A05", "A07"]), "N"].sum() == 0
    assert (res2.posiciones["Hedge_Origen"] == "MES_ANTERIOR").sum() >= 40
    assert res2.resumen["fecha_ant"] == "20260630"
    same = res2.posiciones.set_index("Pos_ID")["Yield"].fillna(-1).eq(pos.set_index("Pos_ID")["Yield"].fillna(-1))
    assert same.all()


def json_ok(resumen):
    import json
    json.dumps(resumen, default=str)
    return "cobertura_mv_activos" in resumen and "aw_dw_patrimonio" in resumen and isinstance(resumen["alertas_inactivas"], list)
