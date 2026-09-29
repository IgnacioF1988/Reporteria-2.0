# =============================================================================
# 05_FALLBACK_CSHF.py — Cuarto proveedor: TD de Bloomberg (DES_CASH_FLOW)
# =============================================================================
# CONTEXTO EN EL FLUJO
#   Tras JPM / RA / YAS_BBG, quedan papeles sin métrica (hoja "A PROP" del
#   REPORTE_COBERTURA). Este script intenta rescatarlos pidiendo a BBG la TABLA
#   DE DESARROLLO completa (BDS DES_CASH_FLOW) y calculando Yield y Duration
#   por cuenta propia a partir de esos flujos. Es el mismo mecanismo del script
#   original 01_BBG_EXTRACCION + 03_CALCULOS, aislado para este fallback.
#
#   → Resultado: Yield_CSHF / Dur_CSHF / Fuente = CSHF
#   → Acota el universo PROP: lo que CSHF resuelve, sale de PROP.
#
# FLUJO INICIAL (por POSICIÓN fondo × PK2)
#   Simplificación pedida:  FI = -TotalMVal        (del CUBO, por fondo)
#   Check adicional      :  FI = -(LocalPrice·Factor·Qty) - AI   [get_attrs]
#   Un mismo PK2 en varios fondos → varias posiciones → varias XIRR (distinto MVal).
#
# MONEDA (crítico)
#   La XIRR sale en la moneda de los flujos = Risk_Currency del papel
#   (Flow_Currency ≡ Risk_Currency, confirmado en 01_BBG_EXTRACCION).
#   Si Risk_Currency es indexada (CLF/UVR/...), la yield es REAL → luego breakeven.
#   Se etiqueta Metric_Currency + Es_Indexado en el output para que sea explícito.
#
# INPUTS
#   REPORTE_COBERTURA_{FECHA}.xlsx   (hoja "A PROP")   → universo a rescatar
#   CUBO_{FECHA}.xlsx                                  → TotalMVal / LocalPrice / Qty / AI / Factor por fondo
#   MAPEO_UNIVERSO_{FECHA}.xlsx      (serie_hermanos)  → fallback de familia
#   [BBG en vivo]  DES_CASH_FLOW  (+ PX_DIRTY_CLEAN opcional para check)
#
# OUTPUT
#   CSHF_{FECHA}.xlsx
#     - resueltos  : posiciones con Yield_CSHF / Dur_CSHF
#     - td_detalle : la TD cruda usada (auditoría)
#     - sin_cshf   : los que BBG tampoco resolvió → siguen a PROP (jsonl)
#     - RESUMEN
# =============================================================================

import os
import sys
import time
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.optimize import brentq
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

_sd = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
for p in (_sd, os.getcwd()):
    if p not in sys.path: sys.path.insert(0, p)

from pipeline_config import (FECHA, CUBO_PATH, OUT_UNIVERSO, OUT_CSHF,
                             FONDOS_BASE_CLP, RISK_CCY_TO_FX, INSTRUMENTOS_BEE,
                             BEE_CONN, CANDIDATOS_ESCALA, UMBRAL_PCT, UMBRAL_TC,
                             RATIO_MIN, RATIO_MAX, pendientes, PREV_METRICAS)

# ============================================================
# CONFIGURACIÓN
# ============================================================
CONSULTAR_BBG = True

FNAME_UNIVERSO = OUT_UNIVERSO
FNAME_CUBO     = CUBO_PATH
FNAME_OUT      = OUT_CSHF

FACE_BBG_DEFAULT = 1000.0   # solo fallback: el face real se DERIVA de la TD
SETTLE_BBG    = FECHA                      # settlement para DES_CASH_FLOW

INDEXADAS = {'UF', 'UDI', 'UVR', 'IPCA', 'UI', 'BONCER', 'VAC', 'CLF', 'UVR COSTER'}
# ============================================================

print("=" * 70)
print(f"05_FALLBACK_CSHF | FECHA={FECHA} | BBG={'ON' if CONSULTAR_BBG else 'OFF'}")
print("=" * 70)


def norm(x): return str(x).strip()

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
    ws.row_dimensions[1].height = 26
    for col in ws.columns:
        w = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 9), 40)

