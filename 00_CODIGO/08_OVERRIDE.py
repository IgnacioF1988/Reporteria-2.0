# =============================================================================
# 08_OVERRIDES.py — Parche final: yields dadas manualmente
# =============================================================================
# QUÉ ES
#   El último paso de la cascada, después de breakeven y drops. Cubre casos
#   rebuscados donde no vale la pena perseguir una TD: Commitments, FIP, o una
#   yield entregada directamente por otro proveedor. PISA TODO — incluso un
#   papel ya resuelto por JPM/RA/BBG o modelado por nosotros — porque es la
#   última palabra del PM.
#
# ARCHIVO MANUAL: OVERRIDES.xlsx (en MANUALES), una fila por override:
#   Fund_Name | PK2 | Name_Instrumento | Yield | Dur | Currency | Source | Comment
#   · Fund_Name vacío → aplica al PK2 en TODOS los fondos que lo tengan.
#   · Fund_Name con valor → aplica solo a ese fondo (match con FundShortName).
#   · Yield / Dur YA vienen listas: no pasan por breakeven ni por drops.
#     Currency es solo informativa (no dispara ninguna conversión).
#   · Source es una etiqueta libre (Commitment, FIP x, Proveedor x…) — no
#     tiene tratamiento distinto en el código, solo queda en el output.
#
# QUÉ HACE EL SCRIPT (dos funciones en una corrida)
#   1. Arma la yield final por posición: parte de 06_BREAKEVEN para lo
#      indexado, de 07_DROPS para lo hedgeado (si ya existe), y del
#      CONSOLIDADO para el resto — y aplica los OVERRIDES por encima de todo.
#   2. Mira lo que SIGUE faltando y deja una PLANTILLA lista para que el
#      analista la complete (no pisa el OVERRIDES.xlsx existente).
#
# OUTPUT
#   OVERRIDES_{FECHA}.xlsx            → cartera completa: Yield_final/Duration_final
#   OVERRIDES_PENDIENTE_{FECHA}.xlsx  → plantilla de lo que aún falta por llenar
# =============================================================================

import os
import sys
import warnings
import numpy as np
import pandas as pd
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

_sd = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
for p in (_sd, os.getcwd()):
    if p not in sys.path: sys.path.insert(0, p)

from pipeline_config import (
    FECHA, OUT_CONSOLIDADO, OUT_BREAKEVEN, OUT_DROPS, OVERRIDES_PATH,
    OUT_OVERRIDES, OUT_OVERRIDES_PENDIENTE, NAVY, RED, RED_F, GREEN, GREEN_F,
    AMBER_F, FONT_NAME, FECHA_ANT, OUT_OVERRIDES_ANT,
)

# =============================================================================
# CONFIG FLAGS / AGREGADOS
# =============================================================================
# Período anterior: se detecta solo en pipeline_config (carpeta 02_OUTPUTS
# más reciente < FECHA). None = primera corrida → flags temporales y
# atribución vs. período previo quedan inactivos con warning.

YIELD_ALTA_UMBRAL      = 0.25   # 25% general
YIELD_ALTA_UMBRAL_MLDL = 0.40   # 40% MLDL (monedas locales: BRL, ARS, COP, etc.)
DELTA_YIELD_UMBRAL     = 0.10   # 10pp
FONDOS_MONEDA_LOCAL    = ('MLDL',)   # agrupan por Risk_Currency y usan umbral MLDL
DEFAULT_CT             = ('DEF', 'PROP NP', 'PROP COM')


def es_fondo_local(fondo):
    f = str(fondo).upper()
    return any(k in f for k in FONDOS_MONEDA_LOCAL)


print("=" * 70)
print(f"08_OVERRIDES | FECHA={FECHA}")
print("=" * 70)


def limpiar_txt(s):
    if not isinstance(s, pd.Series):
        s = pd.Series(dtype=object)
    return (s.fillna('').astype(str).str.strip()
            .replace({'nan': '', 'None': '', 'NaN': '', '<NA>': ''}))

def fmt_ws(ws, hexc, freeze='B2'):
    fill = PatternFill('solid', fgColor=hexc)
    font = Font(color='FFFFFF', bold=True, size=9, name='Calibri')
    for c in ws[1]:
        c.fill = fill; c.font = font
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    if freeze: ws.freeze_panes = freeze
    ws.row_dimensions[1].height = 26
    for col in ws.columns:
        w = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 9), 40)


