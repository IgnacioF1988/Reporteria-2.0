"""Frontera Parquet del datamart (vía duckdb, sin pyarrow).

Una versión de cierre es una carpeta inmutable `datamart/cierres/cierre=YYYYMMDD/version=NNN/` con una tabla Parquet por
hoja del reporte, `posiciones.parquet` (todas las columnas de la cartera, no solo las del Excel), `insumos/` (copias de los
archivos sin fecha + hashes de los fechados) y `corrida.json`. Los borradores usan el mismo formato en
`02_OUTPUTS/{F}/borradores/borrador_{ts}/` (fuera de git); `publicar` copia uno al datamart.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

TABLA_POSICIONES = "posiciones"
CORRIDA = "corrida.json"
PUBLICACION = "publicacion.json"
INSUMOS = "insumos"
_VACIA = "_vacia"


def _duckdb():
    import duckdb
    return duckdb


def _canon(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas object → float si todas numéricas, si no texto (None = nulo); nombres de columna como str."""
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for c in out.columns:
        s = out[c]
        if s.dtype == object:
            num = pd.to_numeric(s, errors="coerce")
            if s.notna().any() and num.notna().sum() == s.notna().sum() and not s.dropna().map(lambda x: isinstance(x, str)).any():
                out[c] = num.astype("float64")
            else:
                out[c] = s.where(s.notna(), None).map(lambda x: None if x is None else str(x)).astype(object)
    return out.reset_index(drop=True)


