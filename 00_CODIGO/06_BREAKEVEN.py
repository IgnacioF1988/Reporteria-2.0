# =============================================================================
# 06_BREAKEVEN.py — Yields indexadas → moneda local
# =============================================================================
# PROBLEMA
#   Los proveedores entregan la yield en la moneda de DENOMINACIÓN del papel.
#   Si esa moneda es indexada (UF, UDI, UVR, IPCA, UI, BONCER), la yield es
#   REAL y no es comparable con una yield nominal. Hay que llevarla a moneda
#   local aplicando la inflación implícita del mercado (breakeven).
#
# MÉTODO — breakeven al plazo de la DURATION
#       be@dur      = (1 + r_nom@dur) / (1 + r_real@dur) − 1
#       yield_local = (1 + yield_real) × (1 + be@dur) − 1
#   donde r_nom se interpola de la curva soberana local (Carga_CurvasSoberanas)
#   y r_real de la curva del índice (Carga_Indexes), ambas AL PLAZO DE LA
#   DURATION del papel.
#
#   Por qué al plazo de la duration y no flujo por flujo (como 3b_INDICES del
#   proyecto): la mayoría de los papeles ahora viene de proveedores con solo
#   Yield y Duration, sin tabla de desarrollo. Se midió el costo de esta
#   aproximación: 0 bps con curva plana, 1-5 bps en plazos 3-7 años y hasta
#   ~10 bps en un bono a 15 años con curva muy empinada. Es menor que la
#   dispersión que ya existe entre proveedores, y usar un método único hace
#   comparables todas las fuentes.
#
# CATEGORÍAS Y ORIGEN DE LA MÉTRICA (dos cosas distintas)
#   REAL (UF, UDI, UVR, IPCA, UI, BONCER): los nominales vienen indexados, así
#        que la yield es una tasa REAL → se le aplica el breakeven para pasarla
#        a moneda local. Aplica venga de donde venga la métrica.
#   RATE (CDI, TIIE): son flotantes. Acá SÍ importa el origen:
#        · TD modelada por nosotros (JSONL/EXCEPCIONES/CSHF) → la TD lleva solo
#          el SPREAD, falta la parte variable: se le SUMA el nivel del índice.
#        · Proveedor externo (JPM/RA/BBG) → SUPUESTO: ya viene en moneda local
#          nominal, no se toca (ver FUENTES_PROVEEDOR más abajo).
#   Sin 'Index Name' / NOMINAL → sin cambio.
#
# DURATION
#   La Macaulay no cambia al pasar de real a nominal (mismos flujos, mismas
#   fechas). Sí cambia la Modified: ModDur = MacDur/(1+y). Se reexpresa.
#
# AUDITORÍA (el punto central de este script)
#   Todo lo que no se pueda convertir queda listado con su motivo, en vez de
#   quedar en silencio: índices sin mapear, sin curva real, sin curva nominal,
#   archivos de atributos ausentes, papeles sin Index_Type determinable.
#
# INPUTS
#   CONSOLIDADO_{FECHA}.xlsx          ← papeles con Requiere_Breakeven
#   Carga_Indexes_{FECHA}.csv         ← curvas reales por índice
#   Carga_CurvasSoberanas_{FECHA}.csv ← curvas nominales locales
#   Atributos_*.xlsx  (MANUALES)      ← Index / Index Name por PK2  [prioridad]
#   ATRIBUTOS_{FECHA}.xlsx            ← Index_Type derivado de BBG  [respaldo]
#
# OUTPUT: BREAKEVEN_{FECHA}.xlsx
#   convertidos | sin_convertir | indices_sin_mapear | curvas_faltantes | RESUMEN
# =============================================================================

import os
import re
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
    FECHA, OUT_CONSOLIDADO, OUT_BREAKEVEN, INDEXES_PATH, CURVAS_SOB_PATH,
    ATRIBUTOS_BBG_PATH, atributos_fondo_paths,
    INDEX_TO_CURVE_REAL, INDEX_TO_CURVE_NOM, INDEX_TO_LOCAL_CCY,
    INDEX_CATEGORY, INDICES_SIN_CONVERSION, INDEX_NAME_ALIAS, CCY_A_INDEX,
    NAVY, RED, AMBER_F, FONT_NAME,
)

# Fuentes cuya yield viene YA calculada por un proveedor externo
FUENTES_PROVEEDOR = {'JPM', 'RA', 'BBG'}

settle_dt = pd.to_datetime(FECHA)
print("=" * 70)
print(f"06_BREAKEVEN | FECHA={FECHA}")
print("=" * 70)

