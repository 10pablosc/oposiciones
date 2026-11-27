"""Actualiza el listado de convocatorias con posibles plazas en la provincia de Sevilla.

Fuentes: BOE (sección II.B), BOJA (sección 2.2) y BOP de Sevilla.
Uso:  python scraper/actualizar.py [--hoy AAAA-MM-DD] [--solo-salida] [--fuentes boe,bop,boja]
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from textos import (EXCLUIR, es_excluido, quita_firmas, grupo_laboral, titulacion_valida, denominacion_de_texto, titulo_sin_preambulo, RE_CONVOCA_BOJA, simplifica_organismo, organismo_estado, titulo_bonito, RE_CONVOCA, RE_CONVOCA_ESTRICTO, denominacion_de_titulo, en_el_extranjero, es_provision,  # noqa: E402
                    extrae_grupo, extrae_num_plazas, extrae_personal, extrae_plazo,
                    extrae_requisitos, extrae_sistema, extrae_titulacion,
                    limpia, lugares_no_sevilla, menciona_sevilla, norm,
                    suma_plazo, tipo_proceso)
from lugares import SEDE_SOLO_MADRID, extrae_lugares, resume  # noqa: E402
from textos import lugares_bonitos  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RAIZ, "data")
DOCS = os.path.join(RAIZ, "docs")
F_REG = os.path.join(DATA, "registros.json")
F_EST = os.path.join(DATA, "estado.json")
F_OUT = os.path.join(DOCS, "convocatorias.json")

# Ventanas de búsqueda hacia atrás (días)
VENTANA_LOCAL = int(os.environ.get("VENTANA_LOCAL", "365"))      # BOP y extractos locales del BOE
VENTANA_ESTADO = int(os.environ.get("VENTANA_ESTADO", "120"))     # convocatorias estatales del BOE
VENTANA_BOJA = int(os.environ.get("VENTANA_BOJA", "150"))       # BOJA
VENTANA_BOLSA = int(os.environ.get("VENTANA_BOLSA", "120"))      # bolsas del BOP (su plazo cuenta desde el BOP)
REVISION = 4             # días recientes que se revisan en cada ejecución

INICIO = time.time()
MAX_MINUTOS = float(os.environ.get("MAX_MINUTOS", "95"))


def sin_tiempo():
    """Para no pasarse del tiempo máximo de la tarea: lo pendiente se hace en la siguiente ejecución."""
    return (time.time() - INICIO) / 60 > MAX_MINUTOS


SCAN_VERSION = 5   # súbelo para volver a recorrer los días del BOP y del BOJA (p. ej. tras corregir filtros)
DIAS_MARGEN_ESTADO = 15   # días que se guarda una convocatoria del Estado ya cerrada según el cálculo
FESTIVOS_ANDALUCES_EN_ESTADO = False  # False: las convocatorias del Estado se calculan solo con festivos nacionales
BOE_SCAN_VERSION = 2   # súbelo para volver a recorrer los días recientes del BOE (recupera lo que se perdió por un fallo)
VERSION = 10   # súbelo cuando cambie la forma de extraer datos: los registros vigentes se vuelven a leer

UA = "Mozilla/5.0 (compatible; oposiciones-sevilla/1.0; +https://github.com/10pablosc/oposiciones)"
BOP = "https://bopsevilla.dipusevilla.es"
L = lambda x: x if isinstance(x, list) else ([] if x is None else [x])  # noqa: E731


def log(*a):
    print(" ".join(str(x) for x in a), flush=True)


# ---------------------------------------------------------------------------
# Red y conversión de documentos
# ---------------------------------------------------------------------------

CACHE = os.environ.get("CACHE_DIR")


def descarga(url, binario=False, intentos=5, aceptar=None):
    if CACHE:
        import hashlib
        ruta = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest())
        if os.path.exists(ruta):
            with open(ruta, "rb") as f:
                datos = f.read()
            if datos == b"__404__":
                return None
            return datos if binario else datos.decode("utf-8", errors="replace")
        datos = _descarga(url, True, intentos, aceptar)
        os.makedirs(CACHE, exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(b"__404__" if datos is None else datos)
        if datos is None:
            return None
        return datos if binario else datos.decode("utf-8", errors="replace")
    return _descarga(url, binario, intentos, aceptar)


def _descarga(url, binario=False, intentos=5, aceptar=None):
    ultimo = None
    for i in range(intentos):
        try:
            cab = {"User-Agent": UA}
            if aceptar:
                cab["Accept"] = aceptar
            req = urllib.request.Request(url, headers=cab)
            with urllib.request.urlopen(req, timeout=60) as r:
                datos = r.read()
            time.sleep(0.15)
            return datos if binario else datos.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            ultimo = e
        except Exception as e:  # noqa: BLE001
            ultimo = e
        time.sleep(2 + 2 * i)
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")


def html_a_texto(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    lineas = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in t.split("\n")]
    return "\n".join(l for l in lineas if l)


RUIDO_SEG = re.compile(r"(?i)^(p[áa]gina \d+|cve:|c[óo]d\. validaci[óo]n|verificaci[óo]n:|documento firmado|hash:|fecha firma|"
                       r"n[ºo°] \d+ ?- ?(lunes|martes|mi[ée]rcoles|jueves|viernes)|\d{1,3}$|https?://|el secretario|"
                       r"[A-ZÁÉÍÓÚÑ ]+\(\d+ de \d+\)$|firmado)")


def letras_utiles(texto):
    return len(re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", texto or ""))


OCR_MAX_PAGINAS = int(os.environ.get("OCR_MAX_PAGINAS", "30"))


def pdf_ocr(datos):
    """Lee un PDF escaneado con Tesseract (español). Devuelve '' si no está disponible."""
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        ruta = os.path.join(tmp, "doc.pdf")
        with open(ruta, "wb") as f:
            f.write(datos)
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-l", str(OCR_MAX_PAGINAS), ruta, os.path.join(tmp, "p")],
                       capture_output=True, timeout=900)
        paginas = sorted(x for x in os.listdir(tmp) if x.endswith(".png"))
        partes = []
        for pag in paginas:
            r = subprocess.run(["tesseract", os.path.join(tmp, pag), "-", "-l", "spa"], capture_output=True, timeout=300)
            partes.append(r.stdout.decode("utf-8", errors="replace"))
    texto = "\n".join(partes)
    # confusiones típicas del OCR en los grupos: "Subgrupo Cl" -> "Subgrupo C1"
    texto = re.sub(r"(?i)\b(subgrupo|grupo)(\s*:?\s*)([ac])\s?[lI|]\b", lambda m: m.group(1) + m.group(2) + m.group(3).upper() + "1", texto)
    lineas = [l for l in texto.split("\n") if not RUIDO_SEG.search(l.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas))


def pdf_a_texto(datos, modo="-layout"):
    """Texto del PDF respetando el orden visual (-layout) y sin las columnas laterales de firma.
    Con modo="-raw" sale en el orden en que se escribió (mejor para leer las celdas de las tablas)."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(datos)
        ruta = f.name
    try:
        r = subprocess.run(["pdftotext", modo, ruta, "-"], capture_output=True, timeout=180)
        bruto = r.stdout.decode("utf-8", errors="replace")
    finally:
        os.unlink(ruta)
    lineas = []
    for l in bruto.split("\n"):
        segs = [x.strip() for x in re.split(r"\s{6,}", l.strip()) if x.strip()]
        segs = [x for x in segs if not RUIDO_SEG.search(x)]
        lineas.append("   ".join(segs) if segs else "")
    texto = "\n".join(lineas)
    return re.sub(r"\n{3,}", "\n\n", texto)


# ---------------------------------------------------------------------------
# Estado persistente
# ---------------------------------------------------------------------------

def carga(ruta, defecto):
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    return defecto


def guarda(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, ruta)


def pendientes(procesados, desde, hasta, recientes_primero=False):
    """Días de la ventana que hay que visitar (los no hechos y los más recientes)."""
    hechos = set(procesados)
    todos = []
    d = desde
    while d <= hasta:
        if d.isoformat() not in hechos or d > hasta - timedelta(days=REVISION):
            todos.append(d)
        d += timedelta(days=1)
    return list(reversed(todos)) if recientes_primero else todos


def poda_vistos(vistos, hoy):
    """Los identificadores ya revisados solo hacen falta para los días que se repasan."""
    limite = (hoy - timedelta(days=REVISION + 6)).isoformat()
    return {k: v for k, v in vistos.items() if v >= limite}


# ---------------------------------------------------------------------------
# BOE
# ---------------------------------------------------------------------------

CCAA_NO_ANDALUCIA = re.compile(r"^(comunidad|comunitat|generalitat|ciudad de (ceuta|melilla)|illes balears|region de murcia|principado)")
UNIV_SEVILLA = re.compile(r"universidad de sevilla|pablo de olavide|universidad internacional de andalucia")


