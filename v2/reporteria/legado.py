"""Siembra la caché Bloomberg / FX a partir de los outputs del pipeline legacy (para correr un cierre sin terminal).

    reporteria importar-cache-legacy --fecha 20260731 --legacy <carpeta con METRICAS_/CSHF_/CURVAS_DROPS_ o LEGACY_*>
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .adaptadores.bbg import _archivo, _ruta_bds
from .config import CURVAS_DROPS, INDICES
from .curvas import CAMPO_CURVA
from .modelo import limpiar_txt

CAMPO_XCCY = "YAS_XCCY_FIXED_COUPON_EQUIVALENT"


def _buscar(legacy: Path, nombre: str, fecha: str, ext: str = "xlsx") -> Path | None:
    for cand in (legacy / f"{nombre}_{fecha}.{ext}", legacy / f"LEGACY_{nombre}_{fecha}.{ext}",
                 legacy / "02_OUTPUTS" / fecha / f"{nombre}_{fecha}.{ext}", legacy / "01_INPUTS" / "MERCADO" / f"{nombre}_{fecha}.{ext}"):
        if cand.exists():
            return cand
    return None


def _ticker(isin: str, origen: str) -> str:
    o = str(origen).strip().upper()
    if o.startswith("HERMANO:"):
        return f"{o.split(':', 1)[1].strip()} Corp"
    return f"{isin}@BGN Corp" if o == "BGN" else f"{isin} Corp"


def _guardar_bdp(cache: Path, campo: str, fecha: str, overrides: dict, valores: dict[str, float]) -> Path:
    p = _archivo(cache, campo, fecha, overrides)
    if p.exists():
        prev = pd.read_csv(p)
        valores = {**dict(zip(prev["ticker"].astype(str), prev["valor"])), **valores}
    cache.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": list(valores), "valor": list(valores.values())}).to_csv(p, index=False)
    return p


def importar_cache_legacy(legacy: Path, cache: Path, fecha: str) -> dict[str, int]:
    """Devuelve conteos por archivo generado. Los valores quedan como los entrega BBG (yields en %)."""
    legacy, cache, out = Path(legacy), Path(cache), {}
    met = _buscar(legacy, "METRICAS", fecha)
    if met:
        xl = pd.ExcelFile(met)
        m = pd.concat([xl.parse(h) for h in ("con_ISIN", "sin_ISIN") if h in xl.sheet_names], ignore_index=True)
        m["ISIN"] = limpiar_txt(m["ISIN"])
        m = m[m["ISIN"].ne("")].drop_duplicates("ISIN")
        yld, dur, xccy = {}, {}, {}
        for _, r in m.iterrows():
            if pd.notna(r.get("BBG_Yield")):
                yld[_ticker(r["ISIN"], r.get("Origen_BBG_Yield", ""))] = float(r["BBG_Yield"])
            if pd.notna(r.get("BBG_Duration")):
                dur[_ticker(r["ISIN"], r.get("Origen_BBG_Duration", ""))] = float(r["BBG_Duration"])
            if pd.notna(r.get("BBG_XCCY_Yield")) and str(r.get("Hedge_Currency", "")).strip():
                xccy.setdefault(str(r["Hedge_Currency"]).strip().upper(), {})[_ticker(r["ISIN"], r.get("Origen_BBG_XCCY", ""))] = float(r["BBG_XCCY_Yield"])
        ov = {"settle_dt": fecha}
        out["YAS_BOND_YLD"] = len(yld); _guardar_bdp(cache, "YAS_BOND_YLD", fecha, ov, yld)
        out["YAS_MOD_DUR"] = len(dur); _guardar_bdp(cache, "YAS_MOD_DUR", fecha, ov, dur)
        for ccy, vals in xccy.items():
            out[f"XCCY_{ccy}"] = len(vals)
            _guardar_bdp(cache, CAMPO_XCCY, fecha, {**ov, "YAS_XCCY_FOREIGN_CURRENCY": ccy}, vals)
    cshf = _buscar(legacy, "CSHF", fecha)
    if cshf:
        td = pd.read_excel(cshf, sheet_name="td_detalle")
        td["ISIN"] = limpiar_txt(td["ISIN"])
        n = 0
        for isin, g in td[td["ISIN"].ne("")].groupby("ISIN"):
            g = g.drop_duplicates("Fecha").sort_values("Fecha")
            p = _ruta_bds(cache, "DES_CASH_FLOW", f"{isin} Corp")
            p.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"Fecha": pd.to_datetime(g["Fecha"]).dt.strftime("%Y-%m-%d"), "Cupon": float("nan"),
                          "Principal": float("nan"), "Flujo": g["Flujo_baseFACE"].astype(float),
                          "Face": g["face_bbg"].astype(float)}).to_csv(p, index=False)
            n += 1
        out["DES_CASH_FLOW"] = n
    curvas = _buscar(legacy, "CURVAS_DROPS", fecha, "csv")
    if curvas:      # respaldo del legacy: Curva = "{CCY}_{local|basis|usd}" → ticker de config.CURVAS_DROPS
        cv = pd.read_csv(curvas)
        n = 0
        for nombre, g in cv.groupby("Curva"):
            ccy, _, pieza = str(nombre).partition("_")
            tick = CURVAS_DROPS.get(ccy, {}).get(pieza)
            if not tick:
                continue
            p = _ruta_bds(cache, CAMPO_CURVA, f"{tick} Index")
            p.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"Tenor": g["tenor"].astype(str), "Tenor Ticker": g["ticker"].astype(str), "Mid Yield": g["mid"].astype(float)}).to_csv(p, index=False)
            n += 1
        out[CAMPO_CURVA] = n
    atr = _buscar(legacy, "ATRIBUTOS", fecha)
    if atr:         # Index_Type derivado de BBG → INFLATION_LINKED_INDICATOR / CPN_TYP / RESET_IDX por ISIN
        a = pd.read_excel(atr, sheet_name="atributos")
        a["ISIN"] = limpiar_txt(a["ISIN"])
        a = a[a["ISIN"].ne("") & limpiar_txt(a["Origen_Index_Type"]).str.startswith("BBG")].drop_duplicates("ISIN")
        infl, cpn, reset = {}, {}, {}
        for _, r in a.iterrows():
            t, it = f"{r['ISIN']} Corp", str(r["Index_Type"]).strip().upper()
            infl[t] = "Y" if it in INDICES and INDICES[it]["cat"] == "REAL" else "N"
            if pd.notna(r.get("CPN_TYP")):
                cpn[t] = str(r["CPN_TYP"]).strip().upper()
            if it.startswith("NUEVO:"):
                reset[t], cpn[t] = it.split(":", 1)[1], "FLOATING"
        out["INFLATION_LINKED_INDICATOR"] = len(infl); _guardar_bdp(cache, "INFLATION_LINKED_INDICATOR", fecha, {}, infl)
        out["CPN_TYP"] = len(cpn); _guardar_bdp(cache, "CPN_TYP", fecha, {}, cpn)
        out["RESET_IDX"] = len(reset); _guardar_bdp(cache, "RESET_IDX", fecha, {}, reset)
    return out


# ── Migración de manuales del legacy a REGLAS.xlsx ──────────────────────────────────────────────────────────────
def _fondos_alias(bd_funds: Path | pd.DataFrame, homol_funds_path: Path | None) -> dict[str, int]:
    """Nombre (upper) → ID_Fund desde BD_FUNDS (FundShortName, NombreTupungato, FundName; ruta o tabla dim_fondos) y HOMOL_FUNDS (Portfolio)."""
    f = bd_funds if isinstance(bd_funds, pd.DataFrame) else pd.read_excel(bd_funds)
    out = {}
    for col in ("FundShortName", "NombreTupungato", "FundName"):
        if col in f.columns:
            out.update({str(k).strip().upper(): int(v) for k, v in zip(f[col], f["ID_Fund"]) if pd.notna(k) and pd.notna(v)})
    if homol_funds_path and Path(homol_funds_path).exists():
        h = pd.read_excel(homol_funds_path, sheet_name="HOMOL_FUNDS")
        out.update({str(k).strip().upper(): int(v) for k, v in zip(h["Portfolio"], h["ID_Fund"]) if pd.notna(k) and pd.notna(v)})
    return out


def _split_pk2(pk2: str) -> tuple[int | None, int | None]:
    a, _, b = str(pk2).strip().partition("-")
    try:
        return int(a), (int(b) if b else None)
    except ValueError:
        return None, None


def migrar_cajas(template: Path, alias: dict[str, int]) -> pd.DataFrame:
    """Template_Cajas.xlsx (corporativo) → hoja `cajas`: ID_Fund, PK2, Indice_Referencia, Spread_Anual (decimal), Dias."""
    xl = pd.ExcelFile(template)
    hojas = [h for h in xl.sheet_names if h != "Reportes"]
    partes = []
    for h in hojas:
        d = xl.parse(h)
        if "PK2" not in d.columns or d.empty:
            continue
        partes.append(d)
    if not partes:
        return pd.DataFrame(columns=["ID_Fund", "PK2", "Indice_Referencia", "Spread_Anual", "Dias", "Comentario"])
    tc = pd.concat(partes, ignore_index=True)
    ind = tc["Indice Referencia"] if "Indice Referencia" in tc.columns else pd.Series("", index=tc.index)
    return pd.DataFrame({
        "ID_Fund": tc["FundShortName"].astype(str).str.strip().str.upper().map(alias).astype("Int64"),
        "PK2": tc["PK2"].astype(str).str.strip(),
        "Indice_Referencia": ind.where(ind.notna(), ""),
        "Spread_Anual": pd.to_numeric(tc.get("Spread (Anual)"), errors="coerce"),
        "Dias": pd.to_numeric(tc.get("Fecha_Vencimiento"), errors="coerce"),
        "Comentario": "migrado de Template_Cajas " + tc["Name_Instrumento"].astype(str),
    }).drop_duplicates(["ID_Fund", "PK2"]).reset_index(drop=True)


def _buscar_manual(legacy: Path, nombre: str) -> Path | None:
    for d in (legacy, legacy / "01_INPUTS" / "MANUALES", legacy / "01_INPUTS" / "GENEVA", legacy / "legacy_manuales"):
        if (d / nombre).exists():
            return d / nombre
    return None


def _atributos_legacy(legacy: Path) -> list[Path]:
    out = []
    for d in (legacy, legacy / "01_INPUTS" / "MANUALES", legacy / "01_INPUTS" / "GENEVA", legacy / "legacy_manuales"):
        out += [p for p in d.glob("Atributos_*.xlsx") if not p.name.startswith("~$")] if d.is_dir() else []
    return sorted(set(out))


def migrar_manuales(legacy: Path, alias: dict[str, int], ids_instrumento: set[int] | None = None, incluir_defaulteados: bool = False,
                    template_cajas: Path | None = None) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """({hoja: filas nuevas en el esquema de REGLAS}, informe). Nombres de fondo → ID_Fund; PK2 → ID/SubID_Instrumento."""
    from .indices import normalizar_indice
    legacy = Path(legacy)
    nuevas: dict[str, list[dict]] = {"clasificacion": [], "cajas": [], "defaulteados": [], "overrides_valor": [], "overrides_atributo": []}
    informe = []

    def fondo(nombre) -> int | None:
        return alias.get(str(nombre).strip().upper())

    def avisar(archivo, leidas, generadas, avisos):
        informe.append(dict(Archivo=archivo, Filas_leidas=leidas, Filas_generadas=generadas, Avisos="; ".join(sorted(set(avisos))) if avisos else ""))

    def sin_maestro(iid, avisos):
        if ids_instrumento is not None and iid is not None and iid not in ids_instrumento:
            avisos.append(f"ID_Instrumento {iid} no está en BD_INSTRUMENTOS")

    fip = _buscar_manual(legacy, "FIP.xlsx")
    if fip:
        d = pd.read_excel(fip); avisos = []
        for _, r in d.iterrows():
            fid = fondo(r["Fund_Name"])
            if fid is None:
                avisos.append(f"fondo desconocido {r['Fund_Name']}"); continue
            if str(r.get("Treatment", "")).strip().upper() == "EQUITY":
                nuevas["clasificacion"].append(dict(ID=None, ID_Fund=fid, Criterio="PK2", Valor=str(r["PK2"]).strip(), Bucket="Equity", Tratamiento="",
                                                    Comentario=f"migrado de FIP.xlsx: {r.get('Name_Instrumento', '')} Treatment Equity"))
        avisar("FIP.xlsx", len(d), len(nuevas["clasificacion"]), avisos)
    for atr in _atributos_legacy(legacy):
        d = pd.read_excel(atr); avisos, n = [], 0
        col = next((c for c in d.columns if str(c).strip().lower().replace("_", " ") == "index name"), None)
        if col is None or "PK2" not in d.columns:
            avisar(atr.name, len(d), 0, ["sin columna 'Index Name' o PK2"]); continue
        for _, r in d.iterrows():
            idx = normalizar_indice(r[col])
            if not idx or str(r.get("Index", "YES")).strip().upper() == "NO":
                continue
            fid = fondo(r.get("Fund_Name", atr.stem.split("_", 1)[-1]))
            if fid is None:
                avisos.append(f"fondo desconocido {r.get('Fund_Name', atr.stem)}"); continue
            iid, sub = _split_pk2(r["PK2"])
            if iid is None:
                avisos.append(f"PK2 inválido {r['PK2']}"); continue
            sin_maestro(iid, avisos)
            if idx not in INDICES and idx not in ("NOMINAL",):
                avisos.append(f"índice {r[col]} → {idx} sin curvas en config.INDICES")
            nuevas["overrides_atributo"].append(dict(ID_Fund=fid, ID_Instrumento=iid, SubID_Instrumento=sub, Field="Indice", Value=idx,
                                                     Fecha_Desde=None, Fecha_Fin=None, Comentario=f"migrado de {atr.name}: {r.get('Name_Instrumento', '')} Index Name={r[col]}"))
            n += 1
        avisar(atr.name, len(d), n, avisos)
    deff = _buscar_manual(legacy, "DEFAULTEADOS.xlsx")
    if deff:
        d = pd.read_excel(deff); avisos, n = [], 0
        if incluir_defaulteados:
            # El legacy aplicaba DEF/PROPDEF por PK2 en TODOS los fondos (ignoraba la columna Fondo): una fila global por instrumento
            por_iid: dict[int, dict] = {}
            for _, r in d.iterrows():
                iid, _ = _split_pk2(r["PK2"])
                if iid is None:
                    avisos.append(f"PK2 inválido {r['PK2']}"); continue
                est = str(r.get("DEF", "")).strip().upper()
                if est not in ("DEF", "PROPDEF"):
                    avisos.append(f"estado {est} inválido para {r['PK2']}"); continue
                if fondo(r["Fondo"]) is None:
                    avisos.append(f"fondo desconocido {r['Fondo']} (informativo: la marca es global)")
                e = por_iid.setdefault(iid, dict(estados=set(), fondos=[], nombre=str(r.get("Instrumento", "") or "")))
                e["estados"].add(est); e["fondos"].append(str(r["Fondo"]).strip())
            for iid, e in por_iid.items():
                est = "DEF" if "DEF" in e["estados"] else "PROPDEF"
                if len(e["estados"]) > 1:
                    avisos.append(f"{iid} marcado DEF y PROPDEF en fondos distintos: se usa DEF")
                sin_maestro(iid, avisos)
                nuevas["defaulteados"].append(dict(ID_Fund=None, ID_Instrumento=iid, Estado=est, Fecha_Desde=None, Fecha_Fin=None,
                                                   Comentario=f"migrado de DEFAULTEADOS.xlsx: {e['nombre']} (fondos: {', '.join(sorted(set(e['fondos'])))})"))
                n += 1
        else:
            avisos.append("omitido: use --incluir-defaulteados para traerlo (el DEFAULTED.xlsx corporativo está desactualizado)")
        avisar("DEFAULTEADOS.xlsx", len(d), n, avisos)
    ov = _buscar_manual(legacy, "OVERRIDES.xlsx")
    if ov:
        d = pd.read_excel(ov); avisos, n = [], 0
        for _, r in d.iterrows():
            y = pd.to_numeric(r.get("Yield"), errors="coerce")
            if pd.isna(y):
                continue
            fid = fondo(r.get("Fund_Name", ""))
            if fid is None:
                avisos.append(f"fondo desconocido {r.get('Fund_Name', '')}"); continue
            iid, sub = _split_pk2(r["PK2"])
            if iid is None:
                avisos.append(f"PK2 inválido {r['PK2']}"); continue
            if abs(y) > 1:
                avisos.append(f"{r['PK2']}: yield {y} se asume en % y se divide por 100"); y = y / 100
            sin_maestro(iid, avisos)
            nuevas["overrides_valor"].append(dict(ID_Fund=fid, ID_Instrumento=iid, SubID_Instrumento=sub, Yield=float(y),
                                                  Duration=pd.to_numeric(r.get("Dur"), errors="coerce"), Fecha_Desde=None, Fecha_Fin=None,
                                                  Moneda=str(r.get("Currency", "") or ""), Fuente=str(r.get("Source", "") or ""),
                                                  Comentario=f"migrado de OVERRIDES.xlsx: {r.get('Comment', '')}"))
            n += 1
        avisar("OVERRIDES.xlsx", len(d), n, avisos)
    if template_cajas and Path(template_cajas).exists():
        c = migrar_cajas(template_cajas, alias)
        sin_fondo = c["ID_Fund"].isna().sum()
        nuevas["cajas"] = c[c["ID_Fund"].notna()].to_dict("records")
        avisar(Path(template_cajas).name, len(c), len(nuevas["cajas"]), [f"{sin_fondo} filas con fondo desconocido"] if sin_fondo else [])
    if (legacy / "Cajas_jul.xlsx").exists():
        avisar("Cajas_jul.xlsx", 0, 0, ["inventario de cajas sin índice ni spread: no se migra (los valores salen de Template_Cajas)"])
    return {k: pd.DataFrame(v) for k, v in nuevas.items() if v}, pd.DataFrame(informe, columns=["Archivo", "Filas_leidas", "Filas_generadas", "Avisos"])


LLAVES = {"clasificacion": ["ID_Fund", "Criterio", "Valor"], "cajas": ["ID_Fund", "PK2"], "defaulteados": ["ID_Fund", "ID_Instrumento"],
          "overrides_valor": ["ID_Fund", "ID_Instrumento", "SubID_Instrumento"], "overrides_atributo": ["ID_Fund", "ID_Instrumento", "SubID_Instrumento", "Field"]}


def _txt_llave(x) -> str:
    """Texto comparable de una celda de llave: 20.0 (entero leído como float) → "20"; el texto (PK2, regex "1.05") queda intacto."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x).strip().upper()


