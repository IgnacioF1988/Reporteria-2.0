"""Construye los fixtures mini a partir de los archivos del repo legacy.

Se corre UNA vez (los .xlsx resultantes quedan versionados):
    python tests/fixtures/construir_fixtures.py ../   # ruta al repo Reporteria-2.0
Cuando el usuario entregue muestras reales de CUBO / BD_INSTRUMENTOS / BD_FUNDS
se reemplazan por esas y este script queda como documentación del origen.
"""
import sys
from pathlib import Path

import pandas as pd

LEGACY = Path(sys.argv[1] if len(sys.argv) > 1 else "..").resolve()
OUT = Path(__file__).parent
FECHA = "20260731"
SETTLE = pd.Timestamp("2026-07-31")

# (PK2, ID_Fund, BalanceSheet) del walking skeleton — cada fila cubre un camino distinto
POSICIONES = [
    ("1213-39", 11, "Asset"),        # caja MDCH                      → CAJA FIJO 0/0
    ("223985-39", 20, "Asset"),      # DAP MRCLP                      → CAJA por regla del fondo (global: DAP CASCADA)
    ("200810-39", 20, "Liability"),  # payable MRCLP                  → CERO
    ("221752-10000", 20, "Asset"),   # derivado MRCLP                 → CERO
    ("189776-39", 20, "Asset"),      # equity MRCLP                   → CERO
    ("87871-39", 20, "Asset"),       # fondo mutuo CFMBNSMMLA         → FIJO sin valor → yield 0 + alerta
    ("223332-39", 20, "Asset"),      # simultánea SIM_CREDICORP       → FIJO sin valor (regex gana a Investment_Type 5)
    ("227718-39", 20, "Asset"),      # factura FACRCGP77804           → FACTURA
    ("527-1", 20, "Asset"),          # BAUZA LOAN (excepción PM)      → CASCADA (EXCEPCIONES en H2)
    ("176142-38", 20, "Asset"),      # FIP ALZA RENTAS II             → EXCLUIR por PK2 en fondo 20
    ("176139-1", 20, "Asset"),       # LTMCI 7.625 2031 (JPM)         → CASCADA (JPM en H2)
    ("46507-39", 20, "Asset"),       # BTP0281033 (RA)                → CASCADA (RA en H2)
    ("928-1", 20, "Asset"),          # OCEANO 2015 defaulteado        → REGLA_DEF
    ("176727-1", 16, "Liability"),   # TPLH LOAN pasivo MLCD          → BANK_DEBT CASCADA (FALTANTE)
]


