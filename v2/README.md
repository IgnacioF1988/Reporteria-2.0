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
```
Códigos de salida: 0 OK · 1 OK con alertas CRÍTICAS · 2 falta un input obligatorio o REGLAS inválido.

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