# =============================================================================
# [1] BASE — CONSOLIDADO
# =============================================================================
print(f"\n[1] Leyendo {os.path.basename(OUT_CONSOLIDADO)}...")
if not os.path.exists(OUT_CONSOLIDADO):
    print("   ERROR: falta el consolidado. Corré 05_CONSOLIDA primero."); sys.exit(1)

cons = pd.read_excel(OUT_CONSOLIDADO, sheet_name='Detalle', header=2, engine='openpyxl')
cons.columns = [str(c).strip() for c in cons.columns]
for c in ('PK2', 'Risk_Currency', 'Name_Instrumento', 'FundShortName', 'Hedge_Currency'):
    if c in cons.columns: cons[c] = limpiar_txt(cons[c])
print(f"   Posiciones: {len(cons):,}")

cons['Yield_final']    = cons['Yield'] / 100    # Consolidado: Yield en % → decimal
cons['Duration_final'] = cons['Duration']
cons['Etapa_final']    = cons['Fuente']

# =============================================================================
# [2] BREAKEVEN — pisa lo indexado
# =============================================================================
print(f"\n[2] Aplicando 06_BREAKEVEN...")
if os.path.exists(OUT_BREAKEVEN):
    be = pd.read_excel(OUT_BREAKEVEN, sheet_name='convertidos', engine='openpyxl')
    if 'PK2' in be.columns:
        be['PK2'] = limpiar_txt(be['PK2'])
        be['Fondo'] = limpiar_txt(be['Fondo']) if 'Fondo' in be.columns else ''
        be['_k'] = be['Fondo'] + '|' + be['PK2']
        cons['_k'] = cons['FundShortName'] + '|' + cons['PK2']
        mapa_y = dict(zip(be['_k'], be['Yield_local']))
        mapa_d = dict(zip(be['_k'], be['Duration_local']))
        m = cons['_k'].map(mapa_y)
        mask = m.notna()
        cons.loc[mask, 'Yield_final']    = m[mask]   # Breakeven: Yield_local YA sale en decimal (fórmula (1+y/100)*(1+ajuste)-1)
        cons.loc[mask, 'Duration_final'] = cons['_k'].map(mapa_d)[mask]
        cons.loc[mask, 'Etapa_final']    = 'BREAKEVEN'
        print(f"   Aplicado a {int(mask.sum()):,} posiciones")
    else:
        print("   [WARN] hoja 'convertidos' sin columna PK2 — se omite")
else:
    print(f"   [WARN] falta {os.path.basename(OUT_BREAKEVEN)} — se omite (usa Yield del consolidado)")

# =============================================================================
# [3] DROPS — pisa lo hedgeado (etapa aún no implementada)
# =============================================================================
print(f"\n[3] Aplicando 07_DROPS...")
if os.path.exists(OUT_DROPS):
    dr = pd.read_excel(OUT_DROPS, sheet_name='convertidos', engine='openpyxl')
    if {'PK2', 'Fondo'}.issubset(dr.columns):
        dr['PK2'] = limpiar_txt(dr['PK2']); dr['Fondo'] = limpiar_txt(dr['Fondo'])
        dr['_k'] = dr['Fondo'] + '|' + dr['PK2']
        m = cons['_k'].map(dict(zip(dr['_k'], dr.get('Yield_Drop'))))
        mask = m.notna()
        cons.loc[mask, 'Yield_final'] = m[mask] / 100   # Drops: Yield_Drop en % → decimal
        if 'Duration_hedged' in dr.columns:
            cons.loc[mask, 'Duration_final'] = cons['_k'].map(dict(zip(dr['_k'], dr['Duration_hedged'])))[mask]
        cons.loc[mask, 'Etapa_final'] = 'DROPS'
        print(f"   Aplicado a {int(mask.sum()):,} posiciones")
else:
    n_pend = int((limpiar_txt(cons.get('Hedge_Currency', '')) != '').sum())
    print(f"   [WARN] falta {os.path.basename(OUT_DROPS)} (etapa aún no implementada) — "
          f"{n_pend} posiciones hedgeadas quedan con su yield sin ajustar por drop")

