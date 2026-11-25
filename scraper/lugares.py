"""Dónde están las plazas según el texto de las bases.

Busca los lugares solo donde las bases hablan del puesto ("en la localidad de…", "Centro de trabajo:",
"Ubicación:", tablas de provincia y destino…) y en el título, y los compara con la lista oficial de
municipios de Andalucía (INE) y las provincias y capitales de España. Los códigos postales también cuentan
(41xxx = provincia de Sevilla).
"""
import csv
import os
import re

from textos import norm

PROVINCIAS = {
    "01": "Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila", "06": "Badajoz",
    "07": "Illes Balears", "08": "Barcelona", "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón",
    "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña", "16": "Cuenca", "17": "Girona", "18": "Granada",
    "19": "Guadalajara", "20": "Gipuzkoa", "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León",
    "25": "Lleida", "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia",
    "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra",
    "37": "Salamanca", "38": "Santa Cruz de Tenerife", "39": "Cantabria", "40": "Segovia", "41": "Sevilla",
    "42": "Soria", "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "Valencia", "47": "Valladolid",
    "48": "Bizkaia", "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}
SEVILLA = "41"

# Otros nombres de provincias y capitales que no coinciden con el de la provincia
OTROS_NOMBRES = {
    "araba": "01", "alava": "01", "vitoria": "01", "vitoria-gasteiz": "01", "alacant": "03", "baleares": "07",
    "islas baleares": "07", "palma de mallorca": "07", "castello": "12", "castellon de la plana": "12",
    "la coruna": "15", "a coruna": "15", "guipuzcoa": "20", "san sebastian": "20", "donostia": "20",
    "lerida": "25", "logrono": "26", "orense": "32", "oviedo": "33", "pamplona": "31", "iruna": "31",
    "santander": "39", "vizcaya": "48", "bilbao": "48", "gerona": "17", "las palmas de gran canaria": "35",
    "tenerife": "38", "valencia": "46", "valencia/valencia": "46",
}

# Municipios de fuera de Sevilla cuyo nombre es también una palabra corriente o un apellido: solo cuentan
# con la provincia al lado. (Los de Sevilla no se filtran: confundirse con ellos solo hace que la
# convocatoria se quede en la lista, que es el error menos grave.)
COMUNES = {
    "alameda", "ronda", "espera", "conquista", "espejo", "posadas", "niebla", "manzanilla", "cala", "galera",
    "salar", "darro", "dolar", "ferreira", "pedro martinez", "colmenar", "pizarra", "peligros", "jun", "fines",
    "vera", "felix", "rota", "torres", "arenas", "linares", "cazorla", "quesada", "arjona", "luque",
    "valenzuela", "montemayor", "belmez", "oria", "turre", "gor", "nevada", "algarrobo", "vinuela", "valor",
    "monda", "casares", "cabra", "lucena", "baza", "padul", "beas", "rioja", "santa fe", "campillos", "teba",
    "guaro", "cortes", "zahara", "jimena", "castellar", "martos", "alcala", "villanueva",
}
# Nombres cortos que se usan sin la parte completa
ALIAS = {"los palacios": "Los Palacios y Villafranca", "la linea": "La Línea de la Concepción",
         "el puerto de santa maria": "El Puerto de Santa María", "alcala de guadaira": "Alcalá de Guadaíra"}

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "municipios_andalucia.csv")


