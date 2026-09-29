# =============================================================================
# 07_PROP_JSONL.py — Tablas de desarrollo propias desde Geneva (bond_schedule)
# =============================================================================
# CONTEXTO EN EL FLUJO
#   Tras JPM / RA / YAS / CSHF / regla DEF, queda un residual que sí exige
#   modelar la TD. Este script arma esa TD con los eventos de Geneva del
#   bond_schedule.jsonl y calcula Yield y Duration por posición (fondo × PK2).
#
# BASADO EN 05_CAPA2_VF.py (rama GENEVA). Mecánica replicada:
#   · Geneva normaliza los PerShareAmount a BASE 100  → escala = Q_real / 100
#   · Interest: se deduplica por EventDate (un mismo día puede venir 2 veces)
#   · Sink    : % del nominal ORIGINAL; el evento Mature aporta el residual
#   · Flujo   = (PerShareAmount_interes + pct_sink) × escala
#
# FLUJO INICIAL (por POSICIÓN, del CUBO)
#   FI = -TotalMVal   (simplificación pedida)
#   Un mismo PK2 en varios fondos → varias posiciones → varias TIR, porque el
#   TotalMVal puede diferir por fuente de precio.
#
# MONEDA — explícita, no inferida
#   El jsonl trae _Ccy_ en CADA evento: esa es la moneda REAL de los flujos y,
#   por lo tanto, de la yield resultante. Se reporta como Metric_Currency y se
#   compara contra Risk_Currency del CUBO (se alerta si difieren).
#   Si la moneda es indexada (CLF/UVR/UDI/...), la yield es REAL → breakeven.
#
# FLOTANTES
#   classify_bond_geneva detecta FLOTANTE (Variable Rate=True, o Accrual
#   Days/Year 252/Actual con PerShareAmount variable). Geneva SÍ trae los
#   cupones ya proyectados, así que se modela igual, PERO se marca
#   'FLOTANTE_PROYECTADO' porque la yield depende de la proyección de Geneva.
#   Si no hay cupones proyectados suficientes → va a EXCEPCIONES.
#
# OUTPUT: PROP_jsonl_{FECHA}.xlsx
#   - RESUMEN     : una fila por posición con Yield / Duration / moneda
#   - TD_<PK2>    : una pestaña por PK2 con su tabla de desarrollo
#   - sin_geneva  : residual sin record en el jsonl → auditoría / excepciones
#   - a_excepciones: detectados pero no modelables (flotantes sin proyección, etc.)
# =============================================================================

import os
import re
import sys
import json
import warnings
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

_sd = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
for p in (_sd, os.getcwd()):
    if p not in sys.path: sys.path.insert(0, p)

from pipeline_config import (
    FECHA, JSONL_PATH, CUBO_PATH, BD_FUNDS_PATH, HOMOL_PATH, OUT_UNIVERSO,
    OUT_JSONL, FONDOS_BASE_CLP, INDEXADAS, RATIO_MIN_FIJO, RATIO_MAX_FIJO,
    RISK_CCY_TO_FX, INSTRUMENTOS_BEE, BEE_CONN, ARCHIVO_PARIDS,
    MAX_DIAS_ATRAS_PAR, MAX_PESTANAS, NAVY, RED, FONT_NAME,
    pendientes, PREV_METRICAS, PREV_CSHF,
    CANDIDATOS_ESCALA, UMBRAL_PCT, UMBRAL_TC, RATIO_MIN, RATIO_MAX,
)

# ============================================================
FNAME_JSONL = JSONL_PATH
FNAME_CUBO  = CUBO_PATH
FNAME_OUT   = OUT_JSONL
# ============================================================

TIPOS_A_EXCEPCION = {'FLOTANTE', 'REVISAR_252', 'SIN_TASA', 'SIN_FLUJOS'}
MOTIVO_EXC = {
    'FLOTANTE':    'Flotante: Interest Rate no explica los cupones pagados — falta índice (CDI/TIIE/TAB)',
    'REVISAR_252': 'Brasil dc=252 (convención CDI): test ambiguo — requiere tabla de atributos',
    'SIN_TASA':    'Sin Interest Rate en el jsonl',
    'SIN_FLUJOS':  'Sin eventos de flujo en el jsonl',
}
# ============================================================

print("=" * 70)
print(f"07_PROP_JSONL | FECHA={FECHA}")
print("=" * 70)

settle_dt = pd.to_datetime(FECHA)


def limpiar_txt(s):
    return (s.fillna('').astype(str).str.strip()
            .replace({'nan': '', 'None': '', 'NaN': '', '<NA>': ''}))

def fmt_ws(ws, hexc, freeze='B2'):
    fill = PatternFill('solid', fgColor=hexc)
    font = Font(color='FFFFFF', bold=True, size=9, name='Calibri')
    aln  = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for c in ws[1]:
        c.fill = fill; c.font = font; c.alignment = aln
    if freeze: ws.freeze_panes = freeze
    ws.row_dimensions[1].height = 24
    for col in ws.columns:
        w = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 9), 40)

def xirr_calc(flujos, fechas):
    if len(flujos) < 2: return np.nan
    t0 = min(pd.to_datetime(f) for f in fechas)
    days = [(pd.to_datetime(f) - t0).days / 365.25 for f in fechas]
    def npv(r): return sum(c / (1 + r) ** d for c, d in zip(flujos, days))
    try:    return brentq(npv, -0.9999, 100.0, maxiter=200)
    except Exception: return np.nan

def duration_calc(ff, fe, s, x):
    """Macaulay y Modified desde flujos FUTUROS descontados a la propia XIRR."""
    if pd.isna(x) or not ff: return np.nan, np.nan
    s = pd.to_datetime(s)
    t = [(pd.to_datetime(f) - s).days / 365.25 for f in fe]
    pv = [cf / (1 + x) ** ti for cf, ti in zip(ff, t)]
    tot = sum(pv)
    if tot <= 0: return np.nan, np.nan
    mac = sum(ti * p for ti, p in zip(t, pv)) / tot
    return mac, mac / (1 + x)

def safe_sheet(name):
    s = re.sub(r'[\[\]\:\*\?\/\\]', '_', str(name))[:28]
    return s or 'TD'

