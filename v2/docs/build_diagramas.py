"""Genera arquitectura_reporteria.html (fragmento) con 3 diagramas SVG en grilla. 1 unidad SVG = 1 pt impreso (A3 apaisado)."""
from pathlib import Path
import html

OUT = Path(__file__).resolve().parent.parent / "arquitectura_reporteria.html"
W = 1100
LH = 14          # interlineado del cuerpo (10.5 pt)
PAD = 12
FS = {"cuerpo": 10.5, "nota": 9.5, "titulo": 12, "grupo": 9.5}

_ids = [0]
def nid():
    _ids[0] += 1
    return f"b{_ids[0]}"

def esc(s):
    return html.escape(s, quote=True)

def _tspans(linea):
    """`code` → tspan mono; **negrita** → tspan bold."""
    out, i = [], 0
    import re
    for m in re.finditer(r"`([^`]+)`|\*\*([^*]+)\*\*", linea):
        if m.start() > i:
            out.append(esc(linea[i:m.start()]))
        if m.group(1) is not None:
            out.append(f'<tspan class="mono">{esc(m.group(1))}</tspan>')
        else:
            out.append(f'<tspan class="b">{esc(m.group(2))}</tspan>')
        i = m.end()
    if i < len(linea):
        out.append(esc(linea[i:]))
    return "".join(out)

def texto(x, y, s, cls="t", anchor="start", en=None, fs=None):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    d = f' data-in="{en}"' if en else ""
    f = f' font-size="{fs}"' if fs else ""
    return f'<text class="{cls}" x="{x}" y="{y}"{a}{d}{f}>{_tspans(s)}</text>'

def caja(x, y, w, titulo, lineas, tipo="", dashed=False, min_h=None, titulo_cls="tt"):
    """Caja con título y líneas; devuelve (svg, alto, id)."""
    bid = nid()
    h = PAD + (16 if titulo else 0) + len(lineas) * LH + PAD - 2
    if min_h:
        h = max(h, min_h)
    cls = "bx" + (f" bx-{tipo}" if tipo else "") + (" bx-dash" if dashed else "")
    parts = [f'<rect id="{bid}" class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="3"/>']
    cy = y + PAD + 9
    if titulo:
        parts.append(texto(x + PAD, cy, titulo, titulo_cls, en=bid))
        cy += 16
    for ln in lineas:
        cls_l = "t2" if ln.startswith("~") else "t"
        parts.append(texto(x + PAD, cy, ln.lstrip("~"), cls_l, en=bid))
        cy += LH
    return "\n".join(parts), h, bid

def grupo(x, y, w, h, etiqueta, tipo=""):
    gid = nid()
    cls = "grp" + (f" grp-{tipo}" if tipo else "")
    return (f'<rect id="{gid}" class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="4"/>\n'
            f'<rect class="lblbg" x="{x + 8}" y="{y - 7}" width="{len(etiqueta) * 6.4 + 10}" height="14"/>\n'
            + texto(x + 13, y + 4, etiqueta, "grp-l")), gid

def flecha(pts, etiqueta=None, lx=None, ly=None, tipo="", dashed=False, anchor="middle", ancho=None):
    cls = "fl" + (f" fl-{tipo}" if tipo else "") + (" fl-dash" if dashed else "")
    d = " ".join(f"{x},{y}" for x, y in pts)
    s = f'<polyline class="{cls}" points="{d}" marker-end="url(#m{tipo or "n"})"/>'
    if etiqueta:
        w = ancho or (len(etiqueta) * 5.3 + 10)
        bx = lx - w / 2 if anchor == "middle" else (lx - 4 if anchor == "start" else lx - w + 4)
        s += f'\n<rect class="lblbg" x="{bx:.1f}" y="{ly - 10}" width="{w:.1f}" height="14"/>'
        s += "\n" + texto(lx, ly, etiqueta, "fl-l" + (f" fl-l-{tipo}" if tipo else ""), anchor=anchor)
    return s

def chip(x, y, s, tipo="", h=24):
    w = len(s) * 6.1 + 14
    bid = nid()
    cls = "chip" + (f" bx-{tipo}" if tipo else "")
    return (f'<rect id="{bid}" class="{cls}" x="{x}" y="{y}" width="{w:.1f}" height="{h}" rx="12"/>\n'
            + texto(x + w / 2, y + h / 2 + 4, s, "t", anchor="middle", en=bid)), w

