# -*- coding: utf-8 -*-
"""
De las respuestas de la encuesta a un plan de perfil, sin IA y explicable (MP7, EVOLUCION-MULTIPERFIL.md
§4.3 y §5.5).

    from perfilado.generador import generar_plan
    plan = generar_plan(respuestas)

El plan sale con el formato que leen `perfiles.config_efectiva` y `buscador.PuntuadorCatalogo` (MP2), y
es autosuficiente: una vez guardado en el perfil, la corrida no vuelve a mirar ni las respuestas ni el
catálogo. Cambiar algo en /ajustes es volver a llamar a esto.

LAS RESPUESTAS, con las claves del §3.1 (lo que guardará la encuesta de MP8):

    puestos            ["admin.administrativo", "hosteleria.otro", ...]   ids del catálogo, como mucho 5
    otros              ["recepcionista de spa"]       lo escrito en «✏️ Otro» del paso 2: un puesto más, con
                       su núcleo hecho de lo escrito (11-11; antes, palabra clave)
    sector_otro        "paisajismo"                   lo escrito en «✏️ Otro» del paso 1: palabra clave
    experiencia        "ninguna" | "menos_1" | "1_2" | "3_5" | "6_10" | "mas_10"
    experiencia_max_exigida   int o None (= sin límite); si falta, el sugerido del paso 3
    experiencia_estricta      bool; si falta, True (descartar es lo que sugiere la encuesta)
    practicas          bool: solo cuenta con «ninguna» o «menos_1», que es cuando se pregunta. Con False (o sin
                       preguntar) el plan DESCARTA prácticas, becas y programas de recién titulado (MP8)
    certificaciones    ["CCNA"]                       puntúan como palabras clave
    ubicacion          {"nombre", "lat", "lon", "radio_km"}; radio 0 = solo remoto, 60 = provincia
    modalidad          ["presencial", "hibrido", "remoto"]
    remoto_alcance     "es" | "ue" | "mundial" | None
    idiomas            ["es", "ca", "en", "fr", "de", "pt", "it"]
    ingles             "basico" | "intermedio" | "alto" | None
    jornada            ["completa", "parcial", "indefinido", "temporal", "autonomo"]
    salario_min        número o None
    claves_mas, claves_menos, empresas_excluidas      listas de texto
    umbral             opcional; 5,0 si falta

La formación (`formacion`) se guarda pero no cambia el plan: «Grado» o «FP» en una descripción no dicen
si la oferta encaja, y como palabra clave casarían con cualquier cosa. Tampoco `turnos_ok`: el §5.2 no
le da peso, y restar por «turnos» a quien no los acepta se decidirá con lo que diga el aprendizaje.
"""

from __future__ import annotations

import copy
import json
import math
import unicodedata
from pathlib import Path

RUTA_CATALOGO = Path(__file__).resolve().parent / "catalogo.json"

MAX_TERMINOS = 6          # D5: que nadie se coma el presupuesto de consultas
MAX_CLAVES_COMO_TERMINO = 2
MIN_LETRAS_CLAVE_COMO_TERMINO = 4
MAX_PUESTOS = 5           # §4.2, paso 2
UMBRAL = 5.0

# Años de la persona por respuesta. Lo que importa son los cortes del §5.2: ≤ 2 suma «junior», < 6
# filtra y resta el senior, ≥ 6 resta el junior. «1–2» va a 1 y «3–5» a 4 por lo mismo que el plan de
# ejemplo de MP2 (pruebas/fixtures/plan_administrativo_valencia.json).
ANOS = {"ninguna": 0, "menos_1": 0, "1_2": 1, "3_5": 4, "6_10": 6, "mas_10": 10}
# El tope sugerido del paso 3 (§4.2). None = sin límite.
TOPE_SUGERIDO = {"ninguna": 1, "menos_1": 1, "1_2": 3, "3_5": 5, "6_10": 10, "mas_10": None}
EXPERIENCIA_TEXTO = {"ninguna": "sin experiencia", "menos_1": "menos de 1 año", "1_2": "1–2 años",
                     "3_5": "3–5 años", "6_10": "6–10 años", "mas_10": "más de 10 años"}