def evaluar_escala(p, of, factor, mv, fx_val):
    """Idéntico a evaluar() de 02_LIMPIEZA_VF.
    Prueba cada (sP,sQ) y valida TRES condiciones contra MVBook:
      1. diff  : |P_ef × OF_ef × Factor / FX − MVBook| / |MVBook| <= UMBRAL_PCT
      2. sanity: TC implícito dentro de ±UMBRAL_TC del FX (salvo FX=1)
      3. ratio : OF_ef × Factor / FX / |MVBook| dentro de [RATIO_MIN, RATIO_MAX]
    Devuelve la lista completa de intentos (para auditoría)."""
    res = []
    for sP, sQ in CANDIDATOS_ESCALA:
        p_ef  = p * sP
        of_ef = of * sQ
        pq    = p_ef * of_ef * factor / fx_val
        diff  = abs(pq - mv) / abs(mv) if mv != 0 else np.nan
        tc    = (p_ef * of_ef * factor) / abs(mv) if mv != 0 else np.nan
        sanity = True if fx_val == 1.0 else (
            pd.notna(tc) and abs(tc - fx_val) / fx_val <= UMBRAL_TC)
        ratio = (of_ef * factor / fx_val) / abs(mv) if (fx_val != 0 and mv != 0) else np.nan
        ratio_ok = pd.notna(ratio) and (RATIO_MIN <= ratio <= RATIO_MAX)
        res.append({'sP': sP, 'sQ': sQ, 'p_ef': p_ef, 'of_ef': of_ef, 'pq_test': pq,
                    'diff_pct': diff, 'tc_impl': tc, 'sanity_ok': sanity,
                    'ratio': ratio, 'ratio_ok': ratio_ok,
                    'all_ok': pd.notna(diff) and (diff <= UMBRAL_PCT) and sanity and ratio_ok})
    return res


# =============================================================================
# [1] UNIVERSO
# =============================================================================
# En este pipeline cada etapa (01/02/03/04) corre sobre el universo completo;
# es 05_CONSOLIDA quien aplica la cascada y decide qué fuente gana por PK2.
# No hay un archivo de "residual" intermedio entre etapas.
print(f"\n[1] Leyendo universo de {os.path.basename(OUT_UNIVERSO)}...")
resid = pd.concat([pd.read_excel(OUT_UNIVERSO, sheet_name=s, engine='openpyxl')
                   for s in ('con_ISIN', 'sin_ISIN')], ignore_index=True)
resid['PK2']  = limpiar_txt(resid['PK2'])
resid['ISIN'] = limpiar_txt(resid['ISIN'])
if 'ID_Fund' not in resid.columns and 'Fondo' not in resid.columns:
    resid = resid.rename(columns={'FundShortName': 'Fondo'})
elif 'Fondo' not in resid.columns and 'FundShortName' in resid.columns:
    resid['Fondo'] = resid['FundShortName']
print(f"   Posiciones: {len(resid)} | PK2 únicos: {resid['PK2'].nunique()}")

# ── FALLBACK: solo lo que 01_METRICAS y 02_CSHF no resolvieron ───────────────
# Modelar una TD es caro y solo tiene sentido para lo que ningún proveedor
# cubrió. Si el output de una etapa previa no existe, no se filtra por ella
# (permite correr esta etapa suelta).
print(f"\n[1b] Filtrando lo ya resuelto por etapas previas...")
resid = pendientes(resid, [PREV_METRICAS, PREV_CSHF])
print(f"   A modelar: {len(resid)} posiciones | PK2 únicos: {resid['PK2'].nunique()}")

