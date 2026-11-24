"""Comparación diaria con el buscador oficial de empleo público del Punto de Acceso General (administracion.gob.es).

El buscador recoge las convocatorias del Estado, de las comunidades autónomas, de las universidades y de los
ayuntamientos de capital de provincia. Se consultan las que tienen el plazo abierto en tres ámbitos (nacional,
autonómico de Andalucía y local de la provincia de Sevilla) y se marcan las que no aparecen en nuestro listado.
"""
import html
import http.cookiejar
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from textos import norm

BASE = ("https://administracion.gob.es/content/pag-home/es/empleopublico/resultados-empleo/jcr:content/root/"
        "container/containerSpace/pag_front_formulario.list.html")
PORTADA = "https://administracion.gob.es/empleopublico/resultados-empleo"
DETALLE = "https://administracion.gob.es/empleopublico/resultados-empleo/detalle-empleo?selectorget={ref}&selectorServicio=bolsa_empleo"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
AMBITOS = [   # (nombre, filtros, comienzo esperado del campo "Ubicación")
    ("Estado (ámbito nacional)", "pag_tipoPlazo=1&pag_ambito=2", "NACIONAL"),
    ("Junta de Andalucía (ámbito autonómico)", "pag_tipoPlazo=1&pag_ambito=3&pag_comunidadAutonoma=01", "AUTONÓMICO - ANDALUCÍA"),
    ("Provincia de Sevilla (ámbito local)", "pag_tipoPlazo=1&pag_ambito=4&pag_comunidadAutonoma=01&pag_provincia=41", "LOCAL - ANDALUCÍA SEVILLA"),
]


def _abridor():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    return op


def _get(op, url, intentos=5):
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "text/html, */*; q=0.01", "X-Requested-With": "XMLHttpRequest", "Referer": PORTADA})
            with op.open(req, timeout=90) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(3 + 3 * i)
    raise RuntimeError(f"No se pudo consultar el buscador oficial: {ultimo}")


def _limpia(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def _tarjetas(h):
    out = []
    for card in re.split(r'<div class="col-12 dnt-item">', h)[1:]:
        a = re.search(r'selectorget=(\d+)[^"]*"[^>]*>\s*<span class="cmp-button__text">(.*?)</span>', card, re.S)
        if not a:
            continue

        def campo(nombre):
            m = re.search(nombre + r"\s*</span>\s*<span>(.*?)</span>", card, re.S)
            return _limpia(m.group(1)) if m else ""
        pl = re.search(r"Plazas:\s*</span>(.*?)</div>", card, re.S)
        out.append({"ref": a.group(1), "denominacion": _limpia(a.group(2)), "fin": campo("Fin de plazo:"),
                    "titulacion": campo("Titulación:"), "ubicacion": campo("Ubicación:"),
                    "organo": campo("Órgano convocante:"), "plazas": _limpia(pl.group(1)) if pl else "",
                    "url": DETALLE.format(ref=a.group(1))})
    return out


def consulta_pag():
    op = _abridor()
    _get(op, PORTADA)   # la búsqueda necesita la cookie de sesión de la portada
    todas, por_ambito = {}, {}
    for nombre, params, prefijo in AMBITOS:
        por_ambito[nombre] = 0
        p = 1
        while p <= 30:
            h = _get(op, f"{BASE}?{params}&pag_fecha=fechaExacta&pag_sort=desc&p={p}&numRegistrosMostrar=100")
            total = int((re.findall(r'data-load-total="(\d+)"', h) or ["0"])[0])
            items = _tarjetas(h)
            buenos = [it for it in items if norm(it["ubicacion"]).startswith(norm(prefijo))]
            if total > 5000 or (items and len(buenos) < 0.8 * len(items)):
                raise RuntimeError(f"El buscador oficial no ha aplicado los filtros ({nombre})")
            if p == 1:
                print(f"  buscador oficial · {nombre}: total {total}, tarjetas {len(items)}, "
                      f"ubicaciones {sorted({it['ubicacion'] for it in items})[:4]}", flush=True)
            for it in buenos:
                it["ambito"] = nombre
                todas[it["ref"]] = it
                por_ambito[nombre] += 1
            if not items or p * 100 >= total:
                break
            p += 1
    return list(todas.values()), por_ambito, op


STOP = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "a", "para", "por", "con", "personal", "plaza", "plazas",
        "grupo", "profesional", "cuerpo", "escala", "categoria", "especialidad", "nivel", "subgrupo", "acceso", "libre",
        "promocion", "interna", "general", "sistema", "turno", "laboral", "funcionario", "fijo", "tecnico", "tecnica",
        "tecnicoa", "superior", "medio", "auxiliar", "oficial", "titulado", "especialista"}