def leyenda(x, y):
    return (f'<rect class="bx bx-azul" x="{x}" y="{y}" width="18" height="12" rx="2"/>'
            + texto(x + 24, y + 10, "sale de la máquina / consume terminal Bloomberg", "t2")
            + f'<rect class="bx bx-ambar" x="{x}" y="{y + 18}" width="18" height="12" rx="2"/>'
            + texto(x + 24, y + 28, "queda sin métrica propia", "t2"))

DEFS = '''<defs>
<marker id="mn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk"/></marker>
<marker id="mazul" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk mk-azul"/></marker>
</defs>'''

def svg(h, cuerpo, aria):
    return (f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(aria)}" width="{W}" height="{h}">\n{DEFS}\n{cuerpo}\n</svg>')

# ───────────────────────── Diagrama 1 ─────────────────────────
def diagrama1():
    P = []
    # Columna A: insumos
    P.append(texto(0, 10, "INSUMOS · SOLO LECTURA · RUTAS EN .ENV", "grp-l"))
    s, h1, _ = caja(0, 18, 300, "CORPORATIVO / BIX", [
        "~Obligatorios",
        "`CUBO_{F}.xlsx`  posiciones del cierre",
        "`BD_INSTRUMENTOS` · `BD_FUNDS`",
        "`BD_BalanceSheet` · `BD_Monedas`",
        "~Opcionales",
        "`HOMOL_INSTRUMENTOS` · `HOMOL_FUNDS`",
        "`DEFAULTED` · `BD_FX_Exposure`"]); P.append(s)
    y = 18 + h1 + 14
    s, h2, _ = caja(0, y, 300, "MERCADO", [
        "`JPM_CEMBI_GBI`  yields de JPM",
        "`RA_TIR`  RiskAmerica",
        "`FACTURAS`  RPT de Facts",
        "`Carga_Indexes`  curvas reales",
        "`Carga_CurvasSoberanas`  curvas nominales",
        "`4- Carga de paridades`  tipos de cambio"]); P.append(s)
    y2 = y + h2 + 14
    s, h3, _ = caja(0, y2, 300, "MANUALES · los mantiene el operador", [
        "`REGLAS.xlsx`  9 hojas, todo por `ID_Fund`:",
        "~`fondos` `buckets` `clasificacion` `cajas`",
        "~`defaulteados` `overrides_valor`",
        "~`overrides_atributo` `alertas` `parametros`",
        "`EXCEPCIONES_*.xlsx`  flujos del PM"]); P.append(s)
    y3 = y2 + h3 + 14
    s, h4, _ = caja(0, y3, 300, "GENEVA", ["`bond_schedule.jsonl`  eventos de bonos"]); P.append(s)
    y4 = y3 + h4 + 14
    s, h5, _ = caja(0, y4, 300, "CIERRE ANTERIOR", [
        "`02_OUTPUTS/{F-1}/REPORTE_{F-1}.xlsx`",
        "~hedge heredado · alertas A05–A07"], dashed=True); P.append(s)
    fin_a = y4 + h5
    # bus
    mids = [18 + h1 / 2, y + h2 / 2, y2 + h3 / 2, y3 + h4 / 2, y4 + h5 / 2]
    for m in mids:
        P.append(f'<line class="fl" x1="300" y1="{m:.0f}" x2="322" y2="{m:.0f}"/>')
    P.append(f'<line class="fl" x1="322" y1="{mids[0]:.0f}" x2="322" y2="{mids[-1]:.0f}"/>')
    y_lee = 250
    P.append(flecha([(322, y_lee), (350, y_lee)], "lee", 336, y_lee - 7, ancho=22))

    # Columna B: CLI + motor + adaptadores
    s, hc, _ = caja(350, 0, 400, "CLI (typer)", [
        "`check` · `correr [--sin-bbg --sin-sql]` · `comparar`",
        "`migrar-manuales` · `importar-cache-legacy`"]); P.append(s)
    y_m = hc + 30
    P.append(flecha([(550, hc), (550, y_m)], "invoca", 582, hc + 18, anchor="start"))
    etapas = ["universo: CUBO + maestro, familias REGS/144A, hedge por fondo",
              "overrides de atributo (`REGLAS`)",
              "clasificación: BalSheetKey → Bucket → Tratamiento",
              "fuentes: cajas · facturas · EXCEPCIONES · JPM · RA",
              "   Bloomberg YAS · CSHF · JSONL (solo lo pendiente)",
              "sanidad y cascada → Yield, Duration",
              "índice · breakeven (indexados) · XCCY / drop (hedgeados)",
              "overrides de valor (pisan todo)",
              "alertas por reglas + estructurales",
              "agregados Activos / Pasivos / Patrimonio",
              "salida: Excel + JSON + log"]
    # etapa 4 ocupa dos líneas → numeración manual
    nums = [1, 2, 3, 4, None, 5, 6, 7, 8, 9, 10]
    g, gid = grupo(350, y_m, 400, 0, "MOTOR · `reporteria.pipeline.correr(fecha)`")   # alto se corrige abajo
    cy = y_m + 22
    lines = [texto(362, cy, "en memoria · sin intermedios · unidades decimales (0,05 = 5 %)", "t2", en=gid)]
    cy += 20
    for n, e in zip(nums, etapas):
        if n is not None:
            lines.append(f'<circle class="num" cx="372" cy="{cy - 4}" r="8"/>' + texto(372, cy - 0.5, str(n), "numt", anchor="middle", en=gid, fs=9))
        lines.append(texto(388, cy, e, "t", en=gid))
        cy += 22 if n != 4 else 16
    cy += 4
    lines.append(f'<line class="sep" x1="362" y1="{cy}" x2="738" y2="{cy}"/>')
    cy += 18
    lines.append(texto(362, cy, "módulos puros: `finanzas` `escala` `td` `curvas` `conversion` `cascada` `agregados`", "t2", en=gid)); cy += LH
    lines.append(texto(362, cy, "110 tests, con goldens del pipeline legacy de julio", "t2", en=gid)); cy += 12
    h_m = cy - y_m
    P.append(g.replace('height="0"', f'height="{h_m}"'))
    P.extend(lines)
    y_ad = y_m + h_m + 34
    s, h_ad, _ = caja(350, y_ad, 400, "ADAPTADORES · única frontera externa", [
        "`Bloomberg`  bdp / bds / bdh vía xbbg",
        "`FuenteFx`  beemining (SQL)",
        "`CacheBloomberg`  lee la caché del cierre y pide",
        "solo lo que falta; guarda cada respuesta"], tipo="azul"); P.append(s)
    P.append(flecha([(550, y_m + h_m), (550, y_ad)], "consulta solo lo pendiente · nunca defaulteados", 566, y_m + h_m + 21, anchor="start", ancho=236))
    fin_b = y_ad + h_ad

    # Columna C: salidas + externos
    P.append(texto(790, 10, "SALIDAS POR CIERRE", "grp-l"))
    s, ho, _ = caja(790, 18, 310, "`02_OUTPUTS/{F}/`", [
        "`REPORTE_{F}.xlsx`  13 hojas:",
        "~`resumen` `agregados` `alertas_resumen`",
        "~`alertas` `faltantes` `plantilla_overrides`",
        "~`cartera_final` `candidatos` `conversiones`",
        "~`curvas_drop` `td_detalle` `reglas_aplicadas`",
        "~`insumos`",
        "`resumen_corrida_{F}.json`"]); P.append(s)
    y_l = 18 + ho + 14
    s, hl, _ = caja(790, y_l, 310, "`03_LOGS/{F}/`", ["`corrida_{ts}.log`  conteos por etapa"]); P.append(s)
    y_c = y_l + hl + 14
    s, hcache, _ = caja(790, y_c, 310, "`04_CACHE/{F}/`", [
        "`bdp_*.csv` · `bdh_*.csv` · `bds_*/` · `fx_*.csv`",
        "~permite `correr --sin-bbg` idéntico"]); P.append(s)
    # externos alineados con adaptadores
    gy = y_ad
    gext, gext_id = grupo(790, gy, 310, 0, "FUERA DE ESTA MÁQUINA", tipo="azul")
    s, ht, _ = caja(802, gy + 14, 286, "Terminal Bloomberg", [
        "xbbg → `bbcomm`, puerto 8194",
        "~solo en la estación Bloomberg"], tipo="azul"); 
    s2, hb, _ = caja(802, gy + 14 + ht + 10, 286, "beemining", [
        "SQL vía ODBC",
        "~credenciales en `.env`"], tipo="azul")
    h_ext = 14 + ht + 10 + hb + 12
    P.append(gext.replace('height="0"', f'height="{h_ext}"')); P.append(s); P.append(s2)
    # flechas horizontales
    P.append(flecha([(750, 75), (790, 75)], "escribe", 770, 65))
    P.append(flecha([(750, y_l + hl / 2), (790, y_l + hl / 2)], "escribe", 770, y_l + hl / 2 - 10))
    ycm = y_c + hcache / 2
    P.append(flecha([(750, y_ad + 16), (770, y_ad + 16), (770, ycm), (790, ycm)]))
    P.append(texto(778, y_c + hcache + 30, "lee la caché y escribe", "fl-l"))
    P.append(texto(778, y_c + hcache + 42, "cada respuesta nueva", "fl-l"))
    P.append(flecha([(750, y_ad + h_ad - 20), (790, y_ad + h_ad - 20)], "consulta", 770, y_ad + h_ad - 30, tipo="azul"))
    P.append(leyenda(0, max(fin_a, fin_b) + 24))
    H = max(fin_a, fin_b, gy + h_ext) + 64
    return svg(H, "\n".join(P), "Insumos, motor, adaptadores y salidas del pipeline")

