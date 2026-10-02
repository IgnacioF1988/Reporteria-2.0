# Reportería — Yield y Duration por posición (v2)

Pipeline de cierre mensual que obtiene **Yield y Duration por posición (fondo × PK2 × BalanceSheet)** para
todo el patrimonio de los fondos (renta fija, caja, fondos mutuos, pactos, facturas, derivados, equity, cuentas)
y entrega agregados por fondo a nivel activos, pasivos y patrimonio. Reescritura desde cero del pipeline legacy
(repo legacy `Reporteria-2.0/00_CODIGO`, solo referencia de lógica; v2 no lee nada de ahí). Plan y decisiones: `PLAN.md`. Pendientes: `FUTURO.md`.

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
pip install -e .[bbg,sql]      # xbbg y pyodbc solo donde hay terminal y driver SQL (duckdb viene en las dependencias base)
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
reporteria dim importar --bix <RUTA_BIX> [--reemplazar]          # dimensionales: migra BD_BalanceSheet / FX / catálogos a dim/dimensionales.duckdb
reporteria dim exportar | dim importar --excel DIM.xlsx          # ciclo de edición (valida antes de escribir)
reporteria dim validar --fecha 20260731 | dim probar --fecha 20260731   # consistencia; clasificación del CUBO con las dimensionales
reporteria publicar --fecha 20260731 [--reexpresar --motivo "…"]  # fija la versión oficial del cierre en datamart/ (luego git add datamart && commit)
reporteria versiones [--fecha F] | reporte --fecha F [--publicada|--version N|--conocimiento D]   # qué hay; regenerar el Excel de una versión
reporteria comparar --fecha F --version-a 1 --version-b 2       # diferencias entre dos versiones del datamart
reporteria maestros cargar | cambios | estado --llave PK2        # BD_INSTRUMENTOS/HOMOL bitemporales (la carga la hace `correr` solo)
reporteria declarar --llave PK2 --columna C --valor V --desde F  # el cambio rige desde F (no es corrección retroactiva)
reporteria impacto [--fecha F] [--detalle] | recalcular --fecha F [--con-terminal] [--motivo "…"] | pendientes [--fecha F]
```
El paso a paso del operador está en [`CHECKLIST_CIERRE.md`](CHECKLIST_CIERRE.md).
Códigos de salida: 0 OK · 1 OK con alertas CRÍTICAS · 2 falta un input obligatorio o REGLAS inválido.

## Cascada de fuentes (por `Tratamiento` del bucket)
| Tratamiento | Orden | Qué consume terminal |
|---|---|---|
| `CASCADA` (renta fija) | EXCEPCIONES → JPM → RA → **BBG YAS** → **CSHF** (TD de Bloomberg) → **JSONL** (TD propia desde Geneva) | BBG y CSHF solo para lo que sigue pendiente y no es DEF |
| `CAJA` | EXCEPCIONES → RA → JPM → regla `cajas` (índice + spread) | nada |
| `FACTURA` | EXCEPCIONES → base de Facts (caché → túnel SSH) → RPT en Excel → RA (respaldo, p. ej. FNCHI) | nada |
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

## Estructura de carpetas (raíz = siempre la carpeta del paquete `v2/`; solo CUBO y BIX viven fuera)
```
01_INPUTS/MERCADO/    JPM_CEMBI_GBI_{FECHA}.xlsx, RA_TIR.xlsx, Carga_Indexes_{FECHA}*.csv,
                      Carga_CurvasSoberanas_{FECHA}*.csv, 4- Carga de paridades.xlsx, FACTURAS_{FECHA}.xlsx
