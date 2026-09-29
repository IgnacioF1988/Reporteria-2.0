# =============================================================================
# 00_MAPEO_UNIVERSO.py — Mapeo de universo multi-fondo (NUEVA LÓGICA MÉTRICAS)
# =============================================================================
# CONTEXTO
#   Reemplaza (en primera instancia) a INPUT_TD_BBG.xlsx como punto de partida.
#   INPUT_TD_BBG respondía "¿a qué proveedor voy a buscar la TABLA DE DESARROLLO?".
#   Este script responde una pregunta distinta y previa: "¿qué universo tengo,
#   en qué moneda debo salir (Hedge_Currency) y con qué atributos de familia
#   (REGS/144A/EMTN) cuento, para luego ir a buscar MÉTRICAS YA CALCULADAS
#   (Yield, Duration, ...) de BBG/RA por PK2?"
#
# INPUTS
#   CUBO_{CUBO_FECHA}.xlsx        (RUTA_CUBO_DIR)  → posiciones crudas
#   BD_INSTRUMENTOS.xlsx          (BD_INSTR_PATH)  → Risk_Currency, Investment_Type_Code (fuente canónica)
#   BD_FUNDS.xlsx                 (BD_FUNDS_PATH)  → FundShortName, FundBaseCurrency por ID_Fund
#
# OUTPUT
#   MAPEO_UNIVERSO_{CUBO_FECHA}.xlsx
#     - con_ISIN     : PK2 con ISIN (candidatos a BBG/RA)
#     - sin_ISIN     : PK2 sin ISIN (loans, distress, estructuras) + Sugerencia_Proveedor
#     - resumen      : conteos por fondo / proveedor sugerido / hedge
#
# REGLA Hedge_Currency (SOLO fondos HEDGE_FUNDS = [11, 17, 20])
#   Si Risk_Currency ∈ STRONG_CCY (USD, GBP, EUR) → se asume el papel está
#   hedgeado a la moneda local del Risk_Country (regla rápida, sujeta a
#   OVERRIDE manual posterior). Mapeo Risk_Country → moneda local en
#   RISK_COUNTRY_TO_LOCAL_CCY.
#   Para el resto de los fondos: Hedge_Currency = lo que traiga el CUBO (o NaN).
#
# NOTA IMPORTANTE
#   Filtra Investment_Type_Code == 1 (solo Renta Fija), igual que 00_CARTERA_VF.
#   Si se requiere el universo SIN ese filtro, comentar el bloque [FILTRO RF].
# =============================================================================

import os
import sys
import glob
import re
import numpy as np
import pandas as pd
from datetime import datetime
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

_script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
if _script_dir not in sys.path: sys.path.insert(0, _script_dir)
if os.getcwd() not in sys.path:  sys.path.insert(0, os.getcwd())

from pipeline_config import (
    FECHA, RUTA_CUBO_DIR, BD_INSTR_PATH, BD_FUNDS_PATH, OUT_UNIVERSO,
    FONDOS, HEDGE_FUNDS, HEDGE_FUNDS_POR_PAIS, HEDGE_FUNDS_A_CLP,
    STRONG_CCY, RISK_COUNTRY_TO_LOCAL_CCY,
)

# ============================================================
# ⚙️ CONFIGURACIÓN — editable
# ============================================================
CUBO_FECHA   = FECHA        # la fecha del cierre sale del config
# FONDOS, HEDGE_FUNDS, STRONG_CCY y el mapa de monedas vienen del config

# Países donde SÍ tenemos RiskAmérica como proveedor alternativo (sin ISIN)
PAISES_RA = {'CL'}

# Columna de valor: rango dinámico [LocalPrice ... TotalMVal] del propio CUBO
COL_VALOR_INICIO = 'LocalPrice'
COL_VALOR_FIN    = 'TotalMVal'
COLS_VALOR_EXCLUIR = {'TotalMVal_Balance'}   # redundante

APLICAR_FILTRO_RF = True   # [FILTRO RF] Investment_Type_Code == 1

# Período anterior (YYYYMMDD). Si existe su UNIVERSO, el Hedge_Currency de los
# HEDGE_FUNDS se hereda de ahí (ya viene corregido a mano) y la regla solo se
# aplica a los PK2 NUEVOS. None = primera corrida → regla para todo.
# Se detecta solo en pipeline_config (carpeta 02_OUTPUTS más reciente < FECHA).
# Se puede forzar con FECHA_ANT_OVERRIDE allá si hace falta saltarse un cierre.
try:
    from pipeline_config import FECHA_ANT, OUT_UNIVERSO_ANT
