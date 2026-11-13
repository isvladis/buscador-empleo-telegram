"""
Dedupe difuso entre fuentes: detecta la misma oferta publicada en portales
distintos (indeed/linkedin/google/infojobs), donde job_url es distinto y el título
trae variaciones menores (mayúsculas, tildes, "Técnico" vs "Técnico/a", puntuación...).

Es un paso ADICIONAL al dedupe exacto por job_url que ya hace almacen.py, no lo
sustituye: aquí solo deberían entrar ofertas que ya sobrevivieron a ese primer filtro
(y, en el flujo real, que no estén en favoritas/ocultas/aplicadas — esa exclusión la
hace quien llama, no este módulo).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from rapidfuzz import fuzz

# Palabras que, si aparecen en un título y no en el otro (o viceversa), casi siempre
# significan que son ofertas DISTINTAS aunque el resto del texto sea casi idéntico:
# "Collections Specialist with Latvian" vs "... with Lithuanian", o "Auditor IT junior
# 2025" vs "... 2026". fuzz.ratio no lo detecta porque la diferencia es de pocos
# caracteres sobre un título largo, así que se comprueba aparte (ver
# _hay_palabra_distintiva_en_conflicto).
IDIOMAS = {
    "english", "spanish", "french", "german", "italian", "dutch", "polish", "czech",
    "slovak", "hungarian", "romanian", "bulgarian", "turkish", "arabic", "hebrew",
    "japanese", "korean", "danish", "swedish", "norwegian", "finnish", "portuguese",
    "greek", "latvian", "lithuanian", "estonian", "albanian", "irish", "ukrainian",
    "russian", "chinese", "mandarin", "croatian", "serbian", "slovenian", "maltese",
    "ingles", "espanol", "frances", "aleman", "italiano", "neerlandes", "polaco",
    "checo", "hungaro", "rumano", "turco", "arabe", "hebreo", "japones", "coreano",
    "danes", "sueco", "noruego", "finlandes", "portugues", "griego", "letón",
    "lituano", "albanes",
}

UMBRAL_TITULO_POR_DEFECTO = 90
# Más laxo que el de título a propósito: el agrupado por empresa normalizada (barato,
# se hace antes) ya actúa como primer filtro; esto es solo una segunda comprobación
# por si dos empresas distintas cayeran en la misma clave por normalización agresiva.
UMBRAL_EMPRESA = 85

# La MISMA vacante publicada bajo nombres de empresa distintos: filiales del mismo grupo
# (Allianz / Allianz Technology, NCR / NCR Voyix, ERNI Schweiz AG / ERNI Spain),
# reclutadoras que revenden la vacante de su cliente (Hired / Quik Hire Staffing,
# IRiS Grupo Tecnológico / Ciberia Tech / CIBERIA) y tablones que replican el anuncio
# (Booking Holdings / Hipo.ro / Undelucram.ro).
#
# Con el bloque por empresa esos pares NUNCA llegaban a compararse, así que cada copia
# era una tarjeta aparte. Sobre el histórico de la instalación original (1.712 ofertas)
# eso costó ocultaciones repetidas y, lo que importa de verdad, **candidaturas enviadas
# dos veces a la misma vacante**, a reclutadoras distintas que ofrecían el puesto del
# mismo cliente.
#
# El juez es la descripción, no el nombre: cuando la empresa no coincide se exige que el
# texto sea prácticamente el mismo. El umbral no está ajustado a ojo — en el histórico
# los pares con el mismo título y empresa distinta se reparten en dos grupos separados
# por un hueco vacío: la mediana de parecido es 42,7 y NO HAY NI UN PAR entre 70 y 80.
# Cualquier corte dentro de ese hueco da el mismo resultado; se toma 95 para dejar fuera
# también la zona 80-95, donde sí aparecen falsos positivos reales (dos ingenierías
# distintas que comparten plantilla de anuncio puntúan 83-84).
UMBRAL_DESCRIPCION_OTRA_EMPRESA = 95
# Comprobación previa barata: dos descripciones que difieren mucho en longitud no pueden
# parecerse un 95 %. Evita el fuzz.ratio, que sobre textos de 3.250 caracteres cuesta
# 2,95 ms en el Atom del Toshiba frente a 0,01 ms de esta comparación.
_MAX_DIFERENCIA_LONGITUD = 0.15

# Orden de fiabilidad de fuente para romper empates de completitud al elegir cuál de
# dos ofertas duplicadas conservar. No hay una métrica objetiva detrás, es el criterio
# acordado para este proyecto. "empresa" va primero porque su enlace lleva directo al
# formulario de la propia empresa, que es donde se acaba aplicando de todas formas.
ORDEN_FUENTES = {"empresa": 0, "linkedin": 1, "indeed": 2, "infojobs": 3,
                 # Tecnoempleo detrás de InfoJobs: su feed trae menos campos (sin
                 # candidatos ni salario estructurado), así que ante la misma oferta en los
                 # dos portales se queda la copia de InfoJobs.
                 "tecnoempleo": 3.5,
                 "eures": 4,
                 # Jooble la última: solo trae un resumen y su enlace es una redirección.
                 # Si la misma oferta llega por otra fuente, se queda la otra.
                 "jooble": 4.5,
                 "google": 5}

_LARGO_DESCRIPCION_MINIMO = 50


def normalizar_texto(texto) -> str:
    """minúsculas, sin tildes, sin caracteres especiales, espacios colapsados."""
    if not texto or not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9 ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


# Jooble pega el país al nombre de la empresa: «IRIUM - Spain», «Adecco Spain», «Deloitte
# España», «Veolia ? Hungary». La misma vacante llegó el 27-10 por Jooble («IRIUM -
# Spain») y por LinkedIn («IRIUM») y avisó dos veces. Solo se quita al FINAL: «Concentrix
# Spain Business Process Outsourcing» se queda como está.
_PAIS_AL_FINAL = re.compile(
    r"(?:(?:^|\s+)(?:spain|espana|hungary|portugal|france|germany|italy|italia|uk|ireland|poland))+$")


def normalizar_empresa(texto) -> str:
    """normalizar_texto() y además sin el país pegado al final."""
    return _PAIS_AL_FINAL.sub("", normalizar_texto(texto)).strip()


# Sube este número si cambia lo que devuelve claves_bloque() (o sus normalizadores): las
# claves se guardan en perfiles.db (`ofertas.bloque_empresa` y `bloque_titulo`), así que
# con una regla nueva dos ofertas del mismo bloque dejarían de encontrarse sin que nada
# fallara. Al arrancar, perfiles.py compara este número con el guardado, recalcula las
# columnas y fuerza una pasada completa del dedupe.
VERSION_BLOQUES = 1


def claves_bloque(oferta: dict) -> tuple[str, ...]:
    """
    Claves baratas para agrupar antes de comparar, y así evitar el O(n²) global.

    Son DOS: la empresa normalizada y el título normalizado. Con solo la empresa —como
    era hasta agosto de 2026— dos anuncios de la misma vacante publicados por empresas
    con nombre distinto no llegaban a compararse nunca (ver
    UMBRAL_DESCRIPCION_OTRA_EMPRESA). Basta con que coincidan en UNA de las dos para
    que son_duplicados() los mire; es esa función la que decide, no el bloque.
    """
    claves = []
    empresa = normalizar_empresa(oferta.get("company"))
    if empresa:
        claves.append("e:" + empresa)
    titulo = normalizar_texto(oferta.get("title"))
    if titulo:
        claves.append("t:" + titulo)
    return tuple(claves)


def clave_de_vacante(oferta: dict) -> tuple[str, str]:
    """
    (título, empresa) normalizados: identifica la VACANTE, no el anuncio.

    Sin empresa, (título, «@ubicación»): Jooble publica la misma oferta con dos ids y sin
    empresa, y el 27-10 avisó dos veces de «Técnico de Soporte Workplace Onsite» (03:42 y
    16:42). Para el dedupe eso no basta para fusionar (EURES trae muchas ofertas públicas
    sin empresa con el mismo título), pero para no repetir un AVISO en 14 días sí: lo peor
    que pasa es no avisar de una segunda vacante idéntica en la misma ciudad.

    Vive aquí y no en app.py desde MP1: la migración rellena con ella `avisos.vacante`, y
    perfiles.py no puede importar la web para eso.
    """
    titulo = normalizar_texto(oferta.get("title"))
    empresa = normalizar_empresa(oferta.get("company"))
    if not empresa:
        ubicacion = normalizar_texto(oferta.get("location"))
        empresa = "@" + ubicacion if ubicacion else ""
    return (titulo, empresa)


def _descripcion_comparable(oferta: dict) -> str:
    texto = oferta.get("description")
    if not isinstance(texto, str) or len(texto.strip()) < 200:
        return ""
    return normalizar_texto(texto)


def _misma_vacante_con_otro_nombre(a: dict, b: dict) -> bool:
    """
    ¿Son el mismo anuncio publicado bajo dos nombres de empresa distintos?

    Solo se responde que sí con la descripción delante y casi idéntica: sin ese requisito
    esto sería fusionar por título, que es justo lo que el resto del módulo evita (dos
    "Técnico/a Helpdesk" de empleadores distintos son dos ofertas, no una).
    """
    da, db = _descripcion_comparable(a), _descripcion_comparable(b)
    if not da or not db:
        return False
    if abs(len(da) - len(db)) > _MAX_DIFERENCIA_LONGITUD * max(len(da), len(db)):
        return False
    return fuzz.ratio(da, db) >= UMBRAL_DESCRIPCION_OTRA_EMPRESA


def _fecha(valor) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _fechas_cercanas(f1, f2, max_dias: int = 1) -> bool:
    """Sin fecha en alguna de las dos no se descarta el posible duplicado: muchas
    ofertas llegan sin date_posted, y perder el emparejamiento por eso sale más caro
    que el riesgo de un falso positivo por fecha ausente."""
    d1, d2 = _fecha(f1), _fecha(f2)
    if d1 is None or d2 is None:
        return True
    return abs((d1 - d2).days) <= max_dias


def _hay_palabra_distintiva_en_conflicto(t1: str, t2: str) -> bool:
    """
    True si las palabras que sobran de un lado respecto al otro delatan que son
    ofertas distintas: números que solo aparecen en uno de los dos títulos (nivel "1"
    vs "2", año "2025" vs "2026"...) o idiomas distintos mencionados en cada uno
    ("with Latvian" vs "with Lithuanian"). Sin este filtro, fuzz.ratio los puntúa por
    encima del umbral porque la diferencia es de muy pocos caracteres.
    """
    palabras1, palabras2 = set(t1.split()), set(t2.split())
    solo_en_1, solo_en_2 = palabras1 - palabras2, palabras2 - palabras1

    # Los dígitos se extraen de DENTRO de la palabra, no solo de las palabras que son un
    # número entero: el caso que motiva esta comprobación —el nivel de soporte— casi
    # siempre viene pegado a una letra ("n1"/"n2", "l1"/"l2"), y "n1".isdigit() es False,
    # así que mirando solo palabras-número "Técnico de Soporte N1" y "... N2" puntuaban
    # 95 de parecido y se daban por la misma oferta.
    numeros1 = {n for p in solo_en_1 for n in re.findall(r"\d+", p)}
    numeros2 = {n for p in solo_en_2 for n in re.findall(r"\d+", p)}
    if numeros1 != numeros2:
        return True

    if (solo_en_1 & IDIOMAS) or (solo_en_2 & IDIOMAS):
        return True

    return False


def son_duplicados(
    a: dict, b: dict, umbral_titulo: int = UMBRAL_TITULO_POR_DEFECTO
) -> tuple[bool, float]:
    """
    ¿a y b son la misma oferta publicada en dos sitios distintos?

    Compara título normalizado + empresa normalizada + proximidad de fecha de
    publicación (mismo día o ±1 día), y descarta el emparejamiento si el título trae
    una palabra distintiva en conflicto (ver _hay_palabra_distintiva_en_conflicto).
    Devuelve (es_duplicado, score_titulo) para poder loguear/revisar el score sin
    recalcularlo.
    """
    empresa_a, empresa_b = normalizar_empresa(a.get("company")), normalizar_empresa(b.get("company"))
    # Sin empresa en alguno de los dos lados no se puede afirmar que sean el mismo
    # anuncio, así que no se fusionan. Importa desde que existe EURES: los volcados de
    # los servicios públicos de empleo publican la oferta sin nombre de empresa, y
    # fuzz.ratio("", "") vale 100 —dos ofertas sin empresa y con título parecido
    # ("TÉCNICO/A HELPDESK") pasarían por duplicadas siendo de empleadores distintos, y
    # una de las dos desaparecería del listado sin dejar rastro.
    if not empresa_a or not empresa_b:
        return False, 0.0
    if (fuzz.ratio(empresa_a, empresa_b) < UMBRAL_EMPRESA
            and not _misma_vacante_con_otro_nombre(a, b)):
        return False, 0.0
    if not _fechas_cercanas(a.get("date_posted"), b.get("date_posted")):
        return False, 0.0

    t1, t2 = normalizar_texto(a.get("title")), normalizar_texto(b.get("title"))
    if _hay_palabra_distintiva_en_conflicto(t1, t2):
        return False, 0.0

    score_titulo = fuzz.ratio(t1, t2)
    return score_titulo >= umbral_titulo, score_titulo


def _puntuar_completitud(oferta: dict) -> int:
    """Cuánta información aprovechable trae una oferta, para decidir cuál de dos
    duplicadas conservar. La descripción pesa el doble que el resto de campos: es,
    con diferencia, el dato más valioso de cara al usuario."""
    puntos = 0
    descripcion = oferta.get("description")
    if isinstance(descripcion, str) and len(descripcion.strip()) >= _LARGO_DESCRIPCION_MINIMO:
        puntos += 2
    if oferta.get("min_amount") or oferta.get("max_amount") or oferta.get("salario"):
        puntos += 1
    if oferta.get("job_type") or oferta.get("contrato_label") not in (None, "No indicado"):
        puntos += 1
    if oferta.get("job_level") or oferta.get("experiencia_label") not in (None, "No especificado"):
        puntos += 1
    if oferta.get("company_industry"):
        puntos += 1
    return puntos


def _mejor(a: dict, b: dict) -> tuple[dict, dict]:
    """(superviviente, descartada) entre dos ofertas duplicadas."""
    pa, pb = _puntuar_completitud(a), _puntuar_completitud(b)
    if pa != pb:
        return (a, b) if pa > pb else (b, a)
    ra = ORDEN_FUENTES.get(a.get("site"), 99)
    rb = ORDEN_FUENTES.get(b.get("site"), 99)
    return (a, b) if ra <= rb else (b, a)


class _UnionFind:
    """Agrupa índices en clusters transitivos (si a~b y b~c, quedan en el mismo grupo
    aunque a y c no se hayan comparado directamente entre sí)."""

    def __init__(self, n: int):
        self.padre = list(range(n))

    def encontrar(self, x: int) -> int:
        while self.padre[x] != x:
            self.padre[x] = self.padre[self.padre[x]]
            x = self.padre[x]
        return x

    def unir(self, x: int, y: int) -> None:
        rx, ry = self.encontrar(x), self.encontrar(y)
        if rx != ry:
            self.padre[rx] = ry


def agrupar_duplicados(
    ofertas: list[dict], umbral_titulo: int = UMBRAL_TITULO_POR_DEFECTO
) -> list[list[dict]]:
    """
    Agrupa `ofertas` en clusters de duplicados difusos y devuelve solo los que tienen
    2 o más elementos (las ofertas sin duplicado no aparecen en el resultado).

    Eficiente por diseño: agrupa primero por empresa normalizada (clave barata) y solo
    aplica fuzzy matching dentro de cada grupo, nunca comparando todo contra todo.
    """
    bloques: dict[str, list[int]] = {}
    for i, oferta in enumerate(ofertas):
        for clave in claves_bloque(oferta):
            bloques.setdefault(clave, []).append(i)

    uf = _UnionFind(len(ofertas))
    # Una misma pareja puede caer en los dos bloques (misma empresa y mismo título); se
    # compara una sola vez, que es lo que cuesta cuando hay que mirar la descripción.
    comparadas: set[tuple[int, int]] = set()
    for indices in bloques.values():
        if len(indices) < 2:
            continue
        for pi in range(len(indices)):
            for pj in range(pi + 1, len(indices)):
                i, j = indices[pi], indices[pj]
                if (i, j) in comparadas:
                    continue
                comparadas.add((i, j))
                es_dup, _ = son_duplicados(ofertas[i], ofertas[j], umbral_titulo)
                if es_dup:
                    uf.unir(i, j)

    grupos: dict[int, list[dict]] = {}
    for i, oferta in enumerate(ofertas):
        grupos.setdefault(uf.encontrar(i), []).append(oferta)

    return [g for g in grupos.values() if len(g) > 1]


def fusionar_duplicados_difusos(
    ofertas: list[dict],
    umbral_titulo: int = UMBRAL_TITULO_POR_DEFECTO,
    campos_a_rescatar: tuple[str, ...] = (),
) -> tuple[list[dict], list[dict]]:
    """
    Colapsa cada cluster de duplicados difusos en una sola oferta (la de más
    información; el empate se rompe por fiabilidad de fuente, ver ORDEN_FUENTES) y
    anota en `fuentes_adicionales` qué otras fuentes trajeron el mismo anuncio.

    `campos_a_rescatar` son campos que, si la superviviente no tiene y alguna de las
    descartadas sí, se copian a la superviviente. Hace falta para los datos que solo se
    pueden tomar UNA vez (ver almacen.CAMPOS_DEL_DESCUBRIMIENTO): la copia de LinkedIn
    trae cuánta gente había aplicado, pero si gana la de Indeed —que puede traer más
    campos y por eso puntúa más completa— ese dato se perdería para siempre. Pasó de
    verdad la primera vez que se desplegó esto, con `Técnico de Soporte Informático`.

    Devuelve (ofertas_resultantes, resumen_fusiones); el resumen es solo para
    inspección/logging, con título y fuente de cada par conservado/descartado.
    """
    clusters = agrupar_duplicados(ofertas, umbral_titulo)
    ids_en_cluster = {id(o) for cluster in clusters for o in cluster}

    resultado = [o for o in ofertas if id(o) not in ids_en_cluster]
    resumen = []

    for cluster in clusters:
        superviviente = cluster[0]
        for otra in cluster[1:]:
            superviviente, descartada = _mejor(superviviente, otra)
            resumen.append(
                {
                    "conservada": {"title": superviviente.get("title"), "site": superviviente.get("site")},
                    "descartada": {"title": descartada.get("title"), "site": descartada.get("site")},
                }
            )

        fuentes_adicionales = sorted(
            {o.get("site") for o in cluster if o.get("site") and o.get("site") != superviviente.get("site")}
            | set(superviviente.get("fuentes_adicionales") or [])
        )

        superviviente = dict(superviviente)
        for campo in campos_a_rescatar:
            if superviviente.get(campo) is None:
                for otra in cluster:
                    if otra.get(campo) is not None:
                        superviviente[campo] = otra[campo]
                        break
        if fuentes_adicionales:
            superviviente["fuentes_adicionales"] = fuentes_adicionales
        resultado.append(superviviente)

    return resultado, resumen