01_INPUTS/MANUALES/   REGLAS.xlsx, EXCEPCIONES*.xlsx, Atributos_*.xlsx
01_INPUTS/GENEVA/     bond_schedule.jsonl
02_OUTPUTS/{FECHA}/   REPORTE_{FECHA}.xlsx, resumen_corrida_{FECHA}.json
03_LOGS/{FECHA}/      corrida_*.log
04_CACHE/{FECHA}/     respuestas de Bloomberg y FX (permite --sin-bbg)
v2/dim/               dimensionales.duckdb (versionado en git) + csv/ espejo legible para el diff
v2/datamart/          cierres/cierre={FECHA}/version=NNN/ (Parquet inmutable por versión publicada, en git)
02_OUTPUTS/{FECHA}/borradores/borrador_{ts}/   cada corrida, mismo formato; `publicar` copia uno al datamart
```
CUBO, BD_INSTRUMENTOS, HOMOL y DEFAULTED se leen donde están (`RUTA_CUBO_DIR`, `RUTA_BIX`). Las dimensionales
(clasificación, fondos, monedas, catálogos) viven en el repo: ver §Dimensionales.

## Clasificación (taxonomía corporativa)
Cada posición recibe **Bucket** (15 valores), **Ficha_FI** y **FX_Exposure** desde `dim_clasificacion` (ver §Dimensionales):
filas con comodines sobre `BalanceSheet` + los 8 códigos del maestro (Investment, Issuer, Issue, Coupon, Rank, Cash,
Bank_Debt, Fund) + `Emision_nacional`. Por atributo gana la fila más específica; una fila con `ID_Fund` (taxonomía propia
del fondo) gana siempre a la genérica. `BalSheetKey` (los 9 campos concatenados) sigue en `cartera_final` solo para auditar.
Lo que ninguna fila define queda en `SIN_REGLA` (Bucket) o vacío (Ficha_FI / FX_Exposure) con alerta y sale en la hoja
`plantilla_dim` del reporte, listo para pegar como fila nueva. Encima actúan `REGLAS/clasificacion` (por instrumento o
regex) y `REGLAS/overrides_atributo` (`Field=Bucket`).

## Versiones del cierre y datamart (`v2/datamart/`)
Cada `correr` deja, además del Excel, un **borrador** completo en `02_OUTPUTS/{F}/borradores/borrador_{ts}/`: una tabla Parquet por
hoja del reporte, `posiciones.parquet` con todas las columnas de la cartera (incluidos los atributos del maestro que decidieron el
resultado), `insumos/` (copias de REGLAS, RA_TIR, paridades, jsonl, EXCEPCIONES y el CSV de dim; hashes de CUBO, JPM, curvas, dim y caché;
`facturas_al_cierre.parquet`) y `corrida.json` (fecha, hora, opciones, hash del código, cierre anterior usado). Los borradores no van a git.

`reporteria publicar --fecha F` copia el último borrador (o `--borrador TS`) a `datamart/cierres/cierre=F/version=001/` como **PUBLICADA**:
eso es "lo reportado" y nunca se reescribe. Una segunda publicación exige `--reexpresar --motivo "…"` y crea `version=002` **REEXPRESADA**;
`publicacion.json` lleva el historial. Después del `publicar`: `git add datamart && git commit && git push`.

Lectura: `reporte --fecha F` regenera el Excel de la **última verdad** (mayor versión); `--publicada` lo reportado; `--version N`; `--conocimiento D`
la última versión publicada hasta ese día. `versiones` lista todo; `comparar --version-a A --version-b B` muestra las diferencias de Yield,
Duration, Fuente, Conversion y Estado. El cierre anterior de una corrida se toma de la última verdad del datamart y, si no hay versión, del
`REPORTE_{F-1}.xlsx` como antes (`resumen.anterior_version` dice cuál se usó). H9b–H9d (maestros bitemporales, impacto y re-expresión
automática, consulta por fecha de conocimiento) están en PLAN.md §5g.

## Modo diario (H10, en construcción)
`REPORTERIA_MODO=diario` activa el layout `datamart/diario/fecha=F/corrida=NNN/` + `estado.json` (estado por fondo-día), pensado para
un datamart en el share (`REPORTERIA_DATAMART`) con caché compartida (`REPORTERIA_CACHE`). Las escrituras son atómicas (carpeta `.tmp` y
rename; una corrida sin `corrida.json` se ignora), hay candado de escritura (`.lock`) y `reporteria migrar-datamart --destino <share>`
copia el datamart mensual de git al share. En modo `mensual` (default) todo sigue igual. `correr --sin-excel` deja solo el borrador.

**Publicación por fondo-día (H10b).** Cada corrida termina con la hoja `publicacion` (una fila por fondo de la corrida y por fondo
esperado = `Activo_MantenedorFondos=1` en `dim_fondos`): `Listo` y `Bloqueos`. Bloquean: insumo obligatorio ausente
(`parametros.insumos_obligatorios`, default `CUBO`; p. ej. `CUBO;JPM`), posiciones `PENDIENTE_TERMINAL:n`, `COBERTURA:x<mínimo`
(`cobertura_min_mv`), `AGREGADO_INCONSISTENTE` del fondo (ahora ámbito FONDO) y `ALERTA:<Nombre>` para las alertas con
`Bloquea_Publicacion=SI` en REGLAS/alertas (la fila con `ID_Fund` manda sobre la global para ese fondo); un fondo esperado sin
posiciones queda `FONDO_SIN_POSICIONES`. En modo diario `publicar` escribe en `estado.json` el **puntero de cada fondo**: listo y nunca
publicado → `PUBLICADA`; listo y ya publicado → `REEXPRESADA` solo si su cartera cambió (`publicacion.fondos_cambiados`: Yield, Duration,
Fuente, Conversion, Estado o posiciones), si no el puntero no se mueve; no listo → `PROVISORIO` apuntando a la corrida nueva (última
verdad) y `publicada` sigue en la última oficial; sin posiciones → `SIN_CORRIDA`. Por eso `recalcular` re-apunta solo los fondos que
cambiaron. `reporteria estado --fecha F [--fondo N]` lo muestra (exit 1 si hay PROVISORIO o SIN_CORRIDA) y `check` resume cuántos
fondos están sin publicar. `impacto` trae ahora la columna `fondo`. Vistas DuckDB (`adaptadores/datamart.vistas(raiz)`): `corridas`,
`estado`, `posiciones_diarias`, `publicadas` (solo lo oficial) y `ultima_verdad` (incluye PROVISORIO con `estado_fondo` y `bloqueos`);
el layout mensual de git se lee como si todos los fondos de la última versión estuvieran publicados.

**Semántica diaria (H10c).** Hay **un cierre por cada día calendario**: Geneva lo entrega el siguiente día hábil (el lunes aparecen
viernes, sábado y domingo; tras un feriado, todo lo acumulado). `calendario.pendientes(fechas_con_corrida, hoy, existe_cubo, feriados)`
lista las fechas hasta hoy−1 sin corrida y su estado: `LISTA` (hay CUBO), `AUN_NO_ESPERADA` (llega el próximo hábil) o `SIN_CUBO`
(ya pasó el hábil en que debía llegar: anomalía). Hábil = lunes a viernes fuera de la hoja opcional `REGLAS/feriados` (`Fecha, Mercado,
Descripcion`; cuentan las filas con Mercado vacío o CL/GLOBAL). El cierre mensual es el fondo-día del último día calendario del mes,
sin caso especial. En modo diario:
- el **cierre anterior es por fondo** (`datamart.anterior_por_fondo`): la última verdad de cada fondo con fecha anterior, aunque sea de
  distinta fecha por fondo (un fondo que faltó un día hereda del anterior; `anterior_version.fondos` lo registra); `Hedge_Origen` pasa a
  `ANTERIOR` (antes `MES_ANTERIOR`) y un fondo sin cartera previa recibe la alerta INFO `SIN_HISTORIA`;
- un parámetro `clave_diario` en REGLAS/parametros pisa a `clave` (p. ej. `delta_yield_max_diario`, `cobertura_min_mv_diario`);
- `RA_TIR_{F}.xlsx` fechado tiene prioridad sobre `RA_TIR.xlsx` (se lee su primera hoja y cuenta como insumo fechado para el impacto);
  con el archivo mensual, si la hoja del mes aún no existe se usa la última;
- **CADENA por contenido**: impacto solo si el hedge que hoy heredaría una posición difiere del que usó esa versión (no por número de
  versión); `comparar` también compara `Hedge_Currency`;
- política `reexpresar_por` (REGLAS/parametros; default diario `MAESTRO;DIM;INSUMO;CADENA`, mensual todo): lo que no está en la
  política se **marca** (`accion=MARCAR` en `impacto`) y se re-expresa a mano con `reporteria recalcular --desde F [--hasta G] --motivo
  "..."`; una hoja de REGLAS cuyas filas distintas tienen todas `ID_Fund` re-expresa solo esos fondos; cambios de código y REGLAS
  globales solo marcan;
- `impacto` evalúa solo los últimos `ventana_reexpresion_dias` (60) días; `impacto --todos` los evalúa todos.

## Impacto y re-expresión (`impacto`, `recalcular`, `pendientes`)
Cada `correr` termina evaluando el **impacto** de la verdad actual sobre la última verdad de cada cierre publicado: atributos del
maestro usados vs as-of hoy (consecuencia CLASIFICACION, HEDGE, FUENTE, IDENTIDAD), dimensionales re-resueltas (DIM), hojas de REGLAS
distintas (REGLAS), hash del código (CODIGO), CUBO/JPM/curvas re-entregados (INSUMO) y cierre anterior desactualizado (CADENA). Con
impacto, los cierres afectados (menos el que se está corriendo) se **re-expresan solos** en orden cronológico como versiones
REEXPRESADA con motivo `auto: …`; `--sin-recalcular` lo posterga y `reporteria recalcular --fecha F [--motivo]` lo hace a mano
(`--sin-cadena` no arrastra los posteriores; `--insumo RA_TIR|paridades|bond_schedule` refresca un insumo sin fecha, que por defecto se
toma de la copia guardada en la versión; `--refrescar-facts` vuelve a bajar Facts). `reporteria impacto [--detalle]` lo muestra sin
tocar nada (exit 1 si hay impacto).

Si xbbg está instalado pero no carga en esa máquina (p. ej. xbbg ≥ 1.0 sin blpapi en Python 3.14: `DLL load failed … _core`), `correr`
no se cae: el adaptador nace "caído", sale la alerta CRÍTICA `BBG_SIN_CONEXION` con el error, no se persiste ningún "sin dato" en la
caché y lo que falte queda `PENDIENTE_TERMINAL`; `check` lo muestra como `[AVISO] xbbg instalado pero no carga`.

Sin terminal, lo que una re-expresión necesitaría pedir a Bloomberg y no está en caché queda **`PENDIENTE_TERMINAL`** (Yield/Duration
vacíos, cuentan como sin métrica en los agregados, motivo `SIN_CACHE_BBG` y columna `Pedido_BBG` con campo, ticker y overrides) y la
versión queda `PARCIAL`; `reporteria pendientes [--fecha F]` las lista y `recalcular --fecha F --con-terminal` las cierra. La caché
Bloomberg guarda ahora también los "sin dato" (fila con valor vacío) para distinguirlos de lo nunca preguntado, y las curvas de drop se
cachean con su `CURVE_DATE` en el nombre (los archivos antiguos sin fecha siguen sirviendo de respaldo de lectura). Una caché anterior a
este cambio no trae "sin dato": la primera re-expresión sin terminal de un cierre viejo deja pendientes que una sola corrida
`--con-terminal` resuelve y persiste.

## Maestros bitemporales (`v2/datamart/maestros/`)
BD_INSTRUMENTOS y HOMOL (instrumentos y fondos) se guardan en el datamart como **base + deltas**: `maestros/base/carga=…/` (una vez)
y `maestros/cambios/carga=YYYYMMDD_HHMMSS/cambios.parquet` con filas `(tabla, llave, columna, valor_anterior, valor_nuevo, tipo ∈ ALTA |
CAMBIO | BAJA | RENOMBRE)`. Cada `correr` compara el BIX de hoy con el estado del datamart y, si difiere, registra una carga nueva
(log `MAESTROS: carga … con N cambios`, alerta INFO `MAESTRO_CAMBIOS`). Sin acceso al BIX la corrida usa el estado del datamart.

Dos tiempos: **conocimiento** = la carga (`correr --conocimiento YYYYMMDD` usa solo lo cargado hasta ese día) y **validez** = desde qué
cierre rige un valor. Por defecto todo cambio es **corrección retroactiva** (vale para toda la historia; lo publicado no se toca, ver
§Versiones). Excepción: una `BAJA` nunca es retroactiva (los cierres anteriores a su carga conservan la fila). Cuando un cambio es un hecho
nuevo y no una corrección, el operador lo declara:

```
reporteria declarar --llave 2-1 --columna Investment_Type_Code --valor 1 --desde 20260831 [--comentario "…"]
reporteria declarar --anular 3
```
La declaración queda en `datamart/declaraciones/vigencias.csv` (en git) con `valor_anterior` tomado de `maestros cambios` (o `--valor-anterior`
si la base ya traía el valor nuevo); los cierres anteriores a `--desde` vuelven a ver el valor anterior. `maestros estado --llave PK2
[--cierre F] [--conocimiento D]` muestra la fila como se usó; `maestros cambios [--desde D] [--llave K]` lista la historia; `maestros cargar
[--base]` fuerza una carga (o una re-base). `Pos_Key = ID_Fund|ID_Instrumento|BalanceSheet` da continuidad entre cierres cuando el PK2 cambia
de moneda (`IDENTIDAD_PK2`: el CUBO trae el PK2 viejo y el maestro ya tiene el nuevo; se toman los atributos de la única fila del instrumento).

## Dimensionales (`v2/dim/dimensionales.duckdb`)
Un solo archivo DuckDB **versionado en git** es la fuente de verdad; reemplaza a `BD_BalanceSheet`, las
`BD_FX_Exposure_{FONDO}.xlsx`, `BD_FUNDS`, `BD_Monedas`, `BD_YLD_FLAG` y los `BD_*_TYPE` del BIX en la corrida. Tablas:
`dim_clasificacion`, `dim_fondos`, `dim_monedas`, `dim_yld_flag`, `dim_investment_type` … `dim_fund_type` (catálogos) y
`dim_meta` (fecha de importación). El espejo `dim/csv/*.csv` se regenera en cada escritura para que el `git diff` sea legible.

`dim_clasificacion`: `ID | ID_Fund | BalanceSheet | Investment_Type_Code … Fund_Type_Code | Emision_nacional | Bucket | Ficha_FI |
FX_Exposure | Comentario | Origen_Migracion | Vigente_Desde | Vigente_Hasta`. Vacío en una columna de llave = cualquiera;
vacío en un atributo = la fila no lo define. Resolución por atributo: candidatas = filas cuyas columnas fijas coinciden; gana la
de más columnas fijas (+100 si `ID_Fund` está fijo); empate con valores distintos → menor `ID` + alerta `DIM_AMBIGUA`.

Cómo se usa:
- **Clasificación nueva** = normalmente **una fila** con los códigos mínimos que la determinan (lo demás vacío) y los atributos
  que cambian. Ej.: pactos → `BalanceSheet=Asset, Investment_Type_Code=1, Issue_Type_Code=7, Bucket=Repo`.
- **Fondo con taxonomía propia** = filas con su `ID_Fund` solo donde difiere; hereda todo lo demás (MRCLP: 3 filas de Bucket
  y 13 de FX_Exposure en lugar de una columna extra y un archivo por fondo).
- **Por instrumento**: `REGLAS/clasificacion` (`PK2`, `ID_Instrumento`, `Nombre_Regex`) y `overrides_atributo`. El criterio
  `BalSheetKey` está deprecado: `dim validar --fecha F` lista esas filas para moverlas a `dim_clasificacion` con `ID_Fund`.
- **Ciclo de edición**: `reporteria dim exportar` (Excel con una hoja por tabla) → editar → `reporteria dim importar --excel
  DIM.xlsx` (valida: códigos en catálogos, fondos, filas sin atributo, solapes ambiguos; exit 2 y no escribe si hay errores) →
  `git add dim/ && git commit`. `dim probar --fecha F` clasifica el CUBO con las dimensionales y lista las combinaciones sin fila.
- **Primera vez / rehacer desde el BIX**: `reporteria dim importar --bix <RUTA_BIX> [--reemplazar]` compacta BD_BalanceSheet
  (146 llaves → 24 filas de Bucket y 18 de Ficha_FI), la variante `_MRCLP` (3 filas con `ID_Fund=20`), las FX (MLDL como
  genérica para todos los fondos; MRCLP con `ID_Fund=20` y `Emision_nacional`) y copia los catálogos. La compactación es
  lossless sobre las llaves presentes (test de equivalencia) y nunca deja dos filas que puedan empatar.

## REGLAS.xlsx (`01_INPUTS/MANUALES/`) — `ID_Fund` vacío = todos los fondos
| Hoja | Para qué |
|---|---|
| `fondos` | `ID_Fund, Politica_Hedge` (`A_CLP`, `POR_PAIS` o vacío). |
| `buckets` | `Bucket, Tratamiento, Orden`: qué se hace con cada bucket. Tratamiento: `CASCADA` busca métrica en proveedores; `CAJA` índice + spread; `FACTURA` RPT de Facts; `CERO` yield 0; `EXCLUIR`. |
| `clasificacion` | Excepciones por instrumento a las dimensionales: `ID, ID_Fund, Criterio (PK2 / ID_Instrumento / Nombre_Regex / Issue_Type_Code / Investment_Type_Code; BalSheetKey deprecado → dim_clasificacion), Valor, Bucket y/o Tratamiento`. Regla por fondo gana a global; criterio más específico gana. Ej.: `Issue_Type_Code = 5` → `FACTURA`; FIP con Treatment Equity por PK2. |
| `cajas` | `ID_Fund, PK2, Indice_Referencia (ticker BBG o N.A.), Spread_Anual (decimal), Dias`. Yield = nivel del índice al cierre + spread; Duration = Dias/365. Sin fila → yield 0 + alerta `CAJA_SIN_REGLA` (solo si ninguna otra fuente, p. ej. RA para un DAP, la resolvió); la hoja `plantilla_cajas` del reporte trae esas posiciones en este formato con una sugerencia trazable (`COPIA_PK2`: mismo PK2 con fila en otro fondo; `NOMBRE`: tasa y vencimiento en el nombre `CR_CLP_SCOTIA_20260909_0.4800`; `POR_MONEDA`: índice y spread más usados para esa moneda) para revisar y pegar. Migrado de `Template_Cajas`. |
| `defaulteados` | `ID_Fund (vacío = todos los fondos), ID_Instrumento, Estado (DEF / PROPDEF), Fecha_Desde, Fecha_Fin`. Se suma al `DEFAULTED.xlsx` corporativo (que está desactualizado: 2019–2020). Ambos → `yield_def` (0) y `duracion_def` (0.5) de `parametros`, pisan cualquier proveedor y no gastan terminal. `migrar-manuales --incluir-defaulteados` trae el `DEFAULTEADOS.xlsx` del legacy como una fila global por instrumento (así lo aplicaba el legacy). |
| `overrides_valor` | `ID_Fund, ID_Instrumento, SubID_Instrumento, Yield, Duration, Fecha_Desde, Fecha_Fin`. Pisa todo. |
| `overrides_atributo` | Mismo esquema que `EXCEPCIONES.xlsx` corporativo: `ID_Fund, ID_Instrumento, SubID_Instrumento, Field, Value, Fecha_Desde, Fecha_Fin`. |
| `alertas` | Reglas del motor de alertas (campo, operador, umbral, severidad). |
| `parametros` | Umbrales globales (`yield_max_proveedor`, `factura_tolerancia_monto`, `yield_type_default`, `ra_unico_factor` = 12: RA entrega los depósitos con TIR base 30 días, `yield_def` = 0 y `duracion_def` = 0.5 para DEF/PROPDEF, `factura_morosa_duration` = 0, …). |

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
`plantilla_cajas` · `plantilla_dim` (combinaciones de códigos sin Bucket / Ficha_FI / FX_Exposure en el formato de
`dim_clasificacion`, con MV, fondos y un ejemplo) · `cartera_final` · `candidatos` · `conversiones` · `curvas_drop` · `td_detalle` · `reglas_aplicadas` (qué fila de REGLAS actuó y
sobre cuántas posiciones) · `insumos`. Más `resumen_corrida_{FECHA}.json`.

## Facturas
Fuente principal: la base de Facts (`bi_facturas`, `bi_prorrogas`, `bi_cambios`) por túnel SSH + Postgres
(`adaptadores/facts.py`). La corrida baja las tres tablas completas y deja `04_CACHE/{FECHA}/facts_*.csv`; `--sin-facts` usa
solo la caché. Si no hay caché ni conexión se lee el RPT en Excel (`FACTURAS_{FECHA}.xlsx` en MERCADO) y, si tampoco,
`INSUMO_FALTANTE`. Cruce: el número del nombre en el CUBO (`FACRCPP59397`, `FACPP59397`, `FAC68135`) es el
`documento_operacion_id` de Facts y manda (Geneva antepone `RC` en MRCLP, así que el nemotécnico de Facts no está en HOMOL);
respaldo `nemotecnico → HOMOL_INSTRUMENTOS (GENEVA) → ID_Instrumento`; `fondo → HOMOL_FUNDS / BD_FUNDS → ID_Fund`.
Yield = `tasa_mensual × 12` (decimal, base 30 días), Duration = días al vencimiento vigente / 365. Morosa (vencida y no
pagada al cierre): tasa × 12 y duration `factura_morosa_duration` (0), alerta INFO `FACTURA_MOROSA`. Pagada el día del cierre
cuenta como viva (el CUBO aún la tiene); `fecha_inversion` posterior al cierre no excluye (INFO `FACTURA_COMPRADA_DESPUES`). Una
posición del CUBO que Facts tiene pagada antes del cierre queda FALTANTE con motivo `FACTURA_PAGADA_{fecha}`; el sufijo de
prórroga de Geneva (`FACRCPP58698PR1`) cruza con el documento original; las facturas vivas en Facts sin posición en el CUBO salen una a una en
`FACTURA_SIN_POSICION` (INFO, con fondo, estado y vencimiento).

Estado **al cierre** (`lectura/facts.facturas_al_cierre`): la base está viva, así que se revierten con `bi_cambios` los cambios
de tasa, vencimiento y monto posteriores al cierre (`FACTURA_ASOF_REVERTIDA`, INFO); una factura pagada después del cierre
sigue viva al cierre (viva = sin `fecha_pago` o pagada después); si hay prórroga iniciada al cierre o antes, mandan su tasa y
su vencimiento (`tasa_origen=PRORROGA`, alerta INFO `FACTURA_TASA_PRORROGA`; el RPT usa la tasa original). `monto_compra` ≠
cantidad del CUBO (nominal; el MV va a precio con devengo) → `FACTURA_MONTO_DISTINTO`. Conexión: `MONEDA_BI_PASSWORD` y `MONEDA_BI_SSH_KEY` en `.env` (nunca en git); host y
usuarios `FACTS_*` con los valores del proveedor en `.env.example`; `pip install -e .[facts]`; `reporteria facts-probar`
abre el túnel y cuenta filas sin correr el cierre.

## Desarrollo
```
pip install -e .[dev]
pytest -q
```
Los fixtures de `tests/fixtures/` salen de `tests/fixtures/construir_fixtures.py` (se corre una vez contra el repo
legacy) y se reemplazan por muestras reales cuando estén disponibles.
