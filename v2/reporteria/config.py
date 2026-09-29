"""Configuración: fondos, rutas y constantes técnicas. Nada de credenciales acá (van en .env)."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Fondo:
    id: int
    alias: tuple[str, ...]
    base_ccy: str
    hedge: str = ""          # '' | 'POR_PAIS' (MLDL) | 'A_CLP' (MDCH, MRCLP)


FONDOS: dict[int, Fondo] = {
    2: Fondo(2, ("ALTURAS II",), "USD"),
    11: Fondo(11, ("MDCH", "MDCHILE"), "CLP", "A_CLP"),
    13: Fondo(13, ("MDLAT",), "USD"),
    16: Fondo(16, ("MLCD",), "USD"),
    17: Fondo(17, ("MLDL",), "USD", "POR_PAIS"),
    20: Fondo(20, ("MRCLP", "MRentaCLP", "MRENTACLP"), "CLP", "A_CLP"),
    59: Fondo(59, ("MLATHY",), "USD"),
    65: Fondo(65, ("MCIT",), "USD"),
    68: Fondo(68, ("MLCC (Geneva)", "MLCC"), "USD"),
}


def id_fondo(valor) -> int | None:
    """Resuelve un ID numérico o un alias (para migrar manuales viejos). None si no existe."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return None
    try:
        i = int(float(valor))
        return i if i in FONDOS else None
    except (TypeError, ValueError):
        pass
    v = str(valor).strip().upper()
    for f in FONDOS.values():
        if v in {a.upper() for a in f.alias}:
            return f.id
    return None


def _uno(patron: str) -> Path | None:
    hits = sorted(p for p in glob.glob(patron) if not os.path.basename(p).startswith("~$"))
    return Path(hits[0]) if hits else None


@dataclass(frozen=True)
class Rutas:
    fecha: str
    raiz: Path
    cubo: Path
    bd_instr: Path
    bd_funds: Path
    homol: Path
    reglas: Path
    facturas: Path | None
    jpm: Path | None
    ra: Path
    jsonl: Path
    indexes: Path | None
    curvas_sob: Path | None
    paridades: Path
    excepciones: tuple[Path, ...]
    atributos: tuple[Path, ...]
    outputs: Path
    logs: Path
    cache: Path
    fecha_ant: str | None = None

    @property
    def settle(self):
        import pandas as pd
        return pd.Timestamp(self.fecha)

    @property
    def excel_final(self) -> Path:
        return self.outputs / f"REPORTE_{self.fecha}.xlsx"

    @classmethod
    def desde_env(cls, fecha: str, raiz: str | Path | None = None, fecha_ant: str | None = None) -> "Rutas":
        load_dotenv()
        raiz = Path(raiz or os.environ.get("REPORTERIA_RAIZ") or Path(__file__).resolve().parents[1])
        cubo_dir = Path(os.environ.get("RUTA_CUBO_DIR", raiz / "01_INPUTS" / "CORPORATIVO"))
        bix = Path(os.environ.get("RUTA_BIX", raiz / "01_INPUTS" / "CORPORATIVO"))
        inp = raiz / "01_INPUTS"
        mercado, manuales, geneva = inp / "MERCADO", inp / "MANUALES", inp / "GENEVA"
        return cls(
            fecha=fecha, raiz=raiz,
            cubo=cubo_dir / f"CUBO_{fecha}.xlsx",
            bd_instr=bix / "BD_INSTRUMENTOS.xlsx",
            bd_funds=bix / "DIMENSIONALES" / "BD_FUNDS.xlsx",
            homol=bix / "HOMOL_INSTRUMENTOS.xlsx",
            reglas=manuales / "REGLAS.xlsx",
            facturas=_uno(str(mercado / f"FACTURAS_{fecha}*.xlsx")),
            jpm=_uno(str(mercado / f"JPM_CEMBI_GBI_{fecha}*.xlsx")),
            ra=mercado / "RA_TIR.xlsx",
            jsonl=geneva / "bond_schedule.jsonl",
            indexes=_uno(str(mercado / f"Carga_Indexes_{fecha}*.csv")),
            curvas_sob=_uno(str(mercado / f"Carga_CurvasSoberanas_{fecha}*.csv")),
            paridades=mercado / "4- Carga de paridades.xlsx",
            excepciones=tuple(sorted(Path(p) for p in glob.glob(str(manuales / "EXCEPCIONES*.xlsx"))
                                     if not Path(p).name.startswith("~$"))),
            atributos=tuple(sorted(Path(p) for p in glob.glob(str(manuales / "Atributos_*.xlsx"))
                                   if not Path(p).name.startswith("~$"))),
            outputs=raiz / "02_OUTPUTS" / fecha,
            logs=raiz / "03_LOGS" / fecha,
            cache=raiz / "04_CACHE" / fecha,
            fecha_ant=fecha_ant or _detectar_fecha_ant(raiz / "02_OUTPUTS", fecha),
        )

    @classmethod
    def para_pruebas(cls, fecha: str, fixtures: Path, tmp: Path) -> "Rutas":
        """Todo apunta a la carpeta de fixtures; outputs/logs/cache en tmp."""
        return cls(
            fecha=fecha, raiz=fixtures,
            cubo=fixtures / f"CUBO_{fecha}.xlsx", bd_instr=fixtures / "BD_INSTRUMENTOS.xlsx",
            bd_funds=fixtures / "BD_FUNDS.xlsx", homol=fixtures / "HOMOL.xlsx", reglas=fixtures / "REGLAS.xlsx",
            facturas=_uno(str(fixtures / f"FACTURAS_{fecha}*.xlsx")), jpm=_uno(str(fixtures / f"JPM_*{fecha}*.xlsx")),
            ra=fixtures / "RA_TIR.xlsx", jsonl=fixtures / "bond_schedule.jsonl",
            indexes=_uno(str(fixtures / f"Carga_Indexes_{fecha}*.csv")),
            curvas_sob=_uno(str(fixtures / f"Carga_CurvasSoberanas_{fecha}*.csv")),
            paridades=fixtures / "paridades.xlsx",
            excepciones=tuple(sorted(fixtures.glob("EXCEPCIONES*.xlsx"))),
            atributos=tuple(sorted(fixtures.glob("Atributos_*.xlsx"))),
            outputs=tmp / "02_OUTPUTS" / fecha, logs=tmp / "03_LOGS" / fecha, cache=tmp / "04_CACHE" / fecha,
        )

    def obligatorias(self) -> dict[str, Path]:
        return {"CUBO": self.cubo, "BD_INSTRUMENTOS": self.bd_instr, "BD_FUNDS": self.bd_funds, "REGLAS": self.reglas}

    def opcionales(self) -> dict[str, Path | None]:
        return {"FACTURAS": self.facturas, "JPM": self.jpm, "RA_TIR": self.ra, "HOMOL": self.homol,
                "bond_schedule": self.jsonl, "Carga_Indexes": self.indexes, "CurvasSoberanas": self.curvas_sob,
                "Paridades": self.paridades,
                "EXCEPCIONES": self.excepciones[0] if self.excepciones else None,
                "Atributos": self.atributos[0] if self.atributos else None}


def _detectar_fecha_ant(dir_outputs: Path, fecha: str) -> str | None:
    if not dir_outputs.is_dir():
        return None
    prev = sorted(d.name for d in dir_outputs.iterdir() if d.is_dir() and d.name.isdigit() and len(d.name) == 8 and d.name < fecha)
    return prev[-1] if prev else None