# ───────────────────────── Diagrama 2 ─────────────────────────
def diagrama2():
    P = []
    P.append(leyenda(800, 6))
    s, hp, _ = caja(0, 40, 200, "posición", ["`ID_Fund | PK2`", "~atributos del maestro y hedge"]); P.append(s)
    y_cl = 40 + hp + 28
    P.append(flecha([(100, 40 + hp), (100, y_cl)], "se clasifica", 110, 40 + hp + 18, anchor="start"))
    s, hcl, _ = caja(0, y_cl, 200, "clasificación", [
        "BalSheetKey → Bucket",
        "~(`BD_BalanceSheet`)",
        "regla por fondo en",
        "`REGLAS/clasificacion` pisa"]); P.append(s)
    # tabla de tratamientos
    TX, TY, TW = 240, 40, 530
    filas = [
        ("DEF / PROPDEF", "ambar", None, ["atajo: `DEFAULTED` corporativo o `REGLAS/defaulteados`", "~yield 0 · duration 0,5 · no consulta fuentes"]),
        ("CASCADA", "", [("EXCEPCIONES", ""), ("JPM", ""), ("RA", ""), ("Bloomberg YAS", "azul"), ("CSHF", "azul"), ("JSONL", "")],
         ["~CSHF = tabla de desarrollo de Bloomberg · JSONL = tabla propia desde Geneva"]),
        ("CAJA", "", [("EXCEPCIONES", ""), ("RA", ""), ("JPM", ""), ("índice + spread", "")], ["~índice de referencia y spread por posición en `REGLAS/cajas`"]),
        ("FACTURA", "", [("EXCEPCIONES", ""), ("RPT de Facts", "")], ["~yield = tasa mensual × 12 · duration = días al vencimiento / 365"]),
        ("CERO", "", None, ["yield 0 · duration 0 (equity, payables, MTM)"]),
        ("EXCLUIR", "", None, ["fuera de métricas y agregados → estado EXCLUIDO"]),
    ]
    RH = 54
    gt, gt_id = grupo(TX, TY, TW, 24 + RH * len(filas) + 8, "TRATAMIENTO DEL BUCKET → ORDEN DE FUENTES · gana la primera válida")
    P.append(gt)
    P.append(flecha([(200, y_cl + hcl / 2), (TX, y_cl + hcl / 2)], "define el", 220, y_cl + hcl / 2 - 12, ancho=54))
    mids = []
    for i, (nombre, tipo, chips, notas) in enumerate(filas):
        y0 = TY + 24 + RH * i
        mid = y0 + 20
        mids.append(mid)
        bid = nid()
        cls = "bx" + (f" bx-{tipo}" if tipo else "")
        P.append(f'<rect id="{bid}" class="{cls}" x="{TX + 10}" y="{y0 + 6}" width="104" height="28" rx="3"/>')
        P.append(texto(TX + 62, mid + 4, nombre, "tt", anchor="middle", en=bid))
        x = TX + 128
        if chips:
            for j, (c, ct) in enumerate(chips):
                if j:
                    P.append(texto(x + 4, mid + 4, "→", "t"))
                    x += 16
                s, w = chip(x, y0 + 8, c, ct); P.append(s); x += w
            for k, n in enumerate(notas):
                P.append(texto(TX + 128, y0 + 47 + k * 12, n.lstrip("~"), "t2", en=gt_id))
        else:
            for k, n in enumerate(notas):
                P.append(texto(TX + 128, mid + 4 + k * LH, n.lstrip("~"), "t2" if n.startswith("~") else "t", en=gt_id))
    # colector
    BX = TX + TW + 18
    for m in mids[:5]:
        P.append(f'<line class="fl" x1="{TX + TW}" y1="{m}" x2="{BX}" y2="{m}"/>')
    P.append(f'<line class="fl" x1="{BX}" y1="{mids[0]}" x2="{BX}" y2="{mids[4]}"/>')
    # columna 3: espina
    CX, CW = 820, 280
    y_s = 98
    s, hs, _ = caja(CX, y_s, CW, "sanidad y elección", [
        "yield entre −50 % y 100 %",
        "duration > 0 · tupla completa",
        "~todo candidato queda en la hoja `candidatos`"]); P.append(s)
    P.append(flecha([(BX, mids[1]), (CX, mids[1])]))
    P.append(texto(BX, mids[0] - 12, "gana la primera válida", "fl-l", anchor="middle"))
    y_cv = y_s + hs + 30
    P.append(flecha([(CX + CW / 2, y_s + hs), (CX + CW / 2, y_cv)], "convierte", CX + CW / 2 + 10, y_s + hs + 19, anchor="start"))
    s, hcv, _ = caja(CX, y_cv, CW, "conversión a la moneda del fondo", [
        "indexado (UF, UDI, UVR, IPCA, UI, BONCER)",
        "→ breakeven; duration reexpresada",
        "USD hedgeado → **XCCY de Bloomberg**;",
        "si no existe, drop propio con curvas",
        "~conserva `Yield_Papel` y `Duration_Papel`"]); P.append(s)
    y_ov = y_cv + hcv + 30
    P.append(flecha([(CX + CW / 2, y_cv + hcv), (CX + CW / 2, y_ov)], "pisa todo", CX + CW / 2 + 10, y_cv + hcv + 19, anchor="start"))
    s, hov, _ = caja(CX, y_ov, CW, "overrides de valor", ["`REGLAS/overrides_valor`, con vigencia"]); P.append(s)
    # ESTADO FINAL bajo la tabla; el flujo entra por arriba desde overrides
    GX, GW = 470, 320
    y_ef = TY + 24 + RH * len(filas) + 8 + 34
    gef, gef_id = grupo(GX, y_ef, GW, 0, "ESTADO FINAL")
    s1, h1, _ = caja(GX + 12, y_ef + 16, GW - 24, "RESUELTO", ["Yield · Duration · Fuente · Origen"])
    s2, h2, _ = caja(GX + 12, y_ef + 16 + h1 + 10, GW - 24, "FALTANTE", ["entra al agregado a yield 0", "va a `plantilla_overrides` con motivo"], tipo="ambar")
    h_ef = 16 + h1 + 10 + h2 + 12
    P.append(gef.replace('height="0"', f'height="{h_ef}"')); P.extend([s1, s2])
    xo = CX + CW / 2
    P.append(flecha([(xo, y_ov + hov), (xo, y_ef - 16), (GX + GW / 2, y_ef - 16), (GX + GW / 2, y_ef)], "estado final", GX + GW / 2 + 10, y_ef - 5, anchor="start"))
    # alertas y agregados apilados bajo la columna 1
    y_al = y_ef
    P.append(texto(0, y_al - 8, "DESPUÉS, SOBRE TODA LA CARTERA", "grp-l"))
    sa, ha, _ = caja(0, y_al, 440, "alertas", [
        "reglas de `REGLAS/alertas` (A01–A09): umbral por fondo,",
        "`param:`, comparación con el cierre anterior",
        "~más las estructurales del pipeline (faltantes, sanidad, hedge…)"])
    y_ag = y_al + ha + 14
    sg, hg, _ = caja(0, y_ag, 440, "agregados", [
        "AW = Σ y·MV / Σ MV     DW = Σ y·MV·D / Σ MV·D",
        "por fondo × ACTIVOS / PASIVOS / PATRIMONIO (A − P)",
        "abiertos por Bucket, Ficha_FI, FX_Exposure, país, moneda",
        "~verifica MV_PAT = MV_ACT − MV_PAS y pesos = 1"])
    P.extend([sa, sg])
    P.append(flecha([(GX, y_ef + 40), (440, y_ef + 40)]))
    P.append(texto(455, y_ef - 6, "alimenta", "fl-l", anchor="middle"))
    P.append(flecha([(GX, y_ef + 16 + h1 + 10 + 24), (455, y_ef + 16 + h1 + 10 + 24), (455, y_ag + 30), (440, y_ag + 30)]))
    H = max(y_ef + h_ef, y_ag + hg) + 16
    return svg(H, "\n".join(P), "Recorrido de una posición: clasificación, cascada de fuentes, sanidad, conversión, overrides y estado final")