def boe_sumario(d):
    txt = descarga(f"https://www.boe.es/datosabiertos/api/boe/sumario/{d:%Y%m%d}", aceptar="application/json")
    if txt is None or not txt.lstrip().startswith("{"):
        return None
    j = json.loads(txt)
    items = []
    for diario in L(j["data"]["sumario"].get("diario")):
        for sec in L(diario.get("seccion")):
            if sec.get("codigo") != "2B":
                continue
            for dep in L(sec.get("departamento")):
                grupos = [(ep.get("nombre", ""), L(ep.get("item"))) for ep in L(dep.get("epigrafe"))]
                grupos.append(("", L(dep.get("item"))))
                for ep, its in grupos:
                    for it in its:
                        items.append({"id": it["identificador"], "titulo": limpia(it["titulo"]),
                                      "departamento": dep.get("nombre", ""), "epigrafe": ep,
                                      "url": it.get("url_html") or f"https://www.boe.es/diario_boe/txt.php?id={it['identificador']}",
                                      "fecha": d.isoformat()})
    return items


def boe_texto(ident):
    x = descarga(f"https://www.boe.es/diario_boe/xml.php?id={ident}")
    if not x:
        return "", []
    notas = [limpia(html.unescape(n)) for n in re.findall(r"<nota[^>]*>(.*?)</nota>", x, re.S)]
    i = x.find("<texto")
    return (html_a_texto(x[i:]) if i >= 0 else ""), notas


MESES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                                         "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}
RE_BOP_REF = re.compile(r"(?:provincia\s+de\s+sevilla|bop[^,]{0,20}sevilla)[»\"']?,?\s*(?:n[uú]m(?:ero|\.)?|n\.?\s?º)\s*(\d{1,3}),?\s*de\s*(?:fecha\s*)?(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de\s+)?(\d{4})", re.I)
RE_BOP_REF2 = re.compile(r"(?:provincia\s+de\s+sevilla|bop[^,]{0,20}sevilla)[»\"']?,?\s*(?:n[uú]m(?:ero|\.)?|n\.?\s?º)\s*(\d{1,3}),?\s*de\s*(?:fecha\s*)?(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", re.I)


def refs_bop(texto):
    out = []
    t = texto.replace("\n", " ")
    for m in RE_BOP_REF.finditer(t):
        mes = MESES.get(norm(m.group(3)))
        if mes:
            try:
                out.append({"num": int(m.group(1)), "fecha": date(int(m.group(4)), mes, int(m.group(2))).isoformat()})
            except ValueError:
                pass
    for m in RE_BOP_REF2.finditer(t):
        try:
            out.append({"num": int(m.group(1)), "fecha": date(int(m.group(4)), int(m.group(3)), int(m.group(2))).isoformat()})
        except ValueError:
            pass
    unicos = {(r["num"], r["fecha"]): r for r in out}
    return list(unicos.values())


DESCARTES = {}   # convocatorias estatales que se dejan fuera a propósito (para explicar la comparación con el buscador oficial)


def descarta(it, motivo, tipo="otro"):
    DESCARTES[it["id"]] = {"id": it["id"], "titulo": it["titulo"], "departamento": it.get("departamento", ""),
                           "fecha": it["fecha"], "motivo": motivo, "url": it.get("url"), "tipo": tipo,
                           "visto": date.today().isoformat()}
    return None


def clasifica_boe(it):
    dep = norm(it["departamento"])
    tit = norm(it["titulo"])
    estatal = dep not in ("administracion local", "universidades") and "andalucia" not in dep and not CCAA_NO_ANDALUCIA.search(dep)
    if es_excluido(tit):
        return None
    if es_provision(it["titulo"] + " " + it["epigrafe"]):
        return descarta(it, "Provisión de puestos (solo para quien ya es funcionario)", "provision") if estatal and RE_CONVOCA_ESTRICTO.search(tit) else None
    if en_el_extranjero(it["titulo"]):
        return descarta(it, "Plazas en el extranjero", "extranjero") if estatal else None
    if dep == "administracion local":
        if re.search(r"\(sevilla\)|ayuntamiento de sevilla\b|diputacion provincial de sevilla", tit):
            return "local"
        return None
    if dep == "universidades":
        if UNIV_SEVILLA.search(tit) and RE_CONVOCA_ESTRICTO.search(tit):
            return "universidad"
        return None
    if "andalucia" in dep or CCAA_NO_ANDALUCIA.search(dep):
        return None  # la Junta se toma del BOJA; otras comunidades no interesan
    if re.search(r"instituto nacional de gestion sanitaria|\bingesa\b", tit):
        return descarta(it, "Plazas en Ceuta y Melilla (INGESA)", "ingesa") if RE_CONVOCA_ESTRICTO.search(tit) else None
    if RE_CONVOCA_ESTRICTO.search(tit):
        if lugares_no_sevilla(it["titulo"]) and not menciona_sevilla(it["titulo"]):
            return descarta(it, "Plazas en " + ", ".join(lugares_no_sevilla(it["titulo"])).title(), "lugar")
        return "estado"
    return None


def procesa_boe(estado, registros, hoy):
    est = estado.setdefault("boe", {"procesados": []})
    vistos = estado.setdefault("boe_vistos", {})
    desde = hoy - timedelta(days=VENTANA_LOCAL)
    nuevos = errores = 0
    for d in pendientes(est["procesados"], desde, hoy):
        if sin_tiempo():
            log("  BOE: tiempo agotado, se continúa en la próxima ejecución")
            break
        if d.day == 1:
            log(f"  BOE: revisando {d:%m/%Y}…")
        if d.weekday() == 6:
            est["procesados"].append(d.isoformat())
            continue
        try:
            items = boe_sumario(d)
        except Exception as e:  # noqa: BLE001
            log("  BOE sin sumario", d, e)
            errores += 1
            continue
        if items is None:
            if d < hoy - timedelta(days=REVISION):
                est["procesados"].append(d.isoformat())
            continue
        completo = True
        for it in items:
            if it["id"] in vistos or it["id"] in registros:
                continue
            cat = clasifica_boe(it)
            if not cat or (cat == "estado" and d < hoy - timedelta(days=VENTANA_ESTADO)):
                continue
            try:
                r = registro_boe(it, cat)
            except Exception as e:  # noqa: BLE001
                log("  BOE error", it["id"], e)
                completo = False
                continue
            vistos[it["id"]] = d.isoformat()
            if r:
                registros[r["id"]] = r
                nuevos += 1
        if completo:
            est["procesados"].append(d.isoformat())
    est["procesados"] = [x for x in sorted(set(est["procesados"])) if x >= desde.isoformat()]
    estado["boe_vistos"] = poda_vistos(vistos, hoy)
    log(f"BOE: {nuevos} registros nuevos, {errores} días con error")
    if errores > 5:
        raise RuntimeError(f"{errores} días del BOE no se pudieron descargar")
    return nuevos


def registro_boe(it, cat):
    texto, notas = boe_texto(it["id"])
    completo = it["titulo"] + "\n" + "\n".join(notas) + "\n" + texto
    if cat == "estado":
        lugares = lugares_no_sevilla(texto[:20000])
        if not menciona_sevilla(texto) and (len(lugares) >= 2 or {"ceuta", "melilla"} & set(lugares)):
            return descarta(it, "Plazas en otras provincias (" + ", ".join(lugares[:4]).title() + "), ninguna en Sevilla", "lugar")
    sitio = (None, None, None)
    if cat in ("estado", "universidad"):
        sitio = resume(extrae_lugares(texto, it["titulo"]))
        if sitio[0] == "fuera":
            return descarta(it, f"Plazas en {sitio[2]}, ninguna en la provincia de Sevilla", "lugar")
        if cat == "estado" and sitio[0] != "sevilla" and SEDE_SOLO_MADRID.search(norm(it["titulo"])):
            return descarta(it, "El organismo solo tiene sede en Madrid", "madrid")
    org = organismo_de_titulo(it["titulo"])
    if cat == "estado":
        org = organismo_estado(org, it["departamento"])
    org = org or titulo_bonito(it["departamento"])
    denoms, suma = [], 0
    for n in notas:
        m = re.match(r"(?i)(?:turno libre|promoci[óo]n interna|acceso libre|discapacidad|estabilizaci[óo]n|concurso|oposici[óo]n)[^:]{0,60}:\s*(.+)", n)
        if m:
            for trozo in re.split(r"\.\s+|;\s*", m.group(1).strip(" .")):
                k = re.search(r"\s*\b(\d+)\s+plazas?\b", trozo)
                if k:
                    suma += int(k.group(1))
                nombre = re.sub(r"\s*\b\d+\s+plazas?\b", "", trozo).strip(" .,")
                if nombre:
                    denoms.append(nombre)
    denom = "; ".join(dict.fromkeys(denoms)) if denoms else None
    if not denom:
        primeras = [l for l in texto.split("\n") if re.match(r"(?i)^[-–•]?\s*(una?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|\d+)\s+plazas?\b", l)]
        denom = "; ".join(filter(None, (denominacion_de_titulo(l) for l in primeras[:4]))) or None
    if not denom:
        denom = denominacion_de_titulo(it["titulo"], preferir_cuerpo=(cat != "local"))
    if not denom and cat == "local":
        denom = "Varias plazas" if "varias plazas" in norm(it["titulo"]) else "Una plaza (ver bases)"
    if not denom:
        denom = denominacion_de_texto(texto) or it["epigrafe"] or titulo_sin_preambulo(it["titulo"])
    r = {
        "v": VERSION,
        "orig": {k: it.get(k) for k in ("id", "titulo", "departamento", "epigrafe", "url", "fecha")},
        "id": it["id"],
        "fuente": "BOE",
        "categoria": cat,
        "titulo_oficial": it["titulo"],
        "denominacion": denom,
        "organismo": org,
        "fecha_publicacion": it["fecha"],
        "fecha_boe": it["fecha"],
        "url_bases": it["url"],
        "url_boe": it["url"],
        "plazo": extrae_plazo(texto),
        "refs_bop": refs_bop(completo) if cat == "local" else [],
        "plazas": suma or extrae_num_plazas(texto[:3000], it["titulo"]),
        "grupo": extrae_grupo(texto, it["titulo"] + " " + " ".join(notas)) or grupo_laboral(texto, it["titulo"]),
        "personal": extrae_personal(it["titulo"]) or extrae_personal(texto[:1200]) or extrae_personal(it["epigrafe"]),
        "sistema": extrae_sistema(it["titulo"] + " " + texto[:4000]),
        "tipo": tipo_proceso(it["titulo"] + " " + " ".join(notas)),
        "requisitos": extrae_requisitos(texto) if cat != "local" else None,
        "titulacion": extrae_titulacion(texto) if cat != "local" else None,
        "sevilla_en_texto": menciona_sevilla(texto) or sitio[0] == "sevilla",
        "lugares_texto": sitio[2],
    }
    if cat == "local":
        r["localidad"] = localidad_boe(it["titulo"])
    elif sitio[0] == "sevilla":
        r["localidad"] = sitio[1]
    elif cat == "universidad" and "universidad internacional de andalucia" in norm(it["titulo"]):
        r["localidad"] = "Destino no indicado"
    return r