# ── XIRR + Duration (idéntico criterio a 03_CALCULOS) ─────────────────────────
def xirr_calc(flujos, fechas):
    if len(flujos) < 2: return np.nan
    t0 = min(pd.to_datetime(f) for f in fechas)
    days = [(pd.to_datetime(f) - t0).days / 365.25 for f in fechas]
    def npv(r): return sum(c / (1 + r) ** d for c, d in zip(flujos, days))
    try:
        return brentq(npv, -0.9999, 100.0, maxiter=200)
    except Exception:
        return np.nan

def xirr_nominal(xef, cpn_freq):
    if pd.isna(xef) or pd.isna(cpn_freq) or cpn_freq <= 0: return np.nan
    return ((1 + xef) ** (1 / cpn_freq) - 1) * cpn_freq

def duration_calc(flujos_fut, fechas_fut, settle, xirr_ef):
    """Macaulay y Modified duration desde los flujos FUTUROS (sin el flujo inicial),
    descontados a la propia XIRR efectiva (anual, ACT/365.25)."""
    if pd.isna(xirr_ef) or not flujos_fut:
        return np.nan, np.nan
    s = pd.to_datetime(settle)
    t = [(pd.to_datetime(f) - s).days / 365.25 for f in fechas_fut]
    pv = [cf / (1 + xirr_ef) ** ti for cf, ti in zip(flujos_fut, t)]
    tot = sum(pv)
    if tot <= 0:
        return np.nan, np.nan
    mac = sum(ti * p for ti, p in zip(t, pv)) / tot
    mod = mac / (1 + xirr_ef)
    return mac, mod


# =============================================================================
# [1] UNIVERSO
# =============================================================================
# Cada etapa (01/02/03/04) corre sobre el universo completo; es 05_CONSOLIDA
# quien aplica la cascada y decide qué fuente gana por PK2. (Antes esta etapa
# leía la hoja "A PROP" del REPORTE_COBERTURA, que se genera al FINAL del
# pipeline: era una dependencia circular.)
print(f"\n[1] Leyendo universo de {os.path.basename(FNAME_UNIVERSO)}...")
prop = pd.concat([pd.read_excel(FNAME_UNIVERSO, sheet_name=s_, engine='openpyxl')
                  for s_ in ('con_ISIN', 'sin_ISIN')], ignore_index=True)
prop.columns = [str(c).strip() for c in prop.columns]
prop['PK2']  = limpiar_txt(prop['PK2'])
prop['ISIN'] = limpiar_txt(prop['ISIN'])
# nombres estándar del universo → los que usa el resto del script
prop['Fondo']       = prop.get('FundShortName', '')
prop['Instrumento'] = prop.get('Name_Instrumento', '')
prop['Emisor']      = prop.get('CompanyName', '')
prop['Moneda']      = prop.get('Risk_Currency', '')
prop['Hedge']       = prop.get('Hedge_Currency', '')

# ── FALLBACK: solo lo que 01_METRICAS no resolvió ────────────────────────────
# Esta etapa consulta el terminal, así que NO debe pedir TDs de papeles que
# JPM / RA / BBG ya resolvieron con tupla completa.
print(f"\n[1b] Filtrando lo ya resuelto por 01_METRICAS...")
prop = pendientes(prop, [PREV_METRICAS])
print(f"   Posiciones PROP: {len(prop)} | PK2 únicos: {prop['PK2'].nunique()} | con ISIN: {(prop['ISIN']!='').sum()}")

# serie_hermanos ya viene en el universo
uni = prop.copy()
uni['serie_hermanos'] = limpiar_txt(uni.get('serie_hermanos', ''))
hermanos_map = {}
for _, r in uni.drop_duplicates('ISIN').iterrows():
    if r['ISIN'] and r['serie_hermanos']:
        hermanos_map[r['ISIN']] = [x.strip() for x in r['serie_hermanos'].split(',') if x.strip()]

# =============================================================================
# [2] CUBO — TotalMVal / LocalPrice / Qty / AI / Factor por (fondo × PK2)
# =============================================================================
print(f"\n[2] Cruzando con CUBO para TotalMVal por posición...")
cubo = pd.read_excel(FNAME_CUBO, engine='openpyxl')
cubo['PK2'] = limpiar_txt(cubo['PK2'])
cubo_cols = ['ID_Fund', 'PK2', 'LocalPrice', 'Qty', 'OriginalFace', 'Factor',
             'AI', 'MVBook', 'TotalMVal']
