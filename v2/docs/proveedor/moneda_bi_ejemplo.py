"""
Ejemplo de conexión a las tablas de tasas y yields de Facts (Moneda).

Abre un túnel SSH al servidor de Moneda, se conecta a la base con el usuario de
solo lectura y baja las tres tablas:

    bi_facturas   una fila por factura comprada (tasa, spread, yield, montos, fechas)
    bi_prorrogas  una fila por prórroga
    bi_cambios    cambios de tasa, spread, vencimiento, montos y estado (desde abril 2026)

Requisitos:
    - Python 3.9 o superior
    - Cliente OpenSSH (viene en macOS, Linux y Windows 10/11)
    - pip install psycopg2-binary pandas openpyxl

Configuración, por variables de entorno:
    MONEDA_BI_PASSWORD   clave de la base (obligatoria; se entrega por separado)
    MONEDA_BI_SSH_KEY    ruta a su llave privada SSH (por defecto ~/.ssh/id_ed25519)

Uso:
    python moneda_bi_ejemplo.py                       # baja todo a Excel
    python moneda_bi_ejemplo.py --cambios-desde 2026-09-01

(Script de referencia entregado por el proveedor. La reportería usa reporteria/adaptadores/facts.py, que replica
el túnel y las consultas; este archivo se conserva tal cual para comparar si el proveedor cambia algo.)
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2

# Servidor y base (no cambian)
SSH_HOST = "moneda.facts.cl"
SSH_USER = "bi"
DB_HOST_REMOTO = "127.0.0.1"  # la base, vista desde el servidor
DB_PORT_REMOTO = 5432
DB_NAME = "facts"
DB_USER = "bi_readonly"


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def tunel_ssh(llave):
    """Abre `ssh -N -L` en segundo plano y devuelve el puerto local del túnel."""
    puerto = _puerto_libre()
    cmd = [
        "ssh", "-N",
        "-L", f"{puerto}:{DB_HOST_REMOTO}:{DB_PORT_REMOTO}",
        "-i", str(llave),
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",                   # sin preguntas interactivas
        "-o", "StrictHostKeyChecking=accept-new",
        f"{SSH_USER}@{SSH_HOST}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        # Esperar a que el túnel acepte conexiones (máx. 20 s)
        limite = time.time() + 20
        while True:
            if proc.poll() is not None:
                error = proc.stderr.read().decode(errors="replace").strip()
                raise RuntimeError(f"No se pudo abrir el túnel SSH: {error}")
            try:
                with socket.create_connection(("127.0.0.1", puerto), timeout=1):
                    break
            except OSError:
                if time.time() > limite:
                    raise RuntimeError("El túnel SSH no respondió en 20 segundos.")
                time.sleep(0.5)
        yield puerto
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def conectar(puerto, password):
    return psycopg2.connect(
        host="127.0.0.1", port=puerto, dbname=DB_NAME,
        user=DB_USER, password=password, connect_timeout=10,
    )


def leer(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columnas = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columnas)


def descargar(conn, cambios_desde=None):
    facturas = leer(conn, "SELECT * FROM bi_facturas ORDER BY fecha_inversion, documento_operacion_id")
    prorrogas = leer(conn, "SELECT * FROM bi_prorrogas ORDER BY documento_operacion_id, fecha_inicio")
    if cambios_desde:
        # Carga incremental: solo los cambios desde una fecha
        cambios = leer(conn, "SELECT * FROM bi_cambios WHERE fecha >= %s ORDER BY fecha", [cambios_desde])
    else:
        cambios = leer(conn, "SELECT * FROM bi_cambios ORDER BY fecha")
    return facturas, prorrogas, cambios


def main():
    parser = argparse.ArgumentParser(description="Descarga las tablas de tasas y yields de Facts.")
    parser.add_argument("--cambios-desde", type=date.fromisoformat,
                        help="Traer solo los cambios desde esta fecha (AAAA-MM-DD).")
    parser.add_argument("--salida", default=f"moneda_bi_{date.today():%Y%m%d}.xlsx",
                        help="Archivo Excel de salida.")
    args = parser.parse_args()

    password = os.environ.get("MONEDA_BI_PASSWORD")
    if not password:
        sys.exit("Falta la variable de entorno MONEDA_BI_PASSWORD.")
    llave = Path(os.environ.get("MONEDA_BI_SSH_KEY", "~/.ssh/id_ed25519")).expanduser()
    if not llave.exists():
        sys.exit(f"No encuentro la llave SSH en {llave} (configure MONEDA_BI_SSH_KEY).")

    with tunel_ssh(llave) as puerto:
        conn = conectar(puerto, password)
        try:
            facturas, prorrogas, cambios = descargar(conn, args.cambios_desde)
        finally:
            conn.close()

    print(f"Facturas:  {len(facturas):>7,}")
    print(f"Prórrogas: {len(prorrogas):>7,}")
    print(f"Cambios:   {len(cambios):>7,}")

    # Ejemplo de uso: yield promedio ponderado por monto de compra, por fondo,
    # de la cartera vigente.
    vigente = facturas[facturas["estado"].isin(["vigente", "moroso", "prorrogado"])].copy()
    vigente["monto_compra"] = vigente["monto_compra"].astype(float)
    vigente["yield"] = vigente["yield"].astype(float)
    resumen = (
        vigente.assign(ponderado=vigente["monto_compra"] * vigente["yield"])
        .groupby("fondo")
        .agg(facturas=("nemotecnico", "count"),
             monto_compra=("monto_compra", "sum"),
             ponderado=("ponderado", "sum"))
    )
    resumen["yield_ponderado"] = resumen["ponderado"] / resumen["monto_compra"]
    print("\nYield ponderado de la cartera vigente por fondo:")
    print(resumen[["facturas", "monto_compra", "yield_ponderado"]].to_string(
        formatters={"monto_compra": "{:,.0f}".format, "yield_ponderado": "{:.2%}".format}))

    with pd.ExcelWriter(args.salida) as xls:
        facturas.to_excel(xls, sheet_name="Facturas", index=False)
        prorrogas.to_excel(xls, sheet_name="Prórrogas", index=False)
        cambios.to_excel(xls, sheet_name="Cambios", index=False)
    print(f"\nGuardado en {args.salida}")


if __name__ == "__main__":
    main()
