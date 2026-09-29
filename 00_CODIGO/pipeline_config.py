# =============================================================================
# pipeline_config.py — Configuración central | Reportería 2.0
# =============================================================================
# Único lugar donde se tocan rutas y parámetros. Ningún script debe hardcodear
# rutas ni fechas: todos importan desde acá.
#
# ESTRUCTURA DE CARPETAS
#   Reporteria 2.0\
#     00_CODIGO\        scripts + este archivo
#     01_INPUTS\
#        MERCADO\       JPM, RA_TIR, curvas   (se refrescan cada cierre)
#        GENEVA\        bond_schedule.jsonl
#        MANUALES\      DEFAULTEADOS.xlsx, EXCEPCIONES.xlsx  (analista / PM)
#     02_OUTPUTS\{FECHA}\   un subfolder por cierre
#     03_LOGS\{FECHA}\      alarmas y auditorías
#
# Las fuentes corporativas (CUBO, BD_INSTRUMENTOS, BD_FUNDS, HOMOL, paridades)
# NO se copian: se leen en su ubicación original.
# =============================================================================

import os

# ============================================================
# 1. FECHA DEL CIERRE  ← lo primero que se cambia cada mes
# ============================================================
FECHA = "20260731"                      # settle_dt / fecha de cartera (YYYYMMDD)
HOJA_RA = "jul26"                       # hoja del mes en RA_TIR.xlsx

# ============================================================
# 2. RAÍZ Y CARPETAS DEL PROYECTO
# ============================================================
RAIZ = r"\\moneda03\Compartidos\Inteligencia de Negocios y Mercados\Desarrollo\Cristopher Olmedo\Reporteria 2.0"

DIR_CODIGO   = os.path.join(RAIZ, "00_CODIGO")
DIR_INPUTS   = os.path.join(RAIZ, "01_INPUTS")
DIR_MERCADO  = os.path.join(DIR_INPUTS, "MERCADO")
DIR_GENEVA   = os.path.join(DIR_INPUTS, "GENEVA")
DIR_MANUALES = os.path.join(DIR_INPUTS, "MANUALES")
DIR_OUTPUTS  = os.path.join(RAIZ, "02_OUTPUTS", FECHA)
DIR_LOGS     = os.path.join(RAIZ, "03_LOGS", FECHA)

for _d in (DIR_OUTPUTS, DIR_LOGS):
    os.makedirs(_d, exist_ok=True)

# ============================================================
# 2b. CIERRE ANTERIOR — se detecta solo, no se tipea a mano
# ============================================================
# Mira las carpetas YYYYMMDD ya existentes en 02_OUTPUTS y toma la más
# reciente que sea ANTERIOR a FECHA. Sirve para heredar Hedge_Currency
# (00_UNIVERSO), comparar períodos en flags/atribución (08_OVERRIDES), etc.
# Se puede forzar a mano definiendo FECHA_ANT_OVERRIDE más abajo (por ejemplo,
# para saltarse un cierre que se corrió mal o no se corrió).
FECHA_ANT_OVERRIDE = None    # ej. '20260731' si hay que forzarlo

def _detectar_fecha_ant():
    if FECHA_ANT_OVERRIDE:
        return FECHA_ANT_OVERRIDE
    _dir_out_root = os.path.join(RAIZ, "02_OUTPUTS")
    if not os.path.isdir(_dir_out_root):
        return None
    _candidatas = sorted(
        d for d in os.listdir(_dir_out_root)
        if d.isdigit() and len(d) == 8 and d < FECHA
        and os.path.isdir(os.path.join(_dir_out_root, d))
    )
    return _candidatas[-1] if _candidatas else None

FECHA_ANT = _detectar_fecha_ant()   # None si FECHA es el primer cierre corrido
DIR_OUTPUTS_ANT = (os.path.join(RAIZ, "02_OUTPUTS", FECHA_ANT)
                   if FECHA_ANT else None)