def organismo_de_titulo(t):
    m = re.search(r"(?i),\s*(?:de la|del|de)\s+((?:Ayuntamiento|Diputaci[óo]n|Consorcio|Mancomunidad|Universidad|Agencia|Instituto|"
                  r"Presidencia|Subsecretar[íi]a|Secretar[íi]a|Direcci[óo]n|Delegaci[óo]n|Servicio|Organismo|Patronato|Gerencia|"
                  r"Fundaci[óo]n|Empresa|Consejer[íi]a|Viceconsejer[íi]a|Parlamento|C[áa]mara|Tribunal|Consejo|Mutualidad|Ente)"
                  r".*?)(?:,\s*(?:de\s+Sevilla,\s*)?(?:por la que|por el que|referente|de corrección|sobre)|\s+por la que|\s+por el que|\s+referente|$)", t)
    return limpia(m.group(1)) if m else None


def localidad_boe(t):
    m = re.search(r"Ayuntamiento de ([^,(]+?)\s*\(Sevilla\)", t)
    if m:
        return m.group(1).strip()
    if re.search(r"Ayuntamiento de Sevilla\b", t):
        return "Sevilla"
    return None


# ---------------------------------------------------------------------------
# BOJA
# ---------------------------------------------------------------------------

def boja_seccion(anio, num):
    h = descarga(f"https://www.juntadeandalucia.es/boja/{anio}/{num}/s53")
    if not h:
        return None, []
    m = re.search(r"BOJA n[ºo°]\s*\d+\s*de\s*(\d{2})/(\d{2})/(\d{4})", h)
    fecha = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else None
    i = h.find("2.2. Oposiciones")
    if i < 0:
        return fecha, []
    cuerpo = h[i:]
    j = cuerpo.find('class="boja_nav"')
    if j > 0:
        cuerpo = cuerpo[:j]
    items, organismo = [], ""
    for m in re.finditer(r'<p class="h5[^"]*">(.*?)</p>|<div class="item[^"]*">\s*<p>(.*?)</p>.*?href="(/boja/\d+/\d+/\d+)"', cuerpo, re.S):
        if m.group(1):
            organismo = limpia(html.unescape(re.sub("<[^>]+>", "", m.group(1))))
        else:
            items.append({"titulo": limpia(html.unescape(re.sub("<[^>]+>", "", m.group(2)))),
                          "url": "https://www.juntadeandalucia.es" + m.group(3),
                          "organismo_seccion": organismo, "fecha": fecha})
    return fecha, items


def completa_seccion_boja(registros):
    """Apunta la consejería (cabecera de la sección del BOJA) en los registros que llegaron por el RSS sin ella."""
    faltan = {}
    for k, r in registros.items():
        m = re.match(r"BOJA-(\d{4})-(\d+)-(\d+)$", k)
        if m and r["fuente"] == "BOJA" and not (r.get("orig") or {}).get("organismo_seccion"):
            faltan.setdefault((int(m.group(1)), int(m.group(2))), []).append(k)
    for (anio, num), ks in faltan.items():
        if sin_tiempo():
            return
        try:
            por_url = {it["url"]: it["organismo_seccion"] for it in boja_seccion(anio, num)[1]}
        except Exception:  # noqa: BLE001
            continue
        for k in ks:
            registros[k].setdefault("orig", {})["organismo_seccion"] = por_url.get(registros[k]["url_bases"]) or "-"


def primer_numero_boja(anio, desde, est):
    """Primer número del BOJA del año con fecha >= desde (búsqueda binaria; se guarda para no repetirla)."""
    if anio < desde.year:
        return 1
    clave = f"inicio_{anio}_{desde.isoformat()[:7]}"
    if clave in est:
        return est[clave]
    cache = {}

    def fecha_de(n):
        if n not in cache:
            try:
                cache[n] = boja_seccion(anio, n)[0]
            except Exception:  # noqa: BLE001
                cache[n] = None
        return cache[n]

    lo, hi = 1, 400
    while lo < hi:
        mid = (lo + hi) // 2
        f = fecha_de(mid)
        if f is None or f >= desde.isoformat():
            hi = mid
        else:
            lo = mid + 1
    inicio = max(1, lo - 3)
    est[clave] = inicio
    return inicio


def boja_rss():
    x = descarga("https://www.juntadeandalucia.es/boja/distribucion/s53.xml") or ""
    items = []
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        t = re.search(r"<title>(.*?)</title>", e, re.S)
        u = re.search(r'<link href="([^"]+)"', e)
        f = re.search(r"<updated>(\d{4}-\d{2}-\d{2})", e)
        o = re.search(r"Organismo:\s*</strong>([^<]+)", e)
        if t and u and f:
            url = u.group(1).replace("http://", "https://").replace(".html", "")
            items.append({"titulo": limpia(html.unescape(t.group(1))), "url": url,
                          "organismo_seccion": limpia(o.group(1)) if o else "", "fecha": f.group(1)})
    return items


def clasifica_boja(it):
    t = norm(it["titulo"])
    if es_excluido(t) or es_provision(it["titulo"]):
        return False
    if not RE_CONVOCA_BOJA.search(t):
        return False
    if re.search(r"universidad", t) and not UNIV_SEVILLA.search(t):
        return False
    if lugares_no_sevilla(it["titulo"]) and not menciona_sevilla(it["titulo"]):
        return False
    return True


def procesa_boja(estado, registros, hoy):
    est = estado.setdefault("boja", {"numeros": {}})
    vistos = estado.setdefault("boja_vistos", {})
    desde = hoy - timedelta(days=VENTANA_BOJA)
    nuevos = errores = 0
    candidatos = []
    for anio in sorted({desde.year, hoy.year}):
        hechos = set(est["numeros"].setdefault(str(anio), []))
        num, fallos = primer_numero_boja(anio, desde, est), 0
        while num < 400 and fallos < 4 and not sin_tiempo():
            if num in hechos:
                num += 1
                fallos = 0
                continue
            try:
                fecha, items = boja_seccion(anio, num)
            except Exception as e:  # noqa: BLE001
                log("  BOJA sin número", anio, num, e)
                errores += 1
                num += 1
                continue
            if fecha is None and not items:
                fallos += 1
                num += 1
                continue
            fallos = 0
            if fecha and fecha >= desde.isoformat():
                candidatos.extend(items)
            if fecha and fecha < (hoy - timedelta(days=REVISION)).isoformat():
                hechos.add(num)
            num += 1
        est["numeros"][str(anio)] = sorted(hechos)
    try:
        candidatos.extend(boja_rss())  # incluye números extraordinarios y complementarios
    except Exception as e:  # noqa: BLE001
        log("  BOJA RSS error", e)
        errores += 1
    for it in candidatos:
        clave = it["url"]
        rid = "BOJA-" + "-".join(clave.rstrip("/").split("/")[-3:])
        if rid in registros and it.get("organismo_seccion"):   # el RSS no trae la consejería; la sección sí
            registros[rid].setdefault("orig", {})
            if not registros[rid]["orig"].get("organismo_seccion"):
                registros[rid]["orig"]["organismo_seccion"] = it["organismo_seccion"]
        if clave in vistos or rid in registros or not clasifica_boja(it):
            continue
        try:
            r = registro_boja(it, rid)
        except Exception as e:  # noqa: BLE001
            log("  BOJA error", clave, e)
            continue
        vistos[clave] = it["fecha"] or hoy.isoformat()
        if r:
            registros[r["id"]] = r
            nuevos += 1
    estado["boja_vistos"] = poda_vistos(vistos, hoy)
    log(f"BOJA: {nuevos} registros nuevos, {errores} errores")
    if errores > 5:
        raise RuntimeError(f"{errores} números del BOJA no se pudieron descargar")
    return nuevos