cubo_cols = [c for c in cubo_cols if c in cubo.columns]
cubo_slim = cubo[cubo_cols].drop_duplicates(['ID_Fund', 'PK2'])

# El merge DEBE ser por ['ID_Fund','PK2'], no solo por PK2: cubo_slim tiene una
# fila por (ID_Fund, PK2), así que un PK2 compartido por N fondos se
# multiplicaría por N. ID_Fund ya viene en el universo.
if 'ID_Fund' not in prop.columns:
    raise RuntimeError("El universo no trae ID_Fund — revisar 00_UNIVERSO")

_dup = [c for c in cubo_slim.columns if c not in ('ID_Fund', 'PK2') and c in prop.columns]
if _dup:
    print(f"   (columnas tomadas del CUBO, no del input: {_dup})")
    prop = prop.drop(columns=_dup)
prop_pos = prop.merge(cubo_slim, on=['ID_Fund', 'PK2'], how='left')
print(f"   Posiciones tras merge con CUBO: {len(prop_pos)}  (debe ser {len(prop)})")
assert len(prop_pos) == len(prop), "El merge sigue multiplicando filas — revisar duplicados en CUBO"
print(f"   Sin TotalMVal en CUBO: {prop_pos['TotalMVal'].isna().sum()}")

# =============================================================================
# [3] BBG — DES_CASH_FLOW con cascada Corp→Govt→hermano
# =============================================================================
isins_pend = sorted(set(prop_pos.loc[prop_pos['ISIN'] != '', 'ISIN']))
print(f"\n[3] BBG DES_CASH_FLOW para {len(isins_pend)} ISIN...")

td_by_isin   = {}     # isin -> DataFrame(Fecha, Flujo_base_face)
fuente_by_isin = {}   # isin -> 'DIRECTO' | 'GOVT' | 'HERMANO:xxx'

if CONSULTAR_BBG and isins_pend:
    from xbbg import blp

    def safe_bds(ticker, **kw):
        try:
            r = blp.bds(ticker, 'DES_CASH_FLOW', **kw)
            return r if r is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    def normalizar(td):
        """Devuelve DataFrame [Fecha, Cupon, Principal, Flujo] ordenado.
        Se conserva 'Principal' porque el FACE de la TD se DERIVA sumándolo
        (ver más abajo): BBG normaliza a 1.000.000 en general, pero no siempre
        respeta el BQ_FACE_AMT pedido y a veces la suma difiere."""
        t = td.copy()
        t.columns = [str(c).strip().lower() for c in t.columns]
        col_f = next((c for c in ['payment_date', 'date', 'payment date'] if c in t.columns), None)
        if col_f is None: return pd.DataFrame()
        if 'coupon_amount' not in t.columns and 'interest_amount' in t.columns:
            t = t.rename(columns={'interest_amount': 'coupon_amount'})
        for c in ('coupon_amount', 'principal_amount'):
            if c not in t.columns: t[c] = 0.0
        t['Fecha']     = pd.to_datetime(t[col_f], errors='coerce')
        t['Cupon']     = pd.to_numeric(t['coupon_amount'], errors='coerce').fillna(0)
        t['Principal'] = pd.to_numeric(t['principal_amount'], errors='coerce').fillna(0)
        t['Flujo']     = t['Cupon'] + t['Principal']
        return (t.dropna(subset=['Fecha'])[['Fecha', 'Cupon', 'Principal', 'Flujo']]
                .sort_values('Fecha').reset_index(drop=True))

    t0 = time.perf_counter()
    for n, isin in enumerate(isins_pend, 1):
        td = pd.DataFrame()
        for yk, etq in ((f"{isin} Corp", 'DIRECTO'), (f"{isin} Govt", 'GOVT')):
            td = safe_bds(yk, SETTLE_DT=SETTLE_BBG, BQ_FACE_AMT=FACE_BBG_DEFAULT)
            if not td.empty:
                fuente_by_isin[isin] = etq
                break
        if td.empty:                                  # hermano de familia
            for h in hermanos_map.get(isin, []):
                for yk in (f"{h} Corp", f"{h} Govt"):
                    td = safe_bds(yk, SETTLE_DT=SETTLE_BBG, BQ_FACE_AMT=FACE_BBG_DEFAULT)
                    if not td.empty:
                        fuente_by_isin[isin] = f'HERMANO:{h}'
                        break
                if not td.empty: break
        if not td.empty:
            tdn = normalizar(td)
            if not tdn.empty:
                td_by_isin[isin] = tdn
        if n % 25 == 0:
            print(f"      {n}/{len(isins_pend)} — TD obtenidas: {len(td_by_isin)}")
    print(f"   TD obtenidas: {len(td_by_isin)}/{len(isins_pend)} | {time.perf_counter()-t0:0.1f}s")
