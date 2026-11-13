# -*- coding: utf-8 -*-
"""
Las 14 familias profesionales y sus puestos: la fuente de `perfilado/catalogo.json` (MP7).

**Aprobada por el titular el 04-11-2026**, antes de escribir las reglas: 14 familias y 94 puestos, con
cuatro añadidos suyos (traducción, niñera, agente cívico y recepcionista ampliado a clínicas y
gimnasios). Python y no JSON por lo mismo que `reglas/`: cada regex lleva la razón que la justifica, y
en JSON se perdería. `python -m perfilado.construir` escribe el JSON y `pruebas/test_catalogo.py`
comprueba que no se ha quedado atrás.

QUÉ ES CADA COSA DE UN PUESTO (el formato del §5.1 y del §5.5):

- `terminos`: lo que se busca en los portales, por idioma. Solo términos que se usan de verdad en
  ofertas en España. `terminos_junior`: lo que se añade si la persona tiene ≤ 2 años.
- `nucleo`: la regex del título que dice «esto es el puesto» (+4,0). Es lo que miden las muestras.
- `senales`: secundarias, con peso; entre todas como mucho +2,0 (lo recorta el puntuador).
- `negativos`: lo que parece el puesto y no lo es; restan, no descartan.
- `descartes`: lo que nunca puede ser el puesto; quitan la oferta antes de que nadie la vea. **Casi no
  hay**: un descarte equivocado no se ve nunca y un negativo equivocado solo baja una nota. Solo los
  de IT, copiados de `titular-ciber`.
- `choca_con` (en negativos y descartes): los puestos o familias a los que la regla haría daño. Si la
  persona eligió también uno de ellos, el generador la quita. Ejemplo: «desarrollo de software» resta
  en soporte IT, pero no a quien buscó soporte Y desarrollo. `test_catalogo.py` lo comprueba contra
  las muestras: si un negativo de A casa con un título que B debe aceptar, B tiene que estar en
  su `choca_con`.
- `tambien_en`: familias en cuyo menú también aparece el puesto (la niñera, en Educación y en
  Cuidados). Un solo `id` y unas solas reglas, para que no se desincronicen dos copias.

LAS REGEX se aplican al título sin distinguir mayúsculas, **con los acentos tal cual** (el puntuador no
los quita), así que van con clase: `almac[ée]n`. Las variantes de género de los portales («camarero/a»,
«Camarer@», «camareros/as») se cogen por la raíz o con `(?:/as?)?`. Y `\\b` donde hace falta: es lo que
aprendió el proyecto con «secops» dentro de «DevSecOps» y con «monitor» dentro de «monitorización».

**Los números de cada familia están en CAMBIOS-MP7.md**: las muestras salen inventadas en casi todo lo
que no es IT, porque la base del servidor son ofertas de IT, y lo que se mide con muestras propias sale
inflado (la del titular da 81 % y sus botones, 35 %).
"""

from __future__ import annotations

import copy

from reglas import titular_2026_10 as _base
from reglas import titular_ciber as _ciber

# ----------------------------------------------------------------------
# FUENTES
#
# EURES en todas: vuelca las ofertas del SEPE y de Feina Activa, que son justo las de los sectores que
# no son IT (§6). InfoJobs, en todas también: es la mejor fuente para hostelería, logística o comercio,
# y hoy está desactivada por la salud de fuentes; tenerla en la lista no hace ninguna petición hasta
# que el titular la reactive. Tecnoempleo, solo en IT, y el generador la quita si el área no es
# Barcelona: es el feed de esa provincia y nada más (fuentes_extra/tecnoempleo.py).
# ----------------------------------------------------------------------
FUENTES_GENERALES = ["linkedin", "indeed", "infojobs", "eures", "jooble"]
FUENTES_IT = FUENTES_GENERALES + ["tecnoempleo"]


def s(nombre: str, peso: float, patron: str) -> dict:
    """Una señal secundaria. `nombre` es el motivo que lee la persona."""
    return {"nombre": nombre, "peso": peso, "patron": patron}


def n(nombre: str, peso: float, patron: str, choca_con=()) -> dict:
    """Un negativo: resta. `choca_con`, los puestos o familias a los que no se aplica."""
    regla = {"nombre": nombre, "peso": peso, "patron": patron}
    if choca_con:
        regla["choca_con"] = list(choca_con)
    return regla


def puesto(id, nombre, es, en=(), ca=(), junior=(), nucleo="", senales=(), negativos=(),
           descartes=(), tambien_en=(), nota=None) -> dict:
    datos = {
        "id": id,
        "nombre": nombre,
        "terminos": {"es": list(es), "en": list(en), "ca": list(ca)},
        "terminos_junior": list(junior),
        "nucleo": nucleo,
        "senales": [copy.deepcopy(x) for x in senales],
        "negativos": [copy.deepcopy(x) for x in negativos],
        "descartes": [copy.deepcopy(x) for x in descartes],
    }
    if tambien_en:
        datos["tambien_en"] = list(tambien_en)
    if nota:
        datos["nota"] = nota
    return datos


def familia(id, emoji, nombre, puestos, fuentes=None) -> dict:
    fuentes = list(fuentes or FUENTES_GENERALES)
    for p in puestos:
        p.setdefault("fuentes", list(fuentes))
    return {
        "id": id, "emoji": emoji, "nombre": nombre,
        # «✏️ Otro» no tiene reglas propias: lo que escriba la persona se guarda como palabra clave
        # (§4.2, paso 2), y con eso puntúa y, si cabe, se busca.
        "otro": {"id": f"{id}.otro", "nombre": "Otro (escribirlo)"},
        "puestos": puestos,
    }


# ----------------------------------------------------------------------
# NEGATIVOS COMPARTIDOS
#
# Un mismo nombre cuenta una vez aunque lo traigan dos puestos (buscador.PuntuadorCatalogo), así que
# cada uno se define UNA vez y los puestos lo reutilizan: dos patrones distintos con el mismo nombre
# harían que mandara el del primer puesto elegido. `test_catalogo.py` lo vigila.
# ----------------------------------------------------------------------
NEG_COMERCIAL = n(
    "comercial o ventas", -2.0,
    r"\bcomercial(?:es)?\b|\bventas\b|\bsales\b|captaci[óo]n|\bdelegad[oa]|visitador|key account",
    choca_con=["comercial", "comercio.dependiente", "comercio.encargado", "admin.comercial"])
# Lo que más se cuela en las otras familias, medido en la base del servidor: «Técnico/a de Soporte –
# Software Hotelero (PMS Cloud)», «L2 Technical Support Specialist — Hospitality Tech», «Técnico/a
# de Reliability y Continuidad de Negocio (CS Retail)». Son de IT y llevan la palabra del sector.
NEG_INFORMATICA = n(
    "informática o software", -3.0,
    r"inform[áa]tic|software|\bit\b|\bsistemas\b|\bsap\b|\berp\b|developer|desarrollador|programador"
    r"|soporte t[ée]cnico|technical support|help ?desk|service desk|ciber|cyber|\bpms\b|\bsaas\b"
    r"|business analyst|consultor\w*(?:/a)? (?:sap|funcional|t[ée]cnic)|functional consultant",
    choca_con=["it"])
NEG_TRIPULANTE = n(
    "tripulante de cabina", -3.0,
    r"de vuelo|cabin crew|flight attendant|tripulante|\btcp\b|auxiliar de vuelo|steward(?:ess)? de",
)
NEG_AUTONOMO = n(
    "autónomo o solo a comisión", -2.0,
    r"aut[óo]nom[oa]|\bfreelance\b|sin fijo|solo comisi[óo]n|100 ?% comisi|veh[íi]culo propio"
    r"|furgoneta propia",
)
# Señales que comparten nombre entre puestos: una sola definición, por lo mismo que los negativos.
S_REDES = s("redes", 0.5, r"redes|network|xarxes|cisco|fortinet")
S_ERP = s("ERP", 0.5, r"\bsap\b|\berp\b|navision|\ba3\b|oracle")
S_SUPERMERCADO = s("supermercado o tienda", 0.5, r"supermercad|hipermercad|alimentaci|tienda|mercado")

NEG_DOCENCIA = n(
    "docencia", -1.5, r"\bprofesor|\bdocente|\bformador|\bteacher\b|\btrainer\b",
    choca_con=["educacion"])


# ======================================================================
# 1. INFORMÁTICA Y TELECOM
#
# Los núcleos salen de `reglas/titular_2026_10.py` y de `reglas/titular_ciber.py`, importados, no
# copiados a mano; `test_catalogo.py` comprueba además que el JSON sigue llevando las mismas cadenas.
# Lo que NO se copia, a propósito, es el gusto del titular: «sin desarrollo», «sin ERP», «solo SOC»,
# «sin prácticas»… En el catálogo, desarrollo y datos son puestos, y quien elija ciberseguridad puede
# querer pentest o GRC. De ahí se toma el oficio, no la persona.
# ======================================================================
def _regla_ciber(nombre: str) -> dict:
    """Una regla de descarte de `titular-ciber`, tal cual. Que no exista o se repita es un error."""
    reglas = [r for r in _ciber.REGLAS_DESCARTE if r["nombre"] == nombre]
    if len(reglas) != 1:
        raise ValueError(f"titular-ciber no tiene una sola regla {nombre!r}")
    return copy.deepcopy(reglas[0])


def _de_la_tabla(tabla, nombre: str) -> tuple[str, float, str]:
    filas = [f for f in tabla if f[0] == nombre]
    if len(filas) != 1:
        raise ValueError(f"no hay una sola regla {nombre!r}")
    return filas[0]


_HELPDESK = _de_la_tabla(_base.SENALES_RELEVANCIA, "helpdesk o soporte")[2]
_SOC_SIEM = _de_la_tabla(_base.SENALES_RELEVANCIA, "SOC / SIEM")[2]
_PREVENTA = _de_la_tabla(_base.PENALIZACIONES_RELEVANCIA, "preventa, comercial o fraude")[2]
_NIVEL_3 = _de_la_tabla(_base.PENALIZACIONES_RELEVANCIA, "nivel 3")[2]
_DESARROLLO_TITULO = _de_la_tabla(_base.PENALIZACIONES_RELEVANCIA, "desarrollo de software")[2]
_QA = _de_la_tabla(_base.PENALIZACIONES_RELEVANCIA, "QA o testing")[2]
_DEVOPS = _de_la_tabla(_base.PENALIZACIONES_RELEVANCIA, "DevOps o cloud engineer")[2]
_DESCARTE_DESARROLLO = _regla_ciber("Desarrollo de software")["patron"]
_DESCARTE_DATOS = _regla_ciber("Datos, BI e IA")["patron"]
# El administrador de sistemas de `titular-2026-10`, que vive dentro de su señal de soporte.
_SYSADMIN = r"administrador\w*(?:/a)? (?:de )?sistemas|sysadmin|systems? administrator"
if _SYSADMIN not in _base._PUESTO_SOPORTE_IT:
    raise ValueError("el administrador de sistemas de titular-2026-10 ha cambiado")

# Lo que la base enseña que se cuela con las palabras de IT. Se copian de `titular-ciber` porque son
# del oficio, no del titular: «Técnico de PRL», «Técnico de Ascensores», «Bodeguero/a (baja IT)».
# Chocan con casi todas las otras familias (llevan «camarero», «conductor», «tesorería»…): si la
# persona busca soporte IT y además hostelería, la regla se va entera.
_FAMILIAS_NO_IT = ["admin", "atencion", "comercial", "marketing", "finanzas", "rrhh", "sanidad",
                   "educacion", "hosteleria", "comercio", "logistica", "industria", "oficios"]
_DESCARTE_OFICIOS = dict(_regla_ciber("Oficios no técnicos (sanidad, obra, PRL…)"),
                         choca_con=_FAMILIAS_NO_IT)
_DESCARTE_FALSOS_AMIGOS = dict(_regla_ciber("Falsos amigos de IT (baja IT, preventa)"),
                               choca_con=["comercial", "hosteleria"])
_DESCARTE_INVESTIGACION = _regla_ciber("Investigación académica")

NEG_PREVENTA = n("preventa, comercial o fraude", -3.0, _PREVENTA, choca_con=["comercial", "admin.comercial"])
NEG_DESARROLLO = n("desarrollo de software", -2.0, _DESARROLLO_TITULO, choca_con=["it.desarrollo"])
NEG_NIVEL_3 = n("nivel 3", -2.0, _NIVEL_3)

