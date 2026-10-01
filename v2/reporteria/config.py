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
MAX_DIAS_ATRAS_PARIDADES = 10
RISK_COUNTRY_TO_LOCAL_CCY = {"AR": "ARS", "BR": "BRL", "CL": "CLP", "PE": "PEN", "UY": "UYU", "CO": "COP", "MX": "MXN"}
# Yield_Type del maestro → campo YAS de Bloomberg (BD_YIELD: 1 YTM, 2 YTC, 15 YTW, 28 YTA)
YIELD_TYPE_BBG = {1: "YAS_YLD_MATURITY", 2: "YAS_YLD_CALL", 15: "YAS_BOND_YLD", 28: "YAS_YLD_AVG_LIFE"}

# ── Índices (conversión de yields) ───────────────────────────────────────────────────────────────────────────────
# REAL: la yield del papel es real → breakeven con la curva nominal local (Carga_CurvasSoberanas) y la real (Carga_Indexes).
# RATE: flotante; si la TD es propia (solo spread) se le suma el nivel del índice; si viene de proveedor se asume nominal local.
INDICES = {
    "UF":     dict(cat="REAL", real="BTUCHILE",    nom="LCCHILE",     ccy="CLP"),
    "UDI":    dict(cat="REAL", real="UDIMEXICO",   nom="LCMEXICO",    ccy="MXN"),
    "UVR":    dict(cat="REAL", real="UVRCOLOMBIA", nom="LCCOLOMBIA",  ccy="COP"),
    "IPCA":   dict(cat="REAL", real="IPCABRAZIL",  nom="LCBRAZIL",    ccy="BRL"),
    "UI":     dict(cat="REAL", real="UIURUGUAY",   nom="LCURUGUAY",   ccy="UYU"),
    "BONCER": dict(cat="REAL", real="BONCERARG",   nom="LCARGENTINA", ccy="ARS"),
    "VAC":    dict(cat="REAL", real="VACPERU",     nom="LCPERU",      ccy="PEN"),
    "CDI":    dict(cat="RATE", real="CDIBRAZIL",   nom=None,          ccy="BRL"),
    "TIIE":   dict(cat="RATE", real="MXNTIIEMXN",  nom=None,          ccy="MXN"),
    # Flotantes chilenos sin curva cargada: la TD del PM ya trae el cupón all-in → la yield queda nominal (INDICE_SIN_CURVA, INFO)
    "TAB30":    dict(cat="RATE", real=None, nom=None, ccy="CLP"),
    "CHIBPROM": dict(cat="RATE", real=None, nom=None, ccy="CLP"),
}
INDICES_SIN_CONVERSION = {"", "NOMINAL", "SOFR", "UST1Y", "UST5Y", "UST10Y"}
# Monedas que declaran el índice por sí mismas (BD_Monedas: supramoneda ≠ código)
CCY_A_INDICE = {"CLF": "UF", "UDI": "UDI", "UVR COSTER": "UVR", "UI CURNCY": "UI"}
# Monedas locales donde un papel puede ser nominal o indexado: lo decide Bloomberg (INFLATION_LINKED_INDICATOR / RESET_IDX)
CCY_AMBIGUA = {"ARS", "BRL", "COP", "MXN", "PEN", "UYU", "CLP", "MXV"}
INFLACION_PAIS = {"CL": "UF", "CH": "UF", "MX": "UDI", "CO": "UVR", "BR": "IPCA", "UY": "UI", "AR": "BONCER", "PE": "VAC"}
RESET_IDX_A_INDICE = {"BZDIOVRA": "CDI", "MXIBTIEF": "TIIE", "MXIBTIIE": "TIIE", "SOFRRATE": "SOFR",
                      "H15T1Y": "UST1Y", "H15T5Y": "UST5Y", "H15T10Y": "UST10Y"}
ALIAS_INDICE = {"CLCPI": "UF", "CLF": "UF", "CER": "BONCER", "MXCPI": "UDI", "UVR COSTER": "UVR", "COCPI": "UVR", "BRCPI": "IPCA",
                "UYCPI": "UI", "UI CURNCY": "UI", "MXIBTIIE": "TIIE", "MXIBTIEF": "TIIE", "BZDIOVRA": "CDI"}
