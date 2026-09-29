# Plan de modificación — Reportería 2.0

Fecha del diagnóstico: 2026-09-29 · Base: commit `fc92d6e` (commit inicial) · Último cierre corrido: 20260731

---

## 0. Qué es el proyecto (resumen de lo que entendí)

Pipeline Python (pandas + openpyxl + scipy + xbbg + pyodbc) que obtiene **Yield y Duration por posición (fondo × PK2)** para 9 fondos de renta fija, usando métricas ya calculadas por proveedores y modelando tabla de desarrollo (TD) solo para el residual.

```
00 UNIVERSO ──► 01 METRICAS (JPM > RA > BBG) ──► 02 CSHF (TD de BBG) ──► 03 JSONL (TD de Geneva)
                                                                              │
04 EXCEPCIONES (flujos del PM) ──► 05 CONSOLIDA (cascada + DEF) ──► 06 BREAKEVEN ──► 07 DROPS ──► 08 OVERRIDES
                                                                     (indexados)   (hedgeados)   (parche + flags + AW/DW)
```

Cascada: `EXCEPCIONES > JPM > RA > BBG > CSHF > JSONL`, exigiendo tupla completa (Yield **y** Duration).
Todo el pipeline trabaja en la moneda del papel; 06 y 07 convierten al final; 08 pisa con overrides manuales, calcula flags y yields agregadas (AW/DW) por fondo.

### Estado de la última corrida (20260731)

| Métrica | Valor |
|---|---|
| Posiciones RF en el CUBO | 2.126 |
| Excluidas en 00: defaulteados / facturas | 194 / 472 |
| Posiciones vigentes (universo) | 1.460 (787 PK2 únicos) |
| Resueltas | 1.418 (97,1 %) |
| Faltantes | 42 (34 PK2), 28 de ellas en MRCLP |
| Fuente final: JPM / BBG / RA / EXCEP / CSHF / JSONL | 591 / 432 / 261 / 84 / 43 / 7 |
| Breakeven aplicado / Drop aplicado | 243 / 106 (26 sin curva) |
| Flags activos | 100 posiciones con yield ≥ 25 %, 16 con yield negativa |

---

## 1. Hallazgos (lo que hoy está mal o incompleto)

### A. Bugs que afectan el resultado numérico

