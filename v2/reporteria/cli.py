"""CLI del operador: `reporteria check --fecha 20260731`, `reporteria correr --fecha 20260731 [--sin-bbg]`."""
from __future__ import annotations

from pathlib import Path

import typer

from .config import Rutas

app = typer.Typer(add_completion=False, help="Yield y Duration por posición — cierre mensual")
dim_app = typer.Typer(add_completion=False, help="Dimensionales locales (dimensionales.duckdb): importar, exportar, validar, probar")
app.add_typer(dim_app, name="dim")
maestros_app = typer.Typer(add_completion=False, help="Maestros bitemporales (BD_INSTRUMENTOS/HOMOL en el datamart): cargar, cambios, estado")
app.add_typer(maestros_app, name="maestros")


@app.command()
def check(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"), raiz: Path | None = None):
    """Verifica inputs, REGLAS.xlsx y .env antes de correr."""
    from .lectura.reglas import leer_reglas
    r = Rutas.desde_env(fecha, raiz)
    faltan = 0
    for nombre, ruta in r.obligatorias().items():
        ok = Path(ruta).exists()
        faltan += not ok
        typer.echo(f"  [{'OK ' if ok else 'FALTA'}] {nombre:16} {ruta}")
    for nombre, ruta in r.opcionales().items():
        ok = ruta is not None and Path(ruta).exists()
        typer.echo(f"  [{'OK ' if ok else 'opc. '}] {nombre:16} {ruta or '(no encontrado)'}")
    if Path(r.reglas).exists():
        try:
            rg = leer_reglas(r.reglas)
            n_al = int(rg.alertas["Campo"].ne("").sum()) if len(rg.alertas) else 0
            typer.echo(f"  [OK ] REGLAS.xlsx válido: clasificacion={len(rg.clasificacion)} cajas={len(rg.cajas)} defaulteados={len(rg.defaulteados)} "
                       f"overrides_valor={len(rg.overrides_valor)} overrides_atributo={len(rg.overrides_atributo)} alertas={n_al} "
                       f"(activas {int(rg.alertas.loc[rg.alertas['Campo'].ne(''), 'Activa'].sum()) if n_al else 0}) parametros={len(rg.parametros)}")
        except ValueError as e:
            faltan += 1
            typer.echo(f"  [ERR] REGLAS.xlsx: {e}")
    typer.echo(f"  cierre anterior detectado: {r.fecha_ant or '(ninguno)'}")
    for linea in _estado_raiz(r):
        typer.echo(f"  {linea}")
    for linea in _estado_datamart(r):
        typer.echo(f"  {linea}")
    for linea in _estado_maestros(r):
        typer.echo(f"  {linea}")
    for linea in _estado_dim(r):
        typer.echo(f"  {linea}")
    for linea in _estado_entorno(r):
        typer.echo(f"  {linea}")
    raise typer.Exit(2 if faltan else 0)


def _estado_raiz(r: Rutas) -> list[str]:
    """v2 es autónomo: todo bajo la carpeta del paquete salvo CUBO y BIX (shares)."""
    import os
    from .config import RAIZ_PAQUETE
    out = [f"[OK ] raíz {r.raiz}" + ("" if Path(r.raiz).resolve() == RAIZ_PAQUETE else " (distinta de la carpeta del paquete: solo para pruebas)")]
    if os.environ.get("REPORTERIA_RAIZ"):
        out.append("[AVISO] REPORTERIA_RAIZ ya no se usa: la raíz es siempre la carpeta del paquete (v2); bórrela del .env")
    externos = {"CUBO", "BD_INSTRUMENTOS", "DEFAULTED", "HOMOL_INSTRUMENTOS", "HOMOL_FUNDS"}
    raiz = Path(r.raiz).resolve()
    for nombre, ruta in {**r.obligatorias(), **r.opcionales()}.items():
        if nombre in externos or ruta is None or not Path(ruta).exists():
            continue
        try:
            Path(ruta).resolve().relative_to(raiz)
        except ValueError:
            out.append(f"[AVISO] {nombre} está fuera de la raíz: {ruta}")
    return out


def _estado_datamart(r: Rutas) -> list[str]:
    """Datamart: cierres con versión, estado del cierre actual, borradores sin publicar."""
    from . import datamart as DMV
    from .adaptadores import datamart as DM
    todos = DM.cierres(r.datamart)
    vs = DMV.versiones_de(r.datamart, r.fecha) if r.fecha in todos else []
    bor = DMV.borradores(r.borradores, r.fecha)
    if vs:
        est = f"v{vs[-1].numero:03d} {vs[-1].estado}" + (f" ({vs[-1].completitud})" if vs[-1].completitud != "COMPLETA" else "")
    else:
        est = "sin versión publicada"
    out = [f"[{'OK ' if todos else 'opc. '}] datamart {r.datamart}: {len(todos)} cierre(s) con versión{' (último ' + todos[-1] + ')' if todos else ''}; "
           f"cierre {r.fecha}: {est}; borradores sin publicar: {len(bor)}"]
    if bor and not vs:
        out.append(f"[AVISO] hay {len(bor)} borrador(es) del cierre {r.fecha} sin publicar → reporteria publicar --fecha {r.fecha}")
    return out


def _estado_maestros(r: Rutas) -> list[str]:
    """Maestros en el datamart: base, cargas de cambios, declaraciones vigentes."""
    from .adaptadores import datamart as DM
    from .maestros_hist import vigentes
    bases, cargas = DM.bases(r.datamart), DM.cargas_cambios(r.datamart)
    if not bases:
        return ["[opc. ] maestros: sin base en el datamart → la primera corrida la crea desde el BIX (BD_INSTRUMENTOS, HOMOL)"]
    camb = DM.leer_cambios(r.datamart, bases[-1])
    nv = len(vigentes(DM.leer_vigencias(r.datamart)))
    return [f"[OK ] maestros: base {bases[-1]}, {len(cargas)} carga(s) de cambios ({len(camb)} cambios), {nv} declaración(es) vigente(s)"
            + (f"; última carga {cargas[-1]}" if cargas else "")]


def _estado_dim(r: Rutas) -> list[str]:
    """Dimensionales: existe el duckdb, filas por tabla, fecha de importación, validación, espejo CSV al día."""
    from .adaptadores import dim as DIM
    from .dim import validar
    if not Path(r.dim).exists():
        return [f"[FALTA] dimensionales {r.dim} → reporteria dim importar --bix <carpeta BIX> (ver CHECKLIST)"]
    try:
        d = DIM.leer(r.dim)
    except Exception as e:                      # noqa: BLE001 — diagnóstico
        return [f"[ERR] dimensionales {r.dim}: {e}"]
    nf = int(d.clasificacion["ID_Fund"].notna().sum())
    prob = validar(d.clasificacion, d.catalogos, fondos=set(d.bd_funds()["ID_Fund"]) if len(d.fondos) else None)
    csv_ok = DIM.csv_al_dia(Path(r.dim).parent / "csv", d)
    out = [f"[OK ] dimensionales {Path(r.dim).name}: clasificacion={len(d.clasificacion)} ({len(d.clasificacion) - nf} genéricas, {nf} por fondo) "
           f"fondos={len(d.fondos)} monedas={len(d.monedas)} catálogos={len(d.catalogos)} yld_flag={len(d.yld_flag)}; importado {d.meta.get('importado', '?')}"]
    out.append(f"[{'OK ' if prob.empty else 'ERR'}] dim validar: {'sin problemas' if prob.empty else str(len(prob)) + ' problemas → reporteria dim validar'}")
    if not csv_ok:
        out.append("[AVISO] espejo dim/csv desactualizado → reporteria dim importar --excel <archivo> o dim exportar/importar")
    return out


def _estado_facts(r: Rutas) -> list[str]:
    """Facts: cliente ssh, clave y llave en .env, caché del cierre."""
    import os
    import shutil
    from .adaptadores.facts import FixtureFacts, DEFAULTS
    out = []
    ssh = shutil.which("ssh") is not None
    clave = bool(os.environ.get("MONEDA_BI_PASSWORD"))
    llave = Path(os.environ.get("MONEDA_BI_SSH_KEY") or DEFAULTS["MONEDA_BI_SSH_KEY"]).expanduser()
    listo = ssh and clave and llave.exists()
    out.append(f"[{'OK ' if listo else 'opc. '}] Facts (facturas): ssh {'OK' if ssh else 'NO ENCONTRADO'}, MONEDA_BI_PASSWORD {'definida' if clave else 'FALTA'}, "
               f"llave {llave} {'OK' if llave.exists() else 'FALTA'}{'' if listo else ' → correr con --sin-facts o completar .env (ver CHECKLIST)'}")
    t = FixtureFacts(r.cache, r.fecha).tablas(r.fecha)
    det = f"{len(t['facturas'])} facturas, {len(t['prorrogas'])} prórrogas, {len(t['cambios'])} cambios → --sin-facts posible" if t else "vacía → la corrida baja las tablas"
    out.append(f"[{'OK ' if t else 'opc. '}] caché Facts {r.fecha}: {det}")
    return out


def _migrados_en_reglas(r: Rutas) -> set[str]:
    """Archivos legacy cuyas filas ya están en REGLAS (Comentario 'migrado de X')."""
    import pandas as pd
    if not Path(r.reglas).exists():
        return set()
    out = set()
    try:
        for h, df in pd.read_excel(r.reglas, sheet_name=None).items():
            if "Comentario" in df.columns:
                for c in df["Comentario"].dropna().astype(str):
                    if c.startswith("migrado de "):
                        out.add(c.split("migrado de ", 1)[1].split()[0].rstrip(":;,"))
    except Exception:       # noqa: BLE001 — diagnóstico, nunca bloquea
        pass
    return out


def _estado_entorno(r: Rutas) -> list[str]:
    """Líneas de diagnóstico: .env, xbbg/pyodbc, caché del cierre, antigüedad del jsonl, manuales legacy sin migrar."""
    import importlib.util
    import os
    import time
    out = []
    env = Path(r.raiz) / ".env"
    vars_ = ("BEE_SERVER", "BEE_DB", "BEE_UID", "BEE_PWD", "RUTA_CUBO_DIR", "RUTA_BIX")
    presentes = [v for v in vars_ if os.environ.get(v)]
    out.append(f"[{'OK ' if env.exists() or len(presentes) == len(vars_) else 'opc. '}] .env {'encontrado' if env.exists() else 'no encontrado en la raíz'}; "
               f"variables definidas: {len(presentes)}/{len(vars_)} {sorted(set(vars_) - set(presentes)) or ''}")
    for mod, flag in (("xbbg", "--sin-bbg"), ("pyodbc", "--sin-sql"), ("psycopg2", "--sin-facts")):
        ok = importlib.util.find_spec(mod) is not None
        out.append(f"[{'OK ' if ok else 'opc. '}] {mod:16} {'instalado' if ok else f'no instalado → correr con {flag}'}")
    out.extend(_estado_facts(r))
    cache = Path(r.cache)
    if cache.is_dir():
        bdp, bdh, bds = len(list(cache.glob("bdp_*.csv"))), len(list(cache.glob("bdh_*.csv"))), [d.name for d in cache.glob("bds_*") if d.is_dir()]
        yas = any(cache.glob("bdp_YAS_BOND_YLD_*.csv"))
        out.append(f"[OK ] caché {r.fecha}: {bdp} bdp, {bdh} bdh, bds {bds or 'ninguno'}; {'con' if yas else 'SIN'} yields YAS "
                   f"{'→ --sin-bbg posible' if yas else '→ hace falta terminal o importar-cache-legacy'}")
    else:
        out.append(f"[opc. ] caché {r.fecha}: vacía → la primera corrida necesita terminal (o importar-cache-legacy)")
    if Path(r.jsonl).exists():
        dias = (time.time() - Path(r.jsonl).stat().st_mtime) / 86400
        out.append(f"[{'OK ' if dias <= 35 else 'AVISO'}] bond_schedule.jsonl con {dias:.0f} días de antigüedad{' (> 35: pedir refresco a Geneva)' if dias > 35 else ''}")
    legacy = [p.name for d in (Path(r.reglas).parent, Path(r.jsonl).parent) if d.is_dir()
              for p in list(d.glob("FIP.xlsx")) + list(d.glob("DEFAULTEADOS.xlsx")) + list(d.glob("OVERRIDES.xlsx")) + list(d.glob("Atributos_*.xlsx"))]
    legacy = [n for n in legacy if n not in _migrados_en_reglas(r)]
    if legacy:
        out.append(f"[AVISO] manuales legacy sin migrar: {sorted(set(legacy))} → reporteria migrar-manuales --fecha {r.fecha} --legacy <carpeta>")
    return out


@app.command("facts-probar")
def facts_probar():
    """Abre el túnel SSH a Facts y cuenta filas de bi_facturas / bi_prorrogas / bi_cambios (no escribe caché)."""
    from dotenv import load_dotenv
    from .adaptadores.facts import FactsSql
    load_dotenv()
    try:
        r = FactsSql().resumen()
    except Exception as e:
        typer.echo(f"ERROR: {e}")
        raise typer.Exit(3)
    typer.echo(f"Facts OK: {r['facturas']} facturas, {r['prorrogas']} prórrogas, {r['cambios']} cambios (último cambio {r['ultimo_cambio']})")


@app.command()
def correr(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"), raiz: Path | None = None,
           sin_bbg: bool = typer.Option(False, "--sin-bbg", help="No consulta Bloomberg; usa caché"),
           sin_sql: bool = typer.Option(False, "--sin-sql", help="No consulta beemining; usa paridades y caché"),
           conocimiento: str | None = typer.Option(None, help="Maestros como se conocían al YYYYMMDD (no carga el BIX de hoy)"),
           sin_facts: bool = typer.Option(False, "--sin-facts", help="No consulta la base de Facts; usa caché o FACTURAS_F.xlsx"),
           fecha_ant: str | None = typer.Option(None, help="Forzar cierre anterior YYYYMMDD")):
    """Corre el cierre completo y deja REPORTE_{FECHA}.xlsx en 02_OUTPUTS/{FECHA}."""
    from .pipeline import Opciones, correr as _correr
    r = Rutas.desde_env(fecha, raiz, fecha_ant)
    try:
        res = _correr(r, Opciones(sin_bbg=sin_bbg, sin_sql=sin_sql, sin_facts=sin_facts, conocimiento=conocimiento))
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}")
        raise typer.Exit(2)
    criticas = int((res.alertas["Severidad"] == "CRITICA").sum()) if len(res.alertas) else 0
    typer.echo(f"Listo: {res.excel} | alertas críticas: {criticas}")
    raise typer.Exit(1 if criticas else 0)


