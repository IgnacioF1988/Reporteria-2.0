# Plan — Reportería 2.0 recreada desde cero (TDD, operador-first)

## 1. Contexto

`Reporteria-2.0` calcula Yield y Duration por posición (fondo × PK2) para 9 fondos con una cascada de proveedores y conversiones (breakeven, drops). El diagnóstico previo (`PLAN_MODIFICACION.md`, rama `claude/plan-modificacion`) encontró bugs que alteran el resultado (defaulteados perdidos, duration de breakeven sin reexpresar, yields de bonos vencidos aceptadas, XCCY ignorado), credenciales en git, helpers duplicados, sin tests y sin convención de unidades.

**Decisión:** el repo actual es solo referencia funcional. Se recrea el pipeline desde cero con código minimalista, TDD estricto, `.env` para credenciales, alertas por reglas parametrizadas y una experiencia pensada para el operador que corre el cierre. Se agrega lo que falta: caja y equivalentes, fondos mutuos, pactos y simultáneas, facturas (fuente nueva), agregados a nivel activos / pasivos / patrimonio, y overrides de valores **y atributos por fondo**. No se inventan inputs: se usan los archivos tal como vienen en el repo; facturas según la descripción del usuario.

## 2. Decisiones de levantamiento (cerradas con el usuario)

| Tema | Decisión |
|---|---|
| Ubicación | Carpeta **`v2/`** dentro de este repo (paquete `reporteria`, `tests/`, `pyproject.toml`). Se migra a repo nuevo cuando exista. |
| Alcance v1 | **Todo el legacy** (EXCEPCIONES, JPM, RA, BBG YAS + XCCY, CSHF, JSONL, DEF, breakeven, drops, overrides, alertas, agregados) **más** caja/FFMM/pactos/simultáneas, facturas, agregados A/P/Patrimonio, overrides por fondo. |
| Universo | **CUBO completo**: todos los `Investment_Type_Code` (1 RF, 2 Equity, 3 Cash, 4 Pay/Rec, 5 Bank Debt, 6 Fund, 7 Derivative) y ambos `BalanceSheet`. Nada queda fuera en silencio. |
| Caja, FFMM, pactos, simultáneas | Yield/Duration por **reglas fijas** en REGLAS.xlsx. Caja = 0/0. FFMM, pactos y simultáneas: filas en la plantilla con Yield/Dur **vacías**; mientras estén vacías entran a yield 0 y se levanta alerta `REGLA_SIN_VALOR` con el MV afectado. |
| Facturas | `FACTURAS_{FECHA}.xlsx` en `01_INPUTS/MERCADO`: `PK2 \| Tasa_Mensual \| Monto \| Fecha_Vencimiento`. Tasa en % mensual (1.2 = 1,2 %). **Yield = Tasa × 12 / 100** (lineal). Duration = días al vencimiento / 365. Monto vs TotalMVal → alerta si difiere. |
| Parametrización | **Un único `REGLAS.xlsx`** en MANUALES (clasificación, defaulteados, overrides de valor, overrides de atributo, alertas, parámetros). **`ID_Fund` numérico** en todos los manuales nuevos (vacío = todos los fondos); nunca nombres. |
| Atributos sobreescribibles por fondo | Yield/Duration, **Bucket/Tratamiento** (ej. DAP = RF en MDCH, caja en MRCLP; excluir), **Hedge_Currency**, **Índice** (UF/UDI/UVR/CDI/TIIE/NOMINAL), **Estado DEF/PROPDEF**. |
| Agregados | AW = Σ(y·MV)/ΣMV y DW = Σ(y·MV·D)/Σ(MV·D) por fondo a nivel **ACTIVOS** (Σ Asset), **PASIVOS** (Σ \|Liability\|) y **PATRIMONIO** (A − P; yield = (A·y_A − P·y_P)/(A − P)). Todo lo sin métrica entra a yield 0 / dur 0. Pasivos conservan su métrica si la tienen. |
| Taxonomía de buckets | **Pendiente**: el usuario sube archivos con el tratamiento actual; de ahí se fija la lista. Mientras, taxonomía provisional (§4.2). El mecanismo no cambia. |
| Período anterior | Se **corre el pipeline nuevo sobre 20260731** (inputs ya en el repo) como semilla; agosto compara contra eso. |
| Operador | CLI Windows: `reporteria check / correr --fecha … [--sin-bbg]`. Salida: un Excel por cierre + log. Python 3.11+, xbbg y pyodbc en su máquina. |
| Fixtures | El usuario deja en `v2/tests/fixtures/` muestras reales de CUBO, BD_INSTRUMENTOS, BD_FUNDS y HOMOL. Hasta entonces, fixtures reconstruidos desde `UNIVERSO_20260731`, `Cajas_jul.xlsx`, `Otros/Fondos_jul.xlsx`. |
| Unidades | **Decimal** en todo el código y archivos; % solo como `number_format` en Excel. Validación en lectura y assert final. |
| Política hedgeados | `XCCY_SI_EXISTE` (parámetro); Yield_Drop siempre se calcula y se compara. |

## 3. Arquitectura (`v2/`)

```
v2/
├── pyproject.toml          # pandas numpy scipy openpyxl python-dotenv typer; extras [bbg]=xbbg [sql]=pyodbc [dev]=pytest
├── .env.example            # BEE_SERVER BEE_DB BEE_UID BEE_PWD RUTA_CUBO_DIR RUTA_BIX REPORTERIA_RAIZ
├── .gitignore              # .env 02_OUTPUTS/ 03_LOGS/ 04_CACHE/ ~$*
├── README.md · FUTURO.md
├── reporteria/
│   ├── config.py           # Rutas(fecha) desde .env; FONDOS {id: alias, base_ccy, política hedge}; mapeos técnicos (curvas, tickers, candidatos de escala) — copiados de legacy pipeline_config.py
│   ├── modelo.py           # ESQUEMA_POSICIONES / ESQUEMA_CANDIDATOS (columna→dtype→unidad), validar(), limpiar_txt(), pos_id(), pct_a_dec(), assert_decimal()
│   ├── lectura/            # una función pura por archivo → DataFrame en unidades canónicas
│   │   ├── cubo.py  maestros.py  reglas.py  manuales.py (excepciones, atributos, facturas)  mercado.py (jpm, ra, curvas csv, paridades)  geneva.py (jsonl, homol)
│   ├── adaptadores/        # lo único con clases: fronteras externas
│   │   ├── bbg.py          # Protocol Bloomberg{bdp,bds,bdh}; XbbgBloomberg; CacheBloomberg(inner, dir); FixtureBloomberg(dir)
│   │   └── fx_sql.py       # Protocol FuenteFx; BeeminingFx (.env, query parametrizada); CacheFx; FixtureFx
│   ├── universo.py         # armar_universo(cubo, bd_instr, bd_funds, fondos, reglas, cartera_ant) → posiciones
│   ├── clasificacion.py    # clasificar(pos, reglas.clasificacion) → Bucket, Tratamiento, Regla_ID
│   ├── finanzas.py         # xirr, duracion, interp_curva, breakeven, reexpresar_moddur, drop  (puras)
│   ├── escala.py           # candidatos_fx, evaluar_escala, elegir_escala_fx  (puras)
│   ├── td.py               # clasificar_geneva, construir_td_geneva, normalizar_td_bbg, td_desde_excepcion
│   ├── fuentes/            # candidatos_<fuente>(pos, insumos, settle, cfg) → (candidatos, tds); sin efectos colaterales
│   │   ├── reglas_fijas.py facturas.py excepciones.py jpm.py ra.py bbg_yas.py cshf.py jsonl.py
│   ├── cascada.py          # validar_candidatos (sanidad), elegir (orden por Tratamiento), pendientes_tras (ahorro terminal)
│   ├── conversion.py       # aplicar_breakeven, aplicar_drops (+ política XCCY)
│   ├── overrides.py        # aplicar_overrides_atributo (antes de fuentes), aplicar_overrides_valor (al final)
│   ├── alertas.py          # columnas_derivadas(pos, ant, parametros); evaluar(pos, reglas.alertas) → alertas
│   ├── agregados.py        # aw_dw(pos) → por fondo × {ACTIVOS, PASIVOS, PATRIMONIO} × grupo
│   ├── pipeline.py         # correr(fecha, rutas, bbg, fx, opciones) → Resultado(dataclass)
│   ├── salida.py  log.py  cli.py   # Excel final, logging, typer (check, correr, reglas-validar, importar-cache-legacy, migrar-manuales)
└── tests/  (conftest, fixtures/, test_e2e_esqueleto, test_<módulo>, test_invariantes, test_golden_20260731)
```

