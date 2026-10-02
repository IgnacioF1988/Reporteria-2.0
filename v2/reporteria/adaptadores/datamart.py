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
    """Escritura atómica: todo va a `<dir>.tmp/`, `corrida.json` al final y rename. Una caída deja un `.tmp` que se ignora."""
    dir_ = Path(dir_)
    tmp = dir_.with_name(dir_.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    for nombre, df in hojas.items():
        escribir_tabla(tmp / f"{nombre}.parquet", df)
    if posiciones is not None:
        escribir_tabla(tmp / f"{TABLA_POSICIONES}.parquet", posiciones)
    corrida = {**corrida, "hojas": list(hojas)}
    (tmp / CORRIDA).write_text(json.dumps(corrida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if dir_.exists():
        shutil.rmtree(dir_)
    tmp.rename(dir_)
    return dir_


def _json_atomico(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def leer_corrida(dir_: Path) -> dict:
    return json.loads((Path(dir_) / CORRIDA).read_text(encoding="utf-8"))


def escribir_corrida(dir_: Path, corrida: dict) -> None:
    _json_atomico(Path(dir_) / CORRIDA, corrida)


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
    _json_atomico(dir_cierre(raiz, fecha) / PUBLICACION, historial)


def cierres(raiz: Path) -> list[str]:
    """Fechas con layout mensual (`cierres/cierre=F`, versionado en git)."""
    base = Path(raiz) / "cierres"
    if not base.is_dir():
        return []
    return sorted(d.name.split("=", 1)[1] for d in base.iterdir() if d.is_dir() and d.name.startswith("cierre="))


# ── layout diario (share): diario/fecha=F/corrida=NNN/ + estado.json ───────────────────────────────────────────────────
ESTADO = "estado.json"


def dir_fecha(raiz: Path, fecha: str) -> Path:
    return Path(raiz) / "diario" / f"fecha={fecha}"


def dir_corrida(raiz: Path, fecha: str, numero: int) -> Path:
    return dir_fecha(raiz, fecha) / f"corrida={numero:03d}"


def fechas_diario(raiz: Path) -> list[str]:
    base = Path(raiz) / "diario"
    if not base.is_dir():
        return []
    return sorted(d.name.split("=", 1)[1] for d in base.iterdir() if d.is_dir() and d.name.startswith("fecha="))


def fechas(raiz: Path) -> list[str]:
    """Todas las fechas con alguna versión, en cualquiera de los dos layouts."""
    return sorted(set(cierres(raiz)) | set(fechas_diario(raiz)))


def leer_estado(raiz: Path, fecha: str) -> dict:
    """estado.json de una fecha: {"corridas": {"001": {estado, motivo, ts}}, "fondos": {"20": {"corrida": 1, "estado": …, "historial": [...]}}, …}."""
    p = dir_fecha(raiz, fecha) / ESTADO
    if not p.exists():
        return {"corridas": {}, "fondos": {}}
    e = json.loads(p.read_text(encoding="utf-8"))
    e.setdefault("corridas", {})
    e.setdefault("fondos", {})
    return e


def escribir_estado(raiz: Path, fecha: str, estado: dict) -> None:
    _json_atomico(dir_fecha(raiz, fecha) / ESTADO, estado)


def _es_version(d: Path, prefijo: str) -> bool:
    return d.is_dir() and d.name.startswith(prefijo) and not d.name.endswith(".tmp") and (d / CORRIDA).exists()


def limpiar_tmp(raiz: Path) -> list[str]:
    """Borra carpetas `*.tmp` (corridas interrumpidas) y devuelve sus nombres."""
    out = []
    for p in Path(raiz).rglob("*.tmp"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            out.append(str(p))
    return out


def listar_versiones(raiz: Path, fecha: str | None = None) -> pd.DataFrame:
    """Una fila por versión en cualquiera de los dos layouts: cierre, version, estado, motivo, ts, completitud, n_pendientes, anterior, ruta, layout."""
    filas = []
    for f in ([fecha] if fecha else fechas(raiz)):
        base = dir_cierre(raiz, f)
        if base.is_dir():
            pub = {int(e["version"]): e for e in leer_publicacion(raiz, f)}
            for d in sorted(base.iterdir()):
                if _es_version(d, "version="):
                    n = int(d.name.split("=", 1)[1])
                    filas.append(_fila_version(f, n, d, pub.get(n, {}), "cierres"))
        base = dir_fecha(raiz, f)
        if base.is_dir():
            est = leer_estado(raiz, f)["corridas"]
            for d in sorted(base.iterdir()):
                if _es_version(d, "corrida="):
                    n = int(d.name.split("=", 1)[1])
                    filas.append(_fila_version(f, n, d, est.get(f"{n:03d}", est.get(str(n), {})), "diario"))
    return pd.DataFrame(filas, columns=["cierre", "version", "estado", "motivo", "ts", "completitud", "n_pendientes", "anterior", "ruta", "layout"])


def _fila_version(f: str, n: int, d: Path, e: dict, layout: str) -> dict:
    c = leer_corrida(d)
    ant = c.get("anterior_version") or {}
    return dict(cierre=f, version=n, estado=e.get("estado", c.get("estado", "")), motivo=e.get("motivo", c.get("motivo", "")),
                ts=e.get("ts", c.get("ts", "")), completitud=c.get("completitud", ""), n_pendientes=c.get("n_pendientes", 0),
                anterior=f"{ant.get('cierre', '')}/v{ant.get('version', '')}" if ant.get("cierre") else ant.get("origen", ""),
                ruta=str(d), layout=layout)


def ultima_corrida(raiz: Path, fecha: str) -> int:
    t = listar_versiones(raiz, fecha)
    return int(t["version"].max()) if len(t) else 0


class Lock:
    """Candado de escritura del datamart (`<raiz>/.lock`, O_CREAT|O_EXCL): host, pid y hora; vence a `horas`."""

    def __init__(self, raiz: Path, horas: float = 6.0, nombre: str = "diario"):
        self.path, self.horas, self.nombre = Path(raiz) / ".lock", horas, nombre
        self.adquirido = False

    def _vencido(self) -> bool:
        import time
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return time.time() - float(info.get("epoch", 0)) > self.horas * 3600
        except Exception:                      # noqa: BLE001 — lock ilegible = vencido
            return True

    def __enter__(self):
        import os
        import socket
        import time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self._vencido():
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f"datamart bloqueado por otra corrida: {self.path.read_text(encoding='utf-8')}") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"host": socket.gethostname(), "pid": os.getpid(), "epoch": time.time(), "nombre": self.nombre,
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        self.adquirido = True
        return self

    def __exit__(self, *exc):
        if self.adquirido:
            self.path.unlink(missing_ok=True)
            self.adquirido = False
        return False


def copiar_version(origen: Path, destino: Path) -> Path:
    """Copia atómica (a `.tmp` y rename)."""
    destino = Path(destino)
    if destino.exists():
        raise FileExistsError(f"la versión ya existe: {destino}")
    tmp = destino.with_name(destino.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(origen, tmp)
    tmp.rename(destino)
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
    """Huella conjunta de una carpeta (nombre + tamaño + mtime de cada archivo) y número de archivos; ('', 0) si no existe.

    Por `stat`, sin leer contenido: la caché del cierre vive en un share (cientos de CSV de Bloomberg y los de Facts con cientos de
    miles de filas) y leerla entera por red tardaba minutos al escribir cada borrador."""
    if dir_ is None or not Path(dir_).is_dir():
        return "", 0
    h, n = hashlib.sha256(), 0
    for p in sorted(Path(dir_).rglob(patron)):
        st = p.stat()
        if stat_es_archivo(st):
            h.update(f"{p.relative_to(dir_).as_posix()}|{st.st_size}|{st.st_mtime_ns}".encode())
            n += 1
    return (h.hexdigest()[:16] if n else ""), n


def stat_es_archivo(st) -> bool:
    import stat as _stat
    return _stat.S_ISREG(st.st_mode)


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


# ── vistas DuckDB sobre el datamart (consulta, BI provisional) ─────────────────────────────────────────────────────────
def vistas(raiz: Path):
    """Conexión DuckDB en memoria con: `corridas` (una fila por versión/corrida), `estado` (fecha × fondo: estado, corrida,
    publicada, bloqueos), `posiciones_diarias` (todas las posiciones de todas las corridas, con fecha y corrida),
    `publicadas` (solo lo oficial: corrida = publicada del fondo) y `ultima_verdad` (corrida = puntero del fondo, incluye PROVISORIO)."""
    from ..publicacion import COLS_ESTADO, estado_todos
    duckdb = _duckdb()
    con = duckdb.connect()
    corr = listar_versiones(raiz)
    if corr.empty:
        corr = pd.DataFrame(columns=["cierre", "version", "ruta", "estado", "motivo", "ts", "completitud", "layout"])
    con.register("corridas_df", corr.rename(columns={"cierre": "fecha", "version": "corrida"}))
    con.execute("CREATE VIEW corridas AS SELECT fecha, CAST(corrida AS INTEGER) AS corrida, estado, motivo, ts, completitud, layout, ruta FROM corridas_df")
    est = estado_todos(raiz)
    if est.empty:
        est = pd.DataFrame(columns=COLS_ESTADO)
    est = est.astype({"corrida": "Int64", "publicada": "Int64", "ID_Fund": "Int64"})
    con.register("estado_df", est)
    con.execute("CREATE VIEW estado AS SELECT fecha, CAST(ID_Fund AS INTEGER) AS ID_Fund, estado, CAST(corrida AS INTEGER) AS corrida, "
                "CAST(publicada AS INTEGER) AS publicada, bloqueos, ts, motivo FROM estado_df")
    partes = [f"SELECT '{f['cierre']}' AS fecha, {int(f['version'])} AS corrida, * "
              f"FROM read_parquet('{(Path(f['ruta']) / (TABLA_POSICIONES + '.parquet')).as_posix()}', hive_partitioning=false)"
              for _, f in corr.iterrows() if (Path(f["ruta"]) / (TABLA_POSICIONES + ".parquet")).exists()]
    if partes:
        con.execute("CREATE VIEW posiciones_diarias AS " + " UNION ALL BY NAME ".join(partes))
    else:
        con.execute("CREATE VIEW posiciones_diarias AS SELECT '' AS fecha, 0 AS corrida, 0 AS ID_Fund, '' AS Pos_ID WHERE false")
    con.execute("CREATE VIEW publicadas AS SELECT p.* FROM posiciones_diarias p JOIN estado e "
                "ON e.fecha = p.fecha AND e.ID_Fund = p.ID_Fund AND e.publicada = p.corrida")
    con.execute("CREATE VIEW ultima_verdad AS SELECT p.*, e.estado AS estado_fondo, e.bloqueos FROM posiciones_diarias p JOIN estado e "
                "ON e.fecha = p.fecha AND e.ID_Fund = p.ID_Fund AND e.corrida = p.corrida")
    return con


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