# ============================================================
# 3. FUENTES CORPORATIVAS (se leen donde están)
# ============================================================
_BIX = r"\\moneda03\Compartidos\Inteligencia de Negocios y Mercados\REPORTES MENSUALES\INPUTS\3.- BIX"

RUTA_CUBO_DIR   = r"\\moneda03\Compartidos\Inteligencia de Negocios y Mercados\Desarrollo\Cristóbal Campos\ETL_TABLAS_MODELO_FONDOS\Archivos"
BD_INSTR_PATH   = os.path.join(_BIX, "BD_INSTRUMENTOS.xlsx")
BD_FUNDS_PATH   = os.path.join(_BIX, "DIMENSIONALES", "BD_FUNDS.xlsx")
HOMOL_PATH      = os.path.join(_BIX, "HOMOL_INSTRUMENTOS.xlsx")
ARCHIVO_PARIDS  = os.path.join(DIR_MERCADO, "4- Carga de paridades.xlsx")

CUBO_PATH = os.path.join(RUTA_CUBO_DIR, f"CUBO_{FECHA}.xlsx")

# ============================================================
# 4. INPUTS DEL PROYECTO
# ============================================================
JPM_PATH         = os.path.join(DIR_MERCADO,  f"JPM_CEMBI_GBI_{FECHA}.xlsx")
RA_TIR_PATH      = os.path.join(DIR_MERCADO,  "RA_TIR.xlsx")
JSONL_PATH       = os.path.join(DIR_GENEVA,   "bond_schedule.jsonl")

# ── Archivos de curvas (búsqueda flexible: tolera sufijos como _20260810) ──
def _buscar_archivo(base, directorios=None):
    """Busca {base}_{FECHA}.csv o {base}_{FECHA}*.csv en directorios."""
    import glob as _glob
    if directorios is None:
        directorios = [DIR_MERCADO, DIR_GENEVA]
    patrones = [
        os.path.join(d, f"{base}_{FECHA}.csv") for d in directorios
    ] + [
        os.path.join(d, f"{base}_{FECHA}*.csv") for d in directorios
    ]
    for p in patrones:
        matches = _glob.glob(p)
        if matches:
            return sorted(matches)[0]  # primer match (orden alfabético)
    return None

INDEXES_PATH     = _buscar_archivo("Carga_Indexes")
CURVAS_SOB_PATH  = _buscar_archivo("Carga_CurvasSoberanas")
DEFAULTEADOS_PATH= os.path.join(DIR_MANUALES, "DEFAULTEADOS.xlsx")

# EXCEPCIONES: se admiten VARIOS archivos con la MISMA estructura, para tenerlo
# ordenado por grupo de fondos (EXCEPCIONES_USD.xlsx, EXCEPCIONES_MLDL.xlsx,
# EXCEPCIONES_MRCLP.xlsx…). Se leen todos y se unen; no hay tratamiento
# distinto por archivo. Un PK2 debe estar en UNO solo: si aparece repetido,
# 04_EXCEPCIONES avisa y se queda con la primera aparición.
EXCEPCIONES_GLOB = os.path.join(DIR_MANUALES, "EXCEPCIONES*.xlsx")

def excepciones_paths():
    """Todos los EXCEPCIONES*.xlsx de MANUALES, excluyendo el output homónimo
    (EXCEPCIONES_{FECHA}.xlsx) por si alguien lo deja en la misma carpeta."""
    import glob as _glob
    salida = os.path.basename(OUT_EXCEPCIONES)
    return sorted(p for p in _glob.glob(EXCEPCIONES_GLOB)
                  if os.path.basename(p) != salida
                  and not os.path.basename(p).startswith('~$'))

# EXCEPCIONES_PATH se define más abajo, una vez conocido OUT_EXCEPCIONES.

# ============================================================
# 5. OUTPUTS (nombre estándar por etapa)
# ============================================================
def _out(nombre):
    return os.path.join(DIR_OUTPUTS, f"{nombre}_{FECHA}.xlsx")