# Lo que entiende `langdetect`, que es lo que usa el filtro de idioma de buscador.py.
IDIOMAS = {"es": "español", "ca": "catalán", "en": "inglés", "fr": "francés", "de": "alemán",
           "pt": "portugués", "it": "italiano"}
JORNADAS = ("completa", "parcial", "indefinido", "temporal", "autonomo")

# El feed de Tecnoempleo es el de la provincia de Barcelona (fuentes_extra/tecnoempleo.py): a quien
# vive lejos le traería ofertas que el filtro de área tira. 60 km desde Plaça Catalunya cubren el área
# metropolitana y las comarcas de alrededor; más allá ya no compensa una petición del feed.
CENTRO_BARCELONA = (41.3874, 2.1686)
RADIO_TECNOEMPLEO_KM = 60
FUENTES_SIN_FAMILIA = ("linkedin", "indeed", "infojobs", "eures", "jooble")

# QUIÉN NO ACEPTA PRÁCTICAS NO RECIBE PROGRAMAS DE PRÁCTICAS (MP8, 05-11). Hallazgo medido al empezar MP8: con
# `practicas` en falso el plan de catálogo solo RESTABA 1,5 a los títulos con «prácticas» o «becari[oa]»
# (PuntuadorCatalogo), y a un programa de recién titulado ni eso. Con el plan de una persona de
# ciberseguridad sin experiencia que no quiere prácticas: «FY27 | Junior | T&T | Ciberseguridad» daba 7,0
# (Encaje alto) y «Graduate Program Cybersecurity» 5,0. Son las mismas ofertas que el titular ocultó el
# 04-11 (CAMBIOS-MP6.md §7): eran las tres notas más altas de su semana, y un perfil de catálogo las volvía
# a sufrir. En vez de restar, se descartan: quien contesta «No» a «¿Te valen prácticas, becas o programas de
# recién titulado?» no las quiere, y una nota alta engaña más que una baja.
#
# El patrón es el de `titular-ciber` (`_PRACTICAS_Y_GRADUADOS`) más «becari[oa]», «internship» y
# «apprentice», que allí cubre otra regla. Va sin «trainee» a propósito: el puntuador de catálogo lo
# cuenta como junior (+2,0) y no es un programa. La excepción añade lo que en otros oficios es el puesto y
# no el programa: el instructor de prácticas de conducción, el tutor o formador de prácticas.
DESCARTE_DE_PRACTICAS = {
    "nombre": "Prácticas, becas y programas de recién titulado",
    "activa": True,
    "patron": (r"pr[áa]cticas|\bpracticum\b|\bbecari[oa]s?\b|\bbecas?\b|\bintern(?:ship)?s?\b|apprentice(?:ship)?"
               r"|working student|\bfy\d{2}\b|graduate (?:program|scheme)|programa de graduad[oa]s"
               r"|reci[ée]n (?:titulad|graduad)"),
    "excepcion": (r"buenas pr[áa]cticas|mejores pr[áa]cticas|best practices"
                  r"|(?:profesor|formador|instructor|tutor|monitor|docente)\w*(?:/a)?\s+(?:de\s+)?pr[áa]cticas"
                  r"|pr[áa]cticas\s+de\s+(?:conducci[óo]n|taller)"),
}

AMBITOS_REMOTOS = {
    "remoto_es": {"etiqueta": "Remoto España", "location": "Spain"},
    "remoto_ue": {"etiqueta": "Remoto Unión Europea", "location": "European Union"},
    "remoto_mundial": {"etiqueta": "Remoto resto del mundo", "location": ""},
}
# Qué ámbitos remotos abre cada alcance. La UE incluye España, como en la config del titular.
ALCANCES = {"es": ("remoto_es",), "ue": ("remoto_es", "remoto_ue"),
            "mundial": ("remoto_es", "remoto_ue", "remoto_mundial")}


