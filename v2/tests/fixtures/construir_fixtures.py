"""Construye tests/fixtures/mini/ a partir de los maestros corporativos entregados (tests/fixtures/corporativo/).

    PYTHONPATH=. python tests/fixtures/construir_fixtures.py   # desde v2/

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
                           ("yield_type_default", 15, "Yield_Type cuando el maestro trae 0 o vacío (15 = YTW)"),
                           ("politica_hedge", "XCCY_SI_EXISTE", "Hedgeados: XCCY_SI_EXISTE (swap de mercado, si no drop propio) o DROP_SIEMPRE"),
                           ("xccy_drop_max_bps", 50, "Alerta XCCY_VS_DROP cuando |XCCY − drop propio| supera estos bps")],
                          columns=["Clave", "Valor", "Descripcion"])
    # override de valor de ejemplo: DOCUFO (defaulteado de facto, BBG devuelve duration 0) en el fondo 16 → yield 12 %, duration 1
    ov_val = pd.DataFrame([(16, 168617, 1, 0.12, 1.0, None, None, "OPERADOR", "DOCUFO 10.25 07/24/2024 144A: reestructuración, valor acordado con PM")],
                          columns=ov_val.columns)
    with pd.ExcelWriter(MINI / "REGLAS.xlsx") as w:
        for nombre, df in [("fondos", fondos), ("buckets", buckets), ("clasificacion", clas), ("cajas", cajas),
                           ("defaulteados", defs), ("overrides_valor", ov_val), ("overrides_atributo", ov_att),
                           ("alertas", alertas), ("parametros", params)]:
            df.to_excel(w, sheet_name=nombre, index=False)

    # ── Fuentes de mercado de julio (legacy), recortadas a la muestra ──
    ids_isin = set(mini_bd["ISIN"].dropna().astype(str))
    cem = pd.read_excel(CORP / "JPM_CEMBI_GBI_20260731.xlsx", sheet_name="CEMBI")
    gbi = pd.read_excel(CORP / "JPM_CEMBI_GBI_20260731.xlsx", sheet_name="GBI_Embroad")
    # hermanos: mismo nombre base → conservar también las series que no están en el CUBO
    base = mini_bd["Name_Instrumento"].astype(str).str.replace(r"\s+(REGS|REG S|144A|EMTN)\s*$", "", regex=True, case=False)
    bd_full_isin = bd[bd["Name_Instrumento"].astype(str).str.replace(r"\s+(REGS|REG S|144A|EMTN)\s*$", "", regex=True, case=False).isin(set(base))]
    ids_isin |= set(bd_full_isin["ISIN"].dropna().astype(str))
    with pd.ExcelWriter(MINI / f"JPM_CEMBI_GBI_{FECHA}.xlsx") as w:
        cem[cem["ISIN_ID"].astype(str).isin(ids_isin)].to_excel(w, sheet_name="CEMBI", index=False)
        gbi[gbi["ISIN"].astype(str).isin(ids_isin)].to_excel(w, sheet_name="GBI_Embroad", index=False)
    ra = pd.read_excel(CORP / "RA_TIR.xlsx", sheet_name="jul26")
    nemos = set(mini_bd["Name_Instrumento"].astype(str).str.upper().str.strip())
    ra_mini = ra[ra.iloc[:, 0].astype(str).str.upper().str.strip().isin(nemos)]
    with pd.ExcelWriter(MINI / "RA_TIR.xlsx") as w:
        pd.concat([ra_mini, ra.head(3)]).drop_duplicates().to_excel(w, sheet_name="jul26", index=False)
    pk2s_cubo = set(cubo["PK2"].astype(str))
    for f in sorted(CORP.glob("EXCEPCIONES_*.xlsx")):
        xl = pd.ExcelFile(f)
        hojas = [sh for sh in xl.sheet_names if sh.strip() in pk2s_cubo]
        if hojas:
            with pd.ExcelWriter(MINI / f.name) as w:
                for sh in hojas:
                    xl.parse(sh).to_excel(w, sheet_name=sh, index=False)
    par = pd.ExcelFile(CORP / "4- Carga de paridades.xlsx")
    with pd.ExcelWriter(MINI / "4- Carga de paridades.xlsx") as w:
        for sh in ("Data Paridad NY", "Data Paridad LDN", "Data EUR|USD OBS"):
            d = par.parse(sh)
            d = d[pd.to_datetime(d["Date"], errors="coerce").between(SETTLE - pd.Timedelta(days=12), SETTLE)]
            d.to_excel(w, sheet_name=sh, index=False)
    # FX beemining del cierre (valores usados por el legacy; USDARS sintético del caso documentado)
    pd.DataFrame([("USDCLP", 924.78), ("USDCLF", 0.02273), ("USDBRL", 5.0729), ("USDCOP", 3152.58), ("USDMXN", 17.3426),
                  ("USDPEN", 3.3985), ("USDUYU", 40.265), ("USDUVR", 7.568429), ("USDARS", 1382.0)],
                 columns=["instrumento", "valor"]).to_csv(MINI / f"fx_beemining_{FECHA}.csv", index=False)
    # Golden: resultados del legacy para excepciones (comparación ±1 bp)
    leg = pd.read_excel(CORP / "LEGACY_EXCEPCIONES_20260731.xlsx", sheet_name="metricas")
    leg[["ID_Fund", "PK2", "Moneda", "sP", "sQ", "Escalar_flag", "FX_usado", "Q_real", "FI_local", "Yield_efectiva", "ModDur"]].to_csv(
        MINI / "golden_excepciones_20260731.csv", index=False)

    # bond_schedule.jsonl recortado a los códigos GENEVA de la muestra + casos documentados
    import json
    CASOS = {"RECARR 9.67 08/10/38 19", "CASH URUGUA 13.50 09/20/26", "PATIO PERU 9.00 29032045", "FUNOMM 9.2 11/29/27 17",
             "SOLFACIL 16.48 01/15/34", "SOLFACIL 5.70 05/08/35", "AGROVISION 0% 19/09/2026", "CONMEX 5.95 12/15/35 REGS",
             "Titularice - Avista 01", "Titularice - Kredit 02", "RDEDOR 11.82 01/13/28 10", "TOWER ONE COLOMBIA Loan B 12/13/26",
             "ARGENT V0 12/15/35 GDP$"}
    codes = set(homol[(homol["ID_Instrumento"].isin(ids)) & (homol["Source"] == "GENEVA")]["SourceInvestment"].astype(str)) | CASOS
    with open(CORP / "bond_schedule.jsonl", encoding="utf-8") as f, open(MINI / "bond_schedule.jsonl", "w", encoding="utf-8") as g:
        for line in f:
            if line.strip() and json.loads(line).get("code") in codes:
                g.write(line if line.endswith("\n") else line + "\n")

    # Caché BBG: YAS / XCCY / DES_CASH_FLOW desde los outputs legacy de julio + niveles de índices de cajas
    from reporteria.legado import importar_cache_legacy
    bbg = MINI / "bbg_cache"; bbg.mkdir(exist_ok=True)
    for old in bbg.glob("bdp_*.csv"):
        old.unlink()
    for old in bbg.glob("bds_CURVE_TENOR_RATES/*.csv"):
        old.unlink()
    print("caché legacy:", importar_cache_legacy(CORP, bbg, FECHA))
    # curvas corporativas del cierre (reales por índice y soberanas nominales) y goldens de conversión del legacy
    for f in CORP.glob(f"Carga_Indexes_{FECHA}*.csv"):
        (MINI / f.name).write_bytes(f.read_bytes())
    for f in CORP.glob(f"Carga_CurvasSoberanas_{FECHA}*.csv"):
        (MINI / f.name).write_bytes(f.read_bytes())
    be = pd.read_excel(CORP / "LEGACY_BREAKEVEN_20260731.xlsx", sheet_name="convertidos")
    be["ID_Fund"] = be["Fondo"].map(nombre2id).astype(int)
    be[["ID_Fund", "PK2", "Index_Type", "Fuente", "Yield_original", "Duration_original", "plazo_dias", "r_real_%", "r_nom_%", "ajuste",
        "Yield_local", "Extrapolado"]].to_csv(MINI / "golden_breakeven_20260731.csv", index=False)
    dr = pd.read_excel(CORP / "LEGACY_DROPS_20260731.xlsx", sheet_name="convertidos")
    dr["ID_Fund"] = dr["Fondo"].map(nombre2id).astype(int)
    dr[["ID_Fund", "PK2", "Hedge_Currency", "Yield_USD", "Duration_original", "BBG_XCCY_Yield", "plazo_dias", "r_local_%", "r_basis_bps",
        "r_usd_%", "drop", "Yield_Drop", "Dif_vs_XCCY_bps", "Metodo"]].to_csv(MINI / "golden_drops_20260731.csv", index=False)
    pd.DataFrame([("OBFR01 Index", 4.33), ("FEDL01 Index", 4.33), ("ESTRON Index", 1.92), ("SONIO/N Index", 4.00),
                  ("MXIBTIIE Index", 7.75), ("CABROVER Index", 2.75), ("NIBOR1W Index", 4.30)],
                 columns=["ticker", "valor"]).to_csv(bbg / f"bdh_PX_LAST_{FECHA}.csv", index=False)
    print(f"mini listo: {len(mini_bd)} instrumentos, {n} facturas, {len(cajas)} filas de cajas")

    # ── casos_legacy: posiciones con TD propia documentadas (golden de JSONL / CSHF) ──
    CAS = AQUI / "casos_legacy"; CAS.mkdir(exist_ok=True)
    full = pd.read_excel(CORP / "LEGACY_CUBO_COMPLETO_20260731.xlsx")
    full["PK2"] = full["PK2"].astype(str).str.strip()
    llaves = [("201936-175", 17), ("166894-135", 17), ("166894-135", 20), ("189238-1", 17), ("29844-194", 17), ("153765-41", 17),
              ("132057-41", 17), ("991-29", 17), ("480-1", 17), ("116575-41", 17)]
    sel = pd.concat([full[(full["PK2"] == pk2) & (full["ID_Fund"] == fid)] for pk2, fid in llaves]).drop_duplicates(["PK2", "ID_Fund", "BalanceSheet"])
    sel = sel.rename(columns={"Fund_Name": "FundShortName"})
    sel["id_CURR"] = sel["PK2"].str.split("-").str[1].astype(int)
    sel[["PK2", "ID_Fund", "ID_Instrumento", "id_CURR", "BalanceSheet", "Source", "LocalPrice", "Qty", "OriginalFace", "Factor", "AI",
         "MVBook", "TotalMVal"]].to_excel(CAS / f"CUBO_{FECHA}.xlsx", index=False)
    ids_c = set(sel["ID_Instrumento"])
    with pd.ExcelWriter(CAS / "BD_INSTRUMENTOS.xlsx") as w:
        bd[bd["ID_Instrumento"].isin(ids_c)].drop(columns="PK2").to_excel(w, sheet_name="BD_INSTRUMENTOS", index=False)
    homol[homol["ID_Instrumento"].isin(ids_c)].to_excel(CAS / "HOMOL_INSTRUMENTOS.xlsx", index=False)
    for f in COPIAR + ["REGLAS.xlsx", "bond_schedule.jsonl", f"fx_beemining_{FECHA}.csv", "4- Carga de paridades.xlsx"]:
        (CAS / f).write_bytes((MINI / f).read_bytes())
    (CAS / "bbg_cache").mkdir(exist_ok=True)
    for f in (MINI / "bbg_cache").glob("*.csv"):
        (CAS / "bbg_cache" / f.name).write_bytes(f.read_bytes())
    leg = pd.read_excel(CORP / "LEGACY_PROP_JSONL_20260731.xlsx", sheet_name="metricas")
    leg[["ID_Fund", "PK2", "Tipo_bono", "Moneda", "Escalar_flag", "sP", "sQ", "Q_real", "FI_local", "Yield_efectiva", "ModDur"]].to_csv(
        CAS / "golden_jsonl_20260731.csv", index=False)
    legc = pd.read_excel(CORP / "LEGACY_CSHF_20260731.xlsx", sheet_name="resueltos")
    legc[["ID_Fund", "PK2", "ISIN", "Moneda", "Escalar_flag", "sP", "sQ", "face_bbg", "Q_real", "CSHF_Yield_efec", "CSHF_ModDur"]].to_csv(
        MINI / "golden_cshf_20260731.csv", index=False)
    print(f"casos_legacy listo: {len(sel)} posiciones")


if __name__ == "__main__":
    main()