@app.command("importar-cache-legacy")
def importar_cache_legacy_cmd(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"),
                              legacy: Path = typer.Option(..., help="Carpeta con METRICAS_/CSHF_ del legacy (o su 02_OUTPUTS)"),
                              raiz: Path | None = None):
    """Siembra 04_CACHE/{FECHA} con las respuestas Bloomberg del pipeline legacy para correr sin terminal."""
    from .legado import importar_cache_legacy
    r = Rutas.desde_env(fecha, raiz)
    n = importar_cache_legacy(legacy, r.cache, fecha)
    for k, v in n.items():
        typer.echo(f"  {k:32} {v:>6}")
    typer.echo(f"caché en {r.cache}")


@app.command("migrar-manuales")
def migrar_manuales_cmd(fecha: str = typer.Option(..., help="Cierre YYYYMMDD (para ubicar la raíz)"),
                        legacy: Path = typer.Option(..., help="Carpeta del repo legacy (o con FIP/DEFAULTEADOS/OVERRIDES/Atributos_*)"),
                        aplicar: bool = typer.Option(False, "--aplicar", help="Fusionar en REGLAS.xlsx (deja REGLAS_backup_*.xlsx)"),
                        incluir_defaulteados: bool = typer.Option(False, "--incluir-defaulteados", help="Traer DEFAULTEADOS.xlsx del legacy como filas globales por instrumento (recomendado)"),
                        template_cajas: Path | None = typer.Option(None, help="Template_Cajas.xlsx para la hoja cajas"),
                        raiz: Path | None = None):
    """Convierte FIP, Atributos_*, OVERRIDES (y opcionalmente DEFAULTEADOS) del legacy a filas de REGLAS.xlsx con ID_Fund."""
    import datetime as dt
    import pandas as pd
    from .legado import _fondos_alias, fusionar_reglas, migrar_manuales
    from .lectura.maestros import leer_bd_instrumentos
    from .adaptadores import dim as DIM
    r = Rutas.desde_env(fecha, raiz)
    fondos = DIM.leer(r.dim).fondos if Path(r.dim).exists() else r.bd_funds
    alias = _fondos_alias(fondos, r.homol_funds if Path(r.homol_funds).exists() else None)
    ids = set(leer_bd_instrumentos(r.bd_instr)["ID_Instrumento"]) if Path(r.bd_instr).exists() else None
    nuevas, informe = migrar_manuales(legacy, alias, ids, incluir_defaulteados, template_cajas)
    for _, f in informe.iterrows():
        typer.echo(f"  {f['Archivo']:24} leídas {f['Filas_leidas']:>4}  generadas {f['Filas_generadas']:>4}  {f['Avisos']}")
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    manuales = Path(r.reglas).parent
    if aplicar:
        backup = manuales / f"REGLAS_backup_{ts}.xlsx"
        backup.write_bytes(Path(r.reglas).read_bytes())
        agregadas = fusionar_reglas(r.reglas, nuevas, r.reglas)
        typer.echo(f"REGLAS.xlsx actualizado (respaldo {backup.name}): filas agregadas {agregadas}")
    else:
        salida = manuales / f"REGLAS_migracion_{ts}.xlsx"
        with pd.ExcelWriter(salida) as w:
            for hoja, df in nuevas.items():
                df.to_excel(w, sheet_name=hoja, index=False)
            informe.to_excel(w, sheet_name="informe", index=False)
        typer.echo(f"filas para revisar y pegar en REGLAS.xlsx: {salida} (o vuelva a correr con --aplicar)")


