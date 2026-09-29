# =============================================================================
# 02_REPORTE_COBERTURA.py — Reporte ejecutivo de cobertura de métricas
# =============================================================================
# CRITERIO ÚNICO EN TODO EL REPORTE:
#   · Se EXCLUYEN las facturas (FAC...) del universo: no son bonos y ningún
#     proveedor de métricas las cubre. Se reportan aparte.
#   · Un papel está RESUELTO si tiene Yield Y Duration de algún proveedor.
#   · Dos unidades de medida, siempre etiquetadas:
#       - POSICIÓN  = fila fondo × PK2 (un papel en 3 fondos = 3 posiciones)
#       - PAPEL ÚNICO = PK2 distinto (ese mismo papel = 1)
# =============================================================================

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

FECHA = "20260731"
F_IN  = f"MAPEO_METRICAS_{FECHA}.xlsx"
F_OUT = f"REPORTE_COBERTURA_{FECHA}.xlsx"

NAVY, GREY_L, WHITE = '1F3864', 'F2F2F2', 'FFFFFF'
GREEN, GREEN_F, RED, RED_F, AMBER_F = '006100', 'C6EFCE', '9C0006', 'FFC7CE', 'FFEB9C'
FONT = 'Arial'
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# ── Carga ────────────────────────────────────────────────────────────────────
con = pd.read_excel(F_IN, sheet_name='con_ISIN')
sin = pd.read_excel(F_IN, sheet_name='sin_ISIN')
raw = pd.concat([con, sin], ignore_index=True)

for c in ('RESUELTO', 'JPM_ok', 'RA_ok', 'BBG_ok'):
    raw[c] = raw[c].fillna(False).astype(bool)
for c in ('Fuente_Yield', 'Fuente_Duration', 'Origen_JPM',
          'Origen_BBG_Yield', 'Origen_BBG_Duration'):
    raw[c] = raw[c].fillna('').astype(str)
raw['nm'] = raw['Name_Instrumento'].astype(str).str.upper().str.strip()

# Separar facturas del universo relevante
es_fac  = raw['nm'].str.startswith('FAC')
fac     = raw[es_fac].copy()
df      = raw[~es_fac].copy()          # universo relevante
uni     = df.drop_duplicates('PK2').copy()

df['via_hermano'] = (df['Origen_JPM'].str.startswith('HERMANO') |
                     df['Origen_BBG_Yield'].str.startswith('HERMANO') |
                     df['Origen_BBG_Duration'].str.startswith('HERMANO'))
nf = df.groupby('PK2')['ID_Fund'].nunique()
df['compartido'] = df['PK2'].map(nf) > 1

# ── Cifras maestras ──────────────────────────────────────────────────────────
POS      = len(df)
POS_RES  = int(df['RESUELTO'].sum())
UNI      = len(uni)
UNI_RES  = int(uni['RESUELTO'].sum())
UNI_JR   = int((uni['JPM_ok'] | uni['RA_ok']).sum())
UNI_BBG  = int((uni['BBG_ok'] & ~uni['JPM_ok'] & ~uni['RA_ok']).sum())
UNI_PROP = UNI - UNI_RES
N_FAC    = int(fac['PK2'].nunique())

