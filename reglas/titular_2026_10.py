# -*- coding: utf-8 -*-
"""
Juego de reglas `titular-2026-10`: el perfil del titular tal y como estaba el 31-10-2026,
soporte IT y ciberseguridad defensiva junior en Barcelona, congelado como datos.

Lo usa el titular mientras tenga `"config": "global"`, y desde MP3 el adicional (D8). Está
aquí, y no en buscador.py, para que varios juegos puedan existir a la vez sin que el código
de los filtros sepa de quién son. Python y no JSON porque cada regex lleva la medida que la
justifica, y en JSON esos comentarios se perderían.

**DOS COSAS QUE NO HAY QUE CONFUNDIR** (medido en MP2, CAMBIOS-MP2.md §2):

- `REGLAS_POR_DEFECTO` son las 20 de FÁBRICA: lo que usa `config.CONFIG_POR_DEFECTO` y por
  tanto lo que ve `pruebas/medir.py`. Se quedan como están porque de ellas depende la línea
  base de la regla 4.
- `REGLAS_DESCARTE` son las 25 que aplica el SERVIDOR, copiadas de su `data/config.json`
  (16.248 B, 27-10-2026, sha256 6f81ba85…). Son las del juego: lo que D8 promete al adicional
  es «lo que recibe ahora», y eso no son las de fábrica. Sobre las 2.787 ofertas de la base,
  las 20 descartan 346 y las 25 descartan 1.479, y en `pruebas/etiquetadas.json` hay 0 de
  diferencia, así que congelar las de fábrica no lo habría cazado ninguna prueba.

`pruebas/equivalencia_titular.py` compara este fichero con la copia real clave a clave.
**No se edita a mano:** un juego congelado no cambia. Si hacen falta otras reglas, juego nuevo.
"""

from __future__ import annotations

import re

# Se excluyen títulos con estas palabras (nivel demasiado alto para el perfil buscado).
#
# «responsable» a secas y «leader» añadidos el 2026-10-25: el patrón exigía
# «responsable de», así que un «Responsable Helpdesk» de Mataró llegó a puntuar 5,0 y a
# cruzar el umbral de aviso, y `\blead\b` no reconoce «Team Leader». Comprobado antes de
# añadirlos contra las ofertas marcadas como interesantes: ninguna lleva «responsable» ni
# «leader» en el título, así que la regla no quita nada.
SENIOR_EXCLUDE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\blead(?:er)?\b|principal|staff\b|\bmanager\b|director|"
    r"\bhead of\b|arquitect[oa]|architect|\bexpert[oa]?\b|\bvp\b"
    r"|\bresponsable\b|\bjefe\b"
    # «Líder de Infraestructura y Soporte Técnico» (Jooble, 27-10): el «lead» en español.
    # Medido: ninguna entre las marcadas como interesantes, 3 en la base.
    r"|\bl[ií]der\b",
    re.IGNORECASE,
)

# Puestos de otras áreas (ventas, finanzas, legal, RRHH, logística...) que los portales
# cuelan porque hacen coincidencia laxa de términos. Derivado de las ofertas que el
# usuario ya ocultó a mano: son las categorías con 0 coincidencias en lo que sí conserva.
RUIDO_POR_DEFECTO = (
    r"account executive|\bsales\b|\bsdr\b|\bbdr\b|business development|customer success"
    r"|account manager|key account|legal counsel|abogad[oa]|tenders? officer"
    r"|accountant|accounting|\bfinance\b|financial|treasury|billing|payroll"
    r"|reconciliation|purchase to pay|procurement|supply chain|inventory"
    r"|capacity planning|demand planning|logistics"
    r"|recruiter|talent acquisition|human resources|recursos humanos|\brrhh\b"
    r"|people (?:partner|experience|operations)"
    r"|marketing|copywriter|content manager|community manager"
    r"|manufacturing engineer|servicio de campo|field service"
)

# Oficios no tecnológicos: los términos en español ("soporte técnico", "helpdesk")
# atraen puestos de sanidad, obra, mantenimiento industrial o comercio.
OFICIOS_NO_TECNICOS = (
    r"enfermer[ií]a|\btcae\b|auxiliar de cl[ií]nica|sanitari[oa]|gesti[óo]n sanitaria"
    r"|hematolog|farmac[ié]|veterinari|fisioterap|odontolog|psicolog"
    r"|aparcamiento|conductor|repartidor|camarer[oa]|cociner[oa]|limpieza"
    r"|ascensor|fontaner|electricist|alba[ñn]il|soldador|carpinter"
    r"|climatizaci[óo]n|refrigeraci[óo]n|calefacci[óo]n|obra civil|delineante"
    r"|prevenci[óo]n de riesgos|\bprl\b|\bcae\b|nataci[óo]n|aquagym|monitor deportiv"
    r"|socorrist|entrenador|peluquer|est[ée]tic|dependient[ae]|cajer[oa]"
    r"|inmobiliari|real estate|estaciones de servicio|centros de mayores"
    r"|\btasoc\b|\btafad\b"
    # Van aquí, con la excepción estricta, porque casi todos se titulan "Técnico/a
    # de..." y la señal IT amplia (que incluye "técnico") los dejaba pasar.
    r"|t[ée]cnic[oa][/ ]?a? de campo|t[ée]cnic[oa] itinerante|electromec[áa]nic"
    r"|\bclaims?\b|siniestros?|suscripci[óo]n|underwrit|p[óo]lizas?"
    r"|\bsettlement\b|liquidaci[óo]n|tesorer[ií]a|book ?keep"
    r"|maintenance engineer|equipment service"
)

# Excepción ESTRECHA para los oficios de arriba. La señal IT general no sirve aquí:
# incluye "técnico", que en español encabeza un montón de puestos que no son de
# informática ("Técnico de PRL", "Técnico de Ascensores"), y anulaba el filtro.
SENAL_IT_ESTRICTA = (
    r"inform[áa]tic|software|\bit\b|helpdesk|help desk|service desk|ciber|cyber"
    r"|\bsoc\b|\berp\b|sistemas inform"
)

# Excepción: si el título lleva una señal IT/ciber fuerte, no se descarta aunque haya
# coincidido con el patrón de ruido ("Logistics IT Systems Specialist" sí interesa).
SENAL_IT_POR_DEFECTO = (
    r"\b(?:it|ict|security|cyber|ciber|seguridad|soc|siem|helpdesk|help desk|service desk"
    r"|soporte|support|sysadmin|systems?|sistemas|infrastructure|infraestructura"
    r"|network|redes|erp|odoo|sap|saas|qa|testing|tester|devops|cloud"
    r"|technical|t[eé]cnic[oa]|inform[aá]tic[oa])\b"
)

# Equivalente en español de la lista de idiomas de "Exige un idioma que no hablas"
# (ver esa regla más abajo). La forma inglesa ("with German", "German-speaking") no
# cubre el corpus real, que es mitad español: "Soporte Helpdesk - N1 (Alemán e
# Inglés)", "Técnico/a de Soporte IT N1 con Neerlandés/Flamenco", "HELPDESK L1 CON
# ALEMÁN". Contrastado contra 1.500 ofertas reales (agosto de 2026): coincide con 8
# títulos, todos ya descartados a mano y ninguno marcado como interesante. Sin conector (a
# diferencia de "habla X"/"X-speaking"/"X market") porque las formas reales que faltan
# por cubrir son variadas ("con neerlandés", "hablante de italiano", "(alemán e
# inglés)", listas con comas) y un conector único no las cubre todas; el propio \b de
# cada palabra ya evita falsos positivos con gentilicios ("alemana", "alemanas").
IDIOMAS_ES_NO_HABLADOS = (
    r"alem[áa]n|neerland[ée]s|holand[ée]s|flamenco|franc[ée]s|italiano|polaco|checo"
    r"|eslovaco|lituano|leton[ée]s|estonio|sueco|noruego|dan[ée]s|finland[ée]s|griego"
    r"|portugu[ée]s|h[uú]ngaro|ruman[oa]|b[úu]lgaro|turco|[áa]rabe|hebreo|japon[ée]s"
    r"|coreano"
)

# ----------------------------------------------------------------------
# REGLAS DE DESCARTE
#
# Cada regla lleva su propia excepción, porque el mismo término significa cosas
# distintas según el contexto: "Business Analyst" sobra, pero "Business Analyst /
# Software Tester" no. Todas se han contrastado contra las ofertas a las que el
# usuario SÍ ha aplicado: ninguna toca ni una sola de ellas.
# ----------------------------------------------------------------------

REGLAS_POR_DEFECTO = [
    {
        "nombre": "Otras áreas profesionales",
        "patron": RUIDO_POR_DEFECTO,
        "excepcion": SENAL_IT_POR_DEFECTO,
        "activa": True,
    },
    {
        "nombre": "Oficios no técnicos (sanidad, obra, PRL…)",
        "patron": OFICIOS_NO_TECNICOS,
        "excepcion": SENAL_IT_ESTRICTA,
        "activa": True,
    },
    {
        "nombre": "Business analyst (salvo si es QA o soporte)",
        "patron": r"business (?:analyst|systems? analyst)|technical business|systems analyst",
        "excepcion": r"tester|testing|\bqa\b|quality|support|soporte|helpdesk|service desk",
        "activa": True,
    },
    {
        "nombre": "Consultoría (salvo junior o soporte)",
        "patron": r"\bconsultant\b|\bconsultor(?:a|/a|es)?\b|consultor[ií]a|advisory",
        "excepcion": r"\bjunior\b|\bjr\b|\bn1\b|soporte|support|helpdesk|service desk",
        "activa": True,
    },
    {
        "nombre": "ERP que no manejas (SAP, Dynamics, Salesforce…)",
        "patron": r"\bsap\b|microsoft dynamics|\bd365\b|business central|salesforce"
                  r"|navision|netsuite|oracle fusion|workday",
        "excepcion": r"\bjunior\b|\bn1\b|helpdesk|service desk",
        "activa": True,
    },
    {
        "nombre": "Desarrollo de software",
        "patron": r"developer|desarrollador|programador|programuotojas|software engineer"
                  r"|fullstack|full[- ]stack|back[- ]?end|front[- ]?end|\bsharepoint\b",
        "excepcion": r"support|soporte|helpdesk|service desk|\bqa\b|tester",
        "activa": True,
    },
    {
        "nombre": "Exige un idioma que no hablas",
        "patron": r"\bwith (?:german|dutch|french|italian|polish|czech|slovak|lithuanian"
                  r"|latvian|estonian|swedish|norwegian|danish|finnish|greek|portuguese"
                  r"|hungarian|romanian|bulgarian|turkish|arabic|hebrew|japanese|korean)\b"
                  r"|(?:german|dutch|french|italian|polish|czech|lithuanian|latvian|swedish"
                  r"|norwegian|danish|finnish|greek|hungarian|romanian|turkish)[- ]speaking"
                  r"|n[ai]tive (?:german|dutch|french|italian|polish|nordic)"
                  r"|\b(?:" + IDIOMAS_ES_NO_HABLADOS + r")\b",
        "excepcion": None,
        "activa": True,
    },
    {
        "nombre": "Gestión de proyectos y producto",
        "patron": r"project manager|\bpmo\b|product (?:owner|manager|leader)|scrum master"
                  r"|delivery (?:manager|lead)|program manager|jefe de proyecto",
        "excepcion": None,
        "activa": True,
    },
    {
        "nombre": "Datos, BI e IA",
        "patron": r"data (?:scientist|engineer|architect|governance|analyst)|machine learning"
                  r"|big data|business intelligence|\bpower bi\b|databricks|\betl\b"
                  r"|\banalytics\b|database (?:administrator|reliability)",
        "excepcion": r"support|soporte|helpdesk|service desk",
        "activa": True,
    },
    {
        # La excepción es a propósito estrecha: dejar pasar cualquier "analyst" colaba
        # cosas como "Compliance Risk Analyst". Las que sí aplicas ("Cyber Security
        # Analyst", "SIEM Analyst") no coinciden con este patrón, así que están a salvo.
        "nombre": "Ciberseguridad avanzada (salvo junior o SOC)",
        "patron": r"pentest|penetration test|red team|blue team|forensic|forense|malware"
                  r"|reverse engineer|\bgrc\b|governance|compliance|\bauditor\b|auditor[ií]a"
                  r"|threat hunt|incident response|\bciso\b|arquitect|architect"
                  r"|hacking [ée]tico|ethical hack",
        "excepcion": r"\bjunior\b|\bjr\b|\bn1\b|\bsoc\b|\bsiem\b",
        "activa": True,
    },
    {
        "nombre": "Atención al cliente y administración",
        "patron": r"customer (?:service|support|care|experience)|atenci[óo]n al cliente"
                  r"|service representative|administrative assistant|administrativ[oa]"
                  r"|back office|front office|recepcionist|teleoperador|call center",
        "excepcion": r"\bit\b|helpdesk|help desk|service desk|t[ée]cnic|technical|soporte t",
        "activa": True,
    },
    {
        "nombre": "Ingeniería industrial y de procesos",
        "patron": r"process engineer|production (?:engineer|planner|process)|controls engineer"
                  r"|manufacturing|industrial engineer|quality engineer|mechanical|electrical engineer"
                  r"|\bmes\b|automation delivery",
        "excepcion": r"\bit\b|software|helpdesk|service desk|soporte",
        "activa": True,
    },
    {
        # Puestos que se venden por el idioma y suelen estar en otro país
        # ("Hebrew Speaking Jobs In Bulgaria").
        "nombre": "Empleos ofertados por idioma",
        "patron": r"\b(?:hebrew|irish|greek|dutch|german|french|italian|polish|czech|slovak"
                  r"|hungarian|romanian|bulgarian|turkish|arabic|danish|swedish|norwegian"
                  r"|finnish|portuguese|spanish|lithuanian|latvian|estonian)[- ]speaking\b",
        "excepcion": None,
        "activa": True,
    },
    {
        "nombre": "Analistas de otras áreas",
        "patron": r"cash (?:planning|management)|social insights|transmission analyst|buyside"
                  r"|excel analyst|product analyst|revenue analyst|credit analyst|risk analyst"
                  r"|insights analyst|pricing analyst|billing analyst|payroll analyst",
        "excepcion": None,
        "activa": True,
    },
    {
        "nombre": "Investigación académica",
        "patron": r"research engineer|researcher|\bphd\b|postdoc|investigador|doctorand",
        "excepcion": None,
        "activa": True,
    },
    {
        # "IT support specialist (relocation to Madrid)": el puesto está bien pero
        # obliga a mudarse. Solo se conserva si el destino es Barcelona.
        "nombre": "Exige reubicarse a otra ciudad",
        "patron": r"relocation to|relocate to|reubicaci[óo]n a|traslado a|mudanza a"
                  r"|\bonsite in\b|on[- ]site in",
        "excepcion": r"barcelona",
        "activa": True,
    },
    {
        # "Dutch Market", "Benelux": piden ese idioma aunque no pongan "speaking".
        "nombre": "Mercado de otro idioma",
        "patron": r"\b(?:dutch|german|french|italian|polish|czech|nordic|benelux|hebrew"
                  r"|greek|turkish|romanian|hungarian|bulgarian|danish|swedish|norwegian"
                  r"|finnish|portuguese|japanese|korean|arabic)\s+market\b"
                  r"|\bmercado\s+(?:alem[áa]n|franc[ée]s|italiano|neerland[ée]s)",
        "excepcion": None,
        "activa": True,
    },
    {
        "nombre": "Gestión de cuentas",
        "patron": r"account manage(?:ment|r)|gesti[óo]n de cuentas",
        "excepcion": None,
        "activa": True,
    },
    {
        # Con 2,5 años de experiencia las prácticas no aportan; se han ocultado 14 y
        # no se ha aplicado a ninguna. Desactívala si alguna vez interesan.
        "nombre": "Prácticas y becas",
        "patron": r"\bintern\b|internship|\bbecari[oa]\b|\bbecas?\b|apprentice|apprenticeship"
                  r"|\btrainee\b|working student|estudiante en pr[áa]cticas",
        "excepcion": None,
        "activa": True,
    },
    {
        # «Diseñador/a Técnico/a - Soporte a Dirección» (Jooble, 27-10) avisaba con 6,5
        # solo por la palabra «soporte». Medido: 0 en el historial, 19 en la base del
        # servidor y ninguna favorita ni aplicada.
        "nombre": "Diseño (técnico, gráfico o de producto)",
        "patron": r"dise[ñn]ador|\bdesigner\b",
        "excepcion": None,
        "activa": True,
    },
]

# ----------------------------------------------------------------------
# RELEVANCIA: señales y penalizaciones del título
# ----------------------------------------------------------------------

# Los mismos conceptos aparecen también en catalán desde que EURES trae los volcados de
# Feina Activa, que publica en catalán. Sin "informàtic" (con acento grave, que no casa
# con inform[áa]tic), "sistemes" o "seguretat", el MISMO puesto puntuaba 1,2 escrito en
# catalán y 6,2 en castellano: los mejores hallazgos de esa fuente quedaban por debajo
# del corte de «Encaje alto» solo por el idioma en el que estaban escritos.
#
# "suport" va aparte y pesa menos (1,5) que "soporte"/"support" (2,5), y no es un
# capricho: en castellano e inglés, dentro de este corpus, "soporte" casi siempre es
# soporte técnico, mientras que en los títulos del sector público catalán "suport" es
# una palabra genérica —"tècnic/a de suport en formació", "de suport a la qualitat",
# "suport serveis d'aigua potable"—. Con los dos al mismo peso, esos puestos empataban
# a 4,7 con un técnico informático de verdad; con "suport" a 1,5 el ruido se queda todo
# en 3,7 o menos y quien discrimina pasa a ser la palabra informática del título, que
# es lo que se quería. Lo que separa no es el peso en sí (la distancia media apenas
# cambia) sino que deja de haber solape entre los dos grupos.
#
# Medido contra el histórico con el que se validó este sistema: AUC 0,8775 -> 0,8767 y AP prácticamente igual. No mejora ahí porque en el
# histórico solo hay 10 títulos en catalán; el cambio no es para ese corpus, es para
# que la fuente nueva no llegue ciega.
#
# AJUSTE DEL 2026-10-21: perfil acotado a soporte IT/informática y ciberseguridad
# defensiva junior (SOC/SIEM/SOAR/SecOps), sin ERP ni QA. Los pesos anteriores están en
# buscador.py.bak-20261021. Cambios de diseño:
#   - Una señal central de «puesto objetivo» (4,0) que, con UNA señal más (junior, SOC,
#     soporte, IT, Barcelona…), cruza el umbral de Encaje alto (5,0). Antes, un título
#     fuerte sin «junior» se quedaba corto ("Helpdesk" 3,7, "SOC Analyst" en remoto 3,8,
#     "NOC Operator" 3,2) y en cambio "junior + técnico + Barcelona" cruzaba solo con
#     esas palabras genéricas (5,2: "Técnico Junior Eléctrico", "Técnico Junior Compras").
#   - "junior" baja de 3,0 a 2,0 y "técnico" de 1,0 a 0,5: ordenan, pero ya no deciden.
#   - Fuera los pesos positivos de ERP y QA, que pasan a penalizar.
# Medido sobre el histórico de la instalación original (sin senior): AUC 0,862 -> 0,920;
# las ofertas marcadas como interesantes que llegarían a Telegram pasan de menos de la
# mitad a más del 90 %, a cambio de unas 2,5 veces más avisos.
_TEC = r"t[éèe]cnic\w*(?:/a)?"
_PUESTO_SOPORTE_IT = (
    r"help ?desk|service ?desk|mesa de ayuda|\bcau\b|microinform"
    r"|\b(?:it|ti|tic|inform[áàa]tic\w*|technical|" + _TEC + r"|desktop|end[- ]user|workplace|euc"
    r"|systems?|sistemas|sistemes)\W+(?:\w+\W+)?(?:support|soporte|suport)\b"
    r"|\b(?:support|soporte|suport)\W+(?:\w+\W+){0,2}(?:it|ti|tic|inform[áàa]tic\w*|t[éèe]cnico"
    r"|usuari\w*|sistemas|sistemes|n1|n2|l1|l2|level ?[12]|nivel ?[12])\b"
    r"|\bit (?:technician|support|helpdesk|specialist)\b|\bsupport (?:technician|specialist)\b"
    r"|" + _TEC + r" (?:j[úu]nior )?(?:de |d'|en |d’)?(?:soporte|suport|sistemas|sistemes"
    r"|inform[áàa]tic\w*|microinform\w*|helpdesk|it\b|ti\b|redes|xarxes|linux|windows)"
    r"|administrador\w*(?:/a)? (?:de )?sistemas|sysadmin|systems? administrator"
    r"|operador\w*(?:/a)? de sistemas"
    # 2026-10-24: puestos de soporte IT que no dicen «soporte» (vistos en la base: «On-site
    # Technician», «Administrador/a Endpoint», «Workplace Administrator – Microsoft 365»,
    # «Técnico/a SAT informático»).
    r"|on-?site (?:technician|support|engineer)|field (?:it|technician)|endpoint"
    r"|workplace (?:administrator|technician|specialist|engineer)|microsoft 365|\bm365\b|\bintune\b"
    r"|" + _TEC + r" sat inform"
)
# «secops» con lookbehind: dentro de «DevSecOps» no es un puesto de operaciones de
# seguridad, y antes del 2026-10-24 colaba tres «DevSecOps junior» a Telegram con 8,0.
_PUESTO_CIBER_DEFENSIVA = (
    r"\bsoc\b|cybersoc|security operations|(?<!dev)secops|\bsiem\b|\bsoar\b|blue team|\bedr\b|\bxdr\b"
    r"|\bmdr\b|incident (?:handler|responder)|threat (?:detection|monitoring)"
    r"|analistas? (?:de )?seguridad"
    r"|(?:analista|analyst|" + _TEC + r"|operador\w*(?:/a)?|operator|specialist|especialista)\W+"
    r"(?:\w+\W+){0,2}(?:ciberseguridad|ciberseguretat|cybersecurity|cyber security|seguridad inform\w*)"
    r"|(?:security|cybersecurity|cyber security|ciberseguridad|seguridad inform\w*) "
    r"(?:analyst|analista|operator|operador|technician|specialist)"
)
_PUESTO_MONITORIZACION = r"monitoriza\w*|monitoritza\w*|\bmonitoring\b|\bnoc\b|24x7|24/7"
SENALES_RELEVANCIA = [
    ('puesto de soporte IT o ciberseguridad', 4.0, _PUESTO_SOPORTE_IT + "|" + _PUESTO_CIBER_DEFENSIVA + "|" + _PUESTO_MONITORIZACION),
    ('junior o nivel 1', 2.0, r"\bjunior\b|\bjr\b|j[úu]nior|\bn1\b|nivel 1|entry[- ]level|\bl1\b|level 1|tier ?1"),
    ('SOC / SIEM', 1.5, r"\bsoc\b|cybersoc|\bsiem\b|\bsoar\b|(?<!dev)secops|security operations|blue team"),
    ('monitorización', 1.0, _PUESTO_MONITORIZACION),
    ('helpdesk o soporte', 1.0, r"help ?desk|service desk|\bcau\b|soporte|support|\bsuport\b"),
    ('IT o sistemas', 1.0, r"\bit\b|\btic\b|inform[áàa]tic|sistemas|sistemes|systems"),
    ('ciberseguridad', 1.0, r"cybersecurity|ciberseguridad|ciberseguretat|\bcyber\b|seguridad|seguretat"),
    ('técnico o especialista', 0.5, r"t[éèe]cnic[oa]?|technician|specialist|especialista"),
    ('analista', 0.5, r"\banalyst\b|analista"),
    # Que sea de Barcelona o en remoto pesa: son los dos escenarios viables
    ('Barcelona', 1.0, r"barcelona"),
    # Terminología de helpdesk en español e inglés que ninguna otra señal reconoce: «SAU»
    # es «Servicio de Atención al Usuario». Se escribió viendo tres relevantes que se
    # quedaban en 1,0–3,5 («Operador/a de Soporte Telefónico SAU», «Remote Computer User
    # Support», «Junior IT Operations Technician»), así que NO se puede validar con esa
    # muestra; se validó mirando los 11 títulos que casan en las 2.621 ofertas de la
    # base, todos de soporte (ver DESIGN.md §3).
    ('atención al usuario (SAU, user support)', 2.0,
     r"user support|soporte telef[oó]nico|\bsau\b|operations technician|computer user"),
]
PENALIZACIONES_RELEVANCIA = [
    ('senior, lead o manager', -3.0, r"\bsenior\b|\bsr\.?\b|\blead\b|principal|\bmanager\b|director|jefe de"),
    ('consultoría', -2.5, r"\bconsultant\b|consultor"),
    ('desarrollo de software', -2.0, r"developer|desarrollador|programador"),
    ('business analyst o datos', -1.5, r"business analyst|data (?:analyst|scientist|engineer)"),
    ('prácticas o becario', -1.5, r"\bintern\b|internship|becari|apprentice|trainee"),
    ('sanidad o prevención de riesgos', -1.5, r"\bprl\b|prevenci[óo]n de riesgos|enfermer|sanitari"),
    ('ERP', -2.5, r"\berp\b|\bodoo\b|\bsap\b|dynamics|salesforce|business central"),
    ('QA o testing', -1.5, r"\bqa\b|tester|testing"),
    ('GRC, IAM o pentest', -2.0, r"\bgrc\b|governance|compliance|\biam\b|pentest|red team|\bot security"),
    ('auditoría', -2.0, r"\bauditor|assurance|\baudit\b"),
    ('preventa, comercial o fraude', -3.0, r"preventa|pre-?sales|comercial|\bfraude?\b|\bfraud\b"),
    # N3 es un nivel de especialista: «Administrador/a de Sistemas VMware (N2/N3)» o
    # «Técnico/a de Sistemas Windows N2/N3» llegaban a Telegram con 6,0–6,5.
    ('nivel 3', -2.0, r"\bn3\b|nivel 3|\bl3\b|level 3|n2/n3|n2-n3"),
    # N2 RESTA, NO DESCARTA. Descartarlo se llevaba ofertas de soporte N2 que sí
    # interesaban.
    # Medido: descartar y restar dan la MISMA precisión (76 %), pero descartar baja el
    # recall del 93 % al 91 % y restar no cuesta nada. Con −2,0 esas tres pasan de 6,5 a
    # 4,5: siguen en la web, dejan de llegar a Telegram. Un «N2/N3» acumula esta y la de
    # arriba, que es lo que se quiere: es a la vez de nivel 2 y de nivel 3.
    ('nivel 2', -2.0, r"\bn2\b|\bl2\b|nivel\s*2\b|level\s*2\b"),
    # Administrador de sistemas: para un perfil sin experiencia en IT no es una puerta
    # de entrada, y era la segunda causa de falsos positivos (5 de 32 en la muestra:
    # «Corporate IT Systems Administrator» 6,0, «Administrador de sistemas Interim» 6,0).
    # La excepción NO es cosmética: los puestos junior de administración de sistemas,
    # endpoint y workplace sí son de entrada, y sin ella la regla se llevaba ofertas
    # marcadas como interesantes.
    ('administrador de sistemas', -2.5, r"^(?!.*(?:\bjunior\b|\bjr\b|j[uú]nior|\bn1\b|\bl1\b|sin experiencia"
           r"|endpoint|workplace|24x7)).*(?:administrador|administrator|sysadmin"
           r"|systems? admin)"),
    ('DevOps o cloud engineer', -1.5, r"devsecops|\bdevops\b|\bsre\b|cloud engineer|ingenier[oa](?:/a)? cloud"),
    # Añadidas el 2026-10-25 tras medir los falsos positivos con las etiquetas confirmadas
    # a mano. Las tres restan, ninguna descarta, y ninguna toca una oferta marcada como
    # interesante (comprobado antes de añadirlas).
    #
    # Idiomas que el perfil no admite: es un ejemplo; ajústalo a los tuyos. Ya había una regla de DESCARTE por «with German», pero no
    # reconocía «Junior Help Desk Engineer (Spanish, English and French)», que llegó a
    # Telegram con 8,0. Se resta en vez de descartar por la misma razón que N2. Con −3,0
    # se quedaba justo en 5,0, el umbral de aviso, que cuenta como cruzarlo: una oferta que
    # exige un idioma que no hablas no puede quedarse en el borde, así que −4,0.
    ('idioma que no hablas', -4.0,
     r"\b(?:french|franc[eé]s|german|alem[aá]n|italian|italiano|dutch|neerland[eé]s|flamenco)\b"),
    # «Technical Support and Development Specialist» (Nippon Gases) puntuaba 5,5 por
    # «Technical Support». El «business development» ya lo recoge la regla de ventas.
    ('desarrollo en el título', -2.0, r"(?<!business )\bdevelopment\b|\bdesarrollo\b"),
    # Coordinar es un puesto de gestión, no de entrada: «SOC Coordinator» puntuaba 6,5.
    ('coordinación', -2.0, r"coordinat|coordinador"),
]

# ----------------------------------------------------------------------
# SEÑALES DE LA DESCRIPCIÓN (2026-10-25)
#
# La puntuación era solo función del título (más un +1 por «barcelona»), y por eso no
# distinguía lo que el título no dice: «Cloud Security Operations Engineer» tiene título
# de puesto SOC y descripción de ingeniería de plataforma (Kubernetes, Terraform, CI/CD).
#
# Solo hay UNA señal de este tipo, y es deliberado. Se probaron varias contra la muestra
# etiquetada Y contra las ofertas marcadas como interesantes, y casi todas se cayeron en
# el segundo contraste:
#   - «stack cloud/DevOps» en cualquier título: penalizaba ofertas marcadas de sistemas
#     junior con contenedores y un acierto de la muestra. Acotada a títulos de «security
#     engineer», no toca ninguna.
#   - «PLC/SCADA/automatización industrial»: no aparece en NINGUNA descripción; los dos
#     «Técnico de sistemas Automatización» de SEIDOR no son industriales.
#   - «desarrollo» en la descripción: aparece en el 57 % de los aciertos.
#   - Python/scripting: está en una parte de las marcadas como interesantes.
# ----------------------------------------------------------------------
_PUESTO_SEGURIDAD_INGENIERIA = re.compile(
    r"(?:security|ciber\w*|seguridad|secops|\bsoc\b)\W.{0,30}(?:engineer|ingenier)"
    r"|(?:engineer|ingenier\w*)\W.{0,30}(?:security|ciber\w*|seguridad|secops)",
    re.IGNORECASE,
)
_STACK_INGENIERIA_CLOUD = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"kubernetes|\bk8s\b|\bhelm\b",
        r"terraform|\bbicep\b|cloudformation",
        r"ansible|\bpuppet\b",
        r"ci\s*/\s*cd|jenkins|gitlab ci|github actions",
        r"\bdocker\b",
        r"infrastructure as code|\biac\b",
        r"devops|devsecops|\bsre\b",
    )
]
MINIMO_TERMINOS_STACK = 2
REGLAS_DE_LA_DESCRIPCION = [
    {
        "nombre": "ingeniería cloud/DevOps en un puesto de seguridad",
        "peso": -2.5,
        "titulo": _PUESTO_SEGURIDAD_INGENIERIA,
        "terminos": _STACK_INGENIERIA_CLOUD,
        # Dos términos DISTINTOS como mínimo: uno suelto («conocimientos de Docker») no
        # convierte un puesto SOC en uno de plataforma.
        "minimo": MINIMO_TERMINOS_STACK,
    },
]

# Excepción deliberada a la protección de "urls_excluidas"/republicaciones (ver almacen.py,
# sección CATEGORÍAS SIEMPRE VISIBLES): para las ofertas que casen con alguno de estos
# patrones, una republicación con URL nueva de algo ya oculto o aplicado SÍ vuelve a aparecer
# y a notificarse, en vez de descartarse en silencio. Decisión del usuario para el perfil de
# ciberseguridad junior (SOC/analista N1): ese segmento tiene poca oferta y alta rotación de
# anuncios, así que prefiere asumir notificaciones repetidas de la misma vacante antes que
# arriesgarse a que una reaparición legítima quede enterrada para siempre por una decisión
# tomada sobre una copia anterior del anuncio. Mismo formato que "reglas_descarte"
# (nombre/activa/patron), pero sin "excepcion": aquí no hace falta, el patrón ya es tan
# específico como se quiere que sea.
CATEGORIAS_SIEMPRE_VISIBLES = [
    {
        "nombre": "SOC junior / analista junior de ciberseguridad",
        "activa": True,
        "patron": r"(?=.*\b(?:junior|jr\.?|n1)\b)(?=.*\b(?:soc|ciberseguridad|cyber\s?security)\b)",
    },
]

# ----------------------------------------------------------------------
# LO QUE APLICA EL SERVIDOR (ver el docstring): no se retoca, se copia.
# ----------------------------------------------------------------------
REGLAS_DESCARTE = [
    {
        "nombre": "Otras áreas profesionales",
        "activa": True,
        "patron": (
            r"account executive|\bsales\b|\bsdr\b|\bbdr\b|business development|customer success|"
            r"account manager|key account|legal counsel|abogad[oa]|tenders? officer|accountant|"
            r"accounting|\bfinance\b|financial|treasury|billing|payroll|reconciliation|"
            r"purchase to pay|procurement|supply chain|inventory|capacity planning|"
            r"demand planning|logistics|recruiter|talent acquisition|human resources|"
            r"recursos humanos|\brrhh\b|people (?:partner|experience|operations)|marketing|"
            r"copywriter|content manager|community manager|manufacturing engineer|"
            r"servicio de campo|field service"
        ),
        "excepcion": (
            r"\b(?:it|ict|security|cyber|ciber|seguridad|soc|siem|helpdesk|help desk|service desk|"
            r"soporte|support|sysadmin|systems?|sistemas|infrastructure|infraestructura|network|"
            r"redes|erp|odoo|sap|saas|qa|testing|tester|devops|cloud|technical|t[eé]cnic[oa]|"
            r"inform[aá]tic[oa])\b"
        ),
    },
    {
        "nombre": "Oficios no técnicos (sanidad, obra, PRL…)",
        "activa": True,
        "patron": (
            r"enfermer[ií]a|\btcae\b|auxiliar de cl[ií]nica|sanitari[oa]|gesti[óo]n sanitaria|"
            r"hematolog|farmac[ié]|veterinari|fisioterap|odontolog|psicolog|aparcamiento|"
            r"conductor|repartidor|camarer[oa]|cociner[oa]|limpieza|ascensor|fontaner|electricist|"
            r"alba[ñn]il|soldador|carpinter|climatizaci[óo]n|refrigeraci[óo]n|calefacci[óo]n|"
            r"obra civil|delineante|prevenci[óo]n de riesgos|\bprl\b|\bcae\b|nataci[óo]n|aquagym|"
            r"monitor deportiv|socorrist|entrenador|peluquer|est[ée]tic|dependient[ae]|cajer[oa]|"
            r"inmobiliari|real estate|estaciones de servicio|centros de mayores|\btasoc\b|"
            r"\btafad\b|t[ée]cnic[oa][/ ]?a? de campo|t[ée]cnic[oa] itinerante|electromec[áa]nic|"
            r"\bclaims?\b|siniestros?|suscripci[óo]n|underwrit|p[óo]lizas?|\bsettlement\b|"
            r"liquidaci[óo]n|tesorer[ií]a|book ?keep|maintenance engineer|equipment service|"
            r"sistemas de seguridad|seguridad electr[óo]nica|\bcctv\b|videovigilancia|alarmas|"
            r"control de accesos|vigilante|fotovoltaic|\bsolar\b|renovable|guiado|maquinaria|"
            r"topograf|bodeguer|baja it\b|\btpv\b|\bpos\b"
        ),
        "excepcion": (
            r"inform[áa]tic|software|\bit\b|helpdesk|help desk|service desk|ciber|cyber|\bsoc\b|"
            r"\berp\b|sistemas inform"
        ),
    },
    {
        "nombre": "Business analyst (salvo si es QA o soporte)",
        "activa": True,
        "patron": r"business (?:analyst|systems? analyst)|technical business|systems analyst",
        "excepcion": r"tester|testing|\bqa\b|quality|support|soporte|helpdesk|service desk",
    },
    {
        "nombre": "Consultoría (salvo soporte o SOC)",
        "activa": True,
        "patron": r"\bconsultant\b|\bconsultor(?:a|/a|es)?\b|consultor[ií]a|advisory",
        "excepcion": r"soporte|support|help ?desk|service desk|\bsoc\b|\bsiem\b",
    },
    {
        "nombre": "ERP y aplicaciones de negocio (fuera del perfil)",
        "activa": True,
        "patron": (
            r"\bsap\b|microsoft dynamics|\bd365\b|business central|salesforce|navision|netsuite|"
            r"oracle fusion|workday|\berp\b|\bodoo\b|\bcrm\b|\bsage\b|\ba3\b|holded"
        ),
        "excepcion": r"help ?desk|service desk|\bcau\b",
    },
    {
        "nombre": "Desarrollo de software",
        "activa": True,
        "patron": (
            r"developer|desarrollador|programador|programuotojas|software engineer|fullstack|"
            r"full[- ]stack|back[- ]?end|front[- ]?end|\bsharepoint\b"
        ),
        "excepcion": r"support|soporte|helpdesk|service desk|\bqa\b|tester",
    },
    {
        "nombre": "Exige un idioma que no hablas",
        "activa": True,
        "patron": (
            r"\bwith (?:german|dutch|french|italian|polish|czech|slovak|lithuanian|latvian|"
            r"estonian|swedish|norwegian|danish|finnish|greek|portuguese|hungarian|romanian|"
            r"bulgarian|turkish|arabic|hebrew|japanese|korean)\b|(?:german|dutch|french|italian|"
            r"polish|czech|lithuanian|latvian|swedish|norwegian|danish|finnish|greek|hungarian|"
            r"romanian|turkish)[- ]speaking|n[ai]tive (?:german|dutch|french|italian|polish|"
            r"nordic)|\b(?:alem[áa]n|neerland[ée]s|holand[ée]s|flamenco|franc[ée]s|italiano|"
            r"polaco|checo|eslovaco|lituano|leton[ée]s|estonio|sueco|noruego|dan[ée]s|"
            r"finland[ée]s|griego|portugu[ée]s|h[uú]ngaro|ruman[oa]|b[úu]lgaro|turco|[áa]rabe|"
            r"hebreo|japon[ée]s|coreano)\b|\beuskera\b|\beuskara\b|\baran[ée]s\b|\bgallego\b|"
            r"\bgalego\b|\b(?:german|dutch|french|italian|polish|portuguese|nordic|swedish|danish|"
            r"norwegian|finnish)\s+(?:speaker|native)|\b(?:german|dutch|french|italian|polish|"
            r"portuguese)\s*(?:&|and|/)\s*(?:german|dutch|french|italian|polish|portuguese|"
            r"english)\b"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Gestión de proyectos y producto",
        "activa": True,
        "patron": (
            r"project manager|\bpmo\b|product (?:owner|manager|leader)|scrum master|"
            r"delivery (?:manager|lead)|program manager|jefe de proyecto"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Datos, BI e IA",
        "activa": True,
        "patron": (
            r"data (?:scientist|engineer|architect|governance|analyst)|machine learning|big data|"
            r"business intelligence|\bpower bi\b|databricks|\betl\b|\banalytics\b|"
            r"database (?:administrator|reliability)"
        ),
        "excepcion": r"support|soporte|helpdesk|service desk",
    },
    {
        "nombre": "Ciberseguridad avanzada (salvo junior o SOC)",
        "activa": True,
        "patron": (
            r"pentest|penetration test|red team|blue team|forensic|forense|malware|"
            r"reverse engineer|\bgrc\b|governance|compliance|\bauditor\b|auditor[ií]a|threat hunt|"
            r"incident response|\bciso\b|arquitect|architect|hacking [ée]tico|ethical hack"
        ),
        "excepcion": r"\bjunior\b|\bjr\b|\bn1\b|\bsoc\b|\bsiem\b",
    },
    {
        "nombre": "Atención al cliente y administración",
        "activa": True,
        "patron": (
            r"customer (?:service|support|care|experience)|atenci[óo]n al cliente|"
            r"service representative|administrative assistant|administrativ[oa]|back office|"
            r"front office|recepcionist|teleoperador|call center|atenci[óo] telef[òo]nica|"
            r"atenci[óo] ciutadana|atenci[óo]n ciudadana|executive assistant|asistente de direcci"
        ),
        "excepcion": r"\bit\b|helpdesk|help desk|service desk|t[ée]cnic|technical|soporte t",
    },
    {
        "nombre": "Ingeniería industrial y de procesos",
        "activa": True,
        "patron": (
            r"process engineer|production (?:engineer|planner|process)|controls engineer|"
            r"manufacturing|industrial engineer|quality engineer|mechanical|electrical engineer|"
            r"\bmes\b|automation delivery"
        ),
        "excepcion": r"\bit\b|software|helpdesk|service desk|soporte",
    },
    {
        "nombre": "Empleos ofertados por idioma",
        "activa": True,
        "patron": (
            r"\b(?:hebrew|irish|greek|dutch|german|french|italian|polish|czech|slovak|hungarian|"
            r"romanian|bulgarian|turkish|arabic|danish|swedish|norwegian|finnish|portuguese|"
            r"spanish|lithuanian|latvian|estonian)(?:\s*[-–]\s*|\s+)speaking\b"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Analistas de otras áreas",
        "activa": True,
        "patron": (
            r"cash (?:planning|management)|social insights|transmission analyst|buyside|"
            r"excel analyst|product analyst|revenue analyst|credit analyst|risk analyst|"
            r"insights analyst|pricing analyst|billing analyst|payroll analyst"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Investigación académica",
        "activa": True,
        "patron": r"research engineer|researcher|\bphd\b|postdoc|investigador|doctorand",
        "excepcion": None,
    },
    {
        "nombre": "Exige reubicarse a otra ciudad",
        "activa": True,
        "patron": (
            r"relocation to|relocate to|reubicaci[óo]n a|traslado a|mudanza a|\bonsite in\b|"
            r"on[- ]site in"
        ),
        "excepcion": r"barcelona",
    },
    {
        "nombre": "Mercado de otro idioma",
        "activa": True,
        "patron": (
            r"\b(?:dutch|german|french|italian|polish|czech|nordic|benelux|hebrew|greek|turkish|"
            r"romanian|hungarian|bulgarian|danish|swedish|norwegian|finnish|portuguese|japanese|"
            r"korean|arabic)\s+market\b|\bmercado\s+(?:alem[áa]n|franc[ée]s|italiano|"
            r"neerland[ée]s)"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Gestión de cuentas",
        "activa": True,
        "patron": r"account manage(?:ment|r)|gesti[óo]n de cuentas",
        "excepcion": None,
    },
    {
        "nombre": "Prácticas y becas",
        "activa": True,
        "patron": (
            r"\bintern\b|internship|\bbecari[oa]\b|\bbecas?\b|apprentice|apprenticeship|"
            r"\btrainee\b|working student|estudiante en pr[áa]cticas|\bpr[áàa]ctic(?:as|ques)\b|"
            r"\bpracticum\b"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Ciberseguridad no defensiva (GRC, IAM, OT, ofensiva…)",
        "activa": True,
        "patron": (
            r"\bgrc\b|governance|gobierno|compliance|cumplimiento|\briesgos?\b|\brisks?\b|\biam\b|"
            r"identity|identidad|sailpoint|cyberark|\bot (?:security|cyber)|\bot\b.*seguridad|"
            r"embebid|embedded|automoci|automotive|\bdlp\b|appsec|application security|devsecops|"
            r"security by design|secure (?:code|development)|pentest|penetration|red team|hacking|"
            r"ofensiv|offensive|vulnerability research|criptograf|cryptograph"
        ),
        "excepcion": (
            r"\bsoc\b|\bsiem\b|\bsoar\b|\bsecops\b|security operations|monitoriz|monitoring|"
            r"blue team|detecci|detection"
        ),
    },
    {
        "nombre": "Analista funcional y desarrollo heredado",
        "activa": True,
        "patron": r"analista funcional|functional analyst|\bcobol\b|\bas ?400\b|mainframe|\babap\b",
        "excepcion": r"help ?desk|service desk|soporte|support",
    },
    {
        "nombre": "Sin señal del perfil (ni soporte IT ni ciberseguridad defensiva)",
        "activa": True,
        "patron": (
            r"^(?!.*(?:help ?desk|service ?desk|mesa de ayuda|\bcau\b|soporte|support|\bsuport\b|"
            r"microinform|inform[áàa]tic|\bit\b|\btic\b|\bti\b|sistemas|sistemes|\bsystems?\b|"
            r"sysadmin|\bredes\b|\bxarxes\b|network|\bsoc\b|\bsiem\b|\bsoar\b|secops|security|"
            r"seguridad|seguretat|ciber|cyber|vulnerab|blue team|incident|monitoriz|monitoritz|"
            r"monitoring|\bnoc\b|24x7|24/7|workplace|endpoint|desktop|on-?site|\bintune\b|"
            r"microsoft 365|\bm365\b|\bn1\b|\bl1\b|nivel 1|level 1|tier ?1|windows|linux))"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Remoto restringido a otro país",
        "activa": True,
        "patron": (
            r"\b(?:remote|remoto|teletrabajo|home ?office)\b.{0,25}?\b(?:within|only|in|en|"
            r"from)\s+(?:croatia|poland|portugal|germany|deutschland|france|italy|netherlands|"
            r"ireland|romania|bulgaria|greece|czech\w*|hungary|slovakia|slovenia|austria|belgium|"
            r"denmark|sweden|finland|norway|estonia|latvia|lithuania|luxembourg|malta|cyprus|"
            r"the uk|uk|united kingdom|us|usa|united states)\b"
        ),
        "excepcion": None,
    },
    {
        "nombre": "Falsos amigos de IT (baja IT, preventa)",
        "activa": True,
        "patron": r"bodeguer|baja it\b|sustituci[óo]n baja|preventa|pre-?sales\b|presales",
        "excepcion": None,
    },
    {
        "nombre": "Diseño (técnico, gráfico o de producto)",
        "activa": True,
        "patron": r"dise[ñn]ador|\bdesigner\b",
        "excepcion": None,
    },
]

# Los términos que Jooble busca para este perfil y dónde. Son de la persona desde MP3 (decidido
# por el titular el 30-10: el cupo se reparte entre perfiles), y estos son los que hasta entonces
# vivían fijos en fuentes_extra/jooble.py. Pocos y amplios: cada uno es una petición de un cupo de
# 500. «SOC» a secas no se usa porque en un buscador genérico trae «social» y «sociedad».
JOOBLE_TERMINOS = ["helpdesk", "soporte técnico", "service desk", "ciberseguridad"]
JOOBLE_UBICACION = "Barcelona"

# El resto de claves «de la persona» de aquel config.json. Con esto un perfil con bloque
# propio no necesita leer nada personal de `config.json`: si lo leyera, lo que MP4 cambie ahí
# para el titular se le colaría al adicional sin que nadie lo hubiera decidido.
CONFIG_PERSONA = {
    "categorias": {
        "ciberseguridad": {
            "etiqueta": "Ciberseguridad defensiva junior (SOC/SIEM)",
            "activa": True,
            "terminos": [
                "junior soc",
                "soc analyst n1",
                "junior cybersecurity",
                "analista soc junior",
                "analista n1 ciberseguridad",
                "siem analyst"
            ]
        },
        "it_saas": {
            "etiqueta": "Soporte IT / informática",
            "activa": True,
            "terminos": [
                "helpdesk",
                "service desk",
                "junior it support",
                "tecnico soporte n1",
                "tecnico informatico"
            ]
        }
    },
    "ambitos": {
        "barcelona_local": {
            "etiqueta": "Barcelona (radio limitado)",
            "activo": True,
            "sitios": [
                "indeed",
                "linkedin",
                "infojobs",
                "eures",
                "tecnoempleo",
                "jooble"
            ],
            "location": "Barcelona, Spain",
            "usar_distancia": True,
            "is_remote": False,
            "usar_country_indeed": True
        },
        "remoto_es": {
            "etiqueta": "Remoto España",
            "activo": True,
            "sitios": [
                "indeed",
                "linkedin",
                "infojobs",
                "eures",
                "tecnoempleo",
                "jooble"
            ],
            "location": "Spain",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": True
        },
        "remoto_ue": {
            "etiqueta": "Remoto Unión Europea",
            "activo": True,
            "sitios": [
                "linkedin",
                "jooble"
            ],
            "location": "European Union",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": False
        },
        "remoto_mundial": {
            "etiqueta": "Remoto resto del mundo",
            "activo": False,
            "sitios": [
                "linkedin"
            ],
            "location": "",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": False
        }
    },
    "centro_lat": 41.3874,
    "centro_lon": 2.1686,
    "centro_nombre": "Barcelona (Plaça Catalunya)",
    "max_distancia_km": 8,
    "distancia_portal_millas": 8,
    "exigir_remoto_fuera_de_barcelona": True,
    "anos_experiencia_max": 3,
    "filtrar_senior": True,
    "niveles_excluidos": [
        "mid-senior level",
        "director",
        "executive"
    ],
    "descartar_otros_idiomas": True,
    "idiomas_permitidos": [
        "es",
        "ca",
        "en",
        "ru"
    ],
    "descartar_fuera_de_perfil": True,
    "empresas_excluidas": [
        "Evolve",
        "Tech Nova",
        "TechNova"
    ],
    "pesos_relevancia": {
        "atención al usuario (SAU, user support)": 4.0
    },
    "usar_modelo_entrenado": False,
    "dias_sin_repetir_aviso": 14,
    "telegram_max_avisos_individuales": 10,
    "reglas_descarte": REGLAS_DESCARTE,
    "categorias_siempre_visibles": CATEGORIAS_SIEMPRE_VISIBLES,
    "jooble_terminos": JOOBLE_TERMINOS,
    "jooble_ubicacion": JOOBLE_UBICACION,
}