@app.command()
def comparar(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"),
             otro: Path | None = typer.Option(None, help="Otro REPORTE_*.xlsx (p. ej. la corrida con terminal)"),
             version_a: int | None = typer.Option(None, "--version-a", help="Versión del datamart como lado A (default: REPORTE del cierre)"),
             version_b: int | None = typer.Option(None, "--version-b", help="Versión del datamart como lado B"),
             raiz: Path | None = None):
    """Compara dos carteras del cierre: el REPORTE con otro Excel, o dos versiones del datamart (--version-a/--version-b). Exit 1 si hay diferencias."""
    import datetime as dt
    import pandas as pd
    from . import datamart as DMV
    from .salida import comparar_carteras
    r = Rutas.desde_env(fecha, raiz)

    def lado(version, ruta):
        if version is not None:
            v = DMV.resolver_version(r.datamart, fecha, version=version)
            if v is None:
                typer.echo(f"no existe la versión {version} del cierre {fecha}")
                raise typer.Exit(2)
            return v.posiciones(), v.etiqueta
        return pd.read_excel(ruta, sheet_name="cartera_final"), Path(ruta).name
    if otro is None and version_b is None:
        typer.echo("indique --otro RUTA o --version-b N")
        raise typer.Exit(2)
    a, na = lado(version_a, r.excel_final)
    b, nb = lado(version_b, otro)
    dif = comparar_carteras(a, b)
    salida = r.outputs / f"comparacion_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    salida.parent.mkdir(parents=True, exist_ok=True)
    dif.to_csv(salida, index=False)
    typer.echo(f"{len(dif)} diferencias entre {na} y {nb} → {salida}")
    if len(dif):
        typer.echo(dif.groupby("Campo").size().to_string())
    raise typer.Exit(1 if len(dif) else 0)