IT = familia("it", "💻", "Informática y telecom", fuentes=FUENTES_IT, puestos=[
    puesto(
        "it.soporte", "soporte / helpdesk",
        es=["técnico soporte informático", "helpdesk", "técnico informático", "service desk"],
        en=["IT support", "service desk"], ca=["tècnic informàtic"],
        junior=["junior it support", "técnico soporte n1"],
        # La señal central de soporte de `titular-2026-10`, entera: es la que se validó con sus
        # marcadas (AUC 0,862 -> 0,920, ver aquel fichero).
        nucleo=_base._PUESTO_SOPORTE_IT,
        senales=[s("helpdesk o soporte", 1.0, _HELPDESK),
                 s("nivel 1", 1.0, r"\bn1\b|nivel 1|\bl1\b|level 1|tier ?1")],
        negativos=[NEG_PREVENTA, NEG_DESARROLLO, NEG_NIVEL_3],
        descartes=[_DESCARTE_OFICIOS, _DESCARTE_FALSOS_AMIGOS, _DESCARTE_INVESTIGACION],
    ),
    puesto(
        "it.sistemas", "sistemas y redes",
        es=["técnico de sistemas", "administrador de sistemas", "técnico de redes"],
        en=["system administrator", "network engineer"], ca=["tècnic de sistemes"],
        junior=["técnico de sistemas junior"],
        nucleo=(
            _SYSADMIN
            + r"|t[éèe]cnic\w*(?:/a)? (?:j[úu]nior )?(?:de |d'|en )?(?:sistemas|sistemes|redes|xarxes"
            r"|infraestructura|comunicaciones)\b"
            r"|administrador\w*(?:/a)? (?:de )?(?:redes|sistemes|infraestructura)|systems? engineer"
            r"|network (?:administrator|engineer|technician|specialist)|ingenier\w*(?:/a)? de (?:sistemas|redes)"
            r"|operador\w*(?:/a)? de sistemas|windows server|\bvmware\b|infrastructure (?:engineer|technician)"
            r"|sistemas (?:y|e) redes|especialista (?:en |de )?(?:sistemas|redes)"
        ),
        senales=[S_REDES,
                 s("Windows o Linux", 0.5, r"windows|linux|vmware|active directory|microsoft 365")],
        negativos=[NEG_PREVENTA, NEG_DESARROLLO, NEG_NIVEL_3],
        descartes=[_DESCARTE_OFICIOS, _DESCARTE_FALSOS_AMIGOS, _DESCARTE_INVESTIGACION],
    ),
    puesto(
        "it.ciber", "ciberseguridad",
        es=["analista ciberseguridad", "técnico ciberseguridad", "analista soc"],
        en=["cybersecurity analyst", "soc analyst"], ca=["ciberseguretat"],
        junior=["junior soc", "junior cybersecurity"],
        # La señal central de `titular-ciber` (MP4), más lo que el titular dejó fuera para sí y que en
        # el oficio es ciberseguridad: pentest, GRC, appsec, ingeniería de seguridad. El nivel lo juzgan
        # la experiencia y el filtro de senior, no esta regex.
        nucleo=(
            _ciber._PUESTO_CIBER
            + r"|pentest\w*|penetration test\w*|ethical hack\w*|hacking [ée]tico|red team|\bgrc\b"
            r"|appsec|application security|(?:cyber ?security|security|seguridad) engineer"
            r"|ingenier\w*(?:/a)? (?:de |en )?(?:ciberseguridad|seguridad inform\w*)"
            r"|consultor\w*(?:/a)? (?:de |en )?ciberseguridad|cybersecurity (?:consultant|specialist)"
            r"|information security officer|\bciso\b|seguridad ofensiva|offensive security"
        ),
        senales=[s("SOC / SIEM", 1.5, _SOC_SIEM)],
        negativos=[
            # «Vigilante de seguridad», «Técnico de sistemas de seguridad (CCTV)»: seguridad física.
            n("seguridad física o electrónica", -4.0,
              r"vigilante|seguridad privada|seguridad electr[óo]nica|\bcctv\b|videovigilancia|alarmas"
              r"|control de accesos|security guard|escolta",
              choca_con=["oficios.vigilancia"]),
            n("prevención de riesgos laborales", -4.0,
              r"\bprl\b|prevenci[óo]n de riesgos|seguridad (?:y salud|laboral|e higiene)|\bhse\b|\behs\b"
              r"|health (?:and|&) safety",
              choca_con=["industria.prl"]),
            NEG_PREVENTA,
        ],
        descartes=[_DESCARTE_INVESTIGACION],
    ),
    puesto(
        "it.desarrollo", "desarrollo",
        es=["desarrollador", "programador"],
        en=["software developer", "backend developer", "frontend developer"], ca=["desenvolupador"],
        junior=["desarrollador junior", "junior developer"],
        # La regla «Desarrollo de software» de `titular-ciber`, que para el titular descarta y aquí es
        # justo el puesto.
        nucleo=(
            _DESCARTE_DESARROLLO
            + r"|ingenier\w*(?:/a)? (?:de )?software|desenvolupador\w*|maquetador\w*|web developer"
            r"|programmer|desarrollo (?:web|de software|de aplicaciones)\b"
        ),
        senales=[s("lenguaje o stack", 1.0,
                   r"\bjava\b|python|javascript|typescript|react|angular|\.net\b|\bc#|\bphp\b|node"
                   r"|kotlin|swift|golang")],
        negativos=[
            # «Desarrollador/a de negocio» es comercial: «desarrollador» casa con el núcleo.
            n("desarrollo de negocio", -4.0,
              r"(?:desarrollador\w*(?:/a)?|developer)\W+(?:\w+\W+){0,2}(?:negocio|business|comercial)"
              r"|business developer",
              choca_con=["comercial.inside", "comercial.cuentas"]),
            n("soporte, no desarrollo", -2.0, r"soporte|support|help ?desk|service desk",
              choca_con=["it.soporte"]),
        ],
    ),
    puesto(
        "it.datos", "datos y BI",
        es=["analista de datos", "ingeniero de datos"],
        en=["data analyst", "data engineer", "business intelligence"], ca=["analista de dades"],
        junior=["data analyst junior"],
        nucleo=(
            _DESCARTE_DATOS
            + r"|analista de (?:datos|dades)|ingenier\w*(?:/a)? de datos|cient[íi]fic\w*(?:/a)? de datos"
            r"|\bbi (?:analyst|developer|consultant|engineer)|analista bi\b|tableau|\b(?:ml|ai) engineer"
            r"|ingenier\w*(?:/a)? (?:de )?(?:machine learning|inteligencia artificial|ia)\b"
        ),
        senales=[s("SQL o Python", 1.0, r"\bsql\b|python|\bdbt\b|snowflake|spark")],
        negativos=[
            n("grabación de datos", -4.0, r"data entry|grabaci[óo]n de datos|grabador\w*",
              choca_con=["admin.backoffice"]),
            n("protección de datos (legal)", -3.0,
              r"protecci[óo]n de datos|\bdpo\b|data protection|privacy|privacidad"),
        ],
    ),
    puesto(
        "it.qa", "QA / testing",
        # El «tester» de `titular-2026-10` casaba con «Pentester Junior» y «Web-Penetration-Tester»
        # (base, 4 de 33): se envuelve con lookbehind en vez de cambiar la cadena, que se compara.
        es=["tester", "qa"], en=["qa engineer", "software tester"],
        junior=["qa junior", "tester junior"],
        nucleo=(
            r"(?<!pen)(?<!penetration )(?<!penetration-)(?:" + _QA + r")|quality assurance|\bqe\b|test (?:engineer|analyst|automation|lead|manager)"
            r"|automatizaci[óo]n de pruebas|pruebas de software|analista de pruebas|\bsdet\b"
        ),
        senales=[s("automatización de pruebas", 1.0, r"automati\w*|selenium|cypress|playwright|appium")],
        negativos=[
            n("pentest", -3.0, r"pen(?:etration)?[ -]?test|pentest|ethical hack", choca_con=["it.ciber"]),
            # «QA» y «Quality Assurance» en farmacia o alimentación son calidad industrial.
            n("calidad industrial o farmacéutica", -3.0,
              r"\bgmp\b|farma|pharma|aliment|\bfood\b|manufactur|producci[óo]n|\bplanta\b|laboratori",
              choca_con=["industria.calidad", "industria.laboratorio", "sanidad.farmacia"]),
        ],
        descartes=[_DESCARTE_INVESTIGACION],
    ),
    puesto(
        "it.cloud", "cloud / DevOps",
        es=["ingeniero devops", "ingeniero cloud"], en=["devops engineer", "cloud engineer"],
        junior=["devops junior"],
        nucleo=(
            _DEVOPS + r"|site reliability|platform engineer|ingenier\w*(?:/a)? de plataforma"
            r"|cloud (?:architect|administrator|operations|support|specialist|technician|infrastructure)"
            r"|t[ée]cnic\w*(?:/a)? cloud|especialista cloud|\baws\b|\bazure\b|\bgcp\b|kubernetes"
            r"|infraestructura cloud|administrador\w*(?:/a)? cloud"
        ),
        senales=[s("contenedores e IaC", 1.0, r"kubernetes|\bk8s\b|docker|terraform|ansible")],
        negativos=[NEG_PREVENTA],
        descartes=[_DESCARTE_INVESTIGACION],
    ),
    puesto(
        "it.telecom", "telecom / NOC",
        es=["técnico noc", "técnico de telecomunicaciones", "técnico de monitorización"],
        en=["noc engineer", "noc technician"], ca=["tècnic de telecomunicacions"],
        junior=["técnico noc junior"],
        # NO es `_PUESTO_MONITORIZACION` de `titular-2026-10` tal cual: allí «monitoring» a secas vale
        # porque las ofertas ya vienen de buscar IT, y en un perfil de catálogo casaría con «AML
        # Transaction Monitoring Analyst» o con un monitor de ensayos clínicos. Aquí «monitorización»
        # solo cuenta detrás de un rol técnico.
        nucleo=(
            r"\bnoc\b|network operations|centro de (?:control|operaciones) de red"
            r"|t[éèe]cnic\w*(?:/a)? (?:j[úu]nior )?(?:de |en )?(?:telecomunicaci\w*|telecom\b|fibra|radiofrecuencia"
            r"|transmisi[óo]n|redes m[óo]viles|monitori[zt]a\w*|observabilidad)"
            r"|(?:operador\w*(?:/a)?|operator|technician|engineer|t[éèe]cnic\w*(?:/a)?)\W+(?:\w+\W+){0,2}"
            r"(?:monitori[zt]a\w*|monitoring)\b"
            r"|ingenier\w*(?:/a)? (?:de )?telecomunicaci\w*|telecom(?:munications)? (?:engineer|technician)"
            r"|\b(?:rf|5g|lte) (?:engineer|optimi[sz]ation)|fibra [óo]ptica|\bftth\b"
        ),
        senales=[s("24x7", 0.5, r"24x7|24/7|turnos"), S_REDES],
        negativos=[NEG_PREVENTA,
                   n("instalación de campo", -2.0, r"instalador|montador|de campo|\bfield\b|tendido",
                     choca_con=["oficios.electricista", "oficios.mantenimiento"])],
        descartes=[_DESCARTE_OFICIOS, _DESCARTE_INVESTIGACION],
    ),
])

# ======================================================================
# 2. ADMINISTRACIÓN Y OFICINA
# ======================================================================
NEG_LEGAL = n("abogacía y legal", -3.0, r"abogad|letrad|contencioso|jur[íi]dic|\blegal\b|paralegal")

ADMIN = familia("admin", "🗂️", "Administración y oficina", puestos=[
    puesto(
        "admin.administrativo", "administrativo/a",
        es=["administrativo", "administrativa"],
        en=["administrative assistant", "office administrator"], ca=["administratiu"],
        junior=["administrativo junior"],
        # «Administrativo» casa con todas sus variantes («administrativo contable», «de logística»):
        # son administrativos. Lo que no es un administrativo («contencioso-administrativo») lo frena
        # el negativo legal. Sin «técnico de administración»: casaba con «de administración de sistemas».
        nucleo=(
            r"\badministrativ|\badministratiu|auxiliar de oficina|office (?:assistant|administrator|clerk)"
            r"|administrative (?:assistant|officer|clerk|coordinator|support)|gesti[óo]n administrativa"
            r"|personal de administraci[óo]n\b(?! de sistemas)"
        ),
        senales=[s("contabilidad y facturación", 1.0, r"contab|factura|comptab"),
                 s("recepción o atención", 0.5, r"recepci[óo]n|atenci[óo]n al cliente|centralita")],
        negativos=[NEG_COMERCIAL, NEG_LEGAL],
    ),
    puesto(
        "admin.auxiliar", "auxiliar administrativo/a",
        es=["auxiliar administrativo"], ca=["auxiliar administratiu"],
        junior=["auxiliar administrativo sin experiencia"],
        nucleo=(
            r"auxiliar\w*(?:/a)? (?:de )?administra\w*|auxiliar de oficina|\baux\.? administrativ\w*"
            r"|ayudante administrativ\w*|administrative (?:assistant|clerk)|office (?:assistant|clerk)"
        ),
        senales=[s("contabilidad y facturación", 1.0, r"contab|factura|comptab")],
        negativos=[NEG_COMERCIAL, NEG_LEGAL],
    ),
    puesto(
        "admin.recepcion", "recepcionista (oficina, clínica, gimnasio…)",
        es=["recepcionista", "recepcionista clínica", "recepcionista centro médico"],
        en=["receptionist"], ca=["recepcionista"], junior=["recepcionista sin experiencia"],
        tambien_en=["atencion", "sanidad"],
        # El de hotel tiene su puesto en Hostelería; aquí no se le resta: a quien busca recepción le
        # vale también la de un hotel.
        nucleo=r"recepcionist|receptionist|\brecepci[óo]n\b|front desk|personal de acogida",
        senales=[s("centralita y atención", 0.5, r"centralita|telef[óo]n|atenci[óo]n|acogida")],
        negativos=[
            # «Recepción de mercancías», «Administrativo/a de recepción de pedidos».
            n("recepción de mercancía", -4.0, r"mercanc|almac[ée]n|log[íi]stic|pedidos|materiales",
              choca_con=["logistica"]),
            NEG_COMERCIAL,
        ],
    ),
    puesto(
        "admin.direccion", "secretario/a y asistente de dirección",
        es=["asistente de dirección", "secretaria de dirección"],
        en=["executive assistant", "personal assistant"], ca=["secretària de direcció"],
        nucleo=(
            r"asistente (?:de|a la) direcci[óo]n|assistant (?:to|of) (?:the )?(?:ceo|director|management|md)"
            r"|\bsecretari[oa]\w*|secret[àa]ri[ae]?\b|executive assistant|personal assistant|\bpa to\b"
            r"|office manager|asistente ejecutiv\w*|asistente personal"
        ),
        senales=[s("agenda y viajes", 0.5, r"agenda|viajes|eventos|direcci[óo]n general")],
        negativos=[
            # «Asistente personal» en dependencia es cuidar a una persona, no una agenda.
            n("asistencia personal a dependientes", -3.0,
              r"dependen|discapacidad|diversidad funcional|mayores|domicilio",
              choca_con=["sanidad.domicilio"]),
            NEG_COMERCIAL,
        ],
    ),
    puesto(
        "admin.backoffice", "back office y grabación de datos",
        es=["back office", "grabador de datos", "gestor administrativo"], en=["back office"],
        nucleo=(
            r"back[- ]?office|grabador\w*(?:/a)? de datos|grabaci[óo]n de datos|data entry"
            r"|gestor\w*(?:/a)? administrativ\w*|gesti[óo]n documental|operador\w*(?:/a)? de datos"
            r"|digitalizaci[óo]n de documentos|middle office"
        ),
        senales=[s("documental", 0.5, r"document|archivo|digitaliz|expedientes")],
        negativos=[NEG_COMERCIAL,
                   n("desarrollo de software", -2.0, _DESARROLLO_TITULO, choca_con=["it.desarrollo"])],
    ),
    puesto(
        "admin.comercial", "administrativo/a comercial y de pedidos",
        es=["administrativo comercial", "gestión de pedidos", "administrativo de ventas"],
        en=["sales administrator"], ca=["administratiu comercial"],
        nucleo=(
            r"administrativ\w*(?:/a)? (?:de )?(?:comercial|ventas|pedidos|exportaci[óo]n|importaci[óo]n"
            r"|compras|clientes)|administratiu\w*(?:/a)? comercial|gesti[óo]n de pedidos"
            r"|gestor\w*(?:/a)? de pedidos|sales (?:administrator|admin|coordinator|support)"
            r"|order (?:management|entry|administrator)|asistente comercial|auxiliar comercial"
            r"|soporte comercial|apoyo comercial"
        ),
        senales=[s("exportación o importación", 0.5, r"export|import|comercio exterior|internacional"),
                 S_ERP],
        negativos=[
            n("venta de campo", -2.0,
              r"comercial de (?:campo|calle|zona)|puerta a puerta|captaci[óo]n|aut[óo]nom[oa]|comisiones",
              choca_con=["comercial.campo", "comercial.asesor"]),
        ],
    ),
])

# ======================================================================
# 3. ATENCIÓN AL CLIENTE
# ======================================================================
NEG_VENTA_TELEFONICA = n(
    "venta telefónica", -2.0,
    # `\bventas?\b`: con `\bventa` a secas casaba «Instalador/a de ventanas». Sin «emisión» ni
    # «outbound»: llamar no es vender, y el teleoperador de emisión es teleoperador.
    r"\bventas?\b|televent|comercial|captaci[óo]n|\bsales\b",
    choca_con=["comercial", "comercio.dependiente"])
NEG_SOPORTE_IT = n(
    "soporte técnico informático", -1.5,
    r"soporte t[ée]cnico|technical support|help ?desk|it support|inform[áa]tic|service desk"
    # «Técnico De Sistemas Contact Center Cloud», «Técnico/a Workforce Management (RTA)» (base).
    r"|sistemas|\bcloud\b|workforce management|\bwfm\b",
    choca_con=["it"])