**Qué NO va:** ORM, clases de dominio, estado global, `print`, lectura de outputs intermedios entre etapas (todo en memoria; intermedios solo para auditoría), credenciales o rutas UNC en código, heurísticas de unidad por fila.

### 3.1 Modelo de datos
- **`posiciones`**: una fila por `Pos_ID = ID_Fund|PK2|BalanceSheet` (duplicados del CUBO se suman + alerta `CUBO_DUPLICADO`). Grupos de columnas: identidad, atributos de maestro, familia REGS/144A, valores CUBO crudos, valores resueltos (`sP, sQ, FX, P_ef, Q_real, AI_local, FI_local, Escala_Flag`), clasificación (`Bucket, Tratamiento ∈ {CASCADA, FIJO, FACTURA, CERO, EXCLUIR}, Regla_ID`), atributos overrideables (`Hedge_Currency, Indice, Estado_DEF` + origen), resultado (`Yield, Duration, Fuente, Origen, Yield_Moneda, Etapa, Estado ∈ {RESUELTO, FALTANTE, EXCLUIDO}, Motivo`), conversión (`Yield_Papel, Yield_Local, Yield_Drop, Yield_XCCY, Dif_XCCY_Drop_bps, Extrapolado`), período anterior (`*_ant`).
- **`candidatos`** (tabla larga, una fila por posición × fuente): `Yield, Duration, Yield_Moneda, Origen, Tupla_Completa, Valido, Motivo_Descarte, Detalle`. Toda fuente deja rastro, incluidas las descartadas.
- **`tds`**, **`alertas`**, **`agregados`** como tablas auxiliares → hojas del Excel.

### 3.2 `REGLAS.xlsx` (MANUALES) — todas las hojas con `ID_Fund` (vacío = todos)
| Hoja | Columnas | Semántica |
|---|---|---|
| `clasificacion` | `ID \| ID_Fund \| Criterio \| Valor \| Bucket \| Tratamiento \| Yield \| Duration \| Comentario` | `Criterio ∈ {PK2, Issue_Type_Code, Investment_Type_Code, Nombre_Regex, Source, BalanceSheet}`. Precedencia: especificidad del criterio (PK2 > Regex > Issue_Type > Investment_Type > Source/BalanceSheet) y regla por fondo > global; empate → primera por `ID` + alerta `REGLA_AMBIGUA`; sin match → `SIN_REGLA` + alerta. `FIJO` con Yield/Dur vacíos → yield 0 + `REGLA_SIN_VALOR`. |
| `defaulteados` | `ID_Fund \| PK2 \| Estado ∈ {DEF, PROPDEF} \| Comentario` | Yield 0, Dur 0.5, `Etapa=REGLA_DEF`; no consulta fuentes; **sí entra en agregados**. Migra de `DEFAULTEADOS.xlsx`. |
| `overrides_valor` | `ID_Fund \| PK2 \| Yield \| Duration \| Moneda \| Fuente \| Comentario \| Vigente_Desde \| Vigente_Hasta` | Yield decimal (|Yield| > 1.5 = error de validación). Pisa todo al final. |
| `overrides_atributo` | `ID_Fund \| PK2 \| Atributo ∈ {Hedge_Currency, Indice, Estado_DEF, Bucket, Tratamiento} \| Valor \| Comentario` | Se aplica antes de las fuentes. `Valor=SIN_HEDGE` anula hedge. `Hedge == Risk_Currency` → se ignora + alerta. |
| `alertas` | `ID \| Nombre \| Campo \| Operador \| Umbral \| Severidad \| ID_Fund \| Activa \| Requiere_Anterior \| Ambito \| Descripcion` | Ver §3.3. |
| `parametros` | `Clave \| Valor \| Descripcion` | `yield_max_proveedor=1.0`, `yield_min_proveedor=-0.5`, `politica_hedge=XCCY_SI_EXISTE`, `xccy_drop_max_bps=50`, `cobertura_min_mv=0.95`, `yield_alta_default=0.25`, `yield_alta_MLDL=0.40`, `factura_tolerancia_monto=0.01`, umbrales de escala. |

Ejemplos de `clasificacion` (provisional; los buckets se ajustan con los archivos del usuario): `Investment_Type_Code=3 → CAJA, FIJO, 0, 0` · `Investment_Type_Code=6 → FONDO_MUTUO, FIJO, (vacío)` · `Nombre_Regex ^SIM_ → SIMULTANEA, FIJO, (vacío)` · `Issue_Type_Code=5 → FACTURA, FACTURA` · `Issue_Type_Code=4 → DAP, CASCADA` global y `ID_Fund=20, Issue_Type_Code=4 → CAJA, FIJO, 0, 0` (MRCLP) · `ID_Fund=20, PK2=176142-38 → EQUITY, EXCLUIR` (FIP Treatment Equity). Con esto `FIP.xlsx`, `DEFAULTEADOS.xlsx`, `OVERRIDES.xlsx` y `Cajas_jul.xlsx` dejan de ser inputs (comando `migrar-manuales` los convierte una vez, resolviendo alias de fondo → `ID_Fund`).

### 3.3 Motor de alertas
`evaluar()` aplica, por regla activa, un operador vectorizado (`>= <= > < == != abs>= in es_nulo no_nulo es_verdadero`) sobre una columna de `posiciones` o una **columna derivada** (lista cerrada en `alertas.columnas_derivadas`, documentada en README). Campo inexistente = error de validación. Severidad ∈ {CRITICA, ALTA, MEDIA, INFO}; Ámbito ∈ {POSICION, FONDO, CORRIDA}; `Requiere_Anterior=SI` sin corrida previa → `INACTIVA` + INFO.

Semilla (rescate legacy F1–F7 + nuevas): A01 yield ≥ umbral (0.25 / 0.40 MLDL) · A02 yield negativa · A03 default con AI>0 · A04 no-default con AI=0 (solo bonos) · A05 precio↑ y yield↑ (ant) · A06 métricas idénticas al mes anterior (ant) · A07 Δyield ≥ 10 pp (ant) · A08 métrica de proveedor inválida (dur ≤ 0, vencido, fuera de rango) · A09 cobertura MV por fondo < 95 % · A10 |XCCY − Drop| ≥ 50 bps · A11 extrapolación fuera de curva · A12 escala/FX en fallback · A13 monto factura ≠ TotalMVal · A14 PK2 duplicado entre EXCEPCIONES · A15 `SIN_REGLA` · A16 FALTANTE con MV · A17 hedge = moneda del papel · A18 insumo opcional ausente · A19 sin maestro · A20 `REGLA_SIN_VALOR` · A21 `CUBO_DUPLICADO`. F4 del legacy (cupón negativo) nunca existió: se documenta en FUTURO.

