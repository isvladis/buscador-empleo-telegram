# -*- coding: utf-8 -*-
"""
Juego de reglas `titular-ciber`: el perfil del titular desde MP4, ciberseguridad defensiva de
entrada en Barcelona o en remoto real (D11).

Parte de `titular-2026-10` e importa de él lo que no cambia en vez de copiarlo: aquel fichero está
congelado, así que nada de lo importado se puede mover por debajo. Aquí solo está la diferencia, y
cada una con su porqué. Las decisiones son del titular, del 30-10 y del 02-11:

- Entran SOC / blue team, respuesta a incidentes y amenazas, seguridad de redes y, por defecto,
  gestión de vulnerabilidades y SOC de OT.
- Fuera GRC, IAM, cumplimiento, pentest y seguridad cloud. Las cuatro reglas que ya los penalizaban
  se quedan tal cual, y seguridad cloud, que ninguna frenaba, gana la suya.
- Soporte, helpdesk, sistemas, NOC y monitorización solo cuentan con señal de ciberseguridad.
- Las prácticas se abren solo en ciberseguridad.

Las medidas de cada elección están en CAMBIOS-MP4.md.
"""

from __future__ import annotations

import copy
import re

from reglas import titular_2026_10 as base
from reglas.titular_2026_10 import _PUESTO_CIBER_DEFENSIVA, _PUESTO_MONITORIZACION, _PUESTO_SOPORTE_IT, _TEC

SENIOR_EXCLUDE = base.SENIOR_EXCLUDE

# Cuándo empezó a regir este juego para el titular: `reenfocar-titular` en el servidor, el 02-11 a
# las 20:17:27 UTC (la hora de `config.json.bak-pre-ciber-20261102_201727`, sellada por el
# contenedor, igual que las marcas). La capa aprendida (MP6) no aprende de lo que se marcó antes:
# describe el perfil de soporte IT que el titular dejó. Lo decidió él el 04-11, con el dato
# (aprendizaje.py).
VIGENTE_DESDE = "2026-11-02T20:17:27"

# ----------------------------------------------------------------------
# LA SEÑAL CENTRAL
#
# En `titular-2026-10` la palabra «ciberseguridad» solo daba el +4,0 pegada a un rol de analista,
# técnico u operador. Se mantiene esa idea, porque es lo que evita premiar «Cybersecurity Engineer»
# o «Consultor de ciberseguridad» como si fueran de entrada, y se ensancha lo que hacía falta:
#   - más roles de entrada: «Junior Cybersecurity» (Deloitte), «Auxiliar de Tecnología y
#     Ciberseguridad» o «Prácticas Ciberseguridad» se quedaban sin señal central;
#   - hasta 5 palabras de separación: «Especialista en Sistemas Microsoft 365 y Seguridad IT»;
#   - seguridad de redes, que entra pero solo con un rol de técnico delante: «Ingeniero/a de Redes
#     y Seguridad» no es un puesto de entrada;
#   - y las subáreas nuevas, que por sí solas ya nombran el puesto.
# ----------------------------------------------------------------------
_ROL_DE_ENTRADA = (
    r"analistas?|analyst|" + _TEC + r"|technician|operador\w*(?:/a)?|operator|specialist|especialista"
    r"|auxiliar|\bjunior\b|j[úu]nior|\bjr\b|\bn1\b|trainee|becari\w*|\bbecas?\b|pr[áa]cticas"
    r"|\bintern\b|internship|apprentice\w*|graduate"
)
_PALABRA_CIBER = (
    r"ciberseguridad|ciberseguretat|cybersecurity|cyber security|\bciber\b|\bcyber\b"
    r"|seguridad inform\w*|seguridad (?:it|ti)\b|it security|information security"
    r"|seguridad de (?:la )?(?:informaci[óo]n|redes)|redes y seguridad|network security"
    r"|seguridad perimetral|vulnerabilidades"
)
_SUBAREAS = (
    r"\bdfir\b|forens\w*|\bmalware\b|ciberinteligencia|inteligencia de amenazas"
    r"|threat (?:intel\w*|hunt\w*|analy\w*|response|investigat\w*)|incident response"
    r"|respuesta a incidentes|security incident"
)
_PUESTO_CIBER = (
    _PUESTO_CIBER_DEFENSIVA + "|" + _SUBAREAS
    + r"|(?:" + _ROL_DE_ENTRADA + r")\W+(?:\w+\W+){0,5}(?:" + _PALABRA_CIBER + r")"
    # «monitoring» solo detrás de la palabra: «Information Security Functional Monitoring» es
    # monitorización de seguridad, y la genérica cuenta como soporte (02-11).
    + r"|(?:" + _PALABRA_CIBER + r")\W+(?:\w+\W+){0,3}(?:" + _ROL_DE_ENTRADA + r"|\bmonitoring\b|monitoriz\w*)"
)