# =============================================================================
# [4] OVERRIDES — pisa TODO
# =============================================================================
print(f"\n[4] Aplicando OVERRIDES.xlsx...")
n_over = 0
sin_match_fondo = []

if os.path.exists(OVERRIDES_PATH):
    ov = pd.read_excel(OVERRIDES_PATH, engine='openpyxl')
    ov.columns = [str(c).strip() for c in ov.columns]
    req = {'PK2', 'Yield'}
    if not req.issubset(ov.columns):
        print(f"   [WARN] faltan columnas obligatorias {req - set(ov.columns)} — se omite")
    else:
        ov['PK2'] = limpiar_txt(ov['PK2'])
        ov['Fund_Name'] = limpiar_txt(ov['Fund_Name']) if 'Fund_Name' in ov.columns else ''
        ov = ov[ov['PK2'] != '']
        ov = ov[pd.to_numeric(ov['Yield'], errors='coerce').notna()]

        # Detectar filas donde Yield = "DEF" o "PROPDEF" (string) → tratamiento especial (ambos: Yield=0%, Dur=0.5)
        y_str = ov['Yield'].astype(str).str.strip().str.upper()
        mask_def = (y_str == 'DEF') | (y_str == 'PROPDEF')
        n_def_override = int(mask_def.sum())
        if n_def_override:
            print(f"   [OJO] {n_def_override} filas con Yield='DEF' o 'PROPDEF' → se aplicarán YIELD=0% y DURATION=0.5")

        # Unidad: se asume que valores > 1 vienen en PORCENTAJE (se dividen
        # /100) y valores ≤ 1 ya vienen en DECIMAL. Es una heurística por
        # FILA, no por mediana del archivo completo — un analista puede
        # tipear algunas filas en % y otras en decimal en la misma planilla.
        # Zona gris: una yield real entre 0 y 1 en % (ej. "0.8" queriendo
        # decir 0.8%) se leería como 80% decimal — pedir al analista usar
        # SIEMPRE la misma convención dentro del archivo evita esto.
        y_num = pd.to_numeric(ov['Yield'], errors='coerce')
        n_pct = int((y_num.abs() > 1).sum())
        if n_pct:
            print(f"   [OJO] {n_pct} filas de OVERRIDES con |Yield|>1 → se asumen % y se dividen /100")
        ov['Yield'] = np.where(y_num.abs() > 1, y_num / 100.0, y_num)
        ov['Dur'] = pd.to_numeric(ov.get('Dur'), errors='coerce')
        
        # Reemplazar DEF/PROPDEF por los valores especiales
        ov.loc[mask_def, 'Yield'] = 0.0
        ov.loc[mask_def, 'Dur'] = 0.5

        fondos_validos = set(cons['FundShortName'].unique())
        for _, r in ov.iterrows():
            fn = r['Fund_Name']
            if fn and fn not in fondos_validos:
                sin_match_fondo.append({'Fund_Name': fn, 'PK2': r['PK2']})
                continue
            mask = (cons['PK2'] == r['PK2']) & ((fn == '') | (cons['FundShortName'] == fn))
            if not mask.any():
                continue
            cons.loc[mask, 'Yield_final']    = r['Yield']
            cons.loc[mask, 'Duration_final'] = r['Dur']
            cons.loc[mask, 'Etapa_final']    = 'OVERRIDE'
            cons.loc[mask, 'Override_Source']  = r.get('Source', '')
            cons.loc[mask, 'Override_Comment'] = r.get('Comment', '')
            cons.loc[mask, 'Override_Currency'] = r.get('Currency', '')
            n_over += int(mask.sum())
        print(f"   Filas en OVERRIDES.xlsx: {len(ov)} | posiciones pisadas: {n_over}")
        if sin_match_fondo:
            print(f"   [WARN] {len(sin_match_fondo)} filas con Fund_Name que no matchea ningún fondo")
else:
    print(f"   [INFO] no existe {os.path.basename(OVERRIDES_PATH)} todavía — nada que aplicar")

for c in ('Override_Source', 'Override_Comment', 'Override_Currency'):
    if c not in cons.columns: cons[c] = ''