def _out_ant(nombre):
    """Ruta del output de una etapa, pero del cierre ANTERIOR (para herencia
    Hedge_Currency, comparación de períodos en flags, etc). None si no hay
    cierre previo detectado."""
    if not FECHA_ANT:
        return None
    return os.path.join(DIR_OUTPUTS_ANT, f"{nombre}_{FECHA_ANT}.xlsx")

OUT_UNIVERSO    = _out("UNIVERSO")
OUT_METRICAS    = _out("METRICAS")
OUT_CSHF        = _out("CSHF")
OUT_JSONL       = _out("PROP_JSONL")
OUT_EXCEPCIONES = _out("EXCEPCIONES")
OUT_CONSOLIDADO = _out("CONSOLIDADO")
OUT_REPORTE     = _out("REPORTE_COBERTURA")
OUT_BREAKEVEN   = _out("BREAKEVEN")

# Atributos por fondo (Index / Index Name por PK2), mantenidos por el analista.
# Se admiten varios: Atributos_MLDL.xlsx, Atributos_MRCLP.xlsx, …
# Se buscan en MANUALES (prioridad) y GENEVA (respaldo).
def atributos_fondo_paths():
    import glob as _glob
    for d in (DIR_MANUALES, DIR_GENEVA):
        paths = sorted(p for p in _glob.glob(os.path.join(d, "Atributos_*.xlsx"))
                       if not os.path.basename(p).startswith('~$'))
        if paths:
            return paths
    return []

# Atributos de indexación derivados de BBG (output de la etapa de atributos)
ATRIBUTOS_BBG_PATH = _out("ATRIBUTOS")

# Parche final: overrides manuales del PM/analista. Pisa TODO — incluso lo ya
# resuelto por proveedores o modelado — para casos rebuscados (Commitments,
# FIP, o una yield dada directamente por otro proveedor) donde no vale la
# pena perseguir una TD. Corre al final, después de breakeven y drops.
OVERRIDES_PATH = os.path.join(DIR_MANUALES, "OVERRIDES.xlsx")
OUT_OVERRIDES           = _out("OVERRIDES")
OUT_OVERRIDES_PENDIENTE = _out("OVERRIDES_PENDIENTE")   # plantilla de lo que aún falta

# Salida de la etapa de drops (aún no implementada). Si no existe, el script
# de overrides sigue funcionando con lo que haya de 05/06 y avisa.
OUT_DROPS = _out("DROPS")

# Outputs del cierre ANTERIOR (auto-detectado). None si no hay cierre previo.
OUT_UNIVERSO_ANT   = _out_ant("UNIVERSO")
OUT_OVERRIDES_ANT  = _out_ant("OVERRIDES")

# Compatibilidad: primer archivo de excepciones (ya conocido OUT_EXCEPCIONES)
_ex = excepciones_paths()
EXCEPCIONES_PATH = _ex[0] if _ex else os.path.join(DIR_MANUALES, "EXCEPCIONES.xlsx")

# ============================================================
# 6. UNIVERSO
# ============================================================
FONDOS = [65, 13, 59, 16, 17, 20, 68, 11, 2]

INVESTMENT_TYPE_RF = 1      # renta fija
ISSUE_TYPE_BONO    = 3      # bonos (subconjunto de RF)

FONDOS_BASE_CLP = {11, 20}  # MDCH y MRCLP: TotalMVal/MVBook/AI en CLP. Resto USD.

# ── Regla de Hedge_Currency — DISTINTA por fondo, no uniforme ───────────────
# MLDL (17): exposición multi-país. Un papel en moneda fuerte se asume
#   swapeado a la moneda LOCAL de su Risk_Country (RISK_COUNTRY_TO_LOCAL_CCY).
# MDCH (11) y MRCLP (20): exposición SOLO CLP. Un papel en moneda fuerte se
#   asume swapeado DIRECTO a CLP, sin mirar el país del papel.
HEDGE_FUNDS_POR_PAIS = {17}          # MLDL
HEDGE_FUNDS_A_CLP     = {11, 20}     # MDCH, MRCLP
HEDGE_FUNDS = HEDGE_FUNDS_POR_PAIS | HEDGE_FUNDS_A_CLP   # compatibilidad
STRONG_CCY  = {'USD', 'GBP', 'EUR'}