# ───────────────────────── Diagrama 3 ─────────────────────────
def diagrama3():
    P = []
    P.append(leyenda(0, 36))
    pasos = [
        ("1  Insumos", "", ["`MERCADO` y `MANUALES`", "archivos del mes"]),
        ("2  `check`", "", ["valida insumos, `REGLAS`,", "`.env` y caché", "~exit 2 = no seguir"]),
        ("3  `correr`", "azul", ["con terminal Bloomberg", "única corrida con terminal", "~llena `04_CACHE`"]),
        ("4  Sin terminal", "", ["respaldo del reporte,", "`correr --sin-bbg`", "`--sin-sql` y `comparar`", "~= 0 diferencias"]),
        ("5  Revisar Excel", "", ["`alertas_resumen`", "`plantilla_overrides`", "agregados y conversiones"]),
        ("6  `REGLAS.xlsx`", "", ["overrides de valor y", "de atributo, con vigencia"]),
    ]
    BW, GAP, Y = 168, 18, 120
    xs = [i * (BW + GAP) for i in range(6)]
    hs = []
    for (t, tipo, ln), x in zip(pasos, xs):
        s, h, _ = caja(x, Y, BW, t, ln, tipo=tipo, min_h=94); P.append(s); hs.append(h)
    HB = max(hs)
    for i in range(5):
        P.append(flecha([(xs[i] + BW, Y + 38), (xs[i + 1], Y + 38)]))
    # caché sobre pasos 3-4
    s, hc, _ = caja(440, 30, 220, "`04_CACHE/{F}`", ["respuestas de Bloomberg y FX del cierre"], tipo="azul"); P.append(s)
    P.append(flecha([(xs[2] + 80, Y), (xs[2] + 80, 30 + hc)], "llena", xs[2] + 92, Y - 18, anchor="start", tipo="azul"))
    P.append(flecha([(xs[3] + 80, 30 + hc), (xs[3] + 80, Y)], "lee", xs[3] + 92, Y - 18, anchor="start"))
    yb = Y + HB
    # lazo interno
    P.append(flecha([(xs[5] + 60, yb), (xs[5] + 60, yb + 38), (xs[3] + 80, yb + 38), (xs[3] + 80, yb)], dashed=True))
    P.append(texto((xs[5] + 60 + xs[3] + 80) / 2, yb + 52, "vuelve a correr sin terminal hasta cerrar", "fl-l", anchor="middle"))
    # lazo externo
    P.append(flecha([(xs[5] + 120, yb), (xs[5] + 120, yb + 84), (xs[0] + 60, yb + 84), (xs[0] + 60, yb)], dashed=True))
    P.append(texto(560, yb + 98, "el reporte final es el cierre anterior del mes siguiente", "fl-l", anchor="middle"))
    s, hf, _ = caja(0, yb + 124, 1100, "", ["El operador nunca edita código: todo lo que cambia entre cierres va en `REGLAS.xlsx` (overrides con vigencia) y queda trazado en la hoja `reglas_aplicadas`."]); P.append(s)
    H = yb + 124 + hf + 16
    return svg(H, "\n".join(P), "Ciclo del operador en cada cierre")