except ImportError:
    FECHA_ANT, OUT_UNIVERSO_ANT = None, None

# Facturas: se excluyen del universo a buscar (Name_Instrumento empieza con FAC
# seguido de letras/dígitos, p.ej. FACRCGP77804). Ojo: 'empieza con', no
# 'contiene' — ALFACL, SOLFACIL son bonos.
FACTURA_RE = re.compile(r'^FAC[A-Z]*\d', re.IGNORECASE)

N_WARN_CONSOLA = 15   # máx. filas de detalle por WARN en consola (todo va a la hoja 'warnings')

FNAME_OUT = OUT_UNIVERSO
# ============================================================

# ── Regex sufijos de serie (idéntico a 00_CARTERA_VF.py) ─────────────────────
SERIES_RE = re.compile(r'\s+(REGS|REGS-S|REG\s*S|144A|144a|144@|EMTN)\s*$', re.IGNORECASE)

def extract_base(name):
    return SERIES_RE.sub('', str(name).strip()).strip()

# ── Helpers formato Excel ─────────────────────────────────────────────────────
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
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 8), 45)

# ── Registro de WARN con PK2 explícito ────────────────────────────────────────
WARNS = []

def warn(paso, tipo, df=None, detalle=''):
    """Imprime el WARN con los PK2 afectados y lo guarda para la hoja 'warnings'."""
    n = 0 if df is None else len(df)
    print(f"   [WARN] {tipo}" + (f" — {n} fila(s)" if df is not None else '') + (f" | {detalle}" if detalle else ''))
    if df is None or n == 0:
        WARNS.append({'Paso': paso, 'Tipo': tipo, 'Detalle': detalle})
        return
    cols = [c for c in ('ID_Fund', 'PK2', 'Name_Instrumento', 'ISIN', 'Risk_Country',
                        'Risk_Currency') if c in df.columns]
    for i, (_, r) in enumerate(df.iterrows()):
        fila = {'Paso': paso, 'Tipo': tipo, 'Detalle': detalle, **{c: r[c] for c in cols}}
        WARNS.append(fila)
        if i < N_WARN_CONSOLA:
            print("          · " + " | ".join(f"{c}={r[c]}" for c in cols))
    if n > N_WARN_CONSOLA:
        print(f"          … y {n - N_WARN_CONSOLA} más (ver hoja 'warnings')")

print("=" * 70)
print(f"00_MAPEO_UNIVERSO | CUBO={CUBO_FECHA} | Fondos={FONDOS}")
print("=" * 70)

# =============================================================================
# [1] LEER CUBO ESPECÍFICO
# =============================================================================
print(f"\n[1] Buscando CUBO_{CUBO_FECHA}.xlsx...")

cubo_path = os.path.join(RUTA_CUBO_DIR, f"CUBO_{CUBO_FECHA}.xlsx")
if not os.path.exists(cubo_path):
    # fallback: buscar variantes con glob si el nombre exacto no calza
    candidatos = sorted(glob.glob(os.path.join(RUTA_CUBO_DIR, f"CUBO_{CUBO_FECHA}*.xlsx")))
    if not candidatos:
        print(f"   ERROR: no se encontró CUBO para {CUBO_FECHA} en:\n   {RUTA_CUBO_DIR}")
        sys.exit(1)
    cubo_path = candidatos[-1]

print(f"   Usando: {os.path.basename(cubo_path)}")
df_cubo = pd.read_excel(cubo_path, engine='openpyxl')
print(f"   Filas CUBO totales: {len(df_cubo):,}")
print(f"   Columnas CUBO ({len(df_cubo.columns)}): {list(df_cubo.columns)}")

# ID_Instrumento derivado del PK2 (parte antes del guión) — igual que 00_CARTERA
df_cubo['ID_Instrumento'] = pd.to_numeric(
    df_cubo['PK2'].astype(str).str.split('-').str[0], errors='coerce')

# Filtro de fondos
df_cubo = df_cubo[df_cubo['ID_Fund'].isin(FONDOS)].copy()
print(f"   Filas tras filtro fondos {FONDOS}: {len(df_cubo):,}")
faltan_fondos = sorted(set(FONDOS) - set(df_cubo['ID_Fund'].unique()))
if faltan_fondos:
    print(f"   [WARN] Fondos sin filas en el CUBO: {faltan_fondos}")

# =============================================================================
# [2] CRUZAR CON BD_INSTRUMENTOS (fuente canónica Risk_Currency / Investment_Type_Code)
# =============================================================================
print(f"\n[2] Cruzando con BD_INSTRUMENTOS...")

