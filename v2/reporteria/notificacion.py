"""Aviso diario: tarjeta de Teams (webhook entrante, `urllib`, sin dependencias) e informe Markdown de la noche.

`teams(resumen, url, dir_logs)` siempre deja `teams_{ts}.json` en `dir_logs` (lo que se mandó o se habría mandado) y hace el POST
solo con URL y sin `dry_run`. Nunca levanta: un Teams caído no cambia el resultado de la nocturna, solo queda en el log.
La carga es un Adaptive Card dentro del sobre `{"type": "message", "attachments": [...]}`, que aceptan tanto el conector clásico
de Office 365 como los flujos de Power Automate ("When a Teams webhook request is received").
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CODIGOS = {0: "todo publicado", 1: "hay provisorios o fondos sin corrida", 2: "fallo de infraestructura", 3: "lock ajeno"}


def _n(d: dict, estado: str) -> int:
    return int((d or {}).get(estado, 0))


def _linea_fecha(f: dict) -> str:
    if f["estado"] == "CORRIDA":
        c = f.get("fondos") or {}
        partes = [f"{_n(c, 'PUBLICADA') + _n(c, 'REEXPRESADA')} publicados"]
        if _n(c, "PROVISORIO"):
            partes.append(f"{_n(c, 'PROVISORIO')} provisorios ({'; '.join(f['bloqueos'][:3])})" if f.get("bloqueos") else f"{_n(c, 'PROVISORIO')} provisorios")
        if _n(c, "SIN_CORRIDA"):
            partes.append(f"{_n(c, 'SIN_CORRIDA')} sin posiciones")
        return f"{f['fecha']} corrida {f['corrida']:03d}: " + ", ".join(partes)
    if f["estado"] == "SIN_CORRIDA":
        return f"{f['fecha']}: SIN CORRIDA — {f.get('detalle', '')}"
    if f["estado"] == "SIN_CUBO":
        return f"{f['fecha']}: sin CUBO (se esperaba el {f.get('esperada_el', '?')})"
    return f"{f['fecha']}: {f['estado']}"


def tarjeta(resumen: dict) -> dict:
    """Adaptive Card con el titular (código), una línea por fecha, re-evaluaciones, re-expresiones, lo marcado y el backlog."""
    codigo = int(resumen.get("codigo", 0))
    titulo = f"Reportería {resumen['hoy']}: {CODIGOS.get(codigo, codigo)}"
    hechos = []
    for f in resumen.get("fechas", []):
        if f["estado"] != "AUN_NO_ESPERADA":
            hechos.append({"title": f["fecha"], "value": _linea_fecha(f).split(": ", 1)[-1]})
    for r in resumen.get("reevaluadas", []):
        hechos.append({"title": f"{r['fecha']} re-evaluada", "value": f"corrida {r['corrida']:03d} por {', '.join(r['disparadores'])}: {r.get('fondos', {})}"})
    for e in resumen.get("reexpresadas", []):
        hechos.append({"title": "re-expresado", "value": e})
    for m in resumen.get("marcadas", []):
        hechos.append({"title": f"{m['cierre']} marcado", "value": f"{m['consecuencia']} ×{m['N']} (recalcular --desde)"})
    cuerpo = [{"type": "TextBlock", "text": titulo, "weight": "Bolder", "size": "Medium", "wrap": True}]
    if hechos:
        cuerpo.append({"type": "FactSet", "facts": hechos})
    backlog = resumen.get("backlog", [])
    if backlog:
        cuerpo.append({"type": "TextBlock", "wrap": True, "text": f"Sin publicar en la ventana: {sum(b['n'] for b in backlog)} fondo-día — " +
                       "; ".join(f"{b['fecha']}: {b['n']} {b['estado']} ({b['bloqueos']})" for b in backlog[:8]) + (" …" if len(backlog) > 8 else "")})
    for e in resumen.get("errores", []):
        cuerpo.append({"type": "TextBlock", "wrap": True, "color": "Attention", "text": e})
    if resumen.get("informe"):
        cuerpo.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small", "text": f"Informe: {resumen['informe']}"})
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": cuerpo,
            "msteams": {"width": "Full"}}
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def teams(resumen: dict, url: str | None, dir_logs: Path, dry_run: bool = False, log=None) -> dict:
    """Guarda `teams_{ts}.json` y, con URL y sin dry_run, hace el POST. Devuelve {enviado, archivo, motivo|estado}."""
    import urllib.request
    carga = tarjeta(resumen)
    dir_logs = Path(dir_logs)
    dir_logs.mkdir(parents=True, exist_ok=True)
    archivo = dir_logs / f"teams_{time.strftime('%Y%m%d_%H%M%S')}.json"
    archivo.write_text(json.dumps(carga, ensure_ascii=False, indent=2), encoding="utf-8")
    out = {"enviado": False, "archivo": str(archivo)}
    if dry_run:
        out["motivo"] = "dry-run"
    elif not url:
        out["motivo"] = "sin TEAMS_WEBHOOK_URL: el aviso queda solo en el JSON"
    else:
        req = urllib.request.Request(url, data=json.dumps(carga, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out.update(enviado=True, estado=int(getattr(resp, "status", 200)))
        except Exception as e:                      # noqa: BLE001 — un Teams caído no invalida la noche
            out["motivo"] = f"no se pudo enviar a Teams: {e}"
    if log:
        log.info("Teams: %s (%s)", "enviado" if out["enviado"] else out.get("motivo"), archivo.name)
    return out


def markdown(resumen: dict) -> str:
    """Informe `estado_diario_{hoy}.md`: lo mismo que la tarjeta, completo y legible en el share."""
    codigo = int(resumen.get("codigo", 0))
    out = [f"# Estado diario {resumen['hoy']} — código {codigo} ({CODIGOS.get(codigo, '')})", ""]
    out.append("## Fechas pendientes esta noche")
    out += [f"- {_linea_fecha(f)}" for f in resumen.get("fechas", [])] or ["- ninguna"]
    out += ["", "## Re-evaluadas (PROVISORIO con insumo o caché nuevos)"]
    out += [f"- {r['fecha']}: corrida {r['corrida']:03d} por {', '.join(r['disparadores'])} → {r.get('fondos', {})}" for r in resumen.get("reevaluadas", [])] or ["- ninguna"]
    out += ["", "## Re-expresadas por impacto"]
    out += [f"- {e}" for e in resumen.get("reexpresadas", [])] or ["- ninguna"]
    if resumen.get("marcadas"):
        out += ["", "## Marcado sin re-expresar (reporteria recalcular --desde F --motivo \"...\")"]
        out += [f"- {m['cierre']} v{int(m['version']):03d}: {m['consecuencia']} ×{m['N']}" for m in resumen["marcadas"]]
    out += ["", f"## Sin publicar en la ventana ({sum(b['n'] for b in resumen.get('backlog', []))} fondo-día)"]
    out += ["| Fecha | Estado | Fondos | Bloqueos |", "|---|---|---|---|"]
    out += [f"| {b['fecha']} | {b['estado']} | {b['n']} ({b['fondos']}) | {b['bloqueos']} |" for b in resumen.get("backlog", [])] or ["| – | – | – | – |"]
    if resumen.get("errores"):
        out += ["", "## Errores"] + [f"- {e}" for e in resumen["errores"]]
    return "\n".join(out) + "\n"
