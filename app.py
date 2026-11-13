"""
El servicio: busca ofertas cada pocas horas y atiende el bot de Telegram. Sin web desde MP5.

Arranque:  python app.py     (en el Toshiba lo arranca el contenedor; Ctrl+C o SIGTERM lo paran)

Un proceso con tres hilos (D7, EVOLUCION-MULTIPERFIL.md §2):

- el **planificador**, en el hilo principal: cada POLL_SCHEDULER_SEGUNDOS mira si toca corrida;
- el **lector de Telegram** (telegram_bot.escuchar): mensajes, comandos y botones;
- el **trabajador**: una cola para lo largo, una cosa detrás de otra (las corridas y /misdatos).

Hasta MP5 esto era una web Flask en 0.0.0.0:5000, sin contraseña (AUDIT.md §0.2), que además
sostenía el listado entero puntuado en memoria. Lo que hacía cada endpoint y cómo se hace ahora
está en CAMBIOS-MP5.md. Los parámetros de búsqueda siguen en config.json y todo lo de cada perfil
en perfiles.db (ver perfiles.py).
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
import threading
import time
from datetime import datetime, timedelta

import almacen
import avisos_perfil
import buscador
import config as config_mod
import etiquetas
import notificaciones
import perfiles
import telegram_bot
from dedupe_difuso import clave_de_vacante as _clave_de_vacante
from fuentes_extra import salud

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Estado de la corrida en curso o de la última: lo que /estado enseña. Hasta MP5 lo leía la barra
# de progreso de la web; la clave `resumen` la sigue leyendo el conductor de equivalencia.
BUSQUEDA = {
    "activa": False,
    "mensaje": "",
    "hecho": 0,
    "total": 0,
    "error": None,
    "resumen": None,
    "iniciada_en": None,
    "terminada_en": None,
    "duracion_s": None,
}
_lock_busqueda = threading.Lock()

# Corridas fallidas seguidas, para avisar al titular. Hasta el 12-11 una corrida que fallaba solo se veía
# en el log y en /estado, salvo que fuera la base corrupta (BaseNoDisponible, que tiene su aviso). El
# 11-11 el disco del Toshiba se quedó en solo lectura a las 21:43 UTC y el bot siguió arrancado sin poder
# guardar nada: el titular se enteró una hora después, y por un `docker compose build`, no por el bot.
#
# Vive en memoria y no en un fichero a propósito: lo que más probablemente falla es el disco, y mandar un
# mensaje a Telegram no lo necesita. Si el contenedor se reinicia la cuenta empieza de cero, que es lo
# correcto (un arranque limpio no arrastra fallos de antes).
#
# Dos y no una: los portales no hacen fallar la corrida (cada fuente tiene su cortafuegos), pero un fallo
# suelto existe (una base ocupada, un corte de red al cargar) y con corridas cada hora dos seguidas son
# como mucho dos horas a ciegas. Descartado avisar en cada fallo: con el disco roto sería un mensaje por
# hora hasta que alguien lo arregle; se avisa una vez, y otra cuando vuelve a funcionar.
FALLOS_PARA_AVISAR = 2
_FALLOS = {"seguidos": 0, "avisado": False}


def _corrida_fallida(error: Exception) -> None:
    """Apunta el fallo y, al llegar a FALLOS_PARA_AVISAR, se lo dice al titular una sola vez."""
    with _lock_busqueda:
        _FALLOS["seguidos"] += 1
        seguidos, toca = _FALLOS["seguidos"], _FALLOS["seguidos"] >= FALLOS_PARA_AVISAR and not _FALLOS["avisado"]
    if not toca:
        return
    try:
        config = config_mod.cargar_config()
        # El error, con el token tachado (regla 3) y recortado: es el mismo texto que ya enseña /estado.
        detalle = notificaciones.tachar_token(f"{type(error).__name__}: {error}",
                                              (config.get("telegram_bot_token") or "").strip())[:300]
        texto = (f"🛑 <b>La búsqueda ha fallado {seguidos} veces seguidas</b> y no se está guardando ni "
                 f"avisando de nada, a nadie.\n\n<code>{notificaciones._esc(detalle)}</code>\n\n"
                 "Si habla de «readonly» o de «disk I/O», es el disco del servidor. Te avisaré cuando "
                 "una corrida vuelva a salir bien; mientras, /estado.")
        if notificaciones._enviar(config, texto, "corridas fallidas seguidas"):
            with _lock_busqueda:
                _FALLOS["avisado"] = True
    except Exception:
        # Como en las incidencias de fuentes: avisar de un fallo no puede ser otro fallo. Sin marcar
        # `avisado`, la corrida siguiente lo vuelve a intentar.
        logger.exception("No se ha podido avisar al titular de las corridas fallidas")


def _corrida_correcta() -> None:
    """Pone la cuenta a cero y, si se había avisado del fallo, dice que ya funciona."""
    with _lock_busqueda:
        seguidos, avisado = _FALLOS["seguidos"], _FALLOS["avisado"]
        _FALLOS.update(seguidos=0, avisado=False)
    if not avisado:
        return
    try:
        notificaciones._enviar(config_mod.cargar_config(),
                               f"✅ <b>La búsqueda vuelve a funcionar</b>, después de {seguidos} corridas fallidas.",
                               "las corridas vuelven a funcionar")
    except Exception:
        logger.exception("No se ha podido avisar al titular de que las corridas vuelven a funcionar")


def _es_el_de_antes(perfil: dict) -> bool:
    """
    ¿Sigue siendo este el perfil con el que empezó la corrida?

    Una corrida tarda unos 140 s con los perfiles que leyó al empezar, y el bot va en otro hilo: en ese
    rato alguien puede haberse borrado (/borrar) o reiniciado (/reiniciar, o el titular desde su ficha).
    Lo que la corrida trae se buscó para el perfil de antes. Guardárselo a un perfil reiniciado le dejaría
    la lista con ofertas del perfil viejo y, como ya no estaría vacía, sin corrida de arranque al acabar
    la encuesta; a uno borrado, enlaces y avisos con el chat_id de alguien que pidió que no quedara nada.

    No mira el estado: quien se pausa a mitad sigue recibiendo lo de esa corrida, como hasta ahora. Y el
    titular sin fila (una base recién migrada, o el PC) es el de siempre. Cuesta una lectura por perfil.
    """
    chat_id = perfil["chat_id"]
    actual = perfiles.obtener(chat_id)
    if actual is None:
        return chat_id == perfiles.titular()
    return actual["datos"].get("reiniciado") == perfil["datos"].get("reiniciado")


def _ejecutar_busqueda(config: dict, reemplazar: bool, automatica: bool = False) -> None:
    """
    Una corrida para todos los perfiles activos.

    `automatica` dice si el titular recibe avisos (ver _notificar_perfiles): las del planificador y
    las de /corrida, sí. `reemplazar` era el botón «Reemplazar» de la web, que vaciaba la lista del
    titular antes de guardar. Sin web no lo usa nadie del servicio; se queda porque el conductor de
    equivalencia (pruebas/conductor_titular.py) lo pide en el árbol viejo y en el nuevo, y la
    comparación tiene que poder hacerse con el mismo guion.
    """
    def progreso(mensaje, hecho, total):
        with _lock_busqueda:
            BUSQUEDA.update(mensaje=mensaje, hecho=hecho, total=total)

    inicio = time.monotonic()
    with _lock_busqueda:
        BUSQUEDA.update(iniciada_en=datetime.now().isoformat(timespec="seconds"), duracion_s=None)
    try:
        # Dos orígenes para no redescargar nada: las descripciones de la base (ofertas
        # que el usuario ya tiene) y las de ofertas que se descargaron en corridas
        # anteriores pero que los filtros descartaron, y que por eso no están en la base
        # (ver almacen.recordar_descripciones: sin esto se vuelven a pedir a LinkedIn en
        # cada corrida, ~300 por corrida medidas, para volver a descartarlas).
        #
        # Si perfiles.db no está disponible salta aquí, antes de consultar ningún portal.
        #
        # El caché ya no se carga: son dos vistas que preguntan por enlace, y la corrida solo pregunta
        # por los que tiene delante (11-11: entero costaba +136 MiB de pico; ver almacen.py).
        en_cache, competencia_en_cache = almacen.cargar_cache_vistas()
        activos = perfiles.activos()
        # Las de todos: es la descarga lo que se comparte, y una oferta que ya tiene otro perfil
        # no se vuelve a pedir a LinkedIn. El texto solo sale de aquí hacia el caché de descarga.
        en_base: dict[str, str] = {}
        for perfil in activos:
            en_base.update(almacen.descripciones_en_base(perfil["chat_id"]))
        conocidas = almacen.Conocidas(en_base, en_cache)  # si está en los dos, manda la base

        def recordar(descripciones: dict[str, str], competencia: dict[str, dict]) -> None:
            """
            Guarda en el caché lo que no esté ya en la base.

            Lo que ya vive en la base NO entra: duplicarlo costaría espacio y tiempo
            de parseo para nada. Lo que ya estaba en el CACHÉ sí se vuelve a
            pasar, aunque no haya cambiado: así almacen.recordar_descripciones() le
            refresca la fecha de "visto por última vez" y la poda de 7 días mide lo que
            su docstring dice medir —cuánto lleva un enlace sin aparecer en ningún
            portal— en vez de cuándo se descargó por primera vez.

            Antes se filtraba por `conocidas` (base + caché), así que a una entrada del
            caché no se le tocaba nunca la fecha y caducaba a los 7 días de bajarla,
            siguiera saliendo o no. Con la ventana de 38 h eso casi nunca llegaba a
            costar una descarga —una oferta deja de salir mucho antes—, pero hacía que
            todo lo guardado el mismo día caducara a la vez.
            """
            guardables = {u: d for u, d in descripciones.items() if u not in en_base}
            if guardables:
                # Antes de guardar: después ya estarían todas, y un enlace que vino con su descripción
                # del portal no se le había preguntado al caché.
                nuevas = sum(1 for u in guardables if u not in en_cache)
                total = almacen.recordar_descripciones(guardables, competencia)
                logger.info(
                    "Descripciones en caché para no volver a descargarlas: %s nuevas, "
                    "%s refrescadas, %s en el caché.",
                    nuevas, len(guardables) - nuevas, total,
                )

        titular = perfiles.titular()
        guardados: list[tuple[dict, dict, bool]] = []  # (perfil, resumen, ¿su primera corrida?)
        marca = None  # una para todos: ver el `ahora` de almacen.fusionar_ofertas

        def guardar(perfil: dict, df) -> None:
            nonlocal marca
            chat_id = perfil["chat_id"]
            if not _es_el_de_antes(perfil):
                logger.info("Un perfil se ha borrado o reiniciado durante la corrida: lo que se buscó para él no se guarda.")
                return
            es_titular = chat_id == titular
            # También la primera tras la encuesta (MP8): el arranque solo hizo hasta 8 de sus consultas, y las que
            # quedan le traen de golpe todo lo publicado. `primera_pendiente` lo pone la encuesta y lo quita
            # _notificar_perfiles al terminar esa corrida.
            # El titular, solo con la marca (12-11): la pone el arranque de su cuestionario, si se reinicia o lo
            # rehace con la lista vacía, y sin esto le llegaba todo de golpe y la marca no se quitaba nunca. Sin
            # ella, lo de siempre: su lista vacía no es una «primera vez» (una base nueva), y nada cambia (regla 4).
            primera = ((not es_titular and perfiles.contar_ofertas(chat_id) == 0)
                       or bool(perfil["datos"].get("primera_pendiente")))
            registros = etiquetas.a_registros(df)
            marca = marca or datetime.now().isoformat(timespec="seconds")
            # «Reemplazar» era el botón de la web, y la web era del titular: a otro perfil le vaciaría
            # una lista que no ha pedido vaciar.
            guardados.append((perfil, almacen.fusionar_ofertas(
                registros, reemplazar=reemplazar and es_titular, chat_id=chat_id,
                config=perfiles.config_efectiva(perfil, config), ahora=marca), primera))

        buscador.buscar_para_perfiles(
            activos, config, guardar, almacen.urls_excluidas,
            progreso=progreso,
            descripciones_conocidas=conocidas,
            al_descargar_descripciones=recordar,
            competencia_conocida=competencia_en_cache,
        )
        # Lo que se apunta es lo del titular (y si está pausado, el primero que haya).
        resumen = next((r for p, r, _ in guardados if p["chat_id"] == titular),
                       guardados[0][1] if guardados else None)
        with _lock_busqueda:
            BUSQUEDA.update(
                activa=False,
                mensaje="Búsqueda completada",
                resumen=resumen,
                error=None,
                terminada_en=datetime.now().isoformat(timespec="seconds"),
                duracion_s=round(time.monotonic() - inicio, 1),
            )
        # Sin la barra de progreso de la web, esta línea es la que dice en el log que la corrida ha
        # acabado (DESPLIEGUE.md: antes de recrear el contenedor hay que verla). Es antes de los
        # avisos, igual que «Búsqueda automática:», para no mezclar la duración con Telegram.
        logger.info("Corrida terminada en %.1f s.", BUSQUEDA["duracion_s"])
        # Aquí y no al final: lo que se cuenta es buscar y guardar. Los avisos tienen sus propios
        # cortafuegos y lo que no sale se queda en la cola.
        _corrida_correcta()
        _notificar_perfiles(guardados, automatica)
        # Esto sí en ambas: que una fuente se haya desactivado sola por bloqueos no se
        # ve en el listado (simplemente llegan menos ofertas), así que se avisa siempre.
        _notificar_incidencias_de_fuentes()
    except Exception as e:
        with _lock_busqueda:
            BUSQUEDA.update(
                activa=False,
                mensaje="La búsqueda ha fallado",
                error=str(e),
                terminada_en=datetime.now().isoformat(timespec="seconds"),
                duracion_s=round(time.monotonic() - inicio, 1),
            )
        # Hasta MP5 el error solo se veía en la web; ahora en /estado y aquí.
        logger.error("La corrida ha fallado tras %.1f s: %s", BUSQUEDA["duracion_s"], e)
        if isinstance(e, perfiles.BaseNoDisponible):
            # Por aquí no se llega al aviso del final del `try`, y es el que importa: sin
            # esto, el sistema estaría parado sin que nadie lo supiera.
            _notificar_incidencias_de_fuentes()
        else:
            # La base corrupta ya tiene su aviso, justo encima; dos mensajes del mismo fallo sobran.
            _corrida_fallida(e)
    finally:
        # El caché de descripciones solo está abierto mientras hay corrida: al cerrarlo su -wal se
        # consolida, y entre corridas no queda ningún fichero cogido.
        almacen.cerrar_cache()


def _notificar_incidencias_de_fuentes() -> None:
    """
    Manda a Telegram las fuentes que se hayan desactivado solas por bloqueos.

    La cola solo se vacía si el mensaje llega a enviarse: si Telegram falla o todavía no
    está configurado, la incidencia se queda pendiente y se reintenta en la búsqueda
    siguiente, en vez de perderse (que es justo lo que haría invisible el problema).
    """
    try:
        incidencias = salud.incidencias_pendientes()
        if not incidencias:
            return
        logger.warning(
            "Hay %s incidencia(s) de fuentes desactivadas por bloqueo: %s",
            len(incidencias), ", ".join(i.get("fuente", "?") for i in incidencias),
        )
        if notificaciones.notificar_incidencias_fuentes(config_mod.cargar_config(), incidencias):
            salud.marcar_avisadas(incidencias)
    except Exception:
        # Avisar de un fallo no puede ser a su vez motivo de que la búsqueda parezca rota
        logger.exception("No se han podido notificar las incidencias de fuentes")


def _indice_de_avisos_previos(
    en_base: list[dict], ofertas_nuevas: list[dict], config: dict
) -> dict[tuple[str, str], str]:
    """
    Vacantes de las que ya se avisó hace poco, para no volver a avisar de la misma.

    Por qué hace falta algo aparte del dedupe: el dedupe difuso NO fusiona dos anuncios
    con fechas de publicación distintas, y hace bien —son dos publicaciones reales del
    mismo puesto—, pero para avisar es la misma vacante y avisar dos veces molesta.
    Medido sobre el histórico: de 41 republicaciones, 3 habrían avisado dos veces
    (la misma oferta de Cobser Consulting entró el 22 y el 23 de octubre, las dos con
    6,5). Poco, pero son justo los avisos que más irritan.

    Solo cuentan las vistas en los últimos `dias_sin_repetir_aviso` días: si una vacante
    reaparece dos meses después, es una vacante que se ha vuelto a abrir y sí interesa.

    Las categorías «siempre visibles» (el SOC junior) quedan fuera de esto en quien
    llama: para ese segmento el usuario prefiere ver repeticiones antes que perderse una
    reaparición legítima (ver config.categorias_siempre_visibles).
    """
    dias = int(config.get("dias_sin_repetir_aviso", 14) or 0)
    if dias <= 0:
        return {}
    nuevas = {o.get("job_url") for o in ofertas_nuevas}
    corte = datetime.now() - timedelta(days=dias)
    indice: dict[tuple[str, str], str] = {}
    for oferta in en_base:
        if oferta.get("job_url") in nuevas:
            continue  # es una de las de ahora, no un aviso previo
        visto = oferta.get("visto_por_primera_vez")
        if not visto:
            continue
        try:
            cuando = datetime.fromisoformat(str(visto))
        except ValueError:
            continue
        if cuando < corte:
            continue
        clave = _clave_de_vacante(oferta)
        if not clave[0] or not clave[1]:
            continue  # sin empresa no se puede afirmar que sea la misma vacante
        anterior = indice.get(clave)
        if anterior is None or str(visto) > anterior:
            indice[clave] = str(visto)[:10]
    return indice


# La primera corrida de un perfil le trae todo lo que sigue publicado de golpe. Queda como visto y
# solo se avisan estas, las mejores: el resto sería una avalancha (D8).
AVISOS_EN_LA_PRIMERA_CORRIDA = 5


def _notificar_perfiles(guardados: list[tuple[dict, dict, bool]], automatica: bool) -> None:
    """
    Los avisos de cada perfil, con lo que le ha entrado a él.

    El titular recibe los suyos cuando `automatica` es True: las corridas del planificador y, desde
    MP5, las de /corrida. Las únicas que no le avisaban eran las del botón de la web, que lanzaba
    delante del listado; sin web, una corrida que no avisara dejaría lo nuevo sin ver nunca. A los
    demás se les avisa siempre: nunca tuvieron web.

    **Un fallo con un perfil no se lleva por delante los avisos de los demás, y el titular tampoco**
    (P1 de CAMBIOS-MP10 §4): iba el primero y sin `try`, así que su excepción salía de este bucle,
    marcaba la corrida como fallida y dejaba a los demás perfiles sin sus avisos de esa corrida. Con
    un perfil solo no se notaba; con el piloto, sí. Lo único suyo que queda aparte es que recibe
    cuando `automatica`, y que su log dice qué ofertas son (de los demás, solo cifras: regla 5).
    """
    titular = perfiles.titular()
    for i, (perfil, resumen, primera) in enumerate(guardados):
        nuevas = resumen.get("ofertas_nuevas", [])
        es_titular = perfil["chat_id"] == titular
        if es_titular and not automatica:
            continue
        posicion = None if es_titular else f"{i + 1}/{len(guardados)}"
        try:
            if not _es_el_de_antes(perfil):
                # Entre guardar y avisar: son segundos, pero el aviso iría a quien acaba de pedir que se borre todo.
                logger.info("Perfil %s: se ha borrado o reiniciado durante la corrida; no se le avisa de lo de antes.",
                            posicion or "titular")
                continue
            _notificar_si_alto_encaje(nuevas, perfil, posicion, primera)
            if primera and nuevas and perfil["datos"].get("primera_pendiente"):
                # Ya ha tenido su primera tanda (una en la que le llegó algo: con la rotación, a veces sus consultas
                # no entran en la primera corrida): las siguientes son las de siempre. Sin la marca no escribe.
                perfiles.actualizar_datos(perfil["chat_id"], lambda d: d.pop("primera_pendiente", None))
        except perfiles.BaseNoDisponible:
            raise
        except Exception:
            logger.exception("Perfil %s: no se han podido mandar sus avisos.", posicion or "titular")


def _vacantes_ya_avisadas(chat_id: int, ofertas_nuevas: list[dict], dias: int) -> dict[tuple[str, str], str]:
    """
    Las vacantes que se le han AVISADO a este perfil en esos días (tabla `avisos`).

    Se suma a _indice_de_avisos_previos y no lo sustituye. Aquella mide lo que está en SU lista, y
    es lo que hace que «lo mismo que hoy» sea exacto para el titular: reproducidas las dos reglas
    sobre los 30 días anteriores al 30-10, 150 avisos con cada una y 0 diferencias, pero esta
    tabla solo existe desde el 26-10, así que sola dejaría sin cubrir parte de la ventana. Y al
    perfil nuevo le da lo que pide D8: lo que la primera corrida deja por visto tampoco se avisa.

    Lo que esto añade es lo que ya no está en su lista: un aviso cuya oferta perdió el enlace.
    """
    if dias <= 0:
        return {}
    nuevas = {o.get("job_url") for o in ofertas_nuevas}
    desde = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    indice: dict[tuple[str, str], str] = {}
    for vacante, url, avisada in perfiles.vacantes_avisadas(chat_id, desde):
        titulo, _, empresa = vacante.partition("|")
        if titulo and empresa and url not in nuevas:
            indice[(titulo, empresa)] = str(avisada)[:10]
    return indice


def _encolar_lo_no_entregado(chat_id: int, ofertas: list[dict], quien: str) -> None:
    """
    Lo que no ha llegado a Telegram espera en la cola de MP9, igual que lo que no cabe en el tope:
    el planificador la vacía cada minuto (`_avisos_pendientes`), así que un fallo de Telegram
    retrasa el aviso en vez de perderlo. Antes la oferta se quedaba guardada y «vista», y en la
    corrida siguiente ya no era nueva: no volvía nunca (P1 de CAMBIOS-MP10 §4).

    Encolar de más no duplica nada: al vaciarse, la cola quita lo que ya está apuntado en `avisos`
    o clasificado (`avisos_perfil._vigentes` y `_sin_repetir`).
    """
    urls = [(o["job_url"], o.get("relevancia")) for o in ofertas if o.get("job_url")]
    if not urls:
        return
    try:
        perfiles.encolar_pendientes(chat_id, urls)
        logger.info("%s%d aviso(s) que no han salido esperan en la cola.", quien, len(urls))
    except perfiles.BaseNoDisponible:
        raise
    except Exception:
        logger.exception("%sNo se ha podido encolar lo que no salió.", quien)


def _notificar_si_alto_encaje(ofertas_nuevas: list[dict], perfil: dict, posicion: str | None = None,
                              primera: bool = False) -> None:
    """
    De las ofertas nuevas de esta búsqueda, filtra las de Encaje alto de este perfil y se las manda
    a Telegram, a su chat. Todo es suyo: su puntuador, su umbral, sus «no repetir» y su tope.
    `posicion` («2/3») es de los perfiles que no son el titular; con él se loguea como siempre, y de
    los demás solo cifras, porque qué ofertas recibe cada uno no es asunto del log (regla 5).

    La relevancia se recalcula aquí (no se lee la que trae el registro guardado): así ajustar los
    pesos o activar el modelo entrenado surte efecto sin tener que repetir la búsqueda.

    Todo lo que decide esta función se loguea explícitamente (incluso cuando no hay
    nada que notificar): la alternativa —quedarse callada— es indistinguible de un
    fallo silencioso, que es justo el problema que motivó añadir estos logs.
    """
    # El registro completo de cada oferta (descripción, modalidad…) está en la base, que
    # fusionar_ofertas acaba de escribir. Con él, el aviso puede decir cuántos años piden y
    # dónde es, y la puntuación tiene en cuenta la experiencia exigida. Solo se leen las nuevas
    # y, sin descripción, las vistas en la ventana de «no repetir»: antes esto mantenía la base
    # entera en memoria para mirar unas pocas.
    quien = f"Perfil {posicion}: " if posicion else ""
    if not perfiles.recibe_avisos(perfil):
        logger.info("%sNo recibe avisos: se queda con lo guardado.", quien)
        return
    chat_id = perfil["chat_id"]
    config_global = config_mod.cargar_config()
    config_actual = perfiles.config_efectiva(perfil, config_global)
    puntuador = buscador.puntuador_para(perfil, config_global)
    anos_max = config_actual.get("anos_experiencia_max")
    umbral = config_actual.get("umbral", buscador.UMBRAL_ENCAJE_ALTO)
    por_url = almacen.ofertas_por_url((o.get("job_url") for o in ofertas_nuevas), chat_id=chat_id)
    dias = int(config_actual.get("dias_sin_repetir_aviso", 14) or 0)
    recientes = almacen.ofertas_recientes(dias, chat_id=chat_id) if dias > 0 and ofertas_nuevas else []
    ya_avisada = {**_vacantes_ya_avisadas(chat_id, ofertas_nuevas, dias),
                  **_indice_de_avisos_previos(recientes, ofertas_nuevas, config_actual)}
    altas = []
    for o in ofertas_nuevas:
        completa = por_url.get(o.get("job_url"), {})
        repetida = ya_avisada.get(_clave_de_vacante(o))
        if repetida and not almacen.es_categoria_siempre_visible(o, config_actual):
            if posicion is None:
                logger.info(
                    "No se avisa de '%s' (%s): la misma vacante ya se vio el %s.",
                    o.get("title"), o.get("company"), repetida,
                )
            continue
        aviso = notificaciones.preparar_aviso(o, completa, puntuador, anos_max, umbral)
        if aviso is not None:
            altas.append(aviso)
    altas.sort(key=lambda o: -o["relevancia"])
    logger.info(
        "%sBúsqueda automática: %s oferta(s) nueva(s), %s de Encaje alto (umbral >= %.1f).",
        quien, len(ofertas_nuevas), len(altas), umbral,
    )
    try:
        _tras_los_avisos(perfil, config_actual, ofertas_nuevas, por_url, ya_avisada, altas, puntuador,
                         umbral, posicion, primera)
    except perfiles.BaseNoDisponible:
        raise
    except Exception:
        # Lo aprendido nunca puede costar los avisos de verdad, que van detrás.
        logger.exception("%sHa fallado lo de la capa aprendida; los avisos siguen.", quien)
    if not altas:
        logger.info("%sNo se notifica a Telegram: ninguna oferta nueva llega a Encaje alto.", quien)
        return

    # `config_actual` se leyó al entrar en esta función, no es la que se pasó a
    # _ejecutar_busqueda al arrancar la búsqueda: así se usan el token y los chat_id más
    # recientes si cambiaron mientras la búsqueda estaba en curso (que puede durar
    # minutos). cargar_config() no cachea nada, lee del disco y del entorno cada vez.
    logger.info(
        "%sIntentando notificar %s oferta(s) de Encaje alto a Telegram (token configurado: %s).",
        quien, len(altas), bool((config_actual.get("telegram_bot_token") or "").strip()),
    )
    # Los dos caminos acaban igual: lo que no se haya entregado espera en la cola (P1). La primera
    # corrida de un perfil sigue siendo de 5 como mucho, y el resto queda como visto a propósito (D8),
    # así que `las_de_ahora` es también lo único que se encola si la entrega revienta.
    las_de_ahora = altas[:AVISOS_EN_LA_PRIMERA_CORRIDA] if primera else altas
    try:
        if not avisos_perfil.config_de(perfil).legado:
            # MP9: elige resumen, silencio o tope. Lo que no sale ahora espera en su cola; nada se pierde.
            resultado = avisos_perfil.entregar(config_actual, perfil, las_de_ahora, posicion)
            logger.info("%sAvisos (modo %s): %s enviado(s), %s en su cola.", quien,
                        avisos_perfil.config_de(perfil).modo, resultado["enviadas"], resultado["encoladas"])
            return
        # El camino de antes de MP9 (el titular y quien no ha elegido nada): se envía igual que siempre,
        # pero ahora dice cuáles no han llegado, para encolarlas.
        fallidas: list[dict] = []
        enviado = notificaciones.notificar_ofertas_alto_encaje(
            config_actual, altas, chat_id=None if posicion is None else chat_id,
            tope=AVISOS_EN_LA_PRIMERA_CORRIDA if primera else None, con_resumen=not primera,
            posicion=posicion, fallidas=fallidas,
        )
        logger.info(
            "%sNotificación a Telegram: %s.", quien,
            "enviada correctamente" if enviado else "NO enviada (ver el motivo en el log de arriba)",
        )
        _encolar_lo_no_entregado(chat_id, fallidas, quien)
    except perfiles.BaseNoDisponible:
        raise
    except Exception:
        # Lo que ya estaba puntuado se perdía con la excepción. En la cola, el planificador lo
        # reintenta en el minuto siguiente; y si parte se había enviado, la propia cola lo descarta.
        logger.exception("%sHa fallado la entrega de los avisos; lo que no salió queda en la cola.", quien)
        _encolar_lo_no_entregado(chat_id, las_de_ahora, quien)


def _tras_los_avisos(perfil: dict, config_actual: dict, ofertas_nuevas: list[dict], por_url: dict,
                     ya_avisada: dict, altas: list[dict], puntuador, umbral: float, posicion: str | None,
                     primera: bool) -> None:
    """
    Lo de MP6 en cada corrida y para cada perfil, con sus datos y nada más: las ofertas de
    descubrimiento, la vigilancia semanal de la capa aprendida y, como mucho una por semana, una
    sugerencia de fondo. Las tres son baratas cuando no toca nada (una o dos consultas).
    """
    import aprendizaje  # diferido: lo importa también buscador.puntuador_para

    chat_id = perfil["chat_id"]
    quien = f"Perfil {posicion}: " if posicion else ""
    destino = None if posicion is None else chat_id
    config_global = config_mod.cargar_config()

    # 1. Descubrimiento: las del borde, ni la primera corrida (ya llegan las 5 mejores) ni apagado.
    cuantas = aprendizaje.quedan_hoy(chat_id) if aprendizaje.descubrimiento_activo(perfil) and not primera else 0
    # MP9: dentro de las mismas reglas de silencio, modo y tope que los avisos (y sin quitarles el sitio).
    legado = avisos_perfil.config_de(perfil).legado
    cuantas = min(cuantas, avisos_perfil.hueco_para_descubrir(perfil, len(altas)))
    avisada = None if legado else avisos_perfil._iso(avisos_perfil._utc(None))
    if cuantas and config_actual.get("telegram_botones", True):
        avisadas = {o.get("job_url") for o in altas}
        candidatas = []
        for o in ofertas_nuevas:
            url = o.get("job_url")
            if not url or url in avisadas or ya_avisada.get(_clave_de_vacante(o)):
                continue
            if perfiles.aviso(chat_id, notificaciones_clave(url)):
                continue  # ya se le enseñó: ni como aviso, ni en /top, ni para descubrir
            completa = por_url.get(url, {})
            anos = buscador.extraer_anos_experiencia(completa.get("description"))
            nota, motivos = buscador.explicar_relevancia(o.get("title"), o.get("location"), anos,
                                                         completa.get("description"), puntuador, completa)
            candidatas.append(({**o, "job_url": url}, nota, motivos))
        for o in aprendizaje.elegir_descubrimiento(candidatas, umbral, cuantas):
            aviso = notificaciones.preparar_aviso(o, por_url.get(o["job_url"], {}), puntuador,
                                                  config_actual.get("anos_experiencia_max"))
            notificaciones.notificar_descubrimiento(config_actual, aviso, destino, posicion, avisada=avisada)
            logger.info("%sOferta de descubrimiento enviada (%.1f, borde del umbral).", quien, aviso["relevancia"])

    # 2. Vigilancia: una vez por semana mide la anterior, con y sin capa, y la apaga si toca.
    medida = aprendizaje.vigilar(perfil, config_global)
    if medida:
        logger.info("%sCapa aprendida, semana del %s: %s respondidos, %s cambiados por la capa, %s %% con y %s %% sin%s.",
                    quien, medida["semana"], medida["respondidos"], medida["cambiados"], medida["con"], medida["sin"],
                    "" if medida["valida"] else " (no cuenta: poca muestra)")
        if medida.get("apagada"):
            quien_es = "Tu capa aprendida" if posicion is None else f"La capa aprendida de un perfil ({posicion} en esta corrida)"
            notificaciones.enviar_a_un_perfil(
                config_actual, perfiles.titular(),
                f"⚠️ {quien_es} se ha apagado sola: dos semanas seguidas con menos precisión que sin ella. "
                "Se puede volver a encender en /ajustes → Lo aprendido.", "aviso de la capa aprendida")

    # 3. Sugerencia de fondo, con sus botones, si hay una y no quita ninguna ⭐ ni ✅.
    perfil = perfiles.obtener(chat_id) or perfil
    sugerencia = aprendizaje.candidata(perfil, config_global)
    if sugerencia:
        telegram_bot.ofrecer_sugerencia(perfil, config_global, sugerencia, destino, posicion)


def notificaciones_clave(url: str) -> str:
    import telegram_botones

    return telegram_botones.clave_de(url)


# ----------------------------------------------------------------------
# EL TRABAJADOR
#
# Una cola y un hilo para todo lo que tarda: las corridas (del planificador y de /corrida) y las
# exportaciones de /misdatos. En fila y no en paralelo a propósito: el pico de RSS lo pone la
# corrida (CAMBIOS-MP0.md §7, el 51,5 % del régimen), y una exportación grande encima de ese pico
# es lo que no cabe en los 512 MiB del contenedor. Hasta MP5 cada corrida abría su propio hilo.
# ----------------------------------------------------------------------
_cola: queue.Queue = queue.Queue()


def encolar(nombre: str, trabajo) -> int:
    """Deja `trabajo` (sin argumentos) en la cola. Devuelve cuántos hay delante, contando este."""
    _cola.put((nombre, trabajo))
    return _cola.qsize()


def _trabajador(parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            nombre, trabajo = _cola.get(timeout=1)
        except queue.Empty:
            continue
        try:
            trabajo()
        except Exception:
            # Una exportación que falla no puede llevarse por delante las corridas que vienen detrás.
            logger.exception("El trabajo «%s» ha fallado", nombre)
        finally:
            _cola.task_done()


# ----------------------------------------------------------------------
# LA CORRIDA DE ARRANQUE (MP8, paso 14 de la encuesta)
#
# Quien acaba de hacer la encuesta tiene la lista vacía (D1), y esperar a que la rotación llegue a sus
# consultas sería esperar horas con el bot callado. Se hace UNA vez, solo con sus consultas (como mucho
# buscador.CONSULTAS_DE_ARRANQUE) y por el trabajador, es decir, detrás de la corrida que haya en marcha
# y sin pisarla. Al terminar, telegram_encuesta.tras_arranque manda la calibración y, de las 5 mejores,
# las que no estén en ella. Lo demás de lo que encuentra queda como visto: nunca una avalancha.
#
# `datos.arranque.estado` dice si falta («pendiente») o no. Es lo que permite retomarlo si el servicio
# se reinicia con el arranque en cola (`recuperar_arranques`), porque la cola vive en memoria.
# ----------------------------------------------------------------------
_arranques: set[int] = set()
_lock_arranques = threading.Lock()


def iniciar_arranque(chat_id: int) -> bool:
    """Encola el arranque de ese perfil, una sola vez. Devuelve si lo ha encolado."""
    with _lock_arranques:
        if chat_id in _arranques:
            return False
        _arranques.add(chat_id)
    encolar("arranque", lambda: _ejecutar_arranque(chat_id))
    return True


def recuperar_arranques() -> int:
    """Al arrancar: los perfiles que se quedaron con el arranque `pendiente` (se reinició el servicio)."""
    n = 0
    for perfil in perfiles.activos():
        if (perfil["datos"].get("arranque") or {}).get("estado") == "pendiente" and perfil.get("chat_id") is not None:
            n += 1 if iniciar_arranque(perfil["chat_id"]) else 0
    return n


def _ejecutar_arranque(chat_id: int) -> None:
    try:
        perfil = perfiles.obtener(chat_id)
        if perfil is None or perfil["estado"] != "activo" or (perfil["datos"].get("arranque") or {}).get("estado") != "pendiente":
            return
        config = config_mod.cargar_config()
        # Lo mismo que _ejecutar_busqueda: no se vuelve a pedir lo que ya se descargó, sea de quien sea.
        en_cache, competencia_en_cache = almacen.cargar_cache_vistas()
        en_base: dict[str, str] = {}
        for activo in perfiles.activos():
            en_base.update(almacen.descripciones_en_base(activo["chat_id"]))
        conocidas = almacen.Conocidas(en_base, en_cache)

        def recordar(descripciones: dict[str, str], competencia: dict[str, dict]) -> None:
            guardables = {u: d for u, d in descripciones.items() if u not in en_base}
            if guardables:
                almacen.recordar_descripciones(guardables, competencia)

        marca = datetime.now().isoformat(timespec="seconds")

        def guardar(perfil_: dict, df) -> None:
            if not _es_el_de_antes(perfil_):
                logger.info("Un perfil se ha borrado o reiniciado durante su arranque: no se guarda nada.")
                return
            almacen.fusionar_ofertas(etiquetas.a_registros(df), reemplazar=False, chat_id=chat_id,
                                     config=perfiles.config_efectiva(perfil_, config), ahora=marca)

        inicio = time.monotonic()
        hechas = buscador.buscar_arranque(
            perfil, config, guardar, almacen.urls_excluidas, descripciones_conocidas=conocidas,
            al_descargar_descripciones=recordar, competencia_conocida=competencia_en_cache)
        logger.info("Corrida de arranque de un perfil nuevo: %d consultas en %.1f s.", hechas, time.monotonic() - inicio)
        import telegram_encuesta

        telegram_encuesta.tras_arranque(chat_id, config_mod.cargar_config())
    except perfiles.BaseNoDisponible:
        raise
    except Exception:
        logger.exception("La corrida de arranque de un perfil nuevo ha fallado; lo recogerá la corrida normal.")
        # No se reintenta en bucle: la corrida normal le traerá lo suyo, con el tope de su primera tanda.
        try:
            perfiles.actualizar_datos(chat_id, lambda d: d.update(arranque={"estado": "fallido"}, primera_pendiente=True))
            notificaciones.enviar_a_un_perfil(
                config_mod.cargar_config(), chat_id,
                "⚠️ No he podido hacer la primera búsqueda. No pasa nada: en la próxima vuelta (cada hora) te llegan las "
                "primeras ofertas.", "arranque fallido")
        except Exception:
            logger.exception("Tampoco se ha podido avisar del fallo del arranque.")
    finally:
        almacen.cerrar_cache()  # como en _ejecutar_busqueda
        with _lock_arranques:
            _arranques.discard(chat_id)


def _mantenimiento_de_encuestas() -> None:
    """Recordatorios a las 24 h y borradores fuera a los 7 días: una lectura cada media hora."""
    import telegram_encuesta

    try:
        telegram_encuesta.mantenimiento(config_mod.cargar_config())
    except perfiles.BaseNoDisponible:
        pass  # el planificador y el resto ya avisan de eso
    except Exception:
        logger.exception("El mantenimiento de las encuestas ha fallado")


def _consultas_de_la_corrida(config: dict) -> int:
    """Cuántas va a hacer esta corrida: las que entran en el presupuesto, de todos los perfiles."""
    try:
        return len(buscador.consultas_de_la_corrida(perfiles.activos(), config)[1])
    except perfiles.BaseNoDisponible:
        # Se deja arrancar: es la corrida quien falla antes de consultar nada, y quien avisa.
        return len(buscador.construir_busquedas(config))


def _iniciar_busqueda(config: dict, reemplazar: bool = False, automatica: bool = True) -> dict:
    """
    Encola una corrida si no hay ya una en curso o esperando.

    La usan el planificador (ver _bucle_scheduler más abajo) y /corrida (telegram_bot), para no
    duplicar entre los dos la comprobación de «ya hay una».
    """
    with _lock_busqueda:
        if BUSQUEDA["activa"]:
            return {"error": "Ya hay una búsqueda en curso"}

        total = _consultas_de_la_corrida(config)
        if total == 0:
            return {"error": "No hay ninguna categoría o ámbito activo"}

        BUSQUEDA.update(
            activa=True, mensaje="En cola...", hecho=0, total=total,
            error=None, resumen=None,
        )

    delante = encolar("corrida", lambda: _ejecutar_busqueda(config, reemplazar, automatica))
    return {"iniciada": True, "total": total, "en_cola": delante}


def lanzar_corrida() -> dict:
    """La de /corrida: con avisos al titular, como las del planificador (ver _notificar_perfiles)."""
    return _iniciar_busqueda(config_mod.cargar_config(), reemplazar=False, automatica=True)


def estado_de_la_corrida() -> dict:
    """Lo que /estado necesita saber de las corridas, como copia."""
    with _lock_busqueda:
        datos = dict(BUSQUEDA)
    datos["proxima_busqueda_automatica"] = _proxima_ejecucion_automatica_iso()
    datos["en_cola"] = _cola.qsize()
    return datos


# ----------------------------------------------------------------------
# BÚSQUEDA AUTOMÁTICA
#
# El planificador comprueba cada POLL_SCHEDULER_SEGUNDOS si toca lanzar la siguiente corrida según
# busqueda_automatica_activa/horas de config.json. Un threading.Timer único no sirve aquí porque
# el intervalo se puede cambiar en cualquier momento (desde MP5, con /frecuencia): con este bucle
# basta con releer la config en cada vuelta para que el cambio surta efecto sin reiniciar el
# proceso, sin tener que cancelar y reprogramar ningún temporizador.
# ----------------------------------------------------------------------

POLL_SCHEDULER_SEGUNDOS = 30
_estado_scheduler = {"proxima_ejecucion": None, "horas_conocidas": None}
_lock_scheduler = threading.Lock()


def _proxima_ejecucion_automatica_iso() -> str | None:
    with _lock_scheduler:
        marca = _estado_scheduler["proxima_ejecucion"]
    if marca is None:
        return None
    return datetime.fromtimestamp(marca).isoformat(timespec="seconds")


def _tick_scheduler() -> None:
    config = config_mod.cargar_config()

    if not config.get("busqueda_automatica_activa"):
        with _lock_scheduler:
            _estado_scheduler["proxima_ejecucion"] = None
            _estado_scheduler["horas_conocidas"] = None
        return

    horas = float(config.get("busqueda_automatica_horas") or 6)
    segundos_intervalo = max(60.0, horas * 3600)
    ahora = time.time()

    with _lock_scheduler:
        # Primera vez que se ve activa, o se ha cambiado el intervalo (/frecuencia): se recalcula
        # la cuenta atrás desde ahora mismo, sin esperar a que venza la anterior. Así el cambio
        # surte efecto sin reiniciar el contenedor.
        if (
            _estado_scheduler["proxima_ejecucion"] is None
            or horas != _estado_scheduler["horas_conocidas"]
        ):
            _estado_scheduler["proxima_ejecucion"] = ahora + segundos_intervalo
            _estado_scheduler["horas_conocidas"] = horas
            return

        if ahora < _estado_scheduler["proxima_ejecucion"]:
            return

        _estado_scheduler["proxima_ejecucion"] = ahora + segundos_intervalo

    resultado = _iniciar_busqueda(config, reemplazar=False, automatica=True)
    if "error" in resultado:
        # Lo más probable es que ya hubiera una corrida (de /corrida o la anterior) en curso: no es
        # un fallo, simplemente se prueba de nuevo en el próximo ciclo.
        logger.info("Búsqueda automática omitida: %s", resultado["error"])
    else:
        logger.info("Búsqueda automática iniciada (%s búsquedas)", resultado["total"])


_ULTIMA_PASADA = {"cola": float("-inf"), "autopausa": float("-inf")}
SEGUNDOS_ENTRE_COLAS = 60
SEGUNDOS_ENTRE_AUTOPAUSAS = 3600


def _avisos_pendientes() -> None:
    """
    MP9: la cola de avisos de cada perfil (silencio que acaba, turno de un resumen, un día nuevo con hueco
    en el tope) y la autopausa. Una pasada por minuto la primera y por hora la segunda: son lecturas de
    una o dos consultas por perfil, y solo escriben cuando hay algo que mandar. No cuesta en el Atom.
    """
    ahora = time.monotonic()
    try:
        if ahora - _ULTIMA_PASADA["cola"] >= SEGUNDOS_ENTRE_COLAS:
            _ULTIMA_PASADA["cola"] = ahora
            avisos_perfil.tick(config_mod.cargar_config())
        if ahora - _ULTIMA_PASADA["autopausa"] >= SEGUNDOS_ENTRE_AUTOPAUSAS:
            _ULTIMA_PASADA["autopausa"] = ahora
            avisos_perfil.vigilar_actividad(config_mod.cargar_config())
    except perfiles.BaseNoDisponible:
        pass  # el planificador y el resto ya avisan de eso
    except Exception:
        logger.exception("Los avisos pendientes o la autopausa han fallado")


def _bucle_scheduler(parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            _tick_scheduler()
        except Exception:
            logger.exception("Fallo en el scheduler de búsqueda automática")
        _mantenimiento_de_encuestas()  # se limita solo a una pasada cada media hora
        _avisos_pendientes()
        parar.wait(POLL_SCHEDULER_SEGUNDOS)


def _comprobar_almacen() -> None:
    """
    Abre perfiles.db al arrancar, y la primera vez migra los JSON. Si no se puede, no se
    aborta: el bot se queda levantado para que el aviso salga y se pueda mirar qué pasa,
    pero las corridas y los botones fallarán con BaseNoDisponible hasta que el titular lo
    resuelva (decidido con él el 30-10, ver perfiles.BaseNoDisponible).

    Hasta MP5 creaba además un perfil por cada chat_id de JOBSPY_TELEGRAM_CHAT_IDS_EXTRA. Ya no: las
    altas son por invitación (/invitar), y esa variable no la lee nadie.
    """
    try:
        perfiles.conexion()
    except perfiles.BaseNoDisponible as e:
        logger.critical("perfiles.db no está disponible: %s", e)
        _notificar_incidencias_de_fuentes()


def arrancar(parar: threading.Event) -> list[threading.Thread]:
    """
    Los dos hilos de fondo, el lector y el trabajador, con el bot enganchado al servicio. El
    planificador no va aquí: es el hilo principal (ver `main`). Separado para poder probarlo.
    """
    telegram_bot.configurar(lanzar_corrida=lanzar_corrida, estado_corrida=estado_de_la_corrida,
                            encolar=encolar, lanzar_arranque=iniciar_arranque)
    hilos = [
        threading.Thread(target=_trabajador, args=(parar,), name="trabajador", daemon=True),
        threading.Thread(target=telegram_bot.escuchar, args=(config_mod.cargar_config, parar.is_set),
                         name="lector-telegram", daemon=True),
    ]
    for hilo in hilos:
        hilo.start()
    return hilos


def main() -> int:
    parar = threading.Event()

    def al_recibir(senal, _marco):
        # SIGTERM es lo que manda `docker stop`. Como PID 1 sin manejador, Python lo ignoraba y
        # Docker mataba el proceso a los 10 s; así sale enseguida. Una corrida a medias no deja
        # nada roto: cada fusión es una transacción (perfiles.aplicar_cambios).
        logger.info("Señal %s: se para el servicio.", senal)
        parar.set()

    signal.signal(signal.SIGTERM, al_recibir)
    signal.signal(signal.SIGINT, al_recibir)
    _comprobar_almacen()
    arrancar(parar)
    try:
        # Un arranque que estaba en cola cuando se paró el servicio (la cola vive en memoria).
        if recuperar_arranques():
            logger.info("Se retoma la corrida de arranque de un perfil nuevo.")
    except Exception:
        logger.exception("No se han podido recuperar las corridas de arranque pendientes")
    logger.info("Servicio en marcha: planificador, lector de Telegram y trabajador (sin web).")
    _bucle_scheduler(parar)
    logger.info("Servicio parado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
