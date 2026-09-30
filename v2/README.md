# Reportería — Yield y Duration por posición (v2)

Pipeline de cierre mensual que obtiene **Yield y Duration por posición (fondo × PK2 × BalanceSheet)** para
todo el patrimonio de los fondos (renta fija, caja, fondos mutuos, pactos, facturas, derivados, equity, cuentas)
y entrega agregados por fondo a nivel activos, pasivos y patrimonio. Reescritura desde cero del pipeline legacy
(`../00_CODIGO`, solo referencia). Plan y decisiones: `PLAN.md`. Pendientes: `FUTURO.md`.

Diagramas de arquitectura, flujo por posición y ciclo del operador: [`docs/Reporteria_2.0_Arquitectura.pdf`](docs/Reporteria_2.0_Arquitectura.pdf)
(se regeneran con `docs/build_diagramas.py`).

## Principios
- **Un solo archivo de parametrización** para el operador: `01_INPUTS/MANUALES/REGLAS.xlsx`.
- **`ID_Fund` numérico** en todo manual; vacío = todos los fondos.
- **Decimal en todo**: 0.05 = 5 %. Un valor > 1.5 en una columna de yield es error de validación.
- Nada queda fuera en silencio: toda posición del CUBO tiene Bucket, Tratamiento y Estado; lo que no se resuelve
  entra al agregado a yield 0 y aparece en `alertas`.
- Credenciales solo en `.env` (ver `.env.example`).

## Instalación (Windows del operador)
```
pip install -e .[bbg,sql]      # xbbg y pyodbc solo donde hay terminal y driver SQL
copy .env.example .env         # completar rutas y credenciales
```

## Uso
```
reporteria check  --fecha 20260731            # inputs, REGLAS.xlsx y cierre anterior
reporteria correr --fecha 20260731            # corrida completa → 02_OUTPUTS/20260731/REPORTE_20260731.xlsx
reporteria correr --fecha 20260731 --sin-bbg  # sin terminal: usa la caché de 04_CACHE/20260731
reporteria importar-cache-legacy --fecha 20260731 --legacy ..   # siembra la caché desde METRICAS/CSHF/CURVAS/ATRIBUTOS del legacy
reporteria migrar-manuales --fecha 20260731 --legacy .. [--aplicar] [--incluir-defaulteados]   # FIP, Atributos_*, OVERRIDES → REGLAS
reporteria comparar --fecha 20260731 --otro 02_OUTPUTS/20260731/REPORTE_20260731_terminal.xlsx  # terminal vs --sin-bbg
reporteria facts-probar                                          # túnel SSH a Facts: cuenta filas de las tres tablas
```
El paso a paso del operador está en [`CHECKLIST_CIERRE.md`](CHECKLIST_CIERRE.md).
Códigos de salida: 0 OK · 1 OK con alertas CRÍTICAS · 2 falta un input obligatorio o REGLAS inválido.

## Cascada de fuentes (por `Tratamiento` del bucket)
| Tratamiento | Orden | Qué consume terminal |
|---|---|---|
| `CASCADA` (renta fija) | EXCEPCIONES → JPM → RA → **BBG YAS** → **CSHF** (TD de Bloomberg) → **JSONL** (TD propia desde Geneva) | BBG y CSHF solo para lo que sigue pendiente y no es DEF |
| `CAJA` | EXCEPCIONES → RA → JPM → regla `cajas` (índice + spread) | nada |
| `FACTURA` | EXCEPCIONES → base de Facts (caché → túnel SSH) → RPT en Excel | nada |
| `CERO` / `EXCLUIR` | yield 0 / fuera de métricas | nada |