FUENTES_TD_PROPIA = {"EXCEPCIONES", "CSHF", "JSONL"}
# ── Drops: papel USD swapeado a moneda local nominal ─────────────────────────────────────────────────────────────
# drop = local_all_in − pata_usd al plazo de la duration. ADD: local + basis (bps); DIRECT: una curva ya combinada.
CURVAS_DROPS = {
    "CLP": dict(metodo="ADD", local="YCSW0193", local_prefix="CHSWNI", basis="YCSW0194", basis_prefix="CPXOSS", usd="YCSW0490", usd_prefix="USOSFR"),
    "COP": dict(metodo="ADD", local="YCSW0329", local_prefix="CLSWIB", basis="YCSW0192", basis_prefix="CLXOQQ", usd="YCSW0490", usd_prefix="USOSFR"),
    "MXN": dict(metodo="ADD", local="YCSW0083", local_prefix="MPSW", basis="YCSW0151", basis_prefix="MPBSF", usd="YCSW0490", usd_prefix="USOSFR"),
    "PEN": dict(metodo="DIRECT", local="YCSW0374", local_prefix="PENSSS", usd="YCSW0490", usd_prefix="USOSFR"),
    "BRL": dict(metodo="DIRECT", local="YCSW0089", local_prefix=None, usd="YCSW0304", usd_prefix=None),
}
CURVAS_SIN_FALLBACK_AUTO = {"MPSW", "MPBSF"}       # tickers no numéricos: si falta 1Y/30Y se extrapola plano
POLITICAS_XCCY = ("XCCY_SI_EXISTE", "DROP_SIEMPRE")


def _uno(patron: str) -> Path | None:
    hits = sorted(p for p in glob.glob(patron) if not os.path.basename(p).startswith("~$"))
    return Path(hits[0]) if hits else None


def _en(dirs: list[Path], nombre: str) -> Path:
    """Primer directorio donde exista el archivo; si no existe en ninguno, la ruta en el primero (para el check)."""
    for d in dirs:
        if (d / nombre).exists():
            return d / nombre
    return dirs[0] / nombre