| # | Hallazgo | Evidencia | Impacto |
|---|---|---|---|
| A1 | **RUN_PIPELINE no encuentra 4 de 9 scripts.** `ETAPAS` referencia `02_CSHF.py`, `03_JSONL.py`, `04_EXCEPCIONES.py`, `08_OVERRIDES.py`; los archivos reales son `02_CSHF_VF.py`, `03_JSONL_VF.py`, `04_EXCEPCIONES_VF.py`, `08_OVERRIDE.py`. | `correr()` imprime `[SKIP] no existe` y sigue | Un `python RUN_PIPELINE.py` completo salta CSHF, JSONL, EXCEPCIONES y OVERRIDES en silencio. Los outputs existentes se generaron corriendo los scripts a mano. |
| A2 | **Los defaulteados no se reincorporan.** 00 los saca del universo (hoja `defaulteados`), pero 05 arma la base solo con `con_ISIN` + `sin_ISIN` y luego intenta mapear DEF sobre PK2 que ya no están. | Panorama: "por regla DEF/PROPDEF: **0**" pese a 194 posiciones DEF; `cartera_final` no tiene ninguna fila `REGLA_DEF` | La cartera final tiene 1.460 posiciones en vez de 1.654. Las yields agregadas AW/DW de 08 están **sobreestimadas** (falta el peso de los defaults con yield 0). El README dice lo contrario de lo que hace el código. |
| A3 | **06_BREAKEVEN: la Duration convertida es idéntica a la original.** `mac = dur*(1+y_local); mod_local = mac/(1+y_local)` ⇒ `mod_local == dur` siempre. | Hoja `convertidos`: `Duration_original == Duration_local` en las 243 filas | La reexpresión de ModDur a la yield nominal nunca ocurre. Fórmula correcta: `mac = dur*(1+y_real)`, `mod_local = mac/(1+y_local)`. |
| A4 | **08: el override `DEF`/`PROPDEF` nunca se aplica.** Se filtra `ov[pd.to_numeric(ov['Yield']).notna()]` *antes* de detectar `Yield == 'DEF'`. | Código, líneas 174–178 | La columna `Marcar_como_DEF_PROPDEF` de la plantilla es inútil. |
| A5 | **Sin filtro de sanidad sobre métricas de proveedor.** Se aceptan yields de bonos vencidos o en default no listados. | `TRNTEL 9 12/01/2016` yield 514 %, dur 0; `CZRSBZ 2015/2016` 142 %; `PDCAR 2026` yield **65.011 %**; `INTSPN 2019`, `DOCUFO 2024` | 100 posiciones con yield ≥ 25 % contaminan AW/DW. Deberían caer a FALTANTES/override cuando `Duration ≤ 0`, `maturity < settle` o yield fuera de rango. |
| A6 | **Unidades inconsistentes entre etapas.** JPM/RA/BBG en %; CSHF/JSONL/EXCEPCIONES en decimal (05 multiplica ×100); 06 recibe % y devuelve decimal; 07 trabaja en %; 08 divide /100 selectivamente. | RESUMEN de 06 muestra "yield 507,31 % → 8,35 %" (multiplica un % por 100) | Cada nueva etapa tiene que adivinar la unidad. Es la fuente más probable de errores futuros. |
| A7 | **08 ignora BBG_XCCY_Yield y usa siempre Yield_Drop**, contradiciendo el comentario de 07 ("por defecto XCCY si existe"). | 08 línea 145 mapea solo `Yield_Drop` | 07 reporta 50 de 81 papeles con `|XCCY − Drop| > 50 bps` (media −151 bps, máx 987 bps). La calculadora propia pisa el dato de mercado sin validación. |
| A8 | **Hedge_Currency = USD en papeles USD** (12 MLDL + 14 MRCLP) → 07 los manda a `sin_convertir` con "USD: sin curvas". | DROPS `sin_convertir` 26 filas | O es "sin hedge" mal codificado o falta el caso `Hedge == Risk_Currency` como no-op. |
| A9 | 3 nombres de índice sin mapear: `COCPI`, `MXIBTIIE`, `UI CURNCY`. | BREAKEVEN `auditoria` | Trivial: agregar a `INDEX_NAME_ALIAS`. |
| A10 | `pipeline_config` define `INDEX_MAP` e `INFLATION_MAP` **dos veces**; la primera definición muere. | líneas 266–273 vs 320–336 | Confuso; el mapeo `BZDIOVRA/MXIBTIEF/H15T*` no existe en la práctica. |
| A11 | 02_CSHF: `xef_check` se calcula con el mismo `fi_local` que `xef` ⇒ `Dif_check_bps` siempre 0. | líneas 433–438 | Check muerto que da falsa seguridad. |

### B. Deuda técnica

| # | Hallazgo |
|---|---|
| B1 | **Credenciales SQL en el código y en git** (`BEE_CONN` con `PWD=...` en `pipeline_config.py`, repetido literal en `03_JSONL_VF.py`). |
| B2 | `RAIZ` es una ruta UNC hardcodeada con nombre de persona; `os.makedirs` corre al importar el config. Nada corre fuera de esa red. |
| B3 | Helpers duplicados en 4–6 scripts con variantes divergentes: `limpiar_txt`, `fmt_ws`, `xirr_calc`, `duration_calc`, `evaluar_escala`, carga FX (beemining + paridades). `03_JSONL_VF` redefine localmente `INSTRUMENTOS_BEE`, `BEE_CONN` y `RISK_CCY_TO_FX` pisando lo importado. |
| B4 | Sin `requirements.txt`, sin `.gitignore`, sin tests. Los `.xlsx` de outputs e inputs (con posiciones reales) están versionados. |
| B5 | `03_LOGS/` se crea pero nunca se escribe; todo es `print`. |
| B6 | `Otros/` son versiones viejas de los scripts (diff de 150–300 líneas) más `aux_REPORTE_COBERTURA.py` que lee un archivo (`MAPEO_METRICAS`) que ya no existe. En la raíz hay archivos sueltos sin consumidor: `ATRIBUTOS_CLP.xlsx`, `Cajas_jul.xlsx`, `FIP.xlsx` (en MANUALES). |
| B7 | README desactualizado: dice que 06/07 están pendientes, omite 08, ATRIBUTOS, OVERRIDES.xlsx y los múltiples EXCEPCIONES_*.xlsx, y referencia `aux_REPORTE.py`. |
| B8 | `aux_ATRIBUTOS_BBG` hardcodea `HOJA_RA = "jul26"` en lugar de importarla del config. |
| B9 | `01_METRICAS` corre con `MODO_MAPEO_COMPLETO = True`: consulta a BBG **todos** los ISIN aunque JPM/RA ya los resolvieron, contra lo que dice el README. `02_CSHF` pide TDs de PK2 que EXCEPCIONES va a pisar igual. |
| B10 | `check_inputs()` de RUN_PIPELINE no verifica curvas ni `Atributos_*.xlsx`; no hay flag global para correr sin terminal (`CONSULTAR_BBG` está en 6 scripts distintos). |

