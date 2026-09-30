import numpy as np
import pandas as pd
import pytest

from reporteria.alertas import ajustar_estructurales, columnas_derivadas, emitir, evaluar, unir_anterior

COLS = ["ID", "Nombre", "Campo", "Operador", "Umbral", "Severidad", "ID_Fund", "Activa", "Requiere_Anterior", "Ambito", "Descripcion"]


def _reglas(*filas):
    return pd.DataFrame(list(filas), columns=COLS)


def _pos():
    return pd.DataFrame([
        dict(Pos_ID="20|1-1|Asset", ID_Fund=20, PK2="1-1", Name_Instrumento="A", BalanceSheet="Asset", Estado="RESUELTO", Fuente="JPM", Yield=0.30, Duration=2.0,
             TotalMVal=100.0, AI=1.0, LocalPrice=101.0, Estado_DEF="", Investment_Type_Code=1, Tratamiento="CASCADA", Conversion="XCCY"),
        dict(Pos_ID="20|2-1|Asset", ID_Fund=20, PK2="2-1", Name_Instrumento="B", BalanceSheet="Asset", Estado="RESUELTO", Fuente="REGLA_DEF", Yield=0.0, Duration=0.5,
             TotalMVal=50.0, AI=3.0, LocalPrice=40.0, Estado_DEF="DEF", Investment_Type_Code=1, Tratamiento="CASCADA", Conversion=""),
        dict(Pos_ID="17|3-1|Asset", ID_Fund=17, PK2="3-1", Name_Instrumento="C", BalanceSheet="Asset", Estado="RESUELTO", Fuente="BBG", Yield=0.30, Duration=3.0,
             TotalMVal=200.0, AI=0.0, LocalPrice=99.0, Estado_DEF="", Investment_Type_Code=1, Tratamiento="CASCADA", Conversion=""),
        dict(Pos_ID="17|4-1|Asset", ID_Fund=17, PK2="4-1", Name_Instrumento="D", BalanceSheet="Asset", Estado="FALTANTE", Fuente="", Yield=np.nan, Duration=np.nan,
             TotalMVal=800.0, AI=0.0, LocalPrice=100.0, Estado_DEF="", Investment_Type_Code=1, Tratamiento="CASCADA", Conversion=""),
        dict(Pos_ID="17|5-1|Liability", ID_Fund=17, PK2="5-1", Name_Instrumento="E", BalanceSheet="Liability", Estado="RESUELTO", Fuente="CERO", Yield=0.0, Duration=0.0,
             TotalMVal=-30.0, AI=0.0, LocalPrice=1.0, Estado_DEF="", Investment_Type_Code=4, Tratamiento="CERO", Conversion=""),
    ])


PARAMS = {"yield_alta_default": 0.25, "yield_alta_local": 0.40, "cobertura_min_mv": 0.95, "delta_yield_max": 0.10}


def test_umbral_por_fondo_pisa_al_global_y_param():
    reglas = _reglas(("A01", "YIELD_ALTA", "Yield", ">=", "param:yield_alta_default", "CRITICA", None, True, False, "POSICION", "alta"),
                     ("A01", "YIELD_ALTA", "Yield", ">=", "param:yield_alta_local", "CRITICA", 17, True, False, "POSICION", "alta local"))
    al, res = evaluar(unir_anterior(_pos(), None), reglas, PARAMS, con_anterior=False)
    assert al["Pos_ID"].tolist() == ["20|1-1|Asset"]                 # 17|3-1 tiene 0.30 < 0.40 en MLDL
    assert al.iloc[0]["Valor"] == 0.30 and "Yield >= param:yield_alta_default" in al.iloc[0]["Detalle"]
    assert res["Estado"].tolist() == ["ACTIVA", "ACTIVA"] and dict(zip(res["ID_Fund"].fillna(-1).astype(int), res["N"])) == {17: 0, -1: 1}


def test_operadores_y_derivadas():
    pos = unir_anterior(_pos(), None)
    reglas = _reglas(("A03", "DEFAULT_CON_AI", "Default_Con_AI", "es_verdadero", "", "ALTA", None, True, False, "POSICION", ""),
                     ("A04", "BONO_SIN_AI", "Bono_Sin_AI", "es_verdadero", "", "MEDIA", None, True, False, "POSICION", ""),
                     ("A16", "FALTANTE_CON_MV", "Falta_Con_MV", "es_verdadero", "", "ALTA", None, True, False, "POSICION", ""),
                     ("X1", "CONVERTIDOS", "Conversion", "in", "xccy;drop", "INFO", None, True, False, "POSICION", ""),
                     ("X2", "SIN_FUENTE", "Fuente", "es_nulo", "", "INFO", None, True, False, "POSICION", ""),
                     ("X3", "DEF_TEXTO", "Estado_DEF", "==", "DEF", "INFO", None, True, False, "POSICION", ""),
                     ("X4", "MV_GRANDE", "MV_Abs", ">", 500, "INFO", None, True, False, "POSICION", ""))
    al, res = evaluar(pos, reglas, PARAMS, False)
    por = al.groupby("Nombre")["Pos_ID"].apply(list).to_dict()
    assert por == {"DEFAULT_CON_AI": ["20|2-1|Asset"], "BONO_SIN_AI": ["17|3-1|Asset"], "FALTANTE_CON_MV": ["17|4-1|Asset"],
                   "CONVERTIDOS": ["20|1-1|Asset"], "SIN_FUENTE": ["17|4-1|Asset"], "DEF_TEXTO": ["20|2-1|Asset"], "MV_GRANDE": ["17|4-1|Asset"]}


