# Checklist de cierre (operador, Windows con terminal Bloomberg)

Todo se corre desde una consola (PowerShell) en la carpeta del paquete. `F` es el cierre, p. ej. `20260731`.

## Una sola vez (instalación)
1. Python 3.11+ instalado. En la carpeta del paquete: `pip install -e .[bbg,sql,dev]` (sin terminal ni ODBC: `pip install -e .[dev]`).
2. Copiar `.env.example` a `.env` y completar `REPORTERIA_RAIZ`, `RUTA_CUBO_DIR`, `RUTA_BIX` y las credenciales `BEE_*`. **`.env` nunca se sube al repo.**
3. Estructura bajo la raíz: `01_INPUTS/MERCADO`, `01_INPUTS/MANUALES` (con `REGLAS.xlsx` y `EXCEPCIONES*.xlsx`), `01_INPUTS/GENEVA` (`bond_schedule.jsonl`). `02_OUTPUTS`, `03_LOGS` y `04_CACHE` se crean solos.
4. Primera vez con manuales del legacy: `reporteria migrar-manuales --fecha F --legacy <carpeta legacy>` revisa el Excel `REGLAS_migracion_*.xlsx` que deja en MANUALES; si está bien, repetir con `--aplicar` (deja `REGLAS_backup_*.xlsx`). `--incluir-defaulteados` solo si se decide traer DEFAULTEADOS.xlsx (el corporativo `DEFAULTED.xlsx` es la fuente).
5. Opcional, para correr sin terminal un cierre que el legacy ya calculó: `reporteria importar-cache-legacy --fecha F --legacy <carpeta legacy>`.

## Cada cierre
1. Dejar en MERCADO los archivos del mes: `JPM_CEMBI_GBI_F.xlsx`, `RA_TIR.xlsx`, `FACTURAS_F.xlsx`, `Carga_Indexes_F*.csv`, `Carga_CurvasSoberanas_F*.csv`, `4- Carga de paridades.xlsx`. Confirmar que `CUBO_F.xlsx` está en `RUTA_CUBO_DIR` y que BD_INSTRUMENTOS/HOMOL están al día.
2. `reporteria check --fecha F` → todo `OK`. Mirar: `.env` con 6/6 variables, `xbbg`/`pyodbc` instalados, antigüedad del `bond_schedule.jsonl` (≤ 35 días), sin manuales legacy pendientes. Exit 2 = falta algo obligatorio o REGLAS inválido: corregir antes de seguir.
3. **Con terminal abierta**: `reporteria correr --fecha F`. Deja `02_OUTPUTS/F/REPORTE_F.xlsx`, `resumen_corrida_F.json`, el log en `03_LOGS/F/` y la caché Bloomberg/FX en `04_CACHE/F/`. Exit 1 = hay alertas CRÍTICAS (la corrida es válida; hay que revisarlas).
4. Copiar el reporte como respaldo: `copy 02_OUTPUTS\F\REPORTE_F.xlsx 02_OUTPUTS\F\REPORTE_F_terminal.xlsx`.
5. **Reproducibilidad**: `reporteria correr --fecha F --sin-bbg --sin-sql` y luego `reporteria comparar --fecha F --otro 02_OUTPUTS\F\REPORTE_F_terminal.xlsx`. Debe decir `0 diferencias` (exit 0). Si no, la caché está incompleta: revisar el log.
6. Abrir `REPORTE_F.xlsx`:
   - `alertas_resumen`: reglas ACTIVAS con N > 0 y las estructurales con MV afectado. CRÍTICAS primero.
   - `plantilla_overrides`: los faltantes ordenados por MV. Completar Yield (decimal: 0.05 = 5 %) y Duration, pegar en `REGLAS/overrides_valor` con `Fecha_Desde`, y volver a correr `--sin-bbg` (no gasta terminal).
   - `agregados`: AW/DW por fondo a nivel ACTIVOS / PASIVOS / PATRIMONIO; `Cobertura` de activos por fondo.
   - `conversiones`: breakeven y XCCY/drop aplicados; `XCCY_VS_DROP` en alertas marca los swaps que difieren del drop propio.
   - `reglas_aplicadas` e `insumos`: qué fila de REGLAS actuó y qué archivos se usaron.
7. Overrides de atributo (hedge distinto, índice, bucket por fondo): `REGLAS/overrides_atributo` con `Field` ∈ Hedge_Currency, Indice, Bucket, Risk_Currency, Risk_Country y vigencia. Correr de nuevo `--sin-bbg`.
8. Cerrar: el reporte final es el último `REPORTE_F.xlsx`. El cierre siguiente lo usa como cierre anterior (hedge heredado y alertas temporales A05–A07).

## Códigos de salida
`0` OK · `1` OK con alertas CRÍTICAS · `2` falta un input obligatorio o REGLAS inválido · `comparar`: `1` = hay diferencias.

## Si algo falla
- `INSUMO_FALTANTE` en alertas: el archivo opcional no estaba; la corrida sigue sin esa fuente.
- `FX_SIN_BEEMINING`: sin ODBC o sin red; se usan paridades y caché. Revisar `.env` y el driver.
- Terminal sin respuesta para un ISIN: queda `FALTANTE` con motivo en `candidatos`; va a `plantilla_overrides`.
- `AGREGADO_INCONSISTENTE` (CRÍTICA): reportar, no es un problema de datos sino del cálculo.