bd_instr = pd.read_excel(BD_INSTR_PATH, sheet_name='BD_INSTRUMENTOS', engine='openpyxl')
bd_instr['PK2'] = (bd_instr['ID_Instrumento'].astype(str).str.strip()
                   + '-' + bd_instr['SubID_Instrumento'].astype(str).str.strip())

bd_cols = ['PK2', 'Risk_Currency', 'Investment_Type_Code', 'Issue_Type_Code',
           'Name_Instrumento', 'ISIN', 'Risk_Country', 'CompanyName']
bd_cols = [c for c in bd_cols if c in bd_instr.columns]
faltantes_bd = [c for c in ('Name_Instrumento', 'ISIN', 'Risk_Country', 'CompanyName', 'Issue_Type_Code') if c not in bd_cols]
if faltantes_bd:
    print(f"   [WARN] Columnas no encontradas en BD_INSTRUMENTOS: {faltantes_bd}. Disponibles: {list(bd_instr.columns)}")

for _drop in ['Risk_Currency', 'Investment_Type_Code', 'Issue_Type_Code', 'Name_Instrumento', 'ISIN', 'Risk_Country', 'CompanyName']:
    if _drop in df_cubo.columns:
        df_cubo = df_cubo.drop(columns=[_drop])

df_cubo = pd.merge(df_cubo, bd_instr[bd_cols], on='PK2', how='left')
n_sin_rc = df_cubo['Risk_Currency'].isna().sum()
print(f"   PK2 sin Risk_Currency tras cruce: {n_sin_rc}")
if n_sin_rc:
    warn('[2]', 'PK2 sin match / sin Risk_Currency en BD_INSTRUMENTOS',
         df_cubo[df_cubo['Risk_Currency'].isna()].drop_duplicates(['ID_Fund', 'PK2']))

# [FILTRO RF]
if APLICAR_FILTRO_RF and 'Investment_Type_Code' in df_cubo.columns:
    n_antes = len(df_cubo)
    df_cubo = df_cubo[df_cubo['Investment_Type_Code'] == 1].copy()
    print(f"   Filtro Investment_Type_Code=1: {n_antes:,} → {len(df_cubo):,}")

# Un registro por PK2 × ID_Fund
df_cubo = df_cubo.drop_duplicates(subset=['ID_Fund', 'PK2']).copy()
print(f"   PK2 únicos (fondo × PK2): {len(df_cubo):,}")

# =============================================================================
# [3] CRUZAR CON BD_FUNDS (FundShortName, FundBaseCurrency)
# =============================================================================
print(f"\n[3] Cruzando con BD_FUNDS...")

bd_funds = pd.read_excel(BD_FUNDS_PATH, engine='openpyxl')

# Detección tolerante de nombre de columna 'FundShortName'
col_short = next((c for c in bd_funds.columns if 'shortname' in c.lower().replace('_', '')), None)
col_basec = next((c for c in bd_funds.columns if 'basecurrency' in c.lower().replace('_', '')), None)

if col_short is None or col_basec is None:
    print(f"   [WARN] No se encontraron columnas esperadas en BD_FUNDS. Columnas disponibles: {list(bd_funds.columns)}")
if col_short is None: col_short = 'FundShortName'
if col_basec is None: col_basec = 'FundBaseCurrency'

bd_funds_cols = ['ID_Fund'] + [c for c in {col_short, col_basec} if c in bd_funds.columns]
bd_funds_slim = bd_funds[bd_funds_cols].drop_duplicates(subset=['ID_Fund']).rename(
    columns={col_short: 'FundShortName', col_basec: 'FundBaseCurrency'}
)

df_cubo = pd.merge(df_cubo, bd_funds_slim, on='ID_Fund', how='left')
n_sin_fund = df_cubo['FundShortName'].isna().sum() if 'FundShortName' in df_cubo.columns else len(df_cubo)
print(f"   PK2 sin match en BD_FUNDS: {n_sin_fund}")
if n_sin_fund and 'FundShortName' in df_cubo.columns:
    warn('[3]', 'ID_Fund sin match en BD_FUNDS',
         df_cubo[df_cubo['FundShortName'].isna()].drop_duplicates(['ID_Fund']),
         'revisar BD_FUNDS')

# =============================================================================
# [4] LÓGICA DE FAMILIA — base_name / serie_hermanos (idéntica a 00_CARTERA_VF)
# =============================================================================
print(f"\n[4] Agrupamiento por familia (REGS/144A/EMTN)...")

df_cubo['Name_Instrumento'] = df_cubo['Name_Instrumento'].astype(str).str.strip()
df_cubo['base_name']    = df_cubo['Name_Instrumento'].apply(extract_base)
df_cubo['tiene_sufijo'] = df_cubo['Name_Instrumento'] != df_cubo['base_name']