### 3.4 Adaptadores externos
- `Bloomberg` Protocol (`bdp`, `bds`, `bdh`). `CacheBloomberg(XbbgBloomberg())` en producción: toda corrida en vivo deja caché CSV en `04_CACHE/{FECHA}/bbg/`; `--sin-bbg` usa `FixtureBloomberg` (solo caché; lo que falta queda FALTANTE + alerta). Tests nunca importan `xbbg`/`pyodbc`.
- `FuenteFx` Protocol; `BeeminingFx` lee credenciales de `.env` y usa query parametrizada; caché `fx_beemining.csv`. Paridades Excel siempre desde archivo.
- `importar-cache-legacy --fecha 20260731 --legacy ..` siembra la caché desde `METRICAS` (BBG_Yield/Duration/XCCY), `CSHF/td_detalle` (DES_CASH_FLOW), `CURVAS_DROPS_20260731.csv` (CURVE_TENOR_RATES) y los `FX_usado` de CSHF/JSONL/EXCEPCIONES. Así 20260731 corre completo sin terminal.

### 3.5 Operador
- `reporteria check --fecha F`: inputs OK/FALTA/OPCIONAL, `.env`, REGLAS válido, estado de caché, antigüedad del jsonl. `reporteria correr --fecha F [--sin-bbg] [--sin-sql] [--fecha-ant] [--solo-hasta etapa]`. Exit codes: 0 OK · 1 OK con CRITICA · 2 input obligatorio/REGLAS inválido · 3 error.
- Excel `02_OUTPUTS/{FECHA}/REPORTE_{FECHA}.xlsx`: `resumen`, `agregados`, `cartera_final`, `candidatos`, `alertas` (+ `alertas_resumen`), `faltantes`, `plantilla_overrides` (prellenada, copy/paste a REGLAS), `conversiones`, `escala_fx`, `td_detalle`, `insumos`, `reglas_aplicadas`. Más `resumen_corrida.json`. Log `03_LOGS/{FECHA}/corrida_{ts}.log` con conteos por etapa.
- Falta de inputs: CUBO/BD/REGLAS = fatal; el resto se omite con alerta A18 al inicio y al final del log.

## 4. Estrategia TDD

### 4.1 Patrón de levantamiento por módulo (se repite en cada hito)
1. Preguntas cerradas al usuario solo si cambian el resultado; el resto se decide y se anota en `README` §Supuestos.
2. Tabla de decisiones → casos de prueba (nominal, borde, inválido) escritos **antes** del código.
3. Implementación mínima que pasa; refactor; `pytest -q` verde antes de avanzar al siguiente módulo.

### 4.2 Orden (walking skeleton primero)
0. `test_e2e_esqueleto`: CUBO mini de 12 filas (caja, payable, derivado, equity, bono JPM, bono RA, DEF, factura, excepción, FIP excluida, pasivo, bono sin fuente) → `pipeline.correr` con stubs devuelve 12 posiciones, ΣMV == CUBO, estados esperados, Excel escrito, alerta FALTANTE.
1. `modelo` → 2. `lectura` (cubo, maestros, **reglas** con 8 casos inválidos) → 3. `universo` → 4. `clasificacion` → 5. `reglas_fijas`, `facturas`, `cascada` → 6. `finanzas` → 7. `escala` → 8. `excepciones` → 9. `jpm`, `ra` → 10. `adaptadores/bbg` + `bbg_yas` → 11. `td` + `cshf` → 12. `geneva` + `jsonl` → 13. `conversion` → 14. `overrides` → 15. `alertas` → 16. `agregados` → 17. `salida`, `log`, `cli`, `pipeline` → 18. `test_golden_20260731`.

### 4.3 Fixtures (desde archivos reales del repo; se reemplazan por las muestras del usuario cuando lleguen)
`cubo_mini` (~60 filas de `Otros/Fondos_jul.xlsx` incl. casos de referencia y los 11 duplicados) · `cubo_completo_20260731` (2.878 filas) · `bd_instrumentos_*` reconstruido de `UNIVERSO` + `Cajas_jul` · `bd_funds_mini` · `homol_mini` desde `PROP_JSONL/metricas` · `bond_schedule_mini.jsonl` (RECARR, CASH URUGUA, PATIO PERU, FUNOMM, SOLFACIL ×2, AGROVISION, CONMEX, TOWER ONE, BADAL-A) · `jpm_mini`, `ra_tir_mini` · curvas CSV íntegras · `bbg_cache_20260731/` vía importar-cache-legacy · `fx_beemining_20260731.csv` · `paridades_mini` · `excepciones_mini` (incl. header `hor`, Liability, PK2 duplicado, PIK) · `facturas_mini` (formato nuevo) · `reglas_mini` / `reglas_20260731` · `atributos_mini` · `golden/cartera_final_20260731.csv` + `breakeven` + `drops` desde los outputs legacy.

### 4.4 Casos numéricos de referencia (tests unitarios)
RECARR primer cupón completo (ancla último cupón pagado, 182 días) · CASH URUGUA Factor 0.3152 (Σ capital × OF/100 == Q_real; cupón sobre saldo vigente) · PATIO PERU Factor 0.95 en dos fondos con FX distinto · caso ARS bee 1382 vs paridades 1471 (gana el que calza) · empate (0.001,1000) vs (1,1) resuelto por ratio, contraste AGROVISION · USDCLP contable 930.86 vs 924.78 → FALLBACK + A12 · breakeven BAARA-B: `Yield_Local=0.063554` y **`Duration_Local=4.1714`** (no 4.305) · drop AES 2034 CLP: `Yield_Drop=0.062862`, XCCY 0.065465, Dif −26 bps, política XCCY · facturas: tasa 0.8 → 0.096, 45 días → 0.1233, monto 2 % ≠ → A13 · XIRR bono par 5 % semestral → 0.050625 · interpolación flat + `Extrapolado` · DEF no consume BBG.

### 4.5 Invariantes (sobre CUBO completo)
`len(posiciones) == CUBO dedup` · `ΣTotalMVal` por (fondo, BalanceSheet) == CUBO · todo `Pos_ID` con Bucket y Tratamiento · `RESUELTO ⇒ Yield y Duration` · `Yield ∈ [-0.5, 1]` salvo OVERRIDE · ninguna columna de yield con mediana > 1.5 · cada `Fuente` elegida existe como candidato válido · `MV_PATRIMONIO == MV_ACTIVOS − MV_PASIVOS` · pesos por bucket suman 1 · BBG solo consultado para pendientes y nunca para DEF (espía que cuenta tickers).

## 5. Hitos y metas de aprobación

| Hito | Entregable | Se aprueba solo si |
|---|---|---|
| **H0 Esqueleto** | `v2/` con pyproject, .env.example, config, modelo, CLI `check`, walking skeleton | `pytest` verde; `reporteria check` corre en Linux sin xbbg/pyodbc; `git grep` no encuentra credenciales |
| **H1 Universo + reglas** | lectura, universo, clasificación, reglas fijas, facturas, cascada básica, Excel básico, `migrar-manuales` | invariantes sobre 2.878 filas; `SIN_REGLA = 0` con `reglas_20260731`; 472 facturas y 194 DEF identificados; taxonomía fijada con los archivos del usuario |
| **H2 Fuentes de archivo** | JPM, RA, EXCEPCIONES, sanidad, `candidatos` | golden: yields JPM/RA/EXCEPCIONES ±1 bp y dur ±0.001 vs legacy; ≥ 5 descartes de sanidad documentados (TRNTEL, CZRSBZ, PDCAR, INTSPN, DOCUFO); 8 errores de validación de REGLAS cubiertos |
| **H3 Motor financiero + TD** | finanzas, escala, td, adaptador BBG con caché, cshf, jsonl, `importar-cache-legacy` | 12 casos numéricos verdes; CSHF reproduce las posiciones legacy con `Escalar_flag OK` ±1 bp; JSONL reproduce las 10; espía BBG confirma ahorro |
| **H4 Conversiones + overrides** | conversion, overrides (valor/atributo, vigencia) | 243 breakeven `Yield_Local` ±1 bp y duration reexpresada; 106 drops ±1 bp; política XCCY y `Dif_XCCY`; `Hedge == Risk_Currency` no rompe |
| **H5 Alertas, agregados, salida** | alertas, agregados, salida, log, pipeline completo | `correr --fecha 20260731 --sin-bbg` < 3 min con Excel completo; 21 reglas activas o INACTIVAS con motivo; `MV_PAT == MV_ACT − MV_PAS`; golden completo verde |
| **H6 Producción** | corrida en vivo (Windows + terminal), README, FUTURO.md, migración a repo nuevo | corrida con BBG deja caché y `--sin-bbg` reproduce idéntico; julio sembrado activa A05–A07 en agosto; operador completa el checklist sin ayuda |

