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

## REGLAS.xlsx
| Hoja | Para qué |
|---|---|
| `clasificacion` | Bucket y Tratamiento por criterio (`PK2`, `Nombre_Regex`, `Issue_Type_Code`, `Investment_Type_Code`, `Source`, `BalanceSheet`). Tratamiento: `CASCADA` busca métrica; `FIJO` usa Yield/Duration de la fila; `FACTURA`; `CERO`; `EXCLUIR`. Regla por fondo gana a global; criterio más específico gana. |
| `defaulteados` | PK2 (y opcionalmente ID_Fund) en DEF / PROPDEF → Yield 0, Duration 0.5. |
| `overrides_valor` | Yield/Duration finales por PK2 (y fondo), con vigencia opcional. Pisa todo. |
| `overrides_atributo` | Hedge_Currency, Indice, Estado_DEF, Bucket, Tratamiento por PK2 (y fondo). |
| `alertas` | Reglas del motor de alertas (campo, operador, umbral, severidad). |
| `parametros` | Umbrales globales (`yield_max_proveedor`, `factura_tolerancia_monto`, …). |

## Desarrollo
```
pip install -e .[dev]
pytest -q
```
Los fixtures de `tests/fixtures/` salen de `tests/fixtures/construir_fixtures.py` (se corre una vez contra el repo
legacy) y se reemplazan por muestras reales cuando estén disponibles.