ATENCION = familia("atencion", "🎧", "Atención al cliente", puestos=[
    puesto(
        "atencion.teleoperador", "teleoperador/a",
        es=["teleoperador", "teleoperadora", "atención telefónica"],
        ca=["teleoperador", "atenció telefònica"], junior=["teleoperador sin experiencia"],
        nucleo=(
            r"teleoperador|tele-operador|operador\w*(?:/a)? telef[óo]nic|agente telef[óo]nic"
            r"|atenci[óo]n telef[óo]nica|atenci[óo] telef[òo]nica|call ?cent(?:er|re)|contact ?cent(?:er|re)"
            r"|gestor\w*(?:/a)? telef[óo]nic|teleasistencia"
        ),
        senales=[s("atención al cliente", 1.0,
                   r"atenci[óo]n al cliente|customer|servicio al cliente|atenci[óo] al client"),
                 s("llamadas entrantes", 0.5, r"recepci[óo]n de llamadas|\binbound\b|entrantes")],
        negativos=[NEG_VENTA_TELEFONICA, NEG_SOPORTE_IT],
    ),
    puesto(
        "atencion.agente", "agente de atención al cliente",
        es=["atención al cliente", "agente de atención al cliente"], en=["customer service"],
        ca=["atenció al client"],
        nucleo=(
            r"atenci[óo]n al (?:cliente|usuario|consumidor)|atenci[óo] al client|servicio al cliente"
            r"|customer (?:service|care|support|experience)\b|agente de (?:atenci[óo]n|servicio|clientes)"
            r"|gestor\w*(?:/a)? de clientes|customer (?:advisor|agent|representative|specialist)"
            r"|asesor\w*(?:/a)? de (?:atenci[óo]n|clientes)|guest (?:service|relations)"
        ),
        senales=[s("teléfono, chat o email", 0.5, r"telef[óo]n|\bchat\b|\bemail\b|omnicanal")],
        negativos=[
            NEG_VENTA_TELEFONICA, NEG_SOPORTE_IT,
            n("customer success o cuentas", -2.0, r"customer success|account|key account|cuentas",
              choca_con=["comercial.cuentas"]),
        ],
    ),
    puesto(
        "atencion.idiomas", "atención al cliente con idiomas",
        es=["atención al cliente inglés"],
        en=["customer service representative", "customer support", "customer care"],
        # El idioma lo tiene que decir el título: es lo que separa este puesto del de arriba, y es lo
        # que hace que el −4,0 de «idioma que no hablas» filtre el resto (quien habla francés y no
        # alemán ve los de francés). «Multilingual» solo delante del puesto: suelto casaba con
        # «Multilingual Training Administrator» o «Eaton is hiring multilingual talent» (base).
        nucleo=(
            r"(?:customer (?:service|care|support|experience)|atenci[óo]n al cliente|servicio al cliente"
            r"|customer (?:advisor|agent|representative|specialist)|agente|asesor\w*|teleoperador\w*|call ?cent\w*"
            r"|contact ?cent\w*)\W+(?:\w+\W+){0,6}?"
            r"(?:english|ingl[ée]s|french|franc[ée]s|german|alem[áa]n|italian\w*|italiano|portugu\w*"
            r"|dutch|neerland[ée]s|nordic|swedish|danish|norwegian|finnish|polish|czech|greek|turkish"
            r"|arabic|hebrew|japanese|korean|chinese|mandarin|russian|basque|catalan|galician|bilingual|biling[üu]e|native"
            r"|speaking|speaker)"
            r"|(?:english|french|german|italian|portuguese|dutch|swedish|danish|norwegian|finnish"
            r"|polish|czech|greek|turkish|arabic|hebrew|japanese|korean|russian|basque|catalan|bilingual|nordic)"
            r"[- ](?:speaking|speaker)\W+(?:\w+\W+){0,4}?(?:customer|support|service|agent|advisor"
            r"|representative)"
            r"|customer (?:service|support|care) representative"
            r"|multilingual (?:customer|support|agent|advisor|service)"
        ),
        senales=[s("idioma en el título", 1.0, r"english|ingl[ée]s|bilingual|biling|speaking|speaker")],
        negativos=[NEG_VENTA_TELEFONICA, NEG_SOPORTE_IT],
    ),
    puesto(
        "atencion.postventa", "postventa, incidencias y reclamaciones",
        es=["postventa", "gestor de reclamaciones", "gestión de incidencias"],
        nucleo=(
            r"post-?venta|after-?sales|gestor\w*(?:/a)? de (?:reclamaciones|incidencias|siniestros)"
            r"|gesti[óo]n de (?:reclamaciones|incidencias|siniestros)|reclamaciones"
            r"|claims (?:handler|specialist|agent|adjuster)|tramitador\w*(?:/a)? de siniestros"
            r"|devoluciones|returns (?:specialist|agent)"
        ),
        senales=[s("seguros", 0.5, r"seguro|siniestro|p[óo]liza"),
                 s("ecommerce", 0.5, r"e-?commerce|online|tienda")],
        negativos=[
            # «Gestión de incidencias» es también el vocabulario de ITIL.
            n("incidencias de IT", -3.0,
              r"\bit\b|inform[áa]tic|\bitil\b|sistemas|soporte t[ée]cnico|service desk|help ?desk|ciber"
              r"|security|seguridad",
              choca_con=["it"]),
            NEG_VENTA_TELEFONICA,
            # «Técnico/a de Automatización Postventa», «Aftersales Engineer» (base): servicio técnico.
            n("servicio técnico postventa", -2.0,
              r"t[ée]cnic|engineer|ingenier|automatizaci|mec[áa]nic|mantenimiento",
              choca_con=["industria", "oficios"]),
        ],
    ),
    puesto(
        "atencion.publico", "atención al público e información",
        es=["atención al público", "azafata", "informador"], ca=["atenció al públic"],
        nucleo=(
            r"atenci[óo]n? al p[úu]blic|atenci[óo] ciutadana|atenci[óo]n ciudadana|\binformador\w*"
            r"|\bazafat[oa]|\bhostess\b|personal de (?:informaci[óo]n|acogida)|auxiliar de informaci[óo]n"
            r"|punto de informaci[óo]n|taquiller\w*|personal de taquilla|acomodador\w*"
        ),
        senales=[s("eventos, ferias o museos", 0.5, r"evento|feria|congreso|museo|fira")],
        negativos=[NEG_TRIPULANTE, NEG_COMERCIAL],
    ),
])

# ======================================================================
# 4. COMERCIAL Y VENTAS
# ======================================================================
COMERCIAL = familia("comercial", "💼", "Comercial y ventas", puestos=[
    puesto(
        "comercial.campo", "comercial / técnico comercial",
        es=["comercial", "técnico comercial", "delegado comercial"], en=["sales representative"],
        ca=["comercial"], junior=["comercial junior"],
        # «Comercial» a secas es el puesto, pero también un adjetivo: «administrativo comercial» es otro
        # puesto (Administración) y lo baja su negativo. «Comercial de tienda» es Comercio, y
        # «Vigilante de Seguridad Centros Comerciales» (base) no es nadie de ventas.
        nucleo=(
            r"(?<!centros )(?<!centro )\bcomercial(?:es)?\b(?! de tienda)|t[ée]cnic\w*(?:/a)? comercial|delegad\w*(?:/a)? comercial"
            r"|agente comercial|representante comercial|ejecutiv\w*(?:/a)? comercial"
            r"|sales (?:representative|executive|rep\b|agent)|field sales|visitador\w*(?:/a)? m[ée]dic"
        ),
        senales=[s("B2B o cartera", 0.5, r"\bb2b\b|cartera|grandes cuentas|distribuci[óo]n"),
                 s("vehículo de empresa", 0.5, r"coche|veh[íi]culo de empresa")],
        negativos=[
            n("administrativo comercial (oficina)", -2.0,
              r"administrativ|back office|soporte comercial|apoyo comercial|asistente comercial"
              r"|gesti[óo]n de pedidos",
              choca_con=["admin"]),
            # Otro nombre que el «venta telefónica» de Atención al cliente: con el mismo nombre contarían
            # una vez y mandaría el patrón del primer puesto elegido.
            n("venta por teléfono", -1.5, r"tele-?(?:venta|operador|marketing)|telef[óo]nic|call ?cent",
              choca_con=["comercial.televenta", "comercial.asesor", "atencion.teleoperador"]),
            NEG_AUTONOMO,
        ],
    ),
    puesto(
        "comercial.televenta", "televenta",
        es=["televenta", "teleoperador ventas", "televendedor"],
        nucleo=(
            r"tele-?venta|televendedor\w*|venta telef[óo]nica|ventas telef[óo]nicas"
            r"|teleoperador\w*(?:/a)? (?:de )?(?:ventas|comercial|emisi[óo]n|outbound)"
            r"|agente (?:de )?televenta|telemarketing|outbound sales|comercial telef[óo]nic\w*"
            r"|televenda|captaci[óo]n telef[óo]nica|venda telef[òo]nica"
        ),
        senales=[s("emisión de llamadas", 0.5, r"emisi[óo]n|outbound")],
        negativos=[NEG_AUTONOMO],
    ),
    puesto(
        "comercial.inside", "inside sales / SDR / BDR",
        en=["sales development representative", "business development representative", "inside sales"],
        # Sin términos en castellano: es un puesto que en España se anuncia en inglés. Para quien no
        # habla inglés, el generador no lo podrá buscar, y lo dice (ver generador.py).
        es=[],
        nucleo=(
            r"\bsdr\b|\bbdr\b|sales development|business development (?:representative|associate|executive)"
            r"|inside sales|lead generation|account development|desarrollo de negocio|business developer"
            r"|new business (?:representative|associate|executive)"
        ),
        senales=[s("SaaS o tecnología", 0.5, r"\bsaas\b|software|\btech\b|cloud")],
        negativos=[n("venta en la calle", -2.0, r"puerta a puerta|\bcalle\b|de campo|field sales",
                     choca_con=["comercial.campo"])],
    ),
    puesto(
        "comercial.cuentas", "gestor/a de cuentas (account / KAM)",
        es=["gestor de cuentas", "ejecutivo de cuentas"],
        en=["account executive", "account manager", "key account manager"],
        nucleo=(
            r"account (?:executive|manager|management|director)|key account|\bkam\b"
            r"|gestor\w*(?:/a)? de cuentas|ejecutiv\w*(?:/a)? de cuentas|gesti[óo]n de cuentas"
            r"|customer success (?:manager|specialist|associate|advisor)|client (?:partner|executive)"
        ),
        senales=[s("SaaS o tecnología", 0.5, r"\bsaas\b|software|\btech\b|cloud"),
                 s("cartera de clientes", 0.5, r"cartera|portfolio")],
        negativos=[n("contabilidad", -4.0,
                     r"accountant|accounting|contab|accounts (?:payable|receivable)",
                     choca_con=["finanzas"])],
    ),
    puesto(
        "comercial.inmobiliario", "agente inmobiliario",
        es=["agente inmobiliario", "asesor inmobiliario", "comercial inmobiliario"],
        ca=["agent immobiliari"],
        nucleo=(
            r"inmobiliari|immobiliari|agente de la propiedad|real estate (?:agent|advisor|consultant|sales)"
            r"|asesor\w*(?:/a)? (?:de )?(?:la )?vivienda|captador\w*(?:/a)? inmobiliari"
            r"|comercial de (?:obra nueva|promociones|viviendas)|property (?:consultant|advisor)"
        ),
        senales=[s("captación", 0.5, r"captaci[óo]n|captador")],
        negativos=[
            n("administración de fincas o administrativo", -2.0,
              r"administrativ|administraci[óo]n de fincas|contab|recepci",
              choca_con=["admin"]),
            NEG_AUTONOMO,
        ],
    ),
    puesto(
        "comercial.asesor", "asesor/a comercial (seguros, energía, banca)",
        es=["asesor comercial", "agente de seguros", "asesor energético"], ca=["assessor comercial"],
        nucleo=(
            r"asesor\w*(?:/a)? comercial|assessor\w*(?:/a)? comercial|agente (?:de )?seguros|agente exclusiv"
            r"|asesor\w*(?:/a)? (?:de )?seguros|mediador\w*(?:/a)? de seguros|asesor\w*(?:/a)? energ[ée]tic"
            r"|comercial de (?:energ[íi]a|seguros|telecomunicaciones|fibra|telefon[íi]a|alarmas|placas"
            r"|renovables)|gestor\w*(?:/a)? comercial|insurance (?:agent|advisor|sales)"
        ),
        senales=[s("seguros o banca", 0.5, r"seguro|banc|financ")],
        negativos=[NEG_AUTONOMO],
    ),
])

# ======================================================================
# 5. MARKETING Y COMUNICACIÓN
# ======================================================================
NEG_DESARROLLO_WEB = n("desarrollo de software", -2.0, _DESARROLLO_TITULO, choca_con=["it.desarrollo"])

MARKETING = familia("marketing", "📣", "Marketing y comunicación", puestos=[
    puesto(
        "marketing.digital", "marketing digital",
        es=["marketing digital", "técnico de marketing"], en=["digital marketing"],
        ca=["tècnic de màrqueting"], junior=["marketing junior"],
        # Ancho a propósito: «marketing» en un título es casi siempre marketing. Con `\b`, para que
        # «Telemarketing» (televenta) no cuente.
        nucleo=(r"\bmarketing\b|\bm[àa]rqueting\b|\bgrowth (?:hacker|specialist|manager|marketer)"
                r"|\bmarketer\b"),
        senales=[s("digital", 1.0,
                   r"digital|online|\bads\b|google|\bmeta\b|social|\bseo\b|\bsem\b|\bcrm\b|automation")],
        negativos=[
            n("ventas", -2.0,
              r"\bsales\b|\bventas\b|comercial|account executive|telemarketing|televent",
              choca_con=["comercial"]),
            NEG_DESARROLLO_WEB,
        ],
    ),
    puesto(
        "marketing.social", "community manager / redes sociales",
        es=["community manager", "social media"], en=["social media"],
        junior=["social media junior"],
        nucleo=(
            r"community manager|social media|redes sociales|xarxes socials|\bsmm\b"
            r"|content (?:creator|manager)|creador\w*(?:/a)? de contenido|gestor\w*(?:/a)? de (?:redes|comunidad)"
            r"|tiktok|instagram|influencer marketing|social ads"
        ),
        senales=[s("diseño o vídeo", 0.5, r"dise[ñn]|v[íi]deo|fotograf|canva|adobe")],
        negativos=[
            NEG_COMERCIAL,
            # Moderar contenidos es un puesto de BPO, no de marketing.
            n("moderación de contenidos", -3.0,
              r"moderad|content moderat|revisor\w* de contenido|content review|trust (?:and|&) safety"),
        ],
    ),
    puesto(
        "marketing.seo", "SEO / SEM / paid media",
        es=["seo", "sem", "paid media"], en=["seo specialist", "ppc"], junior=["seo junior"],
        # Sin «SEA»: casa con «Sea freight» (transporte marítimo). Y `\bsearch engine`: sin el `\b`,
        # «Security Research Engineering Technical Leader» (base) casaba por «re-search engine-ering».
        nucleo=(
            r"\bseo\b|\bsem\b|\bppc\b|paid (?:media|social|search)|performance marketing|google ads"
            r"|\bsearch engine|posicionamiento web|m[àa]rqueting en cercadors|traffic manager|media buyer"
            r"|programmatic"
        ),
        senales=[s("analítica web", 0.5, r"analytics|anal[íi]tica|\bga4\b|looker")],
        negativos=[NEG_DESARROLLO_WEB],
    ),
    puesto(
        "marketing.contenidos", "contenidos y redacción",
        es=["redactor", "copywriter", "creador de contenidos"], en=["content creator", "copywriter"],
        ca=["redactor"],
        nucleo=(
            r"\bredactor\w*|copywriter|content (?:writer|creator|editor|specialist|strategist)"
            r"|creador\w*(?:/a)? de contenido|creaci[óo]n de contenido|editor\w*(?:/a)? de contenido"
            r"|gui[óo]nista|corrector\w*(?:/a)? de (?:textos|estilo)|\bescritor\w*|\bblogger\b|ghostwriter"
            r"|technical writer|redacci[óo]"
        ),
        senales=[s("SEO", 0.5, r"\bseo\b")],
        negativos=[n("moderación de contenidos", -3.0,
                     r"moderad|content moderat|revisor\w* de contenido|content review"
                     r"|trust (?:and|&) safety")],
    ),
    puesto(
        "marketing.comunicacion", "comunicación y prensa",
        es=["técnico de comunicación", "periodista", "prensa"], en=["communications"],
        ca=["tècnic de comunicació"],
        # `\bcomunicaci[óo]n\b`: sin el primer `\b`, «Telecomunicación»; sin el segundo, «Ingeniero de
        # comunicaciones», que es de redes. Las dos están en la base.
        nucleo=(
            r"\bcomunicaci[óo]n\b|\bcomunicaci[óo]\b|communications? (?:specialist|officer|manager|assistant"
            r"|coordinator|executive|associate)|internal communications|relaciones p[úu]blicas"
            r"|public relations|\bpr (?:specialist|executive|assistant|manager)|\bperiodist\w*"
            r"|\bprensa\b|gabinete de prensa|press (?:officer|office)|\bportavoz\b"
        ),
        senales=[s("corporativa o institucional", 0.5, r"corporativ|institucional|interna|externa")],
        negativos=[
            # «Operario/a de prensa»: la máquina.
            n("prensa industrial", -4.0,
              r"operari|prensa (?:hidr|industrial|de inyecci|de estampaci)|estampaci|troquel|maquinista",
              choca_con=["industria.operario"]),
            # «Infrastructure Security Communication Specialist» (base).
            n("telecomunicaciones, redes o IT", -3.0,
              r"\bredes\b|sistemas|telecom|unificadas|ingenier|audiovisual|security|seguridad"
              r"|infraestructur|infrastructure",
              choca_con=["it.telecom", "it.sistemas"]),
        ],
    ),
    puesto(
        "marketing.diseno", "diseño gráfico",
        es=["diseñador gráfico", "diseñador web"], en=["graphic designer"], ca=["dissenyador gràfic"],
        junior=["diseñador gráfico junior"],
        # «Diseñador/a Técnico/a» o «Diseñador/a Civil» (los dos en la base) son oficina técnica: el
        # núcleo pide el adjetivo de diseño gráfico.
        nucleo=(
            r"dise[ñn]ador\w*(?:/a)? (?:gr[áa]fic|web|digital|visual|multimedia|ux|ui\b|de interfaces"
            r"|de producto digital|de packaging|editorial|de moda)|dise[ñn]o gr[áa]fico|graphic designer"
            r"|visual designer|web designer|\bux ?/ ?ui\b|\bui ?/ ?ux\b|\bux designer|\bui designer"
            r"|product designer|motion (?:designer|graphics)|dissenyador\w*(?:/a)? gr[àa]fic"
            r"|\bilustrador\w*|\billustrator\b(?! (?:cc|adobe))"
        ),
        senales=[s("Adobe o Figma", 0.5, r"adobe|figma|photoshop|illustrator|indesign|after effects")],
        negativos=[
            n("diseño industrial o técnico", -3.0,
              r"industrial|mec[áa]nic|t[ée]cnic|\bcad\b|civil|fotovolt|estructur|el[ée]ctric|interiores"
              r"|mueble|moldes",
              choca_con=["industria"]),
            NEG_DESARROLLO_WEB,
        ],
    ),
    puesto(
        "marketing.ecommerce", "e-commerce",
        es=["ecommerce", "tienda online"], en=["ecommerce specialist"],
        nucleo=(r"e-?commerce|comercio electr[òóo]nic|tienda online|online store|(?<!cloud )marketplace"
                r"|amazon (?:specialist|account|seller|vendor)|shopify|prestashop|magento|woocommerce"),
        senales=[s("marketplaces", 0.5, r"amazon|marketplace|zalando|miravia")],
        negativos=[
            n("almacén de e-commerce", -3.0,
              r"almac[ée]n|\bmozo|picking|log[íi]stic|preparador|repartidor|warehouse",
              choca_con=["logistica"]),
            NEG_DESARROLLO_WEB,
        ],
    ),
    puesto(
        "marketing.traduccion", "traducción e interpretación",
        es=["traductor", "traductora", "intérprete", "localización"],
        en=["translator", "interpreter", "localization"], ca=["traductor", "traductora"],
        junior=["traductor junior"], tambien_en=["educacion"],
        # Sin «interpretación» ni «localización» a secas: «interpretación de planos» y «localización de
        # averías» son de taller. Los idiomas del título los juzga el −4,0 de «idioma que no hablas»:
        # «Traductor alemán-español» no le llega a quien no habla alemán, y sí a quien lo habla.
        nucleo=(
            r"\btraductor\w*|\btraducci[óo]n?\b|translator|translation|\bint[ée]rprete|\bint[èe]rpret\b"
            r"|\binterpreter|interpretaci[óo]n (?:simult[áa]nea|consecutiva|de lengua de signos|jurada)"
            r"|locali[sz]ation|localizador\w*(?:/a)? de (?:contenido|software|videojuegos)|transcreat\w*"
            r"|revisor\w*(?:/a)? (?:ling[üu][íi]stic|de traducci)|subtitulad\w*|post-?editor\w*"
            r"|\blinguist\b|\bling[üu]ista\b"
        ),
        senales=[s("jurada o especializada", 0.5, r"jurad|t[ée]cnic|legal|m[ée]dic|audiovisual|sworn")],
        negativos=[
            NEG_COMERCIAL,
            n("atención al cliente con idiomas", -1.5,
              r"customer|atenci[óo]n al cliente|call ?cent|contact ?cent|support",
              choca_con=["atencion.idiomas", "atencion.agente"]),
        ],
    ),
])