## 5b. Estado y diseño detallado de H3 (Bloomberg, CSHF, JSONL) — a implementar

**Hecho:** H0–H5 y H6a en `v2/` (migrar-manuales, check extendido, comparar, CHECKLIST_CIERRE.md). Pendiente H6b: repo nuevo, corrida real en Windows, rotación de credencial. H5: motor de alertas por reglas (`alertas.evaluar`, derivadas, umbral por fondo, `param:`, ajuste de estructurales), `agregados.aw_dw` A/P/Patrimonio con verificación, Excel completo (13 hojas), segunda corrida con cierre anterior activa A05–A07. H4: `indices.py`, `curvas.py`, `conversion.py`, `overrides.aplicar_valores`; goldens 243 breakeven y 106 drops ±1 bp; la duration del breakeven se reexpresa (BAARA-B 4.305 → 4.171); `Dif_XCCY_Drop_bps = XCCY − drop`; caché sembrada con CURVAS_DROPS y ATRIBUTOS del legacy. La muestra real (1.000 posiciones, 14 fondos) resuelve 844; faltan 155 de renta fija que dependen de Bloomberg o de TD propias.

**Insumos ya copiados a `v2/tests/fixtures/corporativo/`:** `LEGACY_METRICAS_20260731.xlsx` (BBG_Yield/BBG_Duration/BBG_XCCY_Yield por PK2 con `Origen_BBG_*` DIRECTO/BGN/HERMANO y `Hedge_Currency`), `LEGACY_CSHF_20260731.xlsx` (`resueltos` + `td_detalle` con `Flujo_baseFACE`, `face_bbg` por ISIN), `LEGACY_PROP_JSONL_20260731.xlsx`, `bond_schedule.jsonl` (270 records). Solapamiento con la muestra: 208 tuplas BBG (62 XCCY), 9 CSHF (6 con escala OK), 45 códigos GENEVA en el jsonl.

**Adaptador Bloomberg** (`adaptadores/bbg.py`, ya existe con `bdp`/`historico`): agregar `bds(ticker, campo, **overrides) -> DataFrame` con caché `bds_{campo}/{ticker_sanitizado}.csv`. `FixtureBloomberg` solo lee; `CacheBloomberg` completa lo que falta; `XbbgBloomberg` usa `blp.bds`. Tickers de curvas (`YCSW… Index`) quedan para H4.

**`fuentes/bbg_yas.py`:** solo para posiciones pendientes tras EXCEPCIONES/JPM/RA (`cascada.pendientes_tras`) con ISIN y tratamiento CASCADA/CAJA. Campo de yield según `Yield_Type` (`config.YIELD_TYPE_BBG`: 15 → `YAS_BOND_YLD`, 1 → `YAS_YLD_MATURITY`, 2 → `YAS_YLD_CALL`, 28 → `YAS_YLD_AVG_LIFE`; 0/vacío → default + alerta INFO) y `YAS_MOD_DUR`, con `settle_dt=FECHA`. Cascada de tickers: `{ISIN} Corp` → `{ISIN}@BGN Corp` → hermanos (`FAMILIA_INFERIDA`). Para hedgeados además `YAS_XCCY_FIXED_COUPON_EQUIVALENT` con override `YAS_XCCY_FOREIGN_CURRENCY=Hedge_Currency` → columna `Yield_XCCY` en posiciones (no es candidato: la política XCCY vs drop se decide en H4). Valores en % → decimal. Sanidad ya existente descarta PDCAR (65.011 %) y bonos vencidos con duration 0. Espía en tests: no se pide nada ya resuelto ni DEF.

**`fuentes/cshf.py`:** pendientes tras BBG, con ISIN: `bds DES_CASH_FLOW` (`Corp` → `Govt` → hermanos) con `SETTLE_DT`, `BQ_FACE_AMT=1000`. `td.normalizar_td_bbg` → `Fecha, Cupon, Principal, Flujo, Face` (Face = Σ Principal; si el caché legacy no trae principal, usa `face_bbg`). Escala igual a EXCEPCIONES pero `Q_real = OF_base × sQ × Factor`, `escala = Q_real / Face`, flujos futuros > settle; FI = −(P_ef × Q_real) − AI_local; XIRR y duración. Frecuencia inferida por mediana de días entre flujos (solo informativa).

**`td.py` + `fuentes/jsonl.py`:** portar `clasificar` (ZERO_COUPON / SINKABLE / BULLET / FLOTANTE / REVISAR_252 / SIN_TASA / SIN_FLUJOS; ratio cupón observado vs `Interest Rate` sobre cupones PASADOS en [0.85, 1.15]; dc=252 → revisión) y `build_td_geneva` (escala `OF_ef/100` porque el Factor ya viene en los flujos; ancla del devengo en el ÚLTIMO CUPÓN PAGADO; cupón recalculado sobre saldo vigente; periodicidad inferida por fechas porque `Coupon Frequency` viene siempre 2; sinks + residual al vencimiento). Búsqueda del record: HOMOL(GENEVA) por `ID_Instrumento` → ISIN → nombre. Tipos no modelables → candidato inválido con motivo (`FLOTANTE_SIN_INDICE`, `REVISAR_252`, …). Moneda de la métrica = `Risk_Currency` (no el `_Ccy_` del evento). Sanidad de AI (`AI_ratio`) como alerta `AI_SOSPECHOSO`.

**`legado.py` + CLI `importar-cache-legacy --fecha --legacy DIR`:** siembra `04_CACHE/{FECHA}/`: `bdp_YAS_BOND_YLD_{F}_settle_dt-{F}.csv` y `bdp_YAS_MOD_DUR_…` con ticker según origen (DIRECTO → `{ISIN} Corp`, BGN → `{ISIN}@BGN Corp`, HERMANO:h → `{h} Corp`), `bdp_YAS_XCCY_FIXED_COUPON_EQUIVALENT_{F}_settle_dt-{F}_YAS_XCCY_FOREIGN_CURRENCY-{CCY}.csv` por moneda de hedge, `bds_DES_CASH_FLOW/{ISIN} Corp.csv` desde `td_detalle` (Flujo, Face), y `fx_beemining_{F}.csv` si no existe. `construir_fixtures.py` lo usa para `mini/bbg_cache`.

**Fixtures nuevos:** `mini/bond_schedule.jsonl` recortado a los códigos GENEVA de la muestra + los casos documentados; `casos_legacy/` con las filas de `Otros/Fondos_jul.xlsx` para CASH URUGUA `201936-175` (F 17), PATIO PERU `166894-135` (F 17 y 20), AGROVISION `189238-1`, CONMEX `29844-194`, Titularice `153765-41`, RDEDOR `991-29`, y su BD/HOMOL recortados, para el golden de JSONL.