auditoria = []          # todo lo que no se pudo resolver
def audit(tipo, detalle, **kw):
    auditoria.append({'Tipo': tipo, 'Detalle': detalle, **kw})


def limpiar_txt(s):
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

def tenor_to_days(tenor):
    m = re.match(r'^(\d+)(D|W|M|Y)$', str(tenor).strip().upper())
    if not m: return np.nan
    n, u = int(m.group(1)), m.group(2)
    return {'D': 1, 'W': 7, 'M': 30.4375, 'Y': 365.25}[u] * n

def interp_rate(target_days, td, yd):
    """Interpolación lineal con extrapolación FLAT en los extremos."""
    v = ~np.isnan(yd)
    td, yd = td[v], yd[v]
    if len(td) == 0: return np.nan
    if target_days <= td[0]:  return float(yd[0])
    if target_days >= td[-1]: return float(yd[-1])
    return float(np.interp(target_days, td, yd))


# =============================================================================
# [1] CONSOLIDADO
# =============================================================================
print(f"\n[1] Leyendo {os.path.basename(OUT_CONSOLIDADO)}...")
if not os.path.exists(OUT_CONSOLIDADO):
    print("   ERROR: falta el consolidado. Corré 05_CONSOLIDA primero."); sys.exit(1)

cons = pd.read_excel(OUT_CONSOLIDADO, sheet_name='Detalle', header=2, engine='openpyxl')
cons.columns = [str(c).strip() for c in cons.columns]
for c in ('PK2', 'Risk_Currency', 'Name_Instrumento', 'FundShortName'):
    if c in cons.columns: cons[c] = limpiar_txt(cons[c])
print(f"   Posiciones: {len(cons):,}")

objetivo = cons[cons.get('Requiere_Breakeven', False).fillna(False).astype(bool)].copy()
print(f"   Requieren breakeven: {len(objetivo):,} | PK2 únicos: {objetivo['PK2'].nunique():,}")
if objetivo.empty:
    print("   Nada que convertir."); sys.exit(0)

# =============================================================================
# [2] INDEX_TYPE — Atributos_{FONDO} (prioridad) → ATRIBUTOS BBG → moneda
# =============================================================================
print(f"\n[2] Determinando el índice de cada papel...")

idx_fondo, origen_fondo, nombres_crudos = {}, {}, {}
paths_fondo = atributos_fondo_paths()
if not paths_fondo:
    audit('ATRIBUTOS_FONDO_AUSENTE', 'No se encontró ningún Atributos_*.xlsx en MANUALES',
          Impacto='se usa solo BBG y la moneda como respaldo')
    print("   [AUDIT] sin archivos Atributos_*.xlsx")
else:
    for ruta in paths_fondo:
        try:
            a = pd.read_excel(ruta, engine='openpyxl')
            a.columns = [str(c).strip() for c in a.columns]
            cn = next((c for c in a.columns if c.lower().replace('_', ' ') == 'index name'), None)
            if cn is None:
                audit('ATRIBUTOS_FONDO_SIN_COLUMNA', os.path.basename(ruta),
                      Impacto="falta la columna 'Index Name'"); continue
            a['PK2'] = limpiar_txt(a['PK2'])
            n = 0
            for _, r in a.iterrows():
                nm = str(r.get(cn, '')).strip().upper()
                if not r['PK2'] or not nm or nm in ('NAN', 'NO', ''): continue
                nombres_crudos.setdefault(nm, set()).add(os.path.basename(ruta))
                it = INDEX_NAME_ALIAS.get(nm)
                if it:
                    idx_fondo[r['PK2']] = it
                    origen_fondo[r['PK2']] = f'ATRIB_FONDO:{os.path.basename(ruta)}'
                    n += 1
            print(f"   {os.path.basename(ruta):28} → {n} PK2 con índice")
        except Exception as e:
            audit('ATRIBUTOS_FONDO_ERROR', os.path.basename(ruta), Impacto=str(e))

    # Nombres de índice que aparecen en los archivos pero NO están mapeados
    for nm, archivos in sorted(nombres_crudos.items()):
        if nm not in INDEX_NAME_ALIAS:
            audit('INDICE_SIN_MAPEAR', nm,
                  Impacto='no se puede convertir: agregar a INDEX_NAME_ALIAS en pipeline_config',
                  Archivos=', '.join(sorted(archivos)))