### C. Gaps funcionales (lo que el pipeline aún no cubre)

| # | Gap | Dato |
|---|---|---|
| C1 | **MRCLP: 27 faltantes sin ISIN**, todos instrumentos no-bono: DAP (Issue_Type 4), pagarés `P$`/`PAG` (7, 8), CFI (10), FIP (10), mutuos hipotecarios `CLH`/`MHE` (13, 14). MRCLP queda en 83,5 % de cobertura y 76 % del AUM. | FALTANTES |
| C2 | `ATRIBUTOS_CLP.xlsx` (hojas `Hoja1` y `Flotantes`: Type, Periodicity, Cpn Rate/Spread, Index Name TAB30/CHIBPROM/CPI, FIP, Upfront Fee) y `FIP.xlsx` (Treatment Equity/Bond) **existen pero ningún script los lee**. Son exactamente los insumos para C1. | raíz y MANUALES |
| C3 | `Cajas_jul.xlsx` (caja, payables/receivables por fondo) tampoco se consume. La "Yield Agregada" de 08 es solo sobre RF resuelta, no sobre el patrimonio del fondo. | raíz |
| C4 | `bond_schedule.jsonl` es un snapshot de **abril 2026**: 100 posiciones (77 PK2) tienen código HOMOL pero no están en el jsonl. Solo 10 posiciones se modelan vía Geneva. | PROP_JSONL `sin_geneva` |
| C5 | RA: la TIR se usa tal cual; la conversión a yield equivalente según `PERIODICIDAD_CUPONES` está declarada pendiente en 01. | comentario 01 |
| C6 | 06: para flotantes (CDI/TIIE) de proveedor se **asume** que ya vienen en nominal local (supuesto explícitamente no verificado). Para flotantes propios se suma el nivel spot, no la forward. | comentario 06 |
| C7 | Hedge_Currency: 131 posiciones asignadas por regla y marcadas "REVISAR", nunca revisadas. La herencia del mes anterior recién arranca en el cierre siguiente. | UNIVERSO resumen |
| C8 | No existe el drop inverso (papel local swapeado a USD en fondo USD), si el negocio lo requiere. | — |
| C9 | 07: México (`MPSW`, `MPBSF`) sin fallback de tenores 1Y/30Y → extrapolación plana auditada. 06: 58 posiciones extrapoladas fuera de curva. | auditorías |

---

## 2. Plan por fases

### Fase 1 — Correcciones críticas (sin cambiar arquitectura) · ~1 semana

Objetivo: que `python RUN_PIPELINE.py` produzca una cartera final correcta y completa.

1. **A1** Alinear nombres: renombrar `02_CSHF_VF.py → 02_CSHF.py`, `03_JSONL_VF.py → 03_JSONL.py`, `04_EXCEPCIONES_VF.py → 04_EXCEPCIONES.py`, `08_OVERRIDE.py → 08_OVERRIDES.py`. Hacer que `correr()` falle (no `[SKIP]`) si falta un script marcado como listo.
2. **A2** En 05, leer también la hoja `defaulteados` del UNIVERSO y reincorporarla con `Yield=0`, `Duration=0.5`, `Fuente='REGLA_DEF'`, `Estado='DEFAULT'`. Verificar que 06/07/08 las excluyan de conversiones y que 08 las incluya en AW/DW.
3. **A3** Corregir la reexpresión de Duration en 06.
4. **A4** En 08, detectar `DEF`/`PROPDEF` antes del filtro numérico.
5. **A5** Nueva validación en 05 (antes de la cascada): descartar la tupla de una fuente si `Duration ≤ 0`, `|Yield| > umbral` (propuesta: 100 % nominal) o vencimiento < settle. Las posiciones caen a la siguiente fuente y, si ninguna pasa, a FALTANTES con motivo `METRICA_INVALIDA`. Agregar hoja `metricas_descartadas` para auditoría.
6. **A6** Definir **una** convención (propuesta: decimal en todos los archivos intermedios, % solo en presentación) y un módulo `unidades.py` con `a_decimal(fuente, valor)`. Cada etapa declara la unidad de sus columnas. Assert al final: yields resueltas en `[-0.5, 1.0]` salvo DEF.
7. **A7** En 08, política explícita y configurable: `YIELD_HEDGED = 'XCCY_SI_EXISTE' | 'DROP_SIEMPRE'`, con columna de trazabilidad. Default a acordar (ver §4).
8. **A8** En 00/07: `Hedge_Currency == Risk_Currency` ⇒ sin hedge (NaN) con warning, en vez de error en 07.
9. **A9, A10, A11, B8** Limpiezas puntuales de config y checks muertos.