**Tests y metas H3:** (1) adaptador: caché de `bds`, fixture vacío → sin dato; (2) `bbg_yas`: cascada de tickers, campo por `Yield_Type`, XCCY solo hedgeados, espía confirma que no se consulta lo resuelto ni DEF; (3) `cshf`: face derivado, flujos futuros, escala, golden 6 posiciones OK ±1 bp; (4) `td`: CASH URUGUA (Σ capital × OF/100 == Q_real; yield 0.143574, dur 0.078704), PATIO PERU (dos fondos ≈ 0.0905), AGROVISION zero (escala (0.001,1000) OK), RECARR (primer cupón completo, 182 días), SOLFACIL → REVISAR_252; (5) e2e: BBG resuelve todo lo pendiente cuyo ISIN está en caché (87 válidas + 6 descartadas por sanidad de 155 pendientes; el resto tampoco tenía BBG en el legacy), `Yield_XCCY` solo en hedgeadas del fondo (12; el legacy mapeaba XCCY por ISIN y contagiaba fondos sin hedge), faltantes de renta fija con ISIN en caché = exactamente los descartados por sanidad; (6) `importar-cache-legacy` reproduce la caché desde los LEGACY_* y `correr --sin-bbg` corre sin terminal.

## 5c. H6a — Preparar la corrida en producción (punto 2, sin esperar el repo nuevo)

**Contexto.** H0–H5 corren sobre la muestra sin terminal. Antes de la corrida real en Windows hace falta (a) convertir los manuales del legacy al `REGLAS.xlsx` con `ID_Fund`, (b) que `check` diga si la máquina está lista (`.env`, xbbg/pyodbc, caché, antigüedad del jsonl, manuales legacy sin migrar), (c) una forma de comprobar que la corrida con terminal y la `--sin-bbg` posterior dan lo mismo, y (d) un checklist que el operador siga sin ayuda. Nombre del repo nuevo y rotación de la credencial quedan para H6b.

### Insumos legacy encontrados (todos en `/home/user/Reporteria-2.0`)
| Archivo | Formato | Destino en REGLAS |
|---|---|---|
| `01_INPUTS/MANUALES/FIP.xlsx` | `Fund_Name, PK2, Name_Instrumento, Treatment ∈ {Equity, Bond}` (2 filas, MRentaCLP) | `clasificacion`: `Treatment=Equity` → fila `ID_Fund, Criterio=PK2, Valor=PK2, Bucket=Equity`; `Bond` no genera fila (es el default) |
| `01_INPUTS/GENEVA/Atributos_MLDL.xlsx` | `Fund_Name, PK2, …, Index (YES/NO), Index Name` (13 filas; nombres COCPI, CER, CDI, UI Curncy, UVR COSTER, MXIBTIIE) | `overrides_atributo`: `Field=Indice, Value=<índice normalizado>` por fondo × PK2 (solo `Index=YES`). Agregar a `config.ALIAS_INDICE`: `COCPI→UVR`, `MXIBTIIE→TIIE` |
| `tests/fixtures/corporativo/Template_Cajas.xlsx` (corporativo) | hojas `Fondos USD`, `MLDL`, `Fondos CLP` con `Indice Referencia, Spread (Anual), Fecha_Vencimiento` | `cajas` (la lógica ya está en `construir_fixtures.py`: se mueve a `legado.migrar_cajas` y el fixture la reusa) |
| `01_INPUTS/MANUALES/DEFAULTEADOS.xlsx` | `Fondo, PK2, Instrumento, ISIN, DEF ∈ {DEF, PROPDEF}` (176 filas) | `defaulteados` **solo con `--incluir-defaulteados`** (decisión R5: el corporativo `DEFAULTED.xlsx` es la fuente; el legacy no se usaba) |
| `MANUALES/OVERRIDES.xlsx` (no existe en el repo; formato conocido por `OVERRIDES_PENDIENTE`: `Fund_Name, PK2, Name_Instrumento, Yield, Dur, Currency, Source, Comment`) | se soporta si aparece | `overrides_valor` con `Moneda=Currency`, `Fuente=Source`; `|Yield| > 1` se asume % y se divide por 100 con aviso |
| `Cajas_jul.xlsx` (raíz) | inventario de cajas sin índice ni spread | **no se migra** (no aporta valores); se reporta |

Resolución de fondo: `Fund_Name`/`Fondo` (upper) contra `BD_FUNDS.FundShortName`, `BD_FUNDS.NombreTupungato` y `HOMOL_FUNDS.Portfolio` (`M.leer_homol_funds`). `PK2` → `ID_Instrumento`/`SubID_Instrumento` con `split("-")`; se avisa si el `ID_Instrumento` no está en `BD_INSTRUMENTOS` (no bloquea).

### Cambios
1. **`reporteria/legado.py`** — `migrar_manuales(legacy: Path, bd_funds, homol_funds, bd_instr_ids: set[int] | None, incluir_defaulteados=False) -> tuple[dict[str, DataFrame], DataFrame]`: devuelve `{hoja: filas nuevas}` en el esquema de cada hoja de REGLAS (`Comentario="migrado de <archivo>"`) y una tabla `informe` (`Archivo, Filas_leidas, Filas_generadas, Avisos`). Busca los archivos en `legacy`, `legacy/01_INPUTS/MANUALES`, `legacy/01_INPUTS/GENEVA`. Helper `fusionar_reglas(path_reglas, nuevas, salida)`: lee todas las hojas de REGLAS, agrega las filas nuevas evitando duplicados por llave natural (`clasificacion`: ID_Fund+Criterio+Valor; `cajas`: ID_Fund+PK2; `defaulteados`: ID_Fund+ID_Instrumento; `overrides_*`: ID_Fund+ID_Instrumento+SubID+Field), renumera `ID` de `clasificacion`, valida con `leer_reglas` y escribe. Reusar `limpiar_txt`, `normalizar_indice` (`indices.py`), `leer_bd_funds`, `leer_homol_funds`.
2. **`reporteria/cli.py`**
   - `migrar-manuales --fecha F --legacy DIR [--aplicar] [--incluir-defaulteados] [--raiz]`: sin `--aplicar` escribe `01_INPUTS/MANUALES/REGLAS_migracion_{ts}.xlsx` (solo las filas nuevas, una hoja por destino + `informe`) para revisar y pegar; con `--aplicar` fusiona en `REGLAS.xlsx` dejando respaldo `REGLAS_backup_{ts}.xlsx`. Imprime el informe.
   - `check` extendido: `.env` (existe; variables `BEE_*`/`RUTA_*` presentes sí/no, sin imprimir valores), `xbbg`/`pyodbc` importables (si no → “correr con --sin-bbg / --sin-sql”), caché `04_CACHE/{F}`: n° de `bdp_*`, `bdh_*`, carpetas `bds_*`, si existe `YAS_BOND_YLD`; antigüedad de `bond_schedule.jsonl` en días (aviso > 35); REGLAS: filas por hoja y reglas de alertas activas; manuales legacy sin migrar detectados en MANUALES/GENEVA (`FIP.xlsx`, `DEFAULTEADOS.xlsx`, `Atributos_*.xlsx`, `OVERRIDES.xlsx`) → sugiere `migrar-manuales`. Exit 2 solo por obligatorios/REGLAS inválido.
   - `comparar --fecha F --otro RUTA_REPORTE [--raiz]`: compara `cartera_final` del reporte del cierre con otro reporte (p. ej. corrida con terminal vs `--sin-bbg`): posiciones solo en uno, y diferencias de `Yield` (> 1e-9), `Duration`, `Fuente`, `Conversion`; escribe `02_OUTPUTS/{F}/comparacion_{ts}.csv`; exit 0 si no hay diferencias, 1 si hay. Función pura `salida.comparar_carteras(a, b) -> DataFrame` para testear.