def cargar_catalogo() -> dict:
    """
    El catálogo, leído del JSON cada vez. **Sin caché a propósito:** solo se usa al terminar la encuesta
    o al cambiar algo en /ajustes, unas pocas veces por semana, y guardarlo en memoria serían unos
    cientos de KB de objetos vivos para siempre en el proceso del bot, en un contenedor con la RAM justa.
    """
    return json.loads(RUTA_CATALOGO.read_text(encoding="utf-8"))


def _normal(texto) -> str:
    """Para comparar términos: sin acentos, sin mayúsculas, espacios simples. «Técnico» = «tecnico»."""
    texto = unicodedata.normalize("NFKD", str(texto or "").casefold())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).split())


def _km(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _indice(catalogo: dict) -> tuple[dict, dict]:
    """{id: puesto} y {id: familia} de todos los puestos, más los «otro» de cada familia."""
    puestos, familias = {}, {}
    for familia in catalogo["familias"]:
        for p in familia["puestos"]:
            puestos[p["id"]] = p
            familias[p["id"]] = familia
        familias[familia["otro"]["id"]] = familia
    return puestos, familias


def _choca(regla: dict, elegidos: list[str]) -> bool:
    """Si la regla haría daño a otro de los puestos elegidos (ver `choca_con` en fuente.py)."""
    for destino in regla.get("choca_con") or ():
        if any(e == destino or e.startswith(destino + ".") for e in elegidos):
            return True
    return False


def _sin_choca(regla: dict) -> dict:
    regla = copy.deepcopy(regla)
    regla.pop("choca_con", None)
    return regla


def _lista(valor) -> list:
    return [v for v in (valor or []) if str(v).strip()]


# LO ESCRITO EN «✏️ OTRO» ES UN PUESTO, NO UNA PALABRA CLAVE (11-11-2026, `REVISION-2026-11-11.md` §2).
#
# Hasta hoy lo que la persona escribía como puesto se guardaba como palabra clave: +1,5 si salía en el
# título. Con la ciudad son 2,5, y con «junior» encima, 4,5: **no podía llegar al listón de 5,0 nunca**.
# Pero sí se buscaba, y la ficha decía «Buscaré: …» con ello. Medido el 11-11 con los dos perfiles de la
# encuesta, que escribieron los dos un puesto que el catálogo no tiene: 7 ofertas con ese puesto en el
# título, nota máxima 2,5, ninguna avisada, y 6 de sus 32 consultas por vuelta gastadas en traerlas.
#
# Ahora cada «Otro» sale en el plan como un puesto más, con un núcleo hecho de lo escrito, y vale lo que
# vale el núcleo de cualquier puesto del catálogo (+4,0). No lleva señales, negativos ni descartes: no hay
# de dónde sacarlos. **Sigue además entre las palabras clave**, y es a propósito: en el título ya no suma
# su +1,5 (el puntuador lo sabe: sería contar dos veces lo mismo), pero conserva el +0,5 de «en la
# descripción», que en un perfil de catálogo puede ser lo único que distinga unas ofertas de las muchas
# que valen 5,0 clavado. Quitarlo habría sido arreglar una cosa rompiendo otra.
#
# El núcleo se hace con lo mínimo para que «Interculturales» encuentre «Mediador/a intercultural» y
# «logopeda» encuentre «Logopedas»: sin acentos, sin el plural y sin la vocal de género, y con
# todas las palabras de lo escrito en el título, en cualquier orden («auxiliar de vivero» casa con
# «Vivero - Auxiliar técnico»). Las regex se aplican al título CON acentos (ver fuente.py), así que
# cada vocal va con sus acentos.
#
# Lo que NO hace, a propósito: no decide si lo escrito es demasiado general. Quien escribe «técnico»
# recibirá todo lo que diga «técnico», que es lo que ha pedido; el tope diario y /ajustes son el freno, y
# una lista de palabras prohibidas habría que inventársela sin un solo caso delante.
_PALABRAS_VACIAS = frozenset(("de", "del", "la", "el", "los", "las", "en", "y", "al", "para", "por", "con",
                              "the", "of", "and", "for"))
_CON_ACENTOS = {"a": "[aáà]", "e": "[eéè]", "i": "[iíï]", "o": "[oóò]", "u": "[uúü]", "n": "[nñ]", "c": "[cç]"}
MIN_LETRAS_DE_UN_OTRO = 3


def _raiz(palabra: str) -> tuple[str, str]:
    """
    (raíz, lo que puede seguirle) de una palabra ya normalizada: se le quita la «s» del plural y la vocal
    final, y se deja que vuelvan. «logopeda» → logoped + (a|o|e)(s); «camareros» → camarer + lo
    mismo; «interculturales» → intercultural + (e)(s); «mediador» → mediador + (es|a|as|s). Las cortas se
    dejan como están («chef», «mozo», «spa», «redes»): quitarles letras las convierte en otra palabra.
    """
    if len(palabra) < 5 or not palabra.isalpha():
        return palabra, "s?" if len(palabra) > 2 and palabra.isalpha() else ""
    raiz = palabra
    if raiz.endswith("s") and not raiz.endswith("ss"):
        raiz = raiz[:-1]
    if raiz[-1] in "aoe" and len(raiz) >= 5:
        return raiz[:-1], "(?:[aoe]s?|@s?)?"   # camarero, camarera, camarer@s; dependiente y dependienta
    if raiz[-1] in "aeiou":
        return raiz, "s?"
    return raiz, "(?:es|as?|s)?"               # mediador, mediadora, mediadores


def nucleo_de_otro(texto) -> str | None:
    """
    La regex de título de un puesto escrito a mano, o None si no da para una (una letra suelta, «c++»):
    entonces se queda como palabra clave, que es lo que era.
    """
    normal = _normal(texto)
    palabras = [p for p in "".join(c if c.isalnum() else " " for c in normal).split()
                if len(p) > 1 and p not in _PALABRAS_VACIAS]
    palabras = list(dict.fromkeys(palabras))
    if not palabras or sum(len(p) for p in palabras) < MIN_LETRAS_DE_UN_OTRO:
        return None
    trozos = []
    for palabra in palabras:
        raiz, cola = _raiz(palabra)
        trozos.append(r"\b" + "".join(_CON_ACENTOS.get(c, c) for c in raiz) + cola + r"\b")
    if len(trozos) == 1:
        return trozos[0]
    return "^" + "".join("(?=.*%s)" % t for t in trozos)


def _candidatos(puesto: dict, junior: bool, en: bool, ca: bool) -> list[str]:
    """
    Los términos de un puesto, por orden de preferencia: el primero en castellano, su variante junior,
    el segundo en castellano, el primero en inglés y en catalán, y después el resto. Así, con varios
    puestos, cada uno pone primero lo que más trae, y el idioma solo entra si queda sitio.
    """
    t = puesto["terminos"]
    es = list(t.get("es") or [])
    ingles = list(t.get("en") or []) if en else []
    catalan = list(t.get("ca") or []) if ca else []
    jr = list(puesto.get("terminos_junior") or []) if junior else []
    return es[:1] + jr[:1] + es[1:2] + ingles[:1] + catalan[:1] + es[2:] + jr[1:] + ingles[1:] + catalan[1:]


def _elegir_terminos(puestos: list[dict], claves: list[str], junior: bool, en: bool,
                     ca: bool) -> list[str]:
    """
    Como mucho 6, sin repetidos. Primero, el primer término de cada puesto (que nadie se quede sin
    buscar); después, hasta 2 palabras clave suyas; después, en rondas, el resto de cada puesto.
    """
    elegidos, vistos = [], set()

    def anadir(termino) -> None:
        clave = _normal(termino)
        if clave and clave not in vistos and len(elegidos) < MAX_TERMINOS:
            vistos.add(clave)
            elegidos.append(str(termino).strip())

    colas = [_candidatos(p, junior, en, ca) for p in puestos]
    for cola in colas:
        if cola:
            anadir(cola.pop(0))
    for clave in claves[:MAX_CLAVES_COMO_TERMINO]:
        anadir(clave)
    while len(elegidos) < MAX_TERMINOS and any(colas):
        for cola in colas:
            if cola:
                anadir(cola.pop(0))
    return elegidos


def _ambitos(fuentes: list[str], ciudad: str, radio: float, modalidad: set, alcance) -> dict:
    ambitos = {}
    if radio > 0 and (modalidad & {"presencial", "hibrido"} or not modalidad):
        ambitos["local"] = {
            "etiqueta": "%s (hasta %g km)" % (ciudad, radio), "activo": True, "sitios": list(fuentes),
            "location": "%s, Spain" % ciudad, "usar_distancia": True, "is_remote": False,
            "usar_country_indeed": True,
        }
    if "remoto" in modalidad or radio <= 0:
        for clave in ALCANCES.get(alcance or "es", ALCANCES["es"]):
            # Fuera de España solo LinkedIn: Indeed necesita el país, y EURES, InfoJobs, Jooble y
            # Tecnoempleo son de aquí (o no tienen remoto, como Jooble).
            sitios = list(fuentes) if clave == "remoto_es" else ["linkedin"]
            ambitos[clave] = {
                "etiqueta": AMBITOS_REMOTOS[clave]["etiqueta"], "activo": True, "sitios": sitios,
                "location": AMBITOS_REMOTOS[clave]["location"], "usar_distancia": False,
                "is_remote": True, "usar_country_indeed": clave == "remoto_es",
            }
    return ambitos


def _explicacion(puestos, otros, exp, tope, estricta, ciudad, radio, modalidad, idiomas, ingles,
                 salario, terminos, consultas, avisos) -> dict:
    """La ficha del paso 13 (§4.2) y cuántas consultas añade. Es lo que verá la persona: en español."""
    nombres = [p["nombre"] for p in puestos] + ["«%s»" % o for o in otros]
    if tope is None:
        limite = "ofertas que pidan cualquier experiencia"
    else:
        limite = "ofertas que pidan hasta %d %s; las que pidan más, %s" % (
            tope, "año" if tope == 1 else "años", "fuera" if estricta else "más abajo")
    nombres = ", ".join(nombres) or "—"
    lineas = ["🗂️ %s · ⏳ %s (%s)" % (nombres[:1].upper() + nombres[1:], EXPERIENCIA_TEXTO.get(exp, exp), limite)]
    donde = "%s, solo remoto" % ciudad if radio <= 0 else "%s, hasta %g km" % (ciudad, radio)
    iconos = " ".join(i for m, i in (("presencial", "🏢"), ("hibrido", "🔀"), ("remoto", "🏠"))
                      if m in modalidad)
    habla = [IDIOMAS[c] + (" %s" % ingles if c == "en" and ingles else "") for c in idiomas if c in IDIOMAS]
    partes = ["📍 " + donde]
    if iconos:
        partes.append(iconos)
    partes.append("🗣️ " + ", ".join(habla))
    if salario:
        partes.append("💰 %s € mín." % format(int(salario), ",").replace(",", "."))
    lineas.append(" · ".join(partes))
    busqueda = "🔎 Buscaré: %s (%d)" % (", ".join("«%s»" % t for t in terminos), len(terminos))
    lineas.append(busqueda)
    lineas.append("Son %d consultas por vuelta a los portales." % consultas)
    lineas += avisos
    return {"ficha": "\n".join(lineas), "busqueda": busqueda, "consultas": consultas, "avisos": avisos}


def generar_plan(respuestas: dict, catalogo: dict | None = None) -> dict:
    """El plan de perfil (§5.5) de unas respuestas (§3.1). Una respuesta imposible es un ValueError."""
    catalogo = catalogo or cargar_catalogo()
    por_id, familia_de = _indice(catalogo)
    r = respuestas or {}

    ids = [i for i in _lista(r.get("puestos")) if i in por_id or i in familia_de]
    desconocidos = [i for i in _lista(r.get("puestos")) if i not in por_id and i not in familia_de]
    if desconocidos:
        raise ValueError("puestos que no están en el catálogo: %s" % ", ".join(desconocidos))
    ids = list(dict.fromkeys(ids))[:MAX_PUESTOS]
    otros = list(dict.fromkeys(str(o).strip() for o in _lista(r.get("otros"))))
    # El sector escrito a mano es un tema, no un puesto: sigue siendo palabra clave (y término de búsqueda).
    sector_otro = [str(r["sector_otro"]).strip()] if str(r.get("sector_otro") or "").strip() else []
    if not ids and not otros and not sector_otro:
        raise ValueError("sin puestos no hay nada que buscar")
    puestos = [por_id[i] for i in ids if i in por_id]

    exp = r.get("experiencia") or "ninguna"
    if exp not in ANOS:
        raise ValueError("experiencia desconocida: %r" % exp)
    anos = ANOS[exp]
    tope = r["experiencia_max_exigida"] if "experiencia_max_exigida" in r else TOPE_SUGERIDO[exp]
    estricta = bool(r.get("experiencia_estricta", True))

    ubic = r.get("ubicacion") or {}
    if not ubic.get("nombre") or ubic.get("lat") is None or ubic.get("lon") is None:
        raise ValueError("falta la ciudad (nombre, lat y lon)")
    ciudad = str(ubic["nombre"]).strip()
    modalidad = set(_lista(r.get("modalidad")))
    radio = float(ubic.get("radio_km") or 0)
    alcance = r.get("remoto_alcance")

    idiomas = [c for c in _lista(r.get("idiomas")) if c in IDIOMAS] or ["es"]
    ingles = r.get("ingles")
    # Con inglés básico no se trabaja en inglés: ni se busca en inglés ni pasan las ofertas escritas en
    # inglés, que dan por hecho que se habla. Para eso pregunta el nivel el paso 8.
    if "en" in idiomas and ingles == "basico":
        idiomas.remove("en")
    habla_en = "en" in idiomas
    habla_ca = "ca" in idiomas

    # Lo escrito como puesto es un puesto (ver `nucleo_de_otro`), y lo que no da para un núcleo se queda
    # solo de palabra clave, como antes. Las palabras clave son las de siempre y en el orden de siempre: lo
    # escrito (sector y puestos), las suyas y los certificados. Ver arriba por qué lo escrito sigue aquí.
    nucleos_de_otros = {o: nucleo_de_otro(o) for o in otros}
    claves_mas = list(dict.fromkeys(sector_otro + otros
                                    + [str(c).strip() for c in _lista(r.get("claves_mas"))]
                                    + [str(c).strip() for c in _lista(r.get("certificaciones"))]))
    # Una palabra clave solo se busca si dice algo por sí sola: «a3» o «sap» como término de búsqueda
    # traen cualquier cosa. Con menos de 4 letras puntúa, pero no se busca. Lo escrito como puesto se
    # sigue buscando igual que cuando era palabra clave: con las mismas dos plazas.
    claves_para_buscar = [c for c in dict.fromkeys(sector_otro + otros
                                                   + [str(c).strip() for c in _lista(r.get("claves_mas"))])
                          if len(_normal(c).replace(" ", "")) >= MIN_LETRAS_CLAVE_COMO_TERMINO]

    terminos = _elegir_terminos(puestos, claves_para_buscar, anos <= 2, habla_en, habla_ca)
    avisos = []
    for p in puestos:
        if not p["terminos"].get("es") and not habla_en:
            avisos.append("ℹ️ «%s» se anuncia en inglés: sin inglés no lo puedo buscar, pero lo "
                          "reconoceré si llega por otro término." % p["nombre"])

    fuentes = []
    for p in puestos:
        fuentes += [f for f in p["fuentes"] if f not in fuentes]
    if not puestos:  # solo «otro»: las fuentes de su familia
        for i in ids:
            for p in familia_de[i]["puestos"][:1]:
                fuentes += [f for f in p["fuentes"] if f not in fuentes]
    # Solo lo escrito, sin ninguna familia: las fuentes de todos menos Tecnoempleo, que es de IT.
    fuentes = fuentes or list(FUENTES_SIN_FAMILIA)
    if "tecnoempleo" in fuentes and _km((float(ubic["lat"]), float(ubic["lon"])),
                                        CENTRO_BARCELONA) > RADIO_TECNOEMPLEO_KM:
        fuentes.remove("tecnoempleo")

    ambitos = _ambitos(fuentes, ciudad, radio, modalidad, alcance)
    if "local" not in ambitos:
        radio = 0.0

    elegidos = ids
    plan_puestos = []
    for p in puestos:
        plan_puestos.append({
            "id": p["id"], "nombre": p["nombre"], "nucleo": p["nucleo"],
            "senales": copy.deepcopy(p["senales"]),
            "negativos": [_sin_choca(x) for x in p["negativos"] if not _choca(x, elegidos)],
        })
    # Detrás de los del catálogo: si un título es de los dos, el motivo que se lee es el del catálogo.
    for otro in otros:
        if nucleos_de_otros[otro]:
            plan_puestos.append({"id": "otro:" + _normal(otro), "nombre": otro, "nucleo": nucleos_de_otros[otro],
                                 "senales": [], "negativos": []})
    descartes, vistos = [], set()
    for p in puestos:
        for regla in p["descartes"]:
            if regla["nombre"] not in vistos and not _choca(regla, elegidos):
                vistos.add(regla["nombre"])
                descartes.append(_sin_choca(regla))

    # Quien no acepta prácticas (la respuesta por defecto: solo se pregunta a quien no tiene experiencia, y
    # el resto no las quiere) no recibe programas de prácticas ni de recién titulado. Ver arriba.
    acepta_practicas = bool(r.get("practicas")) and exp in ("ninguna", "menos_1")
    if not acepta_practicas and DESCARTE_DE_PRACTICAS["nombre"] not in vistos:
        descartes.append(copy.deepcopy(DESCARTE_DE_PRACTICAS))

    consultas = len(terminos) * len(ambitos)
    jornada = [j for j in _lista(r.get("jornada")) if j in JORNADAS]
    salario = r.get("salario_min") or None
    plan = {
        "v": 1,
        "puestos": plan_puestos,
        "descartes": descartes,
        "terminos": terminos,
        # Jooble es un buscador español: sus dos peticiones, con los dos primeros términos que no
        # sean en inglés. Si solo hay en inglés, los de inglés (mejor eso que nada).
        "jooble_terminos": ([t for t in terminos if not _es_ingles(t, puestos)][:2]
                            or terminos[:2]),
        "ambitos": ambitos,
        "area": {"modo": "distancia", "nombre": ciudad, "lat": float(ubic["lat"]),
                 "lon": float(ubic["lon"]), "radio_km": radio},
        "experiencia_anos": anos,
        "experiencia_max_exigida": tope,
        "experiencia_estricta": estricta,
        "practicas": acepta_practicas,
        "idiomas_permitidos": idiomas,
        "ingles": ingles if habla_en else None,
        "jornada": jornada,
        "salario_min": salario,
        "claves_mas": claves_mas,
        "claves_menos": [str(c).strip() for c in _lista(r.get("claves_menos"))],
        "empresas_excluidas": [str(c).strip() for c in _lista(r.get("empresas_excluidas"))],
        "umbral": float(r.get("umbral") or UMBRAL),
    }
    plan["explicacion"] = _explicacion(puestos, sector_otro + otros, exp, tope, estricta, ciudad, radio, modalidad,
                                       idiomas, plan["ingles"], salario, terminos, consultas, avisos)
    return plan


def _es_ingles(termino: str, puestos: list[dict]) -> bool:
    clave = _normal(termino)
    return any(clave == _normal(t) for p in puestos for t in p["terminos"].get("en") or [])