# ── datamart: versiones del cierre ──────────────────────────────────────────────────────────────────────────────────
def _version_o_salir(r: Rutas, fecha: str, publicada: bool, version: int | None, conocimiento: str | None):
    from . import datamart as DMV
    v = DMV.resolver_version(r.datamart, fecha, publicada=publicada, version=version, conocimiento=conocimiento)
    if v is None:
        typer.echo(f"el cierre {fecha} no tiene versión que cumpla lo pedido en {r.datamart} (ver `reporteria versiones --fecha {fecha}`)")
        raise typer.Exit(2)
    return v


@app.command()
def publicar(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"),
             borrador: str | None = typer.Option(None, help="Nombre o timestamp del borrador (default: el último de 02_OUTPUTS/F/borradores)"),
             motivo: str = typer.Option("", help="Obligatorio al re-expresar: qué cambió"),
             reexpresar: bool = typer.Option(False, "--reexpresar", help="El cierre ya tiene versión publicada: agregar una re-expresión"),
             raiz: Path | None = None):
    """Fija la versión oficial del cierre copiando un borrador al datamart (version=NNN). Después: git add datamart && git commit."""
    from . import datamart as DMV
    r = Rutas.desde_env(fecha, raiz)
    bors = DMV.borradores(r.borradores, fecha)
    if not bors:
        typer.echo(f"no hay borradores en {r.borradores}: corra `reporteria correr --fecha {fecha}` primero")
        raise typer.Exit(2)
    elegido = bors[-1] if borrador is None else next((b for b in bors if borrador in b.ruta.name), None)
    if elegido is None:
        typer.echo(f"borrador '{borrador}' no encontrado; disponibles: {[b.ruta.name for b in bors]}")
        raise typer.Exit(2)
    try:
        v = DMV.publicar(r.datamart, fecha, elegido.ruta, motivo, reexpresar)
    except ValueError as e:
        typer.echo(f"ERROR: {e}")
        raise typer.Exit(2)
    typer.echo(f"{v.etiqueta} ← {elegido.ruta.name} (completitud {v.completitud}) → {v.ruta}")
    typer.echo(f"ahora: git add {r.datamart} && git commit -m \"cierre {fecha} v{v.numero:03d} {v.estado}\" && git push")