def boja_pdf_texto(url, h):
    """Texto del PDF oficial de una disposición del BOJA (incluye los anexos que la página web no trae)."""
    m = re.search(r'href="(BOJA\d\d-[^"]+\.pdf)"', h)
    a = re.search(r"/boja/(\d{4})/(\d+)/", url)
    if not (m and a):
        return ""
    datos = descarga(f"https://www.juntadeandalucia.es/eboja/{a.group(1)}/{a.group(2)}/{m.group(1)}", binario=True)
    if not datos:
        return ""
    texto = pdf_a_texto(datos, modo="-raw")
    if letras_utiles(texto) < 400:
        texto = pdf_ocr(datos)
    return texto


def registro_boja(it, rid):
    h = descarga(it["url"]) or ""
    texto = html_a_texto(h)
    i = texto.find("Verificación de autenticidad")
    if i > 0:
        texto = texto[i:]
    tit = it["titulo"]
    nt = norm(tit)
    univ = bool(UNIV_SEVILLA.search(nt))
    unia = "universidad internacional de andalucia" in nt
    sas = "servicio andaluz de salud" in nt
    lugares = extrae_lugares(texto, tit)
    if not lugares:   # el anexo con los centros a veces solo está en el PDF
        lugares = extrae_lugares(boja_pdf_texto(it["url"], h))
    clase, loc, lugares_texto = resume(lugares)
    if clase == "fuera":
        return descarta({"id": rid, "titulo": tit, "departamento": it.get("organismo_seccion") or "", "fecha": it["fecha"],
                         "url": it["url"]}, f"Plazas en {lugares_texto}, ninguna en la provincia de Sevilla", "lugar")
    if clase == "sevilla":
        localidad, ambito = loc, "Provincia de Sevilla"
    elif unia:
        localidad, ambito = "Destino no indicado", "Andalucía (destino no indicado en las bases)"
    elif univ:
        localidad, ambito = "Sevilla", "Provincia de Sevilla"
    elif menciona_sevilla(tit):
        localidad, ambito = localidad_de_texto(tit) or "Sevilla", "Provincia de Sevilla"
    else:
        localidad, ambito = "Andalucía", "Andalucía"
    return {
        "v": VERSION,
        "orig": {k: it.get(k) for k in ("titulo", "url", "organismo_seccion", "fecha")},
        "id": rid,
        "fuente": "BOJA",
        "categoria": "universidad" if univ else ("sas" if sas else "junta"),
        "titulo_oficial": tit,
        "denominacion": denominacion_de_titulo(tit) or denominacion_de_texto(texto) or titulo_sin_preambulo(tit),
        "organismo": simplifica_organismo(organismo_de_titulo(tit) or "", it.get("organismo_seccion") or "") or it.get("organismo_seccion") or "Junta de Andalucía",
        "fecha_publicacion": it["fecha"],
        "url_bases": it["url"],
        "plazo": extrae_plazo(texto),
        "plazas": extrae_num_plazas(tit, texto[:4000]),
        "grupo": extrae_grupo(texto, tit) or grupo_laboral(texto, tit),
        "personal": extrae_personal(tit) or extrae_personal(texto[:2000]),
        "sistema": extrae_sistema(tit + " " + texto[:4000]),
        "tipo": tipo_proceso(tit),
        "requisitos": extrae_requisitos(texto),
        "titulacion": extrae_titulacion(texto),
        "localidad": localidad,
        "ambito": ambito,
        "lugares_texto": lugares_texto,
    }


def localidad_de_texto(t):
    m = re.search(r"(?:de|en)\s+([A-ZÁÉÍÓÚ][\wáéíóúñ]+(?:\s+(?:de|del|la|el)?\s*[A-ZÁÉÍÓÚ][\wáéíóúñ]+)*)\s*\(Sevilla\)", t)
    if m:
        return m.group(1)
    m = re.search(r"(?:de|en)\s+([A-ZÁÉÍÓÚ][\wáéíóúñ]+)\s+de\s+Sevilla", t)
    if m and m.group(1) not in ("Provincia", "Universidad", "Hospital", "Ayuntamiento", "Diputación"):
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# BOP de Sevilla
# ---------------------------------------------------------------------------

RE_BOP_CAND = re.compile(r"bases|convocatoria|proceso selectivo|procesos selectivos|seleccion|bolsa")
RE_BOP_PLAZA = re.compile(r"plazas?|puestos?|bolsa|personal|seleccion|proceso selectivo|cuerpo|escala|categoria|contratacion")
RE_BOP_NO = re.compile(r"subvenci|\bbecas?\b|\bayudas?\b(?! a domicilio)|premio|concurso de (?:agrupaciones|carteles|fotografia|pintura|relatos|ideas)|"
                       r"mercadillo|puestos? (?:del|de|en el) mercado|licencia|taxi|vivienda|concesion|enajenacion|"
                       r"alumn|voluntari|concurso literario")
# Estas palabras suelen ser de fiestas o actividades, pero no si el anuncio es para contratar a alguien
RE_BOP_NO_SALVO_EMPLEO = re.compile(r"monitores? deportiv|cabalgata|carnaval|feria")
RE_BOP_EMPLEO = re.compile(r"contratacion|proceso de seleccion|seleccion de personal|categoria de|\bplazas?\b|"
                           r"bolsa de (?:empleo|trabajo)|personal laboral|funcionari|interin")


def bop_listado(d):
    h = descarga(f"{BOP}/publica/consulta-de-bops/buscador/BOP-{d:%d-%m-%Y}/")
    if not h:
        return None
    if "elementoListado" not in h:
        return []
    items = []
    for li in re.findall(r'<li class="elementoListado.*?</li>', h, re.S):
        a = re.search(r'<a href="(/publica/buscador-anuncios/anuncio/[^"]+)"', li)
        t = re.search(r'<h3 class="titulo_elemento">\s*(.*?)</h3>', li, re.S)
        c1 = re.search(r'campo_1">\s*<div class="container">\s*(.*?)</div>', li, re.S)
        c3 = re.search(r'campo_3">\s*(.*?)</div>', li, re.S)
        if a and t:
            items.append({"url": BOP + a.group(1), "titulo": limpia(html.unescape(t.group(1))),
                          "anunciante": limpia(html.unescape(c1.group(1))) if c1 else "",
                          "cve": limpia(c3.group(1)) if c3 else "", "fecha": d.isoformat()})
    return items


def bop_candidato(it):
    t = norm(it["titulo"])
    if es_excluido(t) or RE_BOP_NO.search(t) or es_provision(it["titulo"]):
        return False
    if RE_BOP_NO_SALVO_EMPLEO.search(t) and not RE_BOP_EMPLEO.search(t):
        return False
    return bool(RE_BOP_CAND.search(t) and RE_BOP_PLAZA.search(t))


def bop_detalle(it):
    h = descarga(it["url"]) or ""
    t = html_a_texto(h)
    meta = {}
    for campo in ("Metadato", "Administración", "Sección", "Subsección", "Grupo"):
        m = re.search(campo + r":\n(.+)", t)
        if m:
            meta[campo] = m.group(1).strip()
    pdf = re.search(r'href="([^"]+\.pdf)"', h)
    if pdf:
        meta["pdf"] = BOP + pdf.group(1) if pdf.group(1).startswith("/") else pdf.group(1)
    return meta


def anunciante_norm(a):
    a = norm(a)
    if "recursos humanos" in a or "gerencia de urbanismo" in a or a.startswith("servicio de"):
        return "sevilla"
    if "empleado publico" in a or "diputacion" in a or a.startswith("area de"):
        return "diputacion"
    return a


def merece_detalle(it, registros, hoy):
    """Evita descargar bases cuyo plazo ya sabemos cerrado (por su extracto en el BOE) o demasiado antiguas."""
    edad = (hoy - date.fromisoformat(it["fecha"])).days
    if tipo_proceso(it["titulo"]) == "Bolsa o empleo temporal" and edad > VENTANA_BOLSA:
        return False
    m = re.search(r"BOP-SE-\d{4}-(\d{3})", it["cve"])
    if not m:
        return True
    num = int(m.group(1))
    anun = anunciante_norm(it["anunciante"])
    estados = []
    for b in registros.values():
        if b["fuente"] != "BOE" or b["categoria"] != "local":
            continue
        if not any(ref["num"] == num and ref["fecha"] == it["fecha"] for ref in b.get("refs_bop", [])):
            continue
        destino = norm(b.get("localidad") or ("diputacion" if "diputacion" in norm(b["organismo"]) else b["organismo"]))
        if anun and anun != destino and anun not in destino and destino not in anun:
            continue
        estados.append(calcula_estado(b, hoy)[0])
    if not estados:
        return True               # aún sin extracto en el BOE: plazo pendiente de apertura
    return any(e in ("abierto", "sin_determinar") for e in estados)


BOP_DESCARTES_V = 3   # súbelo para volver a anotar los anuncios descartados de los últimos 30 días
DIAS_DESCARTES_BOP = 30   # días de anuncios descartados del BOP que se enseñan en la pestaña de comprobación
BOP_DESCARTES = {}
RE_BOP_SELEC = re.compile(r"proceso selectivo|procesos selectivos|\bplazas?\b|\bbolsas?\b|convocatoria|oposicion|concurso|"
                          r"tribunal|aspirantes|admitid|excluid|funcionari|personal laboral|oferta de empleo|\bbases\b|"
                          r"nombramiento|seleccion|interin")