### Conversión a la moneda del fondo (H4)
| Caso | Qué se hace | Columnas |
|---|---|---|
| Papel indexado (UF, UDI, UVR, IPCA, UI, BONCER, VAC) | breakeven al plazo de la duration: `ajuste = (1+r_nom)/(1+r_real) − 1`, `Yield = (1+y_real)(1+ajuste) − 1`; la Modified se reexpresa `Mac/(1+y_local)` (el legacy la dejaba igual) | `Conversion=BREAKEVEN`, `Yield_Papel`, `Duration_Papel` |
| Flotante con TD propia (CDI, TIIE desde EXCEPCIONES/CSHF/JSONL) | se compone el spread con el nivel spot del índice (aprox.; ver FUTURO) | `Conversion=SUMA_INDICE` |
| Flotante de proveedor | no se toca; se asume nominal local (alerta INFO `FLOTANTE_PROVEEDOR`) | `PROVEEDOR_NOMINAL` |
| Flotante con índice sin curva (TAB30, CHIBPROM) | la TD del PM ya trae el cupón all-in: queda nominal local (alerta INFO `INDICE_SIN_CURVA`) | `PROVEEDOR_NOMINAL` |
| Papel USD hedgeado (`Hedge_Currency`) | `politica_hedge`: `XCCY_SI_EXISTE` usa el swap de mercado (`Yield_XCCY`) y si no el drop propio (`Yield_Drop = y_usd + local + basis − usd` con curvas `CURVE_TENOR_RATES`); `DROP_SIEMPRE` usa el drop. Siempre se calculan ambos y `Dif_XCCY_Drop_bps` alerta sobre `xccy_drop_max_bps`. Un XCCY fuera de `yield_min/max_proveedor` se ignora (`XCCY_FUERA_RANGO`) | `Conversion=XCCY/DROP`, `Yield_Drop`, `Dif_XCCY_Drop_bps` |
| Override de valor (`REGLAS/overrides_valor`) | pisa todo al final, con vigencia; `Fuente=OVERRIDE` | `Conversion=OVERRIDE` |

El índice de cada papel (`Indice`, `Indice_Origen`) sale de: override del operador → moneda que lo declara (CLF, UDI, UVR COSTER,
UI CURNCY) → Bloomberg (`INFLATION_LINKED_INDICATOR`, `CPN_TYP`+`RESET_IDX`) para papeles con ISIN en moneda local ambigua →
NOMINAL. Curvas reales y nominales: `Carga_Indexes_{FECHA}*.csv` y `Carga_CurvasSoberanas_{FECHA}*.csv` (MERCADO, sep `|`).
Lo que no se puede convertir queda con la yield del papel y alerta ALTA (`SIN_BREAKEVEN`, `SIN_CONVERSION_HEDGE`); el detalle
numérico de cada conversión va a la hoja `conversiones` y las curvas usadas a `curvas_drop`.

Toda métrica de proveedor pasa por sanidad (`yield_min/max_proveedor`, duration > 0) antes de elegirse; lo descartado queda
en `candidatos` con su motivo. Las posiciones hedgeadas (`Hedge_Currency` por fondo) reciben además `Yield_XCCY`
(`YAS_XCCY_FIXED_COUPON_EQUIVALENT`), que en H4 se compara con el drop. Cada consulta a Bloomberg queda en
`04_CACHE/{FECHA}/` como CSV: `bdp_{campo}_{fecha}[_override-valor].csv`, `bdh_{campo}_{fecha}.csv`,
`bds_{campo}/{ticker}.csv`.

## Estructura de carpetas (raíz = `REPORTERIA_RAIZ` o la carpeta padre del paquete)
```
01_INPUTS/MERCADO/    JPM_CEMBI_GBI_{FECHA}.xlsx, RA_TIR.xlsx, Carga_Indexes_{FECHA}*.csv,
                      Carga_CurvasSoberanas_{FECHA}*.csv, 4- Carga de paridades.xlsx, FACTURAS_{FECHA}.xlsx
01_INPUTS/MANUALES/   REGLAS.xlsx, EXCEPCIONES*.xlsx, Atributos_*.xlsx
01_INPUTS/GENEVA/     bond_schedule.jsonl
02_OUTPUTS/{FECHA}/   REPORTE_{FECHA}.xlsx, resumen_corrida_{FECHA}.json
03_LOGS/{FECHA}/      corrida_*.log
04_CACHE/{FECHA}/     respuestas de Bloomberg y FX (permite --sin-bbg)
```
CUBO, BD_INSTRUMENTOS, BD_FUNDS y HOMOL se leen donde están (`RUTA_CUBO_DIR`, `RUTA_BIX`).

## Clasificación (taxonomía corporativa)
`BalSheetKey` = `BalanceSheet` + los 8 códigos del maestro concatenados sin relleno (Investment, Issuer, Issue, Coupon,
Rank, Cash, Bank_Debt, Fund) → `BD_BalanceSheet` → **Bucket** (`Investment_Type_CarteraFI`, 15 valores) y `Ficha_FI`.
Las tablas `BD_FX_Exposure_{FONDO}.xlsx` agregan `FX_Exposure` para los fondos que la tengan. Lo que no mapea queda en
`SIN_REGLA` con alerta, nunca desaparece.