# =============================================================================
# [5] ESTADO FINAL
# =============================================================================
cons['Resuelto_final'] = cons['Yield_final'].notna() & cons['Duration_final'].notna()
cons['Estado_final'] = np.where(
    cons['Etapa_final'] == 'OVERRIDE', 'OVERRIDE',
    np.where(cons['Resuelto_final'], 'RESUELTO', 'FALTANTE'))

n_tot, n_ok = len(cons), int(cons['Resuelto_final'].sum())
print(f"\n[5] Cartera: {n_ok:,}/{n_tot:,} resuelta ({n_ok/n_tot:.1%}) | "
      f"FALTANTE: {n_tot-n_ok:,}")

# =============================================================================
# [6] PLANTILLA DE PENDIENTES (no pisa el OVERRIDES.xlsx del analista)
# =============================================================================
falt = cons[~cons['Resuelto_final']].copy()
ya_en_overrides = set()
if os.path.exists(OVERRIDES_PATH):
    try:
        ov0 = pd.read_excel(OVERRIDES_PATH, engine='openpyxl')
        ya_en_overrides = set(limpiar_txt(ov0['PK2']))
    except Exception:
        pass

plantilla_pk2 = (falt.groupby('PK2')
                 .agg(Name_Instrumento=('Name_Instrumento', 'first'),
                      Fondos=('FundShortName', lambda s: ', '.join(sorted(set(s)))),
                      Risk_Currency=('Risk_Currency', 'first'),
                      N_fondos=('FundShortName', 'nunique'))
                 .reset_index())
plantilla_pk2['Ya_en_OVERRIDES'] = plantilla_pk2['PK2'].isin(ya_en_overrides)

plantilla = pd.DataFrame({
    'Fund_Name': '',                                    # vacío = aplica a todos los fondos listados
    'PK2': plantilla_pk2['PK2'],
    'Name_Instrumento': plantilla_pk2['Name_Instrumento'],
    'Yield': np.nan, 'Dur': np.nan,
    'Currency': plantilla_pk2['Risk_Currency'],
    'Source': '', 'Comment': '',
    'Marcar_como_DEF_PROPDEF': '',  # escribir 'DEF' o 'PROPDEF' para aplicar default (Yield=0%, Dur=0.5)
    'Fondos_que_lo_tienen': plantilla_pk2['Fondos'],     # informativa, no parte del esquema
})
print(f"   Plantilla de pendientes: {len(plantilla)} PK2 únicos "
      f"({int(plantilla_pk2['Ya_en_OVERRIDES'].sum())} ya estaban en OVERRIDES.xlsx pero sin match)")
print(f"   → Columna Yield: tipear valor en % o escribir 'DEF'/'PROPDEF' para aplicar default (Yield=0%, Dur=0.5)")

# =============================================================================
# [6b] PERÍODO ANTERIOR (para flags temporales y atribución)
# =============================================================================
for c in ('Yield_final', 'Duration_final', 'TotalMVal', 'AI', 'LocalPrice'):
    if c in cons.columns:
        cons[c] = pd.to_numeric(cons[c], errors='coerce')
if 'Risk_Country' in cons.columns:
    cons['Risk_Country'] = limpiar_txt(cons['Risk_Country'])
if 'CalcType' in cons.columns:
    cons['CalcType'] = limpiar_txt(cons['CalcType']).str.upper()

prev = None
if FECHA_ANT is None:
    print("\n[WARN] No existe período previo para esta corrida (FECHA_ANT=None) — "
          "flags Δ Yield / Precio↑+Yield↑ / Métricas sin actualizar y la "
          "atribución vs. período anterior quedan INACTIVOS.")
else:
    path_prev = OUT_OVERRIDES_ANT
    if path_prev and os.path.exists(path_prev):
        prev = pd.read_excel(path_prev, sheet_name='cartera_final', engine='openpyxl')
        prev.columns = [str(c).strip() for c in prev.columns]
        for c in ('PK2', 'FundShortName', 'Risk_Country', 'Risk_Currency'):
            if c in prev.columns: prev[c] = limpiar_txt(prev[c])
        for c in ('Yield_final', 'Duration_final', 'TotalMVal', 'AI', 'LocalPrice'):
            if c in prev.columns: prev[c] = pd.to_numeric(prev[c], errors='coerce')
        print(f"\n[6b] Período anterior cargado: {os.path.basename(path_prev)} ({len(prev):,} filas)")
    else:
        print(f"\n[WARN] No existe período previo: no se encontró {os.path.basename(path_prev)} — "
              f"flags temporales y atribución vs. anterior quedan INACTIVOS.")

