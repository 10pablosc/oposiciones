"""Utilidades para leer y extraer datos de los textos de las convocatorias."""
import re
import unicodedata
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def norm(s):
    """Minúsculas, sin tildes y con espacios simples (para comparar textos)."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


def limpia(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------------------
# Calendario de días inhábiles (sábados, domingos, festivos nacionales y de
# Andalucía). Fuente: resoluciones de la Secretaría de Estado de Función
# Pública BOE-A-2025-23702 (año 2026) y BOE-A-2026-23826 (año 2027).
# ---------------------------------------------------------------------------

FESTIVOS = {
    "2025-01-01", "2025-01-06", "2025-02-28", "2025-04-17", "2025-04-18",
    "2025-05-01", "2025-08-15", "2025-11-01", "2025-12-06", "2025-12-08",
    "2025-12-25",
    "2026-01-01", "2026-01-06", "2026-04-02", "2026-04-03", "2026-05-01",
    "2026-10-12", "2026-11-02", "2026-12-07", "2026-12-08", "2026-12-25",
    "2027-01-01", "2027-01-06", "2027-03-01", "2027-03-25", "2027-03-26",
    "2027-08-16", "2027-10-12", "2027-11-01", "2027-12-06", "2027-12-08",
}
# Años cuyo calendario está cargado. Para años posteriores el plazo se marca
# como estimado (solo se descuentan fines de semana y festivos fijos).
ANIOS_CALENDARIO = {2025, 2026, 2027}
FIJOS = {"01-01", "01-06", "05-01", "08-15", "10-12", "11-01", "12-06", "12-08", "12-25", "02-28"}


def es_inhabil(d):
    if d.weekday() >= 5:
        return True
    if d.year in ANIOS_CALENDARIO:
        return d.isoformat() in FESTIVOS
    return d.strftime("%m-%d") in FIJOS


def siguiente_habil(d):
    while es_inhabil(d):
        d += timedelta(days=1)
    return d


def suma_plazo(publicacion, n, tipo):
    """Último día de un plazo de n días (hábiles/naturales/meses) que empieza
    a contar el día siguiente a la publicación (art. 30 Ley 39/2015)."""
    if tipo == "meses":
        m = publicacion.month - 1 + n
        y = publicacion.year + m // 12
        m = m % 12 + 1
        import calendar
        dia = min(publicacion.day, calendar.monthrange(y, m)[1])
        fin = date(y, m, dia)
        return siguiente_habil(fin)
    if tipo == "naturales":
        fin = publicacion + timedelta(days=n)
        return siguiente_habil(fin)
    # hábiles
    d = publicacion
    contados = 0
    while contados < n:
        d += timedelta(days=1)
        if not es_inhabil(d):
            contados += 1
    return d


# ---------------------------------------------------------------------------
# Números escritos
# ---------------------------------------------------------------------------

NUMEROS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuna": 21, "veintiuno": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
}


def a_numero(palabra):
    p = norm(palabra).strip("() ")
    if p.isdigit():
        return int(p)
    return NUMEROS.get(p)


# ---------------------------------------------------------------------------
# Plazo de presentación
# ---------------------------------------------------------------------------

RE_PLAZO = re.compile(
    r"plazo\s+(?:de\s+)?(?:presentacion\s+de\s+)?(?:las\s+)?(?:solicitudes|instancias)"
    r"(?P<medio>[^.]{0,160}?)"
    r"(?P<num>\b[a-z]+\b|\d+)\s*(?:\(\d+\)\s*)?(?P<tipo>dias?\s+habiles|dias?\s+naturales|dias?|mes(?:es)?)\b"
    r"(?P<resto>.{0,260})",
    re.S,
)
RE_PLAZO2 = re.compile(
    r"(?P<num>\b[a-z]+\b|\d+)\s*(?:\(\d+\)\s*)?(?P<tipo>dias?\s+habiles|dias?\s+naturales|mes(?:es)?)\b"
    r"(?P<resto>.{0,260})",
    re.S,
)


def _boletin_de(resto):
    r = resto
    cortes = [i for i in (r.find(". "), r.find(".\n")) if i > 0]
    if cortes:
        r = r[: min(cortes) + 1]
    if re.search(r"boletin oficial del estado|\bboe\b", r):
        return "BOE"
    if re.search(r"boletin oficial de la junta de andalucia|\bboja\b", r):
        return "BOJA"
    if re.search(r"boletin oficial de la provincia|\bbop\b", r):
        return "BOP"
    if re.search(r"publicacion|siguiente", r):
        return "?"
    return None


RE_PLAZO_B = re.compile(
    r"(?:solicitudes|instancias)[^.]{0,140}?(?:en\s+el|dentro\s+del|un|de\s+un)\s+plazo\s+(?:maximo\s+|improrrogable\s+)?(?:de\s+)?"
    r"(?P<num>\b[a-z]+\b|\d+)\s*(?:\(\d+\)\s*)?(?P<tipo>dias?\s+habiles|dias?\s+naturales|dias?|mes(?:es)?)\b"
    r"(?P<resto>.{0,260})",
    re.S,
)


RE_PLAZO_C = re.compile(
    r"(?:presentar(?:se|an|lo|la|las|los)?|presentaran|deberan presentarse|podran presentarse)\s+(?:en|dentro\s+de)l?\s+(?:el\s+)?"
    r"plazo\s+(?:maximo\s+|improrrogable\s+)?(?:de\s+)?(?P<num>\b[a-z]+\b|\d+)\s*(?:\(\d+\)\s*)?"
    r"(?P<tipo>dias?\s+habiles|dias?\s+naturales|dias?|mes(?:es)?)\b(?P<resto>.{0,260})",
    re.S,
)


def extrae_plazo(texto):
    """Devuelve dict {n, tipo, boletin, frase} o None."""
    t = norm(texto)
    for m in list(RE_PLAZO.finditer(t)) + list(RE_PLAZO_B.finditer(t)) + list(RE_PLAZO_C.finditer(t)):
        n = a_numero(m.group("num"))
        if not n or n > 90:
            continue
        tipo_txt = m.group("tipo")
        if tipo_txt.startswith("mes"):
            tipo = "meses"
        elif "natural" in tipo_txt:
            tipo = "naturales"
        elif "habil" in tipo_txt:
            tipo = "habiles"
        else:
            tipo = "habiles"  # "x días" sin calificar: en procedimiento administrativo son hábiles
        boletin = _boletin_de(m.group("resto"))
        frase = limpia(texto_original_aprox(texto, m.group(0)))
        corte = re.search(r"(?<=[a-zA-Z»\)])\.\s", frase[40:])
        if corte:
            frase = frase[: 40 + corte.start() + 1]
        return {"n": n, "tipo": tipo, "boletin": boletin, "frase": frase[:400]}
    return None


def texto_original_aprox(texto, fragmento_norm):
    """Recupera el fragmento del texto original (con tildes) correspondiente."""
    t = norm(texto)
    i = t.find(fragmento_norm[:60])
    if i < 0:
        return fragmento_norm[:300]
    # norm() conserva longitudes salvo espacios; aproximamos por palabras
    palabras = texto.split()
    tn = norm(" ".join(palabras))
    j = tn.find(fragmento_norm[:60])
    if j < 0:
        return fragmento_norm[:300]
    # cuenta palabras hasta j
    k = len(tn[:j].split())
    n = len(fragmento_norm.split())
    return " ".join(palabras[k:k + min(n, 70)])


# ---------------------------------------------------------------------------
# Grupo / subgrupo
# ---------------------------------------------------------------------------

def grupo_por_titulacion(tit):
    nt = norm(tit or "")
    if not nt:
        return None
    if re.search(r"certificado de escolaridad|sin titulacion|no se exige (?:ninguna )?titulacion", nt):
        return "AP"
    if re.search(r"tecnico superior|tecnico especialista", nt) and not re.search(r"grado|licenciad|diplomad|bachiller", nt):
        return "B"
    if re.search(r"bachiller", nt) and not re.search(r"grado|licenciad|diplomad|ingenier|arquitect", nt):
        return "C1"
    if re.search(r"graduado en educacion secundaria|graduado escolar|\beso\b|educacion secundaria obligatoria|"
                 r"formacion profesional de primer grado|tecnico auxiliar", nt) and not re.search(r"bachiller|grado en |licenciad", nt):
        return "C2"
    return None


def extrae_grupo(texto, titulo=""):
    """Intenta deducir el grupo (A1, A2, B, C1, C2, AP) de la plaza."""
    tn = norm(titulo)
    t = norm(texto)
    pat_sub = r"subgrupo\s*(?:de\s+clasificacion\s*)?(?:profesional\s*)?[:\-]?\s*(a1|a2|c1|c2)\b"
    pat_gr = r"grupo\s*(?:de\s+clasificacion\s*)?(?:profesional\s*)?[:\-]?\s*(?:[abc]\s*[,\-]?\s*(?:subgrupo\s*)?)?(a1|a2|c1|c2)\b"
    pat_esc = r"grupo\s+de\s+escala\s+de\s+clasificacion\s+profesional\s*:\s*(a1|a2|c1|c2)\b"
    # 1) el título
    for p in (pat_sub, pat_gr):
        vals = {v.upper() for v in re.findall(p, tn)}
        if len(vals) == 1:
            return vals.pop()
    # 2) "el Cuerpo/la Escala ... Subgrupo X" (en singular: el plural suele ser un requisito de promoción interna)
    m = re.search(pat_esc, t) or re.search(
        r"\b(?:el|la|del|al)\s+(?:cuerpo|escala|subescala|categoria)\b(?![^.;]{0,40}\b(?:o|y)\s+(?:escalas|cuerpos)\b)[^.;]{0,150}?"
        r"(?:subgrupo|grupo)\s*(?:[abc]\s*,?\s*subgrupo\s*)?(a1|a2|c1|c2)\b", t[:8000])
    if m:
        return m.group(1).upper()
    # 3) la titulación exigida (cuando es inequívoca)
    g = grupo_por_titulacion(extrae_titulacion(texto))
    if g:
        return g
    # 3b) promoción interna "desde los cuerpos o escalas del subgrupo X": la plaza es del nivel inmediato superior
    if "promocion interna" in t:
        m = re.search(r"de\s+los\s+cuerpos\s+o\s+escalas[^.;]{0,140}?(?:del|de\s+los)\s+(?:grupo\s+b\s+y\s+del\s+)?subgrupos?\s*(a2|c1|c2)\b", t)
        if m:
            return {"c2": "C1", "c1": "A2", "a2": "A1"}[m.group(1)]
    # 4) zona específica (anexos) o texto completo, si solo aparece un valor
    especifico = None
    for marca in ("bases especificas", "anexo i", "anexo 1", "anexos"):
        i = t.rfind(marca)
        if i > len(t) * 0.5:
            especifico = t[i:]
            break
    for zona in filter(None, (especifico, t)):
        for p in (pat_sub, pat_gr):
            vals = {v.upper() for v in re.findall(p, zona)}
            if len(vals) == 1:
                return vals.pop()
    cab = tn + " " + t[:3000]
    if re.search(r"agrupacion(?:es)? profesional", cab):
        return "AP"
    if re.search(r"subescala\s+tecnica|cuerpo superior|escala superior|tecnico de administracion general|\btag\b", cab):
        return "A1"
    if re.search(r"subescala\s+de\s+gestion|cuerpo de gestion|tecnico de gestion", cab):
        return "A2"
    if re.search(r"subescala\s+administrativa|administrativo de administracion general|cuerpo general administrativo", cab):
        return "C1"
    if re.search(r"subescala\s+auxiliar|auxiliar administrativo|cuerpo general auxiliar", cab):
        return "C2"
    if re.search(r"subescala\s+subalterna|ordenanza|conserje|\bpeon", cab):
        return "AP"
    if re.search(r"\b(?:cuerpo|escala)\b", tn):
        if re.search(r"ingenieros? tecnicos?|arquitectos? tecnicos?|cuerpo de gestion|escala de gestion|diplomados?|enfermer|"
                     r"ayudantes? de (?:archivos|investigacion)|tecnicos? de hacienda|gestion procesal", tn):
            return "A2"
        if re.search(r"cuerpo (?:superior|facultativo|especial facultativo)|escala superior|ingenieros?\b|arquitectos?\b|letrad|"
                     r"inspectores?|abogados del estado|carrera (?:judicial|fiscal|diplomatica)|notari|registrador", tn):
            return "A1"
        if re.search(r"tramitacion procesal|cuerpo general administrativo|agentes? de la hacienda", tn):
            return "C1"
        if re.search(r"auxilio judicial|cuerpo general auxiliar", tn):
            return "C2"
    return None


# ---------------------------------------------------------------------------
# Titulación y requisitos
# ---------------------------------------------------------------------------

def extrae_titulacion(texto):
    t = texto or ""
    m = re.search(r"TITULACI[ÓO]N\s+(?:EXIGIDA|REQUERIDA|ACAD[ÉE]MICA)\s*[:\s]\s*(.{6,240}?)\s*(?:\n\s*\n|\n[A-ZÁÉÍÓÚ ]{5,}(?:\s|$)|$)", t, re.S)
    if m and not re.match(r"(?i)(que se|exigid|requerid|correspondiente|necesari|de las plazas)", m.group(1)):
        return limpia(m.group(1))
    m = re.search(r"(?i)titulaci[óo]n\s*(?:exigida|requerida|acad[ée]mica)?\s*:\s*([^\n]{6,220})", t)
    if m and not re.match(r"(?i)(que se|exigid|requerid|correspondiente|necesari|la exigida|las exigidas|ver )", m.group(1).strip()):
        return limpia(m.group(1))
    m = re.search(r"(?i)(?:estar\s+en\s+posesi[óo]n|poseer)\s+(?:o\s+en\s+condiciones\s+de\s+obtener\s+)?(?:el\s+|la\s+|del?\s+|los\s+)?"
                  r"(?:t[íi]tulos?|titulaci[óo]n)\s+(?:oficial\s+)?(?:universitari[oa]\s+)?(?:de\s+|del\s+)?([^.;\n]{4,200})", t)
    if m:
        frase = limpia(m.group(1))
        frase = re.split(r"(?i)\s+(?:a|en)\s+la\s+fecha|\s+antes\s+de\s+(?:la|que)|,?\s+o\s+en\s+condiciones|\s+expedid|,\s+referid", frase)[0]
        if len(frase) > 3 and not re.match(r"(?i)(que se|exigid|requerid|correspondiente|necesari|acad[ée]mica (?:exigid|requerid)|la plaza|en los anexos|indicad)", frase):
            return frase
    return None


ORDINALES = ["primera", "segunda", "tercera", "cuarta", "quinta", "sexta", "septima", "octava", "novena",
             "decima", "undecima", "duodecima", "decimotercera", "decimocuarta", "decimoquinta"]
RE_CAB_REQ = re.compile(
    r"(?im)^[ \t]*(?P<pref>(?:base\s+)?(?P<num>\d{1,2})(?:[\.\)]|\.-|\s*[\-–—])?\s*|(?P<rom>[IVX]{1,5})[\.\)\-–—]+\s*|"
    r"(?:base\s+)?(?P<ord>primera|segunda|tercera|cuarta|quinta|sexta|s[ée]ptima|octava|novena)\s*[\.\-–—:]+\s*)?"
    r"requisitos\s+(?:generales\s+)?(?:de\s+(?:los|las)\s+(?:personas\s+)?(?:aspirantes|candidat[oa]s|participantes|solicitantes)|"
    r"que\s+(?:deben|han\s+de|deber[áa]n)\s+reunir|de\s+participaci[óo]n|de\s+admisi[óo]n|exigidos|para\s+(?:participar|ser\s+admitid)|"
    r"de\s+acceso|generales)"
)
ROMANOS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def _fin_apartado(m, resto):
    """Posición donde empieza el siguiente apartado del mismo nivel."""
    if m.group("num"):
        n = int(m.group("num")) + 1
        pat = rf"(?im)^[ \t]*(?:base\s+)?{n}(?:[\.\)](?!\d)|\.-|\s*[\-–—])\s*\S"
    elif m.group("rom") and m.group("rom") in ROMANOS[:-1]:
        sig = ROMANOS[ROMANOS.index(m.group("rom")) + 1]
        pat = rf"(?m)^[ \t]*{sig}[\.\)\-–—]+\s*\S"
    elif m.group("ord"):
        pat = r"(?im)^[ \t]*(?:base\s+)?(?:" + "|".join(ORDINALES) + r"|s[ée]ptima|d[ée]cima|und[ée]cima)\s*[\.\-–—:]+\s*\S"
    else:
        pat = (r"(?im)^[ \t]*(?:\d{1,2}[\.\)]\s+[A-ZÁÉÍÓÚ][^\n]{2,70}$|(?:presentaci[óo]n\s+de\s+)?solicitudes\b|"
               r"forma\s+y\s+plazo|plazo\s+de\s+presentaci[óo]n|admisi[óo]n\s+de\s+(?:aspirantes|solicitudes))")
    for s in re.finditer(pat, norm_mayus(resto)):
        if s.start() > 30:
            return s.start()
    return None


def norm_mayus(t):
    """Quita tildes conservando longitudes (para buscar ordinales sin tilde)."""
    return "".join(c for c in unicodedata.normalize("NFC", t)).translate(str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU"))


def extrae_requisitos(texto, maximo=4000):
    """Copia literal del apartado de requisitos de las bases (si se localiza)."""
    if not texto:
        return None
    m = RE_CAB_REQ.search(texto)
    if not m:
        return None
    resto = texto[m.end():]
    fin = _fin_apartado(m, resto)
    largo = fin if fin is not None else maximo + 500
    bloque = limpia_bloque(texto[m.start(): m.end() + min(largo, maximo + 500)])
    if len(bloque) > maximo:
        bloque = bloque[:maximo].rsplit(" ", 1)[0] + " […]"
    return bloque if len(bloque) > 60 else None


RUIDO = re.compile(
    r"(?i)^(p[áa]gina \d+|cve:|c[óo]d\. validaci[óo]n|verificaci[óo]n:|documento firmado|hash:|fecha firma|n[ºo°] \d+ ?- ?(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado)|plaza del duque|el secretario|bolet[íi]n oficial de la provincia de sevilla\.|\d+\s*$|https?://\S+$)"
)


def limpia_bloque(b):
    lineas = []
    for l in b.split("\n"):
        l = l.strip()
        if not l or RUIDO.search(l):
            continue
        lineas.append(l)
    # une líneas cortadas (párrafos) y mantiene saltos ante enumeraciones
    out = ""
    for l in lineas:
        if re.match(r"^([a-zñ]\)|\d+[\.\)]|[\-–•●·]|\d+\.\d+)", l) or out.endswith((".", ":", ";")):
            out += "\n" + l
        else:
            out += " " + l
    return out.strip()


# ---------------------------------------------------------------------------
# Otros datos de la plaza
# ---------------------------------------------------------------------------

def extrae_num_plazas(*textos):
    for tx in textos:
        for m in re.finditer(r"\b(\d{1,4}|[a-z]+)\s+(?:\(\d+\)\s+)?plazas?\b", norm(tx)):
            n = a_numero(m.group(1))
            if n:
                return n
    return None


def extrae_personal(t):
    n = norm(t)
    tipos = []
    if re.search(r"funcionari", n):
        tipos.append("Funcionario")
    if re.search(r"laboral", n):
        tipos.append("Laboral")
    if re.search(r"estatutari", n):
        tipos.append("Estatutario")
    if len(tipos) == 1:
        return tipos[0]
    if tipos:
        return " / ".join(tipos)
    return None


def extrae_sistema(t):
    n = norm(t)
    if re.search(r"concurso[\s\-–]*oposicion", n):
        return "Concurso-oposición"
    if re.search(r"\boposicion\b", n):
        return "Oposición"
    if re.search(r"libre designacion", n):
        return "Libre designación"
    if re.search(r"\bconcurso\b", n):
        return "Concurso"
    return None


EXCLUIR = re.compile(
    r"correccion de errores|corrigen errores|correccion de error|relacion (?:definitiva|provisional)|"
    r"admitid|excluid|personas aprobadas|aspirantes aprobad|relacion de aprobad|tribunal calificador|"
    r"composicion del tribunal|nombra(?:n|r)? (?:el|al|a los|a las|personal|funcionari)|\bnombramientos?\b|"
    r"se modifica|^modificacion|modificacion de las bases|se amplia|ampliacion del plazo|"
    r"prorroga|resolucion definitiva|se resuelve|adjudica|sorteo|"
    r"relacion de (?:personas|aspirantes|plazas)|plazas correspondientes|se ofrecen|\blistas? (?:provisional|definitiva|de)|\blistado|"
    r"calificacion|fecha de (?:celebracion|realizacion|comienzo)|\bcese\b|toma de posesion|han superado|"
    r"desiert|deja sin efecto|se anula|composicion|subvencion|\bbecas?\b|\bayudas?\b|premio|"
    r"mercadillo|agrupaciones carnavalescas|certificado de aptitud|licitacion|"
    r"^(?:aprobacion de la )?oferta de empleo publico|(?:aprueba|publica) la oferta de empleo publico|"
    r"(?:aprobacion|modificacion)\s+(?:inicial\s+|definitiva\s+)?(?:de\s+)?(?:la\s+)?(?:modificacion\s+de\s+)?(?:la\s+)?plantilla|"
    r"relacion de puestos|valoracion de (?:los )?puestos|manual de puestos|cuenta general|presupuest|"
    r"ordenanza|padron|desistimiento|calendario|se aprueba la relacion|se publica la relacion|"
    r"se hace publica la relacion|personal eventual|personal directivo profesional|"
    r"medidas excepcionales|carrera profesional|instruccion|delegacion de competencias|"
    r"designacion del tribunal|tribunal (?:que ha de juzgar|calificador)|puntuacion|impugnacion|"
    r"plantillas? (?:de respuestas|correctoras?)|respuestas|orden de actuacion|celebracion del sorteo|"
    r"se aplaza|aplazamiento|lugar,? fecha|relacion de seleccionad|propuesta de nombramiento|bases reguladoras|"
    r"bases generales (?:que regiran|para) (?:las|los) (?:convocatorias|procesos)"
)
RE_CONVOCA_ESTRICTO = re.compile(r"\bconvoca(?:n)?\b|\bconvocatorias?\b")
RE_CONVOCA = re.compile(r"\bconvoca(?:n)?\b|\bconvocatorias?\b|\bse convocan?\b|\bbases\b|proceso selectivo|pruebas selectivas|\bbolsas?\b")


RE_PROVISION = re.compile(
    r"libre designacion|concurso de traslado|provision de puestos de trabajo|provision del puesto|"
    r"proveer puestos? de trabajo|concurso (?:general|especifico) de meritos|concurso (?:general|especifico)\b|"
    r"cargo intermedio|puesto directivo|cubrir puestos? de trabajo|puestos? de trabajo (?:vacantes? )?(?:por|mediante)|"
    r"movilidad (?:interadministrativa|horizontal|voluntaria)|por movilidad|comision de servicios|concurso unitario|"
    r"concurso ordinario|personal funcionario\. concursos|reingreso|convoca(?:n)? (?:la |una |las )?vacantes?\b|"
    r"anuncia(?:n)? (?:la |una )?vacantes?\b|provision de (?:la|una) vacante|concurso (?:de meritos )?para la provision de|"
    r"provision de puestos? de trabajo|concurso de meritos para la cobertura de (?:un|el|los) puestos?|jefe/a de (?:equipo|seccion|servicio|bloque|grupo)")


def es_provision(t):
    return bool(RE_PROVISION.search(norm(t)))


def tipo_proceso(t):
    n = norm(t)
    if RE_PROVISION.search(n):
        return "Provisión de puestos"
    if re.search(r"\bbolsas?\b|lista de reserva|listas? de empleo|contratacion temporal|seleccion de personal temporal|"
                 r"personal funcionario interino|nombramiento interino|interino", n):
        return "Bolsa de empleo"
    if "promocion interna" in n and not re.search(r"turno libre|acceso libre|sistema general de acceso libre", n):
        return "Promoción interna"
    return "Acceso libre"


PROVINCIAS_NO_SEVILLA = [
    "almeria", "cadiz", "cordoba", "granada", "huelva", "jaen", "malaga",
    "albacete", "alicante", "asturias", "avila", "badajoz", "barcelona", "bizkaia", "vizcaya",
    "burgos", "caceres", "cantabria", "castellon", "ciudad real", "cuenca", "girona", "gerona",
    "guadalajara", "gipuzkoa", "guipuzcoa", "huesca", "illes balears", "baleares", "la rioja", "las palmas",
    "leon", "lleida", "lerida", "lugo", "madrid", "murcia", "navarra", "ourense", "orense", "palencia",
    "pontevedra", "salamanca", "santa cruz de tenerife", "tenerife", "segovia", "soria", "tarragona",
    "teruel", "toledo", "valencia", "valladolid", "zamora", "zaragoza", "araba", "alava", "a coruna",
    "la coruna", "ceuta", "melilla", "santiago de compostela", "marbella", "jerez", "algeciras",
    "motril", "linares", "ubeda", "antequera", "ronda", "el ejido", "estepona",
]


RE_EXTRANJERO = re.compile(r"cooperacion espanola en|en el exterior|en el extranjero|embajada|consulado|oficina tecnica de cooperacion|"
                           r"instituto cervantes (?:de|en)|representacion permanente|oficina economica y comercial")


def en_el_extranjero(t):
    return bool(RE_EXTRANJERO.search(norm(t)))


def lugares_no_sevilla(t):
    n = norm(t)
    return sorted({p for p in PROVINCIAS_NO_SEVILLA if re.search(r"\b" + p + r"\b", n)})


def menciona_sevilla(t):
    return bool(re.search(r"\bsevilla\b", norm(t)))


def denominacion_de_titulo(titulo, preferir_cuerpo=False):
    d = _denominacion(titulo, preferir_cuerpo)
    return (d[0].upper() + d[1:]) if d else d


def _denominacion(titulo, preferir_cuerpo=False):
    """Saca el nombre de la plaza de un título tipo '... de una plaza de X, vacante ...'."""
    t = limpia(titulo)
    cuerpo = re.search(r"(?i)\b(?:en\s+el|en\s+la|en\s+los|en\s+las|al|a\s+la|a\s+las|a\s+los|de\s+la|del)\s+"
                       r"((?:Cuerpos?|Escalas?|Subescalas?|Categor[íi]as?|grupo profesional|especialidad)\b[^;]{3,160}?)"
                       r"(?:,\s*(?:por|mediante|en\s+el|del\s+Ministerio|convocad|correspondiente)|\s+por\s+el\s+sistema|\.$|$)", t)
    if preferir_cuerpo and cuerpo:
        return cuerpo.group(1).strip(" ,.")
    m = re.search(r"(?i)\b(?:plazas?|puestos?)\s+de\s+(.+?)(?:,\s*(?:vacantes?|incluid|perteneciente|en\s+r[ée]gimen|por\s+el\s+sistema|mediante|del?\s+la\s+plantilla|de\s+personal)|\s+\(OEP|\s+vacantes?\s+en|\s+de\s+la\s+plantilla|\s+por\s+(?:el\s+sistema|turno|promoci)|\s+mediante|\s+en\s+turno|\s+para\s+(?:la|el|los|las)\s|,\s*correspondiente|\.$|$)", t)
    if m:
        d = m.group(1).strip(" ,.")
        if 3 < len(d) < 160 and not re.match(r"(?i)trabajo\b", d):
            return d[0].upper() + d[1:]
    if cuerpo:
        return cuerpo.group(1).strip(" ,.")
    m = re.search(r"(?i)cobertura\s+(?:de|del)\s+(?:(?:un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|\d+)\s+)?(?:puestos?|plazas?)\s+(?:de\s+)?(.+?)(?:,|\s+para\s+la|\s+en\s+(?:la|el)\s+|\.$|$)", t)
    if m and 3 < len(m.group(1)) < 160:
        return m.group(1).strip(" ,.")
    m = re.search(r"(?i)acceso\s+a\s+la\s+condici[óo]n\s+de\s+(personal\s+(?:laboral|funcionario|estatutario)\s+fijo[^.;]{0,140}?)(?:,\s*(?:correspondiente|convocad)|\.$|$)", t)
    if m:
        return m.group(1).strip(" ,.")
    m = re.search(r"(?i)convoca(?:n)?\s+a?\s*concurso\s+p[úu]blico\s+(.+?)(?:\s+para\s+(?:la|el)|,|\.$|$)", t)
    if m and 3 < len(m.group(1)) < 160:
        return m.group(1).strip(" ,.")
    m = re.search(r"(?i)como\s+personal\s+laboral\s+(?:fijo|temporal)?,?\s*(?:en\s+|con\s+la\s+categor[íi]a\s+de\s+)?([^,.;]{4,140})", t)
    if m:
        return "Personal laboral: " + m.group(1).strip(" ,.")
    m = re.search(r"(?i)bolsa\s+(?:de\s+(?:empleo|trabajo)\s+)?(?:de|para)\s+(.{3,120}?)(?:,|\.|$)", t)
    if m:
        return "Bolsa de " + m.group(1).strip(" ,.")
    return None


MENORES = {"de", "del", "la", "las", "los", "el", "y", "e", "para", "con", "en", "a", "al", "por"}


def titulo_bonito(s):
    palabras = (s or "").lower().split()
    return " ".join(p if (i and p in MENORES) else p[:1].upper() + p[1:] for i, p in enumerate(palabras))


def organismo_estado(org_titulo, departamento):
    dep = titulo_bonito(departamento)
    o = org_titulo or ""
    m = re.match(r"(?i)^(?:presidencia|direcci[óo]n(?:\s+general)?|secretar[íi]a\s+general|gerencia|subdirecci[óo]n general)\s+"
                 r"(?:de\s+la|del|de\s+los|de)\s+((?:Agencia|Instituto|Consejo|Autoridad|Organismo|Comisi[óo]n|Centro|Entidad|Servicio|"
                 r"Mutualidad|Fondo|Consorcio|Museo|Biblioteca|Delegaci[óo]n|Parque|Real|Oficina|Tesorer[íi]a|Intervenci[óo]n)\b.*)", o)
    if m:
        return m.group(1)
    if not o or re.match(r"(?i)^(subsecretar[íi]a|secretar[íi]a de estado|direcci[óo]n general|letrado mayor|comisi[óo]n permanente|"
                         r"presidencia de las cortes|mesa)", o):
        return dep or o
    return o


RE_ENTIDAD = (r"(?:Servicio Andaluz de Salud|Agencia|Instituto|Fundaci[óo]n|Empresa|Universidad|Consorcio|Ente|Escuela|Patronato|"
              r"Centro|Parlamento|C[áa]mara|Defensor|Consejo|Sociedad|Organismo)")


def simplifica_organismo(o, seccion=""):
    o = limpia(o)
    m = re.match(r"(?i)^(?:direcci[óo]n(?:\s+general)?(?:\s+gerencia)?(?:\s+de\s+[^,]+?)?|presidencia|gerencia|viceconsejer[íi]a|"
                 r"secretar[íi]a\s+general(?:\s+de\s+[^,]+?)?|rectorado|comisi[óo]n ejecutiva|consejo de administraci[óo]n)\s+"
                 r"(?:de\s+la|del|de\s+los|de)\s+(" + RE_ENTIDAD + r"\b.*)", o)
    if m:
        return m.group(1)
    if re.match(r"(?i)^(direcci[óo]n general|viceconsejer[íi]a|secretar[íi]a general|delegaci[óo]n territorial)", o) and seccion:
        return "Junta de Andalucía · " + seccion
    return o