## REGLAS.xlsx (`01_INPUTS/MANUALES/`) — `ID_Fund` vacío = todos los fondos
| Hoja | Para qué |
|---|---|
| `fondos` | `ID_Fund, Politica_Hedge` (`A_CLP`, `POR_PAIS` o vacío). |
| `buckets` | `Bucket, Tratamiento, Orden`: qué se hace con cada bucket. Tratamiento: `CASCADA` busca métrica en proveedores; `CAJA` índice + spread; `FACTURA` RPT de Facts; `CERO` yield 0; `EXCLUIR`. |
| `clasificacion` | Excepciones a la tabla corporativa: `ID, ID_Fund, Criterio (PK2 / ID_Instrumento / BalSheetKey / Nombre_Regex / Issue_Type_Code / Investment_Type_Code), Valor, Bucket y/o Tratamiento`. Regla por fondo gana a global; criterio más específico gana. Ej.: en MRCLP los depósitos son caja; `Issue_Type_Code = 5` → `FACTURA`. |
| `cajas` | `ID_Fund, PK2, Indice_Referencia (ticker BBG o N.A.), Spread_Anual (decimal), Dias`. Yield = nivel del índice al cierre + spread; Duration = Dias/365. Sin fila → yield 0 + alerta `CAJA_SIN_REGLA` (solo si ninguna otra fuente, p. ej. RA para un DAP, la resolvió). Migrado de `Template_Cajas`. |
| `defaulteados` | `ID_Fund (vacío = todos los fondos), ID_Instrumento, Estado (DEF / PROPDEF), Fecha_Desde, Fecha_Fin`. Se suma al `DEFAULTED.xlsx` corporativo (que está desactualizado: 2019–2020). Ambos → `yield_def` (0) y `duracion_def` (0.5) de `parametros`, pisan cualquier proveedor y no gastan terminal. `migrar-manuales --incluir-defaulteados` trae el `DEFAULTEADOS.xlsx` del legacy como una fila global por instrumento (así lo aplicaba el legacy). |
| `overrides_valor` | `ID_Fund, ID_Instrumento, SubID_Instrumento, Yield, Duration, Fecha_Desde, Fecha_Fin`. Pisa todo. |
| `overrides_atributo` | Mismo esquema que `EXCEPCIONES.xlsx` corporativo: `ID_Fund, ID_Instrumento, SubID_Instrumento, Field, Value, Fecha_Desde, Fecha_Fin`. |
| `alertas` | Reglas del motor de alertas (campo, operador, umbral, severidad). |
| `parametros` | Umbrales globales (`yield_max_proveedor`, `factura_tolerancia_monto`, `yield_type_default`, `ra_unico_factor` = 12: RA entrega los depósitos con TIR base 30 días, `yield_def` = 0 y `duracion_def` = 0.5 para DEF/PROPDEF, …). |

## Alertas (REGLAS/alertas)
Cada fila es `Campo Operador Umbral` sobre una columna de `cartera_final` o una derivada. Operadores: `>= <= > < igual distinto
abs>= abs> in es_nulo no_nulo es_verdadero es_falso` (en Excel `==` se vuelve fórmula: se escribe `igual`). `Umbral` acepta
número, texto, `param:<clave>` de `parametros` o lista `a;b;c` para `in`. Dos filas con el mismo Nombre: la que trae `ID_Fund`
manda sobre la global para ese fondo (A01 usa 25 % general y 40 % en MLDL). `Requiere_Anterior=SI` sin cierre previo → la
regla queda INACTIVA y se informa en `alertas_resumen`. `Ambito=FONDO` emite una alerta por fondo (A09 cobertura). Una fila
**sin Campo** ajusta una alerta estructural del pipeline con ese Nombre: `Activa=NO` la silencia, `Severidad` la reclasifica.

Derivadas disponibles: `Delta_Yield`, `Delta_Yield_bps`, `Delta_Duration`, `Delta_Precio`, `Metricas_Iguales_Ant`,
`Precio_Sube_Yield_Sube`, `Es_Nueva` (requieren cierre anterior); `Es_Default`, `AI_Positivo`, `Default_Con_AI`, `Es_Bono`,
`Bono_Sin_AI`, `Falta_Con_MV`, `Resuelta`, `MV_Abs`, `Cobertura_MV_Fondo`.

