# =============================================================================
# 01_MAPEO_METRICAS.py — Mapeo de MÉTRICAS YA CALCULADAS (Yield / Duration)
# =============================================================================
# OBJETIVO
#   Evaluar cuánto del universo se resuelve con métricas YA CALCULADAS por los
#   proveedores, para saber qué queda realmente para PROP.
#
# CRITERIO DE ÉXITO — TUPLA COMPLETA
#   Un instrumento está RESUELTO solo si tiene Yield Y Duration.
#   Si tiene una sola de las dos → NO sirve → va a PROP.
#
# CASCADA DE RELEVANCIA:  JPM > RA > BBG
#   No es solo prioridad de valor: define A QUIÉN SE LE PREGUNTA.
#   Se resuelve primero con JPM, luego RA (plug-in), y SOLO los que siguen
#   incompletos se consultan en el terminal BBG → minimiza consumo de terminal.
#
# DEDUPLICACIÓN
#   Muchos PK2 se comparten entre fondos. Las métricas se resuelven UNA VEZ a
#   nivel de instrumento único (PK2/ISIN) y luego se hace merge de vuelta a las
#   filas fondo × PK2.
#
# LÓGICA DE FAMILIA (REGS / 144A / EMTN)
#   Si el proveedor no tiene la métrica del ISIN del fondo, se busca en los
#   hermanos de serie (misma familia = misma TD detrás). Aplica a JPM y BBG.
#   RA usa nemotécnico chileno → no aplica.
#   Trazabilidad en Origen_* con formato 'HERMANO:{isin}'.
#
# PROVEEDORES
#   JPM  → CEMBI (ISIN_ID): Yield_to_Worst / IR_Duration_to_Worst
#          GBI   (ISIN)    : YIELD / MOD DUR
#   RA   → RA_TIR.xlsx: TIR / DURACION (+ MONEDA, PERIODICIDAD_CUPONES)
#          OJO: TIR se usa como INPUT tal cual en esta etapa del mapeo.
#          La conversión TIR → Yield equivalente queda pendiente (por eso se
#          arrastra PERIODICIDAD_CUPONES).
#   BBG  → YAS_BOND_YLD / YAS_MOD_DUR
#          Hedgeados: ADEMÁS YAS_XCCY_FIXED_COUPON_EQUIVALENT
#          (se piden LAS DOS yields; si el XCCY falla, la yield normal en USD
#           alimenta la futura calculadora de drops)
#          No hedgeados: solo la yield normal.
#
# NOTA — INDEXADOS / SWAPEADOS
#   Para Yield NO se discrimina por indexación ni swap: se toma la métrica del
#   proveedor tal cual. Los drops (swap) y breakevens (inflación) se resolverán
#   después con una calculadora dedicada.
#
# OUTPUT: MAPEO_METRICAS_{FECHA}.xlsx → con_ISIN | sin_ISIN | RESUMEN
# =============================================================================

import os
import sys
import time
import warnings
import numpy as np
import pandas as pd
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

_script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
if _script_dir not in sys.path: sys.path.insert(0, _script_dir)
if os.getcwd() not in sys.path:  sys.path.insert(0, os.getcwd())

from pipeline_config import (FECHA, HOJA_RA, OUT_UNIVERSO, OUT_METRICAS,
                             JPM_PATH, RA_TIR_PATH, RA_CURRENCIES,
                             RA_ISSUE_TYPE, ISSUE_TYPE_BONO, BBG_BATCH)

# ============================================================
# CONFIGURACIÓN — editable
# ============================================================
CONSULTAR_BBG = True        # False = correr solo JPM + RA (sin terminal)

FNAME_UNIVERSO = OUT_UNIVERSO
FNAME_JPM      = JPM_PATH
FNAME_RA       = RA_TIR_PATH
FNAME_OUT      = OUT_METRICAS

# Cascada: orden de PREFERENCIA para elegir el valor final (destacado)
CASCADA = ['JPM', 'RA', 'BBG']

# MODO MAPEO COMPLETO:
#   True  = se consulta a TODOS los proveedores por TODOS los instrumentos.
#           Objetivo: ver QUÉ HAY disponible en cada proveedor (foto completa).
#           La cascada solo decide cuál se destaca como valor final.
#   False = modo ahorro: a BBG solo se le piden los que JPM+RA no resolvieron.
MODO_MAPEO_COMPLETO = True

# XCCY: True = solo a hedgeados que además van a BBG.
# False = a TODOS los hedgeados (mapeo completo, más consumo de terminal).
XCCY_SOLO_PENDIENTES = False

BBG_BATCH = 100
# ============================================================

print("=" * 70)
print(f"01_MAPEO_METRICAS | FECHA={FECHA} | cascada={' > '.join(CASCADA)} | BBG={'ON' if CONSULTAR_BBG else 'OFF'}")
print("=" * 70)


# ── Helpers ───────────────────────────────────────────────────────────────────
def norm(x):
    return str(x).strip()

