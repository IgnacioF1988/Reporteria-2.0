"""Construye tests/fixtures/mini/ a partir de los maestros corporativos entregados (tests/fixtures/corporativo/).

    python tests/fixtures/construir_fixtures.py

Recorta BD_INSTRUMENTOS y HOMOL a los instrumentos de la muestra del CUBO (el maestro completo tarda ~30 s en
leerse), copia las dimensiones chicas tal cual, arma un REGLAS.xlsx inicial (cajas desde Template_Cajas, las
reclasificaciones MRCLP de BD_BalanceSheet) y sintetiza FACTURAS en el formato del RPT de Facts para las facturas
que sí están en el CUBO. Los .xlsx de mini/ quedan versionados; este script documenta su origen.
"""
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
CORP, MINI = AQUI / "corporativo", AQUI / "mini"
FECHA = "20260731"
SETTLE = pd.Timestamp("2026-07-31")
COPIAR = ["BD_FUNDS.xlsx", "BD_BalanceSheet.xlsx", "BD_Monedas.xlsx", "BD_YLD_FLAG.xlsx", "BD_YIELD.xlsx",
          "DEFAULTED.xlsx", "HOMOL_FUNDS.xlsx", "BD_FX_Exposure_MLDL.xlsx", "BD_FX_Exposure_MRCLP.xlsx",
          "BD_INVESTMENT_TYPE.xlsx", "BD_ISSUE_TYPE.xlsx", "BD_ISSUER_TYPE.xlsx", "BD_COUPON_TYPE.xlsx",
          "BD_CASH_TYPE.xlsx", "BD_BANK_DEBT_TYPE.xlsx", "BD_FUND_TYPE.xlsx", "BD_RANK.xlsx"]