@app.command()
def versiones(fecha: str | None = typer.Option(None, help="Cierre YYYYMMDD (default: todos)"), raiz: Path | None = None):
    """Lista las versiones del datamart (cierre, versión, estado, motivo, fecha de publicación, completitud, cierre anterior usado)."""
    from .adaptadores import datamart as DM
    r = Rutas.desde_env(fecha or "00000000", raiz)
    t = DM.listar_versiones(r.datamart, fecha)
    if t.empty:
        typer.echo(f"sin versiones en {r.datamart}" + (f" para {fecha}" if fecha else ""))
        raise typer.Exit(0)
    for _, f in t.iterrows():
        typer.echo(f"  {f['cierre']}  v{int(f['version']):03d}  {f['estado']:12} {f['completitud'] or 'COMPLETA':9} {f['ts']:19}  anterior {f['anterior'] or '-':16} {f['motivo']}")


@app.command()
def reporte(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"),
            publicada: bool = typer.Option(False, "--publicada", help="Lo reportado (primera versión publicada)"),
            version: int | None = typer.Option(None, help="Una versión concreta"),
            conocimiento: str | None = typer.Option(None, help="Lo que se sabía al YYYYMMDD (última versión con fecha ≤ ese día)"),
            salida: Path | None = typer.Option(None, help="Excel de salida (default: 02_OUTPUTS/F/REPORTE_F_vNNN.xlsx)"),
            raiz: Path | None = None):
    """Regenera el Excel del cierre desde el datamart: última verdad por defecto, o --publicada / --version N / --conocimiento D."""
    from .salida import escribir_excel
    r = Rutas.desde_env(fecha, raiz)
    v = _version_o_salir(r, fecha, publicada, version, conocimiento)
    hojas = v.hojas()
    destino = Path(salida) if salida else r.outputs / f"REPORTE_{fecha}_v{v.numero:03d}.xlsx"
    escribir_excel(hojas, destino)
    typer.echo(f"{v.etiqueta} ({v.completitud}; publicada {v.ts}; motivo: {v.motivo or '-'}) → {destino}")


# ── dim: dimensionales locales ───────────────────────────────────────────────────────────────────────────────────────
def _ruta_dim(dim: Path | None, raiz: Path | None) -> Path:
    return Path(dim) if dim else Rutas.desde_env("00000000", raiz).dim