Alertas estructurales (las emite el pipeline, no se configuran): CUBO_DUPLICADO, SIN_MAESTRO, SIN_FONDO, SIN_REGLA, REGLA_AMBIGUA,
HEDGE_*, OVERRIDE_SIN_POSICION, CAJA_SIN_REGLA, CAJA_SIN_VALOR, INDICE_SIN_NIVEL, FACTURA_*, PK2_DUPLICADO, ESCALA_FALLBACK,
PROVEEDOR_INVALIDO, FAMILIA_INFERIDA, YIELD_TYPE_DEFAULT, AI_SOSPECHOSO, RA_TIR_MENSUAL, FALTANTE, INSUMO_FALTANTE, FX_SIN_BEEMINING,
FACTS_SIN_CONEXION, FACTS_INVALIDO,
SIN_BREAKEVEN, SIN_CONVERSION_HEDGE, XCCY_VS_DROP, XCCY_FUERA_RANGO, INDICE_SIN_CURVA, CURVA_EXTRAPOLADA, INDICE_NUEVO, MONEDA_SIN_CURVAS_DROP, CURVA_SIN_DATOS,
AGREGADO_INCONSISTENTE.

## Agregados
Por fondo y nivel **ACTIVOS**, **PASIVOS** (|MV|) y **PATRIMONIO** (A − P, con MV con signo): `AW = Σ(y·MV)/ΣMV`,
`DW = Σ(y·MV·D)/Σ(MV·D)`, `Duration_AW`, `Cobertura` (MV con métrica / MV). Todo lo sin métrica entra a yield 0 y duration 0;
los excluidos no entran. Se abre por Bucket, Ficha_FI, FX_Exposure, Risk_Country, Risk_Currency, Fuente y Conversion.
La corrida verifica `MV_PAT = MV_ACT − MV_PAS` y que los pesos sumen 1 (alerta CRÍTICA `AGREGADO_INCONSISTENTE` si no).

## Excel del cierre (`REPORTE_{FECHA}.xlsx`)
`resumen` · `agregados` · `alertas_resumen` (una fila por regla con Estado y MV afectado) · `alertas` · `faltantes` ·
`plantilla_overrides` (los faltantes ya en el formato de `REGLAS/overrides_valor`: completar Yield/Duration y pegar) ·
`cartera_final` · `candidatos` · `conversiones` · `curvas_drop` · `td_detalle` · `reglas_aplicadas` (qué fila de REGLAS actuó y
sobre cuántas posiciones) · `insumos`. Más `resumen_corrida_{FECHA}.json`.

## Facturas
Fuente principal: la base de Facts (`bi_facturas`, `bi_prorrogas`, `bi_cambios`) por túnel SSH + Postgres
(`adaptadores/facts.py`). La corrida baja las tres tablas completas y deja `04_CACHE/{FECHA}/facts_*.csv`; `--sin-facts` usa
solo la caché. Si no hay caché ni conexión se lee el RPT en Excel (`FACTURAS_{FECHA}.xlsx` en MERCADO) y, si tampoco,
`INSUMO_FALTANTE`. Cruce `nemotecnico → HOMOL_INSTRUMENTOS (GENEVA) → ID_Instrumento` y `fondo → HOMOL_FUNDS / BD_FUNDS →
ID_Fund`. Yield = `tasa_mensual × 12` (decimal, base 30 días), Duration = días al vencimiento vigente / 365.

Estado **al cierre** (`lectura/facts.facturas_al_cierre`): la base está viva, así que se revierten con `bi_cambios` los cambios
de tasa, vencimiento y monto posteriores al cierre (`FACTURA_ASOF_REVERTIDA`, INFO); una factura pagada después del cierre
sigue viva al cierre (viva = sin `fecha_pago` o pagada después); si hay prórroga iniciada al cierre o antes, mandan su tasa y
su vencimiento (`tasa_origen=PRORROGA`, alerta INFO `FACTURA_TASA_PRORROGA`; el RPT usa la tasa original). `monto_compra` ≠
TotalMVal → `FACTURA_MONTO_DISTINTO`. Conexión: `MONEDA_BI_PASSWORD` y `MONEDA_BI_SSH_KEY` en `.env` (nunca en git); host y
usuarios `FACTS_*` con los valores del proveedor en `.env.example`; `pip install -e .[facts]`; `reporteria facts-probar`
abre el túnel y cuenta filas sin correr el cierre.

## Desarrollo
```
pip install -e .[dev]
pytest -q
```
Los fixtures de `tests/fixtures/` salen de `tests/fixtures/construir_fixtures.py` (se corre una vez contra el repo
legacy) y se reemplazan por muestras reales cuando estén disponibles.