# Grupo de familia a nivel de ISIN único (independiente del fondo que lo tenga)
isin_ok = df_cubo['ISIN'].notna() & (df_cubo['ISIN'].astype(str).str.strip() != '')
dedup_isin = df_cubo[isin_ok].drop_duplicates(subset=['ISIN'])[['ISIN', 'Name_Instrumento', 'base_name']]

grupos = (dedup_isin.groupby('base_name')
          .agg(n_series=('ISIN', 'count'), isins=('ISIN', list))
          .reset_index())
grupos_multi = grupos[grupos['n_series'] > 1]

serie_grupo_id_map = {}
serie_hermanos_map = {}
for _, g in grupos_multi.iterrows():
    isins = g['isins']
    gid   = isins[0]
    for isin in isins:
        serie_grupo_id_map[isin] = gid
        serie_hermanos_map[isin] = ','.join([x for x in isins if x != isin])

df_cubo['serie_grupo_id'] = df_cubo['ISIN'].map(serie_grupo_id_map).fillna('')
df_cubo['serie_hermanos'] = df_cubo['ISIN'].map(serie_hermanos_map).fillna('')

print(f"   Grupos multi-serie (ISIN único): {len(grupos_multi)}")

# =============================================================================
# [5] Hedge_Currency — regla DISTINTA por fondo
# =============================================================================
print(f"\n[5] Aplicando regla Hedge_Currency...")
print(f"   MLDL (17)        → swap a la moneda LOCAL del Risk_Country del papel")
print(f"   MDCH/MRCLP (11,20) → swap DIRECTO a CLP (exposición 100% CLP)")

if 'Hedge_Currency' not in df_cubo.columns:
    df_cubo['Hedge_Currency'] = np.nan

def _regla_hedge(row):
    fid = row['ID_Fund']
    rc  = str(row.get('Risk_Currency', '')).strip().upper()
    if fid in HEDGE_FUNDS_POR_PAIS:
        if rc in STRONG_CCY:
            pais = str(row.get('Risk_Country', '')).strip().upper()
            return RISK_COUNTRY_TO_LOCAL_CCY.get(pais, np.nan)
        return np.nan
    if fid in HEDGE_FUNDS_A_CLP:
        return 'CLP' if rc in STRONG_CCY else np.nan
    return row.get('Hedge_Currency', np.nan)

df_cubo['Hedge_Currency_regla'] = df_cubo.apply(_regla_hedge, axis=1)
df_cubo['Hedge_Origen'] = np.where(df_cubo['ID_Fund'].isin(HEDGE_FUNDS), 'REGLA', 'CUBO')

# ── Herencia del mes anterior (ya corregido manualmente) ─────────────────────
hedge_prev = None
if FECHA_ANT and OUT_UNIVERSO_ANT:
    path_prev = OUT_UNIVERSO_ANT
    if os.path.exists(path_prev):
        _hojas = pd.read_excel(path_prev, sheet_name=None, engine='openpyxl')
        _prev = pd.concat([h for k, h in _hojas.items()
                           if k in ('con_ISIN', 'sin_ISIN', 'defaulteados', 'facturas')
                           and {'ID_Fund', 'PK2', 'Hedge_Currency'}.issubset(h.columns)],
                          ignore_index=True)
        _prev['_k'] = _prev['ID_Fund'].astype(str) + '|' + _prev['PK2'].astype(str).str.strip()
        _prev = _prev.drop_duplicates('_k')
        hedge_prev = dict(zip(_prev['_k'], _prev['Hedge_Currency']))
        print(f"   Heredando Hedge_Currency de {os.path.basename(path_prev)} ({len(hedge_prev):,} fondo×PK2)")
    else:
        warn('[5]', 'No existe UNIVERSO del período anterior — se aplica la regla a TODO',
             detalle=path_prev)
else:
    print("   FECHA_ANT=None (primera corrida) → regla aplicada a todo el universo")

if hedge_prev is not None:
    _k = df_cubo['ID_Fund'].astype(str) + '|' + df_cubo['PK2'].astype(str).str.strip()
    en_prev = _k.isin(hedge_prev.keys()) & df_cubo['ID_Fund'].isin(HEDGE_FUNDS)
    # Se hereda el valor tal cual (incluido NaN = 'sin hedge' decidido a mano)
    df_cubo.loc[en_prev, 'Hedge_Currency_regla'] = _k[en_prev].map(hedge_prev)
    df_cubo.loc[en_prev, 'Hedge_Origen'] = 'MES_ANTERIOR'
    nuevos = df_cubo[df_cubo['ID_Fund'].isin(HEDGE_FUNDS) & ~en_prev]
    print(f"   Heredados: {int(en_prev.sum())} | NUEVOS (regla aplicada): {len(nuevos)}")
    if len(nuevos):
        warn('[5]', 'PK2 NUEVOS en fondos hedge — Hedge_Currency por regla, REVISAR', nuevos,
             'no estaban en el período anterior')