# Respaldo: atributos derivados de BBG
idx_bbg = {}
if os.path.exists(ATRIBUTOS_BBG_PATH):
    try:
        ab = pd.read_excel(ATRIBUTOS_BBG_PATH, sheet_name='atributos', engine='openpyxl')
        ab.columns = [str(c).strip() for c in ab.columns]
        ab['PK2'] = limpiar_txt(ab['PK2'])
        for _, r in ab.iterrows():
            it = str(r.get('Index_Type', '')).strip().upper()
            if r['PK2'] and it and it not in ('NOMINAL', 'NAN', ''):
                idx_bbg[r['PK2']] = INDEX_NAME_ALIAS.get(it, it)
        print(f"   ATRIBUTOS BBG → {len(idx_bbg)} PK2 con índice")
    except Exception as e:
        audit('ATRIBUTOS_BBG_ERROR', os.path.basename(ATRIBUTOS_BBG_PATH), Impacto=str(e))
else:
    audit('ATRIBUTOS_BBG_AUSENTE', os.path.basename(ATRIBUTOS_BBG_PATH),
          Impacto='sin respaldo de BBG para el índice')
    print(f"   [AUDIT] falta {os.path.basename(ATRIBUTOS_BBG_PATH)}")

def resolver_indice(pk2, ccy):
    if pk2 in idx_fondo: return idx_fondo[pk2], origen_fondo[pk2]
    if pk2 in idx_bbg:   return idx_bbg[pk2], 'ATRIB_BBG'
    c = str(ccy).strip().upper()
    if c in CCY_A_INDEX:  return CCY_A_INDEX[c], 'MONEDA'
    return None, 'SIN_DETERMINAR'

objetivo[['Index_Type', 'Origen_Index']] = objetivo.apply(
    lambda r: pd.Series(resolver_indice(r['PK2'], r['Risk_Currency'])), axis=1)
print(f"   Origen del índice: {dict(objetivo['Origen_Index'].value_counts())}")

# =============================================================================
# [3] CURVAS
# =============================================================================
print(f"\n[3] Cargando curvas...")

def cargar_curva(path, etiqueta):
    if not os.path.exists(path):
        audit('CURVA_ARCHIVO_AUSENTE', os.path.basename(path),
              Impacto=f'sin curvas {etiqueta}: no se puede convertir nada que las use')
        print(f"   [AUDIT] falta {os.path.basename(path)}")
        return pd.DataFrame()
    d = pd.read_csv(path, sep='|', encoding='latin1')
    d.columns = [str(c).strip() for c in d.columns]
    d['Fecha'] = pd.to_datetime(d['Fecha'], errors='coerce')
    d['tenor_days'] = d['tenor'].apply(tenor_to_days)
    print(f"   {etiqueta:10} {len(d):>6} filas | curvas: {d['Curve'].nunique()}")
    return d

df_real = cargar_curva(INDEXES_PATH, 'REAL')
df_nom  = cargar_curva(CURVAS_SOB_PATH, 'NOMINAL')

def subset(df, curva):
    """(tenor_days, mid_yield) de la fecha más reciente <= settle."""
    if df.empty or not curva: return None, None
    s = df[(df['Curve'] == curva) & df['mid_yield'].notna() & (df['Fecha'] <= settle_dt)]
    if s.empty: return None, None
    s = s[s['Fecha'] == s['Fecha'].max()].sort_values('tenor_days')
    return s['tenor_days'].values, s['mid_yield'].values

cache = {}
def curva(df, nombre):
    if nombre not in cache: cache[nombre] = subset(df, nombre)
    return cache[nombre]

# =============================================================================
# [4] CONVERTIR
# =============================================================================
print(f"\n[4] Aplicando breakeven...")
filas, sin_conv = [], []

