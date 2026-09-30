import numpy as np
import pandas as pd
import pytest

from reporteria.conversion import convertir
from reporteria.finanzas import breakeven, drop, reexpresar_duracion, sumar_indice


def test_breakeven_baara_b_reexpresa_la_duration():
    # BAARA-B (MDCH, RA): yield real 3.053 %, mod dur 4.305099; curvas al plazo 1572 días: real 2.163191 %, nominal 5.437086 %
    y, d, aj = breakeven(0.030530, 4.305099, 0.02163191, 0.05437086)
    assert abs(y - 0.063554) < 1e-6 and abs(aj - 0.032046) < 1e-6
    assert abs(d - 4.1714) < 5e-4 and d < 4.305099            # el legacy dejaba 4.305 (Mac/(1+y) con la yield equivocada)
    assert abs(reexpresar_duracion(4.305099, 0.030530, y) - d) < 1e-12


def test_drop_aes_2034_clp():
    # AES 6.25 11/14/2034 REGS hedgeado a CLP: yield USD 5.790331 %, local 5.097036 %, basis −44.796408 bps, USD 4.153185 %
    y, dr = drop(0.05790331, 0.05097036, 0.04153185, -0.0044796408)
    assert abs(dr - 0.004959) < 1e-6 and abs(y - 0.06286218) < 1e-7


def test_sumar_indice_compone_y_reexpresa():
    y, d = sumar_indice(0.02, 3.0, 0.10)
    assert abs(y - 0.122) < 1e-12 and abs(d - 3.0 * 1.02 / 1.122) < 1e-12


def _pos(**kw):
    base = dict(Pos_ID="20|1-38|Asset", PK2="1-38", ID_Fund=20, Estado="RESUELTO", Fuente="RA", Yield=0.030530, Duration=4.305099,
                Yield_Moneda="CLF", Risk_Currency="CLF", Hedge_Currency="", Indice="UF", Yield_XCCY=np.nan, Name_Instrumento="BAARA-B",
                TotalMVal=1.0, ISIN="")
    return pd.DataFrame([{**base, **kw}])


CURVA_REAL = {"BTUCHILE": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [0.02163191, 0.02163191]})}
CURVA_NOM = {"LCCHILE": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [0.05437086, 0.05437086]})}
CLP = {"local": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [0.05097036] * 2}), "basis": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [-0.0044796408] * 2}),
       "usd": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [0.04153185] * 2})}


def test_convertir_breakeven_conserva_el_papel_y_alerta_sin_curva():
    out, det, al = convertir(_pos(), CURVA_REAL, CURVA_NOM, {}, {})
    p = out.iloc[0]
    assert abs(p["Yield"] - 0.063554) < 1e-6 and abs(p["Yield_Papel"] - 0.030530) < 1e-12 and abs(p["Duration"] - 4.1714) < 5e-4
    assert p["Yield_Moneda"] == "CLP" and p["Conversion"] == "BREAKEVEN" and det.iloc[0]["Resultado"] == "OK" and al.empty
    out, det, al = convertir(_pos(), {}, CURVA_NOM, {}, {})
    assert out.iloc[0]["Conversion"] == "" and out.iloc[0]["Yield"] == 0.030530 and (al["Nombre"] == "SIN_BREAKEVEN").sum() == 1
    assert det.iloc[0]["Resultado"] == "SIN_CURVA:BTUCHILE"


def test_convertir_no_toca_cero_def_ni_faltantes():
    for kw in (dict(Fuente="CERO", Yield=0.0, Duration=0.0), dict(Fuente="REGLA_DEF", Yield=0.0, Duration=0.5), dict(Estado="FALTANTE", Yield=np.nan, Duration=np.nan)):
        out, det, _ = convertir(_pos(**kw), CURVA_REAL, CURVA_NOM, {}, {})
        assert out.iloc[0]["Conversion"] == "" and det.empty


def test_convertir_hedge_politica_xccy_y_drop():
    hed = dict(Risk_Currency="USD", Yield_Moneda="USD", Indice="NOMINAL", Hedge_Currency="CLP", Yield=0.05790331, Duration=4.074062, Yield_XCCY=0.06546457)
    out, det, al = convertir(_pos(**hed), {}, {}, {"CLP": CLP}, {"politica_hedge": "XCCY_SI_EXISTE", "xccy_drop_max_bps": 50})
    p = out.iloc[0]
    assert p["Conversion"] == "XCCY" and abs(p["Yield"] - 0.06546457) < 1e-9 and abs(p["Yield_Drop"] - 0.06286218) < 1e-7
    assert abs(p["Dif_XCCY_Drop_bps"] - 26.02) < 0.05 and p["Duration"] == 4.074062 and p["Yield_Moneda"] == "CLP"
    assert not (al["Nombre"] == "XCCY_VS_DROP").any()
    out, _, _ = convertir(_pos(**hed), {}, {}, {"CLP": CLP}, {"politica_hedge": "DROP_SIEMPRE"})
    assert out.iloc[0]["Conversion"] == "DROP" and abs(out.iloc[0]["Yield"] - 0.06286218) < 1e-7
    out, _, al = convertir(_pos(**{**hed, "Yield_XCCY": np.nan}), {}, {}, {"CLP": CLP}, {})
    assert out.iloc[0]["Conversion"] == "DROP"
    out, _, al = convertir(_pos(**{**hed, "Yield_XCCY": np.nan}), {}, {}, {}, {})
    assert out.iloc[0]["Conversion"] == "" and out.iloc[0]["Yield"] == 0.05790331 and (al["Nombre"] == "SIN_CONVERSION_HEDGE").sum() == 1
    out, _, al = convertir(_pos(**{**hed, "Yield_XCCY": 0.09}), {}, {}, {"CLP": CLP}, {"xccy_drop_max_bps": 50})
    assert (al["Nombre"] == "XCCY_VS_DROP").sum() == 1
    with pytest.raises(ValueError):
        convertir(_pos(**hed), {}, {}, {}, {"politica_hedge": "OTRA"})