# =============================================================================
# HOJA 1 — PANORAMA
# =============================================================================
pan = [
    ('UNIVERSO', '', ''),
    ('Posiciones totales en cartera (fondo × PK2)', len(raw), 'incluye facturas'),
    ('   menos: facturas (FAC...)', -N_FAC, 'todas de MRCLP · no son bonos · ningún proveedor las cubre'),
    ('Posiciones del universo relevante', POS, 'base de la hoja "Resumen por fondo"'),
    ('Papeles ÚNICOS del universo relevante (PK2)', UNI,
     f'{POS - UNI:,} posiciones son el mismo papel repetido en otro fondo'),
    ('', '', ''),
    ('RESULTADO — MEDIDO EN POSICIONES', '', ''),
    ('Posiciones resueltas con métricas', POS_RES, f'{POS_RES/POS:.1%} de {POS:,} posiciones'),
    ('Posiciones a PROP', POS - POS_RES, f'{(POS-POS_RES)/POS:.1%}'),
    ('', '', ''),
    ('RESULTADO — MEDIDO EN PAPELES ÚNICOS', '', ''),
    ('Papeles únicos resueltos con métricas', UNI_RES, f'{UNI_RES/UNI:.1%} de {UNI:,} papeles únicos'),
    ('   → resueltos con JPM y/o RA (sin tocar Bloomberg)', UNI_JR,
     f'{UNI_JR/UNI_RES:.1%} de los resueltos · reusable a futuro sin consumir terminal'),
    ('   → resueltos únicamente por Bloomberg', UNI_BBG,
     f'{UNI_BBG/UNI_RES:.1%} de los resueltos · universo mínimo que sí requiere terminal'),
    ('Papeles únicos a PROP (a modelar internamente)', UNI_PROP, f'{UNI_PROP/UNI:.1%} del universo relevante'),
    ('', '', ''),
    ('RECONCILIACIÓN ENTRE AMBAS MEDIDAS', '', ''),
    ('Posiciones resueltas', POS_RES, 'lo que se ve en "Resumen por fondo" (suma de los 9 fondos)'),
    ('corresponden a papeles únicos resueltos', UNI_RES,
     f'la diferencia ({POS_RES - UNI_RES:,}) son papeles compartidos, contados una vez por cada fondo que los tiene'),
]
panorama = pd.DataFrame(pan, columns=['Concepto', 'Valor', 'Comentario'])

# Compartidos / hermanos por fondo
comp = (df.groupby('FundShortName')
        .apply(lambda s: pd.Series({
            'Posiciones': len(s),
            'Compartidas con otro fondo': int(s['compartido'].sum()),
            'Resueltas entre las compartidas': int(s.loc[s['compartido'], 'RESUELTO'].sum()),
            'Rescatadas vía hermano de serie': int(s['via_hermano'].sum()),
        }), include_groups=False).reset_index().rename(columns={'FundShortName': 'Fondo'}))
comp['% Compartido'] = comp['Compartidas con otro fondo'] / comp['Posiciones']
comp = comp.sort_values('% Compartido', ascending=False).reset_index(drop=True)

# =============================================================================
# HOJA 2 — RESUMEN POR FONDO  (misma unidad: POSICIONES, sin facturas)
# =============================================================================
res = []
for fid, s in df.groupby('ID_Fund'):
    nm = s['FundShortName'].dropna().iloc[0] if s['FundShortName'].notna().any() else str(fid)
    mv_t = pd.to_numeric(s['TotalMVal'], errors='coerce').abs().sum()
    mv_o = pd.to_numeric(s.loc[s['RESUELTO'], 'TotalMVal'], errors='coerce').abs().sum()
    res.append({
        'Fondo': nm, 'ID': int(fid),
        'Posiciones': len(s),
        'Con métricas': int(s['RESUELTO'].sum()),
        '% Cobertura': s['RESUELTO'].mean(),
        'A PROP': int((~s['RESUELTO']).sum()),
        '% del AUM cubierto': (mv_o / mv_t if mv_t else np.nan),
        'vía JPM': int((s['Fuente_Yield'] == 'JPM').sum()),
        'vía RA': int((s['Fuente_Yield'] == 'RA').sum()),
        'vía BBG': int((s['Fuente_Yield'] == 'BBG').sum()),
    })
res = pd.DataFrame(res).sort_values('% Cobertura', ascending=False).reset_index(drop=True)
tot = {
    'Fondo': 'TOTAL', 'ID': '', 'Posiciones': POS, 'Con métricas': POS_RES,
    '% Cobertura': POS_RES / POS, 'A PROP': POS - POS_RES,
    '% del AUM cubierto': (pd.to_numeric(df.loc[df['RESUELTO'], 'TotalMVal'], errors='coerce').abs().sum()
                           / pd.to_numeric(df['TotalMVal'], errors='coerce').abs().sum()),
    'vía JPM': int((df['Fuente_Yield'] == 'JPM').sum()),
    'vía RA':  int((df['Fuente_Yield'] == 'RA').sum()),
    'vía BBG': int((df['Fuente_Yield'] == 'BBG').sum()),
}