mask_hedge_funds = df_cubo['ID_Fund'].isin(HEDGE_FUNDS)
for fid in sorted(HEDGE_FUNDS):
    sub = df_cubo[df_cubo['ID_Fund'] == fid]
    n = sub['Hedge_Currency_regla'].notna().sum()
    tipo = 'por país' if fid in HEDGE_FUNDS_POR_PAIS else 'directo a CLP'
    print(f"   Fondo {fid} ({tipo}): {n} PK2 con Hedge_Currency derivado")

_sin_map = df_cubo[df_cubo['ID_Fund'].isin(HEDGE_FUNDS_POR_PAIS)
                   & (df_cubo['Hedge_Origen'] == 'REGLA')
                   & df_cubo['Risk_Currency'].astype(str).str.upper().isin(STRONG_CCY)
                   & df_cubo['Hedge_Currency_regla'].isna()]
if len(_sin_map):
    warn('[5]', 'Risk_Country en strong-ccy sin mapeo local', _sin_map,
         f"países {sorted(_sin_map['Risk_Country'].dropna().unique())} — completar RISK_COUNTRY_TO_LOCAL_CCY")

# Hedge_Currency final: para HEDGE_FUNDS, la regla manda; para el resto, se mantiene lo que trajo el CUBO
df_cubo['Hedge_Currency_final'] = np.where(
    mask_hedge_funds, df_cubo['Hedge_Currency_regla'], df_cubo['Hedge_Currency']
)

# =============================================================================
# [6] Columnas de valor dinámicas [LocalPrice ... TotalMVal]
# =============================================================================
print(f"\n[6] Detectando columnas de valor [{COL_VALOR_INICIO} .. {COL_VALOR_FIN}]...")

cols_all = list(df_cubo.columns)
cols_valor = []
if COL_VALOR_INICIO in cols_all and COL_VALOR_FIN in cols_all:
    i0 = cols_all.index(COL_VALOR_INICIO)
    i1 = cols_all.index(COL_VALOR_FIN)
    if i1 >= i0:
        cols_valor = [c for c in cols_all[i0:i1+1] if c not in COLS_VALOR_EXCLUIR]
    else:
        warn('[6]', 'Orden inesperado de columnas de valor', detalle=f"{COL_VALOR_FIN} antes de {COL_VALOR_INICIO}")
        print(f"   [WARN] Orden inesperado: {COL_VALOR_FIN} aparece antes de {COL_VALOR_INICIO} en el CUBO")
else:
    faltan = [c for c in (COL_VALOR_INICIO, COL_VALOR_FIN) if c not in cols_all]
    print(f"   [WARN] Columnas no encontradas en el CUBO: {faltan}. Revisar nombres reales.")

print(f"   Columnas de valor detectadas: {cols_valor}")

# =============================================================================
# [7] ARMAR OUTPUT FINAL
# =============================================================================
print(f"\n[7] Armando estructura final...")

df_cubo['Hedge_Currency'] = df_cubo['Hedge_Currency_final']

COLS_FRONT = [
    'ID_Fund', 'FundShortName', 'FundBaseCurrency',
    'PK2', 'ID_Instrumento', 'ISIN', 'Name_Instrumento', 'CompanyName',
    'base_name', 'serie_grupo_id', 'serie_hermanos', 'tiene_sufijo',
    'Risk_Country', 'Risk_Currency', 'Hedge_Currency', 'Hedge_Origen',
    'Investment_Type_Code', 'Issue_Type_Code',
]
COLS_FRONT = [c for c in COLS_FRONT if c in df_cubo.columns]
cols_finales = COLS_FRONT + [c for c in cols_valor if c not in COLS_FRONT]
cols_finales = [c for c in cols_finales if c in df_cubo.columns]

df_final = df_cubo[cols_finales].copy()