# =============================================================================
# [6c] FLAGS
# =============================================================================
print("\n[6c] Calculando flags...")
FLAG_COLS = ['ID_Fund', 'FundShortName', 'PK2', 'ISIN', 'Name_Instrumento', 'CalcType',
             'Risk_Country', 'Risk_Currency', 'Yield_final', 'Duration_final',
             'AI', 'LocalPrice', 'Etapa_final', 'TotalMVal']
flags_rows, flags_resumen = [], []

def add_flag(nombre, severidad, df, detalle_fn=None, estado='ACTIVO'):
    n = len(df) if df is not None else 0
    mv = float(df['TotalMVal'].sum()) if n and 'TotalMVal' in df.columns else 0.0
    flags_resumen.append({'Flag': nombre, 'Severidad': severidad, 'Estado': estado,
                          'N_posiciones': n, 'MV_afectado': mv})
    if not n: return
    d = df[[c for c in FLAG_COLS if c in df.columns]].copy()
    d.insert(0, 'Severidad', severidad)
    d.insert(0, 'Flag', nombre)
    d['Detalle'] = df.apply(detalle_fn, axis=1) if detalle_fn else ''
    flags_rows.append(d)

yv = cons['Yield_final']
umbral = np.where(cons['FundShortName'].map(es_fondo_local),
                  YIELD_ALTA_UMBRAL_MLDL, YIELD_ALTA_UMBRAL)

# F1: Yield alta (umbral distinto para MLDL)
f1 = cons[yv.notna() & (yv >= umbral)]
add_flag(f"Yield ≥ {YIELD_ALTA_UMBRAL:.0%} ({YIELD_ALTA_UMBRAL_MLDL:.0%} en MLDL)", '🔴 CRÍTICA', f1,
         lambda r: f"Yield={r['Yield_final']:.2%} | etapa={r['Etapa_final']}")

# F2: Yield negativa
f2 = cons[yv.notna() & (yv < 0)]
add_flag('Yield Negativo', '🟠 ALTA', f2,
         lambda r: f"Yield={r['Yield_final']:.2%} | etapa={r['Etapa_final']}")

# F3: Default con AI > 0  /  No-default con AI = 0
if {'CalcType', 'AI'}.issubset(cons.columns):
    es_def = cons['CalcType'].isin(DEFAULT_CT)
    f3 = cons[es_def & (cons['AI'].fillna(0) > 0)]
    add_flag('Default con AI > 0', '🟠 ALTA', f3,
             lambda r: f"CalcType={r['CalcType']} | AI={r['AI']:,.2f}")
    f3b = cons[~es_def & (cons['AI'].fillna(0) == 0)]
    add_flag('No-Default con AI = 0', '🟡 MEDIA', f3b,
             lambda r: f"CalcType={r['CalcType'] or '—'} | AI=0 (¿cupón cero, recién pagó cupón o default no marcado?)")
else:
    print("   [WARN] faltan CalcType/AI en el consolidado — flags de AI omitidos")

# F4: Cupón < 0 → omitido por ahora (campo no disponible en el consolidado)