def test_ambito_fondo_una_alerta_por_fondo():
    reglas = _reglas(("A09", "COBERTURA_BAJA", "Cobertura_MV_Fondo", "<", "param:cobertura_min_mv", "ALTA", None, True, False, "FONDO", "cobertura"))
    al, res = evaluar(unir_anterior(_pos(), None), reglas, PARAMS, False)
    assert len(al) == 1 and al.iloc[0]["ID_Fund"] == 17 and al.iloc[0]["Pos_ID"] == "" and abs(al.iloc[0]["Valor"] - 0.2) < 1e-12   # 200 / 1000


def test_requiere_anterior_inactiva_sin_cierre_y_activa_con_cierre():
    reglas = _reglas(("A06", "METRICAS_SIN_ACTUALIZAR", "Metricas_Iguales_Ant", "es_verdadero", "", "MEDIA", None, True, True, "POSICION", ""),
                     ("A07", "DELTA_YIELD", "Delta_Yield", "abs>=", "param:delta_yield_max", "ALTA", None, True, True, "POSICION", ""),
                     ("A05", "PRECIO_Y_YIELD_SUBEN", "Precio_Sube_Yield_Sube", "es_verdadero", "", "CRITICA", None, True, True, "POSICION", ""))
    al, res = evaluar(unir_anterior(_pos(), None), reglas, PARAMS, False)
    assert al.empty and res["Estado"].str.startswith("INACTIVA").all()
    ant = pd.DataFrame([dict(Pos_ID="20|1-1|Asset", Yield=0.15, Duration=2.0, LocalPrice=100.0, AI=1.0),        # sube precio y yield, Δ 15 pp
                        dict(Pos_ID="17|3-1|Asset", Yield=0.30, Duration=3.0, LocalPrice=99.0, AI=0.0),          # idéntica
                        dict(Pos_ID="20|2-1|Asset", Yield=0.0, Duration=0.5, LocalPrice=40.0, AI=3.0)])          # DEF idéntica: no cuenta
    pos = unir_anterior(_pos(), ant)
    assert pos["_en_anterior"].sum() == 3 and pos.set_index("Pos_ID").loc["20|1-1|Asset", "Yield_ant"] == 0.15
    al, res = evaluar(pos, reglas, PARAMS, True)
    por = al.groupby("Nombre")["Pos_ID"].apply(list).to_dict()
    assert por == {"METRICAS_SIN_ACTUALIZAR": ["17|3-1|Asset"], "DELTA_YIELD": ["20|1-1|Asset"], "PRECIO_Y_YIELD_SUBEN": ["20|1-1|Asset"]}
    assert (res["Estado"] == "ACTIVA").all()


def test_desactivada_sin_campo_y_param_inexistente():
    al, res = evaluar(unir_anterior(_pos(), None), _reglas(("Z", "APAGADA", "Yield", ">", 0, "INFO", None, False, False, "POSICION", "")), PARAMS, False)
    assert al.empty and res.iloc[0]["Estado"] == "DESACTIVADA"
    al, res = evaluar(unir_anterior(_pos(), None), _reglas(("Z", "RARA", "NoExiste", ">", 0, "INFO", None, True, False, "POSICION", "")), PARAMS, False)
    assert res.iloc[0]["Estado"].startswith("SIN_CAMPO") and (al["Nombre"] == "ALERTA_SIN_CAMPO").all()
    with pytest.raises(ValueError, match="param:nada"):
        evaluar(unir_anterior(_pos(), None), _reglas(("Z", "X", "Yield", ">", "param:nada", "INFO", None, True, False, "POSICION", "")), PARAMS, False)


def test_ajustar_estructurales_silencia_o_reclasifica():
    al = pd.concat([emitir("PROVEEDOR_INVALIDO", "ALTA", _pos().iloc[:2]), emitir("HEDGE_NUEVO", "MEDIA", _pos().iloc[2:4])], ignore_index=True)
    reglas = _reglas(("E1", "PROVEEDOR_INVALIDO", "", "", "", "INFO", None, True, False, "POSICION", ""),
                     ("E2", "HEDGE_NUEVO", "", "", "", "MEDIA", 17, False, False, "POSICION", ""))
    out = ajustar_estructurales(al, reglas)
    assert (out.loc[out["Nombre"] == "PROVEEDOR_INVALIDO", "Severidad"] == "INFO").all() and not (out["Nombre"] == "HEDGE_NUEVO").any()


def test_columnas_derivadas_lista_cerrada():
    d = columnas_derivadas(unir_anterior(_pos(), None), con_anterior=False)
    from reporteria.alertas import DERIVADAS
    assert set(DERIVADAS) <= set(d.columns)
    assert d["Cobertura_MV_Fondo"].round(6).tolist() == [1.0, 1.0, 0.2, 0.2, 0.2]