def limpiar_txt(serie):
    """str con '' para nulos. Robusto a StringArray (pandas 3), donde
    astype(str) preserva NaN y rompe las comparaciones con ''."""
    return (serie.fillna('').astype(str).str.strip()
            .replace({'nan': '', 'None': '', 'NaN': '', '<NA>': ''}))

def pick_col(df, *cands):
    for c in cands:
        if c in df.columns:
            return c
    return None

def fmt_ws(ws, hex_color, freeze='B2'):
    fill = PatternFill('solid', fgColor=hex_color)
    font = Font(color='FFFFFF', bold=True, size=9, name='Calibri')
    aln  = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for cell in ws[1]:
        cell.fill = fill; cell.font = font; cell.alignment = aln
    if freeze:
        ws.freeze_panes = freeze
    ws.row_dimensions[1].height = 28
    for col in ws.columns:
        w = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 8), 42)


# =============================================================================
# [1] UNIVERSO + TABLA DE INSTRUMENTOS ÚNICOS (deduplicado)
# =============================================================================
print(f"\n[1] Leyendo universo: {os.path.basename(FNAME_UNIVERSO)}")

if not os.path.exists(FNAME_UNIVERSO):
    print(f"   ERROR: no existe {FNAME_UNIVERSO}"); sys.exit(1)

df_con = pd.read_excel(FNAME_UNIVERSO, sheet_name='con_ISIN', engine='openpyxl')
df_sin = pd.read_excel(FNAME_UNIVERSO, sheet_name='sin_ISIN', engine='openpyxl')

COLS_TXT = ('ISIN', 'Name_Instrumento', 'Risk_Currency', 'Hedge_Currency', 'serie_hermanos')
for _df in (df_con, df_sin):
    for c in COLS_TXT:
        if c in _df.columns:
            _df[c] = limpiar_txt(_df[c])
    _df['Name_upper'] = _df['Name_Instrumento'].str.upper()

print(f"   Filas fondo×PK2 — con_ISIN: {len(df_con):,} | sin_ISIN: {len(df_sin):,}")

# ── Tabla de INSTRUMENTOS ÚNICOS: se consulta una sola vez ───────────────────
df_todo = pd.concat([df_con, df_sin], ignore_index=True)
COLS_INSTR = ['PK2', 'ISIN', 'Name_Instrumento', 'Name_upper', 'Risk_Currency',
              'Hedge_Currency', 'serie_hermanos', 'Issue_Type_Code', 'Risk_Country']
COLS_INSTR = [c for c in COLS_INSTR if c in df_todo.columns]
instr = df_todo[COLS_INSTR].drop_duplicates(subset=['PK2']).reset_index(drop=True)

n_filas_total = len(df_todo)
print(f"   Filas totales (fondo×PK2)     : {n_filas_total:,}")
print(f"   Instrumentos únicos (PK2)     : {len(instr):,}")
print(f"   ISIN únicos                   : {instr.loc[instr['ISIN'] != '', 'ISIN'].nunique():,}")
print(f"   → dedup evita                 : {n_filas_total - len(instr):,} consultas repetidas")

# Mapa de hermanos (ISIN → [hermanos])
hermanos_map = {}
for _, r in instr.iterrows():
    h = r.get('serie_hermanos', '')
    if h:
        hermanos_map[r['ISIN']] = [x.strip() for x in h.split(',') if x.strip()]
print(f"   ISIN con hermanos de familia  : {len(hermanos_map):,}")

# Hedge_Currency por ISIN
hedge_lkp = {r['ISIN']: r['Hedge_Currency'].upper()
             for _, r in instr.iterrows()
             if r.get('ISIN') and r.get('Hedge_Currency')}
print(f"   Instrumentos hedgeados (XCCY) : {len(hedge_lkp):,}")

# Numéricas → NaN | de texto → '' con dtype object (evita choque de dtype al asignar)
for c in ['JPM_Yield', 'JPM_Duration', 'RA_Yield', 'RA_Duration',
          'BBG_Yield', 'BBG_Duration', 'BBG_XCCY_Yield']:
    instr[c] = np.nan
for c in ['JPM_Fuente', 'Origen_JPM', 'RA_Moneda', 'RA_Periodicidad',
          'Origen_BBG_Yield', 'Origen_BBG_Duration', 'Origen_BBG_XCCY']:
    instr[c] = pd.Series([''] * len(instr), index=instr.index, dtype='object')


# =============================================================================
# [2] JPM — primer eslabón de la cascada
# =============================================================================
print(f"\n[2] JPM — {os.path.basename(FNAME_JPM)}")

jpm_y, jpm_d, jpm_s = {}, {}, {}

