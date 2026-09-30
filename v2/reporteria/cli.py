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
            leer_reglas(r.reglas)
            typer.echo("  [OK ] REGLAS.xlsx válido")
        except ValueError as e:
            faltan += 1
            typer.echo(f"  [ERR] REGLAS.xlsx: {e}")
    typer.echo(f"  cierre anterior detectado: {r.fecha_ant or '(ninguno)'}")
    raise typer.Exit(2 if faltan else 0)


@app.command()
def correr(fecha: str = typer.Option(..., help="Cierre YYYYMMDD"), raiz: Path | None = None,
           sin_bbg: bool = typer.Option(False, "--sin-bbg", help="No consulta Bloomberg; usa caché"),
           sin_sql: bool = typer.Option(False, "--sin-sql", help="No consulta beemining; usa paridades y caché"),
           fecha_ant: str | None = typer.Option(None, help="Forzar cierre anterior YYYYMMDD")):
    """Corre el cierre completo y deja REPORTE_{FECHA}.xlsx en 02_OUTPUTS/{FECHA}."""
    from .pipeline import Opciones, correr as _correr
    r = Rutas.desde_env(fecha, raiz, fecha_ant)
    try:
        res = _correr(r, Opciones(sin_bbg=sin_bbg, sin_sql=sin_sql))
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


if __name__ == "__main__":
    app()
