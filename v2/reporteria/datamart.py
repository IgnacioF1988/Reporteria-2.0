"""Versiones de un cierre: borradores (fuera de git), publicación y re-expresión (en git), y la elección de qué versión leer.

Vistas de un cierre: `publicada` = lo reportado (primera PUBLICADA); `ultima` = última verdad (mayor versión, aunque sea
PARCIAL); `version=N`; `conocimiento=D` = la última versión con ts ≤ fin del día D.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .adaptadores import datamart as DM

ESTADOS = ("BORRADOR", "PUBLICADA", "REEXPRESADA")


@dataclass
class Version:
    cierre: str
    numero: int | None                 # None = borrador
    ruta: Path
    estado: str = "BORRADOR"
    motivo: str = ""
    ts: str = ""
    completitud: str = "COMPLETA"
    corrida: dict = field(default_factory=dict)

    @property
    def etiqueta(self) -> str:
        return f"{self.cierre}/v{self.numero:03d} {self.estado}" if self.numero is not None else f"{self.cierre}/borrador {self.ruta.name}"

    def posiciones(self) -> pd.DataFrame | None:
        return DM.leer_posiciones(self.ruta)

    def hojas(self) -> dict[str, pd.DataFrame]:
        return DM.leer_version(self.ruta)[0]


def _ahora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def versiones_de(raiz: Path, fecha: str) -> list[Version]:
    """Versiones de una fecha en cualquiera de los dos layouts, ordenadas por número (el número es único por fecha)."""
    t = DM.listar_versiones(raiz, fecha)
    return [Version(cierre=f["cierre"], numero=int(f["version"]), ruta=Path(f["ruta"]), estado=f["estado"], motivo=f["motivo"], ts=f["ts"],
                    completitud=f["completitud"] or "COMPLETA", corrida={**DM.leer_corrida(f["ruta"]), "_layout": f["layout"]})
            for _, f in t.sort_values("version").iterrows()]


def borradores(dir_borradores: Path, fecha: str) -> list[Version]:
    base = Path(dir_borradores)
    if not base.is_dir():
        return []
    out = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.startswith("borrador_") and (d / DM.CORRIDA).exists():
            c = DM.leer_corrida(d)
            out.append(Version(cierre=fecha, numero=None, ruta=d, estado="BORRADOR", ts=c.get("ts", ""), completitud=c.get("completitud", "COMPLETA"), corrida=c))
    return out


def resolver_version(raiz: Path, fecha: str, publicada: bool = False, version: int | None = None, conocimiento: str | None = None) -> Version | None:
    """Default: última verdad. `publicada`: la primera PUBLICADA. `version`: ese número. `conocimiento` (YYYYMMDD): última con ts ≤ fin de ese día."""
    lista = versiones_de(raiz, fecha)
    if not lista:
        return None
    if version is not None:
        return next((v for v in lista if v.numero == version), None)
    if publicada:
        return next((v for v in lista if v.estado == "PUBLICADA"), None)
    if conocimiento:
        tope = pd.Timestamp(conocimiento) + pd.Timedelta(days=1)
        validas = [v for v in lista if v.ts and pd.Timestamp(v.ts) < tope]
        return validas[-1] if validas else None
    return lista[-1]


def ultima_verdad(raiz: Path, fecha: str | None) -> Version | None:
    return resolver_version(raiz, fecha) if fecha else None


def publicar(raiz: Path, fecha: str, borrador: Path, motivo: str = "", reexpresar: bool = False, modo: str = "mensual") -> Version:
    """Copia el borrador al datamart como nueva versión. La primera es PUBLICADA; las siguientes exigen `reexpresar` y quedan REEXPRESADA.

    `modo="mensual"`: `cierres/cierre=F/version=NNN` + publicacion.json (layout en git). `modo="diario"`: `diario/fecha=F/corrida=NNN`
    + estado.json con todos los fondos de la corrida apuntando a ella (H10b afina el estado por fondo)."""
    corrida = DM.leer_corrida(borrador)
    if str(corrida.get("fecha", fecha)) != str(fecha):
        raise ValueError(f"el borrador es del cierre {corrida.get('fecha')}, no de {fecha}")
    previas = versiones_de(raiz, fecha)
    if previas and not reexpresar:
        raise ValueError(f"el cierre {fecha} ya tiene {len(previas)} versión(es) (publicada v{previas[0].numero:03d}); use --reexpresar con --motivo")
    if previas and not motivo:
        raise ValueError("una re-expresión necesita --motivo")
    numero = (previas[-1].numero + 1) if previas else 1
    estado = "REEXPRESADA" if previas else "PUBLICADA"
    ts = _ahora()
    if modo == "diario":
        destino = DM.copiar_version(borrador, DM.dir_corrida(raiz, fecha, numero))
    else:
        destino = DM.copiar_version(borrador, DM.dir_version(raiz, fecha, numero))
    corrida.update(version=numero, estado=estado, motivo=motivo, publicado_en=ts)
    DM.escribir_corrida(destino, corrida)
    entrada = {"version": numero, "estado": estado, "motivo": motivo, "ts": ts, "borrador": Path(borrador).name,
               "code_hash": corrida.get("hashes", {}).get("codigo", ""), "completitud": corrida.get("completitud", "COMPLETA")}
    if modo == "diario":
        est = DM.leer_estado(raiz, fecha)
        est["corridas"][f"{numero:03d}"] = entrada
        pos = DM.leer_posiciones(destino)
        for fid in (sorted(int(x) for x in pos["ID_Fund"].dropna().unique()) if pos is not None else []):
            f = est["fondos"].setdefault(str(fid), {"historial": []})
            f["historial"].append({"corrida": numero, "estado": estado, "motivo": motivo, "ts": ts, "bloqueos": []})
            f.update(corrida=numero, estado=estado)
        DM.escribir_estado(raiz, fecha, est)
    else:
        hist = DM.leer_publicacion(raiz, fecha)
        hist.append(entrada)
        DM.escribir_publicacion(raiz, fecha, hist)
    return Version(cierre=fecha, numero=numero, ruta=destino, estado=estado, motivo=motivo, ts=ts, completitud=corrida.get("completitud", "COMPLETA"), corrida=corrida)


def migrar_a_diario(origen: Path, destino: Path) -> list[str]:
    """Copia el datamart mensual (cierres/, maestros/, declaraciones/) al layout diario en `destino` (el share). Idempotente."""
    hechos = []
    for f in DM.cierres(origen):
        est = DM.leer_estado(destino, f)
        for v in versiones_de(origen, f):
            if v.corrida.get("_layout") == "diario":
                continue
            d = DM.dir_corrida(destino, f, v.numero)
            if d.exists():
                continue
            DM.copiar_version(v.ruta, d)
            est["corridas"][f"{v.numero:03d}"] = {"version": v.numero, "estado": v.estado, "motivo": v.motivo, "ts": v.ts,
                                                   "completitud": v.completitud, "origen": "migrado de cierres/"}
            pos = v.posiciones()
            for fid in (sorted(int(x) for x in pos["ID_Fund"].dropna().unique()) if pos is not None else []):
                fo = est["fondos"].setdefault(str(fid), {"historial": []})
                fo["historial"].append({"corrida": v.numero, "estado": v.estado, "motivo": v.motivo, "ts": v.ts, "bloqueos": []})
                fo.update(corrida=v.numero, estado=v.estado)
            hechos.append(f"{f}/v{v.numero:03d}")
        DM.escribir_estado(destino, f, est)
    for sub in ("maestros", "declaraciones"):
        o, d = Path(origen) / sub, Path(destino) / sub
        if o.is_dir():
            import shutil
            shutil.copytree(o, d, dirs_exist_ok=True)
            hechos.append(sub)
    return hechos


def comparar_versiones(a: Version, b: Version) -> pd.DataFrame:
    from .salida import comparar_carteras
    pa, pb = a.posiciones(), b.posiciones()
    if pa is None or pb is None:
        raise ValueError("alguna de las versiones no tiene posiciones.parquet")
    return comparar_carteras(pa, pb)