else:
    print("   BBG OMITIDO")

# =============================================================================
# [4] CALCULAR Yield_CSHF / Dur_CSHF por POSICIÓN
# =============================================================================
# ── ESCALARES + FX (patrón 02_LIMPIEZA + 03_CALCULOS del proyecto) ───────────
# El proyecto NUNCA usa TotalMVal como flujo inicial: arma
#     flujo_inicial = -(P_ef × Q_real) - AI_local     [MONEDA LOCAL DEL BONO]
# y usa TotalMVal/MVBook SOLO para VALIDAR qué (sP, sQ, FX) es el correcto:
#     |P_ef × Q_real / FX| + AI_USD  ≈  |TotalMVal_USD|
# Así el flujo inicial y los flujos de la TD quedan ambos en la moneda del bono
# por construcción, sin importar la moneda del fondo — no hay que convertir nada.
print(f"\n[3b] Cargando FX (para identificar escalares)...")
fx_bee, fx_par = {'USDUSD': 1.0}, {'USDUSD': 1.0}
try:
    import pyodbc, pandas.io.sql as psql
    conn = pyodbc.connect(BEE_CONN)
    ph = ",".join(f"'{c}'" for c in INSTRUMENTOS_BEE)
    sql = (f"SELECT instrumentcode,instrumentvalue,daydate FROM (SELECT instrumentcode,"
           f"instrumentvalue,daydate,ROW_NUMBER() OVER (PARTITION BY instrumentcode "
           f"ORDER BY daydate DESC) rn FROM [DW_MONEDA].[dbo].[TBL_RENTABILIDADES_DW] "
           f"WHERE daydate<='{FECHA}' AND instrumentcode IN ({ph})) t WHERE rn=1")
    for _, rr_ in psql.read_sql(sql, conn).iterrows():
        c_ = str(rr_['instrumentcode']); v_ = float(rr_['instrumentvalue'])
        if v_ > 0:
            fx_bee["USDCLP" if c_ == "CLFXDOOB_sindesf" else c_.replace(" Curncy", "")] = v_
    print(f"   beemining: {len(fx_bee)-1} pares")
except Exception as e:
    print(f"   [WARN] beemining: {e}")


def fx_candidatos(ccy):
    """Todos los candidatos, sin prioridad (gana el que calza)."""
    c = str(ccy).strip().upper()
    if c == 'USD': return [('USDUSD', 1.0)]
    base = RISK_CCY_TO_FX.get(c, f'USD{c}')
    out, seen = [], set()
    for lbl, d in (('bee', fx_bee), ('par', fx_par)):
        for k, v in d.items():
            if k.startswith(base) and pd.notna(v) and v > 0 and round(v, 6) not in seen:
                out.append((f'{lbl}_{k}', v)); seen.add(round(v, 6))
    return out


def evaluar_escala(p, of, factor, mv, fx_val):
    """3 checks contra MVBook, idéntico a evaluar() de 02_LIMPIEZA_VF."""
    res = []
    for sP, sQ in CANDIDATOS_ESCALA:
        p_ef, of_ef = p * sP, of * sQ
        pq   = p_ef * of_ef * factor / fx_val
        diff = abs(pq - mv) / abs(mv) if mv != 0 else np.nan
        tc   = (p_ef * of_ef * factor) / abs(mv) if mv != 0 else np.nan
        sanity = True if fx_val == 1.0 else (
            pd.notna(tc) and abs(tc - fx_val) / fx_val <= UMBRAL_TC)
        ratio = (of_ef * factor / fx_val) / abs(mv) if (fx_val != 0 and mv != 0) else np.nan
        ratio_ok = pd.notna(ratio) and (RATIO_MIN <= ratio <= RATIO_MAX)
        res.append({'sP': sP, 'sQ': sQ, 'diff_pct': diff, 'ratio': ratio,
                    'ratio_ok': bool(ratio_ok),
                    'all_ok': pd.notna(diff) and diff <= UMBRAL_PCT and sanity and ratio_ok})
    return res