# ======================================================================
# 6. FINANZAS Y CONTABILIDAD
# ======================================================================
# «Accountant Technical Business Analyst», «Accounting Business Analyst – Treasury Systems», «Finance
# Systems Specialist»: la base está llena de puestos de IT con palabras de finanzas.
NEG_SISTEMAS_FINANCIEROS = n(
    "sistemas financieros (IT)", -3.0,
    r"business analyst|\bsystems?\b|sistemas|software|developer|desarrollador|consultant|consultor"
    r"|functional|funcional|\bsap\b|\berp\b|implementation|platform",
    choca_con=["it"])

FINANZAS = familia("finanzas", "💶", "Finanzas y contabilidad", puestos=[
    puesto(
        "finanzas.contable", "contable / auxiliar contable",
        es=["contable", "auxiliar contable", "administrativo contable"], en=["accountant"],
        ca=["comptable"], junior=["contable junior"],
        nucleo=(
            r"\bcontables?\b|\bcomptable|contabilidad|comptabilitat|accountant|bookkeep\w*"
            r"|accounting (?:assistant|clerk|specialist|analyst|associate|officer|manager)"
            r"|general ledger|\bgl (?:accountant|specialist)|cierre contable"
        ),
        senales=[s("programa contable", 0.5, r"\ba3\b|\bsage\b|\bsap\b|navision|holded|contaplus"),
                 s("fiscal e impuestos", 0.5, r"fiscal|impuest|\biva\b|\btax\b")],
        negativos=[NEG_SISTEMAS_FINANCIEROS],
    ),
    puesto(
        "finanzas.fiscal", "asesoría fiscal y gestoría",
        es=["asesor fiscal", "técnico fiscal", "gestoría"], en=["tax"], ca=["assessor fiscal"],
        nucleo=(
            r"asesor\w*(?:/a)? (?:fiscal|contable|laboral|tributari)|assessor\w*(?:/a)? fiscal"
            r"|t[ée]cnic\w*(?:/a)? (?:de |en )?(?:fiscal|tributari|impuestos)|fiscalista|\btax\b"
            r"|gestor[íi]a|asesor[íi]a (?:fiscal|contable|laboral|de empresas)|departamento fiscal"
        ),
        senales=[s("impuestos de sociedades o IRPF", 0.5,
                   r"sociedades|irpf|\biva\b|declaraci|renta|impuestos")],
        negativos=[NEG_SISTEMAS_FINANCIEROS],
    ),
    puesto(
        "finanzas.apar", "cuentas a pagar / cobrar y facturación",
        es=["facturación", "cuentas a pagar", "cuentas a cobrar"],
        en=["accounts payable", "accounts receivable"], ca=["facturació"],
        nucleo=(
            r"accounts? (?:payable|receivable)|\bap ?(?:/|&) ?ar\b|\bar ?(?:/|&) ?ap\b|\b(?:ap|ar) specialist"
            r"|cuentas (?:a|por) (?:pagar|cobrar)|\bfacturaci[óo]n?\b|billing (?:specialist|clerk|assistant"
            r"|coordinator)|invoic\w*|credit (?:controller|collection)|gesti[óo]n de cobros?|\bcobros\b"
            r"|recobro|collections? (?:specialist|agent|analyst)|order to cash|procure to pay"
            r"|purchase to pay|record to report|\b(?:o2c|p2p|r2r)\b"
        ),
        senales=[S_ERP,
                 s("centro de servicios compartidos", 0.5, r"shared service|\bssc\b|\bgbs\b|\bbpo\b")],
        negativos=[NEG_SISTEMAS_FINANCIEROS],
    ),
    puesto(
        "finanzas.control", "controller y análisis financiero",
        es=["controller", "analista financiero", "control de gestión"],
        en=["financial analyst", "fp&a"], junior=["controller junior"],
        nucleo=(
            r"\bcontroller\b|\bcontrolling\b|control de gesti[óo]n|analista financier\w*"
            r"|financial (?:analyst|controller|planning|reporting)|\bfp ?& ?a\b"
            r"|planificaci[óo]n financiera|(?<!soporte )t[ée]cnic\w*(?:/a)? (?:de |en )?(?:finanzas|financier\w*)"
            r"|analista de (?:costes|inversiones|riesgos financieros)|\btesorer[íi]a|treasury (?:analyst|specialist)"
            r"|cost (?:controller|analyst)|business controller"
            r"|finance (?:analyst|associate|specialist|business partner)"
        ),
        senales=[s("Excel o reporting", 0.5, r"excel|power bi|reporting|\bbi\b"),
                 s("costes y presupuestos", 0.5, r"costes|\bcost\b|presupuest|budget")],
        negativos=[NEG_SISTEMAS_FINANCIEROS],
    ),
    puesto(
        "finanzas.auditoria", "auditoría financiera",
        es=["auditor", "auditoría"], en=["audit associate"], ca=["auditor"],
        junior=["auditor junior"],
        nucleo=(r"\bauditor\w*|auditor[íi]a|\baudit\b|\bassurance (?:associate|assistant)"
                r"|revisor\w*(?:/a)? de cuentas"),
        senales=[s("cuentas anuales", 0.5, r"cuentas|financier|contable|financial")],
        negativos=[
            n("auditoría de sistemas o ciberseguridad", -3.0,
              r"\bit\b|inform[áa]tic|sistemas|systems|ciber|cyber|security|seguridad inform"
              r"|iso ?27001|\bens\b|hacking|pentest|tecnol[óo]gic",
              choca_con=["it"]),
            n("auditoría de calidad, ambiental o de PRL", -3.0,
              r"calidad|quality|iso ?9001|energ[ée]tic|ambiental|medio ?ambiente|alimentari|\bbrc\b"
              r"|\bifs\b|\bprl\b|\blean\b",
              choca_con=["industria.calidad", "industria.prl"]),
            # El «night auditor» es el recepcionista de noche de un hotel.
            n("auditor nocturno de hotel", -4.0, r"\bnight\b|nocturn|hotel|recepci",
              choca_con=["hosteleria.recepcion"]),
        ],
    ),
])

# ======================================================================
# 7. RECURSOS HUMANOS
# ======================================================================
NEG_SISTEMAS_RRHH = n(
    "sistemas de RRHH (IT)", -3.0,
    r"\bsupport\b|soporte|developer|desarrollador|consultant|consultor\w*(?:/a)? (?:sap|funcional|t[ée]cnic)"
    r"|software|\bsystems?\b|sistemas|\bsap\b|implementation|technical|\bproduct\b",
    choca_con=["it"])

RRHH = familia("rrhh", "👥", "Recursos humanos", puestos=[
    puesto(
        "rrhh.seleccion", "selección / recruiter",
        es=["técnico de selección", "reclutador"], en=["recruiter", "talent acquisition"],
        ca=["tècnic de selecció"], junior=["recruiter junior"],
        nucleo=(
            r"\brecruit\w*|reclutador\w*|reclutamiento|talent acquisition|t[éèe]cnic\w*(?:/a)? (?:de |en )?"
            r"selecci[óo]n?\b|consultor\w*(?:/a)? de selecci[óo]n|selecci[óo]n de personal|headhunter"
            r"|\bsourcer\b|talent (?:partner|sourcer|specialist)|captaci[óo]n de talento"
        ),
        senales=[s("perfiles técnicos", 0.5, r"\bit\b|\btech\b|t[ée]cnic|ingenier|engineering"),
                 s("ETT o consultora", 0.5, r"\bett\b|consultora|agencia|staffing")],
        negativos=[n("comercial de ETT", -2.0,
                     r"comercial|ventas|\bsales\b|business development|key account",
                     choca_con=["comercial"])],
    ),
    puesto(
        "rrhh.personal", "administración de personal y nóminas",
        es=["técnico de nóminas", "administración de personal"], en=["payroll"],
        ca=["tècnic de nòmines"],
        nucleo=(
            r"\bn[óo]minas?\b|\bn[òo]mines\b|payroll|administraci[óo]n? de personal|personnel administration"
            r"|t[ée]cnic\w*(?:/a)? laboral|gestor\w*(?:/a)? laboral|asesor\w*(?:/a)? laboral|graduad\w*(?:/a)? social"
            r"|compensation (?:and|&) benefits|\bc ?& ?b\b|compensaci[óo]n y beneficios"
        ),
        senales=[s("programa de nóminas", 0.5,
                   r"\ba3\b|meta4|\bsage\b|sap hr|successfactors|workday|cegid")],
        negativos=[NEG_SISTEMAS_RRHH],
    ),
    puesto(
        "rrhh.generalista", "técnico/a de RRHH",
        # `(?<!/)\bhr\b`: «Business Analyst (Excel) | $60/hr Remote» y otros tres por hora casaban con
        # «HR» en la base (medido el 04-11: 9 de 17 eran de verdad de RRHH antes de esto).
        es=["técnico de recursos humanos", "técnico rrhh", "administrativo rrhh"],
        en=["hr assistant", "hr generalist"], ca=["tècnic de recursos humans"],
        nucleo=(
            r"recursos humanos|recursos humans|\brrhh\b|\brr\.? ?hh\b|human resources|(?<!/)\bhr\b|\bhrbp\b"
            r"|people (?:partner|operations|specialist|coordinator)|personas y cultura"
            r"|people (?:&|and) culture|employee experience"
        ),
        senales=[s("relaciones laborales", 0.5, r"laboral|relaciones|convenio|onboarding")],
        negativos=[NEG_SISTEMAS_RRHH, NEG_COMERCIAL],
    ),
    puesto(
        "rrhh.formacion", "formación y desarrollo",
        es=["técnico de formación"], en=["learning and development"], ca=["tècnic de formació"],
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?formaci[óo]n?\b|learning (?:&|and) development|\bl ?& ?d\b"
            r"|learning (?:specialist|partner|coordinator|designer)|instructional designer"
            r"|dise[ñn]ador\w*(?:/a)? instruccional|coordinador\w*(?:/a)? de formaci[óo]n"
            r"|gestor\w*(?:/a)? de formaci[óo]n|training (?:coordinator|specialist|manager|partner)"
            r"|formaci[óo]n (?:y|&) desarrollo|desarrollo de talento|talent development"
        ),
        senales=[s("e-learning", 0.5, r"e-?learning|moodle|\blms\b|online")],
        negativos=[NEG_COMERCIAL],
    ),
    puesto(
        "rrhh.orientacion", "orientación laboral e inserción",
        es=["orientador laboral", "técnico de empleo", "técnico de inserción"],
        ca=["orientador laboral", "tècnic d'ocupació"],
        nucleo=(
            r"orientador\w*(?:/a)? (?:laboral|profesional|sociolaboral|professional)"
            r"|orientaci[óo]n? (?:laboral|profesional|sociolaboral|professional)"
            r"|t[éèe]cnic\w*(?:/a)? (?:de |d'|en )?(?:empleo|ocupaci[óo]|inserci[óo]n|intermediaci[óo]n)"
            r"|prospector\w*(?:/a)? (?:laboral|de empresas)|\binsertor\w*|job coach"
            r"|preparador\w*(?:/a)? laboral|agente de (?:empleo|desarrollo local)|agent\w* d'ocupaci[óo]"
            r"|t[ée]cnic\w*(?:/a)? de integraci[óo]n laboral"
        ),
        senales=[s("colectivos vulnerables", 0.5,
                   r"vulnerab|discapacidad|juventud|j[óo]venes|inmigra|exclusi|garant[íi]a juvenil")],
        negativos=[NEG_COMERCIAL],
    ),
])

# ======================================================================
# 8. SANIDAD Y CUIDADOS
# ======================================================================
NEG_INDUSTRIA_FARMA = n(
    "industria farmacéutica", -3.0,
    r"industria|calidad|producci[óo]n|\bplanta\b|\bgmp\b|validaci|regulatory|qa\b|quality|manufactur",
    choca_con=["industria"])

