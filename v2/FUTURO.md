# Iteraciones futuras (fuera del alcance v1, documentadas para no perderlas)

1. RA: convertir TIR a yield efectiva equivalente según PERIODICIDAD_CUPONES (hoy se usa tal cual).
2. Flotantes propios (CDI/TIIE/TAB30/CHIBPROM/CPI): proyección con curva forward y recálculo de XIRR; validar el supuesto "proveedor externo ya entrega nominal local".
3. Fuente ATRIBUTOS: tabla de desarrollo desde Atributos_*.xlsx (Bullet/Sinkable/Zero, Cpn Rate, Periodicity, Next_Cpn Date, Upfront Fee) entre EXCEPCIONES y JPM.
4. Drop inverso: papel en moneda local swapeado a USD en fondo USD.
5. Conector a fuente real de pactos, simultáneas y tasas de DAP (hoy regla fija en REGLAS.xlsx).
6. Refresco automático de bond_schedule.jsonl y HOMOL (hoy snapshot; se alerta antigüedad).
7. Fallback de tenores para México (MPSW, MPBSF) y curvas para ARS/UYU en drops.
8. Breakeven flujo a flujo cuando exista tabla de desarrollo (hoy al plazo de la duration).
9. Atribución AW/DW vs mes anterior (efecto peso / efecto yield).
10. Tratamiento fino de pasivos (bank debt con tasa real, derivados por pata).
11. Exportación a base corporativa (hoy Excel + JSON).
12. Alerta de cupón negativo en TDs propias (F4 del legacy, nunca implementada).
13. Índice desde `Atributos_{FONDO}.xlsx` del operador (columna `Index Name`): hoy el override por fondo va en REGLAS/overrides_atributo (`Field=Indice`).
14. Curvas de drops para ARS/UYU y basis para PEN/BRL (hoy DIRECT con una sola curva, como el legacy).
15. XCCY para papeles hedgeados que no están en USD (EUR/GBP → moneda local): hoy solo se usa el XCCY de BBG si existe.
