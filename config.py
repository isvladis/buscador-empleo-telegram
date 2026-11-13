"""
Configuración del buscador: valores por defecto, migración de config.json entre
versiones del código, y carga/guardado.

Separado de buscador.py porque no tiene relación con scraping, filtros ni scoring
(ver INVESTIGACION.md, «Refactors: qué sí y qué no»). Depende de las constantes de
dominio de buscador.py (reglas de descarte, países UE, patrones de fuera-de-la-UE)
para construir CONFIG_POR_DEFECTO, así que el import va en sentido config -> buscador;
buscador.py, a su vez, importa cargar_config/guardar_config de este módulo con imports
diferidos (dentro de las funciones que los necesitan, no al principio del archivo) para
no cerrar un ciclo: así, se importe primero buscador o config, el otro módulo ya está
totalmente inicializado en el momento en que hace falta.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from buscador import (
    CONTRATO_NO_UE_POR_DEFECTO,
    PAIS_NO_UE_EN_TITULO,
    PAISES_UE,
    REGLAS_POR_DEFECTO,
    SENAL_GLOBAL_POR_DEFECTO,
)
from reglas.titular_2026_10 import (
    CATEGORIAS_SIEMPRE_VISIBLES,
    JOOBLE_TERMINOS,
    JOOBLE_UBICACION,
)

# JOBSPY_DATA_DIR permite redirigir los JSON persistentes a un volumen de Docker (ver
# docker-compose.yml) sin tocar nada más; sin la variable de entorno, el comportamiento
# en local es idéntico al de siempre (los JSON viven junto al código).
DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent)))
CONFIG_PATH = DIR_BASE / "config.json"

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# SECRETOS: FUERA DE config.json
#
# El token del bot y los chat_id estaban en claro dentro de config.json, que además
# servía entero por `GET /api/config` a cualquiera de la red local (ver AUDIT.md §0.2).
# Ahora el valor efectivo sale del ENTORNO, que es donde no se comparte por accidente:
# docker-compose lo lee de un fichero .env con permisos 600 que no está en git.
#
# La transición es deliberadamente suave para no dejar al usuario sin avisos de un día
# para otro: si la variable de entorno NO está puesta, se sigue usando el valor que
# haya en config.json (con un aviso en el log). Y guardar_config() solo deja de
# persistir la clave cuando su variable SÍ está puesta: así el fichero se limpia solo
# en cuanto el entorno toma el relevo, y nunca antes, que sería perder el dato.
# ----------------------------------------------------------------------
SECRETOS_EN_ENTORNO = {
    "telegram_bot_token": "JOBSPY_TELEGRAM_TOKEN",
    "telegram_chat_id": "JOBSPY_TELEGRAM_CHAT_ID",
    "telegram_chat_ids_adicionales": "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA",
}


def _del_entorno(clave: str) -> str | None:
    """Valor de la variable de entorno de un secreto, o None si no está puesta.
    Una variable puesta pero vacía cuenta como no puesta: es la forma natural de
    escribir «esto no está configurado» en un .env."""
    valor = os.environ.get(SECRETOS_EN_ENTORNO[clave], "").strip()
    return valor or None


def _aplicar_secretos_del_entorno(config: dict) -> dict:
    """El entorno manda sobre el fichero. Devuelve el mismo dict, modificado."""
    for clave in SECRETOS_EN_ENTORNO:
        valor = _del_entorno(clave)
        if valor is not None:
            config[clave] = valor
        elif str(config.get(clave) or "").strip():
            logger.warning(
                "%s sigue leyéndose de config.json. Pásalo a la variable de entorno %s "
                "(ver .env.example): mientras esté en el fichero, cualquiera de la red "
                "local puede leerlo.", clave, SECRETOS_EN_ENTORNO[clave],
            )
    return config


def estado_telegram(config: dict) -> dict:
    """
    Qué hay configurado, SIN el valor («token puesto, de la variable de entorno»). Lo usaba
    la web, que no devolvía el token; desde MP5 no hay web y queda para comprobarlo a mano
    y en las pruebas de secretos.
    """
    import notificaciones  # import diferido: notificaciones no debe cargarse al importar config

    token = str(config.get("telegram_bot_token") or "").strip()
    destinatarios = notificaciones._chat_ids(config)
    if not token:
        origen = "sin configurar"
    elif _del_entorno("telegram_bot_token") is not None:
        origen = "variable de entorno"
    else:
        origen = "config.json (conviene moverlo al entorno)"
    return {
        "token_configurado": bool(token),
        "destinatarios": len(destinatarios),
        "origen": origen,
    }

# ----------------------------------------------------------------------
# MIGRACIÓN DE CONFIG
#
# _fusionar() reemplaza la lista "sitios" de un ámbito entera con la que haya en el
# config.json guardado (correcto: si el usuario ha quitado un sitio a propósito, no
# hay que devolvérselo). Pero eso significa que un config.json creado antes de que
# existiera una fuente nueva (p.ej. "infojobs") se queda con la lista vieja para
# siempre, aunque CONFIG_POR_DEFECTO ya la incluya: la fuente nueva nunca se llega a
# ejecutar y no hay forma de notarlo salvo leyendo el código.
#
# La solución es una migración explícita y versionada: cada entrada añade, una sola
# vez, los sitios nuevos de una versión a los ámbitos que ya existían en el
# config.json del usuario. "_config_version" en el propio config.json (persistida al
# guardar) marca hasta qué migración se ha aplicado ya, así que si el usuario quita
# ese sitio DESPUÉS de migrar, no se le vuelve a añadir en la siguiente carga.
CONFIG_VERSION_ACTUAL = 2
MIGRACIONES_SITIOS: list[tuple[int, dict[str, list[str]]]] = [
    # v1: se añade InfoJobs como fuente para los ámbitos donde tiene cobertura.
    (1, {"barcelona_local": ["infojobs"], "remoto_es": ["infojobs"]}),
    # v2: EURES, el portal público de empleo de la UE, que es por donde llegan los
    # volcados de Feina Activa y del SEPE.
    #
    # Solo en los dos ámbitos españoles, y eso está medido: sin restringir país devolvió
    # 325 ofertas de las que solo 7 eran de España; las otras 318 eran de Alemania,
    # Países Bajos, Francia y Bélgica, presenciales y en su idioma, y el filtro de
    # idioma se llevó 185 de golpe. Cero supervivientes útiles a cambio de casi 100
    # veces más tráfico. Donde EURES aporta es en España.
    #
    # Se activa por defecto AUNQUE en la corrida de medición ninguna oferta suya llegó
    # a Encaje alto (la mejor se quedó en 4,7, tres décimas por debajo del corte de 5).
    # Decisión explícita del usuario tras ver los números: de las 9 ofertas exclusivas
    # de esa corrida, 3 eran soporte/informática de verdad y no estaban en ningún otro
    # sitio; las otras 6 eran ruido de vocabulario catalán genérico (ver README, sección
    # «Vocabulario catalán»). Tres ofertas reales y exclusivas por corrida, aunque no
    # crucen el umbral de relevancia, ya compensan para un mercado tan pequeño como este.
    #
    # "empresa" (fuentes_extra/empresas.py) NO se activa aquí a propósito, mismo criterio
    # que con Google: el código está terminado y probado, pero en tres corridas reales
    # completas trajo 0 ofertas exclusivas —las 3 que pasaron los filtros ya estaban en
    # LinkedIn—, así que no se enciende sola. Basta con añadirla a los `sitios` del ámbito
    # que se quiera en config.json (hasta MP5, también desde el panel de la web). Ver el README, sección «Fuentes».
    (2, {
        "barcelona_local": ["eures"],
        "remoto_es": ["eures"],
    }),
]


def _migrar_sitios(config: dict) -> tuple[dict, list[str]]:
    """Aplica las migraciones de MIGRACIONES_SITIOS pendientes. Devuelve el config
    (modificado in-place en sus dicts anidados) y una lista de cambios para loguear."""
    version = config.get("_config_version", 0)
    cambios = []
    for version_migracion, sitios_por_ambito in MIGRACIONES_SITIOS:
        if version >= version_migracion:
            continue
        for clave_amb, nuevos in sitios_por_ambito.items():
            amb = config.get("ambitos", {}).get(clave_amb)
            if not isinstance(amb, dict):
                continue  # el usuario ya no tiene (o nunca tuvo) ese ámbito
            sitios = amb.setdefault("sitios", [])
            for sitio in nuevos:
                if sitio not in sitios:
                    sitios.append(sitio)
                    cambios.append(f"{clave_amb}: + {sitio}")
        version = version_migracion
    config["_config_version"] = version
    return config, cambios


CONFIG_POR_DEFECTO = {
    # Ver MIGRACIONES_SITIOS más arriba: marca qué migraciones ya se han aplicado a
    # un config.json existente. Una instalación nueva (sin config.json guardado)
    # arranca directamente en la última versión, así que no le hace falta migrar
    # nada.
    "_config_version": CONFIG_VERSION_ACTUAL,
    "hours_old": 168,
    "results_wanted": 30,
    "max_distancia_km": 5,
    "distancia_portal_millas": 5,
    "centro_lat": 41.3874,  # Plaça Catalunya, Barcelona
    "centro_lon": 2.1686,
    "centro_nombre": "Barcelona (Plaça Catalunya)",
    "country_indeed": "spain",
    "filtrar_senior": True,
    # Sin esto LinkedIn no devuelve descripción, nivel de experiencia ni tipo de
    # contrato, pero cuesta una petición extra por oferta (~1s cada una).
    "linkedin_fetch_description": True,
    # Qué sistema puntúa la relevancia (ver el bloque RELEVANCIA en buscador.py):
    # false = pesos escritos a mano, true = modelo_relevancia.json.
    # Se queda en false porque el modelo entrenado todavía no gana a las reglas con
    # el historial disponible; entrenar_modelo.py rehace la comparación cuando
    # quieras.
    "usar_modelo_entrenado": False,
    # Reglas de descarte por título, cada una con su excepción (ver
    # buscador.REGLAS_POR_DEFECTO).
    "descartar_fuera_de_perfil": True,
    "reglas_descarte": REGLAS_POR_DEFECTO,
    # Niveles de experiencia a los que no optas, según el dato de LinkedIn.
    "niveles_excluidos": ["mid-senior level", "director", "executive"],
    # Años de experiencia que tienes: una oferta cuya descripción pida más resta
    # relevancia y se marca en la tarjeta y en Telegram (ver EXPERIENCIA EXIGIDA en
    # buscador.py).
    "anos_experiencia_max": 3,
    # Días que se conserva la descripción de una oferta oculta. Pasado ese tiempo se
    # borra solo el texto (el registro sigue, para deshacer el ocultado y para detectar
    # republicaciones): ver almacen.podar_descripciones_ocultas. 0 = no podar nunca.
    "dias_descripcion_ocultas": 30,
    # Idioma: se descarta lo que no puedas leer. Solo se juzga con descripción
    # suficiente, porque con un título suelto la detección falla bastante.
    "descartar_otros_idiomas": True,
    "idiomas_permitidos": ["es", "ca", "en", "ru"],
    "idioma_min_caracteres": 200,
    "idioma_confianza_minima": 0.90,
    # Fuera de la UE: descartado salvo que diga explícitamente que se puede trabajar
    # desde otro país. Comprobarlo a mano no compensa y nunca ha dado resultado.
    "descartar_fuera_de_ue": True,
    "paises_permitidos": PAISES_UE,
    "patron_senal_global": SENAL_GLOBAL_POR_DEFECTO,
    "patron_contrato_no_ue": CONTRATO_NO_UE_POR_DEFECTO,
    "patron_pais_no_ue_titulo": PAIS_NO_UE_EN_TITULO,
    # Fuera del área de Barcelona, solo remoto (a una presencial en Madrid no vas).
    "exigir_remoto_fuera_de_barcelona": True,
    # Búsquedas simultáneas. Medido: subirlo apenas acorta la espera porque LinkedIn
    # limita por IP y scrape_jobs ya paraleliza entre portales dentro de cada
    # búsqueda.
    "busquedas_en_paralelo": 2,
    # Descargar las descripciones al final, solo de las ofertas que pasan los
    # filtros, en vez de pedírselas a jobspy durante cada búsqueda (donde se repiten
    # mucho).
    "descripciones_dos_fases": True,
    "descripciones_en_paralelo": 4,
    # Consultas NUEVAS a Nominatim por corrida para los perfiles con área por distancia, a 1
    # por segundo: un minuto como mucho. Lo que no entra se queda en «no se sabe» y se conserva.
    # Es del sistema, no de un perfil: la cuota es de la IP (ver buscador.Geocodificador).
    "geocodificaciones_por_corrida": 60,
    # Lo que evita que añadir personas cargue más a los portales (D5, MP3). El presupuesto no sube:
    # con más consultas únicas que presupuesto, cada una tarda más en repetirse, y eso es lo que
    # no hace bloquear a LinkedIn. 33 es lo que se hace hoy; subirlo exige permiso del titular.
    "max_busquedas_por_corrida": 33,
    # Tope de términos de un perfil de catálogo, para que nadie se coma el presupuesto (D5).
    "max_terminos_por_perfil": 6,
    # Tope de perfiles (D4, decidido por el titular el 30-10). El perfil sombra también cuenta.
    "max_perfiles": 3,
    # Búsqueda automática en segundo plano (ver el scheduler en app.py): cada
    # cuántas horas se repite sola y si está encendida. No hace falta migración para
    # estos dos campos: al ser escalares sueltos (no listas ni diccionarios
    # anidados), _fusionar ya les da el valor por defecto de aquí cuando un
    # config.json antiguo no los trae.
    "busqueda_automatica_activa": False,
    "busqueda_automatica_horas": 6,
    # Notificación a Telegram cuando una búsqueda AUTOMÁTICA (no las manuales, para
    # no saturar) trae ofertas nuevas de «Encaje alto». Se rellenan a mano tras
    # crear el bot con @BotFather y consultar el chat_id propio; vacíos, la búsqueda
    # sigue funcionando igual pero sin enviar ningún mensaje (ver notificaciones.py).
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # Desde MP5 no la lee nadie: las altas son por invitación (/invitar). De MP3 a MP5 cada chat_id de
    # aquí se convertía, una vez, en un perfil propio. Se queda la clave para que un .env viejo con
    # JOBSPY_TELEGRAM_CHAT_IDS_EXTRA no rompa nada.
    "telegram_chat_ids_adicionales": "",
    # Cuántas ofertas se avisan de una en una por corrida. Las que pasen de ahí van en
    # un único mensaje de resumen, con su enlace, en vez de perderse (ver
    # notificaciones.notificar_ofertas_alto_encaje). Con la búsqueda cada hora, una
    # corrida normal trae 1 o 2 de Encaje alto, así que el tope casi nunca entra en
    # juego: está para la primera corrida del día o después de una parada.
    "telegram_max_avisos_individuales": 10,
    # Botones «Me interesa / Aplicada / No me interesa» en cada aviso (telegram_botones.py).
    # false los quita sin reiniciar. Hasta MP5 apagaba también el hilo que escucha; ya no,
    # porque el bot es ahora por donde se administra (telegram_bot.py).
    "telegram_botones": True,
    # Días que una vacante (título + empresa) no se vuelve a avisar aunque el portal la
    # republique con un enlace nuevo. El dedupe difuso no junta dos anuncios con fechas
    # de publicación distintas, y hace bien —son publicaciones reales distintas—, pero
    # para avisar es la misma vacante. Pasado ese plazo sí se vuelve a avisar: una
    # vacante que reaparece dos meses después es una vacante que se ha vuelto a abrir.
    # 0 lo desactiva. Las categorías «siempre visibles» no se ven afectadas.
    "dias_sin_repetir_aviso": 14,
    # Lo que Jooble busca para este perfil: su API tiene cupo, así que no usa los términos de
    # `categorias` sino estos pocos (ver fuentes_extra/jooble.py). Solo cuentan si "jooble" está en
    # los sitios de un ámbito que no sea remoto.
    "jooble_terminos": JOOBLE_TERMINOS,
    "jooble_ubicacion": JOOBLE_UBICACION,
    # Pesos de la puntuación que se quieren sobrescribir, por NOMBRE de regla (los que
    # salen como «motivos» en el aviso): {"nivel 2": -3.0}. Vacío = los de buscador.py. Sin
    # campo en el panel; se edita en config.json y surte efecto sin reiniciar. Un valor que
    # no sea un número se ignora con un aviso en el log.
    "pesos_relevancia": {},
    # Empresas cuyas ofertas se descartan siempre (ver buscador.filtrar_empresas_excluidas).
    # Se compara el nombre normalizado por palabras completas; las variantes de escritura
    # van como entradas separadas. No hay campo en el panel: se edita aquí o en
    # config.json (una vez guardada la config, manda la lista del fichero).
    #   - Evolve, TECH NOVA: anuncios de «ciberseguridad» que son formación, no empleo.
    "empresas_excluidas": ["Evolve", "Tech Nova", "TechNova"],
    # Excepción a la protección de "urls_excluidas"/republicaciones, con su porqué en
    # reglas/titular_2026_10.py. Una regla con el mismo formato que "reglas_descarte".
    "categorias_siempre_visibles": CATEGORIAS_SIEMPRE_VISIBLES,
    "categorias": {
        "ciberseguridad": {
            "etiqueta": "Ciberseguridad",
            "activa": True,
            "terminos": ["junior cybersecurity"],
        },
        "it_saas": {
            "etiqueta": "Técnico IT/SaaS",
            "activa": True,
            "terminos": ["IT support SaaS", "functional analyst QA"],
        },
    },
    "ambitos": {
        "barcelona_local": {
            "etiqueta": "Barcelona (radio limitado)",
            "activo": True,
            # "google" fuera a propósito: bloqueado por detección anti-bot del lado
            # de Google (ver nota en README.md y JobSpy issue #302). El soporte para
            # construir google_search_term se mantiene en el código por si se
            # desbloquea, pero no se activa por defecto en ningún ámbito.
            # "empresa" existe como fuente y funciona, pero no viene activada:
            # medido, 0 ofertas exclusivas en tres corridas (ver README, «Fuentes»).
            "sitios": ["indeed", "linkedin", "infojobs", "eures"],
            "location": "Barcelona, Spain",
            "usar_distancia": True,
            "is_remote": False,
            "usar_country_indeed": True,
        },
        "remoto_es": {
            "etiqueta": "Remoto España",
            "activo": True,
            "sitios": ["indeed", "linkedin", "infojobs", "eures"],
            "location": "Spain",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": True,
        },
        "remoto_ue": {
            "etiqueta": "Remoto Unión Europea",
            "activo": True,
            # Sin "eures": fuera de España devuelve casi solo ofertas presenciales
            # en idiomas que no se leen (ver la nota de la migración v2).
            "sitios": ["linkedin"],
            "location": "European Union",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": False,
        },
        "remoto_mundial": {
            "etiqueta": "Remoto resto del mundo",
            # Apagado por defecto (ver README, sección
            # «Fuentes»): en el histórico de la instalación original, ninguna de
            # sus ofertas acabó en candidatura ni en favorita, y varias ocupaban
            # «Encaje alto» sin producir nada. Mismo criterio
            # que Google y las páginas de empresa: código intacto y disponible
            # desde el panel, apagado porque no compensa.
            "activo": False,
            "sitios": ["linkedin"],
            "location": "",
            "usar_distancia": False,
            "is_remote": True,
            "usar_country_indeed": False,
        },
    },
}


def _fusionar(base: dict, encima: dict) -> dict:
    """Fusión superficial por clave, recursiva en los diccionarios anidados."""
    resultado = dict(base)
    for clave, valor in (encima or {}).items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _fusionar(resultado[clave], valor)
        else:
            resultado[clave] = valor
    return resultado


def cargar_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            guardada = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config = _fusionar(CONFIG_POR_DEFECTO, guardada)
            # _fusionar no debe "prestarle" a un config.json antiguo la versión del
            # default: si el archivo en disco no traía _config_version, es que nunca
            # se ha migrado (versión 0), aunque CONFIG_POR_DEFECTO ya esté en la última.
            version_previa = (
                guardada.get("_config_version", 0) if isinstance(guardada, dict) else 0
            )
            config["_config_version"] = version_previa
            config, cambios = _migrar_sitios(config)
            if config["_config_version"] != version_previa:
                if cambios:
                    logger.info("Config migrada a la versión %s: %s",
                                config["_config_version"], "; ".join(cambios))
                else:
                    logger.info("Config marcada como versión %s (nada que migrar)",
                                config["_config_version"])
                guardar_config(config)
            return _aplicar_secretos_del_entorno(config)
        except (json.JSONDecodeError, OSError):
            pass
    return _aplicar_secretos_del_entorno(dict(CONFIG_POR_DEFECTO))


def guardar_config(config: dict) -> None:
    """
    Escribe config.json sin los secretos que ya vengan del entorno.

    Solo se omite la clave cuya variable de entorno está puesta: si el usuario todavía
    no ha movido el token al .env, se sigue guardando donde estaba, porque borrarlo del
    fichero sin tener de dónde leerlo dejaría las notificaciones muertas en silencio
    (la web, hasta MP5, guardaba la config en cuanto se tocaba cualquier campo).
    """
    guardable = {
        clave: valor
        for clave, valor in config.items()
        if not (clave in SECRETOS_EN_ENTORNO and _del_entorno(clave) is not None)
    }
    CONFIG_PATH.write_text(
        json.dumps(guardable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