RE_BOP_RUIDO = re.compile(r"subvenci|\bbecas?\b|\bayudas?\b(?! a domicilio)|premio|ordenanza|presupuest|padron|licitacion|"
                          r"cuenta general|mercadillo|carnaval|contrato de (?:obra|servicio|suministro)|plantilla|"
                          r"relacion de puestos|valoracion de (?:los )?puestos|juez(?:es)? de paz|escolariza|alumn|deportiv|cultural|"
                          r"\bbdns\b|concurso (?:de|del) (?!meritos|oposicion|traslados)|certamen|campana|bonos?\b|comercio|consumo|"
                          r"junta general|reunion|\bpleno\b|junta de compensacion|expropiacion|concesion|cesion de bienes|"
                          r"reglamento|licencia|itinerarios|participantes|desfile|halloween|navidad")
MOTIVOS_BOP = [
    (r"correccion de error|corrigen error|rectificacion", "Corrección de errores"),
    (r"se modifica|modificacion de (?:las )?bases|se amplia|ampliacion del plazo|prorroga", "Modificación de las bases o del plazo"),
    (r"desiert|sin efecto|anula|desistimiento|renuncia", "Anulación, renuncia o plaza desierta"),
    (r"admitid|excluid|\blistas?\b|listado|relacion", "Lista de admitidos, excluidos o aprobados"),
    (r"tribunal|abstencion|recusacion", "Tribunal"),
    (r"nombra|toma de posesion|\bcese\b|adjudica|^contratacion como|personas que se citan", "Nombramiento o contratación de las personas aprobadas"),
    (r"calificaci|puntuaci|resultado|aprobad|superado|seleccionad", "Resultados o calificaciones"),
    (r"fecha|ejercicio|examen|prueba|sorteo|aplaza|lugar|respuestas|criterios de correccion|supuesto", "Fechas o desarrollo de las pruebas"),
    (r"oferta de empleo", "Oferta de empleo público (todavía no es la convocatoria)"),
    (r"bases generales", "Bases generales (sin plazas concretas)"),
    (r"provision|libre designacion|concurso de meritos|concurso especifico|movilidad|comision de servicios|habilitacion|traslado",
     "Provisión de puestos (solo para quien ya es funcionario)"),
]


def motivo_bop(titulo):
    t = norm(titulo)
    return next((m for patron, m in MOTIVOS_BOP if re.search(patron, t)), "No es una convocatoria con plazo de solicitud")


def apunta_bop(it, motivo):
    BOP_DESCARTES[it["cve"] or it["url"]] = {"fecha": it["fecha"], "titulo": it["titulo"], "anunciante": it["anunciante"],
                                            "url": it["url"], "motivo": motivo}


def procesa_bop(estado, registros, hoy):
    est = estado.setdefault("bop", {"procesados": []})
    vistos = estado.setdefault("bop_vistos", {})
    desde = hoy - timedelta(days=VENTANA_LOCAL)
    nuevos = errores = 0
    for d in pendientes(est["procesados"], desde, hoy, recientes_primero=True):
        if sin_tiempo():
            log("  BOP: tiempo agotado, se continúa en la próxima ejecución")
            break
        if d.day == 1:
            log(f"  BOP: revisando {d:%m/%Y}…")
        if d.weekday() >= 5:
            est["procesados"].append(d.isoformat())
            continue
        try:
            items = bop_listado(d)
        except Exception as e:  # noqa: BLE001
            log("  BOP sin boletín", d, e)
            errores += 1
            continue
        if items is None:
            if d < hoy - timedelta(days=REVISION):
                est["procesados"].append(d.isoformat())
            continue
        completo = True
        for it in items:
            clave = it["cve"] or it["url"]
            if clave in registros:
                continue
            if not bop_candidato(it):
                t = norm(it["titulo"])
                if RE_BOP_SELEC.search(t) and not RE_BOP_RUIDO.search(t):
                    apunta_bop(it, motivo_bop(it["titulo"]))
                continue
            if clave in vistos:
                continue
            if not merece_detalle(it, registros, hoy):
                vistos[clave] = it["fecha"]
                bolsa_vieja = tipo_proceso(it["titulo"]) == "Bolsa o empleo temporal" and \
                    (hoy - date.fromisoformat(it["fecha"])).days > VENTANA_BOLSA
                apunta_bop(it, "Bolsa publicada hace más de 4 meses" if bolsa_vieja else "Plazo ya cerrado (según su anuncio en el BOE)")
                continue
            try:
                r = registro_bop(it)
            except Exception as e:  # noqa: BLE001
                log("  BOP error", clave, e)
                completo = False
                continue
            vistos[clave] = it["fecha"]
            if r:
                registros[r["id"]] = r
                BOP_DESCARTES.pop(clave, None)
                nuevos += 1
            else:
                apunta_bop(it, "El BOP no lo clasifica como proceso selectivo")
        if completo and (items or d < hoy - timedelta(days=REVISION)):
            est["procesados"].append(d.isoformat())
    est["procesados"] = [x for x in sorted(set(est["procesados"])) if x >= desde.isoformat()]
    estado["bop_vistos"] = poda_vistos(vistos, hoy)
    nuevos += completa_desde_boe(registros, hoy)
    log(f"BOP: {nuevos} registros nuevos, {errores} días con error")
    if errores > 5:
        raise RuntimeError(f"{errores} boletines del BOP no se pudieron descargar")
    return nuevos


def completa_desde_boe(registros, hoy):
    """Si un extracto del BOE en plazo no tiene sus bases localizadas, las busca en el BOP citado."""
    une_bop_boe(registros)
    nuevos = 0
    revisados = set()
    for b in list(registros.values()):
        if b["fuente"] != "BOE" or b["categoria"] != "local" or b.get("unido_a"):
            continue
        est, _, _ = calcula_estado(b, hoy)
        if est not in ("abierto", "sin_determinar"):
            continue
        for ref in b.get("refs_bop", []):
            if (ref["num"], ref["fecha"]) in revisados:
                continue
            revisados.add((ref["num"], ref["fecha"]))
            try:
                items = bop_listado(date.fromisoformat(ref["fecha"])) or []
            except Exception as e:  # noqa: BLE001
                log("  BOP (desde BOE) error", ref, e)
                continue
            loc = norm(b.get("localidad") or ("diputacion" if "diputacion" in norm(b["organismo"]) else ""))
            for it in items:
                clave = it["cve"] or it["url"]
                if clave in registros:
                    continue
                t = norm(it["titulo"])
                if es_excluido(t) or not re.search(r"bases|convocatoria", t):
                    continue
                anun = anunciante_norm(it["anunciante"])
                if loc and loc != anun and loc not in anun and anun not in loc:
                    continue
                try:
                    r = registro_bop(it)
                except Exception as e:  # noqa: BLE001
                    log("  BOP error", clave, e)
                    continue
                if r:
                    registros[r["id"]] = r
                    nuevos += 1
    return nuevos


BG_CACHE = {}   # bases generales ya leídas: se guarda en estado.json


def ref_bases_generales(texto, fecha_pub):
    """Número y fecha del BOP donde se publicaron las bases generales que cita unas bases específicas."""
    t = re.sub(r"\s+", " ", texto or "")
    pub = date.fromisoformat(fecha_pub)
    resoluciones = []
    for m in re.finditer(r"(?i)bases\s+generales", t):
        ventana = t[m.start(): m.start() + 700]
        alrededor = t[max(0, m.start() - 300): m.start() + 700]
        res = re.search(r"(?i)resoluci[óo]n\s+(?:n[.º°o]*\s*|n[uú]m\.?\s*)?(\d{2,6}/20\d\d)", alrededor)
        if res:
            resoluciones.append(res.group(1))
        r = re.search(r"(?i)(?:\bB\.?\s?O\.?\s?P\.?(?=[\s,]|$)|Bolet[íi]n\s+Oficial\s+de\s+la\s+Provincia(?:\s+de\s+Sevilla)?)[^0-9]{0,25}?"
                      r"(?:n\.?\s?[º°o]\.?\s*|n[uú]m(?:ero)?\.?\s*)(\d{1,3}),?\s*(?:de\s+(?:fecha\s+)?)?(\d{1,2})"
                      r"(?:\s+de\s+([a-záéíóú]+)(?:\s+(?:de\s+)?(\d{4}))?|[/\-.](\d{1,2})[/\-.](\d{2,4}))", ventana)
        if not r:
            continue
        num, dia = int(r.group(1)), int(r.group(2))
        if r.group(3):
            mes = MESES.get(norm(r.group(3)))
            anio = int(r.group(4)) if r.group(4) else None
        else:
            mes = int(r.group(5))
            anio = int(r.group(6)) if r.group(6) else None
            if anio and anio < 100:
                anio += 2000
        if not mes:
            continue
        if not anio:
            previo = t[max(0, m.start() - 500): m.start() + r.start()]
            por_resolucion = re.findall(r"\d+/(20\d\d)", previo)
            antes = por_resolucion or re.findall(r"(20\d\d)", previo)
            anio = int(antes[-1]) if antes else pub.year
        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue
        if f > pub:
            try:
                f = date(anio - 1, mes, dia)
            except ValueError:
                continue
        if res:
            BG_CACHE[f"res|{res.group(1)}"] = f"{num}|{f.isoformat()}"
        return num, f
    for res in resoluciones:   # solo cita la resolución: se usa la referencia aprendida de otras bases
        conocido = BG_CACHE.get(f"res|{res}")
        if conocido:
            n, fi = conocido.split("|")
            return int(n), date.fromisoformat(fi)
    return None