def _carga():
    nombres = {}   # nombre normalizado -> {(nombre bonito, provincia)}
    with open(_RUTA, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nombres.setdefault(norm(r["nombre"]), set()).add((r["nombre"], r["provincia"]))
    for cod, nombre in PROVINCIAS.items():   # provincias y capitales de toda España
        nombres.setdefault(norm(nombre), set()).add((nombre, cod))
    for alias, cod in OTROS_NOMBRES.items():
        nombres.setdefault(alias, set()).add((PROVINCIAS[cod], cod))
    por_nombre = {norm(n): (n, p) for vs in list(nombres.values()) for n, p in vs}
    for alias, completo in ALIAS.items():
        if norm(completo) in por_nombre:
            nombres.setdefault(alias, set()).add(por_nombre[norm(completo)])
    return nombres


NOMBRES = _carga()
_RE_NOMBRE = re.compile(r"(?<![\w-])(" + "|".join(sorted((re.escape(n) for n in NOMBRES), key=len, reverse=True)) + r")(?![\w-])")
_RE_PROV_AL_LADO = re.compile(r"^\s*(?:\(|,|-|–|/)\s*(" + "|".join(
    sorted((re.escape(norm(n)) for n in list(PROVINCIAS.values()) + list(OTROS_NOMBRES)), key=len, reverse=True)) + r")\b")
_RE_CP = re.compile(r"(?<![\d.,])(0[1-9]|[1-4]\d|5[0-2])\d{3}(?![\d.,]*\d)")

# Dónde hablan las bases del puesto (sobre el texto normalizado)
PISTAS = [
    (r"en la localidad de|en el municipio de|en el termino municipal de|con destino en|"
     r"(?:plazas?|puestos?) (?:ubicad|situad|radicad)[oa]s? en|para la sede|para el centro|adscrit[oa] al centro", 110),
    (r"centro de trabajo|lugar de (?:trabajo|destino|prestacion(?: de(?:l)? servicios?)?|realizacion del trabajo)|"
     r"centro de destino|sede de trabajo|ubicacion(?: del puesto)?|localizacion|localidad(?: de destino)?|"
     r"municipio(?: de destino)?|centro ifapa|ifapa centro", 170),
    (r"provincia ?/ ?destino|provincia ?/ ?localidad|localidad ?/ ?provincia|provincia y localidad|"
     r"centro de trabajo ?/ ?localidad|provincia de destino", 800),
]
_RE_PISTA = re.compile("|".join(f"(?P<p{i}>{p})" for i, (p, _) in enumerate(PISTAS)))
# Si justo antes aparece esto, la pista no habla del puesto (formularios, tribunal, examen, registro…)
_RE_NO_ES_PUESTO = re.compile(r"domicilio|datos personales|solicitante|tribunal|examen|ejercicio|pruebas?\b|registro|"
                              r"notificaci|comunicaci|presentacion de (?:la )?(?:solicitud|documentaci)|nacimiento|"
                              r"expedicion|residencia del|empadronad")
# Fin del trozo de un campo ("Centro de trabajo: … Modalidad de contrato: …")
_RE_FIN_CAMPO = re.compile(r"\b(?:modalidad|tipo de contrato|jornada|horario|retribuci|periodo de prueba|duracion|"
                           r"grupo profesional|categoria|funciones|requisitos|titulacion|numero de (?:plazas|puestos)|"
                           r"salario|incorporacion|caracteristicas)\b")


_ABREV = {"num", "no", "n", "avda", "avd", "av", "c", "edif", "ctra", "pl", "pza", "s", "sr", "sra", "d", "dna",
          "km", "art", "apdo", "tfno", "tlf", "c.p", "cp", "bda", "urb", "pol", "ind", "nave", "1", "2", "3"}


def _hasta_fin_de_frase(trozo):
    """Corta en el primer punto y seguido que no sea de una abreviatura ("núm. 40", "Avda. …")."""
    for m in re.finditer(r"([\w./]*)\.\s+(?=[a-z])", trozo):
        if m.group(1).lower().strip(".") not in _ABREV and len(m.group(1)) > 1:
            return trozo[: m.end(1) + 1]
    return trozo


def _nombres_en(trozo):
    """[(nombre bonito, provincia)] de los lugares que aparecen en un trozo de texto normalizado."""
    hallados = []
    for m in _RE_NOMBRE.finditer(trozo):
        clave = m.group(1)
        opciones = NOMBRES[clave]
        prov_al_lado = _RE_PROV_AL_LADO.match(trozo[m.end(): m.end() + 40])
        cod_al_lado = None
        if prov_al_lado:
            p = prov_al_lado.group(1)
            cod_al_lado = next((c for c, n in PROVINCIAS.items() if norm(n) == p), OTROS_NOMBRES.get(p))
        if cod_al_lado:
            opciones = {o for o in opciones if o[1] == cod_al_lado} or opciones
        elif clave in COMUNES and not any(norm(o[0]) == norm(PROVINCIAS[o[1]]) for o in opciones):
            continue   # palabra corriente sin provincia al lado: no se puede asegurar que sea un lugar
        if len({o[1] for o in opciones}) != 1:
            continue   # el mismo nombre existe en varias provincias
        hallados.append(sorted(opciones)[0])
    for m in _RE_CP.finditer(trozo):
        hallados.append((None, m.group(1)))
    return hallados


def extrae_lugares(texto, titulo=""):
    """Lugares donde las bases sitúan las plazas: lista de (nombre o None, código de provincia)."""
    t = norm(texto)
    hallados = list(_nombres_en(norm(titulo))) if titulo else []
    for m in _RE_PISTA.finditer(t):
        antes = t[max(0, m.start() - 70): m.start()]
        if _RE_NO_ES_PUESTO.search(antes):
            continue
        largo = next(PISTAS[i][1] for i in range(len(PISTAS)) if m.group(f"p{i}"))
        trozo = t[m.end(): m.end() + largo]
        if m.group("p0"):
            trozo = _hasta_fin_de_frase(trozo)
        elif m.group("p1"):
            fin = _RE_FIN_CAMPO.search(trozo)
            if fin and fin.start() > 3:
                trozo = trozo[: fin.start()]
        hallados += _nombres_en(trozo)
    vistos, unicos = set(), []
    for h in hallados:
        if h not in vistos:
            vistos.add(h)
            unicos.append(h)
    return unicos


def _lista(xs):
    xs = list(dict.fromkeys(xs))
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1]