CSS = '''<style>
/* Grilla: páginas apiladas de 1180px; cada figura es un SVG a escala 1 unidad = 1 pt en A3 apaisado. */
:root{--bg:#ffffff;--fg:#16181d;--fg2:#3d434d;--line:#2b2f36;--grp:#8a9099;--sep:#c4c8cf;
 --azul-f:#e8f0fb;--azul-s:#1f5fae;--ambar-f:#fdf1d8;--ambar-s:#a8680a;--num:#16181d;--numt:#ffffff;
 --sans:"IBM Plex Sans","Segoe UI",Arial,sans-serif;--mono:"IBM Plex Mono",Consolas,monospace}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#111317;--fg:#eceef1;--fg2:#c3c8d1;--line:#d0d4db;--grp:#7c8390;--sep:#3a3f48;
 --azul-f:#16263d;--azul-s:#6ea8ec;--ambar-f:#33270f;--ambar-s:#e2a948;--num:#eceef1;--numt:#111317;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#111317;--fg:#eceef1;--fg2:#c3c8d1;--line:#d0d4db;--grp:#7c8390;--sep:#3a3f48;
 --azul-f:#16263d;--azul-s:#6ea8ec;--ambar-f:#33270f;--ambar-s:#e2a948;--num:#eceef1;--numt:#111317;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px;line-height:1.5;margin:0}
main{max-width:1180px;margin:0 auto;padding-block:28px 48px;padding-inline:16px}
.page{display:grid;gap:10px;padding-block:24px 36px;border-bottom:1px solid var(--sep)}
.page:last-child{border-bottom:0}
h1{font-size:34px;font-weight:600;margin:0;line-height:1.2;text-wrap:balance}
h2{font-size:22px;font-weight:600;margin:0;line-height:1.25}
.sub{color:var(--fg2);font-size:15px;max-width:80ch;margin:0}
.eyebrow{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--fg2);font-weight:500;margin:0}
.fig{overflow-x:auto}
svg{display:block;width:100%;max-width:1100px;min-width:900px;height:auto;font-family:var(--sans)}
.mono,code{font-family:var(--mono)}
code{font-size:.92em}
.bx{fill:var(--bg);stroke:var(--line);stroke-width:1.2}
.bx-azul{fill:var(--azul-f);stroke:var(--azul-s)}
.bx-ambar{fill:var(--ambar-f);stroke:var(--ambar-s)}
.bx-dash{stroke-dasharray:4 3}
.chip{fill:var(--bg);stroke:var(--line);stroke-width:1.1}
.grp{fill:none;stroke:var(--grp);stroke-width:1}
.grp-azul{stroke:var(--azul-s);stroke-dasharray:5 3}
.lblbg{fill:var(--bg)}
.t{font-size:10.5px;fill:var(--fg)}
.t2{font-size:9.5px;fill:var(--fg2)}
.tt{font-size:12px;font-weight:600;fill:var(--fg)}
.b{font-weight:600}
.grp-l{font-size:9.5px;font-weight:600;letter-spacing:.08em;fill:var(--fg2)}
.fl{fill:none;stroke:var(--line);stroke-width:1.2}
.fl-azul{stroke:var(--azul-s)}
.fl-dash{stroke-dasharray:5 3}
.fl-l{font-size:9.5px;fill:var(--fg2);font-style:italic}
.fl-l-azul{fill:var(--azul-s)}
.mk{fill:var(--line)} .mk-azul{fill:var(--azul-s)}
.sep{stroke:var(--sep);stroke-width:1}
.num{fill:var(--num)} .numt{fill:var(--numt);font-weight:600}
.portada{display:grid;gap:18px;padding-block:40px 60px}
.portada ol{margin:0;padding-left:22px;display:grid;gap:8px;max-width:78ch}
.portada .meta{color:var(--fg2);font-size:14px}
table{border-collapse:collapse;width:100%;max-width:1000px;font-size:15px}
th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--sep);vertical-align:top}
th{color:var(--fg2);font-weight:500;font-size:12px;letter-spacing:.06em;text-transform:uppercase}
td:first-child{font-family:var(--mono);white-space:nowrap;font-size:14px}
@media print{
 :root{--bg:#ffffff;--fg:#16181d;--fg2:#3d434d;--line:#2b2f36;--grp:#8a9099;--sep:#c4c8cf;--azul-f:#e8f0fb;--azul-s:#1f5fae;--ambar-f:#fdf1d8;--ambar-s:#a8680a;--num:#16181d;--numt:#ffffff}
 @page{size:A3 landscape;margin:12mm 12mm 12mm 12mm}
 main{max-width:none;padding:0}
 .page{break-after:page;border:0;padding:0;gap:6pt}
 .page:last-child{break-after:auto}
 .fig{overflow:visible}
 svg{width:1100pt;max-width:none;min-width:0}
 h1{font-size:30pt} h2{font-size:22pt} .sub{font-size:12.5pt} body{font-size:12pt}
 .portada{padding-top:120pt}
}
</style>'''