# ── EXCLUIR DEFAULTEADOS ────────────────────────────────────────────────────
# DEF y PROPDEF nunca muestran Yield/Duration de proveedor (se les asigna
# Yield=0 / Dur=0.5 en 05_CONSOLIDA). Se sacan del universo ANTES de salir a
# buscar métricas, para no gastar consultas en papeles cuyo resultado ya está
# definido por regla. Solo se necesita el subconjunto de defaulteados: lo que
# no está en esa lista se asume vigente.
df_def = pd.DataFrame()
try:
    from pipeline_config import DEFAULTEADOS_PATH, MARCAS_DEF
    if os.path.exists(DEFAULTEADOS_PATH):
        _dd = pd.read_excel(DEFAULTEADOS_PATH, engine='openpyxl')
        _dd.columns = [str(c).strip() for c in _dd.columns]
        _cd = next((c for c in _dd.columns if c.upper() == 'DEF'), None)
        _dd['PK2'] = _dd['PK2'].astype(str).str.strip()
        _dd = _dd[_dd[_cd].astype(str).str.strip().str.upper().isin(MARCAS_DEF)]
        pk2_def = dict(zip(_dd['PK2'], _dd[_cd].astype(str).str.strip().str.upper()))
        mask_def = df_final['PK2'].astype(str).str.strip().isin(pk2_def)
        df_def = df_final[mask_def].copy()
        df_def['Estado_DEF'] = df_def['PK2'].astype(str).str.strip().map(pk2_def)
        df_final = df_final[~mask_def].copy()
        print(f"\n[DEF] Defaulteados excluidos: {len(df_def)} posiciones "
              f"({df_def['PK2'].nunique()} PK2)")
        for k, v in df_def['Estado_DEF'].value_counts().items():
            print(f"      {k}: {v}")
        print(f"[DEF] Posiciones restantes: {len(df_final)}")
    else:
        print(); warn('[DEF]', 'No existe archivo de defaulteados — no se excluye nada', detalle=DEFAULTEADOS_PATH)
except Exception as _e:
    print(); warn('[DEF]', 'No se pudo aplicar exclusión de defaulteados', detalle=str(_e))

# ── EXCLUIR FACTURAS ─────────────────────────────────────────────────────────
mask_fac = df_final['Name_Instrumento'].astype(str).str.strip().str.match(FACTURA_RE)
df_fac = df_final[mask_fac].copy()
df_final = df_final[~mask_fac].copy()
print(f"\n[FAC] Facturas excluidas: {len(df_fac)} posiciones ({df_fac['PK2'].nunique()} PK2)")
if len(df_fac) and 'Issue_Type_Code' in df_fac.columns:
    _raras = df_fac[pd.to_numeric(df_fac['Issue_Type_Code'], errors='coerce') != 5]
    if len(_raras):
        warn('[FAC]', 'Nombre tipo factura pero Issue_Type_Code != 5 — confirmar', _raras)

mask_isin = df_final['ISIN'].notna() & (df_final['ISIN'].astype(str).str.strip() != '')
df_con_isin = df_final[mask_isin].copy()
df_sin_isin = df_final[~mask_isin].copy()

# Sugerencia de proveedor para sin_ISIN
# RA solo aplica a bonos (Issue_Type_Code == 3, dentro del universo ya
# filtrado a Investment_Type_Code == 1). Esto acota el universo que se manda
# a revisar en el plug-in de RA — no todo Risk_Country=='CL' es bono.
def _sugerir_proveedor(row):
    pais = str(row.get('Risk_Country', '')).strip().upper()
    ityp = row.get('Issue_Type_Code', np.nan)
    es_bono = pd.notna(ityp) and int(ityp) == 3
    if pais in PAISES_RA and es_bono:
        return 'RA (posible) / revisar'
    return 'PROP'

df_sin_isin['Sugerencia_Proveedor'] = df_sin_isin.apply(_sugerir_proveedor, axis=1)

print(f"   con_ISIN : {len(df_con_isin):,} posiciones")
print(f"   sin_ISIN : {len(df_sin_isin):,} posiciones")

# =============================================================================
# [7b] UNIVERSO OBJETIVO — lo que realmente hay que ir a buscar
# =============================================================================
# Una métrica por instrumento: se colapsan (a) el mismo PK2 en varios fondos y
# (b) las series hermanas REGS/144A/EMTN (misma métrica). Clave de búsqueda:
# serie_grupo_id si tiene familia, si no ISIN, si no PK2.
def _clave(r):
    g = str(r.get('serie_grupo_id', '') or '').strip()
    i = str(r.get('ISIN', '') or '').strip()
    if g: return g
    if i and i.lower() != 'nan': return i
    return str(r['PK2']).strip()