def requisitos_bases_generales(texto, fecha_pub, anunciante):
    ref = ref_bases_generales(texto, fecha_pub)
    if not ref:
        return None
    num, f = ref
    anun = anunciante_norm(anunciante)
    clave = f"{num}|{f.isoformat()}|{anun}"
    if clave in BG_CACHE:
        return BG_CACHE[clave]
    resultado = None
    try:
        items = bop_listado(f) or []
        cands = [it for it in items if "bases generales" in norm(it["titulo"])]
        propios = [it for it in cands if anunciante_norm(it["anunciante"]) == anun]
        for it in (propios or cands)[:2]:
            meta = bop_detalle(it)
            if not meta.get("pdf"):
                continue
            datos = descarga(meta["pdf"], binario=True)
            txt = pdf_a_texto(datos) if datos else ""
            if letras_utiles(txt) < 400 and datos:
                txt = pdf_ocr(datos)
            req = extrae_requisitos(txt)
            if req:
                resultado = {"texto": req, "url": it["url"], "fecha": f.isoformat(), "titulo": it["titulo"]}
                break
    except Exception as e:  # noqa: BLE001
        log("  bases generales error", clave, e)
        return None
    BG_CACHE[clave] = resultado
    return resultado


def registro_bop(it):
    meta = bop_detalle(it)
    md = norm(meta.get("Metadato", ""))
    if md and "selectiv" not in md and "empleo" not in md and "personal" not in md:
        return None
    texto, ocr = "", False
    if meta.get("pdf"):
        datos = descarga(meta["pdf"], binario=True)
        if datos:
            texto = pdf_a_texto(datos)
            if letras_utiles(texto) < 400:      # PDF escaneado: solo tiene los sellos del BOP
                leido = pdf_ocr(datos)
                if letras_utiles(leido) > letras_utiles(texto):
                    texto, ocr = leido, True
    seccion = norm(meta.get("Sección", ""))
    sub = meta.get("Subsección") or it["anunciante"]
    if seccion == "ayuntamientos":
        organismo, localidad, cat = "Ayuntamiento de " + sub, sub, "ayuntamiento"
    elif "diputacion" in seccion:
        organismo, localidad, cat = "Diputación de Sevilla" + (f" · {sub}" if sub else ""), "Sevilla", "diputacion"
    else:
        organismo = sub or it["anunciante"] or meta.get("Sección", "")
        localidad = localidad_de_texto(texto[:3000]) or "Provincia de Sevilla"
        cat = "otros_locales"
    m = re.search(r"BOP-SE-(\d{4})-(\d{3})", it["cve"])
    tit = it["titulo"]
    requisitos = extrae_requisitos(texto)
    generales = None
    if (not requisitos or len(requisitos) < 900) and re.search(r"(?i)bases\s+generales", texto):
        generales = requisitos_bases_generales(texto, it["fecha"], it.get("anunciante") or sub)
    return {
        "v": VERSION,
        "orig": {k: it.get(k) for k in ("url", "titulo", "anunciante", "cve", "fecha")},
        "id": it["cve"] or it["url"],
        "fuente": "BOP",
        "categoria": cat,
        "titulo_oficial": tit,
        "denominacion": denominacion_de_titulo(tit) or denominacion_de_texto(texto) or titulo_sin_preambulo(tit),
        "organismo": organismo,
        "fecha_publicacion": it["fecha"],
        "bop_num": int(m.group(2)) if m else None,
        "url_bases": it["url"],
        "url_pdf": meta.get("pdf"),
        "plazo": extrae_plazo(texto),
        "plazas": extrae_num_plazas(tit, texto[:6000]),
        "grupo": extrae_grupo(texto, tit) or grupo_laboral(texto, tit),
        "personal": extrae_personal(tit) or extrae_personal(texto[:4000]),
        "sistema": extrae_sistema(tit) or extrae_sistema(texto[-15000:]) or extrae_sistema(texto[:8000]),
        "tipo": tipo_proceso(tit),
        "requisitos": requisitos,
        "requisitos_generales": generales,
        "titulacion": extrae_titulacion(texto),
        "localidad": localidad,
        "ambito": "Provincia de Sevilla",
        "sin_texto": letras_utiles(texto) < 400,
        "ocr": ocr,
    }


# ---------------------------------------------------------------------------
# Uniones entre boletines y cálculo de vigencia
# ---------------------------------------------------------------------------

STOP = {"de", "del", "la", "el", "los", "las", "y", "en", "a", "plaza", "plazas", "una", "un", "por", "turno",
        "libre", "personal", "laboral", "funcionario", "fijo", "vacante", "vacantes", "plantilla", "oep",
        "sistema", "oposicion", "concurso", "primera", "resolucion", "convocatoria", "provision", "para",
        "que", "se", "convoca", "proceso", "selectivo", "ingreso", "acceso", "general", "noviembre", "octubre"}


def tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", norm(s)) if w not in STOP and len(w) > 2 and not w.isdigit()}


def une_bop_boe(registros):
    bops = [r for r in registros.values() if r["fuente"] == "BOP"]
    for r in bops:
        r.pop("boe", None)
    for b in [r for r in registros.values() if r["fuente"] == "BOE" and r["categoria"] == "local"]:
        b["unido_a"] = []
        loc = norm(b.get("localidad") or "")
        for ref in b.get("refs_bop", []):
            cands = [r for r in bops if r.get("bop_num") == ref["num"] and r["fecha_publicacion"] == ref["fecha"]]
            if loc:
                cands = [r for r in cands if norm(r.get("localidad") or "") == loc or loc in norm(r["organismo"])]
            else:
                cands = [r for r in cands if tokens(b["organismo"]) & tokens(r["organismo"])]
            tb = tokens(b["denominacion"])
            generico = not tb or norm(b["denominacion"]).startswith(("varias plazas", "una plaza", "personal funcionario"))
            if cands and not generico:
                punt = sorted(((len(tb & tokens(r["denominacion"] + " " + r["titulo_oficial"])), r) for r in cands),
                              key=lambda x: -x[0])
                if punt[0][0] == 0:
                    cands = []  # mismo boletín y municipio, pero otra plaza (p. ej., una corrección de otras bases)
                elif "varias" not in norm(b["titulo_oficial"]):
                    cands = [r for p, r in punt if p == punt[0][0]]
                else:
                    cands = [r for p, r in punt if p > 0]
            for r in cands:
                if r.get("boe") and r["boe"]["fecha"] >= b["fecha_boe"]:
                    continue
                r["boe"] = {"id": b["id"], "fecha": b["fecha_boe"], "url": b["url_boe"], "plazo": b.get("plazo")}
                b["unido_a"].append(r["id"])


def une_univ(registros):
    """Una convocatoria universitaria publicada en BOJA y BOE se muestra una sola vez."""
    bojas = [r for r in registros.values() if r["fuente"] == "BOJA" and r["categoria"] == "universidad"]
    for b in [r for r in registros.values() if r["fuente"] == "BOE" and r["categoria"] == "universidad"]:
        b["duplicado_de"] = None
        tb = tokens(b["titulo_oficial"])
        for j in bojas:
            tj = tokens(j["titulo_oficial"])
            if not tb or not tj:
                continue
            parecido = len(tb & tj) / len(tb | tj)
            dias = (date.fromisoformat(b["fecha_publicacion"]) - date.fromisoformat(j["fecha_publicacion"])).days
            if parecido >= 0.6 and -30 <= dias <= 90:
                j["boe"] = {"id": b["id"], "fecha": b["fecha_boe"], "url": b["url_boe"], "plazo": b.get("plazo")}
                b["duplicado_de"] = j["id"]
                break


DIAS_PENDIENTE_BOE = 300      # bases que dicen que el plazo se abre con el BOE
DIAS_PENDIENTE_SIN_PLAZO = 180  # bases de oposición sin plazo legible (casi siempre lo abre el BOE)
DIAS_SIN_PLAZO = 45            # resto de anuncios sin plazo legible
DIAS_SIN_PLAZO_PI = 90         # promoción interna sin plazo legible


