# =============================================================================
# RUN_PIPELINE.py — Orquestador | Reportería 2.0
# =============================================================================
# Corre las etapas en orden, cada una sobre el residual de la anterior.
# Uso:
#   python RUN_PIPELINE.py                → todas las etapas
#   python RUN_PIPELINE.py 00 01          → solo esas
#   python RUN_PIPELINE.py --desde 03     → desde la 03 en adelante
#   python RUN_PIPELINE.py --check        → solo verifica inputs y sale
# =============================================================================

import os
import sys
import time
import subprocess

_sd = os.path.dirname(os.path.abspath(__file__))
if _sd not in sys.path: sys.path.insert(0, _sd)

import pipeline_config as cfg

ETAPAS = [
    ('00', '00_UNIVERSO.py',    'Universo multi-fondo, excluye defaulteados', True),
    ('01', '01_METRICAS.py',    'Métricas calculadas: JPM + RA + BBG (YAS)',  True),
    ('02', '02_CSHF.py',        'Fallback: TD de BBG (DES_CASH_FLOW)',        True),
    ('03', '03_JSONL.py',       'TD propias desde Geneva (bond_schedule)',    True),
    ('04', '04_EXCEPCIONES.py', 'Flujos provistos por el PM',                 True),
    ('05', '05_CONSOLIDA.py',   'Consolida todas las fuentes (cascada)',      True),
    ('06', '06_BREAKEVEN.py',   'Convierte yields indexadas a moneda local',  True),
    ('07', '07_DROPS.py',       'Yield hedgeada para swapeados (drops)',      True),
    ('08', '08_OVERRIDES.py',   'Parche final: yields dadas manualmente',     True),
]

# Cadena de dependencias: qué output necesita cada etapa para tener sentido.
# No bloquea (cada script ya avisa si le falta algo), pero deja claro el orden.
DEPENDE_DE = {
    '01': ['UNIVERSO'], '02': ['UNIVERSO', 'METRICAS'],
    '03': ['UNIVERSO', 'METRICAS'], '04': ['UNIVERSO'],
    '05': ['UNIVERSO'], '06': ['CONSOLIDADO'], '07': ['CONSOLIDADO'],
    '08': ['CONSOLIDADO'],
}


def check_inputs():
    print("\nVerificando inputs...")
    req = [('CUBO', cfg.CUBO_PATH), ('BD_INSTRUMENTOS', cfg.BD_INSTR_PATH),
           ('BD_FUNDS', cfg.BD_FUNDS_PATH), ('HOMOL', cfg.HOMOL_PATH),
           ('Paridades', cfg.ARCHIVO_PARIDS), ('JPM', cfg.JPM_PATH),
           ('RA_TIR', cfg.RA_TIR_PATH), ('bond_schedule', cfg.JSONL_PATH),
           ('DEFAULTEADOS', cfg.DEFAULTEADOS_PATH), ('EXCEPCIONES', cfg.EXCEPCIONES_PATH)]
    faltan = []
    for n, p in req:
        ok = os.path.exists(p)
        print(f"  [{'OK ' if ok else 'FALTA'}] {n:16} {p}")
        if not ok: faltan.append(n)
    return faltan


def diagnostico_final():
    """Al terminar, dice qué quedó sin resolver y qué hacer — en vez de
    dejar que el analista lo descubra abriendo los Excel."""
    import pandas as pd
    ruta = cfg.OUT_OVERRIDES if os.path.exists(cfg.OUT_OVERRIDES) else cfg.OUT_CONSOLIDADO
    if not os.path.exists(ruta):
        return
    try:
        hoja = 'cartera_final' if ruta == cfg.OUT_OVERRIDES else 'Detalle'
        hdr = 0 if ruta == cfg.OUT_OVERRIDES else 2
        d = pd.read_excel(ruta, sheet_name=hoja, header=hdr)
        col_y = 'Yield_final' if 'Yield_final' in d.columns else 'Yield'
        col_d = 'Duration_final' if 'Duration_final' in d.columns else 'Duration'
        falta = d[d[col_y].isna() | d[col_d].isna()]
        print(f"\n{'='*70}\nDIAGNÓSTICO\n{'='*70}")
        print(f"  Cartera resuelta : {len(d)-len(falta):,}/{len(d):,}")
        if len(falta):
            print(f"  SIN MÉTRICA      : {len(falta):,} posiciones "
                  f"({falta['PK2'].nunique()} PK2 únicos)")
            print(f"\n  Qué hacer con los que faltan:")
            print(f"    1. Revisá {os.path.basename(cfg.OUT_OVERRIDES_PENDIENTE)} "
                  f"— trae la plantilla lista con esos PK2.")
            print(f"    2. Completá Yield y Dur y guardalo como "
                  f"{os.path.basename(cfg.OVERRIDES_PATH)} en MANUALES.")
            print(f"    3. Volvé a correr:  python RUN_PIPELINE.py 08")
            por_fondo = falta.groupby(falta.columns[0]).size().sort_values(ascending=False)
            print(f"\n  Concentración por fondo:")
            for k, v in por_fondo.head(5).items():
                print(f"    {k:22} {v:>4}")
        else:
            print("  Cartera COMPLETA — no falta ninguna métrica.")
    except Exception as e:
        print(f"  [WARN] no se pudo generar el diagnóstico: {e}")


def correr(script):
    ruta = os.path.join(_sd, script)
    if not os.path.exists(ruta):
        print(f"  [SKIP] {script} no existe todavía")
        return None
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, ruta], cwd=_sd)
    return (r.returncode, time.perf_counter() - t0)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    print("=" * 70)
    print(cfg.resumen_config())
    print("=" * 70)

    if '--check' in args:
        f = check_inputs()
        print(f"\n{'Todo listo.' if not f else 'Faltan: ' + ', '.join(f)}")
        sys.exit(0 if not f else 1)

    desde = None
    if '--desde' in args:
        desde = args[args.index('--desde') + 1]
        args = []
    pedidas = [a for a in args if not a.startswith('--')]

    faltan = check_inputs()
    if faltan:
        print(f"\n[WARN] Faltan inputs: {', '.join(faltan)}. Algunas etapas pueden fallar.")

    print("\n" + "=" * 70)
    resultados = []
    activo = desde is None
    for cod, script, desc, listo in ETAPAS:
        if desde is not None and cod == desde: activo = True
        if not activo: continue
        if pedidas and cod not in pedidas: continue
        if not listo:
            print(f"\n[{cod}] {desc} — PENDIENTE DE DESARROLLO, se omite")
            continue
        print(f"\n{'='*70}\n[{cod}] {desc}\n{'='*70}")
        out = correr(script)
        if out is None:
            resultados.append((cod, 'NO EXISTE', 0)); continue
        rc, seg = out
        resultados.append((cod, 'OK' if rc == 0 else f'ERROR({rc})', seg))
        if rc != 0:
            print(f"\n[STOP] {script} terminó con error. Se detiene el pipeline.")
            break

    print(f"\n{'='*70}\nRESUMEN\n{'='*70}")
    for cod, est, seg in resultados:
        print(f"  [{cod}] {est:12} {seg:7.1f}s")
    print(f"\nOutputs en: {cfg.DIR_OUTPUTS}")
    diagnostico_final()