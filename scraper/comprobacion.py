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
    todas = {}
    for nombre, params, prefijo in AMBITOS:
        p = 1
        while p <= 30:
            h = _get(op, f"{BASE}?{params}&pag_fecha=fechaExacta&pag_sort=desc&p={p}&numRegistrosMostrar=100")
            total = int((re.findall(r'data-load-total="(\d+)"', h) or ["0"])[0])
            items = _tarjetas(h)
            buenos = [it for it in items if norm(it["ubicacion"]).startswith(norm(prefijo))]
            if total > 5000 or (items and len(buenos) < 0.8 * len(items)):
                raise RuntimeError(f"El buscador oficial no ha aplicado los filtros ({nombre})")
            for it in buenos:
                it["ambito"] = nombre
                todas[it["ref"]] = it
            if not items or p * 100 >= total:
                break
            p += 1
    return list(todas.values())


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


def coincide(item, ficha):
    """¿La convocatoria del buscador oficial es esta ficha? Exige coincidir en la plaza o en el organismo concreto,
    y además en la fecha de fin de plazo o en varias palabras del nombre de la plaza."""
    tp = _tok(item["denominacion"])
    tf = _tok(" ".join([ficha["denominacion"], ficha["titulo_oficial"]]))
    comunes = tp & tf
    org = _org(item["organo"]) & _org(" ".join([ficha["organismo"], ficha["titulo_oficial"], ficha.get("departamento") or ""]))
    fp = _fecha(item["fin"])
    ff = date.fromisoformat(ficha["fin_plazo"]) if ficha.get("fin_plazo") else None
    misma_fecha = bool(fp and ff and abs((fp - ff).days) <= 3)
    junta = item.get("ambito", "").startswith("Junta") and ficha.get("tipo_organismo") in ("Junta de Andalucía", "Servicio Andaluz de Salud")
    if misma_fecha and (comunes or org or junta):
        return True
    if len(comunes) >= 2 and (org or len(comunes) >= max(2, round(len(tp) * 0.7))):
        return True
    return False


def explicado(item, descartes):
    """¿Es una convocatoria estatal que la lista deja fuera a propósito? (mismo ministerio y plazo compatible)."""
    fp = _fecha(item["fin"])
    org_item = _org(item["organo"]) | ({"sanidad"} if "sanidad" in norm(item["organo"]) else set())
    mejor = None
    for d in descartes:
        org_d = _org(d["departamento"] + " " + d["titulo"])
        if not (org_item & org_d):
            continue
        if fp:
            dias = (fp - date.fromisoformat(d["fecha"])).days
            if not (10 <= dias <= 70):
                continue
        comunes = _tok(item["denominacion"]) & _tok(d["titulo"])
        puntos = len(comunes) + (1 if org_item & org_d else 0)
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, d)
    return mejor[1] if mejor else None


def compara(items, fichas, descartes=()):
    omisiones, excluidas, coinciden = [], [], 0
    for it in items:
        f = next((f for f in fichas if coincide(it, f)), None)
        it["coincide_con"] = f["id"] if f else None
        if f:
            coinciden += 1
            continue
        d = explicado(it, descartes)
        if d:
            it["motivo"] = d["motivo"]
            it["boe"] = d.get("url")
            excluidas.append(it)
        else:
            omisiones.append(it)
    clave = lambda x: (_fecha(x["fin"]) or date.max, x["organo"], x["denominacion"])  # noqa: E731
    return coinciden, sorted(omisiones, key=clave), sorted(excluidas, key=clave)


def comprobacion(fichas, descartes=()):
    try:
        items = consulta_pag()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fecha": datetime.now(timezone.utc).isoformat(timespec="minutes"), "error": str(e)[:300]}, []
    coinciden, omisiones, excluidas = compara(items, fichas, descartes)
    return ({"ok": True, "fecha": datetime.now(timezone.utc).isoformat(timespec="minutes"), "total": len(items),
             "coinciden": coinciden, "posibles_omisiones": omisiones, "excluidas": excluidas}, items)
