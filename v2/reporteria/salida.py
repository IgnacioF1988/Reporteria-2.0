"""Excel final del cierre. Yields en decimal con formato de porcentaje solo en la celda."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

NAVY = "1F3864"
COLS_CARTERA = ["Pos_ID", "ID_Fund", "Fondo", "PK2", "BalanceSheet", "Name_Instrumento", "ISIN", "Risk_Country",
                "Risk_Currency", "Moneda_PK2", "Investment_Type_Code", "Issue_Type_Code", "Coupon_Type_Code",
                "Base_Name", "Familia", "ISIN_Hermanos", "Hedge_Currency", "Hedge_Origen", "Indice", "Overrides",
                "BalSheetKey", "Bucket", "Bucket_Origen", "Bucket_Orden", "Ficha_FI", "FX_Exposure", "Tratamiento",
                "Yield", "Duration", "Yield_Moneda", "Yield_XCCY", "Fuente", "Origen", "Etapa", "Estado", "Estado_DEF", "CalcType",
                "CalcType_exportable", "Motivo", "TotalMVal", "MVBook", "AI", "LocalPrice", "Qty", "Factor"]


def _formatear(ws, pct_cols: set[str]):
    for c in ws[1]:
        c.fill, c.font = PatternFill("solid", fgColor=NAVY), Font(color="FFFFFF", bold=True, size=9)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    for j, col in enumerate(ws.iter_cols(1, ws.max_column), 1):
        ancho = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(j)].width = min(max(ancho + 2, 9), 45)
        if col[0].value in pct_cols:
            for c in col[1:]:
                c.number_format = "0.00%"


def escribir_excel(hojas: dict[str, pd.DataFrame], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            (df if len(df) else pd.DataFrame({"info": ["sin filas"]})).to_excel(w, sheet_name=nombre[:31], index=False)
            _formatear(w.sheets[nombre[:31]], {"Yield", "Yield_Local", "Yield_Drop", "AW", "DW"})
    return path