Entregable: pipeline completo corriendo end-to-end sobre 20260731 con BBG apagado (usando los outputs ya generados de 01/02/07 como caché), y una comparación antes/después de `cartera_final` y `Yield Agregada`.

### Fase 2 — Estructura y mantenibilidad · ~1 semana

1. **B3** Crear paquete `00_CODIGO/lib/` (o `comun/`): `finanzas.py` (xirr, duration, breakeven, drop, escalares), `fx.py` (beemining + paridades + candidatos), `io_excel.py` (lectura tolerante, `fmt_ws`, escritura de RESUMEN), `texto.py` (`limpiar_txt`, `pick_col`). Los scripts pasan a importar; se eliminan las copias locales y las redefiniciones de 03.
2. **B1** Sacar credenciales: `BEE_CONN` desde variables de entorno (`BEE_UID`, `BEE_PWD`) o archivo `.env` fuera de git. **Rotar la clave** que ya quedó en el historial.
3. **B2** `RAIZ` = variable de entorno `REPORTERIA_RAIZ` con fallback a la carpeta padre de `00_CODIGO`. Crear carpetas de output en `RUN_PIPELINE`, no al importar.
4. **B4** `requirements.txt` (pandas, numpy, openpyxl, scipy, xbbg, pyodbc), `.gitignore` (`02_OUTPUTS/`, `03_LOGS/`, `~$*`, `.env`). Decidir si los inputs con posiciones reales se versionan (ver §4).
5. **B5** `logging` a `03_LOGS/{FECHA}/{etapa}.log` + consola. RUN_PIPELINE escribe un `resumen_corrida.json`.
6. **B6** Mover `Otros/` a `legacy/` con un README de una línea, o borrarlo (todo está en git). Mover `ATRIBUTOS_CLP.xlsx` y `FIP.xlsx` a `01_INPUTS/MANUALES/` y `Cajas_jul.xlsx` a `01_INPUTS/GENEVA/` (o donde corresponda) con nombre fechado.
7. **B7** Reescribir README: tabla real de etapas, inputs manuales (los tres EXCEPCIONES, OVERRIDES, DEFAULTEADOS, Atributos_*, FIP), convención de unidades, cómo correr sin terminal.
8. **B9, B10** Flags globales en RUN_PIPELINE: `--sin-bbg` (propaga a todas las etapas vía env var), `--modo-ahorro` para 01. `check_inputs` completo. 02 excluye de la consulta los PK2 presentes en EXCEPCIONES*.xlsx.

### Fase 3 — Cobertura funcional · 2–3 semanas, requiere decisiones del PM

1. **C1 + C2** Nueva etapa `04b_ATRIBUTOS_LOCALES.py` (o extensión de 04) que consuma `ATRIBUTOS_CLP.xlsx` + `FIP.xlsx`:
   - `FIP.Treatment = Equity` → sale del universo relevante (hoja aparte, como facturas). `Bond` → TD desde atributos.
   - DAP: `Yield = tasa`, `Duration = plazo residual / 365`.
   - Bullet/Sinkable con `Cpn Rate` fijo: TD desde `Periodicity`, `Next_Cpn Date`, vencimiento; misma mecánica de escala/FX que 04.
   - Flotantes (`Flotantes`: Spread + Index Name TAB30/CHIBPROM/CPI): TD = spread + proyección del índice; `Upfront Fee` al flujo inicial. Requiere curvas TAB/CHIBPROM (definir fuente).
   - Mutuos hipotecarios y pagarés: confirmar con el PM si van por atributos o por override.
   - Precedencia en la cascada: entre EXCEPCIONES y JPM (es un input manual, pero menos explícito que un flujo dado).