# ── DROPS: curvas para papeles USD swapeados a moneda local ────────────────
# drop = local_all_in − pata_usd   (todo en DECIMAL, consistente con el resto)
#   'ADD'    : local_all_in = curva_local + curva_basis/100   (CLP, COP, MXN)
#   'DIRECT' : local_all_in = una sola curva ya combinada     (PEN, BRL)
# CLF/UVR (indexados) NO pasan por acá: ya se resolvieron en 06_BREAKEVEN.
# La pata USD es SOFR salvo BRL, que usa Cupom Cambial.
#
# Fallback de tenors faltantes: SOLO se autogenera cuando los tickers de esa
# MISMA curva siguen un patrón numérico simple (prefijo+año, ej. CPXOSS30).
# No se usa otra familia de tickers como respaldo (ej. CHSWP en vez de
# CHSWNI) mientras no esté confirmada su equivalencia. Sin fallback, se
# extrapola PLANO y se audita (caso México: MPSW16C, MPBSF10J).
CURVAS_DROPS = {
    'CLP': {'metodo': 'ADD',
            'local_curve': 'YCSW0193', 'local_prefix': 'CHSWNI',
            'basis_curve': 'YCSW0194', 'basis_prefix': 'CPXOSS', 'basis_unidad': 'BPS',
            'usd_curve':   'YCSW0490', 'usd_prefix': 'USOSFR'},
    'COP': {'metodo': 'ADD',
            'local_curve': 'YCSW0329', 'local_prefix': 'CLSWIB',
            'basis_curve': 'YCSW0192', 'basis_prefix': 'CLXOQQ', 'basis_unidad': 'BPS',
            'usd_curve':   'YCSW0490', 'usd_prefix': 'USOSFR'},
    'MXN': {'metodo': 'ADD',
            'local_curve': 'YCSW0083', 'local_prefix': 'MPSW',
            'basis_curve': 'YCSW0151', 'basis_prefix': 'MPBSF', 'basis_unidad': 'BPS',
            'usd_curve':   'YCSW0490', 'usd_prefix': 'USOSFR'},
    'PEN': {'metodo': 'DIRECT',
            'local_curve': 'YCSW0374', 'local_prefix': 'PENSSS',
            'usd_curve':   'YCSW0490', 'usd_prefix': 'USOSFR'},
    'BRL': {'metodo': 'DIRECT',
            'local_curve': 'YCSW0089', 'local_prefix': None,
            'usd_curve':   'YCSW0304', 'usd_prefix': None},
}
CURVAS_SIN_FALLBACK_AUTO = {'MPSW', 'MPBSF'}

RISK_COUNTRY_TO_LOCAL_CCY = {
    'AR': 'ARS', 'BR': 'BRL', 'CL': 'CLP', 'PE': 'PEN',
    'UY': 'UYU', 'CO': 'COP', 'MX': 'MXN',
}

# Instrumentos que no son bonos y ningún proveedor cubre (se reportan aparte)
PREFIJOS_EXCLUIDOS = ('FAC',)   # facturas

# ============================================================
# 7. DEFAULT / PROPDEF
# ============================================================
# DEF y PROPDEF NUNCA muestran Yield/Duration del proveedor: se fuerzan
# a estos valores (escenario de liquidación: paga su MVal en un semestre).
YIELD_DEF    = 0.0
DURATION_DEF = 0.5
MARCAS_DEF   = ('DEF', 'PROPDEF')

# ============================================================
# 8. MONEDAS INDEXADAS  (yield del proveedor es REAL → breakeven)
# ============================================================
INDEXADAS = {'CLF', 'UF', 'UDI', 'UVR', 'UVR COSTER', 'IPCA',
             'UI', 'UI CURNCY', 'BONCER', 'VAC'}