for _, r in objetivo.iterrows():
    pk2, it = r['PK2'], r['Index_Type']
    y_real = pd.to_numeric(r.get('Yield'), errors='coerce')
    dur    = pd.to_numeric(r.get('Duration'), errors='coerce')
    com = {'Fondo': r.get('FundShortName', ''), 'PK2': pk2,
           'Instrumento': r.get('Name_Instrumento', ''), 'Moneda': r['Risk_Currency'],
           'Index_Type': it, 'Origen_Index': r['Origen_Index'],
           'Yield_original': y_real, 'Duration_original': dur, 'Fuente': r.get('Fuente', '')}

    if not it:
        sin_conv.append({**com, 'Motivo': 'No se pudo determinar el índice del papel'}); continue
    if it in INDICES_SIN_CONVERSION:
        sin_conv.append({**com, 'Motivo': f'{it}: no requiere conversión'}); continue

    cat = INDEX_CATEGORY.get(it)
    if cat not in ('REAL', 'RATE'):
        sin_conv.append({**com, 'Motivo': f'Índice {it} sin categoría definida en INDEX_CATEGORY'})
        audit('INDICE_SIN_CATEGORIA', it, Impacto='agregar a INDEX_CATEGORY'); continue

    # ── SUPUESTO SOBRE FLOTANTES (a validar) ─────────────────────────────────
    # Para índices RATE (CDI, TIIE) la conversión SOLO aplica cuando la métrica
    # salió de una TD modelada por nosotros (JSONL / EXCEPCIONES / CSHF): esas
    # TDs llevan únicamente el spread, así que hay que sumarle el nivel del
    # índice para llegar a la tasa en moneda local.
    # SUPUESTO NO VERIFICADO: se asume que la yield que entregan los
    # PROVEEDORES externos (JPM / RA / BBG) para un flotante YA viene en moneda
    # local nominal (proyectada con la curva forward), y por lo tanto NO se le
    # suma el índice — hacerlo lo contaría dos veces.
    # Si se comprueba lo contrario, basta con sacar 'BBG'/'JPM'/'RA' de
    # FUENTES_PROVEEDOR para que también se conviertan.
    if cat == 'RATE' and r.get('Fuente', '') in FUENTES_PROVEEDOR:
        sin_conv.append({**com,
            'Motivo': f'{it} (RATE) desde proveedor {r.get("Fuente","")}: '
                      f'se ASUME que ya viene en moneda local nominal'})
        continue
    if pd.isna(y_real) or pd.isna(dur) or dur <= 0:
        sin_conv.append({**com, 'Motivo': 'Sin Yield o Duration válida'}); continue

    c_real, c_nom = INDEX_TO_CURVE_REAL.get(it), INDEX_TO_CURVE_NOM.get(it)
    if not c_real:
        sin_conv.append({**com, 'Motivo': f'{it} sin curva mapeada'})
        audit('SIN_CURVA_REAL', it, Impacto='agregar a INDEX_TO_CURVE_REAL'); continue
    if cat == 'REAL' and not c_nom:
        sin_conv.append({**com, 'Motivo': f'{it} sin curva NOMINAL mapeada'})
        audit('SIN_CURVA_NOM', it, Impacto='agregar a INDEX_TO_CURVE_NOM'); continue

    tr, yr = curva(df_real, c_real)
    if tr is None:
        sin_conv.append({**com, 'Motivo': f'Curva {c_real} sin datos al {FECHA}'})
        audit('CURVA_SIN_DATOS', c_real, Impacto='no está en Carga_Indexes para esta fecha'); continue

    dias   = dur * 365.25
    r_real = interp_rate(dias, tr, yr)
    if pd.isna(r_real):
        sin_conv.append({**com, 'Motivo': 'Interpolación sin resultado'}); continue

    if cat == 'REAL':
        # Nominales indexados (UF, UDI, UVR, IPCA, UI, BONCER): la yield es
        # REAL. Se le aplica la inflación implícita del mercado (breakeven).
        tn, yn = curva(df_nom, c_nom)
        if tn is None:
            sin_conv.append({**com, 'Motivo': f'Curva nominal {c_nom} sin datos al {FECHA}'})
            audit('CURVA_SIN_DATOS', c_nom,
                  Impacto='no está en Carga_CurvasSoberanas para esta fecha'); continue
        r_nom = interp_rate(dias, tn, yn)
        if pd.isna(r_nom):
            sin_conv.append({**com, 'Motivo': 'Interpolación nominal sin resultado'}); continue
        ajuste = (1 + r_nom / 100) / (1 + r_real / 100) - 1     # breakeven
        metodo = 'BREAKEVEN'
    else:
        # Flotantes (CDI, TIIE) modelados por nosotros: la TD lleva solo el
        # SPREAD, así que se le suma el nivel del índice al plazo de la duration.
        # APROXIMACIÓN: usa el nivel spot interpolado, no la curva forward.
        # Lo riguroso sería proyectar cada cupón con la forward y recalcular la
        # XIRR (como 3b_INDICES del proyecto). Queda marcado en 'Metodo'.
        r_nom  = np.nan
        ajuste = r_real / 100
        metodo = 'SUMA_INDICE_APROX'

    y_local = (1 + y_real/100) * (1 + ajuste) - 1
    # MacDur no cambia; ModDur se reexpresa a la nueva yield
    mac = dur * (1 + y_local)
    mod_local = mac / (1 + y_local)

    filas.append({**com, 'Categoria': cat, 'Metodo': metodo,
                  'Curva_real': c_real, 'Curva_nom': c_nom if cat == 'REAL' else '',
                  'plazo_dias': round(dias), 'r_real_%': r_real, 'r_nom_%': r_nom,
                  'ajuste': ajuste, 'Yield_local': y_local,
                  'Duration_local': mod_local,
                  'Moneda_local': INDEX_TO_LOCAL_CCY.get(it, ''),
                  'Extrapolado': bool(dias < tr[0] or dias > tr[-1])})