2. **C3** Incorporar `Cajas` al agregado de 08: nueva hoja `Yield Agregada (patrimonio)` con caja a yield 0 (o tasa overnight configurable) y payables/receivables según decida el PM. Mantener la hoja actual (solo RF).
3. **C4** Documentar/automatizar el refresco del `bond_schedule.jsonl` (hoy abril 2026). Si Geneva tiene extracción programable, script `aux_GENEVA_EXPORT.py`.
4. **C5** RA: convertir TIR a yield efectiva anual según `PERIODICIDAD_CUPONES` antes de la cascada (misma convención que el resto).
5. **C6** Validar con 3–5 papeles reales (BBG YAS vs TD propia) si la yield de flotante de proveedor viene en nominal local. Ajustar `FUENTES_PROVEEDOR` en 06 según resultado.
6. **A7 / C9** Validar metodología de drops contra XCCY: revisar convención del basis (bps vs %), día de curva, interpolación; documentar la diferencia esperada. Agregar fallback de tenores para México.
7. **C7** Hoja `hedge_a_revisar` en UNIVERSO con los 131 casos por regla, para que el analista la corrija una vez y la herencia mensual haga el resto.
8. **C8** Drop inverso local→USD, solo si el PM confirma que existen posiciones así.

### Fase 4 — Calidad y pruebas · en paralelo desde Fase 2

1. Tests unitarios (`pytest`) de `lib/finanzas.py` con los casos que ya están documentados en comentarios: RECARR (primer cupón completo), CASH URUGUA (Factor 0.3152), PATIO PERU (Factor 0.95), caso ARS (beemining vs paridades), empate `(0.001,1000)` vs `(1,1)` resuelto por ratio.
2. Test de regresión "golden": correr el pipeline con `--sin-bbg` sobre 20260731 y comparar `cartera_final` contra una copia de referencia (tolerancia 1 bp).
3. Assert de unidades y de completitud (nº posiciones cartera_final == posiciones RF del CUBO − facturas − FIP equity).
4. Checklist de cierre mensual en README y un `RUN_PIPELINE.py --check` que la valide.

---

## 3. Orden sugerido y dependencias

```
Fase 1 (A1..A11)  ──►  Fase 2 (lib/, config, README)  ──►  Fase 3 (04b, Cajas, RA, drops)
       │                        │
       └── Fase 4.2 golden ─────┴── Fase 4.1 tests unitarios
```

Fase 1 va primero porque hoy cualquier comparación antes/después está contaminada por A2 y A5. Fase 2 antes de Fase 3 porque 04b reutiliza escala/FX/XIRR y no conviene copiarlos una cuarta vez.

---

## 4. Decisiones que necesito de ustedes antes de Fase 1.6 / Fase 3

| # | Decisión | Propuesta |
|---|---|---|
| D1 | Convención de unidades en archivos intermedios | Decimal en todo; % solo en hojas de presentación |
| D2 | Umbral de sanidad para yields de proveedor (A5) | Descartar si `Duration ≤ 0` o `Yield > 100 %` o vencido |
| D3 | Yield final de hedgeados: XCCY si existe, o siempre Yield_Drop | XCCY si existe, hasta validar la calculadora |
| D4 | `MODO_MAPEO_COMPLETO` en producción | `False` (ahorro de terminal), `True` solo para mapeos |
| D5 | Tratamiento de FIP Equity, CFI, cajas y payables en el agregado | Equity/CFI fuera del universo RF; caja a yield 0 en una hoja aparte |
| D6 | Versionar inputs con posiciones reales en git | No: `.gitignore` + carpeta compartida; versionar solo código y plantillas vacías |
| D7 | Rotación de la clave SQL que quedó en el historial | Sí, y mover a variables de entorno |

---

## 5. Archivos que toca cada fase (referencia rápida)

- **Fase 1**: `RUN_PIPELINE.py`, `05_CONSOLIDA.py`, `06_BREAKEVEN.py`, `07_DROPS.py`, `08_OVERRIDE(S).py`, `pipeline_config.py`, `00_UNIVERSO.py` (A8), renombres de 02/03/04/08.
- **Fase 2**: nuevo `lib/`, todos los scripts (imports), `pipeline_config.py`, `README.md`, `requirements.txt`, `.gitignore`, `Otros/` → `legacy/`.
- **Fase 3**: nuevo `04b_ATRIBUTOS_LOCALES.py`, `05_CONSOLIDA.py` (cascada), `08_OVERRIDES.py` (Cajas), `01_METRICAS.py` (RA), `06_BREAKEVEN.py`, `07_DROPS.py`.
- **Fase 4**: nuevo `tests/`, `RUN_PIPELINE.py` (`--check`, `--sin-bbg`).