# =============================================================================
# HOJA 3 — DETALLE
# =============================================================================
COLS = {
    'FundShortName': 'Fondo', 'PK2': 'PK2', 'ISIN': 'ISIN',
    'Name_Instrumento': 'Instrumento', 'CompanyName': 'Emisor',
    'Risk_Currency': 'Moneda', 'Hedge_Currency': 'Hedge',
    'JPM_Yield': 'JPM Yield', 'JPM_Duration': 'JPM Dur',
    'RA_Yield': 'RA Yield', 'RA_Duration': 'RA Dur',
    'BBG_Yield': 'BBG Yield', 'BBG_Duration': 'BBG Dur',
    'BBG_XCCY_Yield': 'BBG Yield Hedge',
    'Yield_final': 'Yield', 'Fuente_Yield': 'Fuente Y',
    'Duration_final': 'Duration', 'Fuente_Duration': 'Fuente D',
}
det = df[[c for c in COLS if c in df.columns]].rename(columns=COLS)
det['Estado'] = np.where(df['RESUELTO'].values, 'RESUELTO', 'PROP')
det = det.sort_values(['Fondo', 'Estado', 'Instrumento']).reset_index(drop=True)

prop = (det[det['Estado'] == 'PROP']
        [['Fondo', 'PK2', 'ISIN', 'Instrumento', 'Emisor', 'Moneda', 'Hedge']]
        .reset_index(drop=True))

facs = (fac[['FundShortName', 'PK2', 'Name_Instrumento', 'Risk_Currency', 'TotalMVal']]
        .rename(columns={'FundShortName': 'Fondo', 'Name_Instrumento': 'Instrumento',
                         'Risk_Currency': 'Moneda'}).reset_index(drop=True))

# =============================================================================
# ESCRITURA
# =============================================================================
with pd.ExcelWriter(F_OUT, engine='openpyxl') as w:
    panorama.to_excel(w, sheet_name='Panorama', index=False, startrow=3)
    comp.to_excel(w,     sheet_name='Panorama', index=False, startrow=len(panorama) + 8)
    pd.concat([res, pd.DataFrame([tot])], ignore_index=True).to_excel(
        w, sheet_name='Resumen por fondo', index=False, startrow=3)
    det.to_excel(w,  sheet_name='Detalle',   index=False, startrow=2)
    prop.to_excel(w, sheet_name='A PROP',    index=False, startrow=2)
    facs.to_excel(w, sheet_name='Facturas',  index=False, startrow=2)

wb = load_workbook(F_OUT)

def titulo(ws, t, sub, nc):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(nc, 3))
    c = ws.cell(1, 1, t); c.font = Font(name=FONT, size=12, bold=True, color=NAVY)
    ws.row_dimensions[1].height = 20
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(nc, 3))
    c = ws.cell(2, 1, sub); c.font = Font(name=FONT, size=9, italic=True, color='595959')

def encabezado(ws, row, nc):
    for j in range(1, nc + 1):
        c = ws.cell(row, j)
        c.fill = PatternFill('solid', fgColor=NAVY)
        c.font = Font(name=FONT, size=9, bold=True, color=WHITE)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[row].height = 28

def anchos(ws, nc, hdr, mn=10, mx=44):
    for j in range(1, nc + 1):
        m = max((len(str(ws.cell(i, j).value)) for i in range(hdr, ws.max_row + 1)
                 if ws.cell(i, j).value is not None), default=8)
        ws.column_dimensions[get_column_letter(j)].width = min(max(m + 3, mn), mx)

# ── Panorama ─────────────────────────────────────────────────────────────────
ws = wb['Panorama']
titulo(ws, 'Cobertura de métricas de mercado — Yield y Duration',
       f'Cierre {FECHA[6:8]}/{FECHA[4:6]}/{FECHA[:4]}. Un papel se considera resuelto cuando se obtiene '
       f'Yield Y Duration de al menos un proveedor (JPM, RiskAmérica o Bloomberg). '
       f'Se excluyen {N_FAC} facturas, que no son bonos.', 3)
