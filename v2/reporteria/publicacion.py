"""Publicación por (fecha, fondo): readiness, puntero por fondo en estado.json y fondos cuya cartera cambió.

Una corrida se calcula entera (todos los fondos) y se guarda inmutable; cada fondo-día apunta a la corrida que lo representa.
`evaluar` dice qué fondos están listos para publicarse y por qué no los demás (bloqueos); `aplicar` escribe los punteros:
- listo y sin publicar antes → PUBLICADA; listo y ya publicado → REEXPRESADA solo si la cartera del fondo cambió (si no, el
  puntero se queda donde estaba: una re-corrida idéntica no re-expresa nada);
- no listo → PROVISORIO apuntando a la corrida nueva (última verdad visible), conservando `publicada` en la última oficial;
- fondo esperado sin posiciones → SIN_CORRIDA.
Bloqueos (decisión del usuario, H10): insumo obligatorio ausente (`parametros.insumos_obligatorios`, default CUBO),
PENDIENTE_TERMINAL, cobertura < `cobertura_min_mv`, AGREGADO_INCONSISTENTE del fondo y alertas con `Bloquea_Publicacion=SI`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .adaptadores import datamart as DM
from .salida import comparar_carteras

COLS_PUBLICACION = ["ID_Fund", "Fondo", "Listo", "Bloqueos", "N_Pendientes", "Cobertura", "MV_Activos"]
COLS_ESTADO = ["fecha", "ID_Fund", "estado", "corrida", "publicada", "bloqueos", "ts", "motivo"]
ESTADOS_FONDO = ("PUBLICADA", "REEXPRESADA", "PROVISORIO", "SIN_CORRIDA")
OFICIALES = ("PUBLICADA", "REEXPRESADA")
SEP = ";"


def esperados(fondos: pd.DataFrame | None) -> list[int]:
    """Fondos que deben tener corrida cada día: `Activo_MantenedorFondos = 1` en dim_fondos (sin la columna: todos)."""
    if fondos is None or fondos.empty or "ID_Fund" not in fondos.columns:
        return []
    f = fondos
    if "Activo_MantenedorFondos" in f.columns:
        act = pd.to_numeric(f["Activo_MantenedorFondos"], errors="coerce").fillna(0)
        f = f[act.eq(1)]
    return sorted(int(x) for x in pd.to_numeric(f["ID_Fund"], errors="coerce").dropna().unique())


def _obligatorios(parametros: dict | None) -> list[str]:
    txt = str((parametros or {}).get("insumos_obligatorios", "CUBO") or "CUBO")
    return [x.strip() for x in txt.replace(",", SEP).split(SEP) if x.strip()]


def _bloquean(reglas_alertas: pd.DataFrame | None) -> dict[str, dict]:
    """{Nombre: {ID_Fund|None: bloquea}} desde REGLAS/alertas (la fila por fondo pisa a la global para ese fondo)."""
    out: dict[str, dict] = {}
    if reglas_alertas is None or reglas_alertas.empty or "Bloquea_Publicacion" not in reglas_alertas.columns:
        return out
    for _, r in reglas_alertas.iterrows():
        fid = int(r["ID_Fund"]) if pd.notna(r.get("ID_Fund")) else None
        out.setdefault(str(r["Nombre"]), {})[fid] = bool(r["Bloquea_Publicacion"])
    return out


def _bloquea(reglas: dict[str, dict], nombre: str, fid: int) -> bool:
    por = reglas.get(nombre)
    if not por:
        return False
    return por[fid] if fid in por else por.get(None, False)


def evaluar(pos: pd.DataFrame, alertas: pd.DataFrame | None, resumen: dict | None, insumos: pd.DataFrame | None,
            reglas_alertas: pd.DataFrame | None, parametros: dict | None, esperados_: list[int] | None = None) -> pd.DataFrame:
    """Una fila por fondo (los de la corrida ∪ los esperados): Listo y Bloqueos (texto `A;B`)."""
    resumen = resumen or {}
    cob = {int(k): float(v) for k, v in (resumen.get("cobertura_mv_activos") or {}).items()}
    minimo = float((parametros or {}).get("cobertura_min_mv", 0.95))
    reglas = _bloquean(reglas_alertas)
    fondos_pos = sorted(int(x) for x in pos["ID_Fund"].dropna().unique()) if pos is not None and len(pos) else []
    nombres = {}
    if pos is not None and "Fondo" in pos.columns and len(pos):
        nombres = pos.drop_duplicates("ID_Fund").set_index("ID_Fund")["Fondo"].to_dict()
    globales = []
    if insumos is not None and len(insumos):
        est = insumos.set_index("Insumo")["Estado"].to_dict()
        globales = [f"INSUMO_FALTANTE:{n}" for n in _obligatorios(parametros) if str(est.get(n, "FALTA")) != "OK"]
    al = alertas if alertas is not None and len(alertas) else pd.DataFrame(columns=["Nombre", "Ambito", "ID_Fund"])
    al_fid = pd.to_numeric(al["ID_Fund"], errors="coerce") if "ID_Fund" in al.columns else pd.Series(dtype=float)
    filas = []
    for fid in sorted(set(fondos_pos) | set(esperados_ or [])):
        b = list(globales)
        if fid not in fondos_pos:
            filas.append(dict(ID_Fund=fid, Fondo=nombres.get(fid, ""), Listo=False, Bloqueos="FONDO_SIN_POSICIONES", N_Pendientes=0,
                              Cobertura=float("nan"), MV_Activos=float("nan")))
            continue
        sub = pos[pos["ID_Fund"].eq(fid)]
        n_pend = int(sub["Estado"].eq("PENDIENTE_TERMINAL").sum()) if "Estado" in sub.columns else 0
        if n_pend:
            b.append(f"PENDIENTE_TERMINAL:{n_pend}")
        c = cob.get(fid)
        if c is not None and c < minimo:
            b.append(f"COBERTURA:{c:.4f}<{minimo:g}")
        mias = al[(al_fid.eq(fid)) | (al["Ambito"].astype(str).eq("CORRIDA") if "Ambito" in al.columns else False)] if len(al) else al
        if (mias["Nombre"].astype(str).eq("AGREGADO_INCONSISTENTE") & al_fid.reindex(mias.index).eq(fid)).any():
            b.append("AGREGADO_INCONSISTENTE")
        for nombre in sorted(set(mias["Nombre"].astype(str))):
            if nombre != "AGREGADO_INCONSISTENTE" and _bloquea(reglas, nombre, fid):
                b.append(f"ALERTA:{nombre}")
        mv = float(pd.to_numeric(sub.loc[sub.get("BalanceSheet", pd.Series("Asset", index=sub.index)).astype(str).eq("Asset"), "TotalMVal"], errors="coerce").sum()) \
            if "TotalMVal" in sub.columns else float("nan")
        filas.append(dict(ID_Fund=fid, Fondo=nombres.get(fid, ""), Listo=not b, Bloqueos=SEP.join(b), N_Pendientes=n_pend,
                          Cobertura=c if c is not None else float("nan"), MV_Activos=mv))
    return pd.DataFrame(filas, columns=COLS_PUBLICACION)


def fondos_cambiados(nueva: pd.DataFrame, vieja: pd.DataFrame | None) -> set[int]:
    """Fondos cuya cartera (Yield/Duration/Fuente/Conversion/Estado o posiciones presentes) difiere entre dos corridas."""
    fn = set(int(x) for x in nueva["ID_Fund"].dropna().unique()) if nueva is not None and len(nueva) else set()
    if vieja is None or vieja.empty:
        return fn
    fv = set(int(x) for x in vieja["ID_Fund"].dropna().unique())
    out = fn ^ fv
    for fid in fn & fv:
        if len(comparar_carteras(nueva[nueva["ID_Fund"].eq(fid)], vieja[vieja["ID_Fund"].eq(fid)])):
            out.add(fid)
    return out


def _readiness_de(dir_corrida: Path, pos: pd.DataFrame | None) -> pd.DataFrame:
    """Hoja `publicacion` de la corrida; corridas anteriores a H10b (sin hoja) → todos los fondos listos."""
    p = Path(dir_corrida) / "publicacion.parquet"
    if p.exists():
        t = DM.leer_tabla(p)
        t["Listo"] = t["Listo"].astype(bool)
        t["Bloqueos"] = t["Bloqueos"].fillna("").astype(str)
        return t
    fondos = sorted(int(x) for x in pos["ID_Fund"].dropna().unique()) if pos is not None and len(pos) else []
    return pd.DataFrame({"ID_Fund": fondos, "Fondo": "", "Listo": True, "Bloqueos": "", "N_Pendientes": 0, "Cobertura": float("nan"), "MV_Activos": float("nan")})


def aplicar(raiz: Path, fecha: str, numero: int, motivo: str, ts: str, readiness: pd.DataFrame | None = None) -> dict:
    """Escribe en estado.json el puntero de cada fondo hacia la corrida `numero` según su readiness. Devuelve el resumen por estado."""
    destino = DM.dir_corrida(raiz, fecha, numero)
    pos = DM.leer_posiciones(destino)
    rd = readiness if readiness is not None else _readiness_de(destino, pos)
    est = DM.leer_estado(raiz, fecha)
    viejas: dict[int, pd.DataFrame | None] = {}
    conteo = {e: 0 for e in ESTADOS_FONDO}
    conteo["SIN_CAMBIO"] = 0
    for _, r in rd.iterrows():
        fid = int(r["ID_Fund"])
        f = est["fondos"].setdefault(str(fid), {"historial": []})
        previo = f.get("estado")
        bloqueos = [b for b in str(r["Bloqueos"] or "").split(SEP) if b]
        if not bool(r["Listo"]):
            estado = "SIN_CORRIDA" if "FONDO_SIN_POSICIONES" in bloqueos else "PROVISORIO"
        else:
            cambio = True
            if previo in OFICIALES and f.get("corrida") is not None:
                old = int(f["corrida"])
                if old not in viejas:
                    viejas[old] = DM.leer_posiciones(DM.dir_corrida(raiz, fecha, old))
                sub_n = pos[pos["ID_Fund"].eq(fid)] if pos is not None else None
                sub_v = viejas[old][viejas[old]["ID_Fund"].eq(fid)] if viejas[old] is not None else None
                cambio = bool(fondos_cambiados(sub_n, sub_v))
            if not cambio:
                conteo["SIN_CAMBIO"] += 1
                continue                                                   # idéntica a lo publicado: el puntero no se mueve
            estado = "REEXPRESADA" if f.get("publicada") is not None else "PUBLICADA"
        f["historial"].append({"corrida": numero, "estado": estado, "motivo": motivo, "ts": ts, "bloqueos": bloqueos})
        f.update(corrida=None if estado == "SIN_CORRIDA" else numero, estado=estado, bloqueos=bloqueos, ts=ts)   # sin posiciones: sin puntero
        if estado in OFICIALES:
            f["publicada"] = numero
        conteo[estado] += 1
    est["corridas"].setdefault(f"{numero:03d}", {}).update(fondos=conteo)
    DM.escribir_estado(raiz, fecha, est)
    return conteo


def estado_fondos(raiz: Path, fecha: str) -> pd.DataFrame:
    """Estado por fondo de una fecha (layout diario desde estado.json; layout cierres/: todos los fondos de la última versión)."""
    est = DM.leer_estado(raiz, fecha)
    filas = []
    if est["fondos"]:
        for fid, f in est["fondos"].items():
            filas.append(dict(fecha=fecha, ID_Fund=int(fid), estado=f.get("estado", ""), corrida=f.get("corrida"), publicada=f.get("publicada"),
                              bloqueos=SEP.join(f.get("bloqueos", []) or []), ts=f.get("ts", ""), motivo=(f.get("historial") or [{}])[-1].get("motivo", "")))
        return pd.DataFrame(filas, columns=COLS_ESTADO).sort_values("ID_Fund").reset_index(drop=True)
    t = DM.listar_versiones(raiz, fecha)
    t = t[t["layout"].eq("cierres")] if len(t) else t
    if t.empty:
        return pd.DataFrame(columns=COLS_ESTADO)
    ult = t.sort_values("version").iloc[-1]
    pos = DM.leer_posiciones(Path(ult["ruta"]))
    for fid in (sorted(int(x) for x in pos["ID_Fund"].dropna().unique()) if pos is not None else []):
        filas.append(dict(fecha=fecha, ID_Fund=fid, estado=ult["estado"], corrida=int(ult["version"]), publicada=int(ult["version"]), bloqueos="",
                          ts=ult["ts"], motivo=ult["motivo"]))
    return pd.DataFrame(filas, columns=COLS_ESTADO)


def estado_todos(raiz: Path) -> pd.DataFrame:
    partes = [estado_fondos(raiz, f) for f in DM.fechas(raiz)]
    partes = [p for p in partes if len(p)]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_ESTADO)