SANIDAD = familia("sanidad", "🏥", "Sanidad y cuidados", puestos=[
    puesto(
        "sanidad.enfermeria", "enfermero/a",
        es=["enfermero", "enfermera"], ca=["infermer", "infermera"],
        junior=["enfermera sin experiencia"],
        # Con cuidado con la auxiliar: «Aux. enfermería TCAE» y «Tècnic/a en cures auxiliars
        # d'infermeria» (las dos en la base) son TCAE, y «enfermería» va dentro. Sin «DUE»: casaba con
        # «Due Diligence».
        nucleo=(
            r"\benfermer[oa]s?\b|\benfermer[oa]s?/|\benfermer[oa]\(|(?<!auxiliar de )(?<!auxiliares de )"
            r"(?<!aux\. )(?<!aux )(?<!cuidados auxiliares de )\benfermer[íi]a\b|\binfermer[ae]?s?\b"
            r"|grau en infermeria|graduad\w*(?:/a)? en (?:enfermer[íi]a|infermeria)|\bnurse\b"
            r"|\bmatr[óo]n[ae]?s?\b|\bllevador[ae]?\b"
        ),
        senales=[s("hospital o especialidad", 1.0,
                   r"hospital|cl[íi]nic|centro (?:m[ée]dico|de salud)|residencia|quir[óo]fano|\buci\b"
                   r"|urgencias|pediatr|geriatr|salud mental")],
        negativos=[NEG_COMERCIAL, NEG_DOCENCIA, NEG_INFORMATICA],
    ),
    puesto(
        "sanidad.tcae", "TCAE / auxiliar de enfermería",
        es=["tcae", "auxiliar de enfermería"], ca=["tcai", "auxiliar d'infermeria"],
        nucleo=(
            r"\btcae\b|\btcai\b|auxiliar\w*(?:/a)? de enfermer[íi]a|\baux\.? (?:de )?enfermer[íi]a"
            r"|t[ée]cnic\w*(?:/a)? (?:en |de )?cuidados (?:auxiliares )?(?:de )?enfermer[íi]a"
            r"|t[èe]cnic\w*(?:/a)? en cures auxiliars|auxiliar\w*(?:/a)? d'infermeria"
            r"|cures auxiliars d'infermeria|auxiliar de cl[íi]nica|auxiliar sanitari\w*|nursing assistant"
        ),
        senales=[s("hospital o residencia", 1.0,
                   r"hospital|cl[íi]nic|centro (?:m[ée]dico|de salud)|residencia|planta|urgencias")],
        negativos=[
            n("clínica dental", -3.0, r"dental|odontol|bucodental", choca_con=["sanidad.dental"]),
            n("veterinaria", -4.0, r"veterinari"),
            NEG_COMERCIAL, NEG_INFORMATICA,
        ],
    ),
    puesto(
        "sanidad.gerocultor", "gerocultor/a",
        es=["gerocultor", "gerocultora"], ca=["gerocultor"], junior=["gerocultora sin experiencia"],
        nucleo=(
            r"gerocultor|auxiliar\w*(?:/a)? (?:de )?geriatr[íi]a|\baux\.? geriatr"
            r"|auxiliar\w*(?:/a)? de (?:residencia|atenci[óo]n sociosanitaria)|atenci[óo]n sociosanitaria"
            r"|atenci[óo] sociosanit[àa]ria|cuidador\w*(?:/a)? (?:de )?(?:residencia|centro de d[íi]a)"
        ),
        senales=[s("residencia o centro de día", 1.0,
                   r"residencia|resid[èe]ncia|centro de d[íi]a|centre de|mayores|gent gran")],
        negativos=[NEG_COMERCIAL],
    ),
    puesto(
        "sanidad.domicilio", "ayuda a domicilio y cuidados",
        es=["auxiliar ayuda a domicilio", "cuidadora"], ca=["treballadora familiar"],
        nucleo=(
            r"ayuda a domicilio|\bsad\b|auxiliar\w*(?:/a)? (?:de )?(?:ayuda a )?domicilio|\bcuidador\w*"
            r"|\bcuidadora|atenci[óo]n domiciliaria|asistente personal|asistencia personal"
            r"|treballador\w*(?:/a)? familiar|auxiliar de la llar|(?:cuidador\w*|empleada|auxiliar)\w*(?:/a)? interna"
            r"|acompa[ñn]ante de (?:personas|mayores)|caregiver|\bhome care\b|cuidado de (?:personas?|mayores)"
            r"|interna para (?:cuidado|cuidar)"
        ),
        senales=[s("personas dependientes", 0.5, r"dependen|mayores|discapacidad|alzheimer")],
        negativos=[
            n("cuidado de animales", -4.0, r"animal|\bperros?\b|mascota|canin|veterinari|\bzoo"),
            n("cuidado de niños", -2.0, r"ni[ñn][oa]s|infantil|beb[ée]|canguro|ni[ñn]er",
              choca_con=["educacion.ninera", "educacion.infantil"]),
            n("asistente de dirección", -3.0, r"direcci[óo]n|executive|\bceo\b|director|ejecutiv",
              choca_con=["admin.direccion"]),
        ],
    ),
    puesto(
        "sanidad.farmacia", "farmacia (técnico/a y farmacéutico/a)",
        es=["técnico de farmacia", "auxiliar de farmacia", "farmacéutico"], ca=["tècnic de farmàcia"],
        nucleo=(
            r"t[ée]cnic\w*(?:/a)? (?:en |de )?farmacia|t[èe]cnic\w*(?:/a)? de farm[àa]cia"
            r"|auxiliar\w*(?:/a)? de farmacia|farmac[ée]utic\w*|farmac[èe]utic\w*|\bpharmacist\b"
            r"|pharmacy (?:technician|assistant)|farmacia (?:hospitalaria|comunitaria)|parafarmacia"
            r"|dependient\w*(?:/a)? de (?:para)?farmacia|\bmancebo\b"
        ),
        senales=[s("oficina de farmacia", 0.5, r"oficina de farmacia|parafarmacia|dermo|ortopedia")],
        negativos=[NEG_INDUSTRIA_FARMA, NEG_COMERCIAL],
    ),
    puesto(
        "sanidad.fisio", "fisioterapeuta",
        es=["fisioterapeuta"], ca=["fisioterapeuta"],
        nucleo=(r"fisioterapeut|fisioterapia|physiotherap\w*|physical therapist|\bfisio\b|oste[óo]pat"
                r"|kinesi[óo]log|readaptador"),
        senales=[s("especialidad", 0.5,
                   r"deportiv|suelo p[ée]lvico|neurol|respiratori|pedi[áa]tric|traumatol")],
        negativos=[NEG_COMERCIAL, NEG_DOCENCIA],
    ),
    puesto(
        "sanidad.tecnicos", "laboratorio clínico y radiología",
        es=["técnico de laboratorio", "técnico de radiodiagnóstico"], ca=["tècnic de laboratori"],
        nucleo=(
            r"t[ée]cnic\w*(?:/a)? (?:superior )?(?:de |en )?(?:laboratorio|radiodiagn[óo]stico|radiolog[íi]a"
            r"|radioterapia|imagen para el diagn[óo]stico|anatom[íi]a patol[óo]gica|medicina nuclear"
            r"|rayos|rx\b|tac\b|resonancia)|\btsid\b|t[èe]cnic\w*(?:/a)? (?:superior )?(?:de |en )?"
            r"(?:laboratori|radiodiagn[òo]stic|imatge)|\bradi[óo]graf|radiographer|analista cl[íi]nic"
            r"|\bflebotom\w*"
        ),
        senales=[s("hospital o centro médico", 0.5, r"hospital|cl[íi]nic|centro m[ée]dico|sanitari")],
        negativos=[
            n("laboratorio industrial o químico", -3.0,
              r"qu[íi]mic|alimentari|industri|calidad|producci[óo]n|materiales|medioambient|\bagua\b"
              r"|petro|cosm[ée]t|pharma|farmac[ée]utic|i\+d|microbiolog[íi]a de alimentos|el[ée]ctric|iluminaci",
              choca_con=["industria"]),
            NEG_COMERCIAL,
        ],
    ),
    puesto(
        "sanidad.dental", "clínica dental",
        es=["auxiliar de clínica dental", "higienista dental"], ca=["auxiliar dental"],
        nucleo=(
            r"auxiliar\w*(?:/a)? (?:de )?(?:cl[íi]nica )?dental|higienista (?:dental|bucodental)"
            r"|\bhigienista\b(?! industrial)|higiene (?:bucodental|dental)"
            r"|t[ée]cnic\w*(?:/a)? (?:superior )?(?:en |de )?(?:higiene bucodental|pr[óo]tesis dental)"
            r"|prot[ée]sico dental|dental (?:assistant|hygienist|nurse)|\bodont[óo]log\w*|\bdentista"
            r"|ortodoncista|recepcionista (?:de )?(?:cl[íi]nica )?dental|cl[íi]nica dental"
        ),
        senales=[s("ortodoncia o implantes", 0.5, r"ortodon|implant|cirug")],
        negativos=[NEG_COMERCIAL,
                   n("higiene industrial", -4.0, r"industrial|\bprl\b|prevenci|ocupacional|laboral",
                     choca_con=["industria.prl"])],
    ),
])

# ======================================================================
# 9. EDUCACIÓN Y FORMACIÓN
# ======================================================================
NEG_DEPORTIVO = n(
    "monitor o entrenador deportivo", -2.0,
    r"deportiv|nataci[óo]n|aquagym|fitness|gimnasio|socorrist|p[áa]del|tenis|f[úu]tbol|esqu[íi]|yoga"
    r"|pilates|personal trainer|entrenador")

EDUCACION = familia("educacion", "🎓", "Educación y formación", puestos=[
    puesto(
        "educacion.profesor", "profesor/a (secundaria, FP, academia)",
        # `(?<!no )`: «Candidaturas espontáneas - Personal no docente» (base).
        es=["profesor", "docente", "profesor academia"], ca=["professor"],
        nucleo=(
            r"\bprofesor\w*|\bprofessor\w*|(?<!no )\bdocentes?\b|\bdocent\b|\bteacher\b|profesorado"
            r"|tutor\w*(?:/a)? (?:acad[ée]mic|escolar|de refuerzo)|\blecturer\b"
            r"|maestr[oa]\w*(?:/a)? (?:de )?(?:primaria|secundaria|educaci[óo]n (?:f[íi]sica|primaria|especial"
            r"|musical)|m[úu]sica|ingl[ée]s)|\bmestr[ae]s? (?:de |d')?(?:prim[àa]ria|m[úu]sica|angl[èe]s)"
            r"|clases (?:particulares|de refuerzo)|refuerzo escolar"
        ),
        senales=[s("FP, academia o secundaria", 0.5,
                   r"\bfp\b|formaci[óo]n profesional|academia|bachillerato|secundaria|\beso\b|ciclo"),
                 s("asignatura", 0.5,
                   r"matem[áa]tic|f[íi]sica|qu[íi]mica|lengua|historia|inform[áa]tic|tecnolog|ciencias")],
        negativos=[n("comercial de formación", -3.0,
                     r"comercial|ventas|asesor\w*(?:/a)? (?:de )?formaci|captaci|\bsales\b|admisiones",
                     choca_con=["comercial"])],
    ),
    puesto(
        "educacion.infantil", "educador/a infantil",
        es=["educadora infantil", "técnico educación infantil", "maestra infantil"],
        ca=["educadora infantil", "mestra"],
        nucleo=(
            r"educador\w*(?:/a)? (?:de )?(?:infantil|0-3|guarder[íi]a|escuela infantil)"
            r"|t[éèe]cnic\w*(?:/a)? (?:superior )?(?:en |de |d')?educaci[óo]n? infantil"
            r"|maestr[oa]\w*(?:/a)? (?:de )?(?:educaci[óo]n )?infantil|mestr[ae]\w* (?:d'|de )?(?:educaci[óo] )?infantil"
            r"|escuela infantil|escola bressol|guarder[íi]a|auxiliar\w*(?:/a)? (?:de )?(?:educaci[óo]n )?infantil"
            r"|early years|nursery (?:teacher|practitioner)|preschool teacher|\bludoteca"
        ),
        senales=[s("0-3 años", 0.5, r"0-3|0 a 3|bressol|guarder|beb[ée]s")],
        negativos=[n("niñera en domicilio", -1.5, r"ni[ñn]era|canguro|au pair|nanny|domicilio|familia",
                     choca_con=["educacion.ninera"])],
    ),
    puesto(
        "educacion.idiomas", "profesor/a de idiomas",
        es=["profesor de inglés"], en=["english teacher"], ca=["professor d'anglès"],
        nucleo=(
            r"profesor\w*(?:/a)? (?:de |nativ\w* de )?(?:ingl[ée]s|franc[ée]s|alem[áa]n|italiano|portugu[ée]s"
            r"|chino|espa[ñn]ol|idiomas|lengua extranjera|\bele\b)|professor\w*(?:/a)? d'(?:angl[èe]s"
            r"|franc[èe]s|alemany|idiomes)|(?:english|french|german|spanish|\besl|\befl|language) teacher"
            r"|teacher of english|\btefl\b|\bcelta\b|auxiliar de conversaci[óo]n|language assistant"
            r"|english tutor|profesor\w*(?:/a)? nativ|conversation (?:teacher|assistant)"
        ),
        senales=[s("certificación docente", 0.5, r"celta|tefl|tesol|delta|cambridge|nativ")],
        negativos=[n("atención al cliente o ventas con idiomas", -3.0,
                     r"customer|atenci[óo]n al cliente|call ?cent|\bsales\b|ventas",
                     choca_con=["atencion", "comercial"])],
    ),
    puesto(
        "educacion.formador", "formador/a",
        es=["formador", "formadora"], en=["trainer"], ca=["formador"],
        # «Formador/a de sistemas de alarmas» y «Formador/a – Instalador/a PMS» (base) SON formadores:
        # enseñan un oficio. El que no lo es, el entrenador personal, lo baja su negativo.
        nucleo=(
            r"\bformador\w*|\bformadora|\btrainer\b|\binstructor\w*|monitor\w*(?:/a)? de formaci[óo]n"
            r"|docente de (?:formaci[óo]n|certificados|cursos)|\bfacilitador\w*"
        ),
        senales=[s("formación para el empleo", 0.5,
                   r"certificado de profesionalidad|\bsepe\b|formaci[óo]n para el empleo|\bfpe\b"
                   r"|ocupacional|bonificad"),
                 s("e-learning", 0.5, r"e-?learning|moodle|\blms\b|online")],
        negativos=[NEG_DEPORTIVO, NEG_COMERCIAL],
    ),
    puesto(
        "educacion.monitor", "monitor/a de ocio y comedor",
        es=["monitor de comedor", "monitor de tiempo libre"],
        ca=["monitor de menjador", "monitor de lleure"],
        # `\bmonitor(?:a|es|as)?\b` y no `monitor\w*`: con `\w*` casaban «Técnico/a de Monitorización
        # (24x7)», «Information Security Functional Monitoring» o «Application Security Monitoring»,
        # los tres en la base.
        nucleo=(
            r"\bmonitor(?:a|es|as|@)?\b|educador\w*(?:/a)? de (?:tiempo libre|ocio|lleure)"
            r"|animador\w*(?:/a)? (?:sociocultural|infantil|de ocio|juvenil)|\btasoc\b"
            r"|director\w*(?:/a)? de (?:tiempo libre|ocio|campamento|casal)|casal d'estiu|\bcampamento"
            r"|\bcolonias\b|\besplai\b|\blleure\b|ocio (?:educativo|infantil)|comedor escolar"
            r"|auxiliar de comedor"
        ),
        senales=[s("comedor o tiempo libre", 0.5,
                   r"comedor|menjador|lleure|tiempo libre|casal|colonias|campamento|extraescolar"),
                 s("título de monitor", 0.5, r"t[íi]tulo|t[íi]tol|\bmtl\b|\bdtl\b")],
        negativos=[
            NEG_DEPORTIVO,
            n("monitorización de sistemas", -4.0,
              r"sistemas|\bredes\b|\bnoc\b|24x7|seguridad|\bcctv\b|alarmas|\bit\b",
              choca_con=["it"]),
        ],
    ),
    puesto(
        "educacion.social", "educación e integración social",
        es=["educador social", "integrador social"], ca=["educador social", "vetllador"],
        nucleo=(
            r"educador\w*(?:/a)? socials?\b|educadora social|educadors socials|integrador\w*(?:/a)? social"
            r"|t[éèe]cnic\w*(?:/a)? (?:superior )?(?:en |de |d')?integraci[óo] social|t[ée]cnic\w*(?:/a)? "
            r"(?:superior )?(?:en |de )?integraci[óo]n social|trabajador\w*(?:/a)? social"
            r"|treballador\w*(?:/a)? social|auxiliar t[ée]cnic\w*(?:/a)? educativ\w*|\bvetllador\w*"
            r"|educador\w*(?:/a)? (?:de )?(?:centro|piso|residencia|menores|medio abierto)"
            r"|t[ée]cnic\w*(?:/a)? de intervenci[óo]n social|\bpsicopedagog\w*"
        ),
        senales=[s("menores, discapacidad o inclusión", 0.5,
                   r"menores|discapacidad|diversidad|j[óo]venes|adolescen|inmigra|exclusi|inclusi")],
        negativos=[NEG_COMERCIAL],
    ),
    puesto(
        "educacion.civico", "agente cívico, mediación y convivencia",
        es=["agente cívico", "agente de convivencia", "educador de calle", "mediador comunitario",
            "mediador intercultural", "informador cívico"],
        ca=["agent cívic", "agent de convivència", "educador de carrer", "mediador comunitari"],
        junior=["agente cívico sin experiencia"], tambien_en=["atencion", "oficios"],
        # Añadido por el titular el 04-11: el que trabaja para el ayuntamiento en la convivencia de la
        # calle, de noche o en el espacio público. Lo más cercano es la educación social, y por eso
        # vive aquí. No hay ni un título así en la base: todo lo de este puesto es sin medir.
        nucleo=(
            r"agente\w*(?:/a)? c[íi]vic\w*|agents? c[íi]vics?|agent\w*(?:/a)? c[íi]vic\w*"
            r"|agente\w*(?:/a)? de (?:convivencia|proximidad|civismo)|agent\w*(?:/a)? de conviv[èe]ncia"
            r"|educador\w*(?:/a)? de (?:calle|carrer|medio abierto)"
            r"|mediador\w*(?:/a)? (?:comunitari|intercultural|social|vecinal|de conflictos|cultural"
            r"|de convivencia)|mediaci[óo]n? (?:comunit[àa]ria|comunitaria|intercultural|vecinal|social)"
            r"|informador\w*(?:/a)? c[íi]vic|dinamizador\w*(?:/a)? (?:comunitari|vecinal|social|de barrio"
            r"|juvenil)|servicio de convivencia|convivencia (?:nocturna|ciudadana)"
            r"|conviv[èe]ncia (?:nocturna|ciutadana)|agentes? de movilidad|agent\w* de mobilitat"
            r"|t[ée]cnic\w*(?:/a)? de (?:convivencia|civismo|participaci[óo]n ciudadana)"
        ),
        senales=[s("nocturno o espacio público", 0.5,
                   r"noct|\bnit\b|\bcalle\b|carrer|espacio p[úu]blico|barrio|\bbarri\b|ayuntamiento"
                   r"|ajuntament")],
        negativos=[NEG_COMERCIAL],
    ),
    puesto(
        "educacion.ninera", "niñera y cuidado infantil",
        es=["niñera", "canguro", "cuidadora de niños", "au pair"], en=["nanny", "babysitter"],
        ca=["cangur", "mainadera"], junior=["niñera sin experiencia"], tambien_en=["sanidad"],
        # Añadido por el titular el 04-11. Llegarán pocas: estas ofertas viven sobre todo en webs de
        # particulares que el buscador no lee; aquí llegan las de Indeed, Jooble y EURES.
        nucleo=(
            r"\bni[ñn]er[oa]s?\b|\bcanguros?\b|\bcangur\b|mainader|\bau[ -]?pair\b|\bnann(?:y|ies)\b"
            r"|baby ?sitter|cuidador\w*(?:/a)? (?:de )?(?:ni[ñn]|beb[ée]|menores)"
            r"|cuidado (?:de )?(?:ni[ñn]|beb[ée])|cuidar (?:de )?(?:ni[ñn]|beb[ée])|recogida (?:de ni[ñn]|escolar)"
            r"|interna con ni[ñn]|childcare|child ?minder|governess|institutriz"
        ),
        senales=[s("recogida escolar o tardes", 0.5, r"recogida|tardes|colegio|escolar|deberes")],
        negativos=[n("limpieza del hogar", -1.0, r"limpieza|plancha|tareas del hogar",
                     choca_con=["oficios.limpieza"])],
    ),
])