# F5/F6/F7: temporales
if prev is not None:
    k_prev = prev['FundShortName'] + '|' + prev['PK2']
    cons['_k'] = cons['FundShortName'] + '|' + cons['PK2']
    for c in ('Yield_final', 'Duration_final', 'LocalPrice', 'AI'):
        if c in prev.columns:
            cons[f'{c}_ant'] = cons['_k'].map(dict(zip(k_prev, prev[c])))
    dY = cons['Yield_final'] - cons.get('Yield_final_ant')

    # F5: Precio↑ + Yield↑
    if 'LocalPrice_ant' in cons.columns and 'LocalPrice' in cons.columns:
        dP = cons['LocalPrice'] - cons['LocalPrice_ant']
        f5 = cons[(dP > 0) & (dY > 0)]
        add_flag('Precio↑ + Yield↑', '🔴 CRÍTICA', f5,
                 lambda r: f"ΔP={r['LocalPrice']-r['LocalPrice_ant']:+.3f} | "
                           f"ΔY={(r['Yield_final']-r['Yield_final_ant'])*1e4:+.0f}bps")
    else:
        add_flag('Precio↑ + Yield↑', '🔴 CRÍTICA', None, estado='SIN LocalPrice')

    # F6: Métricas sin actualizar (yield y duration idénticas)
    f6 = cons[cons['Yield_final_ant'].notna() &
              np.isclose(cons['Yield_final'], cons['Yield_final_ant'], atol=1e-10) &
              np.isclose(cons['Duration_final'], cons['Duration_final_ant'], atol=1e-10)]
    add_flag('Métricas sin actualizar', '🟡 MEDIA', f6,
             lambda r: f"Yield y Duration idénticas al período anterior ({r['Etapa_final']})")

    # F7: Δ Yield
    f7 = cons[dY.abs() >= DELTA_YIELD_UMBRAL]
    add_flag(f"Δ Yield ≥ {DELTA_YIELD_UMBRAL*100:.0f}pp", '🟠 ALTA', f7,
             lambda r: f"Ant={r['Yield_final_ant']:.2%} → Act={r['Yield_final']:.2%}")
else:
    for nom, sev in (('Precio↑ + Yield↑', '🔴 CRÍTICA'),
                     ('Métricas sin actualizar', '🟡 MEDIA'),
                     (f"Δ Yield ≥ {DELTA_YIELD_UMBRAL*100:.0f}pp", '🟠 ALTA')):
        add_flag(nom, sev, None, estado='INACTIVO — sin período previo')

flags_df = pd.concat(flags_rows, ignore_index=True) if flags_rows else pd.DataFrame(columns=['Flag'])
flags_res_df = pd.DataFrame(flags_resumen)
for _, r in flags_res_df.iterrows():
    print(f"   {r['Flag']:<40} {r['Estado']:<32} n={r['N_posiciones']}")

# =============================================================================
# [6d] AGREGADOS POR FONDO — AW / DW
# =============================================================================
# AW = Σ(Y·MV)/ΣMV     DW = Σ(Y·MV·D)/Σ(MV·D)
# Agrupación: Risk_Country (Risk_Currency en MLDL). Se toma TODA la muestra
# (el filtro Long/Asset ya viene de etapas previas).
print("\n[6d] Agregados AW/DW por fondo...")

def agregar(df):
    """Devuelve filas por (Fondo, Grupo) + TOTAL por fondo."""
    out = []
    b = df[df['Yield_final'].notna() & (df['TotalMVal'] > 0)].copy()
    n_resuelto = len(b)
    n_total = len(df)
    pct_res = (n_resuelto / n_total * 100) if n_total else 0
    for fondo, bf in b.groupby('FundShortName'):
        gcol = 'Risk_Currency' if es_fondo_local(fondo) else 'Risk_Country'
        bf = bf.copy()
        bf['_g'] = limpiar_txt(bf[gcol]).replace('', 'SIN DATO') if gcol in bf.columns else 'SIN DATO'
        mv_tot = bf['TotalMVal'].sum()
        mvd_tot = (bf['TotalMVal'] * bf['Duration_final'].fillna(0)).sum()

        def fila(g, sub, lbl):
            mv = sub['TotalMVal'].sum()
            mvd = (sub['TotalMVal'] * sub['Duration_final'].fillna(0)).sum()
            aw = (sub['Yield_final'] * sub['TotalMVal']).sum() / mv if mv else np.nan
            dw = ((sub['Yield_final'] * sub['TotalMVal'] * sub['Duration_final'].fillna(0)).sum() / mvd
                  if mvd > 0 else np.nan)
            return {'Fondo': fondo, 'Agrupacion': gcol, 'Grupo': lbl,
                    'N_posiciones': len(sub), 'MV': mv,
                    'Peso_MV': mv / mv_tot if mv_tot else np.nan,
                    'Peso_MVxDur': mvd / mvd_tot if mvd_tot else np.nan,
                    'Duration_AW': mvd / mv if mv else np.nan,
                    'AW_Yield': aw, 'DW_Yield': dw}

        out.append(fila(None, bf, 'TOTAL'))
        grupos = [fila(g, s, g) for g, s in bf.groupby('_g')]
        out += sorted(grupos, key=lambda x: -x['MV'])
    return pd.DataFrame(out), pct_res