def pagina(titulo, sub, cuerpo):
    return f'<section class="page">\n<h2>{titulo}</h2>\n<p class="sub">{sub}</p>\n<div class="fig">{cuerpo}</div>\n</section>'

portada = '''<section class="page portada">
<p class="eyebrow">Reportería 2.0 · paquete <code>reporteria</code> v2 · 2026-09-30</p>
<h1>Arquitectura del pipeline de Yield y Duration</h1>
<p class="sub">Tres figuras y una tabla para leer el sistema sin abrir el código: de dónde entran los datos y a dónde salen, qué le pasa a cada posición dentro de una corrida, y qué hace el operador cada cierre.</p>
<ol>
<li><strong>Arquitectura.</strong> Los insumos se leen donde están; el motor calcula todo en memoria y solo sale de la máquina por dos adaptadores, siempre a través de la caché del cierre.</li>
<li><strong>Flujo de una posición.</strong> El tratamiento del bucket fija el orden de fuentes; gana la primera válida y los overrides de valor pisan todo.</li>
<li><strong>Ciclo del operador.</strong> Una sola corrida con terminal por cierre; lo demás se repite sobre la caché hasta cerrar, sin editar código.</li>
<li><strong>Dónde vive cada decisión.</strong> Qué se cambia en <code>.env</code>, en <code>REGLAS.xlsx</code>, en <code>config.py</code>, en los maestros corporativos y en la caché.</li>
</ol>
<p class="meta">Colores: el azul marca lo que sale de la máquina o consume la terminal Bloomberg; el ámbar, lo que llega al agregado sin métrica propia. Todo lo demás es neutro.</p>
</section>'''