# ======================================================================
# 10. HOSTELERÍA Y TURISMO
# ======================================================================
HOSTELERIA = familia("hosteleria", "🏨", "Hostelería y turismo", puestos=[
    puesto(
        "hosteleria.camarero", "camarero/a",
        es=["camarero", "camarera", "camarero de sala"], en=["waiter"], ca=["cambrer"],
        junior=["camarero sin experiencia"],
        # Sin la camarera de pisos, que es otro puesto. La exclusión mira también el «/a» de detrás:
        # con `(?:/as?)?(?! de pisos)`, la regex se quedaba con «camarero» sin el «/a», el lookahead
        # veía «/a de pisos» y «Camarero/a de pisos» colaba (lo cazó la muestra).
        nucleo=(
            r"\bcamarer[oa@]s?(?!(?:/as?|\(a\))? de (?:pisos|planta|habitaciones))(?:/as?|\(a\))?(?!\w)"
            r"|\bcambrer[ae]?s?(?!(?:/a|/es)? de pisos)(?:/a|/es)?(?!\w)|\bwait(?:er|ress)\b|waiting staff|\bmesero"
            r"|ayudante de (?:camarero|sala|barra)|auxiliar de sala|\bbartender|\bbarman\b|barmaid"
            r"|\bcocteler\w*|\bsumiller|sommelier|jefe de rango|personal de sala"
            r"|extras? (?:de )?(?:hosteler|banquetes)"
        ),
        senales=[s("sala, eventos o banquetes", 0.5,
                   r"extra|evento|banquete|terraza|hotel|restaurante|sala"),
                 s("temporada", 0.5, r"temporada|verano|fines? de semana")],
        negativos=[NEG_INFORMATICA, NEG_TRIPULANTE],
    ),
    puesto(
        "hosteleria.cocinero", "cocinero/a",
        es=["cocinero", "cocinera"], en=["cook", "chef"], ca=["cuiner"],
        nucleo=(
            r"\bcociner[oa@]s?(?:/as?|\(a\))?(?!\w)|\bcuiner[ae]?s?\b|\bchef\b|sous[- ]?chef"
            r"|jef[ea](?:/a)? de (?:cocina|partida)|cap de cuina|\bcook\b|line cook|\bcommis\b(?! de cocina)"
            r"|cocina (?:caliente|fr[íi]a)|\bpizzer[oa]\b|parrillero|pastelero de restaurante"
        ),
        senales=[s("tipo de cocina", 0.5,
                   r"mediterr|italian|japon|sushi|pasteler|reposter|colectividad|restaurante|hotel")],
        negativos=[NEG_INFORMATICA,
                   n("cocinas (mobiliario)", -4.0,
                     r"montador|instalador|mueble|comercial de cocinas|dise[ñn]ador de cocinas|showroom",
                     choca_con=["oficios.acabados", "comercial"])],
    ),
    puesto(
        "hosteleria.ayudante", "ayudante de cocina y office",
        es=["ayudante de cocina", "friegaplatos", "auxiliar de cocina"], en=["kitchen porter"],
        ca=["ajudant de cuina"],
        nucleo=(
            r"ayudante\w*(?:/a)? de cocina|ajudant\w*(?:/a)? de cuina|auxiliar\w*(?:/a)? de cocina|\bpinche\b"
            r"|friega ?platos|rentaplats|marmit[óo]n|personal de office|office de cocina"
            r"|kitchen (?:porter|assistant|helper)|dishwasher|commis de cocina"
            r"|preparador\w*(?:/a)? de (?:alimentos|comidas)|manipulador\w*(?:/a)? de alimentos"
        ),
        senales=[s("hotel o restaurante", 0.5, r"hotel|restaurante|colectividad|catering")],
        negativos=[NEG_INFORMATICA,
                   n("industria alimentaria", -1.5,
                     r"f[áa]brica|\bplanta\b|industria|producci[óo]n|envasad",
                     choca_con=["industria.operario"])],
    ),
    puesto(
        "hosteleria.recepcion", "recepción de hotel",
        es=["recepcionista de hotel"], en=["front desk agent", "hotel receptionist"],
        ca=["recepcionista d'hotel"],
        nucleo=(
            r"recepcionist\w*(?:/a)? (?:de |d')?(?:hotel|hostal|apartamentos|camping|alberg\w*|nocturn\w*"
            r"|de noche)|recepci[óo]n (?:de |del )?hotel|front ?desk|front office (?:agent|clerk)"
            r"|recepcionist\w*[^.\n]{0,40}\bhotel|night auditor|auditor\w*(?:/a)? nocturn\w*"
            r"|guest (?:service|experience|relations) agent|conserje de hotel|\bconcierge\b"
            r"|jef[ea](?:/a)? de recepci[óo]n"
        ),
        senales=[s("reservas", 0.5, r"reservas|booking|\bopera\b")],
        negativos=[NEG_INFORMATICA],
    ),
    puesto(
        "hosteleria.pisos", "camarero/a de pisos",
        es=["camarera de pisos"], en=["housekeeping"], ca=["cambrera de pisos"],
        nucleo=(
            r"camarer[oa@]s?(?:/as?|\(a\))? de (?:pisos|planta|habitaciones)|cambrer[ae]?s?(?:/a)? de pisos"
            r"|\bgobernant[ae]s?\b|subgobernant|housekeeping|hotel housekeeper|room attendant|valet de (?:pisos|habitaciones)"
            r"|limpieza de habitaciones|auxiliar de pisos|\blencer[íi]a de hotel"
        ),
        senales=[s("hotel", 0.5, r"hotel|apartament|resort|hostal")],
        negativos=[NEG_INFORMATICA],
    ),
    puesto(
        "hosteleria.cafeteria", "barista, cafetería y restauración rápida",
        es=["barista", "dependiente cafetería", "equipo restaurante"], en=["barista"],
        nucleo=(
            r"\bbarista|dependient\w*(?:/a)? (?:de )?(?:cafeter[íi]a|restaurante|comida r[áa]pida|boller[íi]a)"
            r"|\bcafeter[íi]a|restauraci[óo]n (?:r[áa]pida|moderna|organizada)|comida r[áa]pida"
            r"|fast[- ]?food|equipo de (?:restaurante|restauraci[óo]n)|team member (?:de )?restaurante"
            r"|restaurant (?:team member|crew)|empleado\w*(?:/a)? de (?:restaurante|cafeter)"
            r"|\bheladeri?[oa]\b|helader[íi]a|auxiliar de restauraci[óo]n"
        ),
        senales=[s("horario flexible", 0.5, r"parcial|fines? de semana|estudiantes|flexible")],
        negativos=[NEG_INFORMATICA, NEG_TRIPULANTE],
    ),
    puesto(
        "hosteleria.turismo", "agencia de viajes y turismo",
        # Sin «GDS» a secas: en la base es «EY GDS» (Global Delivery Services), una auditora.
        es=["agente de viajes", "guía turístico", "informador turístico"], en=["travel agent"],
        ca=["agent de viatges"],
        nucleo=(
            r"agente\w*(?:/a)? de viajes|agent\w*(?:/a)? de viatges|agencia de viajes"
            r"|travel (?:agent|consultant|advisor|counsel\w*)|gu[íi]a tur[íi]stic\w*"
            r"|gu[íi]a (?:de )?(?:museo|turismo|local|acompa[ñn]ante)|tour (?:guide|leader|operator)"
            r"|informador\w*(?:/a)? tur[íi]stic\w*|t[ée]cnic\w*(?:/a)? (?:de |en )?turismo"
            r"|t[èe]cnic\w*(?:/a)? de turisme|oficina de turismo|reservas (?:de )?(?:viajes|hotel|grupos)"
            r"|reservations? (?:agent|specialist)|booking (?:agent|specialist)|t[ée]cnic\w*(?:/a)? de reservas"
            r"|gestor\w*(?:/a)? de reservas|animador\w*(?:/a)? tur[íi]stic\w*|corporate travel"
            r"|distribuci[óo]n hotelera"
        ),
        senales=[s("idiomas o grupos", 0.5, r"grupos|incoming|receptivo|mice|eventos")],
        negativos=[NEG_INFORMATICA],
    ),
])

# ======================================================================
# 11. COMERCIO Y TIENDAS
# ======================================================================
COMERCIO = familia("comercio", "🛒", "Comercio y tiendas", puestos=[
    puesto(
        "comercio.dependiente", "dependiente/a / vendedor/a de tienda",
        es=["dependiente", "dependienta", "vendedor tienda", "asesor de ventas"],
        en=["sales assistant", "retail"], ca=["dependent", "dependenta"],
        junior=["dependiente sin experiencia"],
        nucleo=(
            r"(?<!personas )(?<!persones )\bdependient[ae]s?\b|\bdependient[ae]s?/|\bdependent[ae]?s?(?:/a)?\b(?! (?:on|upon|of))"
            r"|vendedor\w*(?:/a)? (?:de )?(?:tienda|textil|moda|calzado|perfumer[íi]a|joyer[íi]a|electr[óo]nica"
            r"|muebles|retail)|asesor\w*(?:/a)? de ventas|asesor\w*(?:/a)? (?:comercial )?de tienda"
            r"|comercial de tienda|personal de tienda|sales (?:assistant|associate|advisor|consultant)"
            r"|retail (?:assistant|associate|sales)|shop assistant|store (?:associate|assistant)"
            r"|beauty advisor|consejer\w*(?:/a)? de belleza|auxiliar de tienda|venta en tienda"
        ),
        senales=[s("tienda o centro comercial", 0.5,
                   r"tienda|botiga|\bstore\b|centro comercial|outlet|retail")],
        negativos=[
            # «Atención a personas dependientes» es cuidar, no vender.
            n("atención a personas dependientes", -4.0,
              r"personas dependientes|dependencia|cuidad|domicilio|atenci[óo]n a (?:personas|mayores)",
              choca_con=["sanidad"]),
            n("venta fuera de tienda", -1.5, r"comercial de (?:campo|zona|calle)|aut[óo]nom[oa]|puerta a puerta",
              choca_con=["comercial.campo"]),
        ],
    ),
    puesto(
        "comercio.cajero", "cajero/a",
        es=["cajero", "cajera", "cajero reponedor"], en=["cashier"], ca=["caixer", "caixera"],
        # Sin el cajero automático: «Técnico/a de cajeros automáticos» es un técnico de campo.
        nucleo=(
            r"\bcajer[oa@]s?(?:/as?|\(a\))?(?! autom)(?!\w)|\bcaixer[ae]?s?\b|\bcashier"
            r"|personal de caja|auxiliar de caja|checkout operator"
        ),
        senales=[S_SUPERMERCADO],
        negativos=[],
    ),
    puesto(
        "comercio.reponedor", "reponedor/a",
        es=["reponedor", "reponedora", "auxiliar de supermercado"], ca=["reposador"],
        nucleo=(
            r"\breponedor\w*|\breposador\w*|reposici[óo]n (?:de )?(?:tienda|lineales|producto|supermercado)"
            r"|auxiliar de supermercado|personal de supermercado|mozo de tienda|(?<!visual )merchandiser"
            r"|merchandising (?:de )?(?:punto de venta|pdv)|promotor\w*(?:/a)? de (?:ventas|punto de venta)"
            r"|gondoler\w*|shelf stacker|stock (?:assistant|associate)"
        ),
        senales=[S_SUPERMERCADO],
        negativos=[n("almacén logístico", -1.0,
                     r"almac[ée]n log|plataforma log|centro log[íi]stic|\bpicking\b",
                     choca_con=["logistica.mozo"])],
    ),
    puesto(
        "comercio.frescos", "frescos (carnicería, pescadería, charcutería, panadería)",
        es=["carnicero", "pescadero", "charcutero", "panadero"],
        ca=["carnisser", "peixater", "forner"],
        nucleo=(
            r"\bcarnicer[oa@íi]|\bcarnisser|\bpescader[oa@íi]|\bpeixater|\bcharcuter|\bxarcuter"
            r"|\bpanader[oa@íi]|\bforner[ae]?s?\b|\bfruter[oa@íi]|\bpasteler[oa@íi]"
            r"|secci[óo]n de frescos|profesional de (?:frescos|carnicer|pescader|charcuter|panader)"
            r"|\bfrescos\b|\bbutcher\b|fishmonger|\bbaker\b|deli (?:assistant|counter)|\bobrador\b"
        ),
        senales=[S_SUPERMERCADO],
        negativos=[n("industria cárnica o alimentaria", -1.5,
                     r"industria|f[áa]brica|\bplanta\b|producci[óo]n|matadero|envasad",
                     choca_con=["industria.operario"])],
    ),
    puesto(
        "comercio.encargado", "encargado/a de tienda",
        es=["encargado de tienda", "responsable de tienda", "subdirector de tienda"],
        en=["store manager"], ca=["encarregat de botiga"],
        # Es un puesto de mando: con menos de 6 años, «responsable de tienda» y «store manager» los
        # quita el filtro de senior (perfiles._SENIOR_GENERICO), y se queda «encargado de tienda»,
        # que no lleva palabra de nivel. Es lo que se aprobó el 04-11.
        nota="Puesto de mando: con menos de 6 años solo llegan los que no dicen «responsable», "
             "«manager» o «jefe».",
        nucleo=(
            r"encargad\w*(?:/a)? (?:de )?(?:tienda|secci[óo]n|turno|botiga|establecimiento|supermercado)"
            r"|encarregat\w*(?:/d?a)? de botiga|responsable de (?:tienda|secci[óo]n|turno|establecimiento)"
            r"|subdirector\w*(?:/a)? de tienda|director\w*(?:/a)? de tienda|(?:assistant )?store manager"
            r"|shop manager|jef[ea](?:/a)? de (?:tienda|secci[óo]n)|coordinador\w*(?:/a)? de tienda|gerente de tienda"
            r"|segund[oa] de tienda|store supervisor|supervisor\w*(?:/a)? de tienda"
        ),
        senales=[s("equipo y ventas", 0.5, r"equipo|\bkpi|ventas|apertura")],
        negativos=[],
    ),
    puesto(
        "comercio.visual", "visual merchandiser / escaparatista",
        es=["visual merchandiser", "escaparatista"], en=["visual merchandiser"], ca=["aparadorista"],
        nucleo=(r"visual merchandis\w*|\bescaparatista|\baparadorista|\bvm (?:specialist|coordinator|assistant)"
                r"|montaje de escaparates|merchandising visual|visual (?:coordinator|specialist) (?:retail|store)"),
        senales=[s("moda o lujo", 0.5, r"\bmoda\b|fashion|lujo|luxury|textil")],
        negativos=[],
    ),
])