if os.path.exists(FNAME_JPM):
    try:
        cem = pd.read_excel(FNAME_JPM, sheet_name='CEMBI', engine='openpyxl')
        k  = pick_col(cem, 'ISIN_ID', 'ISIN')
        cy = pick_col(cem, 'Yield_to_Worst', 'Blended_YTM', 'Stripped_YTM')
        cd = pick_col(cem, 'IR_Duration_to_Worst', 'EIR_Duration', 'Spread_Duration')
        print(f"   CEMBI: {len(cem):>5} filas | llave={k} | Y={cy} | D={cd}")
        for _, r in cem.iterrows():
            i = norm(r.get(k, ''))
            if not i or i in ('nan', 'None'): continue
            vy = pd.to_numeric(r.get(cy), errors='coerce')
            vd = pd.to_numeric(r.get(cd), errors='coerce')
            if pd.notna(vy): jpm_y[i] = float(vy)
            if pd.notna(vd): jpm_d[i] = float(vd)
            if pd.notna(vy) or pd.notna(vd): jpm_s[i] = 'CEMBI'
    except Exception as e:
        print(f"   [WARN] CEMBI: {e}")

    try:
        gbi = pd.read_excel(FNAME_JPM, sheet_name='GBI_Embroad', engine='openpyxl')
        k  = pick_col(gbi, 'ISIN', 'LOCAL ID')
        gy = pick_col(gbi, 'YIELD', 'Yield')
        gd = pick_col(gbi, 'MOD DUR', 'MAC DUR')
        print(f"   GBI  : {len(gbi):>5} filas | llave={k} | Y={gy} | D={gd}")
        for _, r in gbi.iterrows():
            i = norm(r.get(k, ''))
            if not i or i in ('nan', 'None'): continue
            vy = pd.to_numeric(r.get(gy), errors='coerce')
            vd = pd.to_numeric(r.get(gd), errors='coerce')
            if pd.notna(vy) and i not in jpm_y: jpm_y[i] = float(vy)
            if pd.notna(vd) and i not in jpm_d: jpm_d[i] = float(vd)
            if i not in jpm_s and (pd.notna(vy) or pd.notna(vd)): jpm_s[i] = 'GBI'
    except Exception as e:
        print(f"   [WARN] GBI: {e}")
else:
    print(f"   [WARN] No encontrado: {FNAME_JPM}")

# Asignación directa + fallback por hermano de familia
n_dir = n_her = 0
for idx, r in instr.iterrows():
    i = r['ISIN']
    if not i: continue
    if i in jpm_y or i in jpm_d:
        instr.at[idx, 'JPM_Yield']    = jpm_y.get(i, np.nan)
        instr.at[idx, 'JPM_Duration'] = jpm_d.get(i, np.nan)
        instr.at[idx, 'JPM_Fuente']   = jpm_s.get(i, '')
        instr.at[idx, 'Origen_JPM']   = 'DIRECTO'
        n_dir += 1
    else:
        for h in hermanos_map.get(i, []):
            if h in jpm_y or h in jpm_d:
                instr.at[idx, 'JPM_Yield']    = jpm_y.get(h, np.nan)
                instr.at[idx, 'JPM_Duration'] = jpm_d.get(h, np.nan)
                instr.at[idx, 'JPM_Fuente']   = jpm_s.get(h, '')
                instr.at[idx, 'Origen_JPM']   = f'HERMANO:{h}'
                n_her += 1
                break

instr['JPM_ok'] = instr['JPM_Yield'].notna() & instr['JPM_Duration'].notna()
print(f"   Con dato JPM: directo={n_dir} | vía hermano={n_her}")
print(f"   → TUPLA completa JPM: {int(instr['JPM_ok'].sum()):,}")


# =============================================================================
# [3] RA — segundo eslabón
# =============================================================================
print(f"\n[3] RA — {os.path.basename(FNAME_RA)} (hoja '{HOJA_RA}')")

ra_tir, ra_dur, ra_mon, ra_per = {}, {}, {}, {}

if os.path.exists(FNAME_RA):
    try:
        _ra = pd.read_excel(FNAME_RA, sheet_name=HOJA_RA, engine='openpyxl')
        cols = list(_ra.columns)
        # Col A = nemotécnico (su header es la fecha del snapshot)
        _ra = _ra.rename(columns={cols[0]: 'Name_Instrumento'})
        _ra = _ra.drop(columns=[c for c in _ra.columns
                                if str(c).startswith('Unnamed') or str(c).startswith('=')],
                       errors='ignore')
        _ra['Name_Instrumento'] = limpiar_txt(_ra['Name_Instrumento']).str.upper()

        c_t = pick_col(_ra, 'TIR')
        c_d = pick_col(_ra, 'DURACION', 'DURACIÓN')
        c_m = pick_col(_ra, 'MONEDA')
        c_p = pick_col(_ra, 'PERIODICIDAD_CUPONES')
        print(f"   Filas: {len(_ra)} | TIR={c_t} | DURACION={c_d} | MONEDA={c_m} | PER={c_p}")

        for _, r in _ra.iterrows():
            nm = r['Name_Instrumento']
            if not nm: continue
            vt = pd.to_numeric(r.get(c_t), errors='coerce') if c_t else np.nan
            vd = pd.to_numeric(r.get(c_d), errors='coerce') if c_d else np.nan
            if pd.notna(vt): ra_tir[nm] = float(vt)
            if pd.notna(vd): ra_dur[nm] = float(vd)
            if c_m: ra_mon[nm] = norm(r.get(c_m, ''))
            if c_p: ra_per[nm] = norm(r.get(c_p, ''))
        print(f"   RA con TIR={len(ra_tir)} | DURACION={len(ra_dur)}")
    except Exception as e:
        print(f"   [WARN] No se pudo leer RA_TIR: {e}")
