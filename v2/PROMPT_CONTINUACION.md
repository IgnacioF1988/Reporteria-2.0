# Prompt de continuación — Reportería 2.0 (H10 completo; siguiente iteración)

Copia todo lo que sigue como primer mensaje de la nueva sesión.

---

Actúa en dos roles a la vez: **PM** (orquestas: lees el plan, propones el alcance exacto del hito, haces las preguntas cerradas que cambian el resultado antes de codear, reportas con tablas cortas y cierras cada hito con verificación) y **ingeniero de software senior** (ejecutas: TDD estricto, código mínimo, sin inventar inputs, sin credenciales en código, commits pequeños y pusheados). No me preguntes cosas que están decididas en `v2/PLAN.md`; pregunta solo lo que cambie materialmente el trabajo.

## Proyecto
Repo `IgnacioF1988/Reporteria-2.0`, rama `claude/plan-modificacion`. El pipeline nuevo vive en `v2/` (paquete `reporteria`, `tests/`, `pyproject.toml`) y es **standalone**: nada mira arriba de `v2/`; solo CUBO y BIX son externos (`RUTA_CUBO_DIR`, `RUTA_BIX`). El repo legacy es referencia funcional, nunca se modifica. Calcula Yield/Duration por posición (fondo × PK2) con cascada de fuentes (EXCEPCIONES → JPM → RA → BBG YAS → CSHF → JSONL), facturas desde Facts, conversiones (breakeven, drop, XCCY), overrides, alertas por reglas, agregados A/P/Patrimonio.

Lee primero, en este orden: `v2/PLAN.md` (plan completo con decisiones cerradas; secciones 5f–5j describen H8–H10 y el estado HECHO/pendiente de cada hito), `v2/README.md`, `v2/CHECKLIST_CIERRE.md`, `v2/FUTURO.md`. Luego `reporteria/pipeline.py`, `reporteria/publicacion.py`, `reporteria/calendario.py`, `reporteria/impacto.py`, `reporteria/datamart.py`, `reporteria/adaptadores/datamart.py`, `reporteria/cli.py` y los tests `tests/test_publicacion.py`, `tests/test_diario.py`, `tests/test_datamart_diario.py`.

## Estado al cerrar la sesión anterior (2026-10-02)
- Suite: `cd v2 && python -m pytest -q` → **193 tests en verde** (≈5 min; mini de 1.000 posiciones y 14 fondos en `tests/fixtures/mini`).
- Cierre real 20260731 en git: `v2/datamart/cierres/cierre=20260731/version=001` PUBLICADA y `version=002` REEXPRESADA COMPLETA (7025 RESUELTO / 172 FALTANTE / 0 pendientes); maestros base `20261002_101040`. Las 17 diferencias v001→v002 son un solo instrumento (225679-1) que pasó de CSHF a BBG YAS/XCCY con datos nuevos de terminal: correcto.
- Hitos HECHOS: H0–H9c (versionado bitemporal, maestros base+deltas, impacto y re-expresión, PENDIENTE_TERMINAL), 5j (xbbg que no carga degrada a caché con alerta `BBG_SIN_CONEXION`; huella de caché por stat; `FOR_DISABLE_CONSOLE_CTRL_HANDLER=1`), H10a (layout diario `diario/fecha=F/corrida=NNN` + `estado.json`, `Lock`, atómico, `migrar-datamart`), **H10b** (readiness y publicación por fondo-día: `publicacion.evaluar/aplicar/fondos_cambiados/estado_fondos`, hoja `publicacion`, puntero por fondo PUBLICADA/REEXPRESADA solo si cambió/PROVISORIO/SIN_CORRIDA, `DM.vistas`, CLI `estado`), **H10c** (semántica diaria: `calendario.py`, `anterior_por_fondo`, `Hedge_Origen=ANTERIOR`, `SIN_HISTORIA`, parámetros `_diario`, RA fechado, CADENA por contenido, política `reexpresar_por` con `accion` REEXPRESAR/MARCAR, ventana, `recalcular --desde/--hasta`, `impacto --todos`).
- Pendientes de operador (no bloquean): llenar `plantilla_cajas`, 172 faltantes conocidos (GSI, MRFIIG, pagarés MRCLP), refrescar `bond_schedule.jsonl`, rotar credencial legacy, nombre del repo nuevo, mover el datamart diario al share cuando exista servidor.

