"""Corrida completa sobre la muestra real del CUBO (1.000 posiciones, 14 fondos).

Los estados de lo que va a la cascada cambian cuando entran las fuentes de proveedor (H2/H3).
"""
import pandas as pd

from reporteria.pipeline import Opciones, correr


def _fila(pos, pk2, fondo, bs="Asset"):
    f = pos[(pos["PK2"] == pk2) & (pos["ID_Fund"] == fondo) & (pos["BalanceSheet"] == bs)]
    assert len(f) == 1, (pk2, fondo, bs, len(f))
    return f.iloc[0]


def test_corrida_muestra(rutas, bbg, fx):
    res = correr(rutas, Opciones(sin_bbg=True, sin_sql=True, bbg=bbg, fx=fx))
    pos, al = res.posiciones, res.alertas
    cubo = pd.read_excel(rutas.cubo)

    # Invariantes: nada se pierde, nada se duplica, los MV cierran por fondo y lado del balance
    assert len(pos) == cubo.drop_duplicates(["ID_Fund", "PK2", "BalanceSheet"]).shape[0]
    for (fid, bs), mv in cubo.groupby(["ID_Fund", "BalanceSheet"])["TotalMVal"].sum().items():
        assert abs(pos[(pos["ID_Fund"] == fid) & (pos["BalanceSheet"] == bs)]["TotalMVal"].sum() - mv) < 1e-4
    assert pos["Bucket"].ne("").all() and pos["Tratamiento"].ne("").all()
    assert set(pos["Estado"]) <= {"RESUELTO", "FALTANTE", "EXCLUIDO"}
    ok = pos[pos["Estado"] == "RESUELTO"]
    assert ok["Yield"].notna().all() and ok["Duration"].notna().all() and ok["Yield"].between(-0.5, 1).all()

    # Clasificación por BD_BalanceSheet + reglas por fondo
    assert set(pos["Bucket"]) - {"SIN_REGLA"} <= {"Fixed Income", "Equity", "Cash, Mutual Funds & Others", "Financial Debt",
                                                  "Payable", "Receivable"}
    assert _fila(pos, "223985-39", 20)["Bucket"] == "Cash, Mutual Funds & Others"        # DAP en MRCLP: regla del fondo
    assert _fila(pos, "223985-39", 20)["Bucket_Origen"].startswith("REGLA")
    assert _fila(pos, "176142-38", 20)["Bucket"] == "Equity"                             # FIP por PK2
    assert _fila(pos, "200810-39", 20, "Liability")["Bucket"] == "Payable"
    cero = pos[pos["Tratamiento"] == "CERO"]
    assert len(cero) > 300 and (cero["Estado"] == "RESUELTO").all() and (cero["Yield"] == 0).all() and (cero["Fuente"] == "CERO").all()
    assert _fila(pos, "176727-1", 16, "Liability")["Bucket"] == "Financial Debt"
    assert (pos["Ficha_FI"] != "").sum() > 600
    mrclp = pos[(pos["ID_Fund"] == 20) & (pos["Bucket"] != "SIN_REGLA")]
    assert (mrclp["FX_Exposure"] != "").all()                                              # MRCLP tiene tabla FX
    assert (pos.loc[pos["ID_Fund"] == 13, "FX_Exposure"] == "").all()                      # MDLAT no

    # Familias REGS/144A inferidas por nombre y hedge por política del fondo (MRCLP = A_CLP)
    fam = pos[pos["ISIN_Hermanos"] != ""]
    assert len(fam) > 0 and fam["Familia"].ne("").all()
    usd20 = pos[(pos["ID_Fund"] == 20) & (pos["Risk_Currency"] == "USD") & (pos["Bucket"] == "Fixed Income")]
    assert (usd20["Hedge_Currency"] == "CLP").all() and (usd20["Hedge_Origen"] == "REGLA").all()
    assert (pos.loc[pos["ID_Fund"] == 13, "Hedge_Currency"] == "").all()                    # MDLAT sin política
    assert (al["Nombre"] == "HEDGE_NUEVO").any()                                            # primera corrida: sin mes anterior

    # PK2 malformado del CUBO real ('46023', sin id_CURR): queda visible, no se pierde
    raro = pos[pos["PK2"] == "46023"].iloc[0]
    assert raro["Bucket"] == "SIN_REGLA" and (al["Nombre"] == "SIN_MAESTRO").any()

    # Defaults corporativos vigentes al settle → DEF en todos los fondos
    defs = pos[pos["Etapa"] == "REGLA_DEF"]
    assert len(defs) == 23 and (defs["Yield"] == 0).all() and (defs["Duration"] == 0.5).all()
    assert (defs["CalcType"] == "DEF").all()

    # Cajas: índice + spread + días (Template_Cajas migrado a REGLAS/cajas)
    con_indice = res.candidatos[(res.candidatos["Fuente"] == "CAJA") & res.candidatos["Detalle"].str.contains("Index")]
    assert len(con_indice) >= 1
    c = con_indice.iloc[0]
    assert "nivel=" in c["Detalle"] and abs(c["Duration"] - 1 / 365) < 1e-9
    sin_regla = al[al["Nombre"] == "CAJA_SIN_REGLA"]
    assert "87871-39" in set(sin_regla["PK2"])                                           # fondo mutuo CLP sin fila
    assert _fila(pos, "87871-39", 20)["Estado"] == "RESUELTO" and _fila(pos, "87871-39", 20)["Yield"] == 0

    # Facturas (RPT de Facts): 302 en MRCLP; una pagada queda fuera, una con monto distinto alerta
    fac = pos[pos["Tratamiento"] == "FACTURA"]
    assert len(fac) == 302
    assert (fac["Estado"] == "RESUELTO").sum() == 301 and (fac["Estado"] == "FALTANTE").sum() == 1
    f_ok = fac[fac["Estado"] == "RESUELTO"]
    assert f_ok["Yield"].between(0.096 - 1e-9, 0.12 + 1e-9).all()
    assert (al["Nombre"] == "FACTURA_MONTO_DISTINTO").sum() == 1
    assert (fac["Duration"].dropna().max() - 120 / 365) < 1e-9

    # Fuentes de archivo (H2): JPM por ISIN (y hermanos), RA por nemotécnico, EXCEPCIONES pisa a todos
    assert (pos["Fuente"] == "JPM").sum() >= 100 and (pos["Fuente"] == "RA").sum() >= 4
    ltmci = _fila(pos, "176139-1", 20)
    assert ltmci["Estado"] == "RESUELTO" and ltmci["Fuente"] == "JPM" and ltmci["CalcType_exportable"] == "YTW"
    assert 0.05 < ltmci["Yield"] < 0.08 and ltmci["Yield_Moneda"] == "USD"
    exc = pos[pos["Fuente"] == "EXCEPCIONES"]
    assert len(exc) >= 30 and (exc["CalcType"] == "PROP").all()
    assert _fila(pos, "527-1", 20)["Fuente"] == "EXCEPCIONES"                             # BAUZA LOAN, flujos del PM
    assert (pos["Origen"].str.startswith("HERMANO:")).any()
    assert (al["Nombre"] == "FALTANTE").any()                                              # lo que solo BBG/TD resuelven (H3)

    # Cada fuente elegida existe como candidato válido; Excel escrito
    cand = res.candidatos[res.candidatos["Valido"]]
    ganadores = set(zip(ok["Pos_ID"], ok["Fuente"]))
    assert ganadores <= set(zip(cand["Pos_ID"], cand["Fuente"]))
    assert {"resumen", "cartera_final", "candidatos", "alertas"} <= set(pd.ExcelFile(res.excel).sheet_names)
