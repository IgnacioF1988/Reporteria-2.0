# Checklist de cierre (operador, Windows con terminal Bloomberg)

Todo se corre desde una consola (PowerShell) en la carpeta del paquete. `F` es el cierre, p. ej. `20260731`.

## Una sola vez (instalación)
1. Python 3.11+ instalado. En la carpeta del paquete: `pip install -e .[bbg,sql,facts,dev]` (sin terminal ni ODBC: `pip install -e .[facts,dev]`). Repetirlo tras cada `git pull` que cambie `pyproject.toml` (H8 agregó `duckdb`).
2. Copiar `.env.example` a `.env` y completar `RUTA_CUBO_DIR`, `RUTA_BIX`, las credenciales `BEE_*` y `MONEDA_BI_PASSWORD` (clave de la base de Facts). **`.env` nunca se sube al repo.**
3. Facts (facturas), una sola vez: generar la llave `ssh-keygen -t ed25519 -C "ignacio-bi-moneda"` (Enter a todo), mandar al proveedor SOLO el archivo `.pub` (`~/.ssh/id_ed25519.pub`; si la llave quedó en otra ruta, ponerla en `MONEDA_BI_SSH_KEY`). Cuando confirmen el acceso: `reporteria facts-probar` debe decir `Facts OK: N facturas, …`. Cada cierre no necesita nada más: la corrida baja las tablas y deja caché.
3. Dimensionales (una sola vez por estación): el repo trae `v2/dim/dimensionales.duckdb`. Para rehacerlo desde el BIX con el `BD_BalanceSheet` completo: `reporteria dim importar --bix <RUTA_BIX> --reemplazar`, revisar el informe (filas leídas/generadas por fuente), `reporteria dim validar --fecha F`, borrar de `REGLAS/clasificacion` las filas con `Criterio=BalSheetKey` que el informe marca como reemplazadas, y `git add dim/` + commit + push. Si el duckdb está en otra ruta, `REPORTERIA_DIM` en `.env`.
3. Todo vive bajo `v2\` (la raíz es la carpeta del paquete; `REPORTERIA_RAIZ` ya no existe): `01_INPUTS\MERCADO`, `01_INPUTS\MANUALES` (`REGLAS.xlsx`, `EXCEPCIONES*.xlsx`), `01_INPUTS\GENEVA` (`bond_schedule.jsonl`), todos versionados en git. `02_OUTPUTS`, `03_LOGS` y `04_CACHE` se crean solos bajo `v2\` y no van a git.
4. Primera vez con manuales del legacy: `reporteria migrar-manuales --fecha F --legacy <ruta del repo legacy>` (los FIP/DEFAULTEADOS/Atributos_* se quedan allá) revisa el Excel `REGLAS_migracion_*.xlsx` que deja en MANUALES; si está bien, repetir con `--aplicar` (deja `REGLAS_backup_*.xlsx`). Usar `--incluir-defaulteados`: el `DEFAULTED.xlsx` corporativo está desactualizado (2019–2020) y la lista real de DEF/PROPDEF es el `DEFAULTEADOS.xlsx` del legacy; queda como una fila global por instrumento en `REGLAS/defaulteados`, que desde ahí se mantiene a mano (agregar el instrumento cuando entra en default, `Fecha_Fin` cuando sale).
5. Opcional, para correr sin terminal un cierre que el legacy ya calculó: `reporteria importar-cache-legacy --fecha F --legacy <carpeta legacy>`.

## Cada cierre
1. Dejar en `v2\01_INPUTS\MERCADO` los archivos del mes (y hacer `git add 01_INPUTS` con el cierre): `JPM_CEMBI_GBI_F.xlsx`, `RA_TIR.xlsx`, `FACTURAS_F.xlsx`, `Carga_Indexes_F*.csv`, `Carga_CurvasSoberanas_F*.csv`, `4- Carga de paridades.xlsx`. Confirmar que `CUBO_F.xlsx` está en `RUTA_CUBO_DIR` y que BD_INSTRUMENTOS/HOMOL están al día.
2. `reporteria check --fecha F` → todo `OK`. Mirar: línea `dimensionales dimensionales.duckdb` con `dim validar: sin problemas` (si dice `espejo dim/csv desactualizado`, alguien editó el duckdb sin pasar por `dim importar`), `.env` con 6/6 variables, `xbbg`/`pyodbc`/`psycopg2` instalados, línea `Facts (facturas)` en OK, antigüedad del `bond_schedule.jsonl` (≤ 35 días), sin manuales legacy pendientes. Exit 2 = falta algo obligatorio o REGLAS inválido: corregir antes de seguir.
3. **Con terminal abierta**: `reporteria correr --fecha F`. Deja `02_OUTPUTS/F/REPORTE_F.xlsx`, `resumen_corrida_F.json`, el log en `03_LOGS/F/` y la caché Bloomberg/FX en `04_CACHE/F/`. Exit 1 = hay alertas CRÍTICAS (la corrida es válida; hay que revisarlas).
4. Copiar el reporte como respaldo: `copy 02_OUTPUTS\F\REPORTE_F.xlsx 02_OUTPUTS\F\REPORTE_F_terminal.xlsx`.
5. **Reproducibilidad**: `reporteria correr --fecha F --sin-bbg --sin-sql --sin-facts` y luego `reporteria comparar --fecha F --otro 02_OUTPUTS\F\REPORTE_F_terminal.xlsx`. Debe decir `0 diferencias` (exit 0). Si no, la caché está incompleta: revisar el log.
6. Abrir `REPORTE_F.xlsx`:
   - `alertas_resumen`: reglas ACTIVAS con N > 0 y las estructurales con MV afectado. CRÍTICAS primero.
   - `plantilla_cajas`: las cajas sin fila en `REGLAS/cajas`, ordenadas por MV, con sugerencia en `_Sugerencia` y `Comentario` (copiada de otro fondo, del nombre, o por moneda). Revisar, completar índice/spread/días y pegar en `REGLAS/cajas`; una fila vacía deja yield 0 a propósito y silencia `CAJA_SIN_REGLA`.
   - `plantilla_overrides`: los faltantes ordenados por MV. Completar Yield (decimal: 0.05 = 5 %) y Duration, pegar en `REGLAS/overrides_valor` con `Fecha_Desde`, y volver a correr `--sin-bbg` (no gasta terminal).
   - `agregados`: AW/DW por fondo a nivel ACTIVOS / PASIVOS / PATRIMONIO; `Cobertura` de activos por fondo.
   - `conversiones`: breakeven y XCCY/drop aplicados; `XCCY_VS_DROP` en alertas marca los swaps que difieren del drop propio.
   - `reglas_aplicadas` e `insumos`: qué fila de REGLAS actuó y qué archivos se usaron.
7. Clasificaciones nuevas (hoja `plantilla_dim`: combinaciones de códigos sin Bucket / Ficha_FI / FX_Exposure): `reporteria dim exportar`, pegar las filas de la plantilla en la hoja `dim_clasificacion` del Excel (dejar vacías las columnas que no importan; `ID_Fund` solo si es propio de un fondo), `reporteria dim importar --excel <archivo>`, `git add dim/` + commit. Volver a correr `--sin-bbg`.
8. Overrides de atributo (hedge distinto, índice, bucket por fondo): `REGLAS/overrides_atributo` con `Field` ∈ Hedge_Currency, Indice, Bucket, Risk_Currency, Risk_Country y vigencia. Correr de nuevo `--sin-bbg`.
8b. Maestros: si el log dice `MAESTROS: carga … con N cambios`, mirar `reporteria maestros cambios --desde F`. Un cambio de atributo es corrección retroactiva salvo que se declare: `reporteria declarar --llave PK2 --columna C --valor V --desde F` cuando el instrumento cambió de verdad a partir de ese cierre. `git add datamart` incluye la carga y las declaraciones.
8c. Al final de `correr` sale el impacto sobre los cierres ya publicados y, si lo hay, se re-expresan solos (`impacto … ×N`, `re-expresado F vNNN`). Si una re-expresión queda `PARCIAL`, `reporteria pendientes` dice qué falta y `reporteria recalcular --fecha F --con-terminal` lo cierra con la terminal abierta. Todo eso va en el `git add datamart` del cierre.
9. Publicar: `reporteria publicar --fecha F` (toma el último borrador de `02_OUTPUTS\F\borradores`; con `--borrador TS` otro) → `git add datamart`, commit, push. Esa versión es lo reportado y queda congelada; el cierre siguiente la usa como cierre anterior (hedge heredado y alertas A05–A07). Si después hay que corregir el cierre: volver a correr y `publicar --reexpresar --motivo "qué cambió"` (nueva versión; `reporte --fecha F --publicada` sigue dando la original). `reporteria versiones` muestra lo que hay.

## Versiones
- `xbbg` 0.7 y ≥ 1.0 funcionan (el adaptador detecta la versión). La 1.x necesita `pyarrow>=22` (viene en el extra `bbg`); si aparece `Backend 'pyarrow' requires pyarrow >= 22`, correr `py -3.12 -m pip install --user --upgrade "pyarrow>=22"`.
- Si `python` abre la Microsoft Store, usar `py -3.12 -m reporteria.cli ...` (o la versión 3.11+ instalada: `py -0` las lista).

## Códigos de salida
`0` OK · `1` OK con alertas CRÍTICAS · `2` falta un input obligatorio o REGLAS inválido · `comparar`: `1` = hay diferencias.

## Si algo falla
- `INSUMO_FALTANTE` en alertas: el archivo opcional no estaba; la corrida sigue sin esa fuente.
- `FX_SIN_BEEMINING`: sin ODBC o sin red; se usan paridades y caché. Revisar `.env` y el driver.
- `FACTS_SIN_CONEXION`: no se pudo abrir el túnel o la base (clave, llave, ssh, red). Las facturas salen del Excel si existe; si no, quedan FALTANTE. `reporteria facts-probar` muestra el error exacto.
- Terminal sin respuesta para un ISIN: queda `FALTANTE` con motivo en `candidatos`; va a `plantilla_overrides`.
- `AGREGADO_INCONSISTENTE` (CRÍTICA): reportar, no es un problema de datos sino del cálculo.