## Decisiones vigentes que NO se re-discuten
- **Calendario (aclaración del usuario, ya implementada en H10c):** hay un cierre por **cada día calendario**; Geneva lo entrega el siguiente día hábil (el lunes aparecen viernes, sábado y domingo; tras feriado, lo acumulado). La nocturna corre **todos los días** y procesa todas las fechas pendientes cuyo CUBO exista (`calendario.pendientes` → LISTA / AUN_NO_ESPERADA / SIN_CUBO). Hábil = L–V fuera de `REGLAS/feriados` (hoja opcional). Cierre mensual = fondo-día del último día calendario del mes (sin caso especial) y se copia a git con `cierre-mensual`.
- Publicación automática por (fondo, día). Bloqueos: insumo obligatorio ausente (`parametros.insumos_obligatorios`, default CUBO), PENDIENTE_TERMINAL, cobertura < `cobertura_min_mv`, `AGREGADO_INCONSISTENTE` del fondo, alertas con `Bloquea_Publicacion=SI`. Fondos esperados = `Activo_MantenedorFondos=1` en `dim_fondos`.
- Datamart diario en el share, fuera de git (`REPORTERIA_DATAMART`, `REPORTERIA_CACHE`, `REPORTERIA_MODO=diario`); git conserva código, dim, REGLAS, declaraciones y los cierres mensuales.
- Nocturna en servidor sin Bloomberg (aún no definido, diseño agnóstico de SO) + pasada BBG **manual** por un operador en estación con terminal (`pasada-bbg`).
- Política de re-expresión diaria `MAESTRO;DIM;INSUMO;CADENA` dentro de `ventana_reexpresion_dias` (60); código y REGLAS globales solo marcan (`recalcular --desde`); REGLAS con `ID_Fund` re-expresa ese fondo.
- Aviso diario por **Teams webhook** (`TEAMS_WEBHOOK_URL` en `.env`, `urllib`, sin dependencias nuevas, `--dry-run` deja el JSON en `03_LOGS`).
- Vista BI definitiva y herencia del hedge por fuente de derivados: **próxima iteración** (hoy hereda de la última verdad del fondo).

## Reglas de trabajo (no negociables)
- TDD: tests antes del código; `python -m pytest -q` verde antes de cada commit. No tocar goldens salvo cambio documentado.
- Credenciales solo en `.env` (nunca en código ni commits); `04_CACHE/`, `02_OUTPUTS/`, `03_LOGS/` nunca versionados; el repo raíz no tiene `.gitignore`, así que nunca `git add` de carpetas de la raíz.
- Commits: mensaje en español, sin identificadores de modelo; terminan con `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` y `Claude-Session: https://claude.ai/code/session_01MSSQoFR1coHjRzkTJJ7d3F`. Push a `claude/plan-modificacion` tras cada hito. Nunca PR salvo que lo pida.
- Operador en Windows/PowerShell: comandos uno por línea (sin `&&`), `python` en la estación normal (3.14, sin xbbg funcional), `py -3.12` en la terminal Bloomberg. El repo vive en un share UNC compartido por ambas máquinas.
- Documentar cada hito en `README.md` (sección Modo diario) y marcar HECHO en `PLAN.md` §5i; actualizar `CHECKLIST_CIERRE.md` cuando cambie la operación.
- Al terminar un hito: tabla corta de qué quedó, comandos exactos para que yo verifique en mi máquina, y pedir aprobación para el siguiente hito. Hitos de a uno.

## Estado: H10d y H10e HECHOS (2026-10-02). Lo que sigue se decide con el usuario (ver FUTURO.md y la próxima iteración: fuente de derivados para el hedge, vista BI definitiva, Facts incremental, servidor nocturno real).

