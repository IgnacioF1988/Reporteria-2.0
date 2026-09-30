"""Frontera con Bloomberg. Toda corrida en vivo deja caché en CSV; los tests y --sin-bbg solo leen la caché."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import pandas as pd


class Bloomberg(Protocol):
    def bdp(self, tickers: list[str], campo: str, **overrides) -> pd.Series: ...        # puntual; index=ticker
    def historico(self, tickers: list[str], campo: str, fecha: str) -> pd.Series: ...  # valor del campo AL cierre
    def bds(self, ticker: str, campo: str, **overrides) -> pd.DataFrame: ...           # bulk (tablas de desarrollo, curvas)


def _ruta_bds(dir_cache: Path, campo: str, ticker: str) -> Path:
    return dir_cache / f"bds_{campo}" / (re.sub(r"[^A-Za-z0-9@._ -]", "_", ticker) + ".csv")


def _archivo(dir_cache: Path, campo: str, fecha: str, overrides: dict, tipo: str = "bdp") -> Path:
    suf = "_".join(f"{k}-{re.sub(r'[^A-Za-z0-9]', '', str(v))}" for k, v in sorted(overrides.items()))
    return dir_cache / f"{tipo}_{campo}_{fecha}{'_' + suf if suf else ''}.csv"


class FixtureBloomberg:
    """Solo caché: lo que no está, no está (Series vacía para esos tickers). Nunca toca la terminal."""

    def __init__(self, dir_cache: Path, fecha: str):
        self.dir, self.fecha = Path(dir_cache), fecha
        self.pedidos: list[tuple[str, tuple[str, ...]]] = []

    def _leer(self, campo, overrides, tipo="bdp") -> pd.Series:
        p = _archivo(self.dir, campo, self.fecha, overrides, tipo)
        if not p.exists():
            return pd.Series(dtype=float)
        d = pd.read_csv(p)
        num = pd.to_numeric(d["valor"], errors="coerce")
        vals = num.values if num.notna().all() else num.where(num.notna(), d["valor"]).values     # texto (RESET_IDX, CPN_TYP)
        return pd.Series(vals, index=d["ticker"].astype(str))

    def bdp(self, tickers, campo, **overrides):
        self.pedidos.append((campo, tuple(tickers)))
        s = self._leer(campo, overrides)
        return s[s.index.isin(tickers)]

    def historico(self, tickers, campo, fecha):
        self.pedidos.append((f"{campo}@{fecha}", tuple(tickers)))
        s = self._leer(campo, {}, "bdh")
        return s[s.index.isin(tickers)]

    def bds(self, ticker, campo, **overrides):
        self.pedidos.append((f"bds:{campo}", (ticker,)))
        p = _ruta_bds(self.dir, campo, ticker)
        return pd.read_csv(p) if p.exists() else pd.DataFrame()


class CacheBloomberg(FixtureBloomberg):
    """Lee de caché y consulta a `inner` solo lo que falta, guardándolo."""

    def __init__(self, inner: Bloomberg, dir_cache: Path, fecha: str):
        super().__init__(dir_cache, fecha)
        self.inner = inner

    def _completar(self, tickers, campo, overrides, tipo, pedir):
        cache = self._leer(campo, overrides, tipo)
        faltan = [t for t in tickers if t not in cache.index]
        if faltan:
            nuevos = pedir(faltan)
            cache = pd.concat([cache, nuevos]) if len(nuevos) else cache
            self.dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"ticker": cache.index, "valor": cache.values}).to_csv(
                _archivo(self.dir, campo, self.fecha, overrides, tipo), index=False)
        return cache[cache.index.isin(tickers)]

    def bdp(self, tickers, campo, **overrides):
        self.pedidos.append((campo, tuple(tickers)))
        return self._completar(tickers, campo, overrides, "bdp", lambda f: self.inner.bdp(f, campo, **overrides))

    def historico(self, tickers, campo, fecha):
        self.pedidos.append((f"{campo}@{fecha}", tuple(tickers)))
        return self._completar(tickers, campo, {}, "bdh", lambda f: self.inner.historico(f, campo, fecha))

    def bds(self, ticker, campo, **overrides):
        self.pedidos.append((f"bds:{campo}", (ticker,)))
        p = _ruta_bds(self.dir, campo, ticker)
        if p.exists():
            return pd.read_csv(p)
        d = self.inner.bds(ticker, campo, **overrides)
        p.parent.mkdir(parents=True, exist_ok=True)
        d.to_csv(p, index=False)          # también la respuesta vacía: evita repreguntar
        return d


class XbbgBloomberg:
    """Terminal real vía xbbg. Import diferido: solo existe en Windows con terminal.

    Soporta xbbg 0.7 (respuestas anchas: index=ticker, columnas=campos; overrides como kwargs) y xbbg ≥ 1.0 (respuestas
    largas ticker/field/value; overrides en MAYÚSCULAS vía `overrides=`). Los overrides del pipeline llegan en cualquier
    capitalización (settle_dt, SETTLE_DT, YAS_XCCY_FOREIGN_CURRENCY) y acá se normalizan a como los espera Bloomberg.
    """

    def __init__(self, lote: int = 100, blp=None, version: str | None = None):
        if blp is None:
            import xbbg
            from xbbg import blp
            version = version or getattr(xbbg, "__version__", "0")
        self.blp, self.lote = blp, lote
        self.nueva_api = int(str(version or "0").split(".")[0].split("+")[0] or 0) >= 1

    @staticmethod
    def _ov(overrides: dict) -> dict:
        out = {}
        for k, v in overrides.items():
            if v is None:
                continue
            out[str(k).strip().upper()] = v.strftime("%Y%m%d") if hasattr(v, "strftime") else str(v)
        return out

    def _llamar(self, fn, *args, **kw):
        ov = self._ov(kw.pop("overrides", {}) or {})
        if self.nueva_api:                       # ≥1.0: pandas explícito (el backend por defecto puede ser narwhals/arrow)
            kw = {"backend": "pandas", **kw}
            return self._a_pandas(fn(*args, overrides=ov, **kw) if ov else fn(*args, **kw))
        return fn(*args, **ov, **kw)

    @staticmethod
    def _a_pandas(res):
        """Lo que devuelva xbbg → DataFrame de pandas (narwhals, pyarrow, polars o pandas)."""
        if res is None or isinstance(res, pd.DataFrame):
            return res
        for metodo in ("to_pandas", "to_native"):
            if hasattr(res, metodo):
                out = getattr(res, metodo)()
                if isinstance(out, pd.DataFrame):
                    return out
                if hasattr(out, "to_pandas"):
                    return out.to_pandas()
        return pd.DataFrame(res)

    @staticmethod
    def _valor(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        if s == "" or s.lower() in ("nan", "none", "<na>"):
            return None
        try:
            return float(s)
        except ValueError:
            return s

    def _puntual(self, res, campo: str) -> dict:
        """Respuesta de bdp/bdh → {ticker: valor} para `campo`, sea formato largo (≥1.0) o ancho (0.7)."""
        out = {}
        if res is None or len(res) == 0:
            return out
        d = res if isinstance(res, pd.DataFrame) else pd.DataFrame(res)
        cols = {str(c).strip().lower(): c for c in d.columns}
        if "ticker" in cols and "field" in cols and "value" in cols:                       # largo
            m = d[cols["field"]].astype(str).str.strip().str.upper().eq(campo.upper())
            for t, v in zip(d.loc[m, cols["ticker"]], d.loc[m, cols["value"]]):
                if (val := self._valor(v)) is not None and str(t).strip() != "__SECURITY_ERROR__":
                    out[str(t).strip()] = val
            return out
        if isinstance(d.columns, pd.MultiIndex):                                            # bdh 0.7: (ticker, campo)
            for (t, f) in d.columns:
                if str(f).strip().upper() == campo.upper() and len(d[(t, f)].dropna()):
                    if (val := self._valor(d[(t, f)].dropna().iloc[-1])) is not None:
                        out[str(t).strip()] = val
            return out
        col = next((c for c in d.columns if str(c).strip().upper() == campo.upper()), None)   # bdp 0.7: index=ticker
        if col is not None:
            for t, v in d[col].items():
                if (val := self._valor(v)) is not None:
                    out[str(t).strip()] = val
        return out

    @staticmethod
    def _serie(out: dict) -> pd.Series:
        return pd.Series(out, dtype=float if out and all(isinstance(v, float) for v in out.values()) else object)

    def bdp(self, tickers, campo, **overrides):
        out = {}
        for i in range(0, len(tickers), self.lote):
            res = self._llamar(self.blp.bdp, tickers=list(tickers[i:i + self.lote]), flds=campo, overrides=overrides)
            out.update(self._puntual(res, campo))
        return self._serie(out)

    def bds(self, ticker, campo, **overrides):
        try:
            d = self._llamar(self.blp.bds, ticker, campo, overrides=overrides)
        except Exception:
            return pd.DataFrame()
        if d is None or len(d) == 0:
            return pd.DataFrame()
        d = (d if isinstance(d, pd.DataFrame) else pd.DataFrame(d)).reset_index(drop=True)
        return d.drop(columns=[c for c in d.columns if str(c).strip().lower() in ("ticker", "field")])

    def historico(self, tickers, campo, fecha):
        out = {}
        if self.nueva_api:
            try:
                res = self._llamar(self.blp.bdh, tickers=list(tickers), flds=campo, start_date=fecha, end_date=fecha)
                out.update(self._puntual(res, campo))
            except Exception:
                pass
            return self._serie(out)
        for t in tickers:
            try:
                d = self.blp.bdh(tickers=t, flds=campo, start_date=fecha, end_date=fecha)
                if d is not None and not d.empty:
                    out[t] = float(d.iloc[-1, 0])
            except Exception:      # ticker inexistente o sin dato: se reporta aguas arriba como INDICE_SIN_NIVEL
                pass
        return self._serie(out)
