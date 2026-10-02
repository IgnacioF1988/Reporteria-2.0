"""Nocturna diaria (`reporteria diario`), cierre mensual a git y retención.

Una noche: candado → limpiar `.tmp` → fechas pendientes según el calendario (LISTA / SIN_CUBO / AUN_NO_ESPERADA) → por cada
LISTA en orden: `correr(sin_bbg, sin Excel)` y `publicar(modo diario)` → re-evaluar los PROVISORIO de la ventana cuyo bloqueo
ya tiene remedio (llegó el insumo que faltaba o cambió la caché Bloomberg tras una pasada con terminal) → impacto de la
ventana y re-expresión automática → `estado_diario_{hoy}.md` (03_LOGS y <datamart>/estado) → Teams.
Una fecha SIN_CUBO ya esperada, o con CUBO/REGLAS inválidos, no tiene corrida: todos los esperados quedan SIN_CORRIDA con el
motivo y la fecha se vuelve a mirar cada noche hasta que el CUBO llegue bien. El BIX se lee una sola vez por noche (la primera
corrida registra la carga de maestros; las demás usan el estado del datamart).
Códigos: 0 todo publicado · 1 queda algún fondo-día PROVISORIO/SIN_CORRIDA en la ventana · 2 fallo de infraestructura ·
3 candado ajeno vigente.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import calendario as C
from . import datamart as DMV
from . import impacto as IMP
from . import notificacion as NOT
from . import publicacion as PUB
from .adaptadores import datamart as DM
from .adaptadores import dim as DIM
from .config import Rutas
from .log import configurar_log
from .publicacion import OFICIALES

LOCK_HORAS = 6.0


@dataclass
class Noche:
    hoy: str
    fechas: list[dict] = field(default_factory=list)        # {fecha, estado CORRIDA|SIN_CUBO|AUN_NO_ESPERADA|SIN_CORRIDA|LISTA, corrida, fondos, bloqueos, detalle}
    reevaluadas: list[dict] = field(default_factory=list)   # {fecha, disparadores, corrida, fondos}
    reexpresadas: list[str] = field(default_factory=list)   # etiquetas de las versiones re-expresadas por impacto
    marcadas: list[dict] = field(default_factory=list)      # impacto con accion MARCAR (resumen)
    backlog: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=PUB.COLS_ESTADO))
    errores: list[str] = field(default_factory=list)
    codigo: int = 0
    infra: bool = False
    informe: Path | None = None
    teams: dict = field(default_factory=dict)

    def resumen(self) -> dict:
        grupos = []
        if len(self.backlog):
            for (f, e, b), g in self.backlog.groupby(["fecha", "estado", "bloqueos"], sort=True):
                grupos.append(dict(fecha=f, estado=e, bloqueos=b, n=int(len(g)), fondos=",".join(str(int(x)) for x in sorted(g["ID_Fund"]))))
        return dict(hoy=self.hoy, codigo=self.codigo, fechas=self.fechas, reevaluadas=self.reevaluadas, reexpresadas=self.reexpresadas,
                    marcadas=self.marcadas, backlog=grupos, errores=self.errores, informe=str(self.informe) if self.informe else "")

    def hay_algo(self) -> bool:
        return any(f["estado"] != "AUN_NO_ESPERADA" for f in self.fechas) or bool(self.reevaluadas or self.reexpresadas or self.errores or len(self.backlog))


def _txt(fecha) -> str:
    return C.txt(fecha)


def _menos_dias(fecha: str, dias: int) -> str:
    return _txt(pd.Timestamp(fecha) - pd.Timedelta(days=int(dias)))


def _fecha_param(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    try:
        return str(int(float(v)))[:8]
    except (TypeError, ValueError):
        return str(v).strip()[:8]


def _reglas(rutas: Rutas) -> tuple[dict, set, str | None]:
    """(parametros, feriados, error): REGLAS inválido no detiene la noche, pero ninguna fecha puede correr."""
    from .lectura.reglas import leer_reglas
    try:
        rg = leer_reglas(rutas.reglas)
    except Exception as e:                      # noqa: BLE001 — se informa por fecha como REGLAS_INVALIDO
        return {}, set(), f"REGLAS_INVALIDO: {e}"
    return rg.parametros, C.feriados_de(rg.feriados), None


def _esperados(rutas: Rutas) -> list[int]:
    return PUB.esperados(DIM.leer(rutas.dim).fondos)


def _conteo(raiz: Path, fecha: str) -> dict:
    t = PUB.estado_fondos(raiz, fecha)
    return {k: int(v) for k, v in t["estado"].value_counts().items()} if len(t) else {}


def _bloqueos_top(raiz: Path, fecha: str, n: int = 3) -> list[str]:
    t = PUB.estado_fondos(raiz, fecha)
    t = t[~t["estado"].isin(OFICIALES)] if len(t) else t
    if t.empty:
        return []
    c = pd.Series([b for s in t["bloqueos"] for b in str(s).split(PUB.SEP) if b]).value_counts()
    return [f"{k} ×{int(v)}" for k, v in c.head(n).items()]


def _clasificar(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, FileNotFoundError):
        return "INSUMO_AUSENTE"
    if isinstance(e, ValueError) and msg.startswith("CUBO"):
        return "CUBO_INVALIDO"
    if isinstance(e, ValueError) and msg.startswith("REGLAS"):
        return "REGLAS_INVALIDO"
    return "ERROR"


def _leer_cubo(rf: Rutas):
    """El CUBO se valida aquí (y se inyecta a `correr`) para distinguir CUBO_INVALIDO de cualquier otro error."""
    from .lectura.cubo import leer_cubo
    try:
        return leer_cubo(rf.cubo), None
    except Exception as e:                      # noqa: BLE001 — archivo corrupto, columnas ausentes, Excel a medio copiar…
        return None, f"CUBO_INVALIDO: {e}"


def disparadores(rf: Rutas, v: DMV.Version, bloqueos: set[str]) -> list[str]:
    """Bloqueos de una fecha que ya tienen remedio: el insumo faltante existe hoy, o la caché Bloomberg cambió (pasada con terminal)."""
    out = []
    rutas_insumos = {**rf.obligatorias(), **rf.opcionales()}
    hp = v.ruta / DM.INSUMOS / "hashes.json"
    hashes = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    for b in sorted(bloqueos):
        if b.startswith("INSUMO_FALTANTE:"):
            p = rutas_insumos.get(b.split(":", 1)[1])
            if p is not None and Path(p).exists():
                out.append(b)
        elif b.startswith("PENDIENTE_TERMINAL") and "PENDIENTE_TERMINAL" not in out:
            if DM.hash_carpeta(rf.cache)[0] != (hashes.get("CACHE") or {}).get("hash"):
                out.append("PENDIENTE_TERMINAL")
    return out


def backlog(raiz: Path, hoy: str, ventana: int) -> pd.DataFrame:
    """Fondo-días de la ventana (hoy−ventana ≤ fecha < hoy) que no están PUBLICADA/REEXPRESADA."""
    t = PUB.estado_todos(raiz)
    if t.empty:
        return t
    t = t[(t["fecha"] >= _menos_dias(hoy, ventana)) & (t["fecha"] < hoy) & ~t["estado"].isin(OFICIALES)]
    return t.reset_index(drop=True)


def reevaluar(rutas_base: Rutas, hoy: str, ventana: int, excluir: set[str], opciones, bbg_factory, cargado: list, log,
              errores: list[str], dry_run: bool = False) -> list[dict]:
    """Re-corre (`recalcular`) cada fecha de la ventana con fondos sin publicar cuyo bloqueo ya tiene remedio."""
    raiz = rutas_base.datamart
    desde = _menos_dias(hoy, ventana)
    out = []
    for f in DM.fechas(raiz):
        if f < desde or f >= hoy or f in excluir:
            continue
        est = PUB.estado_fondos(raiz, f)
        no = est[~est["estado"].isin(OFICIALES)] if len(est) else est
        if no.empty:
            continue
        v = DMV.ultima_verdad(raiz, f)
        if v is None:                                   # sin corrida (SIN_CUBO / inválido): la rehace el calendario cuando llegue el CUBO
            continue
        rf = IMP.rutas_de(rutas_base, f)
        disp = disparadores(rf, v, {b for s in no["bloqueos"] for b in str(s).split(PUB.SEP) if b})
        if not disp:
            continue
        if dry_run:
            out.append(dict(fecha=f, disparadores=disp, corrida=None, fondos={}))
            continue
        log.info("re-evaluando %s: %s", f, disp)
        try:
            w = IMP.recalcular(rf, f"auto: {', '.join(disp)}", bbg=bbg_factory(rf) if bbg_factory else None, fx=opciones.fx, facts=opciones.facts,
                               sin_cargar_maestros=cargado[0], log=log)
            cargado[0] = True
            out.append(dict(fecha=f, disparadores=disp, corrida=w.numero, fondos=_conteo(raiz, f)))
            log.info("re-evaluada %s → %s %s", f, w.etiqueta, out[-1]["fondos"])
        except Exception as e:                      # noqa: BLE001 — una fecha no frena a las demás
            log.exception("re-evaluación de %s falló", f)
            errores.append(f"{f}: re-evaluación falló: {type(e).__name__}: {e}")
    return out


def diario(rutas_base: Rutas, hoy: str | None = None, hasta: str | None = None, dry_run: bool = False, opciones=None, bbg_factory=None,
           teams_url: str | None = None, log=None) -> Noche:
    """La nocturna completa. `rutas_base` es `Rutas.desde_env(hoy)` en modo diario; cada fecha deriva con `impacto.rutas_de`.
    `dry_run`: solo el plan (fechas pendientes, re-evaluaciones con disparador, backlog) y el JSON de Teams; no toca el datamart."""
    from .pipeline import Opciones, correr
    hoy = _txt(hoy or dt.date.today())
    raiz = rutas_base.datamart
    if rutas_base.modo != "diario":
        raise ValueError(f"`diario` requiere REPORTERIA_MODO=diario (modo actual: {rutas_base.modo})")
    opciones = opciones or Opciones()
    log = log or configurar_log(rutas_base.logs.parent / "diario", "reporteria.diario", prefijo="diario")
    log.propagate = False
    n = Noche(hoy=hoy)
    log.info("nocturna hoy=%s datamart=%s%s", hoy, raiz, " (dry-run)" if dry_run else "")
    parametros, feriados, err_reglas = _reglas(rutas_base)
    ventana = IMP.ventana_de(rutas_base, parametros) or IMP.VENTANA_DEFAULT
    try:
        esperados = _esperados(rutas_base)
    except Exception as e:                          # noqa: BLE001 — sin dimensionales no hay esperados ni corrida posible
        log.exception("dimensionales ilegibles")
        n.errores.append(f"dimensionales ilegibles ({rutas_base.dim}): {e}")
        n.codigo, n.infra = 2, True
        return n
    con_corrida = [f for f in DM.fechas(raiz) if DM.ultima_corrida(raiz, f) > 0]
    ini = _fecha_param(parametros.get("nocturna_desde")) or (min(con_corrida) if con_corrida else None)
    inicio = max(_menos_dias(hoy, ventana), ini) if ini else None
    pend = C.pendientes(con_corrida, hoy, C.existe_cubo_en(rutas_base.cubo.parent), feriados, inicio=inicio)
    if hasta:
        pend = pend[pend["fecha"] <= str(hasta)]
    log.info("pendientes: %s", pend[["fecha", "estado"]].values.tolist())
    if dry_run:
        n.fechas = [dict(fecha=p["fecha"], estado=p["estado"], esperada_el=p["esperada_el"]) for _, p in pend.iterrows()]
        n.reevaluadas = reevaluar(rutas_base, hoy, ventana, set(), opciones, bbg_factory, [True], log, n.errores, dry_run=True)
        n.backlog = backlog(raiz, hoy, ventana)
        n.codigo = 1 if len(n.backlog) else 0
        n.informe = _informe(n, rutas_base, en_datamart=False)
        n.teams = NOT.teams(n.resumen(), teams_url, rutas_base.logs.parent, dry_run=True, log=log)
        return n
    try:
        lock = DM.Lock(raiz, horas=float(parametros.get("lock_horas", LOCK_HORAS)), nombre="diario").__enter__()
    except RuntimeError as e:
        log.error("%s", e)
        n.errores.append(str(e))
        n.codigo = 3
        return n
    try:
        for t in DM.limpiar_tmp(raiz):
            log.warning("corrida interrumpida eliminada: %s", t)
        ts = DMV._ahora()
        cargado = [False]                               # el BIX se registra con la primera corrida de la noche
        procesadas = set()
        for _, p in pend.iterrows():
            f, estado = p["fecha"], p["estado"]
            if estado == "AUN_NO_ESPERADA":
                n.fechas.append(dict(fecha=f, estado=estado, esperada_el=p["esperada_el"]))
                continue
            procesadas.add(f)
            if estado == "SIN_CUBO":
                PUB.marcar_sin_corrida(raiz, f, esperados, "SIN_CUBO", ts)
                n.fechas.append(dict(fecha=f, estado="SIN_CUBO", esperada_el=p["esperada_el"], fondos={"SIN_CORRIDA": len(esperados)}))
                log.warning("%s: sin CUBO (se esperaba el %s): %d fondos SIN_CORRIDA", f, p["esperada_el"], len(esperados))
                continue
            rf = IMP.rutas_de(rutas_base, f)
            cubo, detalle = _leer_cubo(rf)
            if err_reglas:
                detalle = err_reglas
            if detalle is None:
                try:
                    opc = dataclasses.replace(opciones, sin_bbg=True, excel=False, sin_cargar_maestros=cargado[0], cubo=cubo,
                                              bbg=bbg_factory(rf) if bbg_factory else None, motivo=f"nocturna {hoy}")
                    res = correr(rf, opc)
                    cargado[0] = True
                    v = DMV.publicar(raiz, f, res.borrador, motivo=f"nocturna {hoy}", reexpresar=bool(DMV.versiones_de(raiz, f)), modo="diario")
                    n.fechas.append(dict(fecha=f, estado="CORRIDA", corrida=v.numero, fondos=_conteo(raiz, f), bloqueos=_bloqueos_top(raiz, f)))
                    log.info("%s → %s %s", f, v.etiqueta, n.fechas[-1]["fondos"])
                    continue
                except Exception as e:                  # noqa: BLE001 — esa fecha no existe; se sigue con las demás
                    tipo = _clasificar(e)
                    detalle = f"{tipo}: {type(e).__name__ + ': ' if tipo == 'ERROR' else ''}{e}"
                    if tipo == "ERROR":
                        n.infra = True
                        log.exception("%s: corrida fallida", f)
            detalle = detalle.replace(PUB.SEP, ",")[:200]
            PUB.marcar_sin_corrida(raiz, f, esperados, detalle, ts)
            n.fechas.append(dict(fecha=f, estado="SIN_CORRIDA", detalle=detalle, fondos={"SIN_CORRIDA": len(esperados)}))
            n.errores.append(f"{f}: {detalle}")
            log.error("%s: %s → %d fondos SIN_CORRIDA", f, detalle, len(esperados))
        n.reevaluadas = reevaluar(rutas_base, hoy, ventana, procesadas, opciones, bbg_factory, cargado, log, n.errores)
        try:
            imp = IMP.impacto(rutas_base, hoy=hoy)
            if len(imp):
                n.marcadas = IMP.resumen_impacto(imp[imp["accion"].eq("MARCAR")]).to_dict("records")
                hechos = IMP.reexpresar_impactados(rutas_base, imp, bbg_factory=bbg_factory, fx=opciones.fx, hoy=hoy, log=log,
                                                   sin_cargar_maestros=cargado[0])
                n.reexpresadas = [h.etiqueta for h in hechos]
        except Exception as e:                          # noqa: BLE001 — el impacto nunca invalida lo ya publicado
            log.exception("impacto")
            n.errores.append(f"impacto: {type(e).__name__}: {e}")
    finally:
        lock.__exit__(None, None, None)
    n.backlog = backlog(raiz, hoy, ventana)
    n.codigo = 2 if n.infra else (1 if len(n.backlog) else 0)
    n.informe = _informe(n, rutas_base)
    n.teams = NOT.teams(n.resumen(), teams_url, rutas_base.logs.parent, log=log) if n.hay_algo() else {"enviado": False, "motivo": "nada que informar"}
    log.info("fin: código %d; backlog %d fondo-día; informe %s", n.codigo, len(n.backlog), n.informe)
    return n


def _informe(n: Noche, rutas_base: Rutas, en_datamart: bool = True) -> Path:
    texto = NOT.markdown(n.resumen())
    destino = rutas_base.logs.parent / f"estado_diario_{n.hoy}{'' if en_datamart else '_dry-run'}.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    if en_datamart:
        copia = Path(rutas_base.datamart) / "estado" / destino.name
        copia.parent.mkdir(parents=True, exist_ok=True)
        copia.write_text(texto, encoding="utf-8")
    return destino


# ── cierre mensual: la última corrida del último día calendario, al layout de git ─────────────────────────────────────
def cierre_mensual(rutas: Rutas, fecha: str, destino: Path, forzar: bool = False) -> dict:
    """Copia la última corrida de `fecha` (contiene la última verdad de todos los fondos: un fondo solo se queda en una corrida
    anterior cuando la nueva es idéntica para él) a `destino/cierres/cierre=F/version=NNN` con `publicacion.json` y marca
    `cierre_mensual=true` en el estado.json del share. Se niega si hay fondos esperados sin publicar, salvo `forzar`. Idempotente."""
    raiz = Path(rutas.datamart)
    destino = Path(destino)
    if not C.es_fin_de_mes(fecha):
        raise ValueError(f"{fecha} no es el último día calendario del mes")
    est = PUB.estado_fondos(raiz, fecha)
    numero_share = DM.ultima_corrida(raiz, fecha)
    if est.empty or numero_share == 0:
        raise ValueError(f"{fecha} no tiene corrida en {raiz}")
    no = est[~est["estado"].isin(OFICIALES)]
    if len(no) and not forzar:
        raise ValueError(f"{fecha}: fondos sin publicar {dict(zip(no['ID_Fund'].astype(int), no['estado']))}; con --forzar se copia igual")
    previas = DMV.versiones_de(destino, fecha)
    for p in previas:
        if (p.corrida.get("origen_share") or {}).get("corrida") == numero_share:
            return {"copiada": False, "version": p.numero, "corrida": numero_share, "motivo": f"ya copiada como v{p.numero:03d}"}
    numero = (previas[-1].numero + 1) if previas else 1
    estado = "REEXPRESADA" if previas else "PUBLICADA"
    motivo = f"cierre mensual {fecha}: corrida {numero_share:03d} del datamart diario"
    dest = DM.copiar_version(DM.dir_corrida(raiz, fecha, numero_share), DM.dir_version(destino, fecha, numero))
    c = DM.leer_corrida(dest)
    ts = DMV._ahora()
    c.update(version=numero, estado=estado, motivo=motivo, publicado_en=ts, origen_share={"datamart": str(raiz), "corrida": numero_share})
    DM.escribir_corrida(dest, c)
    conteo = {k: int(v) for k, v in est["estado"].value_counts().items()}
    hist = DM.leer_publicacion(destino, fecha)
    hist.append({"version": numero, "estado": estado, "motivo": motivo, "ts": ts, "borrador": f"corrida={numero_share:03d}",
                 "code_hash": c.get("hashes", {}).get("codigo", ""), "completitud": c.get("completitud", "COMPLETA"),
                 "origen": {"datamart": str(raiz), "corrida": numero_share}, "fondos": conteo,
                 "detalle": {str(int(r["ID_Fund"])): r["estado"] for _, r in est.iterrows()}})
    DM.escribir_publicacion(destino, fecha, hist)
    e = DM.leer_estado(raiz, fecha)
    e["cierre_mensual"] = True
    e["cierre_mensual_info"] = {"corrida": numero_share, "git_version": numero, "ts": ts, "destino": str(destino)}
    DM.escribir_estado(raiz, fecha, e)
    return {"copiada": True, "version": numero, "corrida": numero_share, "ruta": str(dest), "fondos": conteo}


# ── retención ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def limpiar(rutas: Rutas, dias: int, hoy: str | None = None, dry_run: bool = False) -> dict[str, list[str]]:
    """Borra, para fechas anteriores a hoy−dias: corridas que ningún fondo apunta ni fue nunca oficial (quedan todas las
    PUBLICADA/REEXPRESADA: la vista por conocimiento las necesita), carpetas de caché y borradores de 02_OUTPUTS; más los `.tmp`."""
    hoy = _txt(hoy or dt.date.today())
    corte = _menos_dias(hoy, dias)
    raiz = Path(rutas.datamart)
    out: dict[str, list[str]] = {"corridas": [], "cache": [], "borradores": [], "tmp": []}
    for f in DM.fechas_diario(raiz):
        if f >= corte:
            continue
        est = DM.leer_estado(raiz, f)
        protegidas = set()
        for fo in est["fondos"].values():
            protegidas |= {int(fo[k]) for k in ("corrida", "publicada") if fo.get(k) is not None}
            protegidas |= {int(h["corrida"]) for h in fo.get("historial", []) if h.get("estado") in OFICIALES and h.get("corrida") is not None}
        for d in sorted(DM.dir_fecha(raiz, f).iterdir()):
            if d.is_dir() and d.name.startswith("corrida=") and not d.name.endswith(".tmp") and int(d.name.split("=", 1)[1]) not in protegidas:
                out["corridas"].append(str(d))
    for base, clave, sub in ((Path(rutas.cache).parent, "cache", ""), (Path(rutas.outputs).parent, "borradores", "borradores")):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if d.is_dir() and d.name.isdigit() and len(d.name) == 8 and d.name < corte and (d / sub).is_dir():
                    out[clave].append(str(d / sub))
    out["tmp"] = [str(p) for p in sorted(raiz.rglob("*.tmp")) if p.is_dir()]
    if not dry_run:
        for lista in out.values():
            for p in lista:
                shutil.rmtree(p, ignore_errors=True)
    return out
