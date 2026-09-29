# Reportería 2.0 — Métricas de mercado (Yield / Duration)

Obtiene Yield y Duration por posición (fondo × PK2) usando **métricas ya
calculadas por proveedores**, y solo modela tablas de desarrollo propias para
el residual que ninguno cubre.

---

## Estructura de carpetas

```
Reporteria 2.0\
│
├── 00_CODIGO\                  ← scripts + pipeline_config.py
│
├── 01_INPUTS\
│   ├── MERCADO\                ← se refrescan CADA CIERRE
│   │   ├── JPM_CEMBI_GBI_{FECHA}.xlsx
│   │   ├── RA_TIR.xlsx                      (hoja por mes: jul26, ago26…)
│   │   ├── Carga_Indexes_{FECHA}.csv        (para 06_BREAKEVEN)
│   │   └── Carga_CurvasSoberanas_{FECHA}.csv
│   │
│   ├── GENEVA\
│   │   └── bond_schedule.jsonl              (se actualiza cuando se pueda)
│   │
│   └── MANUALES\               ← mantenidos por analista / PM
│       ├── DEFAULTEADOS.xlsx                (PK2 en DEF o PROPDEF)
│       └── EXCEPCIONES.xlsx                 (una pestaña por PK2 con flujos)
│
├── 02_OUTPUTS\{FECHA}\         ← un subfolder por cierre
└── 03_LOGS\{FECHA}\
```

**No se copian** (se leen donde están): CUBO, BD_INSTRUMENTOS, BD_FUNDS,
HOMOL_INSTRUMENTOS, `4- Carga de paridades.xlsx`.

---

## Orden de ejecución

```
python RUN_PIPELINE.py --check     # verifica que estén todos los inputs
python RUN_PIPELINE.py             # corre todo
python RUN_PIPELINE.py --desde 03  # retoma desde una etapa
```

| # | Script | Qué hace | Output |
|---|--------|----------|--------|
| 00 | `00_UNIVERSO.py` | Arma el universo desde el CUBO, cruza BD_INSTRUMENTOS y BD_FUNDS, deriva familias REGS/144A y Hedge_Currency. **Excluye los defaulteados.** | `UNIVERSO` |
| 01 | `01_METRICAS.py` | Busca métricas ya calculadas: **JPM** (CEMBI/GBI), **RA** (plug-in) y **BBG** (YAS). Aplica lógica de hermanos de serie. | `METRICAS` |
| 02 | `02_CSHF.py` | Fallback: pide la TD a BBG (`DES_CASH_FLOW`) y calcula Yield/Duration. | `CSHF` |
| 03 | `03_JSONL.py` | TD propias con los eventos de Geneva; proyecta los cupones que Geneva no trae. | `PROP_JSONL` |
| 04 | `04_EXCEPCIONES.py` | Flujos entregados por el PM. **Pisa a todos los proveedores.** | `EXCEPCIONES` |
| 05 | `05_CONSOLIDA.py` | Resuelve una Yield y una Duration por posición según la cascada. Aplica DEF/PROPDEF. | `CONSOLIDADO` |
| 06 | `06_BREAKEVEN.py` | *(pendiente)* Pasa yields indexadas (UF/UDI/UVR…) a moneda local. | — |
| 07 | `07_DROPS.py` | *(pendiente)* Yield hedgeada para swapeados. | — |
| aux | `aux_REPORTE.py` | Reporte ejecutivo de cobertura. | `REPORTE_COBERTURA` |

---

## Cascada de fuentes

```
EXCEPCIONES  >  JPM  >  RA  >  BBG  >  CSHF  >  JSONL
```

- **EXCEPCIONES primero**: es decisión explícita del PM y pisa cualquier proveedor.
- **JPM y RA antes que BBG**: no consumen terminal.
- **CSHF y JSONL al final**: son reconstrucciones propias.
- Se exige la **tupla completa** (Yield *y* Duration). Con una sola métrica el
  papel no sirve y sigue a la siguiente fuente.

---

## Reglas de negocio

**Defaulteados** — `DEF` y `PROPDEF` tienen el mismo efecto: `Yield = 0`,
`Duration = 0.5` (se asume liquidación pagando su MVal en un semestre). Se
excluyen del universo en la etapa 00 y se reincorporan con esos valores en la 05.

**Moneda** — todo el pipeline trabaja en la **moneda del papel**
(`Risk_Currency`). Las conversiones ocurren al final:
- moneda indexada (CLF, UDI, UVR…) → `Requiere_Breakeven` → etapa 06
- papel con `Hedge_Currency` → `Requiere_Drop` → etapa 07

**Escalas** — el precio y la cantidad del CUBO vienen en unidades variables.
No se asumen: se prueban los candidatos `(sP, sQ)` y gana el que reproduce
`MVBook`. Igual criterio para el FX (beemining + paridades compiten y gana el
que calza; así se resuelve solo el caso ARS).

**Bases de nominal por fuente**

| Fuente | Base | Escala |
|---|---|---|
| Geneva (jsonl) | 100 del nominal **original** | `OF_ef/100` — el `Factor` ya viene en los flujos |
| BBG (CSHF) | se **deriva** sumando el principal de la TD | `Q_real/face_bbg` |
| EXCEPCIONES | 1.000.000 | `Q_real/1.000.000`, con `Q_real = Qty × sQ` |

**Flujo inicial** — `-(P_ef × Q_real) - AI_local`, en moneda local. Incluye el
devengado porque es el desembolso real; en consecuencia el **primer cupón se
cobra completo** (se ancla al último cupón pagado, no al settle).

---

## Cada cierre de mes

1. Actualizar `FECHA` y `HOJA_RA` en `pipeline_config.py`.
2. Dejar en `01_INPUTS\MERCADO\` el JPM y las curvas del mes; refrescar `RA_TIR.xlsx`.
3. Actualizar `DEFAULTEADOS.xlsx` y `EXCEPCIONES.xlsx` si hubo cambios.
4. `python RUN_PIPELINE.py --check` y luego `python RUN_PIPELINE.py`.

Las etapas 01 y 02 requieren terminal Bloomberg; la 04 y el FX requieren
acceso a beemining (`SANWS007`).