3. **`reporteria/config.py`**: `ALIAS_INDICE` += `COCPI`, `MXIBTIIE`; `Rutas.atributos` también busca en GENEVA (respaldo legacy) — solo para que `check` los detecte; el pipeline no los lee (la fuente ATRIBUTOS sigue en FUTURO 3).
4. **`v2/CHECKLIST_CIERRE.md`** (operador, Windows): instalar (`pip install -e .[bbg,sql]`), `.env` desde `.env.example`, opcional `importar-cache-legacy` y `migrar-manuales --aplicar` la primera vez, `check`, `correr` con terminal, copiar el reporte a `REPORTE_{F}_terminal.xlsx`, `correr --sin-bbg`, `comparar`, qué mirar en `alertas_resumen`/`plantilla_overrides`, dónde quedan log y caché, códigos de salida. README enlaza el checklist.
5. **Fixtures/tests**: copiar a `tests/fixtures/corporativo/legacy_manuales/` `FIP.xlsx`, `DEFAULTEADOS.xlsx`, `Atributos_MLDL.xlsx` (como están). `construir_fixtures.py` pasa a usar `legado.migrar_cajas`.
   - `test_legado.py`: `migrar_manuales` → 1 fila `clasificacion` (fondo 20, PK2 176142-38, Equity), 9 filas `overrides_atributo` Indice (CER→BONCER, COCPI→UVR, UI Curncy→UI, UVR COSTER→UVR, MXIBTIIE→TIIE, CDI), 0 `defaulteados` sin flag y 176 con flag con `ALTURAS II`→2 y fondos desconocidos reportados en `informe`; `fusionar_reglas` no duplica al correr dos veces y el resultado pasa `leer_reglas`.
   - `test_cli.py`: `check` imprime líneas de caché, jsonl y `.env`; `migrar-manuales` sin `--aplicar` deja `REGLAS_migracion_*.xlsx`, con `--aplicar` deja backup y REGLAS válido; `comparar` de un reporte contra sí mismo → exit 0 y 0 diferencias; contra uno con una yield cambiada → exit 1 y 1 fila.
   - `test_golden_h5` sigue verde (REGLAS del fixture no cambia salvo que `cajas` venga de `migrar_cajas`, misma salida).

### Verificación
`cd v2 && pytest -q` (≈110 tests) · `python -m reporteria.cli check --fecha 20260731 --raiz <tmp>` muestra caché/jsonl/.env · `migrar-manuales --fecha 20260731 --legacy .. --raiz <tmp>` produce el Excel de migración con el informe · `correr --sin-bbg` y `comparar` contra el mismo reporte → exit 0.

## 5d. H6c — Ajustes tras la corrida real 20260731 (commit d690112, revisado desde el remoto)

**Resultado de la corrida:** 7.197 posiciones / 49 fondos, 3.358 RESUELTO, corrida con terminal == `--sin-bbg` (0 diferencias). Cobertura MRCLP 84 % de activos (legacy: 76 %; los mismos papeles sin ISIN —MHE, CLH, PAG, P$_CON, FIP, VC— también eran FALTANTE en el legacy). Lo que sigue son cambios de código pequeños (1–4) y decisiones del usuario (5–8).

### Cambios de código (TDD, mismos módulos)
1. **`fuentes/ra.py` — TIR mensual de depósitos.** RA entrega `PERIODICIDAD_CUPONES=UNICO` con TIR base 30 días (BNPDBC040826 → 0,3680 ≈ 4,4 % anual). Hoy (y en el legacy) entra como 0,368 % anual. Regla: `UNICO` → `yield = TIR/100 × 12` (nominal anual, convención chilena 30/360), `Detalle="tir_mensual_x12"`, alerta INFO `RA_TIR_MENSUAL`. Parámetro `ra_unico_factor=12` en `parametros` por si RA cambia la base. Test: BNPDBC040826 0,368 → 0,04416; SEMESTRAL/TRIMESTRAL sin cambio. Afecta ~2,2e10 CLP en MRCLP, MDCH, MCPPP, MLDL.
2. **`conversion.py` — sanidad del XCCY.** `Yield_XCCY` no pasa por `yield_min/max_proveedor`: PCRD (MLDL) quedó con 195 % porque BBG devolvió un XCCY absurdo sobre un papel argentino. Regla: si `Yield_XCCY` está fuera de rango → se ignora, se usa drop propio (o queda la yield del papel si no hay curvas) y alerta MEDIA `XCCY_FUERA_RANGO`. Test con PCRD (papel 0,246, XCCY 4,27 → DROP).
3. **`conversion.py`/`indices.py` — índices flotantes sin curva.** TAB30, CHIBPROM (RATE, Chile) hoy dan `SIN_BREAKEVEN` ALTA aunque la yield de EXCEPCIONES ya es nominal local. Regla: índice de categoría RATE sin curva → `Conversion=PROVEEDOR_NOMINAL`, alerta INFO `INDICE_SIN_CURVA`. CPI en USD (BAUZA) sigue ALTA porque sí es real. Agregar `TAB30`, `CHIBPROM` a `config.INDICES` como RATE sin ticker de curva.
4. **`fuentes/cajas.py` — no alertar `CAJA_SIN_REGLA` cuando otra fuente resolvió** (DAP con RA): la alerta hoy sale aunque RA dio métrica. Emitir solo si el candidato elegido es el `SIN_REGLA`. (Se decide en `pipeline` tras `elegir`, o la alerta se filtra en `resumen_estructurales`.)
5. **`fuentes/reglas_fijas.py` — pasivos bancarios por nombre (opcional).** `Financial Debt` con nombre `CR_{CCY}_{BANCO}_{YYYYMMDD}_{tasa}` (MRV: `CR_CLP_SCOTIA_20260909_0.4800`, −4,5e9) trae tasa mensual y vencimiento en el nombre: candidato `NOMBRE` con `yield = tasa/100 × 12`, `dur = días/365`, alerta INFO. Idem `RD_USD_…`. Solo si el usuario confirma la convención del nombre.

### Decisiones del usuario (se aplican en REGLAS.xlsx, sin código)
6. **MRFIIG (fondo 23) y otros `Investment_Type_Code=6` en Fixed Income** (37 posiciones, 3,9e9; 34 son ETF/fondos de bonos IE/LU/US): hoy CASCADA → FALTANTE y `BBG_PEDIDO_RECHAZADO`. Opción recomendada: fila `clasificacion` `ID_Fund=23, Criterio=Investment_Type_Code, Valor=6, Bucket=Cash, Mutual Funds & Others` (yield 0, cobertura 100 %) y, si quieren look-through, `overrides_valor` por fondo. Alternativa: dejarlos FALTANTE y llenar `plantilla_overrides`.
7. **Defaulteados (resuelto en H6d):** el `DEFAULTED.xlsx` corporativo está desactualizado (74 instrumentos, 2019–2020; 23 en el CUBO) y la hoja `defaulteados` de REGLAS estaba vacía porque no se migró el `DEFAULTEADOS.xlsx` del legacy (176 filas, 89 instrumentos en el CUBO, 195 posiciones: 138 FALTANTE, 25 con yield de proveedor —TELEFB, ENJOY, RAIZBZ, ADASA— y solo 32 REGLA_DEF). Decisión: `migrar-manuales --incluir-defaulteados` genera una fila **global por instrumento** (ID_Fund vacío), como aplicaba el legacy (por PK2 en todos los fondos); `yield_def`/`duracion_def` (0 / 0,5) parametrizables en REGLAS. Operador: `migrar-manuales --fecha F --legacy .. --incluir-defaulteados --aplicar` y volver a correr `--sin-bbg`.
8. **Cobertura de los fondos GSI/MRCLP sin proveedor** (PAG GSI…, MHE-, CLH-, P$_CON, FIP/CFI, VC DEUDA PERU, LN Participating; 2,25e11 en MRCLP, 8,3e10 GSI RL II, 4,7e10 MGSI RC III, 1,9e10 MONEDA GSI, 1,5e10 MCPPP): no hay fuente de mercado ni TD; el nombre trae tasa y vencimiento (`PAG GSI INM LOS VALLES 20291128 6.30`) pero no la amortización. Camino: `EXCEPCIONES_{F}.xlsx` con las TD (mejor) u `overrides_valor` con tasa nominal/duration aproximada (rápido). `plantilla_overrides` ya viene ordenada por MV para eso.
9. **`cajas` para los fondos nuevos** (178 posiciones sin fila, 7,1e10; SIM_LV 1,5e10 y SIM_CREDICORP 2e9 son simultáneas con tasa conocida; collateral/margin USD de MRCLP; FNCHI-*): llenar `Indice_Referencia/Spread_Anual/Dias` en REGLAS `cajas`. Hasta entonces yield 0 (y en MRCLP los USD hedgeados quedan con 0 + drop = 0,87 %).
10. **Vencimientos inminentes** (AES 5.7 2028 rescatado a 4 días: yield 84 %; COSHSA con un solo flujo: 15–40 % y distinto por fondo por precio/AI): matemáticamente correcto pero anualizar 4 días distorsiona el AW (AES pesa +0,6 pp en MLATHY). Decidir: override a yield de caja para papeles con último flujo ≤ N días, o aceptarlo y dejarlo en CRÍTICA. Si se quiere regla: parámetro `dias_vencimiento_inminente=30` → alerta MEDIA `VENCIMIENTO_INMINENTE` y AW sin cambio.