print(f"\n[4] Calculando XIRR y Duration por posición...")

res_rows, td_rows_out, sin_rows = [], [], []
settle_dt = pd.to_datetime(FECHA)

for _, r in prop_pos.iterrows():
    pk2, isin, fondo = r['PK2'], r['ISIN'], r.get('Fondo', '')
    fid = r.get('ID_Fund', np.nan)
    rc  = str(r.get('Moneda', '')).strip().upper()
    tmv = pd.to_numeric(r.get('TotalMVal'), errors='coerce')
    qty = pd.to_numeric(r.get('Qty'), errors='coerce')
    fac = pd.to_numeric(r.get('Factor'), errors='coerce'); fac = fac if pd.notna(fac) else 1.0

    td = td_by_isin.get(isin)
    if td is None or td.empty or pd.isna(tmv):
        sin_rows.append({'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
                         'Instrumento': r.get('Instrumento', ''), 'Moneda': rc,
                         'Motivo': 'sin TD BBG' if (td is None or td.empty) else 'sin TotalMVal'})
        continue

    # ── FACE DERIVADO (patrón de 03_CALCULOS_VF, líneas 278-284) ─────────────
    # BBG normaliza la TD a un face que NO siempre es el pedido con BQ_FACE_AMT
    # (suele ser 1.000.000, pero a veces devuelve otra base y los sinks suman
    # algo distinto). En vez de asumirlo, se DERIVA sumando el principal de la
    # propia TD; así la escala es correcta cualquiera sea la normalización.
    face_bbg = td['Principal'].sum()
    if face_bbg <= 0: face_bbg = td['Principal'].abs().sum()
    if face_bbg <= 0: face_bbg = FACE_BBG_DEFAULT
    if pd.isna(qty) or qty == 0:
        sin_rows.append({'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
                         'Instrumento': r.get('Instrumento', ''), 'Moneda': rc,
                         'Motivo': 'sin Qty en CUBO'})
        continue
    # (la escala definitiva se fija más abajo, con el sQ identificado)

    td_fut = td[td['Fecha'] > settle_dt].copy()
    if td_fut.empty:
        sin_rows.append({'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
                         'Instrumento': r.get('Instrumento', ''), 'Moneda': rc,
                         'Motivo': 'TD sin flujos futuros'})
        continue

    fechas_fut = td_fut['Fecha'].tolist()

    # ── FLUJO INICIAL EN MONEDA LOCAL DEL BONO (patrón del proyecto) ─────────
    # Se identifican (sP, sQ, FX) calzando contra MVBook, y con ellos se arma
    #     flujo_inicial = -(P_ef × Q_real) - AI_local
    # todo en moneda del bono, igual que los flujos de la TD.
    lp_  = pd.to_numeric(r.get('LocalPrice'), errors='coerce')
    of_  = pd.to_numeric(r.get('OriginalFace'), errors='coerce')
    mvb_ = pd.to_numeric(r.get('MVBook'), errors='coerce')
    ai_  = pd.to_numeric(r.get('AI'), errors='coerce'); ai_ = ai_ if pd.notna(ai_) else 0.0
    base_clp = (int(fid) in FONDOS_BASE_CLP) if pd.notna(fid) else False
    of_base  = of_ if pd.notna(of_) and of_ != 0 else qty

    if pd.isna(lp_) or pd.isna(mvb_) or mvb_ == 0:
        sin_rows.append({'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
                         'Instrumento': r.get('Instrumento', ''), 'Moneda': rc,
                         'Motivo': 'sin LocalPrice/MVBook en CUBO'})
        continue

    # Candidatos de FX: papel→USD y, si el fondo es base CLP, papel→CLP
    cands = fx_candidatos(rc)
    if base_clp:
        u = fx_bee.get('USDCLP')
        if u and u > 0:
            cands = [(f'{l}/USDCLP', v / u) for l, v in cands]

    sP_ = sQ_ = np.nan; fxv_, fxf_, flag_ = np.nan, 'SIN_FX', 'FALLBACK'
    for lbl, v in cands:
        ok = next((i for i in evaluar_escala(lp_, of_base, fac, mvb_, v) if i['all_ok']), None)
        if ok:
            sP_, sQ_, fxv_, fxf_, flag_ = ok['sP'], ok['sQ'], v, lbl, 'OK'; break
    if flag_ != 'OK':
        mejor = None
        for lbl, v in cands:
            for i in evaluar_escala(lp_, of_base, fac, mvb_, v):
                if pd.isna(i['diff_pct']): continue
                clave = (not i['ratio_ok'], i['diff_pct'])   # ratio manda sobre diff
                if mejor is None or clave < mejor[0]:
                    mejor = (clave, i, lbl, v)
        if mejor is None:
            sin_rows.append({'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
                             'Instrumento': r.get('Instrumento', ''), 'Moneda': rc,
                             'Motivo': f'sin FX/escalar para {rc}'})
            continue
        _, i, lbl, v = mejor
        sP_, sQ_, fxv_, fxf_ = i['sP'], i['sQ'], v, f'FALLBACK_{lbl}'

    p_ef     = lp_ * sP_
    q_esc    = of_base * sQ_ * fac      # nominal vigente en unidades del papel
    ai_local = ai_ * fxv_               # AI: moneda del fondo → moneda del papel
    fi_local = -(p_ef * q_esc) - ai_local

    # La TD se escala con este mismo Q_real (no con el de Qty×Factor crudo)
    escala     = q_esc / face_bbg
    flujos_fut = (td_fut['Flujo'] * escala).tolist()

    flujos = [fi_local] + flujos_fut
    fechas = [settle_dt] + fechas_fut

    xef = xirr_calc(flujos, fechas)
    # frecuencia inferida de la mediana de días entre cupones
    if len(fechas_fut) >= 2:
        difs = np.diff([pd.Timestamp(f).value for f in fechas_fut]) / 8.64e13 / 365.25
        freq = round(1 / np.median(difs)) if np.median(difs) > 0 else 2
        freq = min(max(freq, 1), 12)
    else:
        freq = 1
    xnom = xirr_nominal(xef, freq)
    mac, mod = duration_calc(flujos_fut, fechas_fut, settle_dt, xef)

    # Check opcional: FI = -(P·Factor·Qty) - AI
    lp = pd.to_numeric(r.get('LocalPrice'), errors='coerce')
    qty = pd.to_numeric(r.get('Qty'), errors='coerce')
    fac = pd.to_numeric(r.get('Factor'), errors='coerce')
    ai  = pd.to_numeric(r.get('AI'), errors='coerce')
    xef_check = np.nan
    if pd.notna(lp) and pd.notna(qty):
        q_real   = qty * (fac if pd.notna(fac) else 1.0)
        # precio efectivo decimal (LocalPrice en base 100) × cantidad real, menos AI
        fi_check = fi_local
        xef_check = xirr_calc([fi_check] + flujos_fut, [settle_dt] + fechas_fut)

    es_idx = rc in INDEXADAS
    res_rows.append({
        'Fondo': fondo, 'ID_Fund': fid, 'PK2': pk2, 'ISIN': isin,
        'Instrumento': r.get('Instrumento', ''), 'Emisor': r.get('Emisor', ''),
        'Moneda': rc, 'Metric_Currency': rc, 'Es_Indexado': es_idx,
        'Hedge': r.get('Hedge', ''),
        'TotalMVal': tmv, 'MVBook': mvb_, 'Base_fondo': 'CLP' if base_clp else 'USD',
        'sP': sP_, 'sQ': sQ_, 'FX_usado': fxv_, 'FX_fuente': fxf_, 'Escalar_flag': flag_,
        'AI_cubo': ai_, 'AI_local': ai_local, 'FI_local': fi_local,
        'Qty': qty, 'Factor': fac,
        'face_bbg': face_bbg, 'escala': escala, 'Q_real': q_esc,
        'Fuente_TD': fuente_by_isin.get(isin, ''),
        'N_flujos': len(flujos_fut), 'Freq_inferida': freq,
        'CSHF_Yield_efec': xef, 'CSHF_Yield_nom': xnom,
        'CSHF_MacDur': mac, 'CSHF_ModDur': mod,
        'CSHF_Yield_check_PQ': xef_check,
        'Dif_check_bps': (abs(xef - xef_check) * 1e4 if pd.notna(xef) and pd.notna(xef_check) else np.nan),
        'Fuente': 'CSHF',
        'Requiere_Breakeven': es_idx,
    })

    for _, tf in td_fut.iterrows():
        td_rows_out.append({'PK2': pk2, 'ID_Fund': fid, 'ISIN': isin,
                            'Fecha': tf['Fecha'], 'Flujo_baseFACE': tf['Flujo'], 'face_bbg': face_bbg,
                            'Flujo_escalado': tf['Flujo'] * escala})