else:
    print(f"   [WARN] No encontrado: {FNAME_RA}")

# Elegibilidad "de negocio" para RA (CLP/CLF + bono). En MODO_MAPEO_COMPLETO
# igual se intenta el match por nemotécnico para TODOS, para ver qué hay
# realmente en RA; la columna Aplica_RA conserva la elegibilidad esperada.
aplica_ra = (instr['Risk_Currency'].str.upper().isin(RA_CURRENCIES) &
             (pd.to_numeric(instr.get('Issue_Type_Code'), errors='coerce') == RA_ISSUE_TYPE))
instr['Aplica_RA'] = aplica_ra

mask_ra = pd.Series(True, index=instr.index) if MODO_MAPEO_COMPLETO else aplica_ra

instr.loc[mask_ra, 'RA_Yield']        = instr.loc[mask_ra, 'Name_upper'].map(ra_tir)
instr.loc[mask_ra, 'RA_Duration']     = instr.loc[mask_ra, 'Name_upper'].map(ra_dur)
instr.loc[mask_ra, 'RA_Moneda']       = instr.loc[mask_ra, 'Name_upper'].map(ra_mon)
instr.loc[mask_ra, 'RA_Periodicidad'] = instr.loc[mask_ra, 'Name_upper'].map(ra_per)

instr['RA_ok'] = instr['RA_Yield'].notna() & instr['RA_Duration'].notna()
print(f"   Elegibles RA (CLP/CLF + Issue={RA_ISSUE_TYPE}): {int(aplica_ra.sum()):,}")
print(f"   → TUPLA completa RA: {int(instr['RA_ok'].sum()):,}")
n_ra_fuera = int((instr['RA_ok'] & ~aplica_ra).sum())
if n_ra_fuera:
    print(f"   [INFO] {n_ra_fuera} con datos RA fuera del criterio CLP/CLF+bono (match por nemotécnico)")


# =============================================================================
# [4] BBG — SOLO los que siguen sin tupla completa
# =============================================================================
instr['Pendiente_BBG'] = ~(instr['JPM_ok'] | instr['RA_ok'])

isins_con = sorted(set(instr.loc[instr['ISIN'] != '', 'ISIN']))
isins_pend = sorted(set(instr.loc[instr['Pendiente_BBG'] & (instr['ISIN'] != ''), 'ISIN']))

# En modo mapeo completo se consulta TODO el universo con ISIN.
pend_isins = isins_con if MODO_MAPEO_COMPLETO else isins_pend

print(f"\n[4] BBG — modo {'MAPEO COMPLETO' if MODO_MAPEO_COMPLETO else 'AHORRO'}")
print(f"   Sin tupla tras JPM+RA        : {int(instr['Pendiente_BBG'].sum()):,}")
print(f"   ISIN a consultar en BBG      : {len(pend_isins):,}"
      f"{'  (todos)' if MODO_MAPEO_COMPLETO else '  (solo pendientes)'}")
if MODO_MAPEO_COMPLETO:
    print(f"   → en modo ahorro serían      : {len(isins_pend):,}")
print(f"   Sin ISIN (no consultables)   : {int((instr['Pendiente_BBG'] & (instr['ISIN'] == '')).sum()):,}")

if XCCY_SOLO_PENDIENTES:
    hedge_query = {i: hc for i, hc in hedge_lkp.items() if i in set(pend_isins)}
else:
    hedge_query = dict(hedge_lkp)
print(f"   Hedgeados para XCCY          : {len(hedge_query):,}")

yld_map, dur_map, xccy_map = {}, {}, {}
org_y, org_d, org_x = {}, {}, {}