def resume(lugares):
    """('sevilla' | 'fuera' | None, localidad para el filtro, texto para la ficha)."""
    if not lugares:
        return None, None, None
    sev = [n for n, p in lugares if p == SEVILLA]
    otros = [(n, p) for n, p in lugares if p != SEVILLA]
    provs_otras = [PROVINCIAS[p] for _, p in otros]
    if sev:
        munis = [n for n in sev if n and n != "Sevilla"] or ["Sevilla"]
        munis = list(dict.fromkeys(munis))
        localidad = munis[0] if len(munis) == 1 else "Varios municipios de Sevilla"
        provs_otras = list(dict.fromkeys(provs_otras))
        if len(provs_otras) >= 4:
            texto = _lista(munis) + f" · también hay plazas en otras {len(provs_otras)} provincias"
        else:
            texto = _lista(munis) + (f" · también hay plazas en {_lista(provs_otras)}" if otros else "")
        return "sevilla", localidad, texto
    partes = []
    for n, p in otros:
        prov = PROVINCIAS[p]
        partes.append(prov if not n or norm(n) == norm(prov) else f"{n} ({prov})")
    # si una provincia aparece sola y también con un municipio, basta el municipio
    partes = [x for x in dict.fromkeys(partes) if not any(y != x and y.endswith(f"({x})") for y in partes)]
    return "fuera", None, _lista(partes[:4])


# Organismos del Estado que solo tienen sede en Madrid (sus bases no suelen decir el lugar de trabajo)
SEDE_SOLO_MADRID = re.compile(
    r"autoridad independiente de responsabilidad fiscal|agencia estatal boletin oficial del estado|"
    r"consejo de seguridad nuclear|museo nacional centro de arte reina sofia|museo nacional del prado|"
    r"tribunal constitucional|consejo de estado\b|tribunal de cuentas|defensor del pueblo|"
    r"comision nacional del mercado de valores|agencia espanola de proteccion de datos|"
    r"agencia espanola de medicamentos|centro de investigaciones sociologicas|centro de estudios politicos y constitucionales|"
    r"congreso de los diputados|\bsenado\b|cortes generales|consejo general del poder judicial|junta electoral central|"
    r"consejo economico y social|biblioteca nacional de espana|museo thyssen")