### Verificación H6c
`pytest -q` verde con los tests nuevos (RA UNICO, XCCY fuera de rango, índice RATE sin curva, CAJA_SIN_REGLA filtrada) · `correr --fecha 20260731 --sin-bbg` sobre el CUBO real: RA de DAP ≈ 4,4 %, PCRD sin 195 %, `SIN_BREAKEVEN` solo BAUZA, `CAJA_SIN_REGLA` sin los DAP resueltos · `comparar` contra `REPORTE_20260731.xlsx` lista exactamente esas posiciones.

## 5e. H7 — Facturas como fuente propia desde la base de Facts (túnel SSH + Postgres)

**Contexto.** Hoy `FACTURA` lee `FACTURAS_{F}.xlsx` (hoja `Facturas` del RPT) con `lectura/manuales.leer_facturas` y `fuentes/facturas.candidatos_facturas`. El proveedor puso en producción tres tablas (`bi_facturas`, `bi_prorrogas`, `bi_cambios`) con exactamente las columnas de `tests/fixtures/corporativo/muestra_facturas.xlsx` (hojas `Facturas`/`Prórrogas`/`Cambios`), accesibles por túnel SSH a `moneda.facts.cl` (usuario `bi`, llave ed25519) y Postgres `facts` (usuario `bi_readonly`, clave en `MONEDA_BI_PASSWORD`). Las 3.540 facturas de julio hoy quedan FALTANTE por no tener el Excel. Decisiones cerradas con el usuario: (1) **as-of al cierre** reconstruido con `bi_cambios` y `fecha_pago`; (2) **tasa de la prórroga vigente** cuando la prórroga empezó ≤ cierre (el RPT usa la original; se documenta la diferencia); (3) host y usuarios en `.env` con los defaults del proveedor, secretos solo en `.env`.

### Semántica de los datos (de `Columnas`/`Readme` de la muestra)
- `tasa_mensual` decimal base 30 días; yield = tasa × 12 (lineal). `fecha_vencimiento` = vencimiento vigente; `fecha_vencimiento_original` antes de prórrogas; `fecha_pago` vacía si no se ha pagado; `estado ∈ {vigente, depositado, moroso, prorrogado, pagado}`.
- `bi_prorrogas`: `documento_operacion_id, fecha_inicio, fecha_vencimiento_nueva, dias, monto_base, tasa_mensual, yield, formula_interes, fecha_registro`.
- `bi_cambios` (desde 2026-04): `fecha, documento_operacion_id, campo ∈ {monto_compra, tasa_interes, fecha_vencimiento, porcentaje_financiamiento, spread_externo}, valor_anterior, valor_nuevo`.
- Llave entre tablas: `documento_operacion_id`. Cruce con el CUBO: `nemotecnico → HOMOL(GENEVA) → ID_Instrumento`, `fondo → HOMOL_FUNDS/BD_FUNDS → ID_Fund` (ya existe).

### Cambios
1. **`adaptadores/facts.py`** (patrón de `fx_sql.py`): `Protocol FuenteFacts.tablas(fecha) -> dict[str, DataFrame]` con llaves `facturas`, `prorrogas`, `cambios`.
   - `FactsSql`: abre túnel con `subprocess.Popen(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-i", key, "-L", f"127.0.0.1:{puerto_local}:127.0.0.1:{FACTS_DB_PORT}", f"{FACTS_SSH_USER}@{FACTS_SSH_HOST}"])` (puerto local libre elegido con `socket`), espera a que el puerto acepte conexión (≤ 20 s), consulta con `psycopg2` (`connect_timeout=15`, `options='-c statement_timeout=60000'`) `SELECT * FROM bi_facturas` / `bi_prorrogas` / `bi_cambios WHERE fecha <= %s + 1 día` (parametrizado), y cierra el túnel en `finally`. Variables: `FACTS_SSH_HOST=moneda.facts.cl`, `FACTS_SSH_USER=bi`, `FACTS_DB=facts`, `FACTS_DB_USER=bi_readonly`, `FACTS_DB_PORT=5432`, `MONEDA_BI_PASSWORD` (obligatoria), `MONEDA_BI_SSH_KEY` (default `~/.ssh/id_ed25519`). Sin `MONEDA_BI_PASSWORD` → `RuntimeError` claro. Si el usuario deja `moneda_bi_ejemplo.py` del proveedor en `v2/docs/proveedor/`, se copian de ahí puerto/opciones exactas del túnel.
   - `FixtureFacts(dir_cache, fecha)`: lee `04_CACHE/{F}/facts_{tabla}_{F}.csv`; devuelve `{}` si no hay.
   - `CacheFacts(inner, dir_cache, fecha)`: caché → si vacía, baja y escribe los tres CSV (fechas ISO, sin índice).
   - `pipeline._facts(opciones, rutas)` como `_fx`; `Opciones.sin_facts` y `Opciones.facts` inyectable.
2. **`lectura/facts.py`** — funciones puras, testeables con la muestra:
   - `normalizar_tablas(t: dict) -> dict`: tipos (fechas, numéricos), `nemotecnico/fondo/estado` limpios; valida columnas mínimas (`COLS_FACTURAS` + `documento_operacion_id`, `prorrogas`, `tasa_mensual_prorroga`, `fecha_vencimiento_prorroga`).
   - `facturas_al_cierre(facturas, prorrogas, cambios, settle) -> DataFrame` con las columnas que hoy consume `candidatos_facturas` (`nemotecnico, fondo, estado, monto_compra, fecha_vencimiento, fecha_pago, tasa_mensual`) más `documento_operacion_id`, `tasa_origen ∈ {ORIGINAL, PRORROGA, CAMBIO_REVERTIDO}`, `vencimiento_origen`:
     a. **As-of con cambios**: para cada `documento_operacion_id` y campo ∈ {`tasa_interes`→`tasa_mensual`, `fecha_vencimiento`, `monto_compra`}, si hay cambios con `fecha > settle`, el valor al cierre es el `valor_anterior` del cambio más antiguo posterior al cierre. Prórrogas con `fecha_inicio > settle` se ignoran (su cambio de vencimiento ya se revierte por `bi_cambios`; si no hubiera registro, se usa `fecha_vencimiento_original` cuando la única prórroga es posterior al cierre).
     b. **Vivas al cierre**: `fecha_pago` vacía o `> settle` (hoy se excluye toda pagada: con base viva y corrida tardía perdería las pagadas entre cierre y corrida). `estado` deja de usarse para excluir; se conserva para el detalle.
     c. **Tasa vigente**: si existe prórroga con `fecha_inicio ≤ settle`, `tasa_mensual = tasa_mensual` de la última prórroga ≤ settle (`tasa_origen=PRORROGA`) y `fecha_vencimiento = fecha_vencimiento_nueva` de esa prórroga; si no, la tasa (ya as-of) de `bi_facturas`.
   - `leer_facturas` (Excel) sigue existiendo: `candidatos_facturas` recibe el mismo frame venga de donde venga.