df_res = pd.DataFrame(res_rows)
df_td  = pd.DataFrame(td_rows_out)
df_sin = pd.DataFrame(sin_rows)

conv = int(df_res['CSHF_Yield_efec'].notna().sum()) if len(df_res) else 0
print(f"   Posiciones con Yield_CSHF: {conv}/{len(prop_pos)}")
print(f"   Sin resolver (siguen a PROP): {len(df_sin)}")
if len(df_res) and df_res['Dif_check_bps'].notna().any():
    print(f"   Check FI=-TotalMVal vs -(P·Q)-AI: dif mediana {df_res['Dif_check_bps'].median():0.1f} bps")

# =============================================================================
# [5] RESUMEN + GUARDAR
# =============================================================================
print(f"\n[5] Guardando {os.path.basename(FNAME_OUT)}...")

rr = []
def add(m, v='', d=''): rr.append({'Concepto': m, 'Valor': v, 'Detalle': d})
add('── UNIVERSO PROP ──')
add('Posiciones PROP (entrada)', len(prop_pos))
add('PK2 únicos', prop_pos['PK2'].nunique())
add('Con ISIN (consultables BBG)', int((prop_pos['ISIN'] != '').sum()))
add('')
add('── RESCATE VÍA CSHF ──')
add('Posiciones resueltas con CSHF', conv, f"{conv/len(prop_pos):.1%}" if len(prop_pos) else '')
add('Posiciones que siguen a PROP', len(df_sin))
if len(df_res):
    add('  vía TD directa', int((df_res['Fuente_TD'] == 'DIRECTO').sum()))
    add('  vía Govt', int((df_res['Fuente_TD'] == 'GOVT').sum()))
    add('  vía hermano de serie', int(df_res['Fuente_TD'].astype(str).str.startswith('HERMANO').sum()))