HDR = 4
encabezado(ws, HDR, 3)
SECC = {'UNIVERSO', 'RESULTADO — MEDIDO EN POSICIONES',
        'RESULTADO — MEDIDO EN PAPELES ÚNICOS', 'RECONCILIACIÓN ENTRE AMBAS MEDIDAS'}
for i in range(HDR + 1, HDR + 1 + len(panorama)):
    lbl = ws.cell(i, 1).value
    for j in range(1, 4):
        c = ws.cell(i, j)
        c.font = Font(name=FONT, size=9); c.border = BORDER
        c.alignment = Alignment(vertical='center', wrap_text=(j == 3))
    if lbl in SECC:
        for j in range(1, 4):
            ws.cell(i, j).fill = PatternFill('solid', fgColor='E8EEF4')
        ws.cell(i, 1).font = Font(name=FONT, size=9, bold=True, color=NAVY)
        continue
    if lbl in (None, ''):
        continue
    ws.cell(i, 1).font = Font(name=FONT, size=9,
                              italic=str(lbl).startswith(('   ', 'corresponden')),
                              color='595959' if str(lbl).startswith(('   ', 'corresponden')) else '000000')
    v = ws.cell(i, 2)
    v.font = Font(name=FONT, size=10, bold=True)
    v.alignment = Alignment(horizontal='center')
    v.number_format = '#,##0'
    if str(lbl).startswith('Papeles únicos resueltos'):
        for j in range(1, 4):
            ws.cell(i, j).fill = PatternFill('solid', fgColor=AMBER_F)
ws.column_dimensions['A'].width = 50
ws.column_dimensions['B'].width = 14
ws.column_dimensions['C'].width = 76
for i in range(HDR + 1, HDR + 1 + len(panorama)):
    ws.row_dimensions[i].height = 26

r0 = len(panorama) + 8
ws.cell(r0, 1, 'Papeles compartidos entre fondos y rescate vía hermano de serie').font = \
    Font(name=FONT, size=11, bold=True, color=NAVY)
HR = r0 + 1
encabezado(ws, HR, comp.shape[1])
cc = {c: i + 1 for i, c in enumerate(comp.columns)}
for i in range(HR + 1, HR + 1 + len(comp)):
    for j in range(1, comp.shape[1] + 1):
        c = ws.cell(i, j); c.font = Font(name=FONT, size=9); c.border = BORDER
        if (i - HR) % 2 == 0:
            c.fill = PatternFill('solid', fgColor=GREY_L)
    ws.cell(i, cc['% Compartido']).number_format = '0.0%'
    ws.cell(i, cc['Fondo']).alignment = Alignment(horizontal='left')
ws.sheet_view.showGridLines = False

# ── Resumen por fondo ────────────────────────────────────────────────────────
ws = wb['Resumen por fondo']
NC = res.shape[1]
titulo(ws, 'Resumen por fondo',
       f'Medido en POSICIONES (fondo × PK2), excluidas las facturas. Las columnas "vía JPM / RA / BBG" '
       f'indican el proveedor de la métrica utilizada y suman exactamente "Con métricas".', NC)
HDR = 4
encabezado(ws, HDR, NC)
r0, r1 = HDR + 1, HDR + len(res)
col = {c: i + 1 for i, c in enumerate(res.columns)}
for i in range(r0, r1 + 2):
    for j in range(1, NC + 1):
        c = ws.cell(i, j); c.font = Font(name=FONT, size=9); c.border = BORDER
        if i <= r1 and (i - r0) % 2 == 1:
            c.fill = PatternFill('solid', fgColor=GREY_L)
    for cn in ('% Cobertura', '% del AUM cubierto'):
        ws.cell(i, col[cn]).number_format = '0.0%'
    for cn in ('Posiciones', 'Con métricas', 'A PROP', 'vía JPM', 'vía RA', 'vía BBG'):
        ws.cell(i, col[cn]).number_format = '#,##0'
    ws.cell(i, col['Fondo']).alignment = Alignment(horizontal='left')
    v = ws.cell(i, col['% Cobertura']).value
    if isinstance(v, (int, float)) and i <= r1:
        f, t = (GREEN_F, GREEN) if v >= 0.8 else (AMBER_F, '9C5700') if v >= 0.5 else (RED_F, RED)
        c = ws.cell(i, col['% Cobertura'])
        c.fill = PatternFill('solid', fgColor=f)
        c.font = Font(name=FONT, size=9, bold=True, color=t)