tabla = '''<section class="page">
<h2>4. Dónde vive cada decisión</h2>
<p class="sub">Cinco lugares, cada uno con un dueño distinto. El operador solo edita los dos primeros.</p>
<table>
<thead><tr><th>Lugar</th><th>Qué decide</th></tr></thead>
<tbody>
<tr><td>.env</td><td>Rutas a CUBO, BIX y raíz de trabajo; credenciales de beemining. Nunca se sube al repositorio.</td></tr>
<tr><td>REGLAS.xlsx</td><td>Política de hedge por fondo, tratamiento por bucket, reclasificaciones por fondo, cajas (índice y spread), defaulteados, overrides de atributo y de valor con vigencia, reglas de alerta y parámetros (umbrales, política XCCY).</td></tr>
<tr><td>config.py</td><td>Mapas técnicos estables: campo YAS por Yield_Type, índices y sus curvas, curvas de drops por moneda, alias de índices, sufijos de familia REGS/144A.</td></tr>
<tr><td>BD_* corporativos</td><td>Maestro de instrumentos, taxonomía de buckets, fondos, monedas, defaulted y homologaciones. Se mantienen fuera de este proyecto y se leen tal cual.</td></tr>
<tr><td>04_CACHE</td><td>Todo lo que respondió Bloomberg y beemining para ese cierre. Se puede borrar y regenerar con terminal; con la caché completa, una corrida sin terminal da el mismo reporte.</td></tr>
</tbody>
</table>
</section>'''

doc = "\n".join([
    "<title>Reportería 2.0 Arquitectura</title>",
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">',
    CSS,
    "<main>",
    portada,
    pagina("1. Arquitectura: insumos, motor y salidas",
           "Los insumos se leen donde están; el motor calcula todo en memoria y solo sale de la máquina por dos adaptadores, siempre a través de la caché del cierre.",
           diagrama1()),
    pagina("2. Flujo de una posición dentro de la corrida",
           "El tratamiento del bucket fija el orden de fuentes; gana la primera válida y los overrides de valor pisan todo.",
           diagrama2()),
    pagina("3. Ciclo del operador en cada cierre",
           "Una sola corrida con terminal por cierre; todo lo demás se repite sobre la caché hasta cerrar, y el operador nunca edita código.",
           diagrama3()),
    tabla,
    "</main>",
])
OUT.write_text(doc, encoding="utf-8")
print("html:", OUT, len(doc), "bytes")