add('')
add('── MONEDA / INDEXACIÓN ──')
if len(df_res):
    add('Resueltos indexados (→ breakeven)', int(df_res['Es_Indexado'].sum()))
    for k, v in df_res['Metric_Currency'].value_counts().items():
        add(f'  {k}', int(v))
df_resumen = pd.DataFrame(rr)

with pd.ExcelWriter(FNAME_OUT, engine='openpyxl') as w:
    (df_res if len(df_res) else pd.DataFrame({'info': ['sin resultados']})).to_excel(
        w, sheet_name='resueltos', index=False)
    fmt_ws(w.sheets['resueltos'], '1F3864')
    if len(df_td):
        df_td.to_excel(w, sheet_name='td_detalle', index=False)
        fmt_ws(w.sheets['td_detalle'], '2E5C9A')
    if len(df_sin):
        df_sin.to_excel(w, sheet_name='sin_cshf', index=False)
        fmt_ws(w.sheets['sin_cshf'], '9C0006')
    df_resumen.to_excel(w, sheet_name='RESUMEN', index=False)
    fmt_ws(w.sheets['RESUMEN'], '2E4057', freeze='A2')

print(f"\n{'='*70}")
print(f"  CSHF resueltos: {conv} | siguen a PROP: {len(df_sin)}")
print(f"  Output: {FNAME_OUT}")
print(f"{'='*70}")