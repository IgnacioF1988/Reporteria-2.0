# Datamart (versiones publicadas de cada cierre)

`cierres/cierre=YYYYMMDD/version=NNN/` es inmutable: una tabla Parquet por hoja del reporte, `posiciones.parquet` con todas las
columnas de la cartera, `insumos/` (copias de los archivos sin fecha y hashes de los demás) y `corrida.json`. `publicacion.json`
por cierre lleva el historial (PUBLICADA, REEXPRESADA + motivo). Los borradores viven en `02_OUTPUTS/{F}/borradores/` (fuera de git);
`reporteria publicar --fecha F` copia uno aquí. Nunca editar a mano; `reporteria reporte --fecha F [--publicada|--version N|--conocimiento D]`
regenera el Excel y `reporteria versiones` lista lo que hay.