if CONSULTAR_BBG and (pend_isins or hedge_query):
    from xbbg import blp

    def bdp_batch(tickers, fld, **kw):
        out = {}
        for i in range(0, len(tickers), BBG_BATCH):
            lote = tickers[i:i + BBG_BATCH]
            try:
                res = blp.bdp(tickers=lote, flds=fld, **kw)
                if res is None or res.empty: continue
                res.index   = [norm(x) for x in res.index]
                res.columns = [norm(c).upper() for c in res.columns]
                if fld.upper() not in res.columns: continue
                for tkr in res.index:
                    v = res.loc[tkr].get(fld.upper())
                    if pd.notna(v): out[tkr] = float(v)
            except Exception as e:
                print(f"      [WARN] bdp {fld} lote {i}: {e}")
        return out

    def cascada_bbg(isins, fld, destino, origen, etq):
        res = bdp_batch([f"{i} Corp" for i in isins], fld, settle_dt=FECHA)
        for i in isins:
            v = res.get(f"{i} Corp")
            if v is not None:
                destino[i] = v; origen[i] = 'DIRECTO'
        pend = [i for i in isins if i not in destino]
        print(f"      {etq} directo={len(destino)} | pendientes={len(pend)}")

        if pend:
            res = bdp_batch([f"{i}@BGN Corp" for i in pend], fld, settle_dt=FECHA)
            for i in pend:
                v = res.get(f"{i}@BGN Corp")
                if v is not None:
                    destino[i] = v; origen[i] = 'BGN'
            pend = [i for i in pend if i not in destino]
            print(f"      {etq} +BGN={len(destino)} | pendientes={len(pend)}")

        if pend:
            cand, back = [], {}
            for i in pend:
                for h in hermanos_map.get(i, []):
                    t = f"{h} Corp"; cand.append(t); back[t] = (i, h)
            if cand:
                res = bdp_batch(sorted(set(cand)), fld, settle_dt=FECHA)
                for t, v in res.items():
                    if t in back:
                        i, h = back[t]
                        if i not in destino:
                            destino[i] = v; origen[i] = f'HERMANO:{h}'
            pend = [i for i in pend if i not in destino]
            nh = sum(1 for o in origen.values() if str(o).startswith('HERMANO'))
            print(f"      {etq} +hermano={nh} | sin dato={len(pend)}")

    t0 = time.perf_counter()
    if pend_isins:
        print("   → YAS_BOND_YLD")
        cascada_bbg(pend_isins, 'YAS_BOND_YLD', yld_map, org_y, 'YLD')
        print("   → YAS_MOD_DUR")
        cascada_bbg(pend_isins, 'YAS_MOD_DUR', dur_map, org_d, 'DUR')

    # XCCY — a los hedgeados se les pide TAMBIÉN la yield normal (para drops)
    if hedge_query:
        FLD_X = 'YAS_XCCY_FIXED_COUPON_EQUIVALENT'
        faltan_yld_hedge = [i for i in hedge_query if i not in yld_map]
        if faltan_yld_hedge:
            print(f"   → YAS_BOND_YLD extra para {len(faltan_yld_hedge)} hedgeados (base drops)")
            cascada_bbg(faltan_yld_hedge, 'YAS_BOND_YLD', yld_map, org_y, 'YLD-H')

        print(f"   → {FLD_X} ({len(hedge_query)} hedgeados)")
        for n, (i, hc) in enumerate(sorted(hedge_query.items()), 1):
            for tkr, etq in ((f"{i} Corp", 'DIRECTO'), (f"{i}@BGN Corp", 'BGN')):
                if i in xccy_map: break
                try:
                    res = blp.bdp(tickers=tkr, flds=FLD_X,
                                  YAS_XCCY_FOREIGN_CURRENCY=hc, settle_dt=FECHA)
                    if res is not None and not res.empty:
                        res.index   = [norm(x) for x in res.index]
                        res.columns = [norm(c).upper() for c in res.columns]
                        if FLD_X in res.columns and tkr in res.index:
                            v = res.loc[tkr].get(FLD_X)
                            if pd.notna(v):
                                xccy_map[i] = float(v); org_x[i] = etq
                except Exception:
                    pass
            if n % 25 == 0:
                print(f"      {n}/{len(hedge_query)} — XCCY recuperados {len(xccy_map)}")
        print(f"      XCCY total: {len(xccy_map)}/{len(hedge_query)}")

    print(f"   Tiempo BBG: {time.perf_counter() - t0:0.1f}s")
else:
    print("   BBG OMITIDO (CONSULTAR_BBG=False o nada pendiente)")

instr['BBG_Yield']           = instr['ISIN'].map(yld_map)
instr['BBG_Duration']        = instr['ISIN'].map(dur_map)
instr['BBG_XCCY_Yield']      = instr['ISIN'].map(xccy_map)
instr['Origen_BBG_Yield']    = instr['ISIN'].map(org_y)
instr['Origen_BBG_Duration'] = instr['ISIN'].map(org_d)
instr['Origen_BBG_XCCY']     = instr['ISIN'].map(org_x)
instr['BBG_ok'] = instr['BBG_Yield'].notna() & instr['BBG_Duration'].notna()


# =============================================================================
# [5] CONSOLIDAR con la cascada JPM > RA > BBG
# =============================================================================
print(f"\n[5] Consolidando (cascada {' > '.join(CASCADA)})...")

COL_Y = {'JPM': 'JPM_Yield',    'RA': 'RA_Yield',    'BBG': 'BBG_Yield'}
COL_D = {'JPM': 'JPM_Duration', 'RA': 'RA_Duration', 'BBG': 'BBG_Duration'}

def elegir(row, cols):
    for p in CASCADA:
        v = row.get(cols[p])
        if pd.notna(v):
            return v, p
    return np.nan, ''

