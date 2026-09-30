"""CLI del operador: `reporteria check --fecha 20260731`, `reporteria correr --fecha 20260731 [--sin-bbg]`."""
from __future__ import annotations

from pathlib import Path

import typer

from .config import Rutas

app = typer.Typer(add_completion=False, help="Yield y Duration por posición — cierre mensual")


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
    for linea in _estado_entorno(r):
        typer.echo(f"  {linea}")
    raise typer.Exit(2 if faltan else 0)


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
           sin_facts: bool = typer.Option(False, "--sin-facts", help="No consulta la base de Facts; usa caché o FACTURAS_F.xlsx"),
           fecha_ant: str | None = typer.Option(None, help="Forzar cierre anterior YYYYMMDD")):
    """Corre el cierre completo y deja REPORTE_{FECHA}.xlsx en 02_OUTPUTS/{FECHA}."""
    from .pipeline import Opciones, correr as _correr
    r = Rutas.desde_env(fecha, raiz, fecha_ant)
    try:
        res = _correr(r, Opciones(sin_bbg=sin_bbg, sin_sql=sin_sql, sin_facts=sin_facts))
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
                        incluir_defaulteados: bool = typer.Option(False, "--incluir-defaulteados", help="Traer DEFAULTEADOS.xlsx del legacy"),
                        template_cajas: Path | None = typer.Option(None, help="Template_Cajas.xlsx para la hoja cajas"),
                        raiz: Path | None = None):
    """Convierte FIP, Atributos_*, OVERRIDES (y opcionalmente DEFAULTEADOS) del legacy a filas de REGLAS.xlsx con ID_Fund."""
    import datetime as dt
    import pandas as pd
    from .legado import _fondos_alias, fusionar_reglas, migrar_manuales
    from .lectura.maestros import leer_bd_instrumentos
    r = Rutas.desde_env(fecha, raiz)
    alias = _fondos_alias(r.bd_funds, r.homol_funds if Path(r.homol_funds).exists() else None)
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
             otro: Path = typer.Option(..., help="Otro REPORTE_*.xlsx (p. ej. la corrida con terminal)"),
             raiz: Path | None = None):
    """Compara la cartera_final del cierre con otro reporte (terminal vs --sin-bbg). Exit 0 sin diferencias, 1 con diferencias."""
    import datetime as dt
    import pandas as pd
    from .salida import comparar_carteras
    r = Rutas.desde_env(fecha, raiz)
    a, b = pd.read_excel(r.excel_final, sheet_name="cartera_final"), pd.read_excel(otro, sheet_name="cartera_final")
    dif = comparar_carteras(a, b)
    salida = r.outputs / f"comparacion_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    dif.to_csv(salida, index=False)
    typer.echo(f"{len(dif)} diferencias entre {r.excel_final.name} y {Path(otro).name} → {salida}")
    if len(dif):
        typer.echo(dif.groupby("Campo").size().to_string())
    raise typer.Exit(1 if len(dif) else 0)


if __name__ == "__main__":
    app()