def calcula_estado(r, hoy, con_maximo=False):
    """Devuelve (estado, fin_plazo_iso, estimada) y, con con_maximo, también la fecha más tardía posible.
    En las del Estado se muestra la fecha más temprana (solo festivos nacionales), pero la convocatoria sigue en
    la lista hasta la más tardía (contando los festivos de Andalucía, art. 30.6 de la Ley 39/2015)."""
    publicada = date.fromisoformat(r["fecha_publicacion"])
    edad = (hoy - publicada).days
    boe = r.get("boe")
    p = (boe or {}).get("plazo") or r.get("plazo")
    tipo = tipo_proceso(r.get("titulo_oficial", ""))
    if r["fuente"] in ("BOP", "BOJA") and boe:
        inicio = date.fromisoformat(boe["fecha"])
    elif r["fuente"] == "BOE":
        inicio = date.fromisoformat(r["fecha_boe"])
    else:
        inicio = publicada
        if p and (p.get("boletin") == "BOE" or (r["fuente"] == "BOP" and p.get("boletin") == "BOJA")):
            return ("pendiente", None, False) if edad <= DIAS_PENDIENTE_BOE else ("caducada", None, False)
        if r["fuente"] == "BOP" and not p:
            if tipo == "Acceso libre":
                return ("pendiente_sin_plazo", None, False) if edad <= DIAS_PENDIENTE_SIN_PLAZO else ("caducada", None, False)
            if tipo == "Promoción interna":
                return ("sin_determinar", None, False) if edad <= DIAS_SIN_PLAZO_PI else ("caducada", None, False)
    # fecha de fin que da el propio organismo en el buscador oficial del Estado (solo convocatorias del Estado)
    oficial = date.fromisoformat(r["fin_oficial"]) if r.get("fin_oficial") and r["categoria"] == "estado" else None
    if not p:
        if oficial:
            res = ("abierto" if oficial >= hoy else "cerrado"), oficial.isoformat(), False
            return res + (None,) if con_maximo else res
        return ("sin_determinar", None, False) if edad <= DIAS_SIN_PLAZO else ("caducada", None, False)
    andalucia = FESTIVOS_ANDALUCES_EN_ESTADO or r["categoria"] != "estado"
    fin = suma_plazo(inicio, p["n"], p["tipo"], andalucia=andalucia)
    ultimo = fin if andalucia else suma_plazo(inicio, p["n"], p["tipo"], andalucia=True)
    estimada = r["fuente"] == "BOP" or r["categoria"] in ("local", "ayuntamiento", "diputacion", "otros_locales", "estado")
    hasta = max(ultimo, oficial) if oficial else ultimo   # sigue en la lista hasta la fecha más tardía
    res = ("abierto" if hasta >= hoy else "cerrado"), fin.isoformat(), estimada
    return res + ((ultimo.isoformat() if ultimo != fin else None),) if con_maximo else res


TIPO_ORG = {
    "ayuntamiento": "Ayuntamiento", "local": "Ayuntamiento", "diputacion": "Diputación de Sevilla",
    "otros_locales": "Otros entes locales", "universidad": "Universidad", "junta": "Junta de Andalucía",
    "sas": "Servicio Andaluz de Salud", "estado": "Administración del Estado",
}


def ficha(r, hoy):
    est, fin, estimada, fin_max = (tuple(calcula_estado(r, hoy, con_maximo=True)) + (None,))[:4]
    cat = r["categoria"]
    if cat == "estado":
        localidad = r.get("localidad") or "Toda España"
        ambito = "Toda España (con plazas en Sevilla)" if r.get("sevilla_en_texto") else "Toda España (posible destino en Sevilla)"
    elif cat == "local":
        localidad = r.get("localidad") or "Provincia de Sevilla"
        ambito = "Provincia de Sevilla"
        if "diputacion" in norm(r["organismo"]):
            cat = "diputacion"
        elif not r.get("localidad"):
            cat = "otros_locales"
    elif cat == "universidad":
        localidad = r.get("localidad") or "Sevilla"
        ambito = ("Andalucía (destino no indicado en las bases)" if localidad == "Destino no indicado"
                  else "Provincia de Sevilla")
    else:
        localidad = r.get("localidad") or "Andalucía"
        ambito = r.get("ambito") or "Andalucía"
        if ambito == "Andalucía":
            ambito = "Andalucía (posible destino en Sevilla)"
    boe = r.get("boe") or {}
    return {
        "id": r["id"],
        "denominacion": r["denominacion"],
        "titulo_oficial": r["titulo_oficial"],
        "organismo": r["organismo"],
        "departamento": (titulo_bonito((r.get("orig") or {}).get("departamento") or "") if r["fuente"] == "BOE"
                         else ((r.get("orig") or {}).get("organismo_seccion") or "").strip("-") if r["fuente"] == "BOJA" else ""),
        "tipo_organismo": TIPO_ORG.get(cat, "Otros"),
        "localidad": localidad,
        "lugares_texto": r.get("lugares_texto"),
        "ambito": ambito,
        "grupo": r.get("grupo") or "Sin determinar",
        "plazas": r.get("plazas"),
        "personal": r.get("personal") or "Sin determinar",
        "sistema": r.get("sistema") or "Sin determinar",
        "tipo": tipo_proceso(r.get("titulo_oficial", "")),
        "fuente": r["fuente"],
        "fecha_publicacion": r["fecha_publicacion"],
        "fecha_boe": boe.get("fecha") or r.get("fecha_boe"),
        "en_boe": bool(boe) or r["fuente"] == "BOE",
        "url_bases": r["url_bases"],
        "url_pdf": r.get("url_pdf"),
        "url_boe": boe.get("url") or r.get("url_boe"),
        "estado": est,
        "fin_plazo": fin,
        "fin_estimado": estimada,
        "fin_plazo_max": fin_max,
        "fin_oficial": (r["fin_oficial"] if cat == "estado" and r.get("fin_oficial") and fin
                        and r["fin_oficial"] > (fin_max or fin) else None),
        "plazo_texto": (boe.get("plazo") or r.get("plazo") or {}).get("frase"),
        "requisitos": quita_firmas(r.get("requisitos")),
        "titulacion": r.get("titulacion") if titulacion_valida(r.get("titulacion")) else None,
        "ocr": bool(r.get("ocr")),
        "requisitos_generales": ({**r["requisitos_generales"], "texto": quita_firmas(r["requisitos_generales"].get("texto"))}
                                 if r.get("requisitos_generales") else None),
    }


VIGENTES = {"abierto", "pendiente", "pendiente_sin_plazo", "sin_determinar"}


def sigue_valido(r):
    """Aplica los filtros actuales a registros guardados con versiones anteriores del programa."""
    t = r.get("titulo_oficial", "")
    nt = norm(t)
    if es_excluido(nt) or es_provision(t) or en_el_extranjero(t):
        return False
    if r["fuente"] == "BOJA" and not RE_CONVOCA_BOJA.search(nt):
        return False
    if r["fuente"] == "BOP" and RE_BOP_NO.search(nt):
        return False
    if r["fuente"] == "BOE" and r["categoria"] == "estado" and re.search(r"instituto nacional de gestion sanitaria", nt):
        return False
    return True


def reprocesa(registros, hoy):
    """Vuelve a leer los registros vigentes guardados con una versión anterior del programa."""
    une_bop_boe(registros)
    hechos = 0
    for k in list(registros):
        r = registros[k]
        if r.get("v", 1) >= VERSION or sin_tiempo():
            continue
        est = calcula_estado(r, hoy)[0]
        local_boe = r["fuente"] == "BOE" and r["categoria"] == "local"
        if not sigue_valido(r) or (est not in VIGENTES and not local_boe):
            continue
        o = r.get("orig") or {}
        try:
            if r["fuente"] == "BOE":
                it = {"id": r["id"], "titulo": o.get("titulo") or r["titulo_oficial"],
                      "departamento": o.get("departamento") or {"local": "ADMINISTRACIÓN LOCAL", "universidad": "UNIVERSIDADES"}.get(r["categoria"], ""),
                      "epigrafe": o.get("epigrafe") or "", "url": r["url_bases"], "fecha": r["fecha_boe"]}
                nuevo = registro_boe(it, r["categoria"])
                if nuevo and r["categoria"] == "estado" and not it["departamento"]:
                    nuevo["organismo"] = r["organismo"]
            elif r["fuente"] == "BOJA":
                it = {"titulo": r["titulo_oficial"], "url": r["url_bases"],
                      "organismo_seccion": o.get("organismo_seccion") or r.get("organismo_seccion") or "", "fecha": r["fecha_publicacion"]}
                nuevo = registro_boja(it, r["id"])
            else:
                anun = o.get("anunciante") or ("Diputación" if "diputaci" in norm(r["organismo"]) else r.get("localidad") or "")
                it = {"url": r["url_bases"], "titulo": r["titulo_oficial"], "anunciante": anun,
                      "cve": r["id"], "fecha": r["fecha_publicacion"]}
                nuevo = registro_bop(it)
        except Exception as e:  # noqa: BLE001
            log("  reproceso error", k, e)
            continue
        pa, pn = r.get("plazo") or {}, (nuevo or {}).get("plazo") or {}
        if nuevo and (pa.get("n"), pa.get("tipo")) != (pn.get("n"), pn.get("tipo")):
            log("  plazo cambiado", k, "|", (pa.get("frase") or "-")[:100], "=>", (pn.get("frase") or "-")[:100])
        if nuevo:
            registros[k] = nuevo
        else:
            del registros[k]
        hechos += 1
    log(f"Registros releídos con la versión {VERSION}: {hechos}")


def genera_salida(registros, hoy, estado):
    une_bop_boe(registros)
    une_univ(registros)
    fichas = []
    for r in registros.values():
        if r["fuente"] == "BOE" and (r.get("unido_a") or r.get("duplicado_de")):
            continue  # ya se muestra junto a sus bases
        if not sigue_valido(r):
            continue
        f = ficha(r, hoy)
        if f["tipo"] == "Provisión de puestos":
            continue
        if f["estado"] in VIGENTES:
            fichas.append(f)
    fichas.sort(key=lambda f: (f["fecha_publicacion"], f["id"]), reverse=True)
    salida = {
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "fecha_referencia": hoy.isoformat(),
        "fuentes": estado.get("fuentes", {}),
        "total": len(fichas),
        "convocatorias": fichas,
        "descartes_lugar": descartes_lugar(hoy),
        "descartes_bop": sorted((d for d in BOP_DESCARTES.values()
                                 if d["fecha"] >= (hoy - timedelta(days=DIAS_DESCARTES_BOP)).isoformat()),
                                key=lambda d: (d["fecha"], d["anunciante"]), reverse=True),
    }
    guarda(F_OUT, salida)
    return salida


