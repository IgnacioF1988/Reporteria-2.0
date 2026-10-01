"""Frontera con DuckDB: `dimensionales.duckdb` versionado en git es la fuente de verdad de las tablas dimensionales.

Se edita exportando a Excel (`dim exportar`), cambiando filas e importando de vuelta (`dim importar --excel`); el espejo
CSV (`dim/csv/*.csv`) se regenera en cada escritura para que el `git diff` sea legible (derivado, no fuente).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import dim as D
from ..modelo import CODIGOS, limpiar_txt

TABLA_CLASIF = "dim_clasificacion"
CATALOGOS = {c: D.CATALOGO_DE[c] for c in CODIGOS}            # columna de código → tabla
ARCHIVO_CATALOGO = {"dim_investment_type": "BD_INVESTMENT_TYPE.xlsx", "dim_issuer_type": "BD_ISSUER_TYPE.xlsx",
                    "dim_issue_type": "BD_ISSUE_TYPE.xlsx", "dim_coupon_type": "BD_COUPON_TYPE.xlsx", "dim_rank": "BD_RANK.xlsx",
                    "dim_cash_type": "BD_CASH_TYPE.xlsx", "dim_bank_debt_type": "BD_BANK_DEBT_TYPE.xlsx", "dim_fund_type": "BD_FUND_TYPE.xlsx"}
TABLAS = [TABLA_CLASIF, *ARCHIVO_CATALOGO, "dim_fondos", "dim_monedas", "dim_yld_flag", "dim_meta"]
VERSION_ESQUEMA = "1"


@dataclass
class Dimensionales:
    clasificacion: pd.DataFrame
    catalogos: dict[str, pd.DataFrame] = field(default_factory=dict)       # nombre de tabla → DataFrame
    fondos: pd.DataFrame = field(default_factory=pd.DataFrame)
    monedas: pd.DataFrame = field(default_factory=pd.DataFrame)
    yld_flag: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict[str, str] = field(default_factory=dict)

    def tablas(self) -> dict[str, pd.DataFrame]:
        meta = pd.DataFrame({"Clave": list(self.meta), "Valor": [str(v) for v in self.meta.values()]})
        return {TABLA_CLASIF: D.normalizar(self.clasificacion), **{t: _canon(self.catalogos.get(t, pd.DataFrame())) for t in ARCHIVO_CATALOGO},
                "dim_fondos": _canon(self.fondos), "dim_monedas": _canon(self.monedas), "dim_yld_flag": _canon(self.yld_flag), "dim_meta": meta}

    def bd_funds(self) -> pd.DataFrame:
        from ..lectura.maestros import normalizar_bd_funds
        return normalizar_bd_funds(self.fondos)

    def bd_monedas(self) -> pd.DataFrame:
        from ..lectura.maestros import normalizar_bd_monedas
        return normalizar_bd_monedas(self.monedas)

    def yld_flag_dict(self) -> dict[str, str]:
        from ..lectura.maestros import normalizar_yld_flag
        return normalizar_yld_flag(self.yld_flag)

    def resumen(self) -> dict[str, int]:
        return {t: len(df) for t, df in self.tablas().items() if t != "dim_meta"}


def _duckdb():
    import duckdb
    return duckdb


def _canon(df: pd.DataFrame) -> pd.DataFrame:
    """Tipos estables entre Excel, DuckDB y CSV: enteros con nulos → Int64, columnas de solo texto → object con NA."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    out = out.loc[:, [c for c in out.columns if c != "_vacia"]]
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            v = pd.to_numeric(s, errors="coerce")
            if v.dropna().eq(v.dropna().round()).all():
                out[c] = v.astype("Int64")
            else:
                out[c] = v.astype("float64")
        else:
            txt = limpiar_txt(s)
            out[c] = txt.where(txt.ne(""), pd.NA).astype("object")
    return out.reset_index(drop=True)


def _desde_tablas(t: dict[str, pd.DataFrame]) -> Dimensionales:
    meta = t.get("dim_meta", pd.DataFrame(columns=["Clave", "Valor"]))
    return Dimensionales(clasificacion=D.normalizar(t.get(TABLA_CLASIF, D.tabla_vacia())),
                         catalogos={k: _canon(t[k]) for k in ARCHIVO_CATALOGO if k in t},
                         fondos=_canon(t.get("dim_fondos", pd.DataFrame())), monedas=_canon(t.get("dim_monedas", pd.DataFrame())),
                         yld_flag=_canon(t.get("dim_yld_flag", pd.DataFrame())),
                         meta=dict(zip(limpiar_txt(meta["Clave"]), limpiar_txt(meta["Valor"]))) if len(meta) else {})


def leer(path: Path) -> Dimensionales:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"dimensionales no encontradas: {path} → reporteria dim importar --bix <carpeta BIX>")
    con = _duckdb().connect(str(path), read_only=True)
    try:
        existentes = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        t = {n: con.execute(f'SELECT * FROM "{n}"').df() for n in TABLAS if n in existentes}
    finally:
        con.close()
    return _desde_tablas(t)


def _para_duckdb(df: pd.DataFrame) -> pd.DataFrame:
    """Int64 con NA → float con NaN y object → str/None para que DuckDB infiera tipos simples y estables."""
    out = df.copy()
    for c in out.columns:
        if str(out[c].dtype) in ("Int64", "Int32", "boolean"):
            out[c] = out[c].astype("float64")
        elif out[c].dtype == object or str(out[c].dtype) == "string":
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out


def escribir(path: Path, dims: Dimensionales, csv_dir: Path | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    duckdb = _duckdb()
    con = duckdb.connect(str(path))
    try:
        for nombre, df in dims.tablas().items():
            tmp = _para_duckdb(df if len(df.columns) else pd.DataFrame({"_vacia": pd.Series(dtype=float)}))
            con.register("tmp_df", tmp)
            con.execute(f'CREATE OR REPLACE TABLE "{nombre}" AS SELECT * FROM tmp_df')
            con.unregister("tmp_df")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    escribir_csv(csv_dir if csv_dir is not None else path.parent / "csv", dims)
    return path


def escribir_csv(csv_dir: Path, dims: Dimensionales) -> None:
    csv_dir = Path(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)
    for nombre, df in dims.tablas().items():
        df.to_csv(csv_dir / f"{nombre}.csv", index=False, lineterminator="\n", encoding="utf-8")


def csv_al_dia(csv_dir: Path, dims: Dimensionales) -> bool:
    csv_dir = Path(csv_dir)
    for nombre, df in dims.tablas().items():
        p = csv_dir / f"{nombre}.csv"
        if not p.exists() or p.read_text(encoding="utf-8").replace("\r\n", "\n") != df.to_csv(index=False, lineterminator="\n"):
            return False
    return True


def exportar_excel(dims: Dimensionales, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as w:
        for nombre, df in dims.tablas().items():
            df.to_excel(w, sheet_name=nombre[:31], index=False)
    return path


def importar_excel(path: Path) -> Dimensionales:
    xl = pd.ExcelFile(path)
    if TABLA_CLASIF not in xl.sheet_names:
        raise ValueError(f"{Path(path).name}: falta la hoja {TABLA_CLASIF}")
    t = {h: xl.parse(h) for h in xl.sheet_names if h in TABLAS}
    for h, df in t.items():
        df.columns = [str(c).strip() for c in df.columns]
    return _desde_tablas(t)