@dim_app.command("importar")
def dim_importar(bix: Path | None = typer.Option(None, help="Carpeta BIX con BD_BalanceSheet, BD_FX_Exposure_*, BD_FUNDS, BD_Monedas, BD_*_TYPE (migración)"),
                 excel: Path | None = typer.Option(None, help="Excel exportado con `dim exportar` y editado (ciclo de edición)"),
                 dim: Path | None = typer.Option(None, help="Ruta del duckdb (default: REPORTERIA_DIM o v2/dim/dimensionales.duckdb)"),
                 reemplazar: bool = typer.Option(False, "--reemplazar", help="Con --bix: sobrescribir un duckdb existente"),
                 raiz: Path | None = None):
    """Crea o actualiza dimensionales.duckdb: desde el BIX (una vez, compactando) o desde un Excel editado (valida antes de escribir)."""
    from .adaptadores import dim as DIM
    from .dim import validar
    from .legado import migrar_dimensionales
    destino = _ruta_dim(dim, raiz)
    if (bix is None) == (excel is None):
        typer.echo("indique --bix <carpeta> (migración) o --excel <archivo> (edición), no ambos")
        raise typer.Exit(2)
    if bix is not None:
        if destino.exists() and not reemplazar:
            typer.echo(f"{destino} ya existe: use --excel para editarlo o --reemplazar para volver a migrar desde el BIX")
            raise typer.Exit(2)
        dirs = [Path(bix)] + ([Path(bix) / "DIMENSIONALES"] if (Path(bix) / "DIMENSIONALES").is_dir() else [])
        dims, informe = migrar_dimensionales(dirs)
        for _, f in informe.iterrows():
            typer.echo(f"  {f['Fuente']:48} leídas {f['Filas_leidas']:>4}  generadas {f['Filas_generadas']:>4}  {f['Avisos']}")
    else:
        dims = DIM.importar_excel(excel)
        import datetime as dt
        dims.meta = {**dims.meta, "importado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "origen": str(excel)}
    prob = validar(dims.clasificacion, dims.catalogos, fondos=set(dims.bd_funds()["ID_Fund"]) if len(dims.fondos) else None)
    if not prob.empty:
        for _, f in prob.iterrows():
            typer.echo(f"  [ERR] {f['Tabla']} ID {f['ID']}: {f['Problema']}")
        typer.echo("no se escribió nada: corrija y vuelva a importar")
        raise typer.Exit(2)
    DIM.escribir(destino, dims)
    typer.echo(f"dimensionales escritas en {destino} ({dims.resumen()}); espejo CSV en {destino.parent / 'csv'} → git add dim/ && git commit")


@dim_app.command("exportar")
def dim_exportar(salida: Path | None = typer.Option(None, help="Excel de salida (default: dim/DIM_{ts}.xlsx junto al duckdb)"),
                 dim: Path | None = typer.Option(None), raiz: Path | None = None):
    """Exporta todas las tablas a un Excel (una hoja por tabla) para editarlas y volver con `dim importar --excel`."""
    import datetime as dt
    from .adaptadores import dim as DIM
    origen = _ruta_dim(dim, raiz)
    d = DIM.leer(origen)
    destino = Path(salida) if salida else origen.parent / f"DIM_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    DIM.exportar_excel(d, destino)
    typer.echo(f"exportado a {destino} ({d.resumen()})")


@dim_app.command("validar")
def dim_validar(fecha: str | None = typer.Option(None, help="Cierre YYYYMMDD para cruzar con REGLAS/buckets (opcional)"),
                dim: Path | None = typer.Option(None), raiz: Path | None = None):
    """Valida dim_clasificacion: códigos en catálogos, fondos, solapes ambiguos; con --fecha, Buckets contra REGLAS/buckets y REGLAS BalSheetKey deprecadas."""
    from .adaptadores import dim as DIM
    from .dim import validar
    ruta = _ruta_dim(dim, raiz)
    d = DIM.leer(ruta)
    buckets = None
    if fecha:
        from .lectura.reglas import leer_reglas
        r = Rutas.desde_env(fecha, raiz)
        if Path(r.reglas).exists():
            rg = leer_reglas(r.reglas)
            buckets = set(rg.buckets["Bucket"])
            dep = rg.clasificacion[rg.clasificacion["Criterio"].eq("BalSheetKey")]
            for _, f in dep.iterrows():
                typer.echo(f"  [AVISO] REGLAS/clasificacion ID {f['ID']} (Criterio=BalSheetKey {f['Valor']} fondo {f['ID_Fund']}) está deprecada: "
                           f"muévala a dim_clasificacion con ID_Fund y bórrela de REGLAS")
    prob = validar(d.clasificacion, d.catalogos, buckets, set(d.bd_funds()["ID_Fund"]) if len(d.fondos) else None)
    for _, f in prob.iterrows():
        typer.echo(f"  [ERR] {f['Tabla']} ID {f['ID']}: {f['Problema']}")
    typer.echo(f"dim validar: {'sin problemas' if prob.empty else str(len(prob)) + ' problemas'} ({ruta})")
    raise typer.Exit(2 if len(prob) else 0)


@dim_app.command("probar")
def dim_probar(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"), dim: Path | None = typer.Option(None), raiz: Path | None = None):
    """Clasifica el CUBO de la fecha con las dimensionales y muestra conteos por Bucket / Ficha_FI / FX_Exposure y combinaciones sin fila."""
    import pandas as pd
    from . import clasificacion, dim as D, universo
    from .adaptadores import dim as DIM
    from .lectura import maestros as M
    from .lectura.cubo import leer_cubo
    from .lectura.reglas import leer_reglas
    r = Rutas.desde_env(fecha, raiz)
    d = DIM.leer(_ruta_dim(dim, raiz))
    cubo = leer_cubo(r.cubo)
    reglas = leer_reglas(r.reglas, set(d.bd_funds()["ID_Fund"]) | set(cubo["ID_Fund"]))
    pos, _ = universo.armar_universo(cubo, M.leer_bd_instrumentos(r.bd_instr), d.bd_funds(), d.bd_monedas())
    pos, al = clasificacion.clasificar(pos, d.clasificacion, reglas.buckets, reglas.clasificacion, r.settle)
    mv = pd.to_numeric(pos["TotalMVal"], errors="coerce").abs()
    for col in ("Bucket", "Ficha_FI", "FX_Exposure"):
        typer.echo(f"\n{col}:")
        t = pos.assign(_mv=mv).groupby(pos[col].replace("", "(vacío)")).agg(n=("Pos_ID", "size"), mv=("_mv", "sum")).sort_values("mv", ascending=False)
        for k, f in t.iterrows():
            typer.echo(f"  {k:36} {int(f['n']):>6} posiciones  {f['mv']:>18,.0f}")
    plantilla = D.plantilla_sin_dim(pos)
    typer.echo(f"\ncombinaciones sin fila (Bucket SIN_REGLA / Ficha_FI o FX_Exposure vacíos): {len(plantilla)}")
    for _, f in plantilla.head(15).iterrows():
        typer.echo(f"  {f['BalanceSheet']}|{'|'.join(str(f[c]) for c in D.COLS_LLAVE[1:])}  falta {f['_Falta']:28} {int(f['_N_Posiciones']):>5} pos  {f['_TotalMVal']:>16,.0f}  ej. {f['_Ejemplo']}")
    if len(al):
        typer.echo(f"alertas: {al.groupby('Nombre').size().to_dict()}")


# ── maestros: BD_INSTRUMENTOS / HOMOL bitemporales ─────────────────────────────────────────────────────────────────────
@maestros_app.command("cargar")
def maestros_cargar(base: bool = typer.Option(False, "--base", help="Escribir una base nueva desde el BIX (primera vez o re-base)"),
                    raiz: Path | None = None):
    """Compara el BIX de hoy con el estado del datamart y registra los cambios (normalmente lo hace `correr` solo)."""
    import pandas as pd
    from . import maestros_hist as MH
    from .adaptadores import datamart as DM
    from .lectura import maestros as M
    r = Rutas.desde_env("00000000", raiz)
    vivos = {}
    if Path(r.bd_instr).exists():
        vivos["bd_instrumentos"] = M.leer_bd_instrumentos(r.bd_instr)
    if Path(r.homol).exists():
        vivos["homol_instrumentos"] = M.leer_homol(r.homol)
    if Path(r.homol_funds).exists():
        vivos["homol_funds"] = M.leer_homol_funds(r.homol_funds)
    if "bd_instrumentos" not in vivos:
        typer.echo(f"BD_INSTRUMENTOS no encontrado en {r.bd_instr}")
        raise typer.Exit(2)
    base_id, tablas = DM.leer_base(r.datamart)
    if base or base_id is None:
        carga = DM.id_carga()
        DM.escribir_base(r.datamart, carga, {t: MH.normalizar_tabla(t, vivos.get(t)) for t in MH.TABLAS})
        typer.echo(f"base {carga} escrita: " + ", ".join(f"{t}={len(vivos.get(t, []))}" for t in MH.TABLAS) + f" → git add {r.datamart}")
        raise typer.Exit(0)
    camb = DM.leer_cambios(r.datamart, base_id)
    nuevos = pd.concat([MH.diff_tablas(t, MH.estado(t, tablas.get(t), camb), vivos[t]) for t in vivos], ignore_index=True)
    if nuevos.empty:
        typer.echo(f"sin cambios respecto del datamart (base {base_id}, {len(camb)} cambios previos)")
        raise typer.Exit(0)
    carga = DM.id_carga()
    DM.escribir_cambios(r.datamart, carga, nuevos)
    typer.echo(f"carga {carga}: {len(nuevos)} cambios {MH.resumen_cambios(nuevos)} → git add {r.datamart}")


@maestros_app.command("cambios")
def maestros_cambios(desde: str | None = typer.Option(None, help="Solo cargas con fecha ≥ YYYYMMDD"),
                     tabla: str | None = typer.Option(None), llave: str | None = typer.Option(None, help="PK2 o SourceInvestment|Source"),
                     limite: int = typer.Option(50), raiz: Path | None = None):
    """Lista los cambios registrados en el datamart (carga, tabla, llave, columna, antes → después, tipo)."""
    from .adaptadores import datamart as DM
    r = Rutas.desde_env("00000000", raiz)
    bases = DM.bases(r.datamart)
    if not bases:
        typer.echo("sin base de maestros en el datamart")
        raise typer.Exit(0)
    c = DM.leer_cambios(r.datamart, bases[-1])
    if desde:
        c = c[c["carga"].str[:8] >= desde[:8]]
    if tabla:
        c = c[c["tabla"].eq(tabla)]
    if llave:
        c = c[c["llave"].str.contains(llave, regex=False)]
    typer.echo(f"{len(c)} cambios (base {bases[-1]})")
    for _, f in c.tail(limite).iterrows():
        typer.echo(f"  {f['carga']}  {f['tabla']:18} {f['llave']:28} {f['columna']:22} {str(f['valor_anterior'])[:40]:40} → {str(f['valor_nuevo'])[:40]:40} {f['tipo']}")


@maestros_app.command("estado")
def maestros_estado(llave: str = typer.Option(..., help="PK2 (bd_instrumentos) o SourceInvestment|Source (homol)"),
                    tabla: str = typer.Option("bd_instrumentos"), cierre: str | None = typer.Option(None, help="Como era válido en ese cierre"),
                    conocimiento: str | None = typer.Option(None), raiz: Path | None = None):
    """Muestra una fila del maestro: estado actual, o as-of un cierre y/o lo conocido a una fecha."""
    from . import maestros_hist as MH
    from .adaptadores import datamart as DM
    r = Rutas.desde_env("00000000", raiz)
    base_id, tablas = DM.leer_base(r.datamart, conocimiento)
    if base_id is None:
        typer.echo("sin base de maestros en el datamart")
        raise typer.Exit(2)
    camb = DM.leer_cambios(r.datamart, base_id, conocimiento)
    t = MH.maestro_asof(tabla, tablas.get(tabla), camb, DM.leer_vigencias(r.datamart), cierre or "99991231", conocimiento)
    k = MH.llave_txt(t, MH.LLAVES[tabla])
    fila = t[k.eq(llave).to_numpy()]
    if fila.empty:
        typer.echo(f"{tabla}[{llave}] no existe" + (f" al cierre {cierre}" if cierre else "") + (f" según lo conocido al {conocimiento}" if conocimiento else ""))
        raise typer.Exit(1)
    for c, v in fila.iloc[0].items():
        typer.echo(f"  {c:22} {v}")


@app.command()
def declarar(tabla: str = typer.Option("bd_instrumentos"), llave: str = typer.Option(None, help="PK2 o SourceInvestment|Source"),
             columna: str = typer.Option(None), valor: str = typer.Option(None, help="Valor que rige desde el cierre"),
             desde: str = typer.Option(None, help="Cierre YYYYMMDD desde el que rige"),
             valor_anterior: str | None = typer.Option(None, "--valor-anterior", help="Solo si el cambio no está en `maestros cambios`"),
             comentario: str = typer.Option(""), anular: int | None = typer.Option(None, "--anular", help="ID de la declaración a anular"),
             raiz: Path | None = None):
    """Declara que un cambio del maestro NO es corrección sino un hecho nuevo que rige desde un cierre (antes vale el valor anterior)."""
    from . import maestros_hist as MH
    from .adaptadores import datamart as DM
    r = Rutas.desde_env("00000000", raiz)
    vig = DM.leer_vigencias(r.datamart)
    try:
        if anular is not None:
            vig = MH.anular(vig, anular)
            DM.escribir_vigencias(r.datamart, vig)
            typer.echo(f"declaración {anular} anulada → git add {DM.ruta_vigencias(r.datamart)}; los cierres afectados vuelven a estar sujetos a impacto")
            raise typer.Exit(0)
        if not all([llave, columna, valor, desde]):
            typer.echo("faltan --llave, --columna, --valor y --desde (o use --anular ID)")
            raise typer.Exit(2)
        bases = DM.bases(r.datamart)
        camb = DM.leer_cambios(r.datamart, bases[-1]) if bases else None
        vig = MH.declarar(vig, camb, tabla, llave, columna, valor, desde, valor_anterior, comentario)
    except ValueError as e:
        typer.echo(f"ERROR: {e}")
        raise typer.Exit(2)
    DM.escribir_vigencias(r.datamart, vig)
    f = vig.iloc[-1]
    typer.echo(f"declaración {f['ID']}: {tabla}[{llave}].{columna} = {f['valor']} desde {f['vigente_desde']} (antes {f['valor_anterior'] or '(vacío)'}) "
               f"→ git add {DM.ruta_vigencias(r.datamart)}")

if __name__ == "__main__":
    app()
