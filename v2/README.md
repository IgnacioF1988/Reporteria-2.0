# Reportería — Yield y Duration por posición (v2)

Pipeline de cierre mensual que obtiene **Yield y Duration por posición (fondo × PK2 × BalanceSheet)** para
todo el patrimonio de los fondos (renta fija, caja, fondos mutuos, pactos, facturas, derivados, equity, cuentas)
y entrega agregados por fondo a nivel activos, pasivos y patrimonio. Reescritura desde cero del pipeline legacy
(`../00_CODIGO`, solo referencia). Plan y decisiones: `PLAN.md`. Pendientes: `FUTURO.md`.

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
```
El paso a paso del operador está en [`CHECKLIST_CIERRE.md`](CHECKLIST_CIERRE.md).
Códigos de salida: 0 OK · 1 OK con alertas CRÍTICAS · 2 falta un input obligatorio o REGLAS inválido.

## Cascada de fuentes (por `Tratamiento` del bucket)
| Tratamiento | Orden | Qué consume terminal |
|---|---|---|
| `CASCADA` (renta fija) | EXCEPCIONES → JPM → RA → **BBG YAS** → **CSHF** (TD de Bloomberg) → **JSONL** (TD propia desde Geneva) | BBG y CSHF solo para lo que sigue pendiente y no es DEF |
| `CAJA` | EXCEPCIONES → RA → JPM → regla `cajas` (índice + spread) | nada |
| `FACTURA` | EXCEPCIONES → RPT de facturas | nada |
| `CERO` / `EXCLUIR` | yield 0 / fuera de métricas | nada |

### Conversión a la moneda del fondo (H4)
| Caso | Qué se hace | Columnas |
|---|---|---|
| Papel indexado (UF, UDI, UVR, IPCA, UI, BONCER, VAC) | breakeven al plazo de la duration: `ajuste = (1+r_nom)/(1+r_real) − 1`, `Yield = (1+y_real)(1+ajuste) − 1`; la Modified se reexpresa `Mac/(1+y_local)` (el legacy la dejaba igual) | `Conversion=BREAKEVEN`, `Yield_Papel`, `Duration_Papel` |
| Flotante con TD propia (CDI, TIIE desde EXCEPCIONES/CSHF/JSONL) | se compone el spread con el nivel spot del índice (aprox.; ver FUTURO) | `Conversion=SUMA_INDICE` |
| Flotante de proveedor | no se toca; se asume nominal local (alerta INFO `FLOTANTE_PROVEEDOR`) | `PROVEEDOR_NOMINAL` |
| Papel USD hedgeado (`Hedge_Currency`) | `politica_hedge`: `XCCY_SI_EXISTE` usa el swap de mercado (`Yield_XCCY`) y si no el drop propio (`Yield_Drop = y_usd + local + basis − usd` con curvas `CURVE_TENOR_RATES`); `DROP_SIEMPRE` usa el drop. Siempre se calculan ambos y `Dif_XCCY_Drop_bps` alerta sobre `xccy_drop_max_bps` | `Conversion=XCCY/DROP`, `Yield_Drop`, `Dif_XCCY_Drop_bps` |
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
| `cajas` | `ID_Fund, PK2, Indice_Referencia (ticker BBG o N.A.), Spread_Anual (decimal), Dias`. Yield = nivel del índice al cierre + spread; Duration = Dias/365. Sin fila → yield 0 + alerta `CAJA_SIN_REGLA`. Migrado de `Template_Cajas`. |
| `defaulteados` | `ID_Fund, ID_Instrumento, Estado (DEF / PROPDEF), Fecha_Desde, Fecha_Fin`. Se suma al `DEFAULTED.xlsx` corporativo (DEF vigente en todos los fondos). Ambos → Yield 0, Duration 0.5. |
| `overrides_valor` | `ID_Fund, ID_Instrumento, SubID_Instrumento, Yield, Duration, Fecha_Desde, Fecha_Fin`. Pisa todo. |
| `overrides_atributo` | Mismo esquema que `EXCEPCIONES.xlsx` corporativo: `ID_Fund, ID_Instrumento, SubID_Instrumento, Field, Value, Fecha_Desde, Fecha_Fin`. |
| `alertas` | Reglas del motor de alertas (campo, operador, umbral, severidad). |
| `parametros` | Umbrales globales (`yield_max_proveedor`, `factura_tolerancia_monto`, `yield_type_default`, …). |

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
PROVEEDOR_INVALIDO, FAMILIA_INFERIDA, YIELD_TYPE_DEFAULT, AI_SOSPECHOSO, FALTANTE, INSUMO_FALTANTE, FX_SIN_BEEMINING,
SIN_BREAKEVEN, SIN_CONVERSION_HEDGE, XCCY_VS_DROP, CURVA_EXTRAPOLADA, INDICE_NUEVO, MONEDA_SIN_CURVAS_DROP, CURVA_SIN_DATOS,
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
Se lee la hoja `Facturas` del RPT de Facts (`FACTURAS_{FECHA}.xlsx` en MERCADO). Cruce `nemotecnico → HOMOL_INSTRUMENTOS
(GENEVA) → ID_Instrumento` y `fondo → HOMOL_FUNDS / BD_FUNDS → ID_Fund`. Se excluyen las pagadas. Yield =
`tasa_mensual × 12` (decimal), Duration = días al vencimiento vigente / 365. `monto_compra` ≠ TotalMVal → alerta.

## Desarrollo
```
pip install -e .[dev]
pytest -q
```
Los fixtures de `tests/fixtures/` salen de `tests/fixtures/construir_fixtures.py` (se corre una vez contra el repo
legacy) y se reemplazan por muestras reales cuando estén disponibles.
