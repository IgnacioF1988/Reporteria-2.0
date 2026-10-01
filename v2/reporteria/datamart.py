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
    t = DM.listar_versiones(raiz, fecha)
    return [Version(cierre=f["cierre"], numero=int(f["version"]), ruta=Path(f["ruta"]), estado=f["estado"], motivo=f["motivo"], ts=f["ts"],
                    completitud=f["completitud"] or "COMPLETA", corrida=DM.leer_corrida(f["ruta"])) for _, f in t.sort_values("version").iterrows()]


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


def publicar(raiz: Path, fecha: str, borrador: Path, motivo: str = "", reexpresar: bool = False) -> Version:
    """Copia el borrador al datamart como version=NNN. La primera es PUBLICADA; las siguientes exigen `reexpresar` y quedan REEXPRESADA."""
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
    destino = DM.copiar_version(borrador, DM.dir_version(raiz, fecha, numero))
    ts = _ahora()
    corrida.update(version=numero, estado=estado, motivo=motivo, publicado_en=ts)
    DM.escribir_corrida(destino, corrida)
    hist = DM.leer_publicacion(raiz, fecha)
    hist.append({"version": numero, "estado": estado, "motivo": motivo, "ts": ts, "borrador": Path(borrador).name,
                 "code_hash": corrida.get("hashes", {}).get("codigo", ""), "completitud": corrida.get("completitud", "COMPLETA")})
    DM.escribir_publicacion(raiz, fecha, hist)
    return Version(cierre=fecha, numero=numero, ruta=destino, estado=estado, motivo=motivo, ts=ts, completitud=corrida.get("completitud", "COMPLETA"), corrida=corrida)


def comparar_versiones(a: Version, b: Version) -> pd.DataFrame:
    from .salida import comparar_carteras
    pa, pb = a.posiciones(), b.posiciones()
    if pa is None or pb is None:
        raise ValueError("alguna de las versiones no tiene posiciones.parquet")
    return comparar_carteras(pa, pb)
