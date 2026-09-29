"""FX de beemining (SQL Server). Credenciales solo desde .env; caché CSV por cierre para correr sin red."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import pandas as pd

from ..fx import INSTRUMENTOS_BEE, nominal_bee


class FuenteFx(Protocol):
    def ultimos(self, fecha: str) -> dict[str, float]: ...       # {"USDCLP": 924.78, ...} último valor <= fecha


class FixtureFx:
    def __init__(self, dir_cache: Path, fecha: str):
        self.path = Path(dir_cache) / f"fx_beemining_{fecha}.csv"

    def ultimos(self, fecha: str) -> dict[str, float]:
        if not self.path.exists():
            return {}
        d = pd.read_csv(self.path)
        return {str(k): float(v) for k, v in zip(d["instrumento"], d["valor"]) if pd.notna(v)}


class BeeminingFx:
    def ultimos(self, fecha: str) -> dict[str, float]:
        import pyodbc
        conn = pyodbc.connect("Driver={SQL Server};SERVER=%s;DATABASE=%s;UID=%s;PWD=%s;" % tuple(
            os.environ[k] for k in ("BEE_SERVER", "BEE_DB", "BEE_UID", "BEE_PWD")))
        marcas = ",".join("?" * len(INSTRUMENTOS_BEE))
        sql = (f"SELECT instrumentcode, instrumentvalue FROM (SELECT instrumentcode, instrumentvalue, "
               f"ROW_NUMBER() OVER (PARTITION BY instrumentcode ORDER BY daydate DESC) rn "
               f"FROM [DW_MONEDA].[dbo].[TBL_RENTABILIDADES_DW] WHERE daydate <= ? AND instrumentcode IN ({marcas})) t WHERE rn = 1")
        filas = conn.cursor().execute(sql, [fecha, *INSTRUMENTOS_BEE]).fetchall()
        return {nominal_bee(str(c)): float(v) for c, v in filas if v and float(v) > 0}


class CacheFx(FixtureFx):
    def __init__(self, inner: FuenteFx, dir_cache: Path, fecha: str):
        super().__init__(dir_cache, fecha)
        self.inner = inner

    def ultimos(self, fecha: str) -> dict[str, float]:
        cache = super().ultimos(fecha)
        if cache:
            return cache
        vivo = self.inner.ultimos(fecha)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"instrumento": list(vivo), "valor": list(vivo.values())}).to_csv(self.path, index=False)
        return vivo