vals = instr.apply(lambda r: pd.Series(elegir(r, COL_Y) + elegir(r, COL_D),
                                       index=['Yield_final', 'Fuente_Yield',
                                              'Duration_final', 'Fuente_Duration']), axis=1)
instr = pd.concat([instr, vals], axis=1)

instr['Tiene_Yield']    = instr['Yield_final'].notna()
instr['Tiene_Duration'] = instr['Duration_final'].notna()
instr['RESUELTO']       = instr['Tiene_Yield'] & instr['Tiene_Duration']
instr['Destino']        = np.where(instr['RESUELTO'], 'METRICAS', 'PROP')

# ── Foto de disponibilidad: qué proveedor tiene QUÉ ──────────────────────────
# Tupla completa por proveedor
def _combo(row):
    ps = [p for p, c in (('JPM', 'JPM_ok'), ('RA', 'RA_ok'), ('BBG', 'BBG_ok')) if row.get(c)]
    return '|'.join(ps) if ps else 'NINGUNO'

instr['Proveedores_tupla']   = instr.apply(_combo, axis=1)
instr['N_Proveedores_tupla'] = instr[['JPM_ok', 'RA_ok', 'BBG_ok']].fillna(False).sum(axis=1).astype(int)

# Disponibilidad suelta por métrica (aunque no forme tupla)
instr['N_Proveedores_Yield'] = (instr[['JPM_Yield', 'RA_Yield', 'BBG_Yield']]
                                .notna().sum(axis=1).astype(int))
instr['N_Proveedores_Dur']   = (instr[['JPM_Duration', 'RA_Duration', 'BBG_Duration']]
                                .notna().sum(axis=1).astype(int))

# Dispersión entre proveedores (para detectar inconsistencias)
instr['Yield_min']  = instr[['JPM_Yield', 'RA_Yield', 'BBG_Yield']].min(axis=1)
instr['Yield_max']  = instr[['JPM_Yield', 'RA_Yield', 'BBG_Yield']].max(axis=1)
instr['Yield_dif']  = instr['Yield_max'] - instr['Yield_min']
instr['Dur_min']    = instr[['JPM_Duration', 'RA_Duration', 'BBG_Duration']].min(axis=1)
instr['Dur_max']    = instr[['JPM_Duration', 'RA_Duration', 'BBG_Duration']].max(axis=1)
instr['Dur_dif']    = instr['Dur_max'] - instr['Dur_min']

# PK2 = {ID_Instrumento}-{ID_Curr}: un mismo ISIN puede estar en 2 PK2 con
# distinta moneda → son papeles distintos, pero el proveedor entrega la métrica
# en la moneda del papel. Se marca para revisión manual.
_cnt = instr.loc[instr['ISIN'] != ''].groupby('ISIN')['PK2'].transform('size')
instr['ISIN_multi_PK2'] = False
instr.loc[instr['ISIN'] != '', 'ISIN_multi_PK2'] = (_cnt > 1).values

print(f"   Instrumentos RESUELTOS: {int(instr['RESUELTO'].sum()):,}/{len(instr):,}")
print(f"   → a PROP              : {int((~instr['RESUELTO']).sum()):,}")
print(f"   Combinaciones de proveedores:")
for k, v in instr['Proveedores_tupla'].value_counts().items():
    print(f"      {k:<14}: {v:>5}")
n_multi = int(instr['ISIN_multi_PK2'].sum())
if n_multi:
    print(f"   [OJO] {n_multi} PK2 comparten ISIN con otro PK2 (distinta moneda) — revisar")

# ── Merge de vuelta a las filas fondo × PK2 ─────────────────────────────────
COLS_METR = ['PK2', 'JPM_Yield', 'JPM_Duration', 'JPM_Fuente', 'Origen_JPM',
             'RA_Yield', 'RA_Duration', 'RA_Moneda', 'RA_Periodicidad', 'Aplica_RA',
             'BBG_Yield', 'BBG_Duration', 'BBG_XCCY_Yield',
             'Origen_BBG_Yield', 'Origen_BBG_Duration', 'Origen_BBG_XCCY',
             'JPM_ok', 'RA_ok', 'BBG_ok', 'Pendiente_BBG',
             'Proveedores_tupla', 'N_Proveedores_tupla',
             'N_Proveedores_Yield', 'N_Proveedores_Dur',
             'Yield_min', 'Yield_max', 'Yield_dif',
             'Dur_min', 'Dur_max', 'Dur_dif', 'ISIN_multi_PK2',
             'Yield_final', 'Fuente_Yield', 'Duration_final', 'Fuente_Duration',
             'Tiene_Yield', 'Tiene_Duration', 'RESUELTO', 'Destino']
COLS_METR = [c for c in COLS_METR if c in instr.columns]