3. **`fuentes/facturas.py`**: acepta el frame nuevo; `origen=f"FACTS:{nemotecnico}"` cuando trae `documento_operacion_id`, `RPT:` si viene del Excel; `detalle` agrega `tasa_origen`. Regla de viva pasa a `fecha_pago.isna() | fecha_pago > settle` también para el Excel. Alerta nueva INFO `FACTURA_TASA_PRORROGA` (N y MV) y `FACTURA_ASOF_REVERTIDA` (INFO, cambios posteriores al cierre revertidos).
4. **`pipeline.py`**: orden de la fuente: `CacheFacts/FactsSql` (o `FixtureFacts` con `--sin-facts`) → si no devuelve tablas, `FACTURAS_{F}.xlsx` si existe → si nada, `INSUMO_FALTANTE` como hoy. Log: `FACTS: N facturas, M prórrogas, K cambios (as-of {F}); vivas al cierre: V`. Fallo de red/credenciales → `warning` + alerta ALTA `FACTS_SIN_CONEXION` y sigue con Excel/caché.
5. **`cli.py`**: `correr --sin-facts`; `check` agrega líneas: `psycopg2` instalado (→ `--sin-facts`), cliente `ssh` en PATH, `MONEDA_BI_PASSWORD`/`MONEDA_BI_SSH_KEY` (existe el archivo) definidas, caché `facts_*_{F}.csv` presente. Comando nuevo `facts-probar --fecha F`: abre el túnel, cuenta filas de las tres tablas y `max(fecha)` de cambios, no escribe caché (diagnóstico del primer setup).
6. **`config.py`**: defaults `FACTS_*` leídos de env; `.env.example` con las variables (hosts y usuarios con valor; secretos vacíos). `pyproject`: extra `facts = ["psycopg2-binary"]`. `.gitignore` ya excluye `.env` y `04_CACHE/`.
7. **Docs**: README §Facturas (fuente, as-of, tasa de prórroga, variables, `--sin-facts`, `facts-probar`), CHECKLIST (primera vez: `ssh-keygen`, mandar `.pub`, `.env`, `pip install -e .[facts]`, `facts-probar`; cada cierre: nada, la corrida baja y cachea), FUTURO (carga incremental con `bi_cambios`, foto diaria del RPT para precio/devengo).

### Fixtures y tests (TDD)
- `tests/fixtures/corporativo/facts/{facturas,prorrogas,cambios}.csv` exportados de `muestra_facturas.xlsx` (187/20/264 filas) y copiados como caché `mini/bbg_cache`-style en `tests/fixtures/mini/facts_*_20260731.csv` recortados a los nemotécnicos del CUBO mini (el `FACTURAS_20260731.xlsx` mini se conserva para el camino Excel).
- `test_lectura_facts.py`: (a) as-of: doc 58964 con cambio de vencimiento 2026-06-12 → al cierre 2026-06-11 vence 2026-06-12, al 2026-06-30 vence 2026-07-17; tasa: doc 68135 cambio 0,0066→0,0070 el 2026-06-04 → al 2026-06-03 tasa 0,0066; (b) pagada después del cierre sigue viva (doc 59396 pagada 2026-08-18: viva al 2026-07-31, no al 2026-08-31); (c) prórroga vigente: doc 68147 al 2026-09-02 → tasa 0,0095 y vencimiento 2026-10-02 (`PRORROGA`); al 2026-08-27 → 0,0085 y 2026-08-28 (`ORIGINAL`); (d) validación de columnas faltantes.
- `test_adaptador_facts.py`: `FixtureFacts` vacío → `{}`; `CacheFacts` con inner stub escribe los CSV y la segunda llamada no llama al inner; `FactsSql` sin `MONEDA_BI_PASSWORD` → error claro sin abrir túnel (no importa psycopg2 en tests).
- `test_fuentes_facturas.py`: frame de Facts → `origen FACTS:`, alerta `FACTURA_TASA_PRORROGA`; regla de viva con `fecha_pago > settle`.
- `test_cli.py`: `check` imprime líneas de facts; `correr --sin-facts` con caché mini resuelve las facturas del CUBO mini; `test_golden_h5` sigue verde (con caché mini las 302 facturas del mini pasan a RESUELTO: se actualiza el golden y se documenta).

### Verificación
`pytest -q` verde · `python -m reporteria.cli check --fecha 20260731` muestra las líneas de Facts · en la estación: `pip install -e .[facts]`, `.env` con `MONEDA_BI_PASSWORD` y llave, `facts-probar --fecha 20260731` cuenta filas · `correr --fecha 20260731` baja las tablas y deja `04_CACHE/20260731/facts_*.csv`; `resumen`: las 3.540 facturas pasan de FALTANTE a RESUELTO con `Fuente=FACTURA`, cobertura MRCLP sube · `correr --sin-facts` reproduce idéntico (`comparar` → 0 diferencias).

## 6. Diferido a `FUTURO.md`
RA TIR → yield equivalente por periodicidad · flotantes propios con forward (hoy suma spot) y validación del supuesto "proveedor entrega nominal local" · fuente `ATRIBUTOS` (TD desde `Atributos_*.xlsx`: Bullet/Sinkable/Zero, Upfront Fee) · drop inverso local→USD · conector a fuente de pactos/simultáneas/DAP · refresco automático de jsonl y HOMOL · fallback tenores México y curvas ARS/UYU · breakeven flujo a flujo · atribución AW/DW vs mes anterior · tratamiento fino de pasivos · exportación a base corporativa · alerta cupón negativo (F4).

## 7. Verificación end-to-end
1. `cd v2 && pytest -q` (unitarios + invariantes; golden marcado `slow`).
2. `reporteria importar-cache-legacy --fecha 20260731 --legacy ..` → `reporteria check --fecha 20260731` → `reporteria correr --fecha 20260731 --sin-bbg`.
3. Abrir `REPORTE_20260731.xlsx`: `resumen` (1.418 + 194 DEF resueltas sobre CUBO completo, cobertura por fondo), `alertas` (A08 lista los bonos vencidos), `agregados` cierran A − P, `plantilla_overrides` con los faltantes.
4. `pytest -m slow` compara contra `golden/` con las diferencias esperadas documentadas (sanidad, duration de breakeven, XCCY).
5. H6 en Windows: `correr` con terminal → segunda corrida `--sin-bbg` → `diff resumen_corrida.json` vacío.

## 8. Pendientes del usuario (no bloquean H0)
- Archivos con el tratamiento actual de buckets (define la taxonomía en H1).
- Muestras reales de CUBO, BD_INSTRUMENTOS, BD_FUNDS, HOMOL en `v2/tests/fixtures/`.
- Un `FACTURAS_{FECHA}.xlsx` real cuando exista (hoy plantilla definida).
- Nombre del repo nuevo para la migración en H6.

## 9. Referencias del legacy a tener abiertas (lógica, no código)
`00_CODIGO/pipeline_config.py` (mapeos, tickers, candidatos de escala) · `03_JSONL_VF.py` (`clasificar`, `build_td_geneva`, `evaluar_escala`, FX por fondo) · `02_CSHF_VF.py` (xirr, duration, face derivado) · `06_BREAKEVEN.py` y `07_DROPS.py` (fórmulas y lectura de curvas) · `08_OVERRIDE.py` (flags F1–F7, AW/DW) · `05_CONSOLIDA.py` (cascada) · `PLAN_MODIFICACION.md` (bugs a no repetir).
