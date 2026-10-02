"""Fuentes de mercado en archivo: JPM (CEMBI/GBI), RiskAmérica, paridades. Todo sale en decimal."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..modelo import limpiar_txt

MESES = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun", 7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic"}
MONEDA_RA = {"UF": "CLF", "CLF": "CLF", "CLP": "CLP", "USD": "USD", "$": "CLP"}


def hoja_ra(fecha: str) -> str:
    return f"{MESES[int(fecha[4:6])]}{fecha[2:4]}"


def _col(df, *cands):
    return next((c for c in cands if c in df.columns), None)


def leer_jpm(path: Path) -> pd.DataFrame:
    """ISIN → Yield (decimal), Duration (años), Fuente. CEMBI gana a GBI si un ISIN está en ambos."""
    xl = pd.ExcelFile(path)
    partes = []
    if "CEMBI" in xl.sheet_names:
        c = xl.parse("CEMBI")
        k, y, d = _col(c, "ISIN_ID", "ISIN"), _col(c, "Yield_to_Worst", "Blended_YTM"), _col(c, "IR_Duration_to_Worst", "EIR_Duration")
        partes.append(pd.DataFrame({"ISIN": limpiar_txt(c[k]), "Yield": pd.to_numeric(c[y], errors="coerce") / 100,
                                    "Duration": pd.to_numeric(c[d], errors="coerce"), "Fuente": "CEMBI"}))
    if "GBI_Embroad" in xl.sheet_names:
        g = xl.parse("GBI_Embroad")
        k, y, d = _col(g, "ISIN", "LOCAL ID"), _col(g, "YIELD", "Yield"), _col(g, "MOD DUR", "MAC DUR")
        partes.append(pd.DataFrame({"ISIN": limpiar_txt(g[k]), "Yield": pd.to_numeric(g[y], errors="coerce") / 100,
                                    "Duration": pd.to_numeric(g[d], errors="coerce"), "Fuente": "GBI"}))
    if not partes:
        raise ValueError(f"{path.name}: sin hojas CEMBI / GBI_Embroad")
    out = pd.concat(partes, ignore_index=True)
    out = out[out["ISIN"].ne("") & (out["Yield"].notna() | out["Duration"].notna())]
    return out.drop_duplicates("ISIN", keep="first").reset_index(drop=True)


def leer_ra(path: Path, hoja: str | None, respaldo: bool = False) -> pd.DataFrame:
    """RA_TIR.xlsx, hoja del mes (o la primera si `hoja` es None: archivo fechado RA_TIR_{F}.xlsx del modo diario). Con `respaldo`,
    si la hoja del mes aún no existe se usa la última (modo diario al cambiar de mes). La columna A es el nemotécnico."""
    xl = pd.ExcelFile(path)
    if hoja is None:
        hoja = xl.sheet_names[0]
    if hoja not in xl.sheet_names:
        if not respaldo:
            raise ValueError(f"{path.name} sin hoja '{hoja}'; tiene {xl.sheet_names}")
        hoja = xl.sheet_names[-1]
    r = xl.parse(hoja)
    r.attrs["hoja"] = hoja
    r = r.rename(columns={r.columns[0]: "Nemo"})
    out = pd.DataFrame({"Nemo": limpiar_txt(r["Nemo"]).str.upper(),
                        "Yield": pd.to_numeric(r[_col(r, "TIR")], errors="coerce") / 100,
                        "Duration": pd.to_numeric(r[_col(r, "DURACION", "DURACIÓN")], errors="coerce"),
                        "Moneda": limpiar_txt(r[_col(r, "MONEDA")]).str.upper().map(lambda m: MONEDA_RA.get(m, m)) if _col(r, "MONEDA") else "",
                        "Periodicidad": limpiar_txt(r[_col(r, "PERIODICIDAD_CUPONES")]).str.upper() if _col(r, "PERIODICIDAD_CUPONES") else ""})
    return out[out["Nemo"].ne("")].drop_duplicates("Nemo").reset_index(drop=True)


def leer_paridades(path: Path, settle: pd.Timestamp, max_dias: int = 10) -> dict[str, float]:
    """Último precio por nomenclatura (col 'Unnamed: 5') en las tres hojas, dentro de los max_dias previos al settle."""
    xl = pd.ExcelFile(path)
    filas = []
    for hoja in ("Data Paridad NY", "Data Paridad LDN", "Data EUR|USD OBS"):
        if hoja not in xl.sheet_names:
            continue
        d = xl.parse(hoja)
        raw = d["Date"]
        fecha = (pd.to_datetime(raw.map(lambda x: pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(x)) if pd.notna(x) else None), errors="coerce")
                 if pd.api.types.is_numeric_dtype(raw) else pd.to_datetime(raw, errors="coerce"))
        nom = limpiar_txt(d["Nomenclatura"] if "Nomenclatura" in d.columns else d["Unnamed: 5"])
        f = pd.DataFrame({"Fecha": fecha, "Nom": nom, "Precio": pd.to_numeric(d["Price"], errors="coerce"), "Hoja": hoja})
        filas.append(f[f["Fecha"].between(settle - pd.Timedelta(days=max_dias), settle) & f["Nom"].ne("") & f["Precio"].gt(0)])
    if not filas:
        return {}
    df = pd.concat(filas).sort_values("Fecha", ascending=False)
    out = {}
    # USDCLP y EURCLP salen de la hoja OBS (cierre observado), el resto de NY; primera aparición = más reciente
    for _, r in df.iterrows():
        if r["Nom"] in ("USDCLP", "EURCLP") and r["Hoja"] != "Data EUR|USD OBS":
            continue
        out.setdefault(r["Nom"], float(r["Precio"]))
    return out