def main():
    MINI.mkdir(exist_ok=True)
    cubo = pd.read_excel(CORP / "CUBO_20260731.xlsx")
    cubo.to_excel(MINI / f"CUBO_{FECHA}.xlsx", index=False)
    for f in COPIAR:
        (MINI / f).write_bytes((CORP / f).read_bytes())

    bd = pd.read_excel(CORP / "BD_INSTRUMENTOS.xlsx")
    bd["PK2"] = bd["ID_Instrumento"].astype(str) + "-" + bd["SubID_Instrumento"].astype(str)
    ids = set(cubo["ID_Instrumento"])
    mini_bd = bd[bd["ID_Instrumento"].isin(ids)].drop(columns="PK2")
    with pd.ExcelWriter(MINI / "BD_INSTRUMENTOS.xlsx") as w:
        mini_bd.to_excel(w, sheet_name="BD_INSTRUMENTOS", index=False)

    homol = pd.read_excel(CORP / "HOMOL_INSTRUMENTOS.xlsx")
    homol[homol["ID_Instrumento"].isin(ids)].to_excel(MINI / "HOMOL_INSTRUMENTOS.xlsx", index=False)

    # FACTURAS en formato RPT de Facts, sintetizadas para las facturas de la muestra (MRCLP, fondo 20)
    fac = cubo.merge(mini_bd[["ID_Instrumento", "Name_Instrumento", "Issue_Type_Code"]], on="ID_Instrumento")
    fac = fac[(fac["Issue_Type_Code"] == 5) & (fac["ID_Fund"] == 20)].drop_duplicates("PK2").reset_index(drop=True)
    n = len(fac)
    rpt = pd.DataFrame({
        "documento_operacion_id": range(80000, 80000 + n),
        "nemotecnico": fac["Name_Instrumento"],
        "fondo": "MRCLP",
        "tipo": "CC",
        "estado": ["vigente"] * n,
        "fecha_inversion": SETTLE - pd.Timedelta(days=20),
        "moneda": "CLP",
        "monto_compra": fac["TotalMVal"].round(0),
        "fecha_vencimiento_original": [SETTLE + pd.Timedelta(days=30 + (i % 60)) for i in range(n)],
        "fecha_vencimiento": [SETTLE + pd.Timedelta(days=30 + (i % 60)) for i in range(n)],
        "fecha_pago": pd.NaT,
        "tasa_mensual": [0.008 + 0.0005 * (i % 5) for i in range(n)],
        "prorrogas": 0,
    })
    rpt["yield"] = rpt["tasa_mensual"] * 12
    if n >= 3:
        rpt.loc[0, "estado"], rpt.loc[0, "fecha_pago"] = "pagado", SETTLE - pd.Timedelta(days=3)   # no debe usarse
        rpt.loc[1, "monto_compra"] = rpt.loc[1, "monto_compra"] * 1.05                             # alerta monto
        rpt.loc[2, ["prorrogas", "fecha_vencimiento"]] = [1, SETTLE + pd.Timedelta(days=120)]      # prórroga
    rpt.to_excel(MINI / f"FACTURAS_{FECHA}.xlsx", sheet_name="Facturas", index=False)

    # REGLAS.xlsx inicial
    tc = pd.concat([pd.read_excel(CORP / "Template_Cajas.xlsx", sheet_name=s) for s in ("Fondos USD", "MLDL")])
    funds = pd.read_excel(CORP / "BD_FUNDS.xlsx")
    nombre2id = dict(zip(funds["FundShortName"], funds["ID_Fund"]))
    tc["ID_Fund"] = tc["FundShortName"].map(nombre2id)
    cajas = pd.DataFrame({
        "ID_Fund": tc["ID_Fund"].astype("Int64"), "PK2": tc["PK2"].astype(str).str.strip(),
        "Indice_Referencia": tc["Indice Referencia"].where(tc["Indice Referencia"].notna(), ""),
        "Spread_Anual": tc["Spread (Anual)"], "Dias": tc["Fecha_Vencimiento"],
        "Comentario": "migrado de Template_Cajas " + tc["Name_Instrumento"].astype(str),
    }).drop_duplicates(["ID_Fund", "PK2"])
    buckets = pd.DataFrame([
        ("Fixed Income", "CASCADA", 1), ("Equity", "CERO", 2), ("Cash, Mutual Funds & Others", "CAJA", 3),
        ("Restricted Cash", "CAJA", 4), ("Restricted Cash (Deriv.)", "CAJA", 5), ("Restricted Cash (REPO)", "CAJA", 6),
        ("Receivable", "CERO", 7), ("MTM (+)", "CERO", 8), ("Fixed Income Short", "CASCADA", 9), ("Equity Short", "CERO", 10),
        ("Financial Debt", "CAJA", 11), ("Repo", "CAJA", 12), ("Collateral Payable", "CERO", 13), ("Payable", "CERO", 14),
        ("MTM (-)", "CERO", 15),
    ], columns=["Bucket", "Tratamiento", "Orden"])
    clas = pd.DataFrame([
        (1, 20, "BalSheetKey", "Asset11412000", "Cash, Mutual Funds & Others", "", "MRCLP: depósito fijo (Issue 4) es caja"),
        (2, 20, "BalSheetKey", "Asset11432000", "Cash, Mutual Funds & Others", "", "MRCLP: depósito inflation-linked es caja"),
        (3, 20, "BalSheetKey", "Asset12422000", "Cash, Mutual Funds & Others", "", "MRCLP: depósito soberano flotante es caja"),
        (4, 20, "BalSheetKey", "Asset12812000", "Cash, Mutual Funds & Others", "", "MRCLP: pagaré soberano fijo es caja"),
        (5, 20, "BalSheetKey", "Asset12832000", "Cash, Mutual Funds & Others", "", "MRCLP: pagaré soberano inflation-linked es caja"),
        (6, 20, "PK2", "176142-38", "Equity", "", "FIP ALZA RENTAS COMERCIALES II — Treatment Equity"),
        (7, None, "Issue_Type_Code", "5", "", "FACTURA", "Facturas: sigue en Fixed Income pero la métrica sale del RPT de Facts"),
    ], columns=["ID", "ID_Fund", "Criterio", "Valor", "Bucket", "Tratamiento", "Comentario"])
    fondos = pd.DataFrame([(11, "A_CLP", "MDCH: papel en moneda fuerte se asume swapeado a CLP"),
                           (20, "A_CLP", "MRCLP: idem"),
                           (17, "POR_PAIS", "MLDL: swap a la moneda local del Risk_Country")],
                          columns=["ID_Fund", "Politica_Hedge", "Comentario"])
    defs = pd.DataFrame(columns=["ID_Fund", "ID_Instrumento", "Estado", "Fecha_Desde", "Fecha_Fin", "Comentario"])
    ov_val = pd.DataFrame(columns=["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Yield", "Duration",
                                   "Fecha_Desde", "Fecha_Fin", "Fuente", "Comentario"])
    ov_att = pd.DataFrame(columns=["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Field", "Value",
                                   "Fecha_Desde", "Fecha_Fin", "Comentario"])
    alertas = pd.DataFrame(columns=["ID", "Nombre", "Campo", "Operador", "Umbral", "Severidad", "ID_Fund",
                                    "Activa", "Requiere_Anterior", "Ambito", "Descripcion"])
    params = pd.DataFrame([("yield_max_proveedor", 1.0, "Yield máxima aceptada de un proveedor (decimal)"),
                           ("yield_min_proveedor", -0.5, "Yield mínima aceptada"),
                           ("factura_tolerancia_monto", 0.01, "Diferencia relativa monto_compra vs TotalMVal que alerta"),
                           ("yield_type_default", 15, "Yield_Type cuando el maestro trae 0 o vacío (15 = YTW)")],
                          columns=["Clave", "Valor", "Descripcion"])
    with pd.ExcelWriter(MINI / "REGLAS.xlsx") as w:
        for nombre, df in [("fondos", fondos), ("buckets", buckets), ("clasificacion", clas), ("cajas", cajas),
                           ("defaulteados", defs), ("overrides_valor", ov_val), ("overrides_atributo", ov_att),
                           ("alertas", alertas), ("parametros", params)]:
            df.to_excel(w, sheet_name=nombre, index=False)

    # Caché BBG de fixture: niveles de los índices de referencia de cajas (PX_LAST, en %, como los entrega BBG)
    bbg = MINI / "bbg_cache"; bbg.mkdir(exist_ok=True)
    pd.DataFrame([("OBFR01 Index", 4.33), ("FEDL01 Index", 4.33), ("ESTRON Index", 1.92), ("SONIO/N Index", 4.00),
                  ("MXIBTIIE Index", 7.75), ("CABROVER Index", 2.75), ("NIBOR1W Index", 4.30)],
                 columns=["ticker", "valor"]).to_csv(bbg / f"bdh_PX_LAST_{FECHA}.csv", index=False)
    print(f"mini listo: {len(mini_bd)} instrumentos, {n} facturas, {len(cajas)} filas de cajas")


if __name__ == "__main__":
    main()