RAIZ_PAQUETE = Path(__file__).resolve().parents[1]          # v2/: TODO lo que el pipeline lee o escribe vive aquí (salvo CUBO y BIX, shares externos)
DIM_DEFAULT = RAIZ_PAQUETE / "dim" / "dimensionales.duckdb"  # versionado en git (REPORTERIA_DIM lo cambia)
DATAMART_DEFAULT = RAIZ_PAQUETE / "datamart"                 # versiones publicadas, en git (REPORTERIA_DATAMART)


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
    dim: Path
    bix_dirs: tuple[Path, ...]
    datamart: Path
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
    def borradores(self) -> Path:
        return self.outputs / "borradores"

    @property
    def reporte_anterior(self) -> Path | None:
        return self.outputs.parent / self.fecha_ant / f"REPORTE_{self.fecha_ant}.xlsx" if self.fecha_ant else None

    @classmethod
    def _armar(cls, fecha, raiz, cubo_dir, bix_dirs, mercado, manuales, geneva, outputs, logs, cache, fecha_ant=None, dim=None, datamart=None):
        fx = []
        for d in bix_dirs:
            fx += [Path(p) for p in glob.glob(str(d / "BD_FX_Exposure_*.xlsx")) if not Path(p).name.startswith("~$")]
        return cls(
            fecha=fecha, raiz=raiz, cubo=cubo_dir / f"CUBO_{fecha}.xlsx",
            bd_instr=_en(bix_dirs, "BD_INSTRUMENTOS.xlsx"), bd_funds=_en(bix_dirs, "BD_FUNDS.xlsx"),
            bd_balance=_en(bix_dirs, "BD_BalanceSheet.xlsx"), bd_monedas=_en(bix_dirs, "BD_Monedas.xlsx"),
            bd_yld_flag=_en(bix_dirs, "BD_YLD_FLAG.xlsx"), defaulted=_en(bix_dirs, "DEFAULTED.xlsx"),
            homol=_en(bix_dirs, "HOMOL_INSTRUMENTOS.xlsx"), homol_funds=_en(bix_dirs, "HOMOL_FUNDS.xlsx"),
            fx_exposure=tuple(sorted(set(fx))), dim=Path(dim) if dim else DIM_DEFAULT, bix_dirs=tuple(Path(d) for d in bix_dirs),
            datamart=Path(datamart) if datamart else DATAMART_DEFAULT,
            reglas=manuales / "REGLAS.xlsx",
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
        """Raíz = carpeta del paquete (v2). `raiz` explícita solo para pruebas; REPORTERIA_RAIZ ya no se lee (v2 es autónomo)."""
        load_dotenv()
        raiz = Path(raiz) if raiz else RAIZ_PAQUETE
        inp = raiz / "01_INPUTS"
        cubo_dir = Path(os.environ.get("RUTA_CUBO_DIR") or inp / "CORPORATIVO")
        bix = Path(os.environ.get("RUTA_BIX") or inp / "CORPORATIVO")
        datamart = Path(os.environ.get("REPORTERIA_DATAMART") or DATAMART_DEFAULT)
        return cls._armar(fecha, raiz, cubo_dir, [bix, bix / "DIMENSIONALES"], inp / "MERCADO", inp / "MANUALES",
                          inp / "GENEVA", raiz / "02_OUTPUTS" / fecha, raiz / "03_LOGS" / fecha, raiz / "04_CACHE" / fecha,
                          fecha_ant or _detectar_fecha_ant(raiz / "02_OUTPUTS", fecha, datamart), os.environ.get("REPORTERIA_DIM"), datamart)

    @classmethod
    def para_pruebas(cls, fecha: str, fixtures: Path, tmp: Path) -> "Rutas":
        """Todo en la carpeta de fixtures; outputs y logs en tmp; caché BBG de fixture (solo lectura); dim = fixtures/dimensionales.duckdb."""
        return cls._armar(fecha, fixtures, fixtures, [fixtures], fixtures, fixtures, fixtures,
                          tmp / "02_OUTPUTS" / fecha, tmp / "03_LOGS" / fecha, fixtures / "bbg_cache",
                          fecha_ant=_detectar_fecha_ant(tmp / "02_OUTPUTS", fecha, tmp / "datamart"),
                          dim=fixtures / "dimensionales.duckdb", datamart=tmp / "datamart")

    def obligatorias(self) -> dict[str, Path]:
        return {"CUBO": self.cubo, "BD_INSTRUMENTOS": self.bd_instr, "DIMENSIONALES": self.dim, "REGLAS": self.reglas}

    def opcionales(self) -> dict[str, Path | None]:
        return {"DEFAULTED": self.defaulted, "HOMOL_INSTRUMENTOS": self.homol, "HOMOL_FUNDS": self.homol_funds,
                "FACTURAS": self.facturas, "JPM": self.jpm, "RA_TIR": self.ra, "bond_schedule": self.jsonl,
                "Carga_Indexes": self.indexes, "CurvasSoberanas": self.curvas_sob, "Paridades": self.paridades,
                "EXCEPCIONES": self.excepciones[0] if self.excepciones else None,
                "Atributos": self.atributos[0] if self.atributos else None}


def _detectar_fecha_ant(dir_outputs: Path, fecha: str, datamart: Path | None = None) -> str | None:
    """Último cierre anterior con reporte en 02_OUTPUTS o con versión en el datamart (cierres/cierre=YYYYMMDD)."""
    prev = set()
    if dir_outputs.is_dir():
        prev |= {d.name for d in dir_outputs.iterdir() if d.is_dir() and d.name.isdigit() and len(d.name) == 8}
    if datamart is not None and (Path(datamart) / "cierres").is_dir():
        prev |= {d.name.split("=", 1)[1] for d in (Path(datamart) / "cierres").iterdir() if d.is_dir() and d.name.startswith("cierre=")}
    prev = sorted(p for p in prev if p < fecha)
    return prev[-1] if prev else None
