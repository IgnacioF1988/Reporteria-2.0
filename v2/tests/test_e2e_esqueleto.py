"""Walking skeleton: una corrida completa sobre 14 posiciones reales del CUBO.

Los estados esperados se actualizan a medida que entran fuentes:
H0 → sin fuentes de proveedor: lo que va a la cascada queda FALTANTE.
"""
import pandas as pd

from reporteria.pipeline import Opciones, correr

ESTADO_ESPERADO = {
    ("1213-39", 11, "Asset"): ("RESUELTO", "CAJA", "REGLA_FIJA"),
    ("223985-39", 20, "Asset"): ("RESUELTO", "CAJA", "REGLA_FIJA"),           # DAP: regla del fondo pisa la global
    ("200810-39", 20, "Liability"): ("RESUELTO", "CUENTAS_POR_PAGAR_COBRAR", "CERO"),
    ("221752-10000", 20, "Asset"): ("RESUELTO", "DERIVADOS", "CERO"),
    ("189776-39", 20, "Asset"): ("RESUELTO", "EQUITY", "CERO"),
    ("87871-39", 20, "Asset"): ("RESUELTO", "FONDOS_MUTUOS", "REGLA_FIJA"),   # sin valor → yield 0 + alerta
    ("223332-39", 20, "Asset"): ("RESUELTO", "PACTOS_SIMULTANEAS", "REGLA_FIJA"),
    ("227718-39", 20, "Asset"): ("RESUELTO", "FACTURAS", "FACTURA"),
    ("527-1", 20, "Asset"): ("FALTANTE", "RENTA_FIJA", ""),                   # EXCEPCIONES llega en H2
    ("176142-38", 20, "Asset"): ("EXCLUIDO", "EQUITY", "EXCLUIR"),
    ("176139-1", 20, "Asset"): ("FALTANTE", "RENTA_FIJA", ""),                # JPM llega en H2
    ("46507-39", 20, "Asset"): ("FALTANTE", "RENTA_FIJA", ""),                # RA llega en H2
    ("928-1", 20, "Asset"): ("RESUELTO", "RENTA_FIJA", "REGLA_DEF"),
    ("176727-1", 16, "Liability"): ("FALTANTE", "BANK_DEBT", ""),
}


def test_correr_minimo(rutas):
    res = correr(rutas, Opciones(sin_bbg=True, sin_sql=True))
    pos = res.posiciones.set_index(["PK2", "ID_Fund", "BalanceSheet"])

    assert len(pos) == 14
    cubo = pd.read_excel(rutas.cubo)
    for (fid, bs), mv in cubo.groupby(["ID_Fund", "BalanceSheet"])["TotalMVal"].sum().items():
        assert abs(pos.xs((fid, bs), level=("ID_Fund", "BalanceSheet"))["TotalMVal"].sum() - mv) < 1e-6

    for llave, (estado, bucket, etapa) in ESTADO_ESPERADO.items():
        fila = pos.loc[llave]
        assert fila["Estado"] == estado, (llave, fila["Estado"], fila["Motivo"])
        assert fila["Bucket"] == bucket, (llave, fila["Bucket"])
        assert fila["Etapa"] == etapa, (llave, fila["Etapa"])

    resueltas = pos[pos["Estado"] == "RESUELTO"]
    assert resueltas["Yield"].notna().all() and resueltas["Duration"].notna().all()
    assert resueltas["Yield"].between(-0.5, 1.0).all()          # decimal, nunca %
    assert pos.loc[("928-1", 20, "Asset"), ["Yield", "Duration"]].tolist() == [0.0, 0.5]
    assert abs(pos.loc[("227718-39", 20, "Asset"), "Yield"] - 0.096) < 1e-9
    assert abs(pos.loc[("227718-39", 20, "Asset"), "Duration"] - 45 / 365) < 1e-9

    nombres = set(res.alertas["Nombre"])
    assert "REGLA_SIN_VALOR" in nombres and "FALTANTE" in nombres
    sin_valor = res.alertas[res.alertas["Nombre"] == "REGLA_SIN_VALOR"]
    assert set(sin_valor["PK2"]) == {"87871-39", "223332-39"}

    # Cada Fuente elegida existe como candidato válido
    cand = res.candidatos[res.candidatos["Valido"]]
    for _, r in resueltas.iterrows():
        assert ((cand["Pos_ID"] == r["Pos_ID"]) & (cand["Fuente"] == r["Fuente"])).any()

    assert res.excel.exists()
    hojas = pd.ExcelFile(res.excel).sheet_names
    assert {"resumen", "cartera_final", "candidatos", "alertas"} <= set(hojas)
