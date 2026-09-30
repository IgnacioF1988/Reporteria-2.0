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
        return pd.Series(pd.to_numeric(d["valor"], errors="coerce").values, index=d["ticker"].astype(str))

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
    """Terminal real vía xbbg. Import diferido: solo existe en Windows con terminal."""

    def __init__(self, lote: int = 100):
        from xbbg import blp
        self.blp, self.lote = blp, lote

    def bdp(self, tickers, campo, **overrides):
        out = {}
        for i in range(0, len(tickers), self.lote):
            res = self.blp.bdp(tickers=tickers[i:i + self.lote], flds=campo, **overrides)
            if res is None or res.empty:
                continue
            res.columns = [str(c).strip().upper() for c in res.columns]
            if campo.upper() in res.columns:
                for t, v in res[campo.upper()].items():
                    if pd.notna(v):
                        out[str(t).strip()] = float(v)
        return pd.Series(out, dtype=float)

    def bds(self, ticker, campo, **overrides):
        try:
            d = self.blp.bds(ticker, campo, **overrides)
        except Exception:
            return pd.DataFrame()
        return pd.DataFrame() if d is None else d.reset_index(drop=True)

    def historico(self, tickers, campo, fecha):
        out = {}
        for t in tickers:
            try:
                d = self.blp.bdh(tickers=t, flds=campo, start_date=fecha, end_date=fecha)
                if d is not None and not d.empty:
                    out[t] = float(d.iloc[-1, 0])
            except Exception:      # ticker inexistente o sin dato: se reporta aguas arriba como INDICE_SIN_NIVEL
                pass
        return pd.Series(out, dtype=float)