df_con_m = df_con.merge(instr[COLS_METR], on='PK2', how='left')
df_sin_m = df_sin.merge(instr[COLS_METR], on='PK2', how='left')
for _d in (df_con_m, df_sin_m):
    for c in ('RESUELTO', 'Tiene_Yield', 'Tiene_Duration'):
        _d[c] = _d[c].fillna(False).astype(bool)

print(f"   con_ISIN resueltos: {int(df_con_m['RESUELTO'].sum()):,}/{len(df_con_m):,}")
print(f"   sin_ISIN resueltos: {int(df_sin_m['RESUELTO'].sum()):,}/{len(df_sin_m):,}")


# =============================================================================
# [6] RESUMEN
# =============================================================================
print(f"\n[6] Generando RESUMEN...")

df_all = pd.concat([df_con_m, df_sin_m], ignore_index=True)
df_all['es_bono'] = pd.to_numeric(df_all['Issue_Type_Code'], errors='coerce') == ISSUE_TYPE_BONO

def pct(a, b):
    return f"{(a / b * 100):0.1f}%" if b else "—"

rows = []
def add(m, v='', d=''):
    rows.append({'Métrica': m, 'Valor': v, 'Detalle': d})

N = len(df_all)
add('── UNIVERSO ──')
add('Filas fondo × PK2', N)
add('Instrumentos únicos (PK2)', len(instr), f"dedup evita {N - len(instr):,} consultas")
add('PK2 con ISIN', len(df_con_m))
add('PK2 sin ISIN', len(df_sin_m))
add('')
add('── RESULTADO GLOBAL (tupla Yield+Duration) ──')
add('RESUELTOS vía métricas', int(df_all['RESUELTO'].sum()), pct(df_all['RESUELTO'].sum(), N))
add('→ a PROP',              int((~df_all['RESUELTO']).sum()), pct((~df_all['RESUELTO']).sum(), N))
add('Solo Yield (insuficiente → PROP)',    int((df_all['Tiene_Yield'] & ~df_all['Tiene_Duration']).sum()))
add('Solo Duration (insuficiente → PROP)', int((~df_all['Tiene_Yield'] & df_all['Tiene_Duration']).sum()))
add('')
add('── APORTE POR PROVEEDOR (tupla completa) ──')
for p, c in (('JPM', 'JPM_ok'), ('RA', 'RA_ok'), ('BBG', 'BBG_ok')):
    if c in df_all.columns:
        n = int(df_all[c].fillna(False).sum())
        add(f'{p} — resuelve tupla', n, pct(n, N))
add('')
add('── QUÉ TENEMOS: COMBINACIONES DE PROVEEDORES ──')
add('(instrumentos únicos PK2, no filas fondo×PK2)')
for k, v in instr['Proveedores_tupla'].value_counts().items():
    add(f'  {k}', int(v), pct(v, len(instr)))
add('')
add('── REDUNDANCIA (cuántos proveedores por papel) ──')
for k, v in instr['N_Proveedores_tupla'].value_counts().sort_index().items():
    etq = {0: 'ningún proveedor', 1: 'un solo proveedor'}.get(k, f'{k} proveedores')
    add(f'  {etq}', int(v), pct(v, len(instr)))
add('')
add('── DISPONIBILIDAD SUELTA POR MÉTRICA ──')
for p, cy, cd in (('JPM', 'JPM_Yield', 'JPM_Duration'),
                  ('RA', 'RA_Yield', 'RA_Duration'),
                  ('BBG', 'BBG_Yield', 'BBG_Duration')):
    add(f'{p} — con Yield',    int(instr[cy].notna().sum()), pct(instr[cy].notna().sum(), len(instr)))
    add(f'{p} — con Duration', int(instr[cd].notna().sum()), pct(instr[cd].notna().sum(), len(instr)))
add('')
add('── CONSISTENCIA ENTRE PROVEEDORES ──')
_cmp = instr[instr['N_Proveedores_Yield'] > 1]
add('Papeles con Yield en >1 proveedor', len(_cmp))
if len(_cmp):
    add('  dif. mediana de Yield', f"{_cmp['Yield_dif'].median():0.4f}")
    add('  dif. máxima de Yield',  f"{_cmp['Yield_dif'].max():0.4f}")
_cmpd = instr[instr['N_Proveedores_Dur'] > 1]
add('Papeles con Duration en >1 proveedor', len(_cmpd))
if len(_cmpd):
    add('  dif. mediana de Duration', f"{_cmpd['Dur_dif'].median():0.4f}")
    add('  dif. máxima de Duration',  f"{_cmpd['Dur_dif'].max():0.4f}")
add('')
add('── FUENTE FINAL ELEGIDA (cascada) ──')
for p, n in df_all.loc[df_all['Tiene_Yield'], 'Fuente_Yield'].value_counts().items():
    if str(p).strip():
        add(f'Yield desde {p}', int(n), pct(n, df_all['Tiene_Yield'].sum()))
for p, n in df_all.loc[df_all['Tiene_Duration'], 'Fuente_Duration'].value_counts().items():
    if str(p).strip():
        add(f'Duration desde {p}', int(n), pct(n, df_all['Tiene_Duration'].sum()))