df_final['Clave_Busqueda'] = df_final.apply(_clave, axis=1)
df_final['Tiene_ISIN'] = mask_isin
_obj = df_final.copy()
_obj['_fondo'] = _obj['FundShortName'].fillna(_obj['ID_Fund'].astype(str)) if 'FundShortName' in _obj.columns else _obj['ID_Fund'].astype(str)
df_obj = (_obj.sort_values(['Clave_Busqueda', 'tiene_sufijo'])
          .groupby('Clave_Busqueda')
          .agg(PK2_representante=('PK2', 'first'),
               ISIN=('ISIN', 'first'),
               Name_Instrumento=('Name_Instrumento', 'first'),
               CompanyName=('CompanyName', 'first') if 'CompanyName' in _obj.columns else ('PK2', 'first'),
               Risk_Country=('Risk_Country', 'first'),
               Risk_Currency=('Risk_Currency', 'first'),
               Issue_Type_Code=('Issue_Type_Code', 'first'),
               Tiene_ISIN=('Tiene_ISIN', 'first'),
               PK2_cubiertos=('PK2', lambda s: ', '.join(sorted(set(s.astype(str))))),
               N_PK2=('PK2', 'nunique'),
               Fondos=('_fondo', lambda s: ', '.join(sorted(set(s.astype(str))))),
               N_fondos=('ID_Fund', 'nunique'),
               N_posiciones=('PK2', 'size'),
               TotalMVal=('TotalMVal', 'sum'))
          .reset_index())
df_obj['Sugerencia_Proveedor'] = np.where(
    df_obj['Tiene_ISIN'], 'BBG / RA',
    df_obj.apply(_sugerir_proveedor, axis=1))
df_obj = df_obj.sort_values(['Tiene_ISIN', 'TotalMVal'], ascending=[False, False])

n_pos_tot  = len(df_final) + len(df_def) + len(df_fac)
n_pos_vig  = len(df_final)
n_pk2_unic = df_final['PK2'].nunique()
n_obj      = len(df_obj)
n_obj_isin = int(df_obj['Tiene_ISIN'].sum())

# =============================================================================
# [8] RESUMEN
# =============================================================================
print(f"\n[8] Generando resumen...")

resumen_rows = []
R = lambda m='', v='', d='': resumen_rows.append({'Métrica': m, 'Valor': v, 'Detalle': d})
R('── EMBUDO: UNIVERSO OBJETIVO A BUSCAR ──')
R('Posiciones RF (fondo × PK2)', n_pos_tot)
R('  (−) Defaulteados', -len(df_def), 'métrica por regla (Yield=0 / Dur=0.5)')
R('  (−) Facturas (FAC…)', -len(df_fac), 'fuera de la búsqueda de métricas')
R('= Posiciones vigentes', n_pos_vig)
R('  (−) Mismo PK2 en varios fondos', -(n_pos_vig - n_pk2_unic))
R('= PK2 únicos', n_pk2_unic)
R('  (−) Series hermanas REGS/144A/EMTN', -(n_pk2_unic - n_obj), 'misma métrica que su hermano')
R('= INSTRUMENTOS A BUSCAR', n_obj, 'ver hoja universo_objetivo')
R('    con ISIN (BBG/RA)', n_obj_isin)
R('    sin ISIN', n_obj - n_obj_isin)
R()
R('── POSICIONES VIGENTES (detalle por fondo en hojas con/sin_ISIN) ──')
R('Posiciones con ISIN', len(df_con_isin))
R('Posiciones sin ISIN', len(df_sin_isin))
resumen_rows.append({'Métrica': '', 'Valor': '', 'Detalle': ''})
resumen_rows.append({'Métrica': '── POR FONDO ──', 'Valor': '', 'Detalle': ''})
for fid in FONDOS:
    sub = df_final[df_final['ID_Fund'] == fid]
    nombre = sub['FundShortName'].iloc[0] if len(sub) and 'FundShortName' in sub.columns and sub['FundShortName'].notna().any() else '?'
    resumen_rows.append({'Métrica': f'Fondo {fid} ({nombre})', 'Valor': len(sub), 'Detalle': f"con_ISIN={sub['ISIN'].notna().sum() if len(sub) else 0}"})
resumen_rows.append({'Métrica': '', 'Valor': '', 'Detalle': ''})
resumen_rows.append({'Métrica': '── INSTRUMENTOS A BUSCAR — POR PROVEEDOR SUGERIDO ──', 'Valor': '', 'Detalle': ''})
for sug, n in df_obj['Sugerencia_Proveedor'].value_counts().items():
    resumen_rows.append({'Métrica': sug, 'Valor': n, 'Detalle': ''})