def escribir_tabla(path: Path, df: pd.DataFrame) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = _canon(df) if len(df.columns) else pd.DataFrame({_VACIA: pd.Series(dtype="int64")})
    con = _duckdb().connect()
    try:
        con.register("tabla_df", t)
        con.execute(f"COPY (SELECT * FROM tabla_df) TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        con.close()
    return path


def leer_tabla(path: Path) -> pd.DataFrame:
    con = _duckdb().connect()
    try:
        df = con.execute(f"SELECT * FROM read_parquet('{Path(path).as_posix()}', hive_partitioning=false)").df()
    finally:
        con.close()
    if _VACIA in df.columns:
        return pd.DataFrame()
    for c in df.columns:
        if str(df[c].dtype) in ("str", "string"):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df


def escribir_version(dir_: Path, hojas: dict[str, pd.DataFrame], corrida: dict, posiciones: pd.DataFrame | None = None) -> Path:
    dir_ = Path(dir_)
    dir_.mkdir(parents=True, exist_ok=True)
    for nombre, df in hojas.items():
        escribir_tabla(dir_ / f"{nombre}.parquet", df)
    if posiciones is not None:
        escribir_tabla(dir_ / f"{TABLA_POSICIONES}.parquet", posiciones)
    corrida = {**corrida, "hojas": list(hojas)}
    (dir_ / CORRIDA).write_text(json.dumps(corrida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return dir_


def leer_corrida(dir_: Path) -> dict:
    return json.loads((Path(dir_) / CORRIDA).read_text(encoding="utf-8"))


def escribir_corrida(dir_: Path, corrida: dict) -> None:
    (Path(dir_) / CORRIDA).write_text(json.dumps(corrida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def leer_version(dir_: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None, dict]:
    dir_ = Path(dir_)
    corrida = leer_corrida(dir_)
    hojas = {h: leer_tabla(dir_ / f"{h}.parquet") for h in corrida.get("hojas", []) if (dir_ / f"{h}.parquet").exists()}
    pos = leer_tabla(dir_ / f"{TABLA_POSICIONES}.parquet") if (dir_ / f"{TABLA_POSICIONES}.parquet").exists() else None
    return hojas, pos, corrida


def leer_posiciones(dir_: Path) -> pd.DataFrame | None:
    p = Path(dir_) / f"{TABLA_POSICIONES}.parquet"
    return leer_tabla(p) if p.exists() else None


# ── catálogo (derivado de los corrida.json + publicacion.json; nada append-only compartido) ───────────────────────────
def dir_cierre(raiz: Path, fecha: str) -> Path:
    return Path(raiz) / "cierres" / f"cierre={fecha}"


def dir_version(raiz: Path, fecha: str, numero: int) -> Path:
    return dir_cierre(raiz, fecha) / f"version={numero:03d}"


def leer_publicacion(raiz: Path, fecha: str) -> list[dict]:
    p = dir_cierre(raiz, fecha) / PUBLICACION
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def escribir_publicacion(raiz: Path, fecha: str, historial: list[dict]) -> None:
    p = dir_cierre(raiz, fecha) / PUBLICACION
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(historial, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def cierres(raiz: Path) -> list[str]:
    base = Path(raiz) / "cierres"
    if not base.is_dir():
        return []
    return sorted(d.name.split("=", 1)[1] for d in base.iterdir() if d.is_dir() and d.name.startswith("cierre="))


def listar_versiones(raiz: Path, fecha: str | None = None) -> pd.DataFrame:
    """Una fila por versión: cierre, version, estado, motivo, ts, completitud, n_pendientes, anterior, ruta."""
    filas = []
    for f in ([fecha] if fecha else cierres(raiz)):
        base = dir_cierre(raiz, f)
        if not base.is_dir():
            continue
        pub = {int(e["version"]): e for e in leer_publicacion(raiz, f)}
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name.startswith("version=") and (d / CORRIDA).exists():
                n = int(d.name.split("=", 1)[1])
                c = leer_corrida(d)
                e = pub.get(n, {})
                ant = c.get("anterior_version") or {}
                filas.append(dict(cierre=f, version=n, estado=e.get("estado", c.get("estado", "")), motivo=e.get("motivo", c.get("motivo", "")),
                                  ts=e.get("ts", c.get("ts", "")), completitud=c.get("completitud", ""), n_pendientes=c.get("n_pendientes", 0),
                                  anterior=f"{ant.get('cierre', '')}/v{ant.get('version', '')}" if ant.get("cierre") else ant.get("origen", ""),
                                  ruta=str(d)))
    return pd.DataFrame(filas, columns=["cierre", "version", "estado", "motivo", "ts", "completitud", "n_pendientes", "anterior", "ruta"])


def copiar_version(origen: Path, destino: Path) -> Path:
    destino = Path(destino)
    if destino.exists():
        raise FileExistsError(f"la versión ya existe: {destino}")
    shutil.copytree(origen, destino)
    return destino


# ── hashes e insumos ───────────────────────────────────────────────────────────────────────────────────────────────────
def hash_archivo(path: Path | None) -> str:
    if path is None or not Path(path).exists() or not Path(path).is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()[:16]


def hash_carpeta(dir_: Path | None, patron: str = "*") -> tuple[str, int]:
    """Hash conjunto (nombre + hash de cada archivo) y número de archivos; ('', 0) si no existe."""
    if dir_ is None or not Path(dir_).is_dir():
        return "", 0
    h, n = hashlib.sha256(), 0
    for p in sorted(Path(dir_).rglob(patron)):
        if p.is_file():
            h.update(p.relative_to(dir_).as_posix().encode())
            h.update(hash_archivo(p).encode())
            n += 1
    return (h.hexdigest()[:16] if n else ""), n


def hash_codigo(paquete: Path | None = None) -> str:
    """Hash de todos los .py del paquete `reporteria` (un fix de código = versión distinta de la verdad)."""
    paquete = Path(paquete) if paquete else Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for p in sorted(paquete.rglob("*.py")):
        h.update(p.relative_to(paquete).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def copiar_insumos(rutas, dir_: Path, derivados: dict[str, pd.DataFrame] | None = None) -> dict:
    """Copia a `dir_/insumos/` los archivos sin fecha (REGLAS, RA_TIR, paridades, jsonl, EXCEPCIONES, CSV de dim) y escribe
    `hashes.json` con hashes de todo lo usado (incl. CUBO, JPM, curvas, dim, caché). `derivados` (p. ej. facturas_al_cierre) van a Parquet."""
    dest = Path(dir_) / INSUMOS
    dest.mkdir(parents=True, exist_ok=True)
    copias = {"REGLAS": rutas.reglas, "RA_TIR": rutas.ra, "paridades": rutas.paridades, "bond_schedule": rutas.jsonl}
    for i, p in enumerate(getattr(rutas, "excepciones", ()) or ()):
        copias[f"EXCEPCIONES_{i}"] = p
    hashes = {}
    for nombre, p in copias.items():
        if p is not None and Path(p).exists():
            shutil.copy2(p, dest / Path(p).name)
            hashes[nombre] = {"ruta": str(p), "hash": hash_archivo(p), "copia": Path(p).name}
    csv_dim = Path(rutas.dim).parent / "csv" / "dim_clasificacion.csv"
    if csv_dim.exists():
        shutil.copy2(csv_dim, dest / csv_dim.name)
    for nombre, p in {"CUBO": rutas.cubo, "BD_INSTRUMENTOS": rutas.bd_instr, "HOMOL_INSTRUMENTOS": rutas.homol, "HOMOL_FUNDS": rutas.homol_funds,
                      "JPM": rutas.jpm, "Carga_Indexes": rutas.indexes, "CurvasSoberanas": rutas.curvas_sob, "DIMENSIONALES": rutas.dim,
                      "DEFAULTED": rutas.defaulted, "FACTURAS_xlsx": rutas.facturas}.items():
        if p is not None and Path(p).exists():
            hashes[nombre] = {"ruta": str(p), "hash": hash_archivo(p)}
    h, n = hash_carpeta(rutas.cache)
    hashes["CACHE"] = {"ruta": str(rutas.cache), "hash": h, "archivos": n}
    for nombre, df in (derivados or {}).items():
        if df is not None:
            escribir_tabla(dest / f"{nombre}.parquet", df)
            hashes[nombre] = {"filas": int(len(df)), "copia": f"{nombre}.parquet"}
    (dest / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return hashes


# ── maestros: base + cambios por carga; declaraciones de vigencia ───────────────────────────────────────────────────────
def id_carga(ts=None) -> str:
    import datetime as dt
    return (ts or dt.datetime.now()).strftime("%Y%m%d_%H%M%S")


def dir_maestros(raiz: Path) -> Path:
    return Path(raiz) / "maestros"


def _cargas(dir_: Path) -> list[str]:
    return sorted(d.name.split("=", 1)[1] for d in dir_.iterdir() if d.is_dir() and d.name.startswith("carga=")) if dir_.is_dir() else []


def bases(raiz: Path) -> list[str]:
    return _cargas(dir_maestros(raiz) / "base")


def cargas_cambios(raiz: Path) -> list[str]:
    return _cargas(dir_maestros(raiz) / "cambios")


def escribir_base(raiz: Path, carga: str, tablas: dict[str, pd.DataFrame]) -> Path:
    d = dir_maestros(raiz) / "base" / f"carga={carga}"
    if d.exists():
        raise FileExistsError(f"la base {carga} ya existe")
    for nombre, df in tablas.items():
        escribir_tabla(d / f"{nombre}.parquet", df)
    return d


def leer_base(raiz: Path, conocimiento: str | None = None) -> tuple[str | None, dict[str, pd.DataFrame]]:
    """Última base con carga ≤ conocimiento (todas si None). (None, {}) si no hay."""
    ids = [c for c in bases(raiz) if not conocimiento or c[:8] <= str(conocimiento)[:8]]
    if not ids:
        return None, {}
    d = dir_maestros(raiz) / "base" / f"carga={ids[-1]}"
    return ids[-1], {p.stem: leer_tabla(p) for p in sorted(d.glob("*.parquet"))}


def escribir_cambios(raiz: Path, carga: str, cambios: pd.DataFrame) -> Path:
    d = dir_maestros(raiz) / "cambios" / f"carga={carga}"
    if d.exists():
        raise FileExistsError(f"la carga de cambios {carga} ya existe")
    return escribir_tabla(d / "cambios.parquet", cambios.assign(carga=carga))


def leer_cambios(raiz: Path, desde_base: str | None = None, conocimiento: str | None = None) -> pd.DataFrame:
    """Cambios con carga > base y (si se da) fecha ≤ conocimiento, en orden."""
    from ..maestros_hist import COLS_CAMBIO
    partes = []
    for c in cargas_cambios(raiz):
        if desde_base and c <= desde_base:
            continue
        if conocimiento and c[:8] > str(conocimiento)[:8]:
            continue
        p = dir_maestros(raiz) / "cambios" / f"carga={c}" / "cambios.parquet"
        if p.exists():
            t = leer_tabla(p)
            if len(t):
                partes.append(t.assign(carga=c))
    out = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_CAMBIO)
    for c in COLS_CAMBIO:
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].astype(object).where(out[c].notna(), "").astype(str)
    return out[COLS_CAMBIO].sort_values("carga", kind="stable").reset_index(drop=True)


def ruta_vigencias(raiz: Path) -> Path:
    return Path(raiz) / "declaraciones" / "vigencias.csv"


def leer_vigencias(raiz: Path) -> pd.DataFrame:
    from ..maestros_hist import normalizar_vigencias
    p = ruta_vigencias(raiz)
    return normalizar_vigencias(pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8") if p.exists() else None)


def escribir_vigencias(raiz: Path, vig: pd.DataFrame) -> Path:
    from ..maestros_hist import normalizar_vigencias
    p = ruta_vigencias(raiz)
    p.parent.mkdir(parents=True, exist_ok=True)
    normalizar_vigencias(vig).to_csv(p, index=False, lineterminator="\n", encoding="utf-8")
    return p