INDEX_MAP = {
    'BZDIOVRA': 'CDI',  'MXIBTIEF': 'TIIE', 'MXIBTIIE': 'TIIE',
    'SOFRRATE': 'SOFR', 'H15T1Y': 'UST1Y',  'H15T5Y': 'UST5Y',
    'H15T10Y': 'UST10Y',
}
INFLATION_MAP = {
    'BR': 'IPCA', 'MX': 'UDI', 'CO': 'UVR', 'CL': 'UF',
    'UY': 'UI',   'AR': 'BONCER', 'PE': 'VAC',
}
RISK_CCY_TO_FX = {'CLF': 'USDCLF', 'UVR COSTER': 'USDUVR',
                  'UDI': 'USDUDI', 'UI CURNCY': 'USDUYU'}

# ── BREAKEVEN: curvas por índice (de 3b_INDICES del proyecto) ───────────────
# REAL: curva del índice (Carga_Indexes) | NOM: soberana local (Carga_CurvasSoberanas)
INDEX_TO_CURVE_REAL = {
    'UDI': 'UDIMEXICO', 'UVR': 'UVRCOLOMBIA', 'UF': 'BTUCHILE',
    'IPCA': 'IPCABRAZIL', 'UI': 'UIURUGUAY', 'BONCER': 'BONCERARG',
    'CDI': 'CDIBRAZIL', 'TIIE': 'MXNTIIEMXN', 'SOFR': 'USDSOFR',
}
INDEX_TO_CURVE_NOM = {
    'UDI': 'LCMEXICO', 'UVR': 'LCCOLOMBIA', 'UF': 'LCCHILE',
    'IPCA': 'LCBRAZIL', 'UI': 'LCURUGUAY', 'BONCER': 'LCARGENTINA',
}
INDEX_TO_LOCAL_CCY = {
    'UDI': 'MXN', 'UVR': 'COP', 'UF': 'CLP', 'IPCA': 'BRL',
    'UI': 'UYU', 'BONCER': 'ARS', 'CDI': 'BRL', 'TIIE': 'MXN', 'SOFR': 'USD',
}
# REAL → la yield del proveedor es una tasa real y hay que aplicarle breakeven.
# RATE → ya está en moneda local nominal: NO se convierte.
INDEX_CATEGORY = {
    'UDI': 'REAL', 'UVR': 'REAL', 'UF': 'REAL', 'IPCA': 'REAL',
    'UI': 'REAL', 'BONCER': 'REAL',
    'CDI': 'RATE', 'TIIE': 'RATE', 'SOFR': 'RATE',
}
INDICES_SIN_CONVERSION = {'NOMINAL', 'SOFR', 'UST1Y', 'UST5Y', 'UST10Y'}

# Equivalencias entre los nombres de 'Index Name' de los Atributos_{FONDO}
# y las claves internas. Lo que NO esté acá se reporta en la auditoría del
# script 06 (hoja 'indices_sin_mapear') en vez de fallar silenciosamente.
INDEX_NAME_ALIAS = {
    'CLCPI': 'UF',    'UF': 'UF',      'CLF': 'UF',
    'CER': 'BONCER',  'BONCER': 'BONCER',
    'UDI': 'UDI',     'MXCPI': 'UDI',
    'UVR': 'UVR',     'UVR COSTER': 'UVR',
    'IPCA': 'IPCA',   'BRCPI': 'IPCA',
    'UI': 'UI',       'UYCPI': 'UI',
    'CDI': 'CDI',     'TIIE': 'TIIE',  'SOFR': 'SOFR',
}
# Moneda del papel → índice, cuando la moneda ya es inequívoca
CCY_A_INDEX = {'CLF': 'UF', 'UVR COSTER': 'UVR', 'UDI': 'UDI',
               'UI CURNCY': 'UI'}