def main():
    cubo = pd.read_excel(LEGACY / "Otros/Fondos_jul.xlsx")
    cubo["PK2"] = cubo["PK2"].astype(str).str.strip()
    cajas = pd.concat([pd.read_excel(LEGACY / "Cajas_jul.xlsx", sheet_name=s) for s in ("USD", "MLDL", "CLP")])
    cajas["PK2"] = cajas["PK2"].astype(str).str.strip()
    uni = pd.concat([pd.read_excel(LEGACY / f"02_OUTPUTS/{FECHA}/UNIVERSO_{FECHA}.xlsx", sheet_name=s)
                     for s in ("con_ISIN", "sin_ISIN", "defaulteados", "facturas")], ignore_index=True)
    uni["PK2"] = uni["PK2"].astype(str).str.strip()

    llaves = pd.DataFrame(POSICIONES, columns=["PK2", "ID_Fund", "BalanceSheet"])
    mini = llaves.merge(cubo, on=["PK2", "ID_Fund", "BalanceSheet"], how="left")
    assert mini["TotalMVal"].notna().all(), mini[mini["TotalMVal"].isna()]
    mini.to_excel(OUT / f"CUBO_{FECHA}.xlsx", index=False)

    # BD_INSTRUMENTOS: RF desde UNIVERSO, no-RF desde Cajas (Investment_Type_Code, moneda, nombre)
    rf = uni.drop_duplicates("PK2").set_index("PK2")
    no_rf = cajas.drop_duplicates("PK2").set_index("PK2")
    filas = []
    for pk2 in mini["PK2"].unique():
        iid, sub = pk2.split("-")
        if pk2 in rf.index:
            r = rf.loc[pk2]
            filas.append(dict(ID_Instrumento=int(iid), SubID_Instrumento=int(sub), Risk_Currency=r["Risk_Currency"],
                              Investment_Type_Code=1, Issue_Type_Code=r["Issue_Type_Code"],
                              Name_Instrumento=r["Name_Instrumento"], ISIN=r["ISIN"],
                              Risk_Country=r["Risk_Country"], CompanyName=r["CompanyName"]))
        else:
            r = no_rf.loc[pk2]
            filas.append(dict(ID_Instrumento=int(iid), SubID_Instrumento=int(sub), Risk_Currency=r["id_CURR_Code"],
                              Investment_Type_Code=int(r["Investment_Type_Code"]), Issue_Type_Code=None,
                              Name_Instrumento=r["Name_Instrumento"], ISIN=None, Risk_Country=None, CompanyName=None))
    with pd.ExcelWriter(OUT / "BD_INSTRUMENTOS.xlsx") as w:
        pd.DataFrame(filas).to_excel(w, sheet_name="BD_INSTRUMENTOS", index=False)

    pd.DataFrame({"ID_Fund": [2, 11, 13, 16, 17, 20, 59, 65, 68],
                  "FundShortName": ["ALTURAS II", "MDCH", "MDLAT", "MLCD", "MLDL", "MRCLP", "MLATHY", "MCIT", "MLCC (Geneva)"],
                  "FundBaseCurrency": ["USD", "CLP", "USD", "USD", "USD", "CLP", "USD", "USD", "USD"]}
                 ).to_excel(OUT / "BD_FUNDS.xlsx", index=False)

    # FACTURAS (formato nuevo): tasa mensual en %, vencimiento a 45 días, monto igual al MV
    fac = mini[mini["PK2"] == "227718-39"].iloc[0]
    pd.DataFrame([{"PK2": "227718-39", "Tasa_Mensual": 0.8, "Monto": round(fac["TotalMVal"], 2),
                   "Fecha_Vencimiento": SETTLE + pd.Timedelta(days=45)}]
                 ).to_excel(OUT / f"FACTURAS_{FECHA}.xlsx", index=False)

    # REGLAS.xlsx mini
    clas = pd.DataFrame([
        # ID, ID_Fund, Criterio, Valor, Bucket, Tratamiento, Yield, Duration, Comentario
        (1, None, "Investment_Type_Code", "1", "RENTA_FIJA", "CASCADA", None, None, "Renta fija: busca métrica en la cascada"),
        (2, None, "Investment_Type_Code", "3", "CAJA", "FIJO", 0, 0, "Caja y equivalentes"),
        (3, None, "Investment_Type_Code", "4", "CUENTAS_POR_PAGAR_COBRAR", "CERO", None, None, ""),
        (4, None, "Investment_Type_Code", "6", "FONDOS_MUTUOS", "FIJO", None, None, "Completar yield/dur por el operador"),
        (5, None, "Investment_Type_Code", "7", "DERIVADOS", "CERO", None, None, ""),
        (6, None, "Investment_Type_Code", "2", "EQUITY", "CERO", None, None, ""),
        (7, None, "Investment_Type_Code", "5", "BANK_DEBT", "CASCADA", None, None, "Deuda bancaria"),
        (8, None, "Issue_Type_Code", "5", "FACTURAS", "FACTURA", None, None, "Cruza con FACTURAS_{FECHA}.xlsx"),
        (9, None, "Nombre_Regex", r"^SIM_", "PACTOS_SIMULTANEAS", "FIJO", None, None, "Simultáneas: completar yield/dur"),
        (10, None, "Issue_Type_Code", "4", "RENTA_FIJA", "CASCADA", None, None, "DAP: renta fija por defecto"),
        (11, 20, "Issue_Type_Code", "4", "CAJA", "FIJO", 0, 0, "DAP en MRCLP es caja y equivalentes"),
        (12, 20, "PK2", "176142-38", "EQUITY", "EXCLUIR", None, None, "FIP ALZA RENTAS II — Treatment Equity"),
    ], columns=["ID", "ID_Fund", "Criterio", "Valor", "Bucket", "Tratamiento", "Yield", "Duration", "Comentario"])
    defs = pd.DataFrame([(None, "928-1", "DEF", "OCEANO 11.25 07/15/2015 REGS")],
                        columns=["ID_Fund", "PK2", "Estado", "Comentario"])
    ov_val = pd.DataFrame(columns=["ID_Fund", "PK2", "Yield", "Duration", "Moneda", "Fuente", "Comentario",
                                   "Vigente_Desde", "Vigente_Hasta"])
    ov_att = pd.DataFrame(columns=["ID_Fund", "PK2", "Atributo", "Valor", "Comentario"])
    alertas = pd.DataFrame(columns=["ID", "Nombre", "Campo", "Operador", "Umbral", "Severidad", "ID_Fund",
                                    "Activa", "Requiere_Anterior", "Ambito", "Descripcion"])
    params = pd.DataFrame([("yield_max_proveedor", 1.0, "Yield máxima aceptada de un proveedor (decimal)"),
                           ("yield_min_proveedor", -0.5, "Yield mínima aceptada"),
                           ("factura_tolerancia_monto", 0.01, "Diferencia relativa Monto vs TotalMVal que dispara alerta")],
                          columns=["Clave", "Valor", "Descripcion"])
    with pd.ExcelWriter(OUT / "REGLAS.xlsx") as w:
        for nombre, df in [("clasificacion", clas), ("defaulteados", defs), ("overrides_valor", ov_val),
                           ("overrides_atributo", ov_att), ("alertas", alertas), ("parametros", params)]:
            df.to_excel(w, sheet_name=nombre, index=False)
    print("fixtures listos en", OUT)


if __name__ == "__main__":
    main()
