"""Rutas y constantes técnicas. Fondos, alias y monedas salen de BD_FUNDS; credenciales del .env."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

POLITICAS_HEDGE = ("", "POR_PAIS", "A_CLP")
# Familias de serie: mismo bono emitido en tramos legales distintos (Reg S / 144A / EMTN) con ISIN propio.
# Se infieren por nombre base (sin el sufijo). Ajustable desde REGLAS/parametros: sufijos_serie = "REGS;144A;…".
SUFIJOS_SERIE = ("REGS", "REGS-S", "REG S", "144A", "144@", "EMTN")
STRONG_CCY = {"USD", "EUR", "GBP"}
RISK_COUNTRY_TO_LOCAL_CCY = {"AR": "ARS", "BR": "BRL", "CL": "CLP", "PE": "PEN", "UY": "UYU", "CO": "COP", "MX": "MXN"}
# Yield_Type del maestro → campo YAS de Bloomberg (BD_YIELD: 1 YTM, 2 YTC, 15 YTW, 28 YTA)
YIELD_TYPE_BBG = {1: "YAS_YLD_MATURITY", 2: "YAS_YLD_CALL", 15: "YAS_BOND_YLD", 28: "YAS_YLD_AVG_LIFE"}


def _uno(patron: str) -> Path | None:
    hits = sorted(p for p in glob.glob(patron) if not os.path.basename(p).startswith("~$"))
    return Path(hits[0]) if hits else None


def _en(dirs: list[Path], nombre: str) -> Path:
    """Primer directorio donde exista el archivo; si no existe en ninguno, la ruta en el primero (para el check)."""
    for d in dirs:
        if (d / nombre).exists():
            return d / nombre
    return dirs[0] / nombre


@dataclass(frozen=True)
class Rutas:
    fecha: str
    raiz: Path
    cubo: Path
    bd_instr: Path
    bd_funds: Path
    bd_balance: Path
    bd_monedas: Path
    bd_yld_flag: Path
    defaulted: Path
    homol: Path
    homol_funds: Path
    fx_exposure: tuple[Path, ...]
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

    @property
    def reporte_anterior(self) -> Path | None:
        return self.outputs.parent / self.fecha_ant / f"REPORTE_{self.fecha_ant}.xlsx" if self.fecha_ant else None

    @classmethod
    def _armar(cls, fecha, raiz, cubo_dir, bix_dirs, mercado, manuales, geneva, outputs, logs, cache, fecha_ant=None):
        fx = []
        for d in bix_dirs:
            fx += [Path(p) for p in glob.glob(str(d / "BD_FX_Exposure_*.xlsx")) if not Path(p).name.startswith("~$")]
        return cls(
            fecha=fecha, raiz=raiz, cubo=cubo_dir / f"CUBO_{fecha}.xlsx",
            bd_instr=_en(bix_dirs, "BD_INSTRUMENTOS.xlsx"), bd_funds=_en(bix_dirs, "BD_FUNDS.xlsx"),
            bd_balance=_en(bix_dirs, "BD_BalanceSheet.xlsx"), bd_monedas=_en(bix_dirs, "BD_Monedas.xlsx"),
            bd_yld_flag=_en(bix_dirs, "BD_YLD_FLAG.xlsx"), defaulted=_en(bix_dirs, "DEFAULTED.xlsx"),
            homol=_en(bix_dirs, "HOMOL_INSTRUMENTOS.xlsx"), homol_funds=_en(bix_dirs, "HOMOL_FUNDS.xlsx"),
            fx_exposure=tuple(sorted(set(fx))), reglas=manuales / "REGLAS.xlsx",
            facturas=_uno(str(mercado / f"FACTURAS_{fecha}*.xlsx")), jpm=_uno(str(mercado / f"JPM_CEMBI_GBI_{fecha}*.xlsx")),
            ra=mercado / "RA_TIR.xlsx", jsonl=geneva / "bond_schedule.jsonl",
            indexes=_uno(str(mercado / f"Carga_Indexes_{fecha}*.csv")),
            curvas_sob=_uno(str(mercado / f"Carga_CurvasSoberanas_{fecha}*.csv")),
            paridades=mercado / "4- Carga de paridades.xlsx",
            excepciones=tuple(sorted(Path(p) for p in glob.glob(str(manuales / "EXCEPCIONES*.xlsx")) if not Path(p).name.startswith("~$"))),
            atributos=tuple(sorted(Path(p) for p in glob.glob(str(manuales / "Atributos_*.xlsx")) if not Path(p).name.startswith("~$"))),
            outputs=outputs, logs=logs, cache=cache, fecha_ant=fecha_ant,
        )

    @classmethod
    def desde_env(cls, fecha: str, raiz: str | Path | None = None, fecha_ant: str | None = None) -> "Rutas":
        load_dotenv()
        raiz = Path(raiz or os.environ.get("REPORTERIA_RAIZ") or Path(__file__).resolve().parents[1])
        inp = raiz / "01_INPUTS"
        cubo_dir = Path(os.environ.get("RUTA_CUBO_DIR") or inp / "CORPORATIVO")
        bix = Path(os.environ.get("RUTA_BIX") or inp / "CORPORATIVO")
        return cls._armar(fecha, raiz, cubo_dir, [bix, bix / "DIMENSIONALES"], inp / "MERCADO", inp / "MANUALES",
                          inp / "GENEVA", raiz / "02_OUTPUTS" / fecha, raiz / "03_LOGS" / fecha, raiz / "04_CACHE" / fecha,
                          fecha_ant or _detectar_fecha_ant(raiz / "02_OUTPUTS", fecha))

    @classmethod
    def para_pruebas(cls, fecha: str, fixtures: Path, tmp: Path) -> "Rutas":
        """Todo en la carpeta de fixtures; outputs y logs en tmp; caché BBG de fixture (solo lectura)."""
        return cls._armar(fecha, fixtures, fixtures, [fixtures], fixtures, fixtures, fixtures,
                          tmp / "02_OUTPUTS" / fecha, tmp / "03_LOGS" / fecha, fixtures / "bbg_cache")

    def obligatorias(self) -> dict[str, Path]:
        return {"CUBO": self.cubo, "BD_INSTRUMENTOS": self.bd_instr, "BD_FUNDS": self.bd_funds,
                "BD_BalanceSheet": self.bd_balance, "BD_Monedas": self.bd_monedas, "REGLAS": self.reglas}

    def opcionales(self) -> dict[str, Path | None]:
        return {"DEFAULTED": self.defaulted, "BD_YLD_FLAG": self.bd_yld_flag, "HOMOL_INSTRUMENTOS": self.homol,
                "HOMOL_FUNDS": self.homol_funds, "BD_FX_Exposure": self.fx_exposure[0] if self.fx_exposure else None,
                "FACTURAS": self.facturas, "JPM": self.jpm, "RA_TIR": self.ra, "bond_schedule": self.jsonl,
                "Carga_Indexes": self.indexes, "CurvasSoberanas": self.curvas_sob, "Paridades": self.paridades,
                "EXCEPCIONES": self.excepciones[0] if self.excepciones else None,
                "Atributos": self.atributos[0] if self.atributos else None}


def _detectar_fecha_ant(dir_outputs: Path, fecha: str) -> str | None:
    if not dir_outputs.is_dir():
        return None
    prev = sorted(d.name for d in dir_outputs.iterdir() if d.is_dir() and d.name.isdigit() and len(d.name) == 8 and d.name < fecha)
    return prev[-1] if prev else None
