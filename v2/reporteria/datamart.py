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


def anterior_por_fondo(raiz: Path, fecha: str, max_fechas: int = 90) -> tuple[pd.DataFrame | None, dict | None]:
    """Cartera anterior **por fondo**: para cada fondo, su última verdad más reciente con fecha < `fecha` (el puntero `corrida` de
    estado.json en el layout diario; la última versión en cierres/). Puede ser una fecha distinta por fondo (un fondo que faltó un
    día hereda del anterior). Devuelve (posiciones concatenadas con `Fecha_Ant`, {"origen": "DATAMART_POR_FONDO", "fondos": {...}})."""
    from .publicacion import estado_fondos
    previas = [f for f in DM.fechas(raiz) if f < fecha][-max_fechas:]
    if not previas:
        return None, None
    partes, info, vistos = [], {}, set()
    for f in reversed(previas):
        t = estado_fondos(raiz, f)
        t = t[t["corrida"].notna() & ~t["ID_Fund"].isin(vistos)] if len(t) else t
        if t.empty:
            continue
        for corrida, grupo in t.groupby("corrida"):
            d = DM.dir_corrida(raiz, f, int(corrida)) if DM.dir_corrida(raiz, f, int(corrida)).exists() else DM.dir_version(raiz, f, int(corrida))
            pos = DM.leer_posiciones(d)
            if pos is None:
                continue
            fondos = [int(x) for x in grupo["ID_Fund"]]
            sub = pos[pos["ID_Fund"].isin(fondos)]
            if len(sub):
                partes.append(sub.assign(Fecha_Ant=f))
            for fid, est in zip(grupo["ID_Fund"], grupo["estado"]):
                info[int(fid)] = {"cierre": f, "version": int(corrida), "estado": est}
                vistos.add(int(fid))
    if not partes:
        return None, None
    return pd.concat(partes, ignore_index=True), {"origen": "DATAMART_POR_FONDO", "fondos": info}


def publicar(raiz: Path, fecha: str, borrador: Path, motivo: str = "", reexpresar: bool = False, modo: str = "mensual") -> Version:
    """Copia el borrador al datamart como nueva versión. La primera es PUBLICADA; las siguientes exigen `reexpresar` y quedan REEXPRESADA.

    `modo="mensual"`: `cierres/cierre=F/version=NNN` + publicacion.json (layout en git). `modo="diario"`: `diario/fecha=F/corrida=NNN`
    + estado.json con el puntero de cada fondo según su readiness (`publicacion.aplicar`): PUBLICADA / REEXPRESADA solo si la
    cartera del fondo cambió / PROVISORIO con bloqueos / SIN_CORRIDA."""
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
        from . import publicacion
        est = DM.leer_estado(raiz, fecha)
        est["corridas"][f"{numero:03d}"] = entrada
        DM.escribir_estado(raiz, fecha, est)
        entrada["fondos"] = publicacion.aplicar(raiz, fecha, numero, motivo, ts)          # puntero por fondo según su readiness
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