## (Histórico) H10d — Orquestación nocturna
Entregables (todo en `v2/`, módulo nuevo `reporteria/orquestacion.py` + `reporteria/notificacion.py` + CLI):
1. `reporteria diario [--hoy D] [--hasta D] [--dry-run]`: con `Lock` del datamart (exit 3 si ajeno y vigente) → `limpiar_tmp` → cargar maestros una vez (único escritor) → `calendario.pendientes(DM.fechas, hoy, existe_cubo_en(RUTA_CUBO_DIR), feriados de REGLAS)` → por cada fecha LISTA en orden: `correr(sin_bbg=True, excel=False)` → `publicar(modo="diario")` (readiness ya viene en la hoja `publicacion`) → re-evaluar los PROVISORIO/SIN_CORRIDA de la ventana cuyo bloqueo pudo cambiar (insumo llegado, caché con dato nuevo, cobertura) con `recalcular` → `impacto(ventana)` y `reexpresar_impactados` → `estado_diario_{hoy}.md` en `03_LOGS` (y en el share junto al datamart) → Teams. Fechas SIN_CUBO: alerta y `SIN_CORRIDA(SIN_CUBO)` para los esperados en `estado.json` de esa fecha; AUN_NO_ESPERADA: silencio. CUBO o REGLAS inválidos: la corrida de esa fecha no existe, todos los esperados `SIN_CORRIDA` y notificación; la nocturna sigue con las demás fechas. Códigos: 0 todo publicado · 1 hubo provisorios/sin corrida · 2 fallo de infraestructura · 3 lock ajeno.
2. `notificacion.teams(resumen, url)` con `urllib.request` (sin dependencias); `--dry-run` escribe el JSON en `03_LOGS/teams_{ts}.json`; sin `TEAMS_WEBHOOK_URL` → aviso en log y sigue. Mensaje: "F: 47/49 publicados; 2 provisorios (fondo: causa); re-expresados N fondo-días; marcados M (recalcular --desde)".
3. `cierre-mensual --fecha F`: copia la corrida vigente del último día calendario del mes al layout de git `v2/datamart/cierres/cierre=F/version=NNN` (reutilizar `copiar_version` y `publicacion.json`), marca `cierre_mensual=true` en `estado.json` del share, recuerda `git add datamart`.
4. `limpiar --dias N`: retención de corridas superadas (no apuntadas por ningún fondo ni publicada) y caché más vieja que N días; `--dry-run` lista.
5. Tests (`tests/test_orquestacion.py`, derivados del fixture de tres días de `tests/test_diario.py`): día normal (lunes con viernes/sábado/domingo LISTA → tres corridas y estados por fondo); CUBO ausente ya esperado → SIN_CORRIDA(SIN_CUBO) + notificación, el resto sigue; insumo tardío que destraba un PROVISORIO la noche siguiente; lock ajeno vigente → exit 3, vencido → rehace; re-corrida idempotente (sin cambios → nadie se re-apunta, Teams dice 0 cambios); Teams `--dry-run` con JSON validado; `cierre-mensual` copia al layout de git y `versiones` lo lista; `limpiar --dry-run` no borra nada apuntado.
6. README (operación nocturna, Teams, cierre-mensual, limpiar, códigos de salida), CHECKLIST (qué mira el operador cada mañana), PLAN §5i H10d → HECHO.

Después de H10d sigue **H10e**: `pasada-bbg [--fecha F | --todas]` en la estación con terminal (recalcular con `con_terminal=True`, `sin_cargar_maestros=True`, publica los fondo-días que se destraban, notifica), aislamiento por fondo (`_correr_aislado` bisect + `INSUMO_INVALIDO`), `REPORTERIA_MODO=diario` por defecto, `reporte --mes`, README/CHECKLIST de operación completa.

Empieza leyendo los archivos indicados, confirma en 10 líneas tu entendimiento del estado y del alcance de H10d, hazme solo las preguntas que cambien el diseño (si no hay, dilo) y arranca con los tests.