# ======================================================================
# 12. LOGÍSTICA Y TRANSPORTE
# ======================================================================
NEG_SISTEMAS_LOGISTICOS = n(
    "sistemas logísticos (IT)", -3.0,
    r"\bsap\b|consultor|consultant|\bsystems?\b|sistemas|software|\bit\b|developer|\bbi\b|almacenamiento"
    r"|storage",
    choca_con=["it"])

LOGISTICA = familia("logistica", "📦", "Logística y transporte", puestos=[
    puesto(
        "logistica.mozo", "mozo/a de almacén y preparador/a de pedidos",
        es=["mozo de almacén", "operario de almacén", "preparador de pedidos"],
        en=["warehouse operative"], ca=["mosso de magatzem"],
        junior=["mozo de almacén sin experiencia"],
        # `almac[ée]n\b`: sin el `\b`, «Administrador/a de Almacenamiento SAN/NAS» (base).
        nucleo=(
            r"moz[oa@]s?(?:/as?)? (?:de )?(?:almac[ée]n\b|carga|descarga|log[íi]stic|reparto)"
            r"|moss[oa]s?(?:/as?)? de magatzem|operari\w*(?:/a)? (?:de )?(?:almac[ée]n\b|log[íi]stic\w*|magatzem)"
            r"|preparador\w*(?:/a)? de (?:pedidos|comandes)|preparaci[óo]n de pedidos|\bpicking\b"
            r"|\bpicker\b|\bpacking\b|carga y descarga|warehouse (?:operative|operator|associate|worker"
            r"|assistant)|auxiliar de almac[ée]n\b|personal de almac[ée]n\b|pe[óo]n\w* de almac[ée]n\b"
        ),
        senales=[s("carretilla", 1.0, r"carretill|forklift|transpalet|apilador"),
                 s("logística", 0.5, r"log[íi]stic|plataforma|centro de distribuci[óo]n")],
        negativos=[
            # Lo mismo que el plan de MP2 (pruebas/fixtures/plan_mozo_zaragoza.json).
            n("repartidor autónomo", -3.0, r"aut[óo]nom[oa]|repartidor", choca_con=["logistica.reparto"]),
            NEG_SISTEMAS_LOGISTICOS,
        ],
    ),
    puesto(
        "logistica.carretillero", "carretillero/a",
        es=["carretillero", "carretillera"], en=["forklift"], ca=["carretoner"],
        nucleo=(r"carretiller\w*|carretoner\w*|(?:operador|conductor)\w*(?:/a)? de carretilla"
                r"|carretilla (?:elevadora|frontal|retr[áa]ctil|trilateral)|forklift|reach truck"
                r"|transpaletista"),
        senales=[s("retráctil o trilateral", 0.5, r"retr[áa]ctil|trilateral|frontal|\bvna\b")],
        negativos=[NEG_AUTONOMO],
    ),
    puesto(
        "logistica.conductor", "conductor/a de camión",
        es=["conductor camión", "chófer c+e", "conductor carnet c"], en=["truck driver"],
        ca=["conductor de camió", "xofer"],
        # «Conductor» a secas no: en la base hay «conductor de línea» (fábrica) y en el oficio, el
        # cable. Tiene que decir qué conduce o el carnet.
        nucleo=(
            r"conductor\w*(?:/a)? (?:de )?(?:cami[óo]n\w*|camions?|tr[áa]iler|ba[ñn]era|cisterna|autob[úu]s"
            r"|autocar|ruta|carnet c|permiso c|c ?\+ ?e\b|c1\b|profesional|\btir\b|internacional|nacional"
            r"|gr[úu]a|hormigonera|g[óo]ndola|furgoneta|veh[íi]culos pesados)|ch[óo]fer\w*|\bxofer"
            r"|camioner[oa]|(?:truck|lorry|hgv|bus|coach) driver|\btransportista\b|cami[óo]n r[íi]gido"
        ),
        senales=[s("CAP o tacógrafo", 0.5, r"\bcap\b|tac[óo]grafo|\badr\b"),
                 s("ruta", 0.5, r"nacional|internacional|regional|local|\bruta\b")],
        negativos=[NEG_AUTONOMO],
    ),
    puesto(
        "logistica.reparto", "repartidor/a (furgoneta, última milla)",
        es=["repartidor", "conductor reparto", "repartidor furgoneta"], en=["delivery driver"],
        ca=["repartidor"],
        nucleo=(
            r"\brepartidor\w*|\brepartidora|personal de reparto|conductor\w*(?:/a)? (?:de )?reparto"
            r"|reparto (?:de |a domicilio|con furgoneta|en furgoneta|urbano)|\brepartiment"
            r"|delivery (?:driver|rider|associate)|\brider\b|\bcourier\b|\bmensajer[oa@]|\bmissatger"
            r"|[úu]ltima milla|last mile"
        ),
        senales=[s("furgoneta de empresa", 0.5, r"furgoneta de empresa|veh[íi]culo de empresa")],
        negativos=[NEG_AUTONOMO],
    ),
    puesto(
        "logistica.tecnico", "técnico/a de logística y tráfico",
        es=["técnico de logística", "administrativo de logística", "técnico de tráfico"],
        en=["logistics coordinator"], ca=["tècnic de logística"],
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?(?:control de )?(?:log[íi]stica|tr[áa]fico|transporte|aduanas"
            r"|comercio exterior|exportaci[óo]n|importaci[óo]n|operaciones log[íi]sticas|supply chain)"
            r"|administrativ\w*(?:/a)? (?:de )?(?:log[íi]stica|tr[áa]fico|transporte|almac[ée]n\b|aduanas)"
            r"|gestor\w*(?:/a)? de (?:tr[áa]fico|transporte|flotas|aduanas)"
            r"|planificador\w*(?:/a)? (?:de )?(?:transporte|rutas|log[íi]stic\w*|tr[áa]fico)"
            r"|coordinador\w*(?:/a)? (?:de )?(?:log[íi]stica|transporte|tr[áa]fico)"
            r"|logistics (?:coordinator|specialist|analyst|technician|assistant|executive|officer)"
            r"|supply chain (?:analyst|specialist|coordinator|planner|assistant)|demand planner"
            r"|analista log[íi]stic\w*|optimizaci[óo]n de transporte|freight forwarder|\btransitario"
            r"|customs (?:broker|specialist|agent)|operador\w*(?:/a)? log[íi]stic\w*|jefe de tr[áa]fico"
            r"|(?:traffic|transport) (?:coordinator|planner)|especialista en (?:\w+ ){0,4}log[íi]stica"
        ),
        senales=[s("aduanas o internacional", 0.5, r"aduan|incoterm|internacional"),
                 s("SGA o ERP", 0.5, r"\bsga\b|\bwms\b|\bsap\b|\btms\b")],
        negativos=[NEG_SISTEMAS_LOGISTICOS],
    ),
    puesto(
        "logistica.compras", "compras y aprovisionamiento",
        es=["técnico de compras", "comprador", "aprovisionamiento"], en=["buyer", "procurement"],
        ca=["tècnic de compres"],
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?(?:compras|compres)|\bcomprador\w*|(?<!media )\bbuyer\b"
            r"|purchas(?:ing|e) (?:assistant|specialist|agent|analyst|officer|coordinator)"
            r"|procurement (?:specialist|analyst|assistant|officer|coordinator|buyer)|aprovisionamiento"
            r"|aprovisionador\w*|gestor\w*(?:/a)? de (?:compras|aprovisionamiento)"
            r"|administrativ\w*(?:/a)? de compras|sourcing (?:specialist|analyst)|category (?:buyer|analyst)"
        ),
        senales=[s("proveedores y negociación", 0.5, r"proveedor|negociaci|supplier|sourcing")],
        negativos=[
            n("compra de medios (marketing)", -4.0, r"media buyer|\bmedios\b|paid media|programmatic",
              choca_con=["marketing.seo"]),
            NEG_SISTEMAS_LOGISTICOS,
        ],
    ),
])

# ======================================================================
# 13. INDUSTRIA E INGENIERÍA
# ======================================================================
NEG_INGENIERIA_IT = n(
    "ingeniería de software o IT", -3.0,
    r"software|sistemas|\bsystems?\b|\bredes\b|network|cloud|devops|\bdata\b|datos|ciber|cyber"
    r"|security|seguridad inform|\bit\b|telecomunicaci|\bai\b|\bia\b",
    choca_con=["it"])

INDUSTRIA = familia("industria", "🏭", "Industria e ingeniería", puestos=[
    puesto(
        "industria.operario", "operario/a de producción",
        es=["operario de producción", "operaria de producción", "peón industrial"],
        en=["production operator"], ca=["operari de producció"],
        junior=["operario sin experiencia"],
        nucleo=(
            r"operari\w*(?:/\w+)? (?:de )?(?:producci[óo]n?|f[áa]brica|planta|l[íi]nea|montaje|envasado"
            r"|m[áa]quina|industrial|cadena|fabricaci[óo]n|ensamblaje|manipulado|extrusi[óo]n|inyecci[óo]n"
            r"|prensa|torno|cnc|alimentari\w*|c[áa]rnic\w*|qu[íi]mic\w*|metal\w*|pl[áa]stic\w*|textil"
            r"|automoci[óo]n)|pe[óo]n\w* (?:industrial|de producci[óo]n|de f[áa]brica)"
            r"|production (?:operator|operative|worker|associate)|machine operator|assembly (?:worker|operator)"
            r"|operador\w*(?:/a)? de (?:m[áa]quina|producci[óo]n|planta|l[íi]nea)"
            r"|conductor\w*(?:/a)? de (?:l[íi]nea|m[áa]quina)|montador\w*(?:/a)? (?:industrial|de cuadros"
            r"|electromec\w*|de componentes)|\benvasador\w*|\bsoldador\w*|\btorner[oa]\b|\bfresador\w*"
        ),
        senales=[s("turnos rotativos", 0.5, r"turnos|rotativ|noche"),
                 s("sector industrial", 0.5, r"alimentari|automoci|qu[íi]mic|farmac|metal|pl[áa]stic")],
        negativos=[n("ingeniería de producción", -2.0,
                     r"ingenier|engineer|t[ée]cnic\w*(?:/a)? (?:de )?(?:procesos|producci[óo]n)",
                     choca_con=["industria.ingenieria"])],
    ),
    puesto(
        "industria.calidad", "técnico/a de calidad",
        es=["técnico de calidad", "inspector de calidad", "control de calidad"],
        en=["quality technician"], ca=["tècnic de qualitat"], junior=["técnico de calidad junior"],
        # Sin «quality analyst» ni «analista de calidad»: en la base son de moderación («Content
        # Quality Analyst») o de call center.
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?(?:control de )?(?:calidad|qualitat)|inspector\w*(?:/a)? de calidad"
            r"|control de calidad|controlador\w*(?:/a)? de calidad"
            r"|quality (?:technician|inspector|control|engineer|specialist|assurance (?:technician"
            r"|specialist|associate))|auditor\w*(?:/a)? de calidad|ingenier\w*(?:/a)? de calidad"
            r"|calidad (?:y|e) (?:medio ambiente|seguridad alimentaria)|seguridad alimentaria|food safety"
            r"|t[ée]cnic\w*(?:/a)? de validaci[óo]n|validation (?:specialist|engineer|technician)|metr[óo]log"
        ),
        senales=[s("normas ISO, GMP o IFS", 0.5, r"\biso\b|\bgmp\b|\bifs\b|\bbrc\b|appcc|haccp|iatf")],
        negativos=[
            n("calidad de software", -3.0,
              r"software|testing|tester|automati[zs]aci[óo]n de pruebas|selenium|developer|\bqa engineer",
              choca_con=["it.qa"]),
            n("calidad de servicio o de llamadas", -3.0,
              r"llamadas|call ?cent|atenci[óo]n al cliente|customer|contenido|content",
              choca_con=["atencion"]),
        ],
    ),
    puesto(
        "industria.mantenimiento", "mantenimiento industrial",
        es=["técnico de mantenimiento industrial", "electromecánico", "mecánico de mantenimiento"],
        en=["maintenance technician"], ca=["tècnic de manteniment industrial"],
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?mantenimiento (?:industrial|de planta|el[ée]ctrico|mec[áa]nico"
            r"|electromec[áa]nico|de maquinaria|preventivo|correctivo|de l[íi]neas|de equipos)"
            r"|t[èe]cnic\w*(?:/a)? de manteniment industrial|mantenimiento industrial|manteniment industrial"
            r"|\belectromec[áa]nic\w*|mec[áa]nic\w*(?:/a)? (?:de )?(?:mantenimiento|industrial|ajustador|de maquinaria)"
            r"|maintenance (?:engineer|mechanic|electrician)|industrial (?:maintenance|electrician|mechanic)"
            r"|oficial (?:de )?mantenimiento (?:industrial|de planta)|electricista (?:industrial|de mantenimiento)"
            r"|\bautomatista|t[ée]cnic\w*(?:/a)? (?:de )?(?:automatismos|automatizaci[óo]n industrial|plc)"
            r"|mecatr[óo]nic\w*"
        ),
        senales=[s("PLC y automatismos", 0.5, r"\bplc|automat|siemens|neum[áa]tic|hidr[áa]ulic"),
                 s("electromecánica", 0.5, r"electromec|mecatr|el[ée]ctric")],
        negativos=[n("mantenimiento informático", -3.0,
                     r"inform[áa]tic|sistemas|software|\bit\b|\bredes\b|ordenadores|microinform",
                     choca_con=["it"])],
    ),
    puesto(
        "industria.ingenieria", "ingeniero/a de procesos y producción",
        # Sin «automation engineer»: en la base son de IT casi todos («Junior Fortinet Automation
        # Engineer | Terraform», «AI Automation Engineer», «Android Test Automation Engineer»: 5 de 6).
        # «Ingeniero/a de automatización», en castellano, sí suele ser industrial y se queda.
        es=["ingeniero de procesos", "ingeniero industrial", "ingeniero de producción"],
        en=["process engineer", "manufacturing engineer"], ca=["enginyer de processos"],
        junior=["ingeniero junior"],
        nucleo=(
            r"ingenier\w*(?:/a)? (?:de |en )?(?:procesos|producci[óo]n|fabricaci[óo]n|industrial|mec[áa]nic\w*"
            r"|el[ée]ctric\w*|electr[óo]nic\w*|organizaci[óo]n industrial|mejora continua|m[ée]todos|lean"
            r"|planta|materiales|qu[íi]mic\w*|i\+d|dise[ñn]o mec\w*|proyectos industriales|automatizaci[óo]n)"
            r"|enginyer\w*(?:/a)? (?:de |en )?(?:processos|producci[óo]|industrial)"
            r"|(?:process|production|manufacturing|industrial|mechanical|electrical|electronics|chemical"
            r"|controls|continuous improvement|lean|r&d|design|methods) engineer"
            r"|t[ée]cnic\w*(?:/a)? (?:de |en )?(?:procesos|m[ée]todos|mejora continua|i\+d)\b"
            r"|ingenier\w* t[ée]cnic\w* industrial"
        ),
        senales=[s("lean y mejora continua", 0.5, r"\blean\b|kaizen|six sigma|mejora continua|\bsmed\b"),
                 s("CAD", 0.5, r"\bcad\b|solidworks|catia|autocad|inventor")],
        negativos=[NEG_INGENIERIA_IT, NEG_COMERCIAL],
    ),
    puesto(
        "industria.oficina_tecnica", "delineante / oficina técnica",
        es=["delineante", "proyectista", "oficina técnica"], en=["cad designer"], ca=["delineant"],
        nucleo=(
            r"\bdelineante|\bdelineant\b|\bproyectista|oficina t[éèe]cnica|dibujante (?:t[ée]cnic|cad|proyectista)"
            r"|\bcad (?:designer|technician|operator|drafter)|drafts?(?:man|person)|draughtsman"
            r"|t[ée]cnic\w*(?:/a)? (?:de |en )?(?:oficina t[ée]cnica|dise[ñn]o (?:mec[áa]nic\w*|industrial"
            r"|t[ée]cnic\w*|cad))|dise[ñn]ador\w*(?:/a)? (?:t[ée]cnic\w*|industrial|mec[áa]nic\w*|cad|de producto"
            r"|de moldes|de utillajes|el[ée]ctric\w*)|bim (?:modell?er|technician|coordinator)"
            r"|modelador\w*(?:/a)? bim|t[ée]cnic\w*(?:/a)? bim|\bcalculista|t[ée]cnic\w*(?:/a)? de c[áa]lculo"
        ),
        senales=[s("CAD o BIM", 1.0, r"autocad|\bcad\b|solidworks|revit|\bbim\b|catia|inventor|microstation")],
        negativos=[n("diseño gráfico o web", -3.0, r"gr[áa]fic|\bweb\b|\bux\b|\bui\b|digital|\bmoda\b",
                     choca_con=["marketing.diseno"])],
    ),
    puesto(
        "industria.prl", "PRL y medio ambiente",
        es=["técnico prl", "técnico de prevención de riesgos laborales", "técnico de medio ambiente"],
        en=["hse technician"], ca=["tècnic de prevenció"],
        nucleo=(
            r"\bprl\b|prevenci[óo]n de riesgos|prevenci[óo] de riscos"
            r"|t[ée]cnic\w*(?:/a)? (?:superior )?(?:de |en )?(?:prevenci[óo]n|seguridad (?:y|e) salud|seguridad laboral"
            r"|medio ?ambiente|\bhse\b|\behs\b|gesti[óo]n ambiental)|t[èe]cnic\w*(?:/a)? (?:de )?(?:prevenci[óo]"
            r"|medi ambient)|\bhse\b|\behs\b|health (?:and|&) safety|safety (?:officer|technician|specialist"
            r"|coordinator)|environmental (?:technician|specialist|officer|engineer)"
            r"|coordinador\w*(?:/a)? de seguridad (?:y salud|en obra)|recurso preventivo"
            r"|higienista industrial|\bmedio ?ambiente\b"
        ),
        senales=[s("nivel superior", 0.5, r"superior|intermedio|m[áa]ster|tres especialidades"),
                 s("ISO 14001 o 45001", 0.5, r"14001|45001|ohsas|\biso\b")],
        negativos=[
            n("ciberseguridad", -4.0, r"ciber|cyber|inform[áa]tic|\bit\b|security analyst",
              choca_con=["it.ciber"]),
            NEG_COMERCIAL,
        ],
    ),
    puesto(
        "industria.laboratorio", "laboratorio (químico, alimentario)",
        es=["técnico de laboratorio", "analista de laboratorio"], en=["laboratory technician"],
        ca=["tècnic de laboratori"],
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:superior )?(?:de |en )?(?:laboratori\w*|an[áa]lisis qu[íi]mico|qu[íi]mica"
            r"|i\+d|microbiolog\w*|bioqu[íi]mic\w*|biotecnolog\w*|formulaci[óo]n|cosm[ée]tic\w*)"
            r"|analista (?:de laboratorio|qu[íi]mic\w*|microbiol\w*)|\bqu[íi]mic[oa]s?\b|\bchemist\b"
            r"|(?:laboratory|lab) (?:technician|analyst|assistant)|auxiliar de laboratorio"
            r"|\bmicrobi[óo]log[oa]s?\b"
        ),
        senales=[s("técnicas analíticas", 0.5, r"hplc|cromatograf|\bgmp\b|\bglp\b|espectro|microbiol")],
        negativos=[
            n("laboratorio clínico", -3.0,
              r"cl[íi]nic|centro m[ée]dico|hospital|an[áa]lisis cl[íi]nicos|sanitari|extracci",
              choca_con=["sanidad.tecnicos"]),
            NEG_COMERCIAL,
        ],
    ),
    puesto(
        "industria.civil", "ingeniería civil y obra civil",
        es=["ingeniero civil", "ingeniero de caminos", "técnico de obra civil",
            "ingeniero técnico de obras públicas"],
        en=["civil engineer"], ca=["enginyer civil", "enginyer de camins"],
        junior=["ingeniero civil junior"], tambien_en=["oficios"],
        nucleo=(
            r"ingenier\w*(?:/a)? (?:de )?(?:caminos|civil|obra civil|obras p[úu]blicas|estructuras"
            r"|geot[ée]cnic\w*|hidr[áa]ulic\w*|edificaci[óo]n|la edificaci[óo]n)"
            r"|enginyer\w*(?:/a)? (?:de )?(?:camins|civil|obra p[úu]blica)"
            r"|civil (?:engineer|engineering|(?:&|and|y) structural)|structural engineer"
            r"|t[ée]cnic\w*(?:/a)? (?:de |en )?(?:obra civil|obras|obra|edificaci[óo]n|topograf[íi]a|estructuras)\b"
            r"|\btop[óo]graf[oa]s?\b|ingenier\w* t[ée]cnic\w* de obras p[úu]blicas|\bitop\b|\bicc?p\b"
            r"|jefe de (?:obra|producci[óo]n de obra)|encargad\w*(?:/a)? de obra|arquitect\w*(?:/a)? t[ée]cnic\w*"
            r"|aparejador\w*|construction (?:engineer|manager)|dise[ñn]ador\w*(?:/a)? civil"
            r"|calculista de estructuras"
        ),
        senales=[s("BIM o estructuras", 0.5, r"\bbim\b|revit|civil 3d|estructur|cype"),
                 s("obra pública", 0.5,
                   r"obra p[úu]blica|infraestructur|carreteras|ferrocarril|hidr[áa]ulic|fotovolt")],
        negativos=[
            n("otro «civil»", -4.0,
              r"aviaci[óo]n civil|protecci[óo]n civil|guardia civil|registro civil|responsabilidad civil"
              r"|derecho civil|abogad"),
            NEG_INGENIERIA_IT,
        ],
    ),
])

# ======================================================================
# 14. OFICIOS, MANTENIMIENTO Y CONSTRUCCIÓN
# ======================================================================
OFICIOS = familia("oficios", "🔧", "Oficios, mantenimiento y construcción", puestos=[
    puesto(
        "oficios.electricista", "electricista",
        es=["electricista", "oficial electricista", "ayudante electricista"], en=["electrician"],
        ca=["electricista"], junior=["ayudante electricista"],
        nucleo=(
            r"electricist\w*|electrician|instalador\w*(?:/a)? el[ée]ctric\w*|instal·lador\w*(?:/a)? el[èe]ctric\w*"
            r"|t[ée]cnic\w*(?:/a)? (?:de |en )?instalaciones el[ée]ctricas|montador\w*(?:/a)? el[ée]ctric\w*"
            r"|t[ée]cnic\w*(?:/a)? electricista|electricidad (?:industrial|y fontaner[íi]a)"
        ),
        senales=[s("carnet o REBT", 0.5, r"carnet|\brebt\b|instalador autorizado|baja tensi")],
        negativos=[n("ingeniería eléctrica", -1.5, r"ingenier|engineer",
                     choca_con=["industria.ingenieria"])],
    ),
    puesto(
        "oficios.fontaneria", "fontanería y climatización",
        es=["fontanero", "técnico de climatización", "frigorista"], ca=["lampista", "frigorista"],
        nucleo=(
            r"\bfontaner\w*|\blampist\w*|\bplumber|t[ée]cnic\w*(?:/a)? (?:de |en )?(?:climatizaci[óo]n|calefacci[óo]n"
            r"|fr[íi]o|refrigeraci[óo]n|hvac|calderas|aire acondicionado|gas)\b|\bfrigorist\w*"
            r"|instalador\w*(?:/a)? (?:de )?(?:climatizaci[óo]n|calefacci[óo]n|fontaner[íi]a|gas|aire acondicionado"
            r"|aerotermia|calderas)|hvac (?:technician|engineer)|t[èe]cnic\w*(?:/a)? de climatitzaci[óo]"
            r"|\bcalefactor\b|\bcalderista|oficial de fontaner"
        ),
        senales=[s("carnet RITE o de gas", 0.5, r"\brite\b|carnet|\bgas\b|frigorista")],
        negativos=[n("ingeniería", -1.5, r"ingenier|engineer", choca_con=["industria.ingenieria"]),
                   NEG_COMERCIAL],
    ),
    puesto(
        "oficios.mantenimiento", "mantenimiento de edificios",
        es=["técnico de mantenimiento", "oficial de mantenimiento", "mantenimiento de edificios"],
        en=["maintenance technician"], ca=["tècnic de manteniment"],
        # El de «Hoteles Yurbban» y el del «Grand Hotel Central» (base) son esto. El industrial tiene su
        # puesto en Industria, y el de sistemas no es un oficio.
        nucleo=(
            r"t[éèe]cnic\w*(?:/a)? (?:de |en )?mantenimiento(?! (?:industrial|de planta|de sistemas|de software"
            r"|de aplicaciones|de maquinaria|de l[íi]neas))|t[èe]cnic\w*(?:/a)? de manteniment(?! industrial)"
            r"|oficial (?:de )?mantenimiento(?! industrial)|operari\w*(?:/a)? (?:de )?mantenimiento(?! industrial)"
            r"|mantenimiento (?:de edificios|integral|general|de instalaciones|polivalente|de hotel"
            r"|de comunidades)|manteniment d'edificis|maintenance (?:technician|operative|handyman)"
            r"|\bhandyman\b|\bmanitas\b|personal de mantenimiento|encargad\w*(?:/a)? de mantenimiento"
            r"|t[ée]cnic\w*(?:/a)? polivalente"
        ),
        senales=[s("polivalente", 0.5, r"polivalent|electricidad|fontaner|pintura|climatizaci|carpinter")],
        negativos=[n("mantenimiento informático", -3.0,
                     r"inform[áa]tic|sistemas|software|\bit\b|\bredes\b|ordenadores|microinform",
                     choca_con=["it"])],
    ),
    puesto(
        "oficios.construccion", "construcción (albañil, peón)",
        es=["albañil", "peón de obra", "oficial de primera construcción"], ca=["paleta", "manobre"],
        junior=["peón sin experiencia"],
        nucleo=(
            r"alba[ñn]il\w*|\bpaleta\b|pe[óo]n\w* (?:de )?(?:obra|construcci[óo]n|alba[ñn]il\w*|la construcci[óo]n)"
            r"|\bmanobre|oficial (?:de )?(?:1[ªa]|primera|2[ªa]|segunda)? ?(?:de )?(?:alba[ñn]iler[íi]a"
            r"|construcci[óo]n|obra)|operari\w*(?:/a)? (?:de )?(?:obra|construcci[óo]n)|\bencofrador"
            r"|ferrallista|\byesero|alicatador|\bsolador|\bgruista|construction (?:worker|labou?rer)"
            r"|bricklayer|oficial de reformas|operari\w*(?:/a)? de reformas"
        ),
        senales=[s("PRL de obra o TPC", 0.5, r"\bprl\b|20 ?h|60 ?h|\btpc\b|tarjeta profesional")],
        negativos=[n("ingeniería o arquitectura", -1.5, r"ingenier|arquitect|aparejador|t[ée]cnic",
                     choca_con=["industria.civil"])],
    ),
    puesto(
        "oficios.acabados", "acabados (pintor/a, pladur, carpintería)",
        es=["pintor", "pladurista", "carpintero"], ca=["pintor", "fuster"],
        nucleo=(
            r"\bpintor(?:a|es|as)?\b|pintura (?:industrial|decorativa)|\bpladur\w*|yeso laminado"
            r"|\bcarpinter\w*|\bfuster\w*|\bebanist\w*|montador\w*(?:/a)? (?:de )?(?:muebles|cocinas|mobiliario"
            r"|ventanas|puertas|aluminio|pvc|stands|tarima|parquet)|instalador\w*(?:/a)? de (?:pladur"
            r"|ventanas|cocinas|suelos|parquet|tarima|puertas|persianas)|parquetista|tarimador"
            r"|\bcristaler[oa]s?\b|\bvidrier[oa]s?\b|\btapicer\w*|\bcerrajer\w*|\bpainter\b|\bcarpenter|\bjoiner\b"
        ),
        senales=[s("oficial", 0.5, r"oficial|1[ªa]|primera")],
        negativos=[],
    ),
    puesto(
        "oficios.mecanico", "mecánico/a de automoción",
        es=["mecánico", "mecánico de automoción", "chapista"], en=["mechanic"], ca=["mecànic"],
        # «Mecánico» a secas no: «Mecánico/a de Mantenimiento de Maquinaria y Equipos Industriales»
        # (base) es industria, y «escaleras mecánicas» no es nadie.
        nucleo=(
            r"mec[áàa]nic\w*(?:/a)? (?:de |d')?(?:automoci[óo]n?|veh[íi]cul\w*|coches|camiones|motos|turismos|taller"
            r"|autom[óo]vil\w*|electromec\w*|primera|oficial|flota|agr[íi]cola|naval|maquinaria pesada|campo"
            r"|1[ªa])|electromec[áa]nic\w*(?:/a)? (?:de )?(?:automoci[óo]n|veh[íi]culos)|\bchapist\w*"
            r"|pintor\w*(?:/a)? (?:de )?(?:carrocer[íi]a|autom[óo]vil|veh[íi]culos)"
            r"|(?<!soporte )t[ée]cnic\w*(?:/a)? (?:de |en )?(?:automoci[óo]n|electromec[áa]nica de veh[íi]culos|diagnosis)"
            r"|(?:car|auto|motor) mechanic|vehicle technician"
        ),
        senales=[s("taller o diagnosis", 0.5, r"taller|diagnosis|concesionari")],
        negativos=[n("ingeniería mecánica", -2.0, r"ingenier|engineer|dise[ñn]",
                     choca_con=["industria.ingenieria"])],
    ),
    puesto(
        "oficios.limpieza", "limpieza",
        es=["limpiador", "limpiadora", "personal de limpieza"], en=["cleaner"],
        ca=["netejador", "netejadora"],
        # Sin «limpieza de datos», que no es un oficio: el núcleo pide un sitio que se limpia.
        nucleo=(
            r"\blimpiador\w*|\blimpiadora|\bnetejador\w*|personal de (?:limpieza|neteja)"
            r"|operari\w*(?:/a)? (?:de )?(?:limpieza|neteja)|auxiliar de limpieza"
            r"|limpieza (?:de |en )?(?:oficinas|hogar|hospital\w*|comunidades|edificios|industrial|hoteles"
            r"|cristales|portales|colegios|tiendas|centros)|empleada de (?:hogar|limpieza)"
            r"|empleado\w*(?:/a)? de hogar|pe[óo]n de limpieza|\bcleaner\b|cleaning (?:operative|staff|technician)"
            r"|\bhousekeeper\b|especialista (?:en|de) limpieza|t[ée]cnic\w*(?:/a)? de limpieza"
        ),
        senales=[s("tipo de centro", 0.5, r"oficinas|hospital|hotel|comunidades|colegio|industrial")],
        negativos=[NEG_COMERCIAL],
    ),
    puesto(
        "oficios.vigilancia", "vigilancia y conserjería",
        es=["vigilante de seguridad", "auxiliar de servicios", "conserje"], en=["security guard"],
        ca=["vigilant de seguretat", "conserge"],
        # Con los dos lookbehind: «Information System Security Officer (Cybersecurity…)» (base) lleva
        # «Security Officer» y es ciberseguridad.
        nucleo=(
            r"vigilante\w*(?:/a)? (?:de )?seguridad|vigilant\w*(?:/a)? de seguretat|\bvigilantes?\b"
            r"|(?<!system )(?<!systems )(?<!information )(?<!it )(?<!cyber )security (?:guard|officer|agent"
            r"|supervisor)|auxiliar\w*(?:/a)? de (?:servicios|seguridad|control)|controlador\w*(?:/a)? de acceso"
            r"|\bconserje\w*|\bconserge\w*|\bporter[oa@]s?\b|\bescolta\b|guarda (?:de seguridad|rural|jurado)"
            r"|operador\w*(?:/a)? de (?:cra|central receptora)|vigilancia (?:nocturna|de seguridad)"
        ),
        senales=[s("habilitación de vigilante", 0.5, r"\btip\b|habilitad|vigilante de seguridad")],
        negativos=[n("instalación de sistemas de seguridad", -3.0,
                     r"t[ée]cnic|instalador|\bcctv\b|alarmas|electr[óo]nic|mantenimiento",
                     choca_con=["oficios.electricista", "oficios.mantenimiento"])],
    ),
])

FAMILIAS = [IT, ADMIN, ATENCION, COMERCIAL, MARKETING, FINANZAS, RRHH, SANIDAD, EDUCACION, HOSTELERIA,
            COMERCIO, LOGISTICA, INDUSTRIA, OFICIOS]


def catalogo() -> dict:
    """El catálogo tal y como se escribe en `perfilado/catalogo.json`."""
    return {
        "v": 1,
        "fuente": "perfilado/fuente.py (no se edita el JSON a mano: python -m perfilado.construir)",
        "familias": copy.deepcopy(FAMILIAS),
    }