# Palabras de los organismos demasiado genéricas para identificar uno concreto
ORG_GENERICO = {"ministerio", "consejeria", "universidad", "ayuntamiento", "agencia", "instituto", "direccion", "general",
                "secretaria", "subsecretaria", "andalucia", "junta", "estado", "servicio", "servicios", "nacional", "estatal",
                "presidencia", "gobierno", "administracion", "publica", "publico", "desarrollo", "sevilla", "provincial",
                "diputacion", "organismo", "autonomo", "recursos", "humanos", "empleo", "funcion"}


def _tok(s):
    return {w for w in re.findall(r"[a-z0-9]+", norm(s)) if len(w) > 2 and w not in STOP and not w.isdigit()}


def _org(s):
    return {w for w in re.findall(r"[a-z0-9]+", norm(s)) if len(w) > 3 and w not in ORG_GENERICO and w not in STOP}


def _fecha(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def detalle(op, ref):
    """Datos de la ficha del buscador: dónde se publicó (BOE, BOJA, BOP o solo la web del organismo)."""
    h = _get(op, DETALLE.format(ref=ref))
    i = h.find('class="pag-elastic-response-detail"')
    seg = h[i: h.find("<footer", i)] if i >= 0 else ""
    campos = {_limpia(a): _limpia(b) for a, b in re.findall(
        r'pag-detail__subtitle"[^>]*>(.*?)</[^>]+>\s*<[^>]*pag-detail__text"[^>]*>(.*?)</(?:p|div|span)>', seg, re.S)}
    enlaces = [html.unescape(u) for u in re.findall(r'href="([^"]+)"', seg) if "gestionsuscripciones" not in u]
    todo = " ".join(enlaces)
    return {
        "campos": campos,
        "boe": sorted(set(re.findall(r"BOE-[AB]-\d{4}-\d+", todo))),
        "boja": sorted({(a, str(int(b))) for a, b in re.findall(r"boja/(\d{4})/(\d{1,3})/", todo)}),
        "bop": any("bopsevilla" in u or "dipusevilla" in u for u in enlaces),
        "web": next((u for u in enlaces if u.startswith("http") and not re.search(r"boe\.es|juntadeandalucia\.es/(?:e)?boja|sede\.|registro", u)), None),
    }


COMPATIBLE = {   # qué tipos de organismo de la lista corresponden a cada ámbito del buscador oficial
    "Estado (ámbito nacional)": {"Administración del Estado"},
    "Junta de Andalucía (ámbito autonómico)": {"Junta de Andalucía", "Servicio Andaluz de Salud", "Universidad", "Otros"},
    "Provincia de Sevilla (ámbito local)": {"Ayuntamiento", "Diputación de Sevilla", "Otros entes locales", "Universidad", "Otros"},
}


def puntua(item, ficha):
    """0 si la convocatoria del buscador oficial no puede ser esta ficha; si puede, cuanto más alto, más parecida."""
    tp = _tok(item["denominacion"])
    tf = _tok(" ".join([ficha["denominacion"], ficha["titulo_oficial"]]))
    comunes = tp & tf
    frac = len(comunes) / len(tp) if tp else 0
    org = bool(_org(item["organo"]) & _org(" ".join([ficha["organismo"], ficha.get("departamento") or ""])))
    compatible = ficha.get("tipo_organismo") in COMPATIBLE.get(item.get("ambito"), set())
    if not compatible and not org:
        return 0
    fp = _fecha(item["fin"])
    ff = date.fromisoformat(ficha["fin_plazo"]) if ficha.get("fin_plazo") else None
    dias = abs((fp - ff).days) if fp and ff else None
    misma = dias is not None and dias <= 3
    cerca = dias is not None and dias <= 10
    sin_fecha = ff is None
    if (misma and (frac >= 0.34 or org)) \
            or (len(comunes) >= 2 and frac >= 0.5 and (org or cerca or sin_fecha)) \
            or (comunes and frac >= 0.5 and org and (cerca or sin_fecha)):
        td = _tok(ficha["denominacion"])
        cubre = len(comunes & td) / len(td) if td else 0   # cuánto del nombre de la ficha está en la del buscador
        return 3 * frac + 1.5 * cubre + 2 * org + 2 * misma + cerca + (0.1 * (10 - dias) if cerca else 0)
    if dias is not None and dias <= 1 and compatible and item.get("ambito", "").startswith("Junta"):
        return 0.5   # la Junta agrupa muchas categorías en una sola resolución
    return 0


def mejor(item, fichas):
    puntos = [(puntua(item, f), f) for f in fichas]
    puntos = [x for x in puntos if x[0] > 0]
    return max(puntos, key=lambda x: x[0])[1] if puntos else None


def por_boletin(item, fichas, descartes):
    """Busca por la referencia del boletín que da la ficha del buscador oficial."""
    d = item.get("detalle") or {}
    for b in d.get("boe", []):
        f = next((f for f in fichas if b == f["id"] or b in (f.get("url_boe") or "") or b in (f.get("url_bases") or "")), None)
        if f:
            return f, None
        if b in descartes:
            return None, descartes[b]
    for anio, num in d.get("boja", []):
        cands = [f for f in fichas if f["id"].startswith(f"BOJA-{anio}-{num}-")]
        if len(cands) == 1:
            return cands[0], None
        f = mejor(item, cands)
        if f:
            return f, None
    return None, None


def explicado(item, descartes):
    """¿Es una convocatoria estatal que la lista deja fuera a propósito? (mismo ministerio, nombre parecido y plazo compatible)."""
    fp = _fecha(item["fin"])
    org_item = _org(item["organo"]) | ({"sanidad"} if "sanidad" in norm(item["organo"]) else set())
    tp = _tok(item["denominacion"])
    elegido = None
    for d in descartes.values():
        org_d = _org(d["departamento"] + " " + d["titulo"])
        if not (org_item & org_d):
            continue
        if fp:
            dias = (fp - date.fromisoformat(d["fecha"])).days
            if not (10 <= dias <= 70):
                continue
        comunes = tp & _tok(d["titulo"])
        if not comunes and "ingesa" not in norm(d["motivo"]):
            continue
        puntos = len(comunes)
        if elegido is None or puntos > elegido[0]:
            elegido = (puntos, d)
    return elegido[1] if elegido else None


def donde(item):
    d = item.get("detalle")
    if not d:
        return None
    if d["boe"]:
        return {"texto": d["boe"][0], "url": f"https://www.boe.es/buscar/doc.php?id={d['boe'][0]}"}
    if d["boja"]:
        return {"texto": f"BOJA nº {d['boja'][0][1]} de {d['boja'][0][0]}", "url": None}
    if d["bop"]:
        return {"texto": "BOP de Sevilla", "url": None}
    return {"texto": "solo en la web del organismo (no en BOE, BOJA ni BOP)", "url": d.get("web")}


def compara(items, fichas, descartes=None):
    descartes = descartes or {}
    omisiones, excluidas, coinciden = [], [], 0
    for it in items:
        f, desc = por_boletin(it, fichas, descartes)
        if not f and not desc:
            f = mejor(it, fichas)
        if not f and not desc:
            desc = explicado(it, descartes)
        it["coincide_con"] = f["id"] if f else None
        it["publicada"] = donde(it)
        if f:
            coinciden += 1
        elif desc:
            it["motivo"] = desc["motivo"]
            it["boe"] = desc.get("url") or f"https://www.boe.es/buscar/doc.php?id={desc['id']}"
            excluidas.append(it)
        else:
            omisiones.append(it)
    clave = lambda x: (_fecha(x["fin"]) or date.max, x["organo"], x["denominacion"])  # noqa: E731
    return coinciden, sorted(omisiones, key=clave), sorted(excluidas, key=clave)


def comprobacion(fichas, descartes=None, cache_detalles=None):
    """Devuelve (resumen para la web, convocatorias del buscador oficial con su emparejamiento, caché de detalles)."""
    cache = dict(cache_detalles or {})
    ahora = datetime.now(timezone.utc).isoformat(timespec="minutes")
    try:
        items, por_ambito, op = consulta_pag()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fecha": ahora, "error": str(e)[:300]}, [], cache
    nuevos = 0
    for it in items:
        if it["ref"] not in cache:
            try:
                cache[it["ref"]] = detalle(op, it["ref"])
                nuevos += 1
            except Exception as e:  # noqa: BLE001
                print("  detalle no disponible", it["ref"], e, flush=True)
                continue
        it["detalle"] = cache[it["ref"]]
    print(f"  fichas del buscador oficial leídas hoy: {nuevos}", flush=True)
    cache = {k: v for k, v in cache.items() if any(k == it["ref"] for it in items)}
    coinciden, omisiones, excluidas = compara(items, fichas, descartes)
    ligeras = lambda xs: [{k: v for k, v in x.items() if k != "detalle"} for x in xs]  # noqa: E731
    return ({"ok": True, "fecha": ahora, "total": len(items), "por_ambito": por_ambito, "coinciden": coinciden,
             "posibles_omisiones": ligeras(omisiones), "excluidas": ligeras(excluidas)}, items, cache)