agg, pct_res = agregar(cons)
print(f"\n[6d] Agregados AW/DW por fondo ({int(len(cons[cons['Yield_final'].notna() & (cons['TotalMVal']>0)]))} posiciones resueltas / {len(cons)} totales = {pct_res:.1f}%)...")
for _, r in agg[agg['Grupo'] == 'TOTAL'].iterrows():
    print(f"   {r['Fondo']:<15} AW={r['AW_Yield']:.2%}  DW={r['DW_Yield']:.2%}  "
          f"Dur={r['Duration_AW']:.2f}  N={r['N_posiciones']}")

# ── Atribución: contribución de cada grupo al total del fondo ────────────────
atr = agg[agg['Grupo'] != 'TOTAL'].copy()
atr['Contrib_AW_bps'] = atr['Peso_MV'] * atr['AW_Yield'] * 1e4
atr['Contrib_DW_bps'] = atr['Peso_MVxDur'] * atr['DW_Yield'] * 1e4

if prev is not None:
    agg_ant = agregar(prev)
    k = ['Fondo', 'Grupo']
    atr = atr.merge(agg_ant[k + ['Peso_MV', 'Peso_MVxDur', 'AW_Yield', 'DW_Yield']],
                    on=k, how='outer', suffixes=('', '_ant'))
    atr = atr[atr['Grupo'] != 'TOTAL']
    for m, w in (('AW', 'Peso_MV'), ('DW', 'Peso_MVxDur')):
        wa, wm = atr[w].fillna(0), atr[f'{w}_ant'].fillna(0)
        ya, ym = atr[f'{m}_Yield'].fillna(0), atr[f'{m}_Yield_ant'].fillna(0)
        atr[f'Contrib_{m}_ant_bps'] = wm * ym * 1e4
        atr[f'Δ_Contrib_{m}_bps']   = (wa * ya - wm * ym) * 1e4
        atr[f'Efecto_Peso_{m}_bps']  = (wa - wm) * ym * 1e4      # cambio de asignación
        atr[f'Efecto_Yield_{m}_bps'] = wa * (ya - ym) * 1e4      # cambio de yield
else:
    print("   [WARN] No existe período previo para esta corrida — atribución solo "
          "muestra la contribución del período actual (sin Δ ni efectos).")

atr = atr.sort_values(['Fondo', 'MV'], ascending=[True, False])

# =============================================================================
# [7] GUARDAR
# =============================================================================
print(f"\n[6] Guardando...")
COLS_OUT = ['FundShortName', 'ID_Fund', 'PK2', 'ISIN', 'Name_Instrumento', 'CompanyName',
            'CalcType', 'Risk_Country', 'Risk_Currency', 'Hedge_Currency',
            'LocalPrice', 'AI', 'Yield_final', 'Duration_final',
            'Etapa_final', 'Estado_final', 'Override_Source', 'Override_Comment',
            'Override_Currency', 'TotalMVal']
cols_final = [c for c in COLS_OUT if c in cons.columns]