def test_hedge_igual_a_la_moneda_del_papel_no_convierte():
    out, det, al = convertir(_pos(Risk_Currency="CLP", Yield_Moneda="CLP", Indice="NOMINAL", Hedge_Currency="CLP"), {}, {}, {"CLP": CLP}, {})
    assert out.iloc[0]["Conversion"] == "" and det.empty and al.empty


def test_flotante_propio_suma_indice_y_de_proveedor_no():
    cdi = {"CDIBRAZIL": pd.DataFrame({"dias": [365.25, 3652.5], "tasa": [0.14, 0.14]})}
    out, det, al = convertir(_pos(Indice="CDI", Risk_Currency="BRL", Yield_Moneda="BRL", Fuente="JSONL", Yield=0.02, Duration=3.0), cdi, {}, {}, {})
    assert out.iloc[0]["Conversion"] == "SUMA_INDICE" and abs(out.iloc[0]["Yield"] - 0.1628) < 1e-12
    out, det, al = convertir(_pos(Indice="CDI", Risk_Currency="BRL", Yield_Moneda="BRL", Fuente="BBG", Yield=0.15, Duration=3.0), cdi, {}, {}, {})
    assert out.iloc[0]["Conversion"] == "PROVEEDOR_NOMINAL" and out.iloc[0]["Yield"] == 0.15 and (al["Nombre"] == "FLOTANTE_PROVEEDOR").sum() == 1


def test_xccy_fuera_de_rango_se_ignora_y_usa_drop():
    # PCRD (MLDL): papel 24,6 % USD; Bloomberg devolvió un XCCY de 426 % → no se usa; queda el drop propio y alerta MEDIA
    hed = dict(Risk_Currency="USD", Yield_Moneda="USD", Indice="NOMINAL", Hedge_Currency="CLP", Yield=0.246309, Duration=0.283118, Yield_XCCY=4.265199)
    out, det, al = convertir(_pos(**hed), {}, {}, {"CLP": CLP}, {"yield_max_proveedor": 1.0})
    assert out["Conversion"].iloc[0] == "DROP" and out["Yield"].iloc[0] < 0.3 and abs(out["Yield_XCCY"].iloc[0] - 4.265199) < 1e-9
    assert "XCCY_FUERA_RANGO" in set(al["Nombre"]) and "XCCY_VS_DROP" not in set(al["Nombre"])
    assert al.set_index("Nombre").loc["XCCY_FUERA_RANGO", "Severidad"] == "MEDIA"
    out, _, al = convertir(_pos(**hed), {}, {}, {}, {})                    # sin curvas de drop: queda la yield del papel
    assert out["Conversion"].iloc[0] == "" and abs(out["Yield"].iloc[0] - 0.246309) < 1e-12 and "SIN_CONVERSION_HEDGE" in set(al["Nombre"])


def test_indice_rate_sin_curva_queda_nominal_del_proveedor():
    # IMED LOAN CHIBPROM + 4,60 % (EXCEPCIONES, MRCLP): la TD del PM ya trae el cupón all-in → nominal CLP, sin breakeven
    p = _pos(Fuente="EXCEPCIONES", Indice="CHIBPROM", Risk_Currency="CLP", Yield_Moneda="CLP", Yield=0.104268, Duration=3.2)
    out, det, al = convertir(p, {}, {}, {}, {})
    assert out["Conversion"].iloc[0] == "PROVEEDOR_NOMINAL" and abs(out["Yield"].iloc[0] - 0.104268) < 1e-12
    assert det["Resultado"].iloc[0] == "PROVEEDOR_NOMINAL"
    assert set(al["Nombre"]) == {"INDICE_SIN_CURVA"} and al["Severidad"].iloc[0] == "INFO"
    p = _pos(Fuente="EXCEPCIONES", Indice="CPI", Risk_Currency="USD", Yield_Moneda="USD", Yield=0.184322, Duration=3.2)
    out, det, al = convertir(p, {}, {}, {}, {})                            # CPI (real en USD) sigue sin curvas → ALTA
    assert "SIN_BREAKEVEN" in set(al["Nombre"]) and det["Resultado"].iloc[0] == "SIN_CURVAS:CPI"