# ── Mapeos para aux_ATRIBUTOS_BBG (si se corre) ──────────────────────────────
# RESET_IDX de BBG → nombre interno del índice (flotante)
INDEX_MAP = {
    'COPIBRL': 'CDI', 'CDIBRL': 'CDI',
    'TIIE': 'TIIE', 'TIIE28D': 'TIIE',
    'MXIBRATE': 'TIIE',
    'USDONTD': 'SOFR',
    'CLIBOR': 'CLIBOR',
    'CLICP': 'CLICP',
}
# Risk_Country → índice de inflación (para papeles INFLATION_LINKED_INDICATOR='Y')
INFLATION_MAP = {
    'CL': 'UF', 'CH': 'UF',
    'MX': 'UDI',
    'CO': 'UVR',
    'AR': 'BONCER',
    'UY': 'UI',
    'BR': 'IPCA',
}

# ============================================================
# 9. CASCADA DE PROVEEDORES
# ============================================================
# Orden de preferencia del valor final. EXCEPCIONES va primero porque es
# decisión explícita del PM y pisa a cualquier proveedor.
CASCADA = ['EXCEPCIONES', 'JPM', 'RA', 'BBG', 'CSHF', 'JSONL']

RA_CURRENCIES = {'CLP', 'CLF'}   # RA aplica a papeles locales chilenos
RA_ISSUE_TYPE = 3

# ============================================================
# 10. ESCALARES Y FX  (lógica de 02_LIMPIEZA / 03_CALCULOS)
# ============================================================
# El precio y la cantidad del CUBO vienen en unidades variables por papel.
# No se asume escala: se prueban los candidatos y gana el que calza con MVBook.
CANDIDATOS_ESCALA = [(0.001, 1000), (0.01, 1000), (0.01, 100), (0.01, 1), (1, 1)]
UMBRAL_PCT = 0.005    # 0.5%  calce P*Q/FX vs MVBook
UMBRAL_TC  = 0.20     # ±20%  TC implícito vs FX
RATIO_MIN  = 0.10     # ratio mínimo (distressed severo)
RATIO_MAX  = 8.00     # ratio máximo (BONCER ARS, etc.)
MAX_DIAS_ATRAS_PAR = 10

INSTRUMENTOS_BEE = [
    "USDMXN Curncy", "USDBRL Curncy", "CLFXDOOB_sindesf", "USDCLF Curncy",
    "USDUVR Curncy", "USDPEN Curncy", "USDPYG Curncy", "USDUYU Curncy",
    "USDCOP Curncy", "USDARS Curncy", "USDGBP Curncy", "USDEUR Curncy",
    "USDUDI Curncy", "USDDOP Curncy",
]
BEE_CONN = ('Driver={SQL Server};SERVER=SANWS007;DATABASE=DW_MONEDA;'
            'UID=Consulta_DW;PWD=Consulta2023;')

# ============================================================
# 11. TABLAS DE DESARROLLO
# ============================================================
# BBG normaliza la TD a un face que NO siempre es el pedido: se DERIVA sumando
# el principal de la propia TD (patrón de 03_CALCULOS_VF líneas 278-284).
FACE_BBG_DEFAULT = 1000.0

# Flujos de EXCEPCIONES: vienen normalizados a este nominal.
FACE_EXCEPCIONES = 1_000_000.0

# Clasificación de tasa en el jsonl (validada empíricamente):
# 'Interest Rate' debe reproducir los cupones PASADOS para considerarse fija.
RATIO_MIN_FIJO = 0.85
RATIO_MAX_FIJO = 1.15
# Brasil dc=252 (convención CDI): el test es ambiguo → revisión manual.
DC_REVISION_MANUAL = ('252',)

BBG_BATCH    = 100
MAX_PESTANAS = 60     # tope de pestañas TD_<PK2> por libro

# ============================================================
# 12. FORMATO DE REPORTES
# ============================================================
NAVY, GREY_L, WHITE = '1F3864', 'F2F2F2', 'FFFFFF'
GREEN, GREEN_F      = '006100', 'C6EFCE'
RED, RED_F, AMBER_F = '9C0006', 'FFC7CE', 'FFEB9C'
FONT_NAME = 'Arial'


