"""Base de facturas de Facts (Postgres detrás de un túnel SSH). Credenciales solo desde .env; caché CSV por cierre.

Variables (.env): MONEDA_BI_PASSWORD (obligatoria), MONEDA_BI_SSH_KEY (default ~/.ssh/id_ed25519), FACTS_SSH_HOST, FACTS_SSH_PORT,
FACTS_SSH_USER, FACTS_DB, FACTS_DB_USER, FACTS_DB_PORT (defaults del proveedor). Tests nunca importan psycopg2 ni abren ssh.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Protocol

import pandas as pd

TABLAS = ("facturas", "prorrogas", "cambios")
DEFAULTS = {"FACTS_SSH_HOST": "moneda.facts.cl", "FACTS_SSH_PORT": "22", "FACTS_SSH_USER": "bi", "FACTS_DB": "facts",
            "FACTS_DB_USER": "bi_readonly", "FACTS_DB_PORT": "5432", "MONEDA_BI_SSH_KEY": "~/.ssh/id_ed25519"}
FECHAS = {"facturas": ("fecha_inversion", "fecha_emision", "fecha_vencimiento_original", "fecha_vencimiento", "fecha_pago", "fecha_vencimiento_prorroga"),
          "prorrogas": ("fecha_inicio", "fecha_vencimiento_nueva", "fecha_registro"), "cambios": ("fecha",)}


class FuenteFacts(Protocol):
    def tablas(self, fecha: str) -> dict[str, pd.DataFrame]: ...      # {"facturas", "prorrogas", "cambios"}; {} si no hay


def _cfg(clave: str) -> str:
    return os.environ.get(clave) or DEFAULTS.get(clave, "")


class FixtureFacts:
    def __init__(self, dir_cache: Path, fecha: str):
        self.dir, self.fecha = Path(dir_cache), fecha

    def _path(self, tabla: str) -> Path:
        return self.dir / f"facts_{tabla}_{self.fecha}.csv"

    def tablas(self, fecha: str) -> dict[str, pd.DataFrame]:
        if not self._path("facturas").exists():
            return {}
        out = {}
        for t in TABLAS:
            p = self._path(t)
            out[t] = pd.read_csv(p, parse_dates=[c for c in FECHAS[t] if c in pd.read_csv(p, nrows=0).columns]) if p.exists() else pd.DataFrame()
        return out


class CacheFacts(FixtureFacts):
    def __init__(self, inner: FuenteFacts, dir_cache: Path, fecha: str):
        super().__init__(dir_cache, fecha)
        self.inner = inner

    def tablas(self, fecha: str) -> dict[str, pd.DataFrame]:
        cache = super().tablas(fecha)
        if cache:
            return cache
        vivo = self.inner.tablas(fecha)
        if not vivo:
            return {}
        self.dir.mkdir(parents=True, exist_ok=True)
        for t in TABLAS:
            vivo.get(t, pd.DataFrame()).to_csv(self._path(t), index=False)
        return vivo


class FactsSql:
    """Túnel `ssh -L` con el cliente del sistema + psycopg2. Descarga las tres tablas completas (pocos segundos)."""

    def __init__(self, espera_tunel: float = 20.0):
        self.espera = espera_tunel

    def _credenciales(self) -> tuple[str, Path]:
        clave = os.environ.get("MONEDA_BI_PASSWORD")
        if not clave:
            raise RuntimeError("MONEDA_BI_PASSWORD no definida en .env (clave de la base de Facts)")
        llave = Path(_cfg("MONEDA_BI_SSH_KEY")).expanduser()
        if not llave.exists():
            raise RuntimeError(f"MONEDA_BI_SSH_KEY: no existe la llave SSH {llave}")
        return clave, llave

    def _tunel(self, llave: Path) -> tuple[subprocess.Popen, int]:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); puerto = s.getsockname()[1]
        cmd = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=15", "-i", str(llave), "-p", _cfg("FACTS_SSH_PORT"),
               "-L", f"127.0.0.1:{puerto}:127.0.0.1:{_cfg('FACTS_DB_PORT')}", f"{_cfg('FACTS_SSH_USER')}@{_cfg('FACTS_SSH_HOST')}"]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        limite = time.time() + self.espera
        while time.time() < limite:
            if proc.poll() is not None:
                raise RuntimeError(f"túnel SSH a {_cfg('FACTS_SSH_HOST')} terminó: {proc.stderr.read().decode(errors='replace').strip()[-400:]}")
            try:
                with socket.create_connection(("127.0.0.1", puerto), timeout=1):
                    return proc, puerto
            except OSError:
                time.sleep(0.5)
        proc.kill()
        raise RuntimeError(f"túnel SSH a {_cfg('FACTS_SSH_HOST')} no respondió en {self.espera:.0f} s")

    def _consultar(self, sqls: dict[str, str]) -> dict[str, pd.DataFrame]:
        clave, llave = self._credenciales()
        import psycopg2
        proc, puerto = self._tunel(llave)
        try:
            conn = psycopg2.connect(host="127.0.0.1", port=puerto, dbname=_cfg("FACTS_DB"), user=_cfg("FACTS_DB_USER"), password=clave,
                                    connect_timeout=15, options="-c statement_timeout=60000")
            try:
                out = {}
                with conn.cursor() as cur:
                    for nombre, sql in sqls.items():
                        cur.execute(sql)
                        out[nombre] = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
                return out
            finally:
                conn.close()
        finally:
            proc.kill()

    def tablas(self, fecha: str) -> dict[str, pd.DataFrame]:
        return self._consultar({t: f"SELECT * FROM bi_{t}" for t in TABLAS})

    def resumen(self) -> dict[str, object]:
        """Diagnóstico de conexión (facts-probar): filas por tabla y último cambio registrado."""
        r = self._consultar({"facturas": "SELECT count(*) AS n FROM bi_facturas", "prorrogas": "SELECT count(*) AS n FROM bi_prorrogas",
                             "cambios": "SELECT count(*) AS n, max(fecha) AS ultimo FROM bi_cambios"})
        return {"facturas": int(r["facturas"]["n"][0]), "prorrogas": int(r["prorrogas"]["n"][0]),
                "cambios": int(r["cambios"]["n"][0]), "ultimo_cambio": str(r["cambios"]["ultimo"][0])}