for j in range(1, NC + 1):
    c = ws.cell(r1 + 1, j)
    c.font = Font(name=FONT, size=9, bold=True, color=WHITE)
    c.fill = PatternFill('solid', fgColor=NAVY)
ws.cell(r1 + 1, col['% Cobertura']).number_format = '0.0%'

n = r1 + 3
ws.cell(n, 1, 'Notas').font = Font(name=FONT, size=10, bold=True, color=NAVY)
for k, t in enumerate([
    f'· {POS_RES:,} de {POS:,} posiciones ({POS_RES/POS:.0%}) se resuelven con métricas ya calculadas por los proveedores.',
    f'· Esas {POS_RES:,} posiciones corresponden a {UNI_RES:,} papeles únicos: los papeles compartidos se cuentan una vez por cada fondo.',
    f'· {UNI_PROP:,} papeles únicos no tienen métrica en ninguna fuente y requieren modelación propia (PROP).',
    f'· Se excluyeron {N_FAC} facturas del universo (ver hoja "Facturas"); ningún proveedor entrega métricas para ellas.',
    '· Prioridad de fuente cuando hay más de una disponible: JPM > RiskAmérica > Bloomberg.',
], 1):
    ws.cell(n + k, 1, t).font = Font(name=FONT, size=9, color='404040')
anchos(ws, NC, HDR)
ws.freeze_panes = f'A{HDR+1}'
ws.sheet_view.showGridLines = False

# ── Detalle / A PROP / Facturas ──────────────────────────────────────────────
for sh, t, sub in (
    ('Detalle', 'Detalle por posición',
     'Métricas de cada proveedor. "Yield" y "Duration" son los valores seleccionados según prioridad JPM > RA > BBG.'),
    ('A PROP', 'Papeles sin métricas disponibles',
     'No se obtuvo Yield ni Duration de JPM, RiskAmérica ni Bloomberg. Requieren modelación propia.'),
    ('Facturas', 'Facturas excluidas del universo',
     'Instrumentos FAC... de MRCLP. No son bonos y ningún proveedor de métricas los cubre.'),
):
    ws = wb[sh]; nc = ws.max_column
    titulo(ws, t, sub, nc)
    encabezado(ws, 3, nc)
    for i in range(4, ws.max_row + 1):
        for j in range(1, nc + 1):
            c = ws.cell(i, j); c.font = Font(name=FONT, size=9); c.border = BORDER
    if sh == 'Detalle':
        dc = {c: i + 1 for i, c in enumerate(det.columns)}
        for i in range(4, ws.max_row + 1):
            for cn in ('JPM Yield', 'RA Yield', 'BBG Yield', 'BBG Yield Hedge', 'Yield',
                       'JPM Dur', 'RA Dur', 'BBG Dur', 'Duration'):
                if cn in dc: ws.cell(i, dc[cn]).number_format = '0.00'
            for cn in ('Yield', 'Duration'):
                ws.cell(i, dc[cn]).font = Font(name=FONT, size=9, bold=True)
            e = ws.cell(i, dc['Estado'])
            ok = e.value == 'RESUELTO'
            e.fill = PatternFill('solid', fgColor=GREEN_F if ok else RED_F)
            e.font = Font(name=FONT, size=9, bold=not ok, color=GREEN if ok else RED)
    anchos(ws, nc, 3)
    ws.freeze_panes = 'A4'
    ws.auto_filter.ref = f"A3:{get_column_letter(nc)}{ws.max_row}"
    ws.sheet_view.showGridLines = False

wb.save(F_OUT)
print(f"OK → {F_OUT}")
print(f"Posiciones {POS_RES}/{POS} ({POS_RES/POS:.1%}) | Únicos {UNI_RES}/{UNI} ({UNI_RES/UNI:.1%}) | PROP {UNI_PROP}")