def resumen_config():
    return (f"Reportería 2.0 | FECHA={FECHA} | fondos={len(FONDOS)}\n"
            f"  inputs : {DIR_INPUTS}\n"
            f"  outputs: {DIR_OUTPUTS}\n"
            f"  cierre anterior: {FECHA_ANT or '(ninguno detectado — se trata como primera corrida)'}")


def pendientes(universo, etapas_previas):
    """Filtra el universo dejando SOLO lo que las etapas previas no resolvieron.

    Cada etapa de fuente es un FALLBACK de la anterior: 02 solo consulta lo que
    01 no resolvió, 03 solo lo que 01+02 no resolvieron. Así no se gasta
    terminal ni se modelan TDs de papeles que ya tienen métrica.

    Criterio de "resuelto": TUPLA COMPLETA (Yield Y Duration). Un papel con
    solo una de las dos sigue pendiente, porque una métrica sola no sirve.

    etapas_previas: lista de (ruta_xlsx, hojas, [(col_yield, col_dur), ...])
    Devuelve el universo filtrado; si un output previo no existe, lo omite
    (permite correr etapas sueltas sin haber corrido las anteriores).
    """
    import pandas as pd
    resueltos = set()
    for ruta, hojas, pares in etapas_previas:
        if not os.path.exists(ruta):
            print(f"   [INFO] {os.path.basename(ruta)} no existe — no se filtra por esa etapa")
            continue
        for hoja in hojas:
            try:
                d = pd.read_excel(ruta, sheet_name=hoja, engine='openpyxl')
            except Exception:
                continue
            if 'PK2' not in d.columns:
                continue
            d['_k'] = (d['ID_Fund'].astype(str) if 'ID_Fund' in d.columns
                       else '') + '|' + d['PK2'].astype(str).str.strip()
            for cy, cd in pares:
                if cy in d.columns and cd in d.columns:
                    ok = d[pd.to_numeric(d[cy], errors='coerce').notna()
                           & pd.to_numeric(d[cd], errors='coerce').notna()]
                    resueltos |= set(ok['_k'])
        print(f"   [PREV] {os.path.basename(ruta):28} → resueltos acumulados: {len(resueltos):,}")

    u = universo.copy()
    u['_k'] = (u['ID_Fund'].astype(str) if 'ID_Fund' in u.columns
               else '') + '|' + u['PK2'].astype(str).str.strip()
    pend = u[~u['_k'].isin(resueltos)].drop(columns=['_k'])
    print(f"   Universo {len(u):,} → ya resueltos {len(u)-len(pend):,} → PENDIENTES {len(pend):,}")
    return pend


# Definición de qué mira cada etapa como "previa"
PREV_METRICAS = (OUT_METRICAS, ('con_ISIN', 'sin_ISIN'),
                 [('JPM_Yield', 'JPM_Duration'), ('RA_Yield', 'RA_Duration'),
                  ('BBG_Yield', 'BBG_Duration')])
PREV_CSHF     = (OUT_CSHF, ('resueltos',), [('CSHF_Yield_efec', 'CSHF_ModDur')])


if __name__ == '__main__':
    print(resumen_config())
    print("\nVerificando inputs...")
    for n, p in [('CUBO', CUBO_PATH), ('BD_INSTRUMENTOS', BD_INSTR_PATH),
                 ('BD_FUNDS', BD_FUNDS_PATH), ('HOMOL', HOMOL_PATH),
                 ('Paridades', ARCHIVO_PARIDS), ('JPM', JPM_PATH),
                 ('RA_TIR', RA_TIR_PATH), ('jsonl', JSONL_PATH),
                 ('DEFAULTEADOS', DEFAULTEADOS_PATH), ('EXCEPCIONES', EXCEPCIONES_PATH),
                 ('Carga_Indexes', INDEXES_PATH), ('CurvasSoberanas', CURVAS_SOB_PATH)]:
        print(f"  [{'OK ' if os.path.exists(p) else 'FALTA'}] {n:18} {p}")