add('')
add('── CONSULTA A TERMINAL BBG ──')
add('Modo', 'MAPEO COMPLETO' if MODO_MAPEO_COMPLETO else 'AHORRO')
add('ISIN consultados en BBG', len(pend_isins), f"de {len(instr):,} instrumentos únicos")
add('Resueltos sin necesidad de BBG', int((instr['JPM_ok'] | instr['RA_ok']).sum()))
add('ISIN que bastarían en modo ahorro', len(isins_pend),
    f"ahorro potencial: {len(pend_isins) - len(isins_pend):,}")
add('Solo BBG los resuelve (insustituible)',
    int((instr['BBG_ok'] & ~instr['JPM_ok'] & ~instr['RA_ok']).sum()))
add('')
add('── PK2 = ID_Instrumento-ID_Curr ──')
add('PK2 que comparten ISIN con otro PK2', int(instr['ISIN_multi_PK2'].sum()),
    'mismo papel en otra moneda — revisar métrica')
add('')
add('── HEDGEADOS ──')
n_hedge = len(hedge_lkp)
n_xccy  = int(instr['BBG_XCCY_Yield'].notna().sum())
add('Instrumentos con Hedge_Currency', n_hedge)
add('  → con YAS_XCCY', n_xccy, pct(n_xccy, n_hedge))
add('  → sin XCCY (calculadora de drops)', n_hedge - n_xccy)
add('')
add('── VÍA FAMILIA (hermano REGS/144A) ──')
add('JPM vía hermano',          int(limpiar_txt(instr['Origen_JPM']).str.startswith('HERMANO').sum()))
add('BBG Yield vía hermano',    int(limpiar_txt(instr['Origen_BBG_Yield']).str.startswith('HERMANO').sum()))
add('BBG Duration vía hermano', int(limpiar_txt(instr['Origen_BBG_Duration']).str.startswith('HERMANO').sum()))
add('')
add('── POR FONDO: BUCKET TOTAL ──')
for fid, sub in df_all.groupby('ID_Fund'):
    nm = sub['FundShortName'].dropna().iloc[0] if sub['FundShortName'].notna().any() else '?'
    add(f'Fondo {fid} ({nm})', f"{int(sub['RESUELTO'].sum())}/{len(sub)}",
        f"{pct(sub['RESUELTO'].sum(), len(sub))} resuelto | PROP={int((~sub['RESUELTO']).sum())}")
add('')
add(f'── POR FONDO: BUCKET BONOS (Issue_Type_Code={ISSUE_TYPE_BONO}) ──')
for fid, sub_all in df_all.groupby('ID_Fund'):
    sub = sub_all[sub_all['es_bono']]
    nm = sub_all['FundShortName'].dropna().iloc[0] if sub_all['FundShortName'].notna().any() else '?'
    add(f'Fondo {fid} ({nm})', f"{int(sub['RESUELTO'].sum())}/{len(sub)}",
        f"{pct(sub['RESUELTO'].sum(), len(sub))} resuelto | PROP={int((~sub['RESUELTO']).sum())}")
add('')
add('── COBERTURA PONDERADA POR TotalMVal ──')
if 'TotalMVal' in df_all.columns:
    for fid, sub in df_all.groupby('ID_Fund'):
        mv_t = pd.to_numeric(sub['TotalMVal'], errors='coerce').abs().sum()
        mv_o = pd.to_numeric(sub.loc[sub['RESUELTO'], 'TotalMVal'], errors='coerce').abs().sum()
        nm = sub['FundShortName'].dropna().iloc[0] if sub['FundShortName'].notna().any() else '?'
        add(f'Fondo {fid} ({nm}) — MVal resuelto', pct(mv_o, mv_t))

df_resumen = pd.DataFrame(rows)


# =============================================================================
# [7] GUARDAR
# =============================================================================
print(f"\n[7] Guardando: {FNAME_OUT}")

with pd.ExcelWriter(FNAME_OUT, engine='openpyxl') as w:
    df_con_m.to_excel(w, sheet_name='con_ISIN', index=False)
    fmt_ws(w.sheets['con_ISIN'], '0B2447')
    df_sin_m.to_excel(w, sheet_name='sin_ISIN', index=False)
    fmt_ws(w.sheets['sin_ISIN'], 'B8540B')
    df_resumen.to_excel(w, sheet_name='RESUMEN', index=False)
    fmt_ws(w.sheets['RESUMEN'], '2E4057', freeze='A2')

print("\n" + "=" * 70)
print("01_MAPEO_METRICAS completado.")
print(f"  RESUELTOS: {int(df_all['RESUELTO'].sum()):,}/{N:,} ({df_all['RESUELTO'].sum()/N*100:0.1f}%)")
print(f"  A PROP   : {int((~df_all['RESUELTO']).sum()):,}")
print(f"  Output   : {FNAME_OUT}")
print("=" * 70)