conv = pd.DataFrame(filas)
sinc = pd.DataFrame(sin_conv)
print(f"   Convertidos   : {len(conv):,}")
print(f"   Sin convertir : {len(sinc):,}")
if len(conv):
    print(f"   Ajuste medio: {conv['ajuste'].mean()*100:.2f}% | "
          f"extrapolados: {int(conv['Extrapolado'].sum())}")
if len(sinc):
    for k, v in sinc['Motivo'].value_counts().items():
        print(f"      {k}: {v}")

# =============================================================================
# [5] AUDITORÍA + GUARDAR
# =============================================================================
print(f"\n[5] Guardando {os.path.basename(OUT_BREAKEVEN)}...")
aud = pd.DataFrame(auditoria) if auditoria else pd.DataFrame(
    columns=['Tipo', 'Detalle', 'Impacto'])

rr = []
def add(m, v='', c=''): rr.append({'Concepto': m, 'Valor': v, 'Comentario': c})
add('── MÉTODO ──')
add('Fórmula', 'be@dur = (1+r_nom@dur)/(1+r_real@dur) − 1',
    'yield_local = (1+yield_real) × (1+be@dur) − 1')
add('Interpolación', 'al plazo de la DURATION', 'lineal, extrapolación flat en extremos')
add('')
add('── UNIVERSO ──')
add('Posiciones que requieren breakeven', len(objetivo))
add('  convertidas', len(conv), f"{len(conv)/len(objetivo):.1%}" if len(objetivo) else '')
add('  sin convertir', len(sinc))
if len(sinc):
    for k, v in sinc['Motivo'].value_counts().items():
        add(f'    {k}', int(v))
add('')
add('── ORIGEN DEL ÍNDICE ──')
for k, v in objetivo['Origen_Index'].value_counts().items():
    add(f'  {k}', int(v))
add('')
if len(conv):
    add('── AJUSTE POR ÍNDICE ──')
    for it, g in conv.groupby('Index_Type'):
        add(f'  {it}', f"{g['ajuste'].mean()*100:.2f}%",
            f"{len(g)} papeles → {INDEX_TO_LOCAL_CCY.get(it,'')} | "
            f"yield {g['Yield_original'].mean()*100:.2f}% → {g['Yield_local'].mean()*100:.2f}%")
    n_ext = int(conv['Extrapolado'].sum())
    if n_ext:
        add('Extrapolados fuera de curva', n_ext, 'duration fuera del rango de tenores')
add('')
add('── AUDITORÍA ──')
if auditoria:
    for k, v in aud['Tipo'].value_counts().items():
        add(f'  {k}', int(v))
    add('', '', 'ver hoja auditoria para el detalle')
else:
    add('Sin incidencias', 'OK', 'todos los índices y curvas estaban disponibles')

with pd.ExcelWriter(OUT_BREAKEVEN, engine='openpyxl') as w:
    pd.DataFrame(rr).to_excel(w, sheet_name='RESUMEN', index=False)
    fmt_ws(w.sheets['RESUMEN'], '2E4057', freeze='A2')
    (conv if len(conv) else pd.DataFrame({'info': ['sin conversiones']})).to_excel(
        w, sheet_name='convertidos', index=False)
    fmt_ws(w.sheets['convertidos'], NAVY)
    if len(sinc):
        sinc.to_excel(w, sheet_name='sin_convertir', index=False)
        fmt_ws(w.sheets['sin_convertir'], AMBER_F)
    if len(aud):
        aud.to_excel(w, sheet_name='auditoria', index=False)
        fmt_ws(w.sheets['auditoria'], RED)

print(f"\n{'='*70}")
print(f"  Convertidos: {len(conv):,} | Sin convertir: {len(sinc):,}")
if auditoria:
    print(f"  [AUDITORÍA] {len(auditoria)} incidencias — ver hoja 'auditoria':")
    for a in auditoria[:8]:
        print(f"     · {a['Tipo']:28} {str(a['Detalle'])[:40]}")
    if len(auditoria) > 8:
        print(f"     … y {len(auditoria)-8} más")
print(f"  Output: {OUT_BREAKEVEN}")
print(f"{'='*70}")