resumen_rows.append({'Métrica': '', 'Valor': '', 'Detalle': ''})
resumen_rows.append({'Métrica': '── HEDGE_CURRENCY (fondos HEDGE_FUNDS) ──', 'Valor': '', 'Detalle': ''})
for fid in sorted(HEDGE_FUNDS):
    sub = df_final[df_final['ID_Fund'] == fid]
    n_hedge = sub['Hedge_Currency'].notna().sum() if len(sub) else 0
    orig = sub['Hedge_Origen'].value_counts().to_dict() if len(sub) else {}
    resumen_rows.append({'Métrica': f'Fondo {fid} — con Hedge_Currency', 'Valor': n_hedge,
                         'Detalle': f"total={len(sub)} | " + ', '.join(f'{k}={v}' for k, v in orig.items())})
resumen_rows.append({'Métrica': '', 'Valor': '', 'Detalle': ''})
resumen_rows.append({'Métrica': '── FAMILIA (REGS/144A/EMTN) ──', 'Valor': '', 'Detalle': ''})
resumen_rows.append({'Métrica': 'Grupos multi-serie detectados', 'Valor': len(grupos_multi), 'Detalle': ''})
resumen_rows.append({'Métrica': 'ISIN con hermano de serie', 'Valor': df_final['serie_hermanos'].astype(str).str.strip().ne('').sum(), 'Detalle': ''})

R()
R('── WARNINGS ──', len(WARNS), "detalle por PK2 en hoja 'warnings'")
df_resumen = pd.DataFrame(resumen_rows)

print(f"\n   ┌─ EMBUDO ─────────────────────────────────────────────")
print(f"   │ Posiciones RF (fondo×PK2)        {n_pos_tot:>6,}")
print(f"   │  (−) defaulteados                {-len(df_def):>6,}")
print(f"   │  (−) facturas                    {-len(df_fac):>6,}")
print(f"   │ = posiciones vigentes            {n_pos_vig:>6,}")
print(f"   │  (−) PK2 repetidos entre fondos  {-(n_pos_vig - n_pk2_unic):>6,}")
print(f"   │ = PK2 únicos                     {n_pk2_unic:>6,}")
print(f"   │  (−) series hermanas             {-(n_pk2_unic - n_obj):>6,}")
print(f"   │ = INSTRUMENTOS A BUSCAR          {n_obj:>6,}   (con ISIN {n_obj_isin:,} | sin ISIN {n_obj - n_obj_isin:,})")
print(f"   └──────────────────────────────────────────────────────")

# =============================================================================
# [9] GUARDAR EXCEL
# =============================================================================
print(f"\n[9] Guardando: {FNAME_OUT}")

C_ISIN     = '0B2447'  # azul oscuro
C_SIN_ISIN = 'B8540B'  # naranja
C_RESUMEN  = '2E4057'  # gris azulado

with pd.ExcelWriter(FNAME_OUT, engine='openpyxl') as writer:
    df_con_isin.to_excel(writer, sheet_name='con_ISIN', index=False)
    fmt_ws(writer.sheets['con_ISIN'], C_ISIN)

    df_sin_isin.to_excel(writer, sheet_name='sin_ISIN', index=False)
    fmt_ws(writer.sheets['sin_ISIN'], C_SIN_ISIN)

    if len(df_def):
        df_def.to_excel(writer, sheet_name='defaulteados', index=False)
        fmt_ws(writer.sheets['defaulteados'], '9C0006')

    if len(df_fac):
        df_fac.to_excel(writer, sheet_name='facturas', index=False)
        fmt_ws(writer.sheets['facturas'], '7F6000')

    df_obj.to_excel(writer, sheet_name='universo_objetivo', index=False)
    fmt_ws(writer.sheets['universo_objetivo'], '1F6F43')

    df_resumen.to_excel(writer, sheet_name='resumen', index=False)
    fmt_ws(writer.sheets['resumen'], C_RESUMEN, freeze='A2')

    if WARNS:
        pd.DataFrame(WARNS).to_excel(writer, sheet_name='warnings', index=False)
        fmt_ws(writer.sheets['warnings'], 'C53030', freeze='A2')

print("\n" + "=" * 70)
print("00_MAPEO_UNIVERSO completado.")
print(f"  Instrumentos a buscar : {n_obj:,}  (con ISIN {n_obj_isin:,} | sin ISIN {n_obj - n_obj_isin:,})")
print(f"  Posiciones vigentes   : {n_pos_vig:,}  (con_ISIN {len(df_con_isin):,} | sin_ISIN {len(df_sin_isin):,})")
print(f"  Excluidas             : DEF {len(df_def):,} | FAC {len(df_fac):,}")
print(f"  Warnings              : {len(WARNS)}" + (" → ver hoja 'warnings'" if WARNS else ''))
print(f"  Output   : {FNAME_OUT}")
print("=" * 70)