def descartes_lugar(hoy):
    """Convocatorias quitadas de la lista porque sus plazas no están en la provincia de Sevilla (últimos 60 días)."""
    desde = (hoy - timedelta(days=60)).isoformat()
    xs = []
    for d in DESCARTES.values():
        if d.get("tipo") not in ("lugar", "madrid") or not (d["fecha"] >= desde or d.get("visto", "") >= desde):
            continue
        t = d["titulo"]
        xs.append({"id": d["id"], "fecha": d["fecha"], "motivo": lugares_bonitos(d["motivo"]), "url": d.get("url"),
                   "nombre": denominacion_de_titulo(t) or titulo_sin_preambulo(t),
                   "organismo": org if (org := organismo_de_titulo(t)) and not re.match(r"(?i)subsecretar|secretar[íi]a (?:general|de estado)", org)
                   else titulo_bonito(d.get("departamento") or "") or org or ""})
    return sorted(xs, key=lambda x: x["fecha"], reverse=True)


def recupera_boe(boe_id, registros):
    """Lee una convocatoria del BOE que el buscador oficial da en plazo y que aquí no estaba (p. ej. borrada por
    haberla dado por cerrada antes de tiempo)."""
    x = descarga(f"https://www.boe.es/diario_boe/xml.php?id={boe_id}") or ""
    tit = re.search(r"<titulo>(.*?)</titulo>", x, re.S)
    dep = re.search(r"<departamento[^>]*>(.*?)</departamento>", x, re.S)
    f = re.search(r"<fecha_publicacion>(\d{8})</fecha_publicacion>", x)
    if not (tit and f):
        return None
    it = {"id": boe_id, "titulo": html.unescape(tit.group(1)).strip(), "departamento": html.unescape(dep.group(1)).strip() if dep else "",
          "epigrafe": "", "fecha": f"{f.group(1)[:4]}-{f.group(1)[4:6]}-{f.group(1)[6:]}",
          "url": f"https://www.boe.es/diario_boe/txt.php?id={boe_id}"}
    if clasifica_boe(it) != "estado":
        return None
    r = registro_boe(it, "estado")
    if r:
        registros[boe_id] = r
        log("  recuperada desde el buscador oficial:", boe_id, it["titulo"][:90])
    return r


def fechas_oficiales(registros, items, hoy):
    """Apunta en las convocatorias del Estado la fecha de fin que da el organismo en el buscador oficial.
    Devuelve True si ha cambiado alguna."""
    cambio = False
    for it in items:
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", it.get("fin") or "")
        if not m:
            continue
        fin = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        ids = ([it["coincide_con"]] if it.get("coincide_con") else []) + (it.get("detalle") or {}).get("boe", [])
        for b in ids:
            r = registros.get(b) or next((x for x in registros.values() if b in (x.get("url_boe") or "")), None)
            if r is None and b.startswith("BOE-A") and b not in DESCARTES and fin >= hoy.isoformat():
                try:
                    r = recupera_boe(b, registros)
                except Exception as e:  # noqa: BLE001
                    log("  no se pudo recuperar", b, e)
            if r is None:
                continue
            if r.get("categoria") == "estado" and r.get("fin_oficial") != fin:
                r["fin_oficial"] = fin
                cambio = True
            break
    return cambio


def purga(registros, hoy):
    """Borra lo cerrado o fuera de ventana para que los datos no crezcan sin límite."""
    une_bop_boe(registros)
    une_univ(registros)
    for k in list(registros):
        r = registros[k]
        if (hoy - date.fromisoformat(r["fecha_publicacion"])).days > VENTANA_LOCAL + 5:
            del registros[k]
            continue
        if r["fuente"] == "BOE" and r["categoria"] == "local":
            continue  # hacen falta para unirlos con las bases del BOP
        est, fin, _, fmax = (tuple(calcula_estado(r, hoy, con_maximo=True)) + (None,))[:4]
        if r["categoria"] == "estado" and est == "cerrado" and fin and sigue_valido(r) and \
                date.fromisoformat(fmax or fin) >= hoy - timedelta(days=DIAS_MARGEN_ESTADO):
            continue   # se guarda unos días por si el organismo da una fecha de fin posterior
        if est in ("cerrado", "caducada") or not sigue_valido(r):
            del registros[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hoy", help="fecha de referencia AAAA-MM-DD")
    ap.add_argument("--solo-salida", action="store_true", help="no descarga nada; solo recalcula la página")
    ap.add_argument("--fuentes", default="boe,bop,boja,pag")
    a = ap.parse_args()
    hoy = date.fromisoformat(a.hoy) if a.hoy else (datetime.now(timezone.utc) + timedelta(hours=1)).date()
    estado = carga(F_EST, {})
    registros = carga(F_REG, {})
    estado.setdefault("fuentes", {})
    BG_CACHE.update(estado.get("bases_generales", {}))
    limite = (hoy - timedelta(days=VENTANA_ESTADO)).isoformat()
    hace_30 = (hoy - timedelta(days=DIAS_DESCARTES_BOP)).isoformat()
    BOP_DESCARTES.update({k: v for k, v in estado.get("bop_descartes", {}).items() if v["fecha"] >= hace_30})
    if not a.solo_salida and estado.get("bop_descartes_v", 0) < BOP_DESCARTES_V:
        BOP_DESCARTES.clear()
        log(f"Se repasan los últimos {DIAS_DESCARTES_BOP} días del BOP para anotar los anuncios descartados")
        est_bop = estado.setdefault("bop", {"procesados": []})
        est_bop["procesados"] = [d for d in est_bop.get("procesados", []) if d < hace_30]
        estado["bop_vistos"] = {k: v for k, v in estado.get("bop_vistos", {}).items() if v < hace_30}
        estado["bop_descartes_v"] = BOP_DESCARTES_V
    hace_60 = (hoy - timedelta(days=60)).isoformat()
    DESCARTES.update({k: v for k, v in estado.get("descartes", {}).items()
                      if v["fecha"] >= limite or v.get("visto", "") >= hace_60})
    if not a.solo_salida and estado.get("boe_scan", 0) < BOE_SCAN_VERSION:
        log(f"Nueva revisión de los días del BOE de los últimos {VENTANA_ESTADO} días (versión {BOE_SCAN_VERSION})")
        estado.setdefault("boe", {"procesados": []})["procesados"] = [d for d in estado["boe"].get("procesados", []) if d < limite]
        estado["boe_vistos"] = {k: v for k, v in estado.get("boe_vistos", {}).items() if v < limite}
        estado["boe_scan"] = BOE_SCAN_VERSION
        estado.pop("descartes_version", None)
    if not a.solo_salida and estado.get("scan_version", 1) < SCAN_VERSION:
        log(f"Nueva revisión completa del BOP y del BOJA (versión {SCAN_VERSION})")
        estado.setdefault("bop", {})["procesados"] = []
        estado.setdefault("boja", {})["numeros"] = {}
        estado["scan_version"] = SCAN_VERSION
    if not a.solo_salida:
        for nombre, fn in (("BOE", procesa_boe), ("BOJA", procesa_boja), ("BOP", procesa_bop)):
            if nombre.lower() not in a.fuentes:
                continue
            ini = time.time()
            try:
                n = fn(estado, registros, hoy)
                estado["fuentes"][nombre] = {"ok": True, "fecha": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                             "nuevos": n, "segundos": round(time.time() - ini)}
            except Exception as e:  # noqa: BLE001
                log(f"ERROR en {nombre}: {e}")
                estado["fuentes"][nombre] = {"ok": False, "fecha": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                             "error": str(e)[:300]}
            guarda(F_REG, registros)
            guarda(F_EST, estado)
    if not a.solo_salida:
        completa_seccion_boja(registros)
        reprocesa(registros, hoy)
        guarda(F_REG, registros)
    estado["bases_generales"] = BG_CACHE
    estado["descartes"] = DESCARTES
    estado["bop_descartes"] = {k: v for k, v in BOP_DESCARTES.items() if v["fecha"] >= hace_30}
    purga(registros, hoy)
    salida = genera_salida(registros, hoy, estado)
    if not a.solo_salida and "pag" in a.fuentes:
        import comprobacion
        comp, revisadas, estado["pag_detalles"] = comprobacion.comprobacion(
            salida["convocatorias"], DESCARTES, estado.get("pag_detalles"))
        if revisadas and fechas_oficiales(registros, revisadas, hoy):
            salida = genera_salida(registros, hoy, estado)   # con las fechas que da el organismo
            comp = comprobacion.rehace(comp, revisadas, salida["convocatorias"], DESCARTES)
        estado["comprobacion"] = comp
        if revisadas:
            guarda(os.path.join(DATA, "buscador_oficial.json"), revisadas)   # para auditar la comparación
        log(f"Comprobación con el buscador oficial: {comp.get('total')} en plazo, "
            f"{len(comp.get('posibles_omisiones', []))} posibles omisiones" if comp.get("ok") else f"Comprobación con error: {comp.get('error')}")
    if estado.get("comprobacion"):
        salida["comprobacion"] = estado["comprobacion"]
        guarda(F_OUT, salida)
    guarda(F_REG, registros)
    guarda(F_EST, estado)
    log(f"Convocatorias vigentes: {salida['total']}")


if __name__ == "__main__":
    main()