with pd.ExcelWriter(OUT_OVERRIDES, engine='openpyxl') as w:
    cons[cols_final].to_excel(w, sheet_name='cartera_final', index=False)
    fmt_ws(w.sheets['cartera_final'], NAVY)
    falt[cols_final].to_excel(w, sheet_name='faltante', index=False)
    fmt_ws(w.sheets['faltante'], RED)
    if sin_match_fondo:
        pd.DataFrame(sin_match_fondo).to_excel(w, sheet_name='fund_name_sin_match', index=False)
        fmt_ws(w.sheets['fund_name_sin_match'], AMBER_F)

    # ── Flags ──
    flags_res_df.to_excel(w, sheet_name='flags_resumen', index=False)
    fmt_ws(w.sheets['flags_resumen'], RED, freeze='A2')
    flags_df.to_excel(w, sheet_name='flags', index=False)
    fmt_ws(w.sheets['flags'], RED)
    ws_f = w.sheets['flags']
    for j, col in enumerate(flags_df.columns, 1):
        if col == 'Yield_final':
            for i in range(2, len(flags_df) + 2): ws_f.cell(i, j).number_format = '0.00%'

    # ── Agregados ──
    agg.to_excel(w, sheet_name='Yield Agregada', index=False)
    fmt_ws(w.sheets['Yield Agregada'], NAVY)
    atr.to_excel(w, sheet_name='Atribucion Yield', index=False)
    fmt_ws(w.sheets['Atribucion Yield'], NAVY)
    for sh, df_ in (('Yield Agregada', agg), ('Atribucion Yield', atr)):
        ws_ = w.sheets[sh]
        for j, col in enumerate(df_.columns, 1):
            fmt = ('0.00%' if ('Yield' in col or col.startswith('Peso')) else
                   '+0.0;-0.0' if col.endswith('bps') else
                   '0.00' if col.startswith('Duration') else
                   '#,##0' if col == 'MV' else None)
            if fmt:
                for i in range(2, len(df_) + 2): ws_.cell(i, j).number_format = fmt
        if sh == 'Yield Agregada':   # resaltar filas TOTAL
            bold = Font(bold=True, name='Calibri', size=9)
            for i, g in enumerate(df_['Grupo'], 2):
                if g == 'TOTAL':
                    for j in range(1, len(df_.columns) + 1):
                        ws_.cell(i, j).font = bold
                        ws_.cell(i, j).fill = PatternFill('solid', fgColor='EFF6FF')

    rr = [{'Concepto': 'Posiciones totales', 'Valor': n_tot},
          {'Concepto': 'Resueltas (cartera completa)', 'Valor': n_ok,
           'Comentario': f"{n_ok/n_tot:.1%}"},
          {'Concepto': '  vía OVERRIDES', 'Valor': n_over},
          {'Concepto': 'FALTANTES', 'Valor': n_tot - n_ok},
          {'Concepto': 'PK2 únicos faltantes', 'Valor': plantilla_pk2['PK2'].nunique()}]
    for k, v in cons.loc[cons['Resuelto_final'], 'Etapa_final'].value_counts().items():
        rr.append({'Concepto': f'  fuente: {k}', 'Valor': int(v)})
    pd.DataFrame(rr).to_excel(w, sheet_name='RESUMEN', index=False)
    fmt_ws(w.sheets['RESUMEN'], '2E4057', freeze='A2')

# ── Yields sospechosas: instrumentos con flags activos (resueltos pero con warnings)
suspicious_clean = flags_df[flags_df['Yield_final'].notna()].copy() if len(flags_df) else pd.DataFrame()
if len(suspicious_clean):
    cols_keep = ['Flag', 'Severidad', 'ID_Fund', 'PK2', 'Name_Instrumento',
                 'Risk_Country', 'Risk_Currency', 'Yield_final', 'Duration_final', 'Detalle']
    cols_keep = [c for c in cols_keep if c in suspicious_clean.columns]
    suspicious_clean = suspicious_clean[cols_keep].copy()
    suspicious_clean['Yield_override'] = ''         # columna para opcionalmente cambiar yield
    suspicious_clean['Marcar_como_DEF'] = ''        # columna para marcar como DEF (Yield=0, Dur=0.5)
    suspicious_clean['Duration_override'] = ''
    suspicious_clean = suspicious_clean.sort_values('Flag', ascending=True)

with pd.ExcelWriter(OUT_OVERRIDES_PENDIENTE, engine='openpyxl') as w:
    plantilla.to_excel(w, sheet_name='OVERRIDES_a_completar', index=False)
    fmt_ws(w.sheets['OVERRIDES_a_completar'], AMBER_F)
    if len(suspicious_clean):
        suspicious_clean.to_excel(w, sheet_name='yields_sospechosas', index=False)
        fmt_ws(w.sheets['yields_sospechosas'], RED)

print(f"\n{'='*70}")
print(f"  Cartera final : {n_ok:,}/{n_tot:,} ({n_ok/n_tot:.1%})")
print(f"  FALTANTES     : {n_tot-n_ok:,} ({plantilla_pk2['PK2'].nunique()} PK2 únicos)")
print(f"  Output        : {OUT_OVERRIDES}")
print(f"  Plantilla     : {OUT_OVERRIDES_PENDIENTE}  ← completar y guardar como {os.path.basename(OVERRIDES_PATH)}")
print(f"{'='*70}")