def fusionar_reglas(reglas_path: Path, nuevas: dict[str, pd.DataFrame], salida: Path, fondos_validos: set[int] | None = None) -> dict[str, int]:
    """Agrega las filas nuevas a cada hoja de REGLAS sin duplicar por llave natural, renumera clasificacion.ID, valida y escribe."""
    from .lectura.reglas import leer_reglas
    hojas = pd.read_excel(reglas_path, sheet_name=None)
    agregadas = {}
    for hoja, df in nuevas.items():
        base = hojas[hoja]
        llave = LLAVES[hoja]
        df = df.copy()
        for c in base.columns:
            if c not in df.columns:
                df[c] = None
        def _k(d):
            return d[llave].apply(lambda r: "|".join(_txt_llave(x) for x in r), axis=1)
        existentes = set(_k(base)) if len(base) else set()
        df = df[~_k(df).isin(existentes)]
        agregadas[hoja] = len(df)
        hojas[hoja] = pd.concat([base, df[base.columns]], ignore_index=True)
        if hoja == "clasificacion":
            hojas[hoja]["ID"] = range(1, len(hojas[hoja]) + 1)
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(salida) as w:
        for n, d in hojas.items():
            d.to_excel(w, sheet_name=n, index=False)
    leer_reglas(salida, fondos_validos)        # lanza ValueError si la fusión no es válida
    return agregadas


# ── Dimensionales (H8): BD_BalanceSheet + BD_FX_Exposure_* + catálogos → dimensionales.duckdb ─────────────────────────
def migrar_dimensionales(bix_dirs: list[Path]) -> tuple["Dimensionales", pd.DataFrame]:
    """Compacta BD_BalanceSheet (genérica y variante _MRCLP), las BD_FX_Exposure_{fondo} y los catálogos.

    FX: la tabla de MLDL pasa a filas genéricas (todos los fondos); las demás tablas por fondo se compactan con ID_Fund.
    Variante `Investment_Type_CarteraFI_MRCLP`: solo las llaves que difieren de la genérica, como filas ID_Fund=MRCLP.
    Devuelve (Dimensionales, informe[Fuente, Filas_leidas, Filas_generadas, Avisos]).
    """
    import glob as _glob
    from .adaptadores.dim import ARCHIVO_CATALOGO, Dimensionales
    from .config import _en
    from .dim import ATRIBUTOS, COLS_LLAVE, compactar, fusionar_filas, normalizar
    from .lectura.maestros import _hoja, fondo_de_fx_exposure, leer_fx_exposure, normalizar_bd_funds
    from .modelo import CODIGOS

    bix_dirs = [Path(d) for d in bix_dirs]
    informe, partes = [], []
    cols = ["BalanceSheet"] + CODIGOS

    fondos = _hoja(_en(bix_dirs, "BD_FUNDS.xlsx"), "BD_FUNDS")
    bd_funds = normalizar_bd_funds(fondos)
    informe.append(("BD_FUNDS", len(fondos), len(fondos), ""))

    bs = _hoja(_en(bix_dirs, "BD_BalanceSheet.xlsx"), "BalSheet").rename(columns={"ASSET_TYPE": "BalanceSheet"})
    etiquetas = {"Bucket": "Investment_Type_CarteraFI", "Ficha_FI": "Investment_Type_Ficha_FI"}
    avisos = []
    for atributo, col in etiquetas.items():
        if col not in bs.columns:
            avisos.append(f"sin columna {col}")
            continue
        t = bs.rename(columns={col: atributo})
        vacios = int(limpiar_txt(t[atributo]).eq("").sum())
        if vacios:
            avisos.append(f"{vacios} llaves sin {atributo} (heredan el de sus vecinas cuando una fila comodín las cubre; el resto sale en plantilla_dim)")
        f = compactar(t, cols, atributo)
        f["Origen_Migracion"] = f"BD_BalanceSheet.{col}"
        partes.append(f)
        informe.append((f"BD_BalanceSheet.{col}", len(bs), len(f), "; ".join(avisos)))
        avisos = []
    base = normalizar(pd.concat(partes, ignore_index=True))
    variantes = [c for c in bs.columns if c.startswith("Investment_Type_CarteraFI_")]
    for col in variantes:
        sufijo = col.replace("Investment_Type_CarteraFI_", "").upper()
        m = bd_funds[bd_funds["FundShortName"].str.upper() == sufijo]
        if m.empty:
            informe.append((f"BD_BalanceSheet.{col}", len(bs), 0, f"fondo '{sufijo}' no está en BD_FUNDS: se omite"))
            continue
        fid = int(m["ID_Fund"].iloc[0])
        t = bs.drop(columns=["Investment_Type_CarteraFI"]).rename(columns={col: "Bucket"})
        f = compactar(t, cols, "Bucket", base=base, fondo=fid)
        f["Origen_Migracion"] = f"BD_BalanceSheet.{col}"
        partes.append(f)
        informe.append((f"BD_BalanceSheet.{col}", len(bs), len(f), f"ID_Fund={fid}; reemplaza las REGLAS/clasificacion con Criterio=BalSheetKey de ese fondo"))

    fx_paths = sorted({Path(p) for d in bix_dirs for p in _glob.glob(str(d / "BD_FX_Exposure_*.xlsx")) if not Path(p).name.startswith("~$")})
    for p in fx_paths:
        fid = fondo_de_fx_exposure(p, bd_funds)
        tabla = leer_fx_exposure(p)
        cfx = [c for c in tabla.columns if c != "FX_Exposure"]
        if fid is None:
            informe.append((p.name, len(tabla), 0, "fondo no encontrado en BD_FUNDS: se omite"))
            continue
        generica = p.stem.upper().endswith("_MLDL")
        f = compactar(tabla, cfx, "FX_Exposure", fijas=()) if generica else compactar(tabla, cfx, "FX_Exposure", fijas=(), fondo=fid)
        if generica:
            f["ID_Fund"] = pd.NA
        f["Origen_Migracion"] = p.name
        partes.append(f)
        informe.append((p.name, len(tabla), len(f), "genérica (todos los fondos)" if generica else f"ID_Fund={fid}"))

    clasif = fusionar_filas(partes)
    catalogos = {}
    for tabla, archivo in ARCHIVO_CATALOGO.items():
        ruta = _en(bix_dirs, archivo)
        if ruta.exists():
            df = _hoja(ruta, archivo.replace(".xlsx", ""))
            catalogos[tabla] = df
            informe.append((archivo, len(df), len(df), ""))
        else:
            informe.append((archivo, 0, 0, "no encontrado"))
    monedas = _hoja(_en(bix_dirs, "BD_Monedas.xlsx"), "Monedas")
    informe.append(("BD_Monedas", len(monedas), len(monedas), ""))
    ruta_yld = _en(bix_dirs, "BD_YLD_FLAG.xlsx")
    yld = _hoja(ruta_yld, "BD_YLD_FLAG") if ruta_yld.exists() else pd.DataFrame(columns=["CalcType_final", "CalcType_exportable"])
    informe.append(("BD_YLD_FLAG", len(yld), len(yld), "" if ruta_yld.exists() else "no encontrado"))
    import datetime as _dt
    dims = Dimensionales(clasificacion=clasif, catalogos=catalogos, fondos=fondos, monedas=monedas, yld_flag=yld,
                         meta={"importado": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "origen": "; ".join(str(d) for d in bix_dirs),
                               "version_esquema": "1"})
    return dims, pd.DataFrame(informe, columns=["Fuente", "Filas_leidas", "Filas_generadas", "Avisos"])