# =============================================================================
# [2] CARGAR bond_schedule.jsonl
# =============================================================================
print(f"\n[2] Cargando {os.path.basename(FNAME_JSONL)}...")
recs = {}
with open(FNAME_JSONL, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        recs[str(r.get('code', '')).strip()] = r
print(f"   Records: {len(recs)}")

# =============================================================================
# [3] HOMOL — PK2 → ID_Instrumento → códigos Geneva
# =============================================================================
print(f"\n[3] Mapeando PK2 → código Geneva (HOMOL)...")
h = pd.read_excel(HOMOL_PATH, engine='openpyxl')
h.columns = [str(c).strip() for c in h.columns]
h['Source'] = h['Source'].astype(str).str.strip().str.upper()
hg = h[h['Source'] == 'GENEVA'].copy()
hg['SourceInvestment'] = hg['SourceInvestment'].astype(str).str.strip()
id2codes = {}
for _, r in hg.iterrows():
    if pd.notna(r['ID_Instrumento']):
        id2codes.setdefault(int(r['ID_Instrumento']), []).append(r['SourceInvestment'])
print(f"   ID_Instrumento con código Geneva: {len(id2codes):,}")

def find_record(pk2, isin, name):
    """Cascada: HOMOL(ID_Instrumento) → ISIN → nombre."""
    try:
        iid = int(str(pk2).split('-')[0])
    except Exception:
        iid = None
    if iid is not None:
        for c in id2codes.get(iid, []):
            if c in recs: return recs[c], c, 'HOMOL'
    if isin and isin in recs: return recs[isin], isin, 'ISIN'
    nm = str(name).strip()
    if nm in recs: return recs[nm], nm, 'NOMBRE'
    return None, '', ''

# =============================================================================
# [4] FX — beemining (SQL) + paridades (Excel), SIN jerarquía fija
# =============================================================================
# Replica 02_LIMPIEZA (carga) + 03_CALCULOS (búsqueda por calce).
# El FX correcto NO se elige por prioridad: se PRUEBAN todos los candidatos y
# gana el que reproduce el MVBook dentro de UMBRAL_PCT. Así se resuelve
# solo el caso ARS (beemining≈1382 vs paridades≈1471: gana el que calza).
# =============================================================================
print(f"\n[4] Cargando FX (beemining + paridades)...")

fx_bee = {'USDUSD': 1.0}
fx_par = {'USDUSD': 1.0}

# ── beemining (SQL) ─────────────────────────────────────────────────────────
INSTRUMENTOS_BEE = [
    "USDMXN Curncy", "USDBRL Curncy", "CLFXDOOB_sindesf", "USDCLF Curncy",
    "USDUVR Curncy", "USDPEN Curncy", "USDPYG Curncy", "USDUYU Curncy",
    "USDCOP Curncy", "USDARS Curncy", "USDGBP Curncy", "USDEUR Curncy",
    "USDUDI Curncy", "USDDOP Curncy",
]
def _to_nom(c):
    return "USDCLP" if c == "CLFXDOOB_sindesf" else c.replace(" Curncy", "")

try:
    import pyodbc
    import pandas.io.sql as psql
    conn = pyodbc.connect('Driver={SQL Server};SERVER=SANWS007;DATABASE=DW_MONEDA;'
                          'UID=Consulta_DW;PWD=Consulta2023;')
    ph  = ",".join(f"'{c}'" for c in INSTRUMENTOS_BEE)
    sql = (f"SELECT instrumentcode,instrumentvalue,daydate FROM ("
           f"SELECT instrumentcode,instrumentvalue,daydate,"
           f"ROW_NUMBER() OVER (PARTITION BY instrumentcode ORDER BY daydate DESC) AS rn "
           f"FROM [DW_MONEDA].[dbo].[TBL_RENTABILIDADES_DW] "
           f"WHERE daydate<='{FECHA}' AND instrumentcode IN ({ph})) t WHERE rn=1")
    _bee = psql.read_sql(sql, conn)
    for _, r in _bee.iterrows():
        v = float(r['instrumentvalue'])
        if v > 0: fx_bee[_to_nom(str(r['instrumentcode']))] = v
    print(f"   beemining: {len(fx_bee)-1} pares")
except Exception as e:
    print(f"   [WARN] beemining no disponible: {e}")

# ── paridades (Excel) — misma lectura que 02_LIMPIEZA ───────────────────────
SHEETS_P = ["Data Paridad NY", "Data Paridad LDN", "Data EUR|USD OBS"]
_par_frames = {}
for hoja in SHEETS_P:
    try:
        dp = pd.read_excel(ARCHIVO_PARIDS, sheet_name=hoja, engine='openpyxl')
        raw = dp['Date']
        if pd.api.types.is_numeric_dtype(raw):
            from datetime import date as _d
            dp['Date'] = pd.to_datetime(raw.apply(
                lambda x: _d.fromordinal(int(x) + 693594) if pd.notna(x) and x > 0 else None),
                errors='coerce')
        else:
            p = pd.to_datetime(raw, errors='coerce', dayfirst=False)
            if p.isna().sum() > len(p) * 0.5:
                p = pd.to_datetime(raw, errors='coerce', dayfirst=True)
            dp['Date'] = p
        dp = dp[dp['Date'].notna() & (dp['Date'].dt.year >= 2000)]
        dp = dp[(dp['Date'] <= settle_dt) &
                (dp['Date'] >= settle_dt - pd.Timedelta(days=MAX_DIAS_ATRAS_PAR))].copy()
        dp = dp.rename(columns={'Unnamed: 5': 'Nomenclatura'})
        dp['Source'] = hoja
        _par_frames[hoja] = dp
    except Exception as e:
        print(f"   [WARN] paridades '{hoja}': {e}")

df_par = (pd.concat(_par_frames.values(), ignore_index=True)
          if _par_frames else pd.DataFrame(columns=['Date', 'Price', 'Nomenclatura', 'Source']))
if not df_par.empty:
    df_par = (df_par.sort_values('Date', ascending=False)
              .drop_duplicates(subset=['Nomenclatura', 'Source']).reset_index(drop=True))

def _g(n, s, inv=False):
    f = df_par[(df_par['Nomenclatura'] == n) & (df_par['Source'] == s)]
    if not f.empty:
        v = float(f['Price'].iloc[0])
        return (1 / v) if (inv and v) else v
    return None

def get_par(nom):
    if df_par.empty: return None
    if nom in ('USDCLP', 'EURCLP'): return _g(nom, 'Data EUR|USD OBS')
    if nom == 'USDCLF':
        return _g('USDCLF', 'Data Paridad NY') or _g('CLFUSD', 'Data Paridad NY', inv=True)
    if nom == 'USDUVR':
        return _g('USDUVR', 'Data Paridad NY') or _g('UVR CurncyUSD', 'Data Paridad NY', inv=True)
    if nom == 'USDEUR': return _g('EURUSD', 'Data Paridad NY', inv=True)
    if nom == 'USDGBP': return _g('GBPUSD', 'Data Paridad NY', inv=True)
    return _g(nom, 'Data Paridad NY')

if not df_par.empty:
    # todos los pares tal cual vienen (incluye 'USDARS MAE', 'USDCOP TRM', etc.)
    for _, r in df_par.iterrows():
        nom = str(r.get('Nomenclatura', '')).strip()
        try: v = float(r['Price'])
        except Exception: continue
        if nom and v > 0: fx_par.setdefault(nom, v)
    for nom in ['USDCLP', 'USDCLF', 'USDUVR', 'USDUDI', 'USDEUR', 'USDGBP', 'USDDOP']:
        v = get_par(nom)
        if v and v > 0: fx_par[nom] = v
print(f"   paridades: {len(fx_par)-1} pares")

# ── Derivados (idéntico a 03_CALCULOS) ──────────────────────────────────────
for fx_d in (fx_bee, fx_par):
    if fx_d.get('USDCLF', 0) > 0 and 'USDCLP' in fx_d:
        fx_d['CLFCLP'] = fx_d['USDCLP'] / fx_d['USDCLF']
    if fx_d.get('CLFUSD', 0) > 0 and 'USDCLF' not in fx_d:
        fx_d['USDCLF'] = 1 / fx_d['CLFUSD']
    if fx_d.get('EURUSD', 0) > 0 and 'USDEUR' not in fx_d:
        fx_d['USDEUR'] = 1 / fx_d['EURUSD']
    if fx_d.get('GBPUSD', 0) > 0 and 'USDGBP' not in fx_d:
        fx_d['USDGBP'] = 1 / fx_d['GBPUSD']
    if fx_d.get('UVR CurncyUSD', 0) > 0 and 'USDUVR' not in fx_d:
        fx_d['USDUVR'] = 1 / fx_d['UVR CurncyUSD']

RISK_CCY_TO_FX = {'CLF': 'USDCLF', 'UVR COSTER': 'USDUVR', 'UDI': 'USDUDI',
                  'UI CURNCY': 'USDUYU'}

def fx_candidatos(ccy):
    """Todos los candidatos bee+par para una moneda, sin prioridad.
    startswith permite capturar variantes ('USDARS' y 'USDARS MAE')."""
    c = str(ccy).strip().upper()
    if c == 'USD': return [('USDUSD', 1.0)]
    base = RISK_CCY_TO_FX.get(c, f'USD{c}')
    out, seen = [], set()
    for lbl, d in (('bee', fx_bee), ('par', fx_par)):
        for k, v in d.items():
            if k.startswith(base) and pd.notna(v) and v > 0:
                rv = round(v, 6)
                if rv not in seen:
                    out.append((f'{lbl}_{k}', v)); seen.add(rv)
    return out

print(f"   FX bee={len(fx_bee)} par={len(fx_par)}")

# =============================================================================
# [5] CUBO — TotalMVal / Qty / Factor por (ID_Fund, PK2)
# =============================================================================
print(f"\n[4] Leyendo CUBO para TotalMVal por posición...")
cubo = pd.read_excel(FNAME_CUBO, engine='openpyxl')
cubo['PK2'] = limpiar_txt(cubo['PK2'])
cc = [c for c in ['ID_Fund', 'PK2', 'Qty', 'OriginalFace', 'Factor', 'AI',
                  'LocalPrice', 'MVBook', 'TotalMVal'] if c in cubo.columns]
cubo_slim = cubo[cc].drop_duplicates(['ID_Fund', 'PK2'])

# ID_Fund desde el nombre de fondo (el residual trae 'Fondo')
try:
    from pipeline_config import BD_FUNDS_PATH
    bdf = pd.read_excel(BD_FUNDS_PATH, engine='openpyxl')
    cs = next((c for c in bdf.columns if 'shortname' in c.lower().replace('_', '')), None)
    name2id = dict(zip(bdf[cs].astype(str).str.strip(), bdf['ID_Fund']))
    resid['ID_Fund'] = resid['Fondo'].astype(str).str.strip().map(name2id)
except Exception as e:
    print(f"   [WARN] BD_FUNDS: {e}")
    resid['ID_Fund'] = np.nan

pos = resid.copy()
# El universo YA arrastra las columnas de valor del CUBO (LocalPrice…TotalMVal).
# Si se mergea sin quitarlas, pandas las renombra a _x/_y y desaparece
# 'TotalMVal'. Se eliminan de resid para que el CUBO sea la fuente autoritativa.
_dup = [c for c in cubo_slim.columns if c not in ('ID_Fund', 'PK2') and c in pos.columns]
if _dup:
    print(f"   (columnas tomadas del CUBO, no del universo: {_dup})")
    pos = pos.drop(columns=_dup)

pos = pos.merge(cubo_slim, on=['ID_Fund', 'PK2'], how='left')
assert len(pos) == len(resid), "merge multiplicó filas — revisar CUBO"
print(f"   Posiciones: {len(pos)} | sin TotalMVal: {pos['TotalMVal'].isna().sum()}")

# =============================================================================
# [5] CLASIFICAR + CONSTRUIR TD (rama GENEVA de Capa 2)
# =============================================================================
print(f"\n[5] Construyendo TD desde eventos Geneva...")

def clasificar(rec):
    """Clasificación validada empíricamente. Tres reglas, sin umbrales inventados:

    1. ZERO_COUPON — no hay ningún evento Interest con monto != 0.
       Determinista (no es un umbral). Se modela con un solo flujo a maturity,
       igual que la rama ZERO_COUPON de build_prop_td en Capa 2.

    2. TASA FIJA — 'Interest Rate' REPRODUCE los cupones realmente pagados:
           ratio = cupon_observado / (Interest Rate x dias/365)
       cae en [RATIO_MIN_FIJO, RATIO_MAX_FIJO] sobre cupones PASADOS.
       El test es "¿el campo de tasa del sistema explica la realidad?".
       Se mide sobre cupones PASADOS porque Geneva proyecta los FUTUROS
       congelando el índice: un flotante muestra cupones futuros idénticos
       (varianza 0) y parecería fijo si se midiera sobre ellos.
       Funciona con 1-2 observaciones (a diferencia de un test de varianza).

    3. FLOTANTE — ratio fuera de rango: el cupón pagado no se explica con
       'Interest Rate', luego hay un índice detrás (CDI/TIIE/TAB...).

    Regla adicional: los papeles brasileños con dc=252 (convención de días
    hábiles CDI) van SIEMPRE a revisión manual. En ellos el test es ambiguo:
    SOLFACIL 16.48 (fijo) y SOLFACIL 5.70 (CDI flotante) dan ratios casi
    idénticos (0.916 vs 0.941) y no son separables por esta vía.

    Devuelve (tipo, tasa_a_usar, diag) — diag lleva la traza para auditoría.
    """
    bs = rec.get('bond_specific') or {}
    ev = rec.get('events') or []
    rate = bs.get('Interest Rate')
    dc   = str(bs.get('Accrual Days/Year', '')).strip()
    sinks = [e for e in ev if e.get('Type') == 'Sink']

    ints = sorted({pd.to_datetime(e['EventDate']): float(e['PerShareAmount'])
                   for e in ev if e.get('Type') == 'Interest' and 'PerShareAmount' in e}.items())
    nz = [v for _, v in ints if abs(v) > 1e-9]

    diag = {'n_int': len(ints), 'n_int_nz': len(nz), 'dc': dc,
            'Interest_Rate': rate, 'ratio_obs_teo': np.nan, 'n_pasados': 0}

    # ── 1. ZERO_COUPON ───────────────────────────────────────────────────────
    if not nz:
        if any(e.get('Type') == 'Mature' for e in ev) or sinks:
            return 'ZERO_COUPON', 0.0, diag
        return 'SIN_FLUJOS', np.nan, diag

    # ── Regla dc=252: ambiguo por convención CDI → revisión manual ───────────
    if dc == '252':
        return 'REVISAR_252', np.nan, diag

    # ── 2/3. ¿Interest Rate explica los cupones pasados? ─────────────────────
    pasados = [x for x in ints if x[0] <= settle_dt]
    diag['n_pasados'] = len(pasados)
    if len(pasados) >= 2 and rate:
        ratios = []
        for i in range(1, len(pasados)):
            d0, _ = pasados[i - 1]
            d1, v = pasados[i]
            dd = (d1 - d0).days
            if dd > 0:
                teo = rate * (dd / 365.0)
                if teo > 0:
                    ratios.append(v / teo)
        if ratios:
            diag['ratio_obs_teo'] = float(np.median(ratios))
            if not (RATIO_MIN_FIJO <= diag['ratio_obs_teo'] <= RATIO_MAX_FIJO):
                return 'FLOTANTE', np.nan, diag
    elif not rate:
        return 'SIN_TASA', np.nan, diag

    # Tasa fija validada (o sin historial suficiente para desmentirla)
    if sinks:
        return 'SINKABLE', float(rate), diag
    return 'BULLET', float(rate), diag

def build_sink_schedule(events):
    """Devuelve (lista (fecha,pct), fecha_mature, pct_residual)."""
    sinks = []
    for e in events:
        if e.get('Type') == 'Sink':
            pct = e.get('Percent', e.get('PerShareAmount'))
            if pct is None: continue
            sinks.append((pd.to_datetime(e['EventDate']), float(pct)))
    mature = None
    for e in events:
        if e.get('Type') == 'Mature':
            mature = pd.to_datetime(e['EventDate'])
    total = sum(p for _, p in sinks)
    residual = max(100.0 - total, 0.0)
    return sinks, mature, residual

def inferir_periodicidad(ints):
    """Meses entre cupones, inferido de las FECHAS reales.
    'Coupon Frequency' del jsonl viene = 2 en los 242 records (valor por
    defecto inservible): 54 de ellos son en realidad mensuales, trimestrales
    o anuales. La mediana de días entre eventos Interest sí es confiable."""
    if len(ints) < 3:
        return None, None
    gaps = np.diff([d.value for d, _ in ints]) / 8.64e13
    med = float(np.median(gaps))
    if med <= 0:
        return None, None
    freq = int(round(365.25 / med))
    freq = min([1, 2, 4, 12], key=lambda f: abs(f - freq))
    return max(1, 12 // freq), freq


def build_td_geneva(rec, of_ef, tipo, tasa):
    """Construye la TD combinando lo que Geneva SÍ trae con la proyección
    del tramo faltante (rama build_prop_td de Capa 2).

    ESCALA — punto crítico:
      Los montos base-100 de Geneva son % del nominal ORIGINAL. Los eventos
      futuros ya suman Factor×100 (lo amortizado quedó en eventos pasados),
      es decir el Factor YA ESTÁ INCORPORADO en los flujos.
      Por eso se escala con el nominal ORIGINAL (OF_ef = OriginalFace × sQ)
      y NO con el vigente (Q_real = OF_ef × Factor): hacerlo con Q_real
      aplicaría el Factor dos veces.
      Verificado: Σ Capital_base100 × OF_ef/100 == Q_real exactamente
      (PATIO PERU 95.00 y Factor .95; CASH URUGUA 31.52 y Factor .3152).
      sP y sQ (escalas de unidad del papel) no intervienen acá: vienen ya
      aplicadas en OF_ef.

    Geneva materializa los cupones solo hasta ~8 meses adelante; los papeles
    largos quedan con capital sin cupones si se usa el jsonl tal cual (caso
    FUNOMM: 1 cupón + principal a 5.5 años). Aquí se respetan los cupones que
    Geneva sí entrega y se PROYECTAN los faltantes hasta el vencimiento con la
    tasa validada, aplicada sobre el SALDO INSOLUTO.
    """
    ev = rec.get('events') or []
    bs = rec.get('bond_specific') or {}
    escala = of_ef / 100.0          # nominal ORIGINAL (el Factor ya va en los flujos)
    ccys = {str(e['_Ccy_']).strip().upper() for e in ev if e.get('_Ccy_')}
    ccy = sorted(ccys)[0] if ccys else ''

    sinks, mature, residual = build_sink_schedule(ev)
    mat = pd.to_datetime(bs.get('Maturity Date'), errors='coerce')
    if pd.isna(mat):
        mat = mature
    if pd.isna(mat):
        return pd.DataFrame(), ccy, {}

    # Amortizaciones futuras por fecha (Percent = amortización del período;
    # el saldo insoluto es 100 - acumulado, verificado contra la tabla del analista)
    sink_fut = {}
    sink_pasado = 0.0
    for fdate, pct in sinks:
        if fdate <= settle_dt:
            sink_pasado += pct
        else:
            sink_fut[fdate.strftime('%Y-%m-%d')] = \
                sink_fut.get(fdate.strftime('%Y-%m-%d'), 0.0) + pct
    cap_vigente = max(100.0 - sink_pasado, 0.0)

    # ── ZERO_COUPON: un único flujo al vencimiento ──────────────────────────
    if tipo == 'ZERO_COUPON':
        df = pd.DataFrame([{'Fecha': mat, 'Cupon_base100': 0.0,
                            'Capital_base100': cap_vigente,
                            'Cupon': 0.0, 'Capital': cap_vigente * escala,
                            'Flujo': cap_vigente * escala, 'Origen': 'PROYECTADO'}])
        return df, ccy, {'cupones_geneva': 0, 'cupones_proyectados': 0}

    # Cupones que Geneva SÍ entrega (futuros)
    ints_all = sorted({pd.to_datetime(e['EventDate']): float(e['PerShareAmount'])
                       for e in ev if e.get('Type') == 'Interest' and 'PerShareAmount' in e}.items())
    cpn_geneva = {d.strftime('%Y-%m-%d'): v for d, v in ints_all if d > settle_dt}

    meses, _ = inferir_periodicidad(ints_all)
    if meses is None:
        cf = bs.get('Coupon Frequency') or 2
        meses = max(1, int(12 // cf))

    # Calendario de cupones desde maturity hacia atrás (Capa 2)
    cpn_dates = []
    dt = mat
    while dt > settle_dt:
        cpn_dates.append(dt)
        dt = dt - pd.DateOffset(months=meses)
    cpn_dates = sorted(cpn_dates)

    # Unir con fechas de sink para no perder amortizaciones
    todas = sorted(set([d.strftime('%Y-%m-%d') for d in cpn_dates]) | set(sink_fut.keys()))
    if not todas:
        return pd.DataFrame(), ccy, {}

    # ── Ancla del devengo: ÚLTIMO CUPÓN PAGADO, no el settle ────────────────
    # Al comprar se paga precio limpio + AI (el devengado que corresponde al
    # vendedor); a cambio se recibe el cupón COMPLETO en la próxima fecha.
    # Si se contara la fracción desde el settle, el devengado se descontaría
    # dos veces (se paga vía AI y además se recorta el primer cupón).
    # Caso RECARR: último cupón 31-ene, próximo 1-ago → 181 de 182 días
    # devengados; el primer cupón debe ser 4.73%, no 0.26%.
    pagados = [d for d, _ in ints_all if d <= settle_dt]
    prev = max(pagados) if pagados else settle_dt

    rows = []
    cap_rem = cap_vigente
    n_gen = n_proy = 0
    for k in todas:
        f = pd.to_datetime(k)
        # ── CUPÓN SOBRE EL SALDO VIGENTE ─────────────────────────────────────
        # El PerShareAmount de Geneva viene calculado sobre el nominal ORIGINAL
        # e ignora la amortización acumulada: en CASH URUGUA daba 965.990 cuando
        # el correcto es 304.480 (ratio 3.17 = 1/0.3152 = 1/Factor).
        # Por eso el cupón se recalcula siempre que haya tasa validada:
        #     cupón = tasa × (saldo_vigente/100) × (días del período/365)
        # El saldo usado es el del INICIO del período (antes de la amortización
        # de esa misma fecha), que es sobre el que se devenga el interés.
        frac = (f - prev).days / 365.0
        if pd.notna(tasa) and tasa > 0 and frac > 0:
            cpn = tasa * (cap_rem / 100.0) * frac
            origen = 'GENEVA' if k in cpn_geneva else 'PROYECTADO'
            if k in cpn_geneva: n_gen += 1
            else:               n_proy += 1
        elif k in cpn_geneva:          # sin tasa: se usa el dato de Geneva
            cpn = cpn_geneva[k]; n_gen += 1; origen = 'GENEVA_RAW'
        else:
            cpn = 0.0; origen = 'SINK'

        cpn_ref = cpn_geneva.get(k, np.nan)   # referencia cruda de Geneva
        pri = sink_fut.get(k, 0.0)
        if f == mat:                             # último pago: capital residual
            pri = cap_rem
        rows.append({'Fecha': f, 'Cupon_base100': cpn, 'Capital_base100': pri,
                     'Saldo_ini_base100': cap_rem, 'dias_periodo': (f - prev).days,
                     'Cupon': cpn * escala, 'Capital': pri * escala,
                     'Flujo': (cpn + pri) * escala, 'Origen': origen,
                     'Cupon_Geneva_raw': cpn_ref})
        cap_rem = max(cap_rem - pri, 0.0)
        prev = f

    return (pd.DataFrame(rows), ccy,
            {'cupones_geneva': n_gen, 'cupones_proyectados': n_proy,
             'meses_cupon': meses, 'cap_vigente_ini': cap_vigente,
             'ultimo_cupon_pagado': (max(pagados) if pagados else None)})

res_rows, td_store, sin_geneva, a_exc = [], {}, [], []

for _, r in pos.iterrows():
    pk2, isin = r['PK2'], r['ISIN']
    nom   = r.get('Name_Instrumento', r.get('Instrumento', ''))
    fondo = r.get('Fondo', r.get('FundShortName', ''))
    fid   = r.get('ID_Fund', np.nan)
    rc    = str(r.get('Risk_Currency', r.get('Moneda', ''))).strip().upper()
    tmv = pd.to_numeric(r.get('TotalMVal'), errors='coerce')

    rec, code, via = find_record(pk2, isin, nom)
    if rec is None:
        sin_geneva.append({'Fondo': fondo, 'PK2': pk2, 'ISIN': isin, 'Instrumento': nom,
                           'Moneda': rc,
                           'Tiene_codigo_HOMOL': bool(id2codes.get(
                               int(str(pk2).split('-')[0]) if str(pk2).split('-')[0].isdigit() else -1, [])),
                           'Motivo': 'Sin record en bond_schedule.jsonl'})
        continue

    tipo, tasa, diag = clasificar(rec)

    # Tipos no modelables con el jsonl → excepciones (tabla de atributos)
    if tipo in TIPOS_A_EXCEPCION:
        a_exc.append({'Fondo': fondo, 'PK2': pk2, 'ISIN': isin, 'Instrumento': nom,
                      'Moneda': rc, 'Tipo': tipo,
                      'Interest_Rate': diag.get('Interest_Rate'),
                      'ratio_obs_teo': diag.get('ratio_obs_teo'),
                      'day_count': diag.get('dc'),
                      'n_cupones_pasados': diag.get('n_pasados'),
                      'Motivo': MOTIVO_EXC.get(tipo, tipo)})
        continue
    # ── ESCALA + FX (idéntico a 02_LIMPIEZA → 03_CALCULOS) ───────────────────
    # LocalPrice y OriginalFace del CUBO vienen en unidades variables. Se busca
    # el par (sP,sQ) y el FX que juntos reproducen MVBook (moneda del fondo).
    #   P_ef  = LocalPrice × sP        (precio decimal del sistema)
    #   Q_real= OriginalFace × sQ      (nominales reales)
    #   check : P_ef × Q_real × Factor / FX ≈ MVBook
    # Luego el flujo inicial se arma EN MONEDA LOCAL: fi = -(P_ef × Q_real)
    lp  = pd.to_numeric(r.get('LocalPrice'), errors='coerce')
    of  = pd.to_numeric(r.get('OriginalFace'), errors='coerce')
    of  = of if pd.notna(of) else 0.0
    fac = pd.to_numeric(r.get('Factor'), errors='coerce')
    fac = fac if pd.notna(fac) else 1.0
    mvb = pd.to_numeric(r.get('MVBook'), errors='coerce')
    ai  = pd.to_numeric(r.get('AI'), errors='coerce')
    ai  = ai if pd.notna(ai) else 0.0

    if pd.isna(lp) or pd.isna(mvb) or mvb == 0 or of == 0:
        a_exc.append({'Fondo': fondo, 'PK2': pk2, 'ISIN': isin, 'Instrumento': nom,
                      'Moneda': rc, 'Tipo': tipo,
                      'Motivo': 'Sin LocalPrice / OriginalFace / MVBook en CUBO'})
        continue

    # ── MONEDA DEL FONDO ─────────────────────────────────────────────────────
    # MVBook y AI vienen EN LA MONEDA DEL FONDO, no del papel: los fondos de
    # FONDOS_BASE_CLP contabilizan en CLP, el resto en USD.
    # En vez de convertir MVBook con UN USDCLP fijo, se generan CANDIDATOS de
    # "unidades del papel por unidad de moneda del fondo" y se deja que el
    # calce elija — misma filosofía que el FX del papel (buscar, no asumir).
    # Motivo: el USDCLP contable del fondo puede diferir del de mercado
    # (caso real: 930.86 contable vs 924.78 de beemining = 0.66% de desvío,
    # suficiente para romper el umbral de 0.5% y caer a FALLBACK).
    base_clp = (int(fid) in FONDOS_BASE_CLP) if pd.notna(fid) else False

    def candidatos_fondo():
        """(label, fx) con fx = unidades del papel por unidad de moneda del fondo."""
        base = fx_candidatos(rc)
        if not base_clp:
            return base
        out, vistos = [], set()
        usdclps = ([('bee_USDCLP', fx_bee['USDCLP'])] if 'USDCLP' in fx_bee else []) + \
                  ([('par_USDCLP', fx_par['USDCLP'])] if 'USDCLP' in fx_par else [])
        for lbl_u, u in usdclps:
            if not u or u <= 0: continue
            for lbl, v in base:
                fx = v / u              # papel→USD ÷ (CLP por USD) = papel por CLP
                if fx > 0 and round(fx, 10) not in vistos:
                    out.append((f'{lbl}/{lbl_u}', fx)); vistos.add(round(fx, 10))
        return out or base

    cands_fx = candidatos_fondo()

    fx_usado, fx_fuente = np.nan, 'SIN_FX'
    sP, sQ, esc_flag, esc_diff, esc_ratio = np.nan, np.nan, 'SIN_SCALAR', np.nan, np.nan
    intentos_log = ''

    for lbl, v in cands_fx:
        if pd.isna(v) or v <= 0: continue
        intentos = evaluar_escala(lp, of, fac, mvb, v)
        ok = next((i for i in intentos if i['all_ok']), None)
        if ok:
            fx_usado, fx_fuente = v, lbl
            sP, sQ, esc_flag = ok['sP'], ok['sQ'], 'OK'
            esc_diff, esc_ratio = ok['diff_pct'], ok['ratio']
            intentos_log = ' | '.join(
                f"({i['sP']},{i['sQ']}):diff={i['diff_pct']:.1%}" for i in intentos)
            break

    if esc_flag != 'OK':
        # Fallback: mejor combinación por diff, aunque no pase los 3 checks
        # Se prefiere SIEMPRE un candidato que pase el check de ratio: escalares
        # que dan el MISMO P×Q (p.ej. (0.001,1000) y (1,1)) empatan en diff y
        # ganaría el primero de la lista, inflando Q_real 1000x y con él los
        # flujos. El ratio los distingue (996.76 vs 0.997).
        mejor = None
        for lbl, v in cands_fx:
            if pd.isna(v) or v <= 0: continue
            for i in evaluar_escala(lp, of, fac, mvb, v):
                if pd.isna(i['diff_pct']): continue
                clave = (not i['ratio_ok'], i['diff_pct'])
                if mejor is None or clave < mejor[0]:
                    mejor = (clave, i, lbl, v)
        if mejor:
            _, i, lbl, v = mejor
            fx_usado, fx_fuente = v, f'FALLBACK_{lbl}'
            sP, sQ, esc_flag = i['sP'], i['sQ'], 'FALLBACK'
            esc_diff, esc_ratio = i['diff_pct'], i['ratio']
        else:
            a_exc.append({'Fondo': fondo, 'PK2': pk2, 'ISIN': isin, 'Instrumento': nom,
                          'Moneda': rc, 'Tipo': tipo, 'Motivo': f'Sin FX/escalar para {rc}'})
            continue

    p_ef   = lp * sP
    of_ef  = of * sQ            # nominal ORIGINAL en unidades correctas
    q_real = of_ef * fac        # nominal VIGENTE (= OriginalFace × sQ × Factor)
    # AI viene en moneda del FONDO; fx_usado son unidades del papel por unidad
    # de moneda del fondo, así que la conversión es directa.
    ai_local = ai * fx_usado if pd.notna(fx_usado) else 0.0

    # ── Sanidad del AI ───────────────────────────────────────────────────────
    # El AI debe aproximar tasa × (días devengados/365) × saldo vigente.
    # Se detectan casos donde el CUBO trae un AI incoherente (p.ej. TOWER ONE:
    # AI 14x el teórico — es un Loan y el campo parece incluir otra cosa).
    ai_teorico, ai_ratio = np.nan, np.nan

    # ── TD de Geneva escalada con el Q_real correcto ─────────────────────────
    td, ccy_ev, dtd = build_td_geneva(rec, of_ef, tipo, tasa)
    if td.empty:
        a_exc.append({'Fondo': fondo, 'PK2': pk2, 'ISIN': isin, 'Instrumento': nom,
                      'Moneda': rc, 'Tipo': tipo, 'Motivo': 'Sin flujos futuros ni vencimiento'})
        continue

    flujos_fut = td['Flujo'].tolist()
    fechas_fut = td['Fecha'].tolist()

    # AI teórico: devengado desde el último cupón pagado hasta el settle
    ult_pagado = dtd.get('ultimo_cupon_pagado')
    if pd.notna(tasa) and tasa and ult_pagado is not None:
        dias_dev = (settle_dt - pd.to_datetime(ult_pagado)).days
        if dias_dev >= 0:
            ai_teorico = tasa * (dias_dev / 365.0) * (dtd.get('cap_vigente_ini', 100.0) / 100.0) * of_ef / 100.0
            ai_ratio = (ai_local / ai_teorico) if ai_teorico else np.nan

    # ── Flujo inicial EN MONEDA LOCAL (misma unidad que la TD de Geneva) ─────
    # Desembolso real = precio limpio × nominales + interés devengado.
    # MVBook es LIMPIO (no incluye AI) — por eso el calce de escalares se hace
    # contra MVBook sin AI, pero la inversión inicial sí lo incluye.
    # (MVTotal sí incluye AI; no se usa aquí para no mezclar unidades.)
    fi_limpio = -(p_ef * q_real)
    fi_local  = fi_limpio - ai_local
    xef = xirr_calc([fi_local] + flujos_fut, [settle_dt] + fechas_fut)
    mac, mod = duration_calc(flujos_fut, fechas_fut, settle_dt, xef)

    # ── MONEDA ───────────────────────────────────────────────────────────────
    # MANDA LA DEL CUBO (Risk_Currency). El CUBO también proviene de Geneva,
    # pero toma el campo de moneda de DENOMINACIÓN del papel, que es el que
    # define la unidad en que queda expresada la TIR.
    # El _Ccy_ del evento del jsonl es la moneda de LIQUIDACIÓN/pago y NO debe
    # usarse para interpretar la TIR (ej. COSTER es UVR de sistema aunque el
    # evento diga USD; CONMEX es UDI aunque diga MXN; CRC III es CLF aunque
    # diga CLP). Se conserva solo como referencia informativa.
    metric_ccy = rc                       # ← Risk_Currency del CUBO
    es_idx     = metric_ccy in INDEXADAS
    dif_ccy_informativa = bool(ccy_ev and rc and ccy_ev != rc)

    res_rows.append({
        'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
        'Instrumento': nom, 'Emisor': r.get('CompanyName', r.get('Emisor', '')),
        'Tipo_bono': tipo, 'Tasa_usada': tasa,
        'Interest_Rate': diag.get('Interest_Rate'),
        'ratio_obs_teo': diag.get('ratio_obs_teo'), 'day_count': diag.get('dc'),
        'n_cupones_pasados': diag.get('n_pasados'),
        'cupones_Geneva': dtd.get('cupones_geneva'),
        'cupones_proyectados': dtd.get('cupones_proyectados'),
        'meses_cupon': dtd.get('meses_cupon'),
        'Fuente': 'GENEVA', 'Code_Geneva': code, 'Match_via': via,
        'Moneda': rc, 'Ccy_evento_jsonl': ccy_ev,
        'Metric_Currency': metric_ccy, 'Dif_ccy_informativa': dif_ccy_informativa,
        'Es_Indexado': es_idx, 'Requiere_Breakeven': es_idx,
        'TotalMVal': tmv, 'MVBook': mvb,
        'Base_fondo': 'CLP' if base_clp else 'USD', 'LocalPrice': lp, 'OriginalFace': of, 'Factor': fac,
        'sP': sP, 'sQ': sQ, 'Escalar_flag': esc_flag, 'Escalar_diff': esc_diff,
        'Escalar_ratio': esc_ratio,
        'P_ef_pct': p_ef * 100, 'Q_real': q_real,
        'AI_cubo': ai, 'AI_local': ai_local,
        'AI_teorico': ai_teorico, 'AI_ratio': ai_ratio,
        'AI_sospechoso': bool(pd.notna(ai_ratio) and (ai_ratio > 3 or (ai_ratio < 0.33 and ai_local > 0))),
        'ultimo_cupon_pagado': dtd.get('ultimo_cupon_pagado'),
        'FI_limpio': fi_limpio, 'FI_local': fi_local,
        'FX_usado': fx_usado, 'FX_fuente': fx_fuente,
        'N_flujos': len(td),
        'Primer_flujo': td['Fecha'].min(), 'Ultimo_flujo': td['Fecha'].max(),
        'Yield_efectiva': xef, 'MacDur': mac, 'ModDur': mod,
    })
    td_store.setdefault(pk2, td.assign(PK2=pk2, Instrumento=nom, Moneda=metric_ccy,
                                       Es_Indexado=es_idx))

df_res = pd.DataFrame(res_rows)
df_sin = pd.DataFrame(sin_geneva)
df_exc = pd.DataFrame(a_exc)

ok = int(df_res['Yield_efectiva'].notna().sum()) if len(df_res) else 0
print(f"   Posiciones modeladas       : {len(df_res)}")
if len(df_res):
    n_fxok = int((df_res['Escalar_flag'] == 'OK').sum())
    print(f"   Escalar+FX calzado c/MVBook  : {n_fxok}/{len(df_res)}"
          f"  (fallback: {len(df_res)-n_fxok})")
    for k, v in df_res['FX_fuente'].value_counts().items():
        print(f"      {k:<22}: {v}")
print(f"   → con Yield calculada      : {ok}")
print(f"   Sin record en jsonl        : {len(df_sin)}  ({df_sin['PK2'].nunique() if len(df_sin) else 0} PK2)")
print(f"   A excepciones              : {len(df_exc)}")
if len(df_res):
    print(f"   Tipos: {dict(df_res['Tipo_bono'].value_counts())}")
    if df_res['Dif_ccy_informativa'].any():
        print(f"   [INFO] {int(df_res['Dif_ccy_informativa'].sum())} con _Ccy_ del evento ≠ moneda del CUBO "
              f"(se usa la del CUBO, que es la de denominación)")

# =============================================================================
# [6] GUARDAR
# =============================================================================
print(f"\n[6] Guardando {os.path.basename(FNAME_OUT)}...")

rr = []
def add(m, v='', c=''): rr.append({'Concepto': m, 'Valor': v, 'Comentario': c})
add('── ENTRADA ──')
add('Posiciones residuales', len(pos))
add('PK2 únicos', pos['PK2'].nunique())
add('')
add('── MODELADO CON GENEVA ──')
add('Posiciones modeladas', len(df_res), f"{df_res['PK2'].nunique() if len(df_res) else 0} PK2")
add('  con Yield calculada', ok)
if len(df_res):
    for k, v in df_res['Tipo_bono'].value_counts().items():
        add(f'    {k}', int(v))
add('')
add('── NO MODELADOS ──')
add('Sin record en jsonl', len(df_sin), f"{df_sin['PK2'].nunique() if len(df_sin) else 0} PK2 · auditoría Geneva")
if len(df_sin):
    add('  con código HOMOL pero ausente del jsonl',
        int(df_sin['Tiene_codigo_HOMOL'].sum()), 'accionable: pedir jsonl más completo')
    add('  sin código Geneva en HOMOL',
        int((~df_sin['Tiene_codigo_HOMOL']).sum()), 'loans/estructuras → excepciones')
add('A excepciones', len(df_exc))
add('')
add('── FX (conversión a moneda local) ──')
if len(df_res):
    add('Escalar+FX calzado contra MVBook', int((df_res['Escalar_flag'] == 'OK').sum()),
        f"tolerancia {UMBRAL_PCT:.1%} · método de 02_LIMPIEZA/03_CALCULOS")
    add('Por fallback (no pasó los 3 checks)', int((df_res['Escalar_flag'] != 'OK').sum()), 'revisar')
    for k, v in df_res['FX_fuente'].value_counts().items():
        add(f'  {k}', int(v))
add('')
add('── MONEDA ──')
if len(df_res):
    add('Indexados (→ breakeven)', int(df_res['Es_Indexado'].sum()))
    for k, v in df_res['Metric_Currency'].value_counts().items():
        add(f'  {k}', int(v))
resumen = pd.DataFrame(rr)

with pd.ExcelWriter(FNAME_OUT, engine='openpyxl') as w:
    resumen.to_excel(w, sheet_name='RESUMEN', index=False)
    fmt_ws(w.sheets['RESUMEN'], '2E4057', freeze='A2')
    (df_res if len(df_res) else pd.DataFrame({'info': ['sin resultados']})).to_excel(
        w, sheet_name='metricas', index=False)
    fmt_ws(w.sheets['metricas'], '1F3864')
    if len(df_sin):
        df_sin.to_excel(w, sheet_name='sin_geneva', index=False)
        fmt_ws(w.sheets['sin_geneva'], '9C0006')
    if len(df_exc):
        df_exc.to_excel(w, sheet_name='a_excepciones', index=False)
        fmt_ws(w.sheets['a_excepciones'], 'B8540B')
    # una pestaña por PK2 con su TD
    for n, (pk2, td) in enumerate(td_store.items()):
        if n >= MAX_PESTANAS: break
        sh = safe_sheet(f"TD_{pk2}")
        td.to_excel(w, sheet_name=sh, index=False)
        fmt_ws(w.sheets[sh], '2E5C9A', freeze='A2')

print(f"\n{'='*70}")
print(f"  Modeladas: {ok} posiciones | Pestañas TD: {min(len(td_store), MAX_PESTANAS)}")
print(f"  Sin jsonl: {len(df_sin)} | Excepciones: {len(df_exc)}")
print(f"  Output: {FNAME_OUT}")
print(f"{'='*70}")