# Lo que basta en el TÍTULO para que un puesto de soporte cuente como enfocado a ciberseguridad.
# Es más ancho que la señal central a propósito: «Técnico/a de Sistemas y Seguridad IT» no tiene por
# qué sumar +4, pero tampoco es el soporte genérico que se quiere apartar.
_CIBER_EN_EL_TITULO = (
    r"ciber|cyber|\bsoc\b|\bsiem\b|\bsoar\b|secops|\bedr\b|\bxdr\b|\bmdr\b|\bdfir\b|forens|malware"
    r"|threat|amenazas|blue team|firewall|antivirus|\bsecurity\b|seguridad|seguretat"
)
_SOPORTE = (
    _PUESTO_SOPORTE_IT + "|" + _PUESTO_MONITORIZACION
    + r"|soporte|support|\bsuport\b|help ?desk|service ?desk"
)
# Sin `re.DOTALL` a propósito: el texto que se mira es «título ubicación», de una línea.
_SOPORTE_SIN_CIBER = r"^(?!.*(?:" + _CIBER_EN_EL_TITULO + r")).*(?:" + _SOPORTE + r")"

SENALES_RELEVANCIA = []
for nombre, peso, patron in base.SENALES_RELEVANCIA:
    if nombre == "puesto de soporte IT o ciberseguridad":
        SENALES_RELEVANCIA.append(("puesto de ciberseguridad", 4.0, _PUESTO_CIBER))
    elif nombre == "junior o nivel 1":
        # Las prácticas se abren y tienen que poder llegar: es la entrada de quien no tiene
        # experiencia. Solo se llega aquí con ciberseguridad en el título: sin ella las descarta
        # la regla de abajo.
        SENALES_RELEVANCIA.append((nombre, peso, patron + r"|trainee|becari|pr[áa]cticas|\bintern\b"
                                   r"|internship|apprentice"))
    elif nombre != "atención al usuario (SAU, user support)":  # era una señal de soporte puro
        SENALES_RELEVANCIA.append((nombre, peso, patron))

# Quitar la señal central no basta: «Técnico Help Desk N1 Barcelona» aún suma 5,5 con junior,
# soporte, IT, técnico y Barcelona. El peso se eligió midiendo: ver CAMBIOS-MP4.md.
PESO_SOPORTE_SIN_CIBER = -3.0

PENALIZACIONES_RELEVANCIA = [
    # Las prácticas siguen restando donde no se han abierto. Con ciberseguridad en el título ya no.
    (nombre, peso, r"^(?!.*(?:" + _CIBER_EN_EL_TITULO + r")).*(?:" + patron + r")")
    if nombre == "prácticas o becario" else (nombre, peso, patron)
    for nombre, peso, patron in base.PENALIZACIONES_RELEVANCIA
] + [
    # El 30-10 quedó fuera, pero «Especialista en Ciberseguridad Cloud» (SEIDOR) seguía en 6,5:
    # ninguna de las cuatro reglas que se quedan lo frena. Tres favoritas del titular caen por esta
    # regla, y lo ha decidido él (02-11). Resta, no descarta: siguen en la web.
    ("seguridad cloud", -2.0, r"(?:ciber|cyber|seguridad|security).{0,25}\bcloud\b"
                              r"|\bcloud\b.{0,25}(?:ciber|cyber|seguridad|security)"),
    ("soporte sin ciberseguridad en el título", PESO_SOPORTE_SIN_CIBER, _SOPORTE_SIN_CIBER),
]

# ----------------------------------------------------------------------
# SEÑALES DE LA DESCRIPCIÓN
#
# La de ingeniería cloud se queda. La nueva deja pasar el soporte que el título no delata, y es
# estricta porque la señal es floja: entre los soportes de la base con dos señales distintas o más,
# 25 de 33 los ocultó el titular, y el README ya descartó en agosto sumar por herramientas de SOC
# (bajaba el AUC de 0,924 a 0,917). Firewall, antivirus o la palabra «ciberseguridad» salen en
# cualquier puesto de sistemas: cuentan como segunda señal, pero solas no bastan. Títulos validados
# por el titular el 02-11.
# ----------------------------------------------------------------------
_SENALES_FUERTES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsoc\b|security operations|centro de operaciones de seguridad",
        r"\bsiem\b|splunk|qradar|arcsight|microsoft sentinel|azure sentinel",
        r"\bedr\b|\bxdr\b|crowdstrike|sentinelone|defender for endpoint|cortex xdr|carbon black",
        r"incidentes? de (?:ciber)?seguridad|security incidents?|respuesta a incidentes|incident response",
        r"vulnerabilidad|vulnerabilit",
        r"threat (?:intel|hunt|detect)|amenazas",
    )
]
_SENALES_GENERALES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"antivirus|anti-?malware|\bmalware\b|ransomware",
        r"ciberseguridad|ciberseguretat|cybersecurity|cyber security",
        r"firewalls?|cortafuegos|fortinet|fortigate|palo alto",
    )
]
PESO_SOPORTE_CON_CIBER = 6.0

REGLAS_DE_LA_DESCRIPCION = base.REGLAS_DE_LA_DESCRIPCION + [
    {
        "nombre": "soporte con ciberseguridad en la descripción",
        "peso": PESO_SOPORTE_CON_CIBER,
        "titulo": re.compile(_SOPORTE_SIN_CIBER, re.IGNORECASE),
        "terminos": _SENALES_FUERTES + _SENALES_GENERALES,
        "minimo": 2,
        "fuertes": _SENALES_FUERTES,
    },
]

# ----------------------------------------------------------------------
# REGLAS DE DESCARTE
# ----------------------------------------------------------------------
_AJUSTES_DE_DESCARTE = {
    # Tiraba forense, malware, threat hunting, incident response y blue team sin «junior» ni «SOC»
    # en el título: justo lo que el 30-10 decidió que entra. Lo demás de esta regla se queda.
    "Ciberseguridad avanzada (salvo junior o SOC)": {
        "patron": (
            r"pentest|penetration test|red team|reverse engineer|\bgrc\b|governance|compliance|"
            r"\bauditor\b|auditor[ií]a|\bciso\b|arquitect|architect|hacking [ée]tico|ethical hack"
        ),
    },
    # Su motivo era «con 2,5 años de experiencia no aportan», que el titular desmintió el 25-10.
    "Prácticas y becas": {"excepcion": _CIBER_EN_EL_TITULO},
}


# Añadida el 04-11-2026 con los botones del titular, no con una corazonada: de los 17 avisos desde el
# reenfoque ocultó 11, y TRES eran programas de prácticas o de recién titulado —«Prácticas IT:
# Ciberseguridad 26-27» (9,0), «FY27 | Junior | T&T | Ciberseguridad» (7,5) y «Prácticas en Smart
# Factory y Ciberseguridad STP27» (7,0)—, que eran las tres notas más altas de la semana.
#
# No basta con quitar la excepción de «Prácticas y becas»: su patrón pide «estudiante en prácticas»,
# «becari[oa]», «internship», «apprentice», «trainee» o «working student», y NO caza la palabra
# «prácticas» a secas ni un «FY27». A las tres no las cazaba nada. Y va sin excepción de
# ciberseguridad a propósito: las tres la dirían y volverían a pasar.
#
# Lo que NO se toca, a propósito: «Prácticas y becas» y su excepción de ciberseguridad, que es una
# decisión del perfil. Medido en la base de la instalación original: esa excepción deja pasar muy pocas
# ofertas y ninguna de Encaje alto, así que no hay nada que ganar quitándola.
#
# Medido antes de escribirla, sobre la base del servidor (2.969 ofertas) y la muestra: caza 17 ofertas,
# de ellas las 3 de Encaje alto de arriba, ninguna marcada como interesante, y **0 casos en
# `etiquetadas_ciber.json`**, así que `linea_base_mp4.json` no se mueve. Esto invierte lo que la prueba
# daba por bueno («Prácticas Ciberseguridad» llegaba): ver `pruebas/test_ciber.py`.
_PRACTICAS_Y_GRADUADOS = {
    "nombre": "Programas de prácticas o de recién titulado",
    "patron": (r"pr[áa]cticas|\bfy\d{2}\b|graduate (?:program|scheme)|programa de graduad[oa]s"
               r"|reci[ée]n (?:titulad|graduad)"),
    # «buenas prácticas» o «best practices» en un título de consultoría sería un falso positivo. Hoy no
    # hay ninguno en la base, pero la palabra aparece sola y la excepción cuesta nada.
    "excepcion": r"buenas pr[áa]cticas|mejores pr[áa]cticas|best practices",
    "activa": True,
}


def _reglas_de_descarte() -> list[dict]:
    reglas = copy.deepcopy(base.REGLAS_DESCARTE)
    for regla in reglas:
        regla.update(_AJUSTES_DE_DESCARTE.get(regla["nombre"], {}))
        if regla["nombre"].startswith("Sin señal del perfil"):
            # Tiene que dejar pasar lo que ahora entra y no decía ninguna de sus palabras.
            regla["patron"] = regla["patron"].replace(r"blue team|", r"blue team|\bdfir\b|forens|malware|"
                                                                     r"threat|amenazas|ciberinteligencia|")
    reglas.append(copy.deepcopy(_PRACTICAS_Y_GRADUADOS))
    return reglas


REGLAS_DESCARTE = _reglas_de_descarte()

TERMINOS_CIBERSEGURIDAD = [
    # Los seis de siempre: el adicional también los busca, así que no añaden consultas.
    "junior soc",
    "soc analyst n1",
    "junior cybersecurity",
    "analista soc junior",
    "analista n1 ciberseguridad",
    "siem analyst",
    # Los nuevos, validados por el titular el 02-11. Sin ningún «junior»: el nivel lo juzga la
    # puntuación, y buscarlo en el término deja fuera a quien no lo escribe en el título.
    "analista ciberseguridad",
    "cybersecurity analyst",
    "tecnico ciberseguridad",  # lo que queda para el soporte con ciberseguridad, sin helpdesk (D11)
    "incident response analyst",
    "threat intelligence analyst",
]
# «SOC» a secas no: en un buscador genérico trae «social» y «sociedad». Ver fuentes_extra/jooble.py.
JOOBLE_TERMINOS = ["ciberseguridad", "analista soc", "cybersecurity analyst", "seguridad informatica"]


def _config_persona() -> dict:
    config = copy.deepcopy(base.CONFIG_PERSONA)
    config["categorias"]["ciberseguridad"].update(
        etiqueta="Ciberseguridad (SOC, incidentes y amenazas)", terminos=list(TERMINOS_CIBERSEGURIDAD))
    # Apagada y vacía, NO quitada: config.cargar_config fusiona con CONFIG_POR_DEFECTO, y si esta
    # clave faltara en config.json volverían los términos de fábrica «IT support SaaS» y
    # «functional analyst QA».
    config["categorias"]["it_saas"].update(activa=False, terminos=[])
    config["pesos_relevancia"] = {}
    config["reglas_descarte"] = REGLAS_DESCARTE
    config["jooble_terminos"] = list(JOOBLE_TERMINOS)
    return config


CONFIG_PERSONA = _config_persona()
