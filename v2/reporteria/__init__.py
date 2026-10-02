"""Reportería: Yield y Duration por posición para los fondos."""
import os

# Con numpy/scipy de anaconda (MKL + runtime Fortran de Intel) un Ctrl-C en la consola aborta el proceso entero
# (`forrtl: error (200): program aborting due to control-C event`) antes de que Python pueda levantar KeyboardInterrupt.
# Debe fijarse antes de que cargue la DLL, es decir antes del primer `import numpy`: por eso vive aquí.
os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")
