"""
El bot escucha (MP5, EVOLUCION-MULTIPERFIL.md §4.5 y D4, D6, D7, D9): mensajes, comandos y botones.

Hasta MP5 el bot solo atendía los botones de los avisos, y todo lo demás estaba en una web sin
contraseña o exigía `sudo docker exec`. Ahora cada persona tiene sus comandos y su ficha, y el
titular, además, invita, ve qué busca cada perfil y opera el sistema. Lo que hacía cada endpoint de
la web y cómo se hace aquí está en CAMBIOS-MP5.md.

CÓMO LLEGA TODO: un solo hilo hace «long polling» a getUpdates con `allowed_updates: ["message",
"callback_query"]` (el Toshiba no tiene URL pública, así que no hay webhook). Lo que tarda —una
corrida, una exportación— no se hace aquí, que dejaría el bot sordo minutos: se encola al
trabajador del servicio (app.py), que lo hace en fila.

BOTONES, por prefijo: los de las ofertas (`f:`, `a:`, `o:`, `m?:`, `d:`) siguen siendo los de
telegram_botones.py, tal cual. Los nuevos llevan versión para poder cambiarlos sin que un botón
viejo haga otra cosa: `adm1:` (administración), `aj1:` (lo de cada persona: /borrar, /reiniciar, /ajustes y las sugerencias de MP6) y `ob1:`
(la encuesta y /ajustes, MP8: telegram_encuesta.py). Telegram limita `callback_data` a
64 bytes: ningún botón lleva un chat_id ni un código, sino una huella corta (`_ref`).

QUIÉN PUEDE QUÉ (D4, §7):

- Solo chats privados. En un grupo se contesta una vez y se ignora lo demás.
- Un chat sin perfil no recibe respuesta (decidido en MP0: «se ignora en silencio»), salvo un
  /start con un código que existió (decidido por el titular el 03-11): si caducó o ya se usó, se le
  dice. Un código inventado no recibe nada: no se le confirma a nadie que el bot existe.
- Los comandos de administración, solo el titular (JOBSPY_TELEGRAM_CHAT_ID). Ningún comando
  devuelve nada de otro perfil; la única excepción es la ficha de /perfiles (D9), que enseña qué
  busca cada uno y cifras, nunca ofertas, marcas ni motivos.
- Nada de lo que se loguea lleva un chat_id, un código de invitación ni el token (regla 3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import almacen
import avisos_perfil
import notificaciones
import perfiles
import telegram_botones
import telegram_encuesta as encuesta
from fuentes_extra import salud

logger = logging.getLogger(__name__)

ESPERA_LARGA_SEGUNDOS = 50
# Los botones de confirmar un borrado o una reactivación caducan: pulsar uno de hace una semana
# por error no puede borrar a nadie.
MINUTOS_DE_UNA_CONFIRMACION = 10
# /reiniciar, una vez cada 24 h por persona: cada reinicio acaba en una corrida de arranque (hasta 8
# consultas a los portales, fuera de las 33 de la corrida: regla 2) y deja una copia entera de lo suyo en
# el disco del Toshiba. Quien se equivoca en una respuesta tiene /ajustes, que no cuesta ni lo uno ni lo
# otro. El titular, desde la ficha, no tiene este límite: es quien da el permiso.
HORAS_ENTRE_REINICIOS = 24
# Desde la ficha solo se reinicia a quien tiene algo que reiniciar y puede recibir el cuestionario: uno
# en `encuesta` ya está a cero, y a uno `bloqueado` no le llegaría.
ESTADOS_QUE_SE_REINICIAN = ("activo", "pausado")
TOP = 5
# /interesan (12-11). De cinco en cinco, como /top: cada oferta lleva su fila de botones y con cinco el
# mensaje se lee entero en una pantalla. Lo guardado con ⭐ no tenía ninguna forma de verse.
INTERESAN_POR_PAGINA = 5
DIAS_PARA_DARLA_POR_VISTA = 2
DIAS_DEL_TOP = 7
OPCIONES_DE_TOPE = (5, 10, 20)  # provisionales: se afinan con el piloto (CAMBIOS-MP9.md §6)
# Para /top se preseleccionan estas por la relevancia guardada y se repuntúan con el puntuador de
# hoy: repuntuar la lista entera (~3.000 del titular) sería lo que hacía la web en cada refresco.
CANDIDATAS_DEL_TOP = 30
HORAS_DE_FRECUENCIA = (1, 2, 3, 6)
_CODIGO_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Lo que el servicio le presta al bot (app.arrancar): lanzar una corrida, cómo va la corrida y la
# cola del trabajador. Así este módulo no importa app.py: con `python app.py`, app es __main__, e
# importarlo desde aquí crearía una segunda copia con su propio estado de corrida.
SERVICIO: dict = {"lanzar_corrida": None, "estado_corrida": None, "encolar": None, "lanzar_arranque": None}
encuesta.SERVICIO = SERVICIO  # la encuesta lanza la corrida de arranque por el mismo gancho

_BOT = {"nombre": None}
_GRUPOS_AVISADOS: set[int] = set()

COMANDOS_DE_TODOS = (
    ("perfil", "Tu ficha: qué se busca para ti"),
    ("top", "Tus 5 mejores ofertas vigentes"),
    ("interesan", "Lo que guardaste con ⭐ y aún no has aplicado"),
    ("resumen", "Tu semana: avisos, respuestas y lo aprendido"),
    ("ajustes", "Cambia lo que buscas, tus avisos y lo aprendido"),
    ("pausa", "Dejar de buscar y de avisar"),
    ("reanudar", "Volver a buscar y avisar"),
    ("misdatos", "Todo lo tuyo, en un fichero"),
    ("reiniciar", "Empezar de cero: repetir el cuestionario"),
    ("borrar", "Borrar todo lo tuyo"),
    ("ayuda", "Qué hace este bot"),
)
COMANDOS_DEL_TITULAR = COMANDOS_DE_TODOS + (
    ("invitar", "Enlace de invitación de un solo uso"),
    ("perfiles", "Los perfiles y su ficha"),
    ("estado", "Corridas, fuentes, memoria y disco"),
    ("corrida", "Lanzar una corrida ahora"),
    ("fuentes", "Ver y reactivar fuentes"),
    ("frecuencia", "Cada cuántas horas se busca"),
)
_SOLO_TITULAR = {c for c, _ in COMANDOS_DEL_TITULAR} - {c for c, _ in COMANDOS_DE_TODOS}

# Los pasos de la encuesta y de /ajustes que esperan un texto o una ubicación (MP8): {paso: función(chat,
# mensaje)}. Lo que no está aquí recibe «no te entiendo».
PASOS: dict = {}
encuesta.registrar(PASOS)


def configurar(**ganchos) -> None:
    desconocidos = set(ganchos) - set(SERVICIO)
    if desconocidos:
        raise ValueError(f"Ganchos desconocidos: {', '.join(sorted(desconocidos))}")
    SERVICIO.update(ganchos)


_esc = notificaciones._esc


def _ref(valor) -> str:
    """Una huella corta para un botón: ni el chat_id ni el nombre de una fuente van tal cual."""
    return hashlib.sha1(str(valor).encode("utf-8")).hexdigest()[:8]


def _minuto() -> int:
    return int(time.time() // 60)


def _vigente(sello: str) -> bool:
    try:
        return 0 <= _minuto() - int(sello) <= MINUTOS_DE_UNA_CONFIRMACION
    except ValueError:
        return False


def _boton(texto: str, datos: str) -> dict:
    return {"text": texto, "callback_data": datos}


def _fecha(iso: str | None) -> str:
    """«03-11 17:44» (hora del contenedor, UTC: ver DESPLIEGUE.md)."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d-%m %H:%M")
    except ValueError:
        return str(iso)[:16]


# ----------------------------------------------------------------------
# Enviar (siempre por el emisor de notificaciones.llamar: ritmo, 429 y 403)
# ----------------------------------------------------------------------
def _enviar(token: str, chat_id: int, texto: str, teclado: dict | None = None) -> int | None:
    """Manda un mensaje (troceado si pasa de 4.096) y devuelve el id del último trozo, o None."""
    trozos = notificaciones._trocear(texto)
    ultimo = None
    for i, trozo in enumerate(trozos):
        cuerpo = {"chat_id": chat_id, "text": trozo, "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if teclado and i == len(trozos) - 1:
            cuerpo["reply_markup"] = teclado
        respuesta = notificaciones.llamar(token, "sendMessage", cuerpo)
        if respuesta.ok:
            ultimo = (respuesta.resultado or {}).get("message_id") if isinstance(respuesta.resultado, dict) else None
        else:
            logger.warning("Telegram sendMessage respondió %s: %s", respuesta.codigo, respuesta.descripcion[:200])
    return ultimo


def _editar(token: str, chat_id: int, mensaje_id, texto: str, teclado: dict | None = None) -> None:
    cuerpo = {"chat_id": chat_id, "message_id": mensaje_id, "text": texto[:notificaciones.MAX_CARACTERES_TELEGRAM],
              "parse_mode": "HTML", "disable_web_page_preview": True}
    if teclado:
        cuerpo["reply_markup"] = teclado
    respuesta = notificaciones.llamar(token, "editMessageText", cuerpo)
    if not respuesta.ok:
        logger.warning("Telegram editMessageText respondió %s: %s", respuesta.codigo, respuesta.descripcion[:200])


def _contestar(token: str, pulsacion_id, texto: str = "") -> None:
    notificaciones.llamar(token, "answerCallbackQuery", {"callback_query_id": pulsacion_id, "text": texto[:200]})


# ----------------------------------------------------------------------
# El chat que habla
# ----------------------------------------------------------------------
class Chat:
    """Quién escribe o pulsa, y lo que hace falta para contestarle."""

    def __init__(self, chat_id: int, config: dict):
        self.chat_id = chat_id
        self.config = config
        self.token = (config.get("telegram_bot_token") or "").strip()
        principal = str(config.get("telegram_chat_id") or "").strip()
        # El mismo criterio que telegram_botones._perfil_del_chat: sin JOBSPY_TELEGRAM_CHAT_ID no
        # hay titular, y nadie puede administrar.
        self.es_titular = bool(principal) and str(chat_id) == principal
        self.perfil = perfiles.obtener(chat_id)
        if self.perfil is None and self.es_titular:
            # Una base sin su fila (recién migrada, o en el PC): el titular es el de la migración.
            self.perfil = {**perfiles.PERFIL_GLOBAL, "chat_id": chat_id, "estado": "activo"}

    def decir(self, texto: str, teclado: dict | None = None) -> int | None:
        return _enviar(self.token, self.chat_id, texto, teclado)


# ----------------------------------------------------------------------
# El router
# ----------------------------------------------------------------------
def procesar(update: dict, config: dict) -> str | None:
    """
    Atiende una actualización de getUpdates. Devuelve qué ha hecho (para el log y las pruebas):
    «silencio», «grupo», «comando:<nombre>», «start:<resultado>», «texto», «boton:<prefijo>»…
    """
    if not (config.get("telegram_bot_token") or "").strip():
        return None
    if update.get("callback_query"):
        return _pulsacion(update["callback_query"], config)
    if update.get("message"):
        return _mensaje(update["message"], config)
    return None


def _en_un_grupo(chat: dict, config: dict) -> str:
    """Una respuesta por grupo y nada más (también tras un reinicio: queda en `meta`)."""
    chat_id = chat.get("id")
    clave = f"grupo_avisado:{chat_id}"
    if chat_id in _GRUPOS_AVISADOS or perfiles.meta_get(clave) is not None:
        _GRUPOS_AVISADOS.add(chat_id)
        return "grupo"
    _GRUPOS_AVISADOS.add(chat_id)
    perfiles.meta_set(clave, perfiles._ahora())
    _enviar((config.get("telegram_bot_token") or "").strip(), chat_id,
            "Este bot solo funciona en chats privados.")
    logger.info("Mensaje desde un grupo: se contesta una vez y se ignora lo demás.")
    return "grupo"


def _mensaje(mensaje: dict, config: dict) -> str:
    datos_chat = mensaje.get("chat") or {}
    if datos_chat.get("type", "private") != "private":
        return _en_un_grupo(datos_chat, config)
    try:
        chat = Chat(int(datos_chat["id"]), config)
    except (KeyError, TypeError, ValueError):
        return "silencio"
    texto = (mensaje.get("text") or "").strip()
    comando, argumento = _comando(texto)

    if chat.perfil is None:
        # D4: sin perfil, solo un /start con un código que existió recibe respuesta.
        if comando == "start" and argumento:
            return "start:" + _alta(chat, argumento)
        logger.info("Mensaje de un chat sin perfil: no se contesta (D4).")
        return "silencio"

    perfiles.anotar_actividad(chat.chat_id)
    if chat.perfil.get("estado") == "bloqueado":
        # Si escribe es que ha desbloqueado el bot: vuelve a como estaba.
        estado = perfiles.desbloquear(chat.chat_id)
        chat.perfil = perfiles.obtener(chat.chat_id)
        logger.info("Un perfil bloqueado ha vuelto a escribir: pasa a «%s».", estado)
        if comando == "start":
            chat.decir("👋 De vuelta. Tu perfil está como lo dejaste. /ayuda")
            return "start:desbloqueado"

    if comando:
        return _atender_comando(chat, comando, argumento)
    if mensaje.get("location"):
        return _paso(chat, mensaje, "ubicacion")
    return _paso(chat, mensaje, "texto")


def _comando(texto: str) -> tuple[str | None, str]:
    """«/estado@mi_bot  algo» -> ("estado", "algo"). Lo que no empieza por «/», (None, "")."""
    if not texto.startswith("/"):
        return None, ""
    cabeza, _, resto = texto.partition(" ")
    nombre, _, bot = cabeza[1:].partition("@")
    if bot and _BOT["nombre"] and bot.lower() != _BOT["nombre"].lower():
        return None, ""  # es para otro bot
    return nombre.lower(), resto.strip()


def _paso(chat: Chat, mensaje: dict, que: str) -> str:
    paso = perfiles.paso_de(chat.chat_id)
    manejador = PASOS.get(paso) if paso else None
    if manejador is None and chat.perfil.get("estado") == "encuesta":
        # En la encuesta sin borrador (se borró a los 7 días): cualquier mensaje la retoma.
        manejador = encuesta.texto_libre
    if manejador:
        manejador(chat, mensaje)
        return f"paso:{paso}"
    if que == "ubicacion":
        chat.decir("Gracias, pero ahora mismo no necesito tu ubicación.")
    else:
        chat.decir("No te entiendo. Mira /ayuda.")
    return que


def _atender_comando(chat: Chat, comando: str, argumento: str) -> str:
    funcion = _COMANDOS.get(comando)
    if funcion is None or (comando in _SOLO_TITULAR and not chat.es_titular):
        chat.decir("No conozco ese comando. Mira /ayuda.")
        return "comando:desconocido"
    logger.info("Comando /%s %s.", comando, "del titular" if chat.es_titular else "de otro perfil")
    funcion(chat, argumento)
    return f"comando:{comando}"


# ----------------------------------------------------------------------
# Altas (D4)
# ----------------------------------------------------------------------
BIENVENIDA = (
    "👋 Hola. Este bot te avisa de ofertas de empleo que encajan con tu perfil, y cada aviso lleva "
    "botones (⭐ Me interesa · ✅ Aplicada · 🙈 No me interesa) para que lo que te llega se afine.\n\n"
    "<b>Qué se guarda:</b> tu número de chat de Telegram y lo que marques. Ni tu nombre, ni tu "
    "teléfono, ni tu CV.\n"
    "<b>Quién ve qué:</b> quien administra el bot puede ver tu ficha (qué se busca para ti: "
    "términos, experiencia, modalidad y zona) y cifras (cuántos avisos, qué parte te interesa). No "
    "ve qué ofertas recibes ni lo que marcas.\n"
    "/misdatos te lo manda todo y /borrar lo borra."
)
BIENVENIDA_PLANTILLA = (
    "Tu perfil empieza como <b>soporte IT y ciberseguridad en Barcelona</b> (o en remoto desde "
    "España), con ofertas que pidan <b>1 año de experiencia o menos</b>; las que no dicen los años "
    "también llegan. En la primera tanda te llegan como mucho 5. /perfil enseña tu ficha."
)


def _alta(chat: Chat, codigo: str) -> str:
    if not _CODIGO_VALIDO.match(codigo):
        logger.info("Un /start con un código mal formado: no se contesta (D4).")
        return "silencio"
    resultado = perfiles.usar_invitacion(codigo, chat.chat_id, chat.config)
    if resultado == "alta":
        perfil = perfiles.obtener(chat.chat_id)
        chat.perfil = perfil
        if perfil["estado"] == "activo":
            chat.decir(BIENVENIDA + "\n\n" + BIENVENIDA_PLANTILLA)
        else:
            # La encuesta (MP8): su bienvenida ya dice qué se guarda y quién ve qué (D9).
            encuesta.iniciar(chat)
        total = len({p["chat_id"] for p in perfiles.listar()} | {perfiles.titular()})
        _al_titular(chat.config, f"🆕 Alta nueva por invitación ({'plantilla de soporte' if perfil['estado'] == 'activo' else 'encuesta'}): "
                    f"ya hay {total} de {chat.config.get('max_perfiles') or '∞'} perfiles. /perfiles")
        logger.info("Alta nueva por invitación (%s).", perfil["estado"])
    elif resultado in ("caducada", "usada"):
        chat.decir("Esta invitación ya no vale (ha caducado o ya se ha usado). Pide otra a quien te la pasó.")
    elif resultado == "sin_hueco":
        chat.decir("Ahora mismo no queda sitio en este bot. Díselo a quien te invitó.")
    else:
        logger.info("Un /start con un código que no existe: no se contesta (D4).")
        return "silencio"
    return resultado


def _al_titular(config: dict, texto: str) -> None:
    principal = str(config.get("telegram_chat_id") or "").strip()
    if principal:
        _enviar((config.get("telegram_bot_token") or "").strip(), int(principal), texto)


# ----------------------------------------------------------------------
# La ficha (D9): qué busca un perfil, nunca qué recibe
# ----------------------------------------------------------------------
def ficha(perfil: dict, config_global: dict, numero: int | None = None, es_titular: bool = False) -> str:
    """
    La ficha de configuración de un perfil: términos, experiencia y tope, modalidad, zona, idiomas,
    avisos, estado y cifras. Sale de su config efectiva, que es lo que de verdad usan los filtros.

    Por construcción no puede llevar una oferta: no lee ni `ofertas_perfil` ni `marcas`, y de
    `avisos` solo cuentas (perfiles.cifras_de_avisos). La prueba lo comprueba con su control.
    """
    datos = perfil.get("datos") or {}
    cabeza = f"📋 <b>Perfil {numero}</b>" if numero else "📋 <b>Tu ficha</b>"
    if es_titular:
        cabeza += " (titular)"
    lineas = [cabeza, f"Estado: {_esc(perfil.get('estado'))} · alta {_fecha(perfil.get('creado'))}"
              f" · actividad {_fecha(perfil.get('ultima_actividad'))}"]
    try:
        efectiva = perfiles.config_efectiva(perfil, config_global)
    except ValueError:
        lineas.append("Búsqueda: <i>todavía no hay</i> (aún no ha terminado la encuesta).")
        return "\n".join(lineas)

    terminos = [t for c in (efectiva.get("categorias") or {}).values() if c.get("activa", True)
                for t in c.get("terminos") or []]
    lineas.append(f"Juego de reglas: {_esc(datos.get('reglas') if datos.get('reglas') != 'catalogo' else 'catálogo')}")
    lineas.append("Términos: " + (_esc(", ".join(terminos[:20])) + (f" y {len(terminos) - 20} más" if len(terminos) > 20 else "")
                                  if terminos else "—"))
    tope = efectiva.get("anos_experiencia_max")
    if tope is None:
        lineas.append("Experiencia exigida: sin tope")
    else:
        regla = "se descartan" if efectiva.get("experiencia_estricta") else "restan"
        lineas.append(f"Experiencia exigida: hasta {tope} año{'s' if tope != 1 else ''} (las que piden más {regla}; "
                      "las que no lo dicen, pasan)")
    modalidades = []
    for ambito in (efectiva.get("ambitos") or {}).values():
        if not ambito.get("activo", True):
            continue
        sitio = _esc(ambito.get("location") or "")
        modalidades.append(f"remoto ({sitio})" if ambito.get("is_remote") else f"presencial o híbrido ({sitio})")
    lineas.append("Modalidad: " + (", ".join(modalidades) or "—"))
    if efectiva.get("centro_nombre"):
        lineas.append(f"Zona: {_esc(efectiva['centro_nombre'])}, {notificaciones._num(float(efectiva.get('max_distancia_km') or 0))} km")
    if efectiva.get("descartar_otros_idiomas"):
        lineas.append("Idiomas: " + _esc(", ".join(efectiva.get("idiomas_permitidos") or [])))
    modo = (datos.get("avisos") or {}).get("modo") or "instantaneo"
    lineas.append(f"Avisos: {_esc(modo)} · desde {notificaciones._num(float(efectiva.get('umbral') or 5.0))} de encaje")
    cifras = perfiles.cifras_de_avisos(perfil["chat_id"]) if perfil.get("chat_id") is not None else None
    if cifras:
        precision = (f"{round(100 * cifras['positivos'] / cifras['respondidos'])} % de {cifras['respondidos']} respondidos"
                     if cifras["respondidos"] else "sin respuestas aún")
        lineas.append(f"Avisos en 7 días: {cifras['semana']} · interés (30 días): {precision}")
        lineas.append(_linea_de_etiquetas(perfil))
    return "\n".join(lineas)


def _linea_de_etiquetas(perfil: dict) -> str:
    """
    Cuántas etiquetas tiene y si su capa aprendida está puesta: lo único de lo aprendido que sale en la
    ficha que ve el titular (D9). Ni qué rasgos ni qué ofertas. Barato a propósito: cuenta, no aprende.
    """
    import aprendizaje

    ajustes = aprendizaje.ajustes(perfil)
    n = len({e["url"] for e in perfiles.etiquetas(perfil["chat_id"])
             if not ajustes["desde"] or (e.get("fecha") or "") > ajustes["desde"]})
    if ajustes["apagada"]:
        capa = "apagada"
    elif n < aprendizaje.MIN_ETIQUETAS:
        capa = f"se pone a las {aprendizaje.MIN_ETIQUETAS}"
    else:
        capa = "puesta"
    return f"Etiquetas (botones): {n} · lo aprendido: {capa}"


# ----------------------------------------------------------------------
# Comandos de todos
# ----------------------------------------------------------------------
def _ayuda(chat: Chat, _argumento: str = "") -> None:
    lineas = ["ℹ️ <b>Qué hace este bot</b>",
              "Te avisa de ofertas que encajan con tu perfil. En cada aviso: ⭐ Me interesa · "
              "✅ Aplicada · 🙈 No me interesa (con un motivo, si quieres): es lo que afina lo que te llega.", ""]
    lineas += [f"/{c} — {d}" for c, d in COMANDOS_DE_TODOS]
    if chat.es_titular:
        lineas += ["", "<b>Administración</b>"] + [f"/{c} — {d}" for c, d in COMANDOS_DEL_TITULAR
                                                   if c in _SOLO_TITULAR]
        lineas += ["", "Quien escriba sin invitación no recibe respuesta (D4): si invitas a alguien, "
                       "que entre por el enlace antes de escribir nada."]
    if chat.perfil and chat.perfil.get("estado") == "encuesta":
        lineas += ["", "Tu perfil aún está sin terminar: hasta que acabes la encuesta (/start la retoma) no recibirás avisos."]
    chat.decir("\n".join(lineas))


# El primer /start del titular en una instalación por estrenar (12-11). Hasta ese día recibía «Eres el titular»
# y se quedaba con el perfil de fábrica (`titular-2026-10`, soporte y ciberseguridad en Barcelona), que es el
# de quien escribió esto: lo pidió el titular al publicar el proyecto, para que quien lo instale empiece de cero
# con el cuestionario, como cualquier invitado.
BIENVENIDA_DEL_TITULAR = (
    "👋 Hola. Eres quien administra este bot: /ayuda tiene también los comandos de administración.\n\n"
    "Primero, tu perfil: con el cuestionario de aquí abajo (3-4 minutos) sé qué buscar para ti. Hasta que lo "
    "termines no busco nada. La búsqueda automática viene apagada: cuando acabes, /frecuencia la enciende.")


def _por_estrenar(chat: Chat) -> bool:
    """
    ¿Es el titular de una instalación que no ha usado nadie? Sin fila, sin ofertas y sin marcas. La fila falta
    también en una base recién migrada de los JSON (no: la migración la crea) o si alguien ha lanzado /corrida
    antes del primer /start; en ese caso ya tiene ofertas del perfil de fábrica y se le deja como está, con
    /reiniciar a mano. Sin fila es como ha corrido siempre el titular en las pruebas: nada de esto les afecta.
    """
    if not chat.es_titular or perfiles.obtener(chat.chat_id) is not None:
        return False
    marcas = perfiles.marcas(chat.chat_id)
    return perfiles.contar_ofertas(chat.chat_id) == 0 and not any(marcas.values())


def _start(chat: Chat, argumento: str) -> None:
    if _por_estrenar(chat):
        perfiles.guardar(chat.chat_id, perfiles.datos_a_cero(), "encuesta")
        chat.perfil = perfiles.obtener(chat.chat_id)
        logger.info("Primer /start del titular en una instalación nueva: empieza con el cuestionario.")
        chat.decir(BIENVENIDA_DEL_TITULAR)
        encuesta.iniciar(chat)
        return
    if chat.perfil and chat.perfil.get("estado") == "encuesta":
        encuesta.reanudar(chat)  # sigue donde lo dejó (MP8)
    elif chat.es_titular:
        chat.decir("👋 Hola. Eres el titular: /ayuda tiene también los comandos de administración.")
    else:
        chat.decir("👋 Ya estás dentro. /ayuda")


def _perfil(chat: Chat, _argumento: str = "") -> None:
    chat.decir(ficha(chat.perfil, chat.config, es_titular=chat.es_titular))


def _cambiar_estado_propio(chat: Chat, estado: str) -> None:
    if perfiles.obtener(chat.chat_id) is None:
        # El titular sin fila: se le crea la de la migración, con el estado pedido.
        perfiles.guardar(chat.chat_id, perfiles.PERFIL_GLOBAL["datos"], estado)
    else:
        perfiles.cambiar_estado(chat.chat_id, estado)
    chat.perfil = perfiles.obtener(chat.chat_id)


def _pausa(chat: Chat, _argumento: str = "") -> None:
    estado = chat.perfil.get("estado")
    if estado == "encuesta":
        chat.decir("Tu perfil aún está en preparación: no se busca nada para ti todavía.")
    elif estado == "pausado":
        chat.decir("Ya estaba en pausa. /reanudar cuando quieras.")
    else:
        _cambiar_estado_propio(chat, "pausado")
        extra = " Las corridas siguen para los demás perfiles." if chat.es_titular else ""
        chat.decir("⏸ En pausa: no se busca ni se avisa para ti." + extra + " /reanudar cuando quieras.")


def _reanudar(chat: Chat, _argumento: str = "") -> None:
    if chat.perfil.get("estado") != "pausado":
        chat.decir("No estaba en pausa." if chat.perfil.get("estado") == "activo"
                   else "Tu perfil aún está en preparación.")
        return
    _cambiar_estado_propio(chat, "activo")
    chat.decir("▶️ Reanudado: en la próxima corrida se vuelve a buscar para ti.")


def _top(chat: Chat, _argumento: str = "") -> None:
    """
    Sus 5 mejores vigentes (MP5; versión completa en MP9): las suyas vistas en los últimos 7 días que
    todavía no ha clasificado (ni ⭐, ni ✅, ni 🙈), repuntuadas con su puntuador como un aviso, y cada
    una con sus botones. Se apuntan en `avisos` como «top» para que los botones funcionen.

    Las que enseña salen de su cola de avisos: ya las ha visto, y mandárselas otra vez en el
    próximo resumen sería repetirlas. Dice cuántas le quedan esperando.
    """
    import buscador  # aquí y no arriba: arrastra jobspy, y el servicio ya lo tiene cargado

    if chat.perfil.get("estado") == "encuesta":
        chat.decir("Tu perfil aún está en preparación: todavía no hay ofertas para ti.")
        return
    desde = (datetime.now() - timedelta(days=DIAS_DEL_TOP)).isoformat(timespec="seconds")
    urls = perfiles.mejores_vigentes(chat.chat_id, desde, CANDIDATAS_DEL_TOP, sin_marcar=True)
    if not urls:
        chat.decir(f"No tienes ofertas vistas en los últimos {DIAS_DEL_TOP} días que no hayas clasificado ya.")
        return
    por_url = almacen.ofertas_por_url(urls, chat_id=chat.chat_id)
    puntuador = buscador.puntuador_para(chat.perfil, chat.config)
    anos_max = perfiles.config_efectiva(chat.perfil, chat.config).get("anos_experiencia_max")
    candidatas = [notificaciones.preparar_aviso(por_url[u], por_url[u], puntuador, anos_max)
                  for u in urls if u in por_url]
    mejores = sorted(candidatas, key=lambda o: (-o["relevancia"], o["job_url"]))[:TOP]
    chat.decir(f"⭐ <b>Tus {len(mejores)} mejores</b> de los últimos {DIAS_DEL_TOP} días, sin clasificar:")
    for oferta in mejores:
        clave = telegram_botones.registrar_aviso(oferta, chat.chat_id, tipo="top")
        chat.decir(notificaciones._mensaje_de_una_oferta(oferta), telegram_botones.teclado_inicial(clave))
    perfiles.quitar_pendientes(chat.chat_id, [o["job_url"] for o in mejores])
    esperan = avisos_perfil.en_cola(chat.chat_id)
    if esperan:
        chat.decir(f"➕ Te esperan {esperan} más para tu próximo envío.")


def _interesan_pendientes(chat_id: int) -> list[tuple[str, dict]]:
    """Lo que guardó con ⭐ y no ha aplicado ni descartado después: lo último guardado, primero."""
    marcas = perfiles.marcas(chat_id)
    fuera = set(marcas["aplicadas"]) | set(marcas["ocultas"])
    pendientes = [(url, info) for url, info in marcas["favoritas"].items() if url not in fuera]
    pendientes.sort(key=lambda par: (par[1].get("fecha") or "", par[0]), reverse=True)
    return pendientes


def _hace(iso: str | None, ahora: datetime) -> int | None:
    try:
        return max(0, (ahora - datetime.fromisoformat(str(iso)[:19])).days) if iso else None
    except ValueError:
        return None


def _texto_de_interesan(chat: Chat, desde: int = 0) -> tuple[str, dict | None]:
    """
    Una página de /interesan (12-11): las ⭐ sin aplicar, con ✅ y 🙈 en cada una y botones para pasar página.

    Por qué existe: desde que se quitó la web (MP5) «Me interesa» no se podía ver en ningún sitio, solo
    subiendo por el chat, y en la práctica lo guardado se acumulaba sin que nadie volviera a ello.
    Guardar sin poder volver no acerca a nadie a un trabajo.

    Los botones son los de siempre (`a:` y `o:`, telegram_botones), con el número delante como en un
    resumen de MP9: así la pulsación cambia solo la fila de esa oferta y no hay código nuevo que marque
    nada. Necesitan su fila en `avisos`; la que no la tiene (las guardadas en la web, o hace más de 90
    días) se apunta como «interesa», que no es un aviso y no cuenta en ninguna cifra ni en el tope. La
    que ya la tiene no se toca: ni su tipo ni su fecha (ver `perfiles.registrar_aviso`).

    «Vista hace N días» es cuándo salió por última vez en una búsqueda SUYA, no si la oferta sigue
    abierta: comprobarlo sería pedirle la página al portal por cada una, y las consultas no suben
    (regla 2). Se dice tal cual para que no parezca más de lo que es.
    """
    pendientes = _interesan_pendientes(chat.chat_id)
    if not pendientes:
        return ("⭐ No tienes ninguna oferta guardada pendiente de aplicar.\n\n"
                "Lo que marques con «⭐ Me interesa» en un aviso aparecerá aquí."), None
    total = len(pendientes)
    desde = max(0, min(int(desde), ((total - 1) // INTERESAN_POR_PAGINA) * INTERESAN_POR_PAGINA))
    pagina = pendientes[desde:desde + INTERESAN_POR_PAGINA]
    por_url = almacen.ofertas_por_url([url for url, _ in pagina], chat_id=chat.chat_id)
    ahora = datetime.now()
    lineas = [f"⭐ <b>Te interesan y no has aplicado</b> · {desde + 1}–{desde + len(pagina)} de {total}", ""]
    filas = []
    for numero, (url, info) in enumerate(pagina, start=desde + 1):
        oferta = por_url.get(url) or {}
        titulo = _esc(str(oferta.get("title") or info.get("titulo") or "Sin título")[:120])
        empresa = oferta.get("company") or info.get("empresa")
        cabeza = f'{numero}. <a href="{_esc(url)}"><b>{titulo}</b></a>'
        if empresa:
            cabeza += f" — {_esc(str(empresa)[:60])}"
        if oferta.get("site"):
            cabeza += f" · {_esc(oferta['site'])}"
        datos = []
        guardada = _hace(info.get("fecha"), ahora)
        if guardada is not None:
            datos.append("guardada hoy" if guardada == 0 else f"guardada hace {guardada} día{'s' if guardada != 1 else ''}")
        vista = _hace(oferta.get("visto_por_ultima_vez"), ahora)
        if vista is None:
            datos.append("ya no está en tu lista")
        elif vista <= DIAS_PARA_DARLA_POR_VISTA:
            datos.append("sigue saliendo en las búsquedas")
        else:
            datos.append(f"⚠️ no sale en las búsquedas desde hace {vista} días")
        lineas += [cabeza, "   " + " · ".join(datos)]
        clave = telegram_botones.clave_de(url)
        if perfiles.aviso(chat.chat_id, clave) is None:
            telegram_botones.registrar_aviso(
                {"job_url": url, "title": oferta.get("title") or info.get("titulo"), "company": empresa,
                 "relevancia": oferta.get("relevancia"), "site": oferta.get("site")}, chat.chat_id, tipo="interesa")
        filas.append([_boton(f"{numero} ✅ Aplicada", f"a:{clave}"), _boton(f"{numero} 🙈 Ya no", f"o:{clave}")])
    paso = []
    if desde > 0:
        paso.append(_boton("« Anteriores", f"aj1:in:{desde - INTERESAN_POR_PAGINA}"))
    if desde + INTERESAN_POR_PAGINA < total:
        paso.append(_boton("Siguientes »", f"aj1:in:{desde + INTERESAN_POR_PAGINA}"))
    if paso:
        filas.append(paso)
    return "\n".join(lineas), {"inline_keyboard": filas}


def _interesan(chat: Chat, _argumento: str = "") -> None:
    chat.decir(*_texto_de_interesan(chat))


def _resumen(chat: Chat, _argumento: str = "") -> None:
    """
    Su semana (§4.5): avisos y descubrimientos, qué respondió con cada botón, la precisión
    ((✅ + ⭐ + 👍) / respondidos) y lo aprendido que más ha cambiado frente a hace 7 días. Todo suyo.
    """
    import aprendizaje

    ahora = datetime.now()
    hace = (ahora - timedelta(days=7)).isoformat(timespec="seconds")
    lista = perfiles.avisos_entre(chat.chat_id, hace, (ahora + timedelta(seconds=1)).isoformat(timespec="seconds"))
    avisos_ = [a for a in lista if a["tipo"] == "aviso"]
    descubrir = [a for a in lista if a["tipo"] == "descubrimiento"]
    respondidos = [a for a in lista if a.get("respuesta")]
    botones = {"favorito": "⭐", "aplicada": "✅", "oculta": "🙈", "encaja": "👍", "no_encaja": "👎"}
    cuenta = {b: sum(1 for a in respondidos if a["respuesta"] == r) for r, b in botones.items()}
    positivos = sum(1 for a in respondidos if a["respuesta"] in aprendizaje.POSITIVAS_DE_AVISO)
    lineas = ["📊 <b>Tu semana</b>",
              f"Avisos: {len(avisos_)} · para descubrir: {len(descubrir)} · respondidos: {len(respondidos)}",
              "Por botón: " + " · ".join(f"{b} {n}" for b, n in cuenta.items())]
    if respondidos:
        lineas.append(f"Precisión: {round(100 * positivos / len(respondidos))} % ({positivos} de {len(respondidos)})")
    else:
        lineas.append("Precisión: sin respuestas esta semana")
    estado = aprendizaje.estado(chat.perfil, chat.config)
    ajustes = aprendizaje.ajustes(chat.perfil)
    antes, _ = aprendizaje.aprender(aprendizaje.etiquetas_de(chat.chat_id, ajustes, hasta=hace), ajustes["olvidados"])
    pesos = estado["capa"].pesos
    cambios = sorted(((r, p - antes.get(r, 0.0)) for r, p in pesos.items() if abs(p - antes.get(r, 0.0)) >= 0.2),
                     key=lambda x: (-abs(x[1]), x[0]))[:5]
    lineas.append("")
    if not estado["activa"]:
        lineas.append(_estado_de_la_capa(estado, ajustes))
    elif cambios:
        lineas.append("🧠 Lo aprendido que más ha cambiado:")
        lineas += [f"  {_peso(p)} {_esc(aprendizaje.nombre_de_rasgo(r))}" + ("" if r in antes else " (nuevo)")
                   for r, p in cambios]
    else:
        lineas.append("🧠 Lo aprendido no ha cambiado esta semana.")
    chat.decir("\n".join(lineas))


def _peso(valor: float) -> str:
    return ("+" if valor > 0 else "−") + ("%.1f" % abs(valor)).replace(".", ",")


def _estado_de_la_capa(estado: dict, ajustes: dict) -> str:
    import aprendizaje

    if estado["motivo"] == "apagada":
        return (f"🧠 Lo aprendido está <b>apagado</b> desde el {_fecha(ajustes['apagada'].get('fecha'))}: "
                "dos semanas seguidas con menos precisión que sin ello.")
    if estado["motivo"] == "pocas":
        return (f"🧠 Lo aprendido se pone solo a las {aprendizaje.MIN_ETIQUETAS} respuestas; llevas "
                f"{estado['etiquetas']}.")
    if estado["motivo"] == "nada":
        return "🧠 Con tus respuestas todavía no hay nada claro que aprender."
    return f"🧠 Lo aprendido está puesto: sale de {estado['etiquetas']} respuestas tuyas."


def _ajustes(chat: Chat, _argumento: str = "") -> None:
    if chat.perfil.get("estado") == "encuesta":
        chat.decir("Primero termina la encuesta: /start la retoma donde la dejaste.")
        return
    # Un menú nuevo deja sin efecto una edición a medias: no quedan dos conversaciones abiertas.
    if perfiles.paso_de(chat.chat_id) is not None:
        perfiles.borrar_conversacion(chat.chat_id)
    chat.decir(*encuesta.texto_de_ajustes(chat.perfil, chat.es_titular))


def _texto_de_avisos(perfil: dict) -> tuple[str, dict]:
    import aprendizaje

    activo = aprendizaje.descubrimiento_activo(perfil)
    cfg = avisos_perfil.config_de(perfil)
    modos = {"instantaneo": "al momento", "diario": "un resumen al día (9:00)",
             "dos_diarios": "dos resúmenes al día (9:00 y 18:00)"}
    silencio = f"{cfg.silencio[0]}:00–{cfg.silencio[1]}:00" if cfg.silencio else "sin silencio"
    tope = f"{cfg.max_dia} al día" if cfg.max_dia else "sin tope"

    def marca(texto: str, puesto: bool) -> str:
        return ("✅ " if puesto else "") + texto

    texto = (f"🔔 <b>Avisos</b>\n\n⏰ Cuándo: <b>{modos[cfg.modo]}</b>\n🌙 No molestar: <b>{silencio}</b>\n"
             f"🔢 Tope: <b>{tope}</b>\n\n"
             "Lo que llega fuera de hora o pasa del tope no se pierde: espera y sale en el siguiente envío. "
             "Hora de Madrid.\n\n"
             "🔍 Ofertas para descubrir: <b>" + ("encendidas" if activo else "apagadas") + "</b>\n"
             f"Como mucho {aprendizaje.DESCUBRIMIENTO_AL_DIA} al día, de las que se quedan justo por debajo de lo "
             "que te aviso, con 👍 / 👎. Sirven para afinar lo que te llega. Solo llegan en el modo «al momento».")
    filas = [
        [_boton(marca("Al momento", cfg.modo == "instantaneo"), "aj1:am:i")],
        [_boton(marca("Un resumen al día", cfg.modo == "diario"), "aj1:am:d"),
         _boton(marca("Dos al día", cfg.modo == "dos_diarios"), "aj1:am:2")],
        [_boton(marca("🌙 22:00–8:00", cfg.silencio == (22, 8)), "aj1:as:1"),
         _boton(marca("Sin silencio", cfg.silencio is None), "aj1:as:0")],
        [_boton(marca(str(n), cfg.max_dia == n), f"aj1:at:{n}") for n in OPCIONES_DE_TOPE]
        + [_boton(marca("Sin tope", cfg.max_dia is None), "aj1:at:0")],
        [_boton("Apagar descubrimiento" if activo else "Encender descubrimiento", f"aj1:ad:{0 if activo else 1}")],
        [_boton("↩️ Ajustes", "aj1:aj")]]
    return texto, {"inline_keyboard": filas}


def _texto_de_lo_aprendido(chat: Chat) -> tuple[str, dict]:
    import aprendizaje

    estado = aprendizaje.estado(chat.perfil, chat.config)
    ajustes = aprendizaje.ajustes(chat.perfil)
    pesos = estado["capa"].pesos
    suben = sorted((r for r in pesos if pesos[r] > 0), key=lambda r: (-pesos[r], r))[:aprendizaje.EN_LO_APRENDIDO]
    bajan = sorted((r for r in pesos if pesos[r] < 0), key=lambda r: (pesos[r], r))[:aprendizaje.EN_LO_APRENDIDO]
    lineas = ["🧠 <b>Lo aprendido de tus botones</b>", _estado_de_la_capa(estado, ajustes)]
    if pesos:
        lineas.append("Suma o resta a cada oferta, como mucho ±3." + ("" if estado["activa"] else " Ahora no se aplica."))
    if suben:
        lineas += ["", "<b>Lo que más sube</b>"] + [f"  {_peso(pesos[r])} {_esc(aprendizaje.nombre_de_rasgo(r))}"
                                                     for r in suben]
    if bajan:
        lineas += ["", "<b>Lo que más baja</b>"] + [f"  {_peso(pesos[r])} {_esc(aprendizaje.nombre_de_rasgo(r))}"
                                                     for r in bajan]
    if estado["capa"].protegidos:
        lineas += ["", "Rebajado para no dejar fuera ninguna ⭐ ni ✅: "
                   + _esc(", ".join(aprendizaje.nombre_de_rasgo(r) for r in estado["capa"].protegidos))]
    if ajustes["olvidados"]:
        lineas.append(f"Olvidados a petición tuya: {len(ajustes['olvidados'])}.")
    filas = [[_boton(f"➖ Olvidar «{aprendizaje.nombre_de_rasgo(r)[:40]}»", f"aj1:ol:{aprendizaje.huella(r)}")]
             for r in suben + bajan]
    if ajustes["apagada"]:
        filas.append([_boton("🔛 Volver a encenderlo", "aj1:en")])
    filas.append([_boton("🔄 Empezar de cero", f"aj1:c1:{_minuto()}")])
    filas.append([_boton("↩️ Ajustes", "aj1:aj")])
    return "\n".join(lineas), {"inline_keyboard": filas}


def ofrecer_sugerencia(perfil: dict, config_global: dict, sugerencia: dict, destino: int | None,
                       posicion: str | None = None) -> bool:
    """
    Una sugerencia de fondo (§5.4) a su perfil, con [Sí, aplícalo] [No]. Solo si, aplicada, no deja
    fuera ninguna ⭐ ni ✅ suya: si no, se apunta como no segura y no se vuelve a mirar en una semana.
    `destino` None es el titular, como en el resto de avisos.
    """
    import aprendizaje

    chat_id = perfil["chat_id"]
    ahora = perfiles._ahora()
    if not aprendizaje.segura(perfil, config_global, sugerencia):
        aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(sugerencias=(a.get("sugerencias") or [])[-19:] + [
            {"fecha": ahora, "clave": sugerencia["clave"], "valor": sugerencia["valor"], "resultado": "no_segura"}]))
        logger.info("Sugerencia de fondo (%s) descartada: dejaría fuera una ⭐ o ✅.", sugerencia["tipo"])
        return False
    mes = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    urls = [a["url"] for a in perfiles.avisos_entre(chat_id, mes, (datetime.now() + timedelta(seconds=1)).isoformat())]
    efecto = aprendizaje.efecto(perfil, config_global, sugerencia, urls)
    texto = (f"💡 <b>Una sugerencia</b>\n{_esc(sugerencia['texto'])}\n\nDe las {efecto['de']} ofertas que te he "
             f"enseñado en 30 días habría quitado {len(efecto['fuera'])}, y ninguna de las que marcaste con ⭐ o ✅.")
    aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(sugerencia={**sugerencia, "ofrecida": ahora},
                                                            ultima_sugerencia=ahora))
    teclado = {"inline_keyboard": [[_boton("Sí, aplícalo", f"aj1:sg1:{sugerencia['id']}"),
                                    _boton("No", f"aj1:sg0:{sugerencia['id']}")]]}
    logger.info("Sugerencia de fondo ofrecida (%s).", sugerencia["tipo"])
    return notificaciones._enviar(config_global, texto, "sugerencia de fondo", teclado,
                                  None if destino is None else [destino], posicion)


def _misdatos(chat: Chat, _argumento: str = "") -> None:
    if perfiles.obtener(chat.chat_id) is None:
        chat.decir("Todavía no hay nada guardado a tu nombre.")
        return
    encolar = SERVICIO["encolar"]
    estado = SERVICIO["estado_corrida"]() if SERVICIO["estado_corrida"] else {}
    if encolar is None:
        _mandar_misdatos(chat)
        return
    encolar("misdatos", lambda: _mandar_misdatos(chat))
    chat.decir("📦 Preparando tus datos." + (" Hay una búsqueda en curso: te llegan cuando acabe."
                                           if estado.get("activa") else " Te llegan en un momento."))


def _mandar_misdatos(chat: Chat) -> None:
    contenido = perfiles.exportar_comprimido(chat.chat_id)
    nombre = f"misdatos-{datetime.now():%Y%m%d}.json.gz"
    respuesta = notificaciones.llamar(
        chat.token, "sendDocument",
        {"chat_id": str(chat.chat_id),
         "caption": "Todo lo tuyo: tu ficha, tus ofertas (sin el texto del anuncio), lo que has marcado y "
                    "los avisos. Es un JSON comprimido."},
        timeout=60, archivos={"document": (nombre, contenido, "application/gzip")})
    if respuesta.ok:
        logger.info("/misdatos enviado: %s KB comprimidos.", round(len(contenido) / 1024))
    else:
        logger.warning("/misdatos no se ha podido enviar (%s): %s", respuesta.codigo, respuesta.descripcion[:200])
        chat.decir("No he podido mandarte el fichero. Prueba más tarde.")


def _borrar(chat: Chat, _argumento: str = "") -> None:
    if perfiles.obtener(chat.chat_id) is None:
        chat.decir("Todavía no hay nada guardado a tu nombre.")
        return
    texto = ("🗑 <b>¿Borrar todo lo tuyo?</b> Tu perfil, tus ofertas, lo que has marcado y los avisos. "
             "Queda una copia que solo puede borrar quien administra el bot. Después, este bot dejará "
             "de contestarte hasta que te inviten otra vez.")
    if chat.es_titular:
        texto = ("🗑 <b>¿Borrar todo lo tuyo?</b> Tus ofertas, marcas y avisos, con copia previa. Tu perfil "
                 "se queda (eres el administrador). Te lo preguntaré dos veces.")
    chat.decir(texto, {"inline_keyboard": [[_boton("Sí, borrar todo", f"aj1:b1:{_minuto()}"),
                                            _boton("No", "aj1:no")]]})


# ----------------------------------------------------------------------
# Reiniciar un perfil (11-11): volver a como entró, con el cuestionario por hacer
#
# Lo pide la persona con /reiniciar o el titular desde la ficha de /perfiles. Se va todo lo suyo —sus
# respuestas, su plan, sus ofertas, lo que marcó, sus avisos, su cola y lo aprendido de sus botones (que
# son esas marcas y `ajustes_aprendidos`)— y se queda la fila, con su fecha de alta: conserva el sitio en
# el tope de perfiles y no necesita otra invitación. Antes, copia en data/borrados (regla 7), como /borrar.
#
# Siempre con un aviso previo que dice qué se pierde, y con una confirmación que caduca a los 10 minutos.
# Descartado guardar lo de antes en el `historial` del perfil, como hace «rehacer con la encuesta»: quien
# pide empezar de cero no espera que sus respuestas viejas sigan en su fila (y /misdatos las enseñaría).
# ----------------------------------------------------------------------
AVISO_DE_REINICIO = (
    "♻️ <b>¿Reiniciar tu perfil?</b> Vuelves a empezar como el primer día:\n"
    "• se borran tus respuestas del cuestionario, tus ofertas, lo que has marcado (⭐ ✅ 🙈), tus avisos y "
    "lo que he aprendido de tus botones;\n"
    "• te mando el cuestionario otra vez (3-4 minutos) y, hasta que lo termines, no recibes avisos;\n"
    "• conservas tu sitio: no hace falta otra invitación.\n\n"
    "Queda una copia de lo de ahora que solo puede borrar quien administra el bot. <b>No se puede deshacer.</b>\n\n"
    "Si solo quieres cambiar algo (puestos, ciudad, experiencia, avisos…), /ajustes lo hace sin borrar nada.")
# El del titular (12-11): lo mismo, más lo que solo le pasa a él. Hasta ese día su /reiniciar se negaba; se
# abrió al preparar la copia pública, para quien instale el bot y quiera dejar el perfil de fábrica sin tocar
# ficheros. Lo que hay que decirle es que pierde las reglas escritas a mano y que el bot sigue para los demás
# mientras él contesta: lo segundo es lo que antes se daba como razón para no dejarle.
AVISO_DE_REINICIO_DEL_TITULAR = (
    "♻️ <b>¿Reiniciar tu perfil?</b> Es el de quien administra el bot. Tu búsqueda vuelve a empezar como el primer día:\n"
    "• se borran tus ofertas, lo que has marcado (⭐ ✅ 🙈), tus avisos y lo que he aprendido de tus botones;\n"
    "• si tu perfil usa reglas escritas a mano, deja de usarlas y pasa a salir del cuestionario, como los demás;\n"
    "• te mando el cuestionario (3-4 minutos) y, hasta que lo termines, no busco nada para ti. Para los demás "
    "todo sigue igual, y tú sigues administrando.\n\n"
    "Queda una copia de lo de ahora en data/borrados. <b>Desde el bot no se puede deshacer.</b>\n\n"
    "Si solo quieres cambiar algo (modalidad, distancia, experiencia, avisos…), /ajustes lo hace sin borrar nada.")


def _horas_para_reiniciar(perfil: dict | None) -> int:
    """Cuántas horas faltan para que /reiniciar vuelva a valer (0 si ya vale). Mira la marca del último."""
    marca = ((perfil or {}).get("datos") or {}).get("reiniciado")
    try:
        falta = datetime.fromisoformat(str(marca)) + timedelta(hours=HORAS_ENTRE_REINICIOS) - datetime.now()
    except ValueError:
        return 0
    return max(0, -int(-falta.total_seconds() // 3600))  # hacia arriba: «en 1 hora», no «en 0»


def _no_se_reinicia(chat: Chat) -> str | None:
    """
    Por qué este chat no puede reiniciar su perfil ahora, o None si puede. El titular, con las mismas reglas
    que cualquiera desde el 12-11 (las 24 h también: cada reinicio acaba en una corrida de arranque). Sin fila
    sí puede: es el titular de una instalación nueva, y `perfiles.borrar` se la crea para reiniciarla.
    """
    perfil = perfiles.obtener(chat.chat_id)
    if perfil is None and not chat.es_titular:
        return "Todavía no hay nada guardado a tu nombre."
    perfil = perfil or {}
    if perfil.get("estado") == "encuesta":
        return "Tu perfil ya está a cero: /start sigue con el cuestionario donde lo dejaste."
    horas = _horas_para_reiniciar(perfil)
    if horas:
        return (f"♻️ Tu perfil ya se reinició hace menos de {HORAS_ENTRE_REINICIOS} horas: podrás volver a hacerlo "
                f"en {'1 hora' if horas == 1 else f'{horas} horas'}. Mientras, /ajustes cambia lo que quieras sin "
                "empezar de cero.")
    return None


def _reiniciar(chat: Chat, _argumento: str = "") -> None:
    pega = _no_se_reinicia(chat)
    if pega:
        chat.decir(pega)
        return
    chat.decir(AVISO_DE_REINICIO_DEL_TITULAR if chat.es_titular else AVISO_DE_REINICIO,
               {"inline_keyboard": [[_boton("♻️ Sí, reiniciar", f"aj1:r1:{_minuto()}"), _boton("No", "aj1:no")]]})


def _confirmar_reinicio(chat: Chat, sello: str | None, mensaje_id) -> tuple[str, str]:
    """`aj1:r1:<minuto>`: el «Sí» del aviso. Todo se vuelve a mirar aquí: un botón se puede pulsar dos veces."""
    if sello is None or not _vigente(sello):
        _editar(chat.token, chat.chat_id, mensaje_id, "Esa confirmación ha caducado. Vuelve a pedir /reiniciar.")
        return "caducado", "Caducado"
    pega = _no_se_reinicia(chat)
    if pega:
        _editar(chat.token, chat.chat_id, mensaje_id, pega)
        return "no_se_reinicia", "No se ha reiniciado"
    copia, quitado = perfiles.copia_y_borrado(chat.chat_id, reiniciar=True)
    logger.info("/reiniciar %s: %s (copia en data/borrados/%s).", "del titular" if chat.es_titular else "de un perfil",
                ", ".join(f"{v} {k}" for k, v in quitado.items()), copia.name)
    chat.perfil = perfiles.obtener(chat.chat_id)
    # Al titular, dónde está la copia en el mismo mensaje: el aviso de abajo es para cuando lo hace otro.
    donde = f" La copia está en data/borrados/{_esc(copia.name)}." if chat.es_titular else ""
    _editar(chat.token, chat.chat_id, mensaje_id, "♻️ <b>Perfil reiniciado.</b> Ya no queda nada de lo de antes."
                                                  f"{donde} Aquí abajo tienes el cuestionario.")
    encuesta.iniciar(chat)
    if not chat.es_titular:
        _al_titular(chat.config, "♻️ Un perfil se ha reiniciado con /reiniciar y vuelve a hacer el cuestionario. "
                                 f"Copia de lo de antes en data/borrados/{copia.name}.")
    return "reiniciado", "Reiniciado"


# ----------------------------------------------------------------------
# Comandos del titular
# ----------------------------------------------------------------------
def _invitar(chat: Chat, _argumento: str = "") -> None:
    if not perfiles.hay_hueco(chat.config):
        chat.decir(f"Ya hay {_cuantos_perfiles()} de {chat.config.get('max_perfiles')} perfiles: no queda sitio.")
        return
    chat.decir("🔗 <b>¿Con qué perfil entra?</b>\n\n• <b>Soporte IT + ciber BCN, ≤ 1 año</b>: el juego "
               "<code>titular-2026-10</code> con el tope de 1 año, lo que tenía el adicional. Avisos desde la "
               "primera corrida (5 como mucho).\n• <b>Encuesta</b>: la persona contesta unas 12 preguntas (3-4 min) "
               "y tiene un perfil de su sector y su ciudad, con una primera tanda de ofertas al acabar.",
               {"inline_keyboard": [[_boton("Soporte IT + ciber BCN", "adm1:inv:soporte")],
                                    [_boton("Encuesta", "adm1:inv:encuesta")]]})


def _cuantos_perfiles() -> int:
    return len({p["chat_id"] for p in perfiles.listar()} | {perfiles.titular()})


def _lista_de_perfiles() -> list[dict]:
    """El titular el primero, y luego por fecha de alta: el número es estable mientras nadie se vaya."""
    titular = perfiles.titular()
    todos = {p["chat_id"]: p for p in perfiles.listar()}
    if titular not in todos:
        todos[titular] = {**perfiles.PERFIL_GLOBAL, "chat_id": titular, "estado": "activo"}
    return [todos[titular]] + sorted((p for c, p in todos.items() if c != titular),
                                     key=lambda p: (p.get("creado") or "", p["chat_id"]))


def _perfil_por_ref(ref: str) -> tuple[int, dict] | tuple[None, None]:
    for numero, perfil in enumerate(_lista_de_perfiles(), 1):
        if _ref(perfil["chat_id"]) == ref:
            return numero, perfil
    return None, None


def _texto_de_perfiles(config: dict) -> tuple[str, dict]:
    lista = _lista_de_perfiles()
    titular = perfiles.titular()
    lineas = [f"👥 <b>Perfiles</b>: {len(lista)} de {config.get('max_perfiles') or '∞'}"]
    for numero, perfil in enumerate(lista, 1):
        quien = " (titular)" if perfil["chat_id"] == titular else ""
        lineas.append(f"{numero}.{quien} {_esc(perfil.get('estado'))} · alta {_fecha(perfil.get('creado'))}"
                      f" · actividad {_fecha(perfil.get('ultima_actividad'))}")
    botones = [_boton(f"Ficha {n}", f"adm1:fi:{_ref(p['chat_id'])}") for n, p in enumerate(lista, 1)]
    return "\n".join(lineas), {"inline_keyboard": [botones[i:i + 3] for i in range(0, len(botones), 3)]}


def _perfiles(chat: Chat, _argumento: str = "") -> None:
    chat.decir(*_texto_de_perfiles(chat.config))


def _teclado_de_ficha(perfil: dict) -> dict:
    ref = _ref(perfil["chat_id"])
    filas = []
    if perfil["chat_id"] != perfiles.titular():
        if perfil.get("estado") == "pausado":
            filas.append([_boton("▶️ Reanudar", f"adm1:re:{ref}")])
        elif perfil.get("estado") == "activo":
            filas.append([_boton("⏸ Pausar", f"adm1:pa:{ref}")])
        if perfil.get("estado") in ESTADOS_QUE_SE_REINICIAN:
            filas.append([_boton("♻️ Reiniciar", f"adm1:ri:{ref}")])
        filas.append([_boton("🗑 Borrar", f"adm1:bo:{ref}")])
    filas.append([_boton("↩️ Lista", "adm1:li")])
    return {"inline_keyboard": filas}


def _leer_proc(ruta: str) -> dict[str, int]:
    """Líneas «Clave:  123 kB» de /proc, en kB. Vacío donde no hay /proc (Windows)."""
    try:
        texto = Path(ruta).read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    valores = {}
    for linea in texto.splitlines():
        clave, _, resto = linea.partition(":")
        partes = resto.split()
        if partes and partes[0].isdigit():
            valores[clave.strip()] = int(partes[0])
    return valores


def _mib(kb: int | None) -> str:
    return "—" if kb is None else notificaciones._num(kb / 1024) + " MiB"


def _memoria_del_cgroup() -> str | None:
    """Lo que el contenedor gasta de su `mem_limit` (cgroup v2; v1 si es lo que hay)."""
    for actual, tope in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                         ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            usado = int(Path(actual).read_text().strip())
            limite = Path(tope).read_text().strip()
        except (OSError, ValueError):
            continue
        maximo = "sin tope" if limite == "max" or int(limite) > 1 << 50 else _mib(int(limite) // 1024)
        return f"{_mib(usado // 1024)} de {maximo} (con caché de página)"
    return None


def _tamano_de_datos() -> int:
    total = 0
    for raiz, _, ficheros in os.walk(perfiles.ruta().parent):
        for nombre in ficheros:
            try:
                total += os.path.getsize(os.path.join(raiz, nombre))
            except OSError:
                pass
    return total


# Una copia al día (el cron de las 04:10, DESPLIEGUE.md «Copias»): con más de 36 h, se ha saltado
# una, y eso hay que verlo en /estado y no el día que haga falta restaurar.
HORAS_SIN_COPIA = 36
_NOMBRE_DE_COPIA = re.compile(r"^copia-(\d{8}_\d{6})\.tar\.gz$")  # el de operacion/copia_perfiles.py


def _linea_de_copias(ahora: datetime | None = None) -> str:
    """
    Cuándo fue la última copia de perfiles.db, leído de los nombres de data/copias/ (hora UTC, la
    misma del contenedor) y de la última línea de su registro. No escribe nada ni abre ninguna copia:
    la copia la hace el host (operacion/copia_perfiles.py), y aquí solo se mira que siga haciéndose.
    """
    carpeta = perfiles.ruta().parent / "copias"
    ahora = ahora or datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        fechas = sorted(datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                        for m in (_NOMBRE_DE_COPIA.match(n) for n in os.listdir(carpeta)) if m)
    except OSError:
        fechas = []
    if not fechas:
        return "⚠️ Copias de perfiles.db: <b>ninguna</b> en data/copias (DESPLIEGUE.md, «Copias»)"
    horas = (ahora - fechas[-1]).total_seconds() / 3600
    linea = (f"Última copia: {fechas[-1].strftime('%d-%m %H:%M')} (hace {notificaciones._num(round(horas, 1))} h)"
             f" · {len(fechas)} en data/copias")
    if horas > HORAS_SIN_COPIA:
        linea = "⚠️ " + linea + f": <b>más de {HORAS_SIN_COPIA} h sin copia</b>"
    try:
        registro = (carpeta / "registro.log").read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        registro = []
    if registro and " FALLA " in registro[-1]:
        linea += " · ⚠️ <b>la última ha fallado</b> (data/copias/registro.log)"
    return linea


def texto_de_estado(config: dict) -> str:
    """
    /estado. La memoria es VmRSS + VmSwap, no VmRSS a secas: con el host cambiando a swap, el RSS
    solo baja sin que el proceso gaste menos (las series de MP1 no lo sumaron y por eso su mejora
    nunca quedó demostrada, CAMBIOS-MP1.md §9). Y VmHWM es un máximo: un pico quieto no demuestra
    que no crezca.
    """
    import buscador
    from fuentes_extra import jooble

    corrida = SERVICIO["estado_corrida"]() if SERVICIO["estado_corrida"] else {}
    lineas = ["📊 <b>Estado</b> (horas del contenedor, UTC)"]
    if corrida.get("activa"):
        lineas.append(f"Corrida: <b>en curso</b> ({corrida.get('hecho', 0)} de {corrida.get('total', 0)} consultas) "
                      f"desde {_fecha(corrida.get('iniciada_en'))}")
    elif corrida.get("terminada_en"):
        duracion = corrida.get("duracion_s")
        lineas.append(f"Última corrida: {_fecha(corrida.get('iniciada_en'))} → {_fecha(corrida.get('terminada_en'))}"
                      + (f", {notificaciones._num(duracion)} s" if duracion is not None else "")
                      + (f" · <b>error</b>: {_esc(str(corrida['error'])[:200])}" if corrida.get("error") else ""))
    else:
        lineas.append(f"Última corrida: {_fecha(perfiles.ultima_busqueda(perfiles.titular()))} (de antes de arrancar)")
    if config.get("busqueda_automatica_activa"):
        lineas.append(f"Automática cada {config.get('busqueda_automatica_horas')} h · próxima "
                      f"{_fecha(corrida.get('proxima_busqueda_automatica'))}")
    else:
        lineas.append("Búsqueda automática: <b>apagada</b> (/frecuencia)")
    try:
        plan, elegidas = buscador.consultas_de_la_corrida(perfiles.activos(), config)
        lineas.append(f"Consultas: {len(plan)} únicas, {len(elegidas)} por corrida; cada una se repite cada "
                      f"{notificaciones._num(buscador.periodo_de_rotacion(len(plan), config))} h "
                      f"(la mitad de la ventana son {notificaciones._num((config.get('hours_old') or 0) / 2)} h)")
    except Exception as e:  # /estado tiene que contestar aunque falle una parte
        lineas.append(f"Consultas: no se han podido calcular ({_esc(type(e).__name__)})")

    fuentes = salud.fuentes()
    caidas = [f for f, i in sorted(fuentes.items()) if i.get("desactivada")]
    lineas.append("Fuentes desactivadas: " + (", ".join(_esc(f) for f in caidas) if caidas else "ninguna")
                  + " (/fuentes)")
    try:
        uso = jooble._leer_uso()
        lineas.append(f"Jooble: {uso.get('peticiones', 0)} de {jooble.limite()} peticiones (para a las "
                      f"{jooble.limite() - jooble.RESERVA}) · última descarga {_fecha(uso.get('ultima_descarga'))}")
    except Exception as e:
        lineas.append(f"Jooble: no se ha podido leer el cupo ({_esc(type(e).__name__)})")

    propio = _leer_proc("/proc/self/status")
    if propio:
        rss, swap = propio.get("VmRSS"), propio.get("VmSwap", 0)
        lineas.append(f"Memoria del proceso: {_mib((rss or 0) + swap)} (VmRSS {_mib(rss)} + VmSwap {_mib(swap)}), "
                      f"pico residente {_mib(propio.get('VmHWM'))}")
    else:
        lineas.append("Memoria del proceso: no disponible (sin /proc)")
    cgroup = _memoria_del_cgroup()
    if cgroup:
        lineas.append(f"Contenedor: {cgroup}")
    host = _leer_proc("/proc/meminfo")
    if host.get("MemAvailable"):
        lineas.append(f"Host: {_mib(host['MemAvailable'])} disponibles de {_mib(host.get('MemTotal'))}")
    lineas.append(f"data/: {notificaciones._num(_tamano_de_datos() / 1048576)} MiB")
    lineas.append(_linea_de_copias())
    lineas.append(f"Perfiles: {_cuantos_perfiles()} de {config.get('max_perfiles') or '∞'} · invitaciones sin usar: "
                  f"{perfiles.invitaciones_pendientes()}")
    return "\n".join(lineas)


def _estado(chat: Chat, _argumento: str = "") -> None:
    chat.decir(texto_de_estado(chat.config))


def _corrida(chat: Chat, _argumento: str = "") -> None:
    lanzar = SERVICIO["lanzar_corrida"]
    if lanzar is None:
        chat.decir("El servicio no está enganchado al bot: no se puede lanzar nada desde aquí.")
        return
    resultado = lanzar()
    if "error" in resultado:
        chat.decir(f"No se lanza: {_esc(resultado['error'])}. /estado dice cómo va.")
        return
    chat.decir(f"🔎 Corrida en marcha: {resultado['total']} consultas. Lo nuevo te llega como siempre; "
               "al acabar te digo cuánto ha tardado.")
    if SERVICIO["encolar"]:
        # Va detrás de la corrida en la misma cola, así que sale justo cuando acaba.
        SERVICIO["encolar"]("fin de /corrida", lambda: _fin_de_corrida(chat))


def _fin_de_corrida(chat: Chat) -> None:
    corrida = SERVICIO["estado_corrida"]() if SERVICIO["estado_corrida"] else {}
    if corrida.get("error"):
        chat.decir(f"⚠️ La corrida ha fallado: {_esc(str(corrida['error'])[:300])}")
        return
    nuevas = (corrida.get("resumen") or {}).get("nuevas")
    duracion = corrida.get("duracion_s")
    chat.decir("✅ Corrida terminada" + (f" en {notificaciones._num(duracion)} s" if duracion is not None else "")
               + (f": {nuevas} oferta(s) nueva(s) en tu lista." if nuevas is not None else "."))


def _sitios_configurados(config: dict) -> list[str]:
    sitios = set()
    for perfil in perfiles.activos():
        try:
            ambitos = perfiles.config_efectiva(perfil, config).get("ambitos") or {}
        except ValueError:
            continue
        for ambito in ambitos.values():
            if ambito.get("activo", True):
                sitios.update(ambito.get("sitios") or [])
    return sorted(sitios)


def _texto_de_fuentes(config: dict) -> tuple[str, dict]:
    fuentes = salud.fuentes()
    nombres = sorted(set(_sitios_configurados(config)) | set(fuentes))
    lineas = ["🛰 <b>Fuentes</b>"]
    botones = []
    for nombre in nombres:
        info = fuentes.get(nombre) or {}
        if info.get("desactivada"):
            lineas.append(f"⛔ <b>{_esc(nombre)}</b> — desactivada desde {_fecha(info.get('desactivada_en'))}: "
                          f"{_esc(str(info.get('motivo') or '')[:160])}")
            botones.append([_boton(f"Reactivar {nombre}"[:40], f"adm1:fu:{_ref(nombre)}")])
        else:
            lineas.append(f"✅ {_esc(nombre)}")
    if botones:
        lineas.append("\nUna fuente desactivada no se vuelve a consultar hasta que alguien la reactive. "
                      "Si vuelve a bloquear, se para sola otra vez y no se insiste.")
    return "\n".join(lineas), ({"inline_keyboard": botones} if botones else None)


def _fuentes(chat: Chat, _argumento: str = "") -> None:
    chat.decir(*_texto_de_fuentes(chat.config))


def _texto_de_frecuencia(config: dict) -> tuple[str, dict]:
    import buscador

    activa = bool(config.get("busqueda_automatica_activa"))
    horas = config.get("busqueda_automatica_horas")
    lineas = [f"⏱ Búsqueda automática: <b>{'encendida' if activa else 'apagada'}</b>, cada {horas} h."]
    try:
        unicas = len(buscador.consultas_de_la_corrida(perfiles.activos(), config)[0])
        lineas.append(f"Con {unicas} consultas únicas, cada una se repite cada "
                      f"{notificaciones._num(buscador.periodo_de_rotacion(unicas, config))} h "
                      f"(conviene no pasar de {notificaciones._num((config.get('hours_old') or 0) / 2)}).")
    except Exception as e:
        # /frecuencia contesta aunque no se pueda calcular la rotación, pero no en silencio: es el mismo
        # cálculo que /estado, y si falla aquí falla también la corrida (MP10).
        logger.warning("/frecuencia: no se ha podido calcular la rotación (%s).", type(e).__name__)
        lineas.append(f"Rotación: no se ha podido calcular ({_esc(type(e).__name__)}).")
    filas = [[_boton(("• " if float(horas or 0) == h else "") + f"{h} h", f"adm1:fr:{h}") for h in HORAS_DE_FRECUENCIA],
             [_boton("Apagar" if activa else "Encender", f"adm1:fa:{0 if activa else 1}")]]
    return "\n".join(lineas), {"inline_keyboard": filas}


def _frecuencia(chat: Chat, _argumento: str = "") -> None:
    chat.decir(*_texto_de_frecuencia(chat.config))


_COMANDOS = {
    "start": _start, "ayuda": _ayuda, "help": _ayuda, "perfil": _perfil, "pausa": _pausa,
    "reanudar": _reanudar, "top": _top, "interesan": _interesan, "misdatos": _misdatos, "borrar": _borrar, "reiniciar": _reiniciar,
    "invitar": _invitar, "perfiles": _perfiles, "estado": _estado, "corrida": _corrida,
    "fuentes": _fuentes, "frecuencia": _frecuencia, "resumen": _resumen, "ajustes": _ajustes,
}


# ----------------------------------------------------------------------
# Las pulsaciones de los botones nuevos
# ----------------------------------------------------------------------
def _pulsacion(pulsacion: dict, config: dict) -> str | None:
    datos = pulsacion.get("data") or ""
    prefijo, _, resto = datos.partition(":")
    if prefijo not in ("adm1", "aj1", "ob1"):
        # Los de las ofertas: los de siempre, con sus pruebas (test_botones.py). Son lo que más hace
        # cada persona, así que también cuentan como actividad (D9), una vez al día como mucho.
        hecho = telegram_botones.procesar({"callback_query": pulsacion}, config)
        chat_id = ((pulsacion.get("message") or {}).get("chat") or {}).get("id")
        if hecho not in (None, "rechazada") and isinstance(chat_id, int):
            perfiles.anotar_actividad(chat_id)
        return hecho
    token = (config.get("telegram_bot_token") or "").strip()
    mensaje = pulsacion.get("message") or {}
    datos_chat = mensaje.get("chat") or {}
    if datos_chat.get("type", "private") != "private" or "id" not in datos_chat:
        _contestar(token, pulsacion.get("id"), "Este botón no funciona aquí.")
        return "boton:rechazado"
    chat = Chat(int(datos_chat["id"]), config)
    if (prefijo == "adm1" and not chat.es_titular) or (prefijo in ("aj1", "ob1") and chat.perfil is None):
        _contestar(token, pulsacion.get("id"), "Este botón no funciona en este chat.")
        logger.warning("Botón %s rechazado: el chat no puede usarlo.", prefijo)
        return "boton:rechazado"
    mensaje_id = mensaje.get("message_id")
    if prefijo != "adm1":
        perfiles.anotar_actividad(chat.chat_id)  # D9: contestar la encuesta o tocar /ajustes es actividad
    manejador = {"adm1": _boton_admin, "aj1": _boton_propio, "ob1": encuesta.boton}[prefijo]
    hecho = manejador(chat, resto.split(":"), mensaje_id)
    _contestar(token, pulsacion.get("id"), hecho[1])
    return f"boton:{prefijo}:{hecho[0]}"


def _boton_propio(chat: Chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    accion = partes[0]
    if accion in ("aj", "av", "ad", "am", "as", "at", "ap", "la", "ol", "c1", "c2", "en", "sg1", "sg0"):
        return _boton_de_ajustes(chat, accion, partes[1:], mensaje_id)
    if accion == "no":
        _editar(chat.token, chat.chat_id, mensaje_id, "Vale, no se borra nada.")
        return "cancelado", "Cancelado"
    if accion == "in":
        # Pasar página de /interesan: se vuelve a leer la lista, porque entre una pulsación y otra puede
        # haber aplicado o descartado alguna y las posiciones se han movido.
        try:
            desde = int(partes[1]) if len(partes) > 1 else 0
        except ValueError:
            desde = 0
        _editar(chat.token, chat.chat_id, mensaje_id, *_texto_de_interesan(chat, desde))
        return "interesan", ""
    if accion == "r1":
        return _confirmar_reinicio(chat, partes[1] if len(partes) > 1 else None, mensaje_id)
    if accion in ("b1", "b2"):
        if len(partes) < 2 or not _vigente(partes[1]):
            _editar(chat.token, chat.chat_id, mensaje_id, "Esa confirmación ha caducado. Vuelve a pedir /borrar.")
            return "caducado", "Caducado"
        if chat.es_titular and accion == "b1":
            _editar(chat.token, chat.chat_id, mensaje_id,
                    "🗑 <b>Segunda confirmación.</b> Se van todas tus ofertas, marcas y avisos (queda copia en "
                    "data/borrados). Tu perfil y tu papel de administrador se quedan.",
                    {"inline_keyboard": [[_boton("Sí, de verdad", f"aj1:b2:{_minuto()}"), _boton("No", "aj1:no")]]})
            return "segunda", "Confirma otra vez"
        copia, quitado = perfiles.copia_y_borrado(chat.chat_id, conservar_perfil=chat.es_titular)
        logger.info("/borrar %s: %s (copia en data/borrados/%s).", "del titular" if chat.es_titular else "de un perfil",
                    ", ".join(f"{v} {k}" for k, v in quitado.items()), copia.name)
        if chat.es_titular:
            _editar(chat.token, chat.chat_id, mensaje_id,
                    f"Borrado: {quitado['enlaces']} ofertas, {quitado['marcas']} marcas y {quitado['avisos']} avisos. "
                    f"Copia en data/borrados/{copia.name}.")
        else:
            _editar(chat.token, chat.chat_id, mensaje_id, "Borrado. Gracias por usarlo. 👋")
            _al_titular(chat.config, f"👋 Un perfil se ha dado de baja con /borrar. Quedan {_cuantos_perfiles()}. "
                                     f"Copia en data/borrados/{copia.name}.")
        return "borrado", "Borrado"
    return "desconocido", "Botón desconocido."


def _boton_de_ajustes(chat: Chat, accion: str, args: list[str], mensaje_id) -> tuple[str, str]:
    """Los de /ajustes y las sugerencias (MP6). Solo tocan al chat que pulsa: no llevan ningún chat_id."""
    import aprendizaje

    token, chat_id = chat.token, chat.chat_id

    def recargar() -> None:
        chat.perfil = perfiles.obtener(chat_id) or chat.perfil

    if accion == "aj":
        _editar(token, chat_id, mensaje_id, *encuesta.texto_de_ajustes(chat.perfil, chat.es_titular))
        return "ajustes", ""
    if accion == "av":
        _editar(token, chat_id, mensaje_id, *_texto_de_avisos(chat.perfil))
        return "avisos", ""
    if accion == "ad" and args and args[0] in ("0", "1"):
        perfiles.actualizar_datos(chat_id, lambda d: d.setdefault("avisos", {}).update(descubrimiento=args[0] == "1"))
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_avisos(chat.perfil))
        return "descubrimiento", "Encendidas" if args[0] == "1" else "Apagadas"
    if accion == "am" and args and args[0] in ("i", "d", "2"):
        modo = {"i": "instantaneo", "d": "diario", "2": "dos_diarios"}[args[0]]
        perfiles.actualizar_datos(chat_id, lambda d: d.setdefault("avisos", {}).update(modo=modo))
        avisos_perfil.al_cambiar_modo(chat_id, modo)
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_avisos(chat.perfil))
        return "modo_" + modo, "Anotado"
    if accion == "as" and args and args[0] in ("0", "1"):
        def silencio(d):
            avisos = d.setdefault("avisos", {})
            if args[0] == "1":
                avisos["silencio"] = list(avisos_perfil.SILENCIO_POR_DEFECTO)
            else:
                avisos.pop("silencio", None)
        perfiles.actualizar_datos(chat_id, silencio)
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_avisos(chat.perfil))
        return "silencio_" + args[0], "Anotado"
    if accion == "at" and args and args[0].isdigit() and (int(args[0]) == 0 or int(args[0]) in OPCIONES_DE_TOPE):
        def tope(d):
            avisos = d.setdefault("avisos", {})
            if int(args[0]):
                avisos["max_dia"] = int(args[0])
            else:
                avisos.pop("max_dia", None)
        perfiles.actualizar_datos(chat_id, tope)
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_avisos(chat.perfil))
        return "tope_" + args[0], "Anotado"
    if accion == "ap" and args and args[0] in ("s", "p"):
        # «¿Sigues buscando?» (D9). La pulsación ya ha contado como actividad (_pulsacion).
        _editar(token, chat_id, mensaje_id, avisos_perfil.responder_pregunta(chat_id, args[0] == "s"))
        return "autopausa_" + args[0], "Hecho"
    if accion == "la":
        _editar(token, chat_id, mensaje_id, *_texto_de_lo_aprendido(chat))
        return "lo_aprendido", ""
    if accion == "ol" and args:
        pesos = aprendizaje.estado(chat.perfil, chat.config)["capa"].pesos
        rasgo = next((r for r in pesos if aprendizaje.huella(r) == args[0]), None)
        if rasgo is None:
            _editar(token, chat_id, mensaje_id, *_texto_de_lo_aprendido(chat))
            return "ya_olvidado", "Ya no estaba"
        aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(
            olvidados=(a.get("olvidados") or [])[-(aprendizaje.MAX_RASGOS - 1):] + [rasgo]))
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_lo_aprendido(chat))
        return "olvidado", "Olvidado: " + aprendizaje.nombre_de_rasgo(rasgo)[:40]
    if accion == "c1":
        _editar(token, chat_id, mensaje_id,
                "🔄 <b>¿Empezar de cero?</b> Se olvida todo lo aprendido hasta hoy y tu puntuación vuelve a ser "
                "la de las reglas. Tus marcas se quedan, y desde ahora se vuelve a aprender.",
                {"inline_keyboard": [[_boton("Sí, de cero", f"aj1:c2:{_minuto()}"), _boton("No", "aj1:la")]]})
        return "confirmar_cero", "Confirma"
    if accion == "c2":
        if not args or not _vigente(args[0]):
            _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado.",
                    {"inline_keyboard": [[_boton("🧠 Lo aprendido", "aj1:la")]]})
            return "caducado", "Caducado"
        ahora = perfiles._ahora()
        aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(desde=ahora, olvidados=[], apagada=None, semanas=[]))
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_lo_aprendido(chat))
        logger.info("Capa aprendida empezada de cero %s.", "por el titular" if chat.es_titular else "por un perfil")
        return "de_cero", "Empezado de cero"
    if accion == "en":
        aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(apagada=None, semanas=[]))
        recargar()
        _editar(token, chat_id, mensaje_id, *_texto_de_lo_aprendido(chat))
        return "encendida", "Encendido"
    if accion in ("sg1", "sg0") and args:
        pendiente = aprendizaje.ajustes(chat.perfil).get("sugerencia")
        if not pendiente or pendiente.get("id") != args[0]:
            _editar(token, chat_id, mensaje_id, "Esa sugerencia ya no está pendiente.")
            return "sugerencia_vieja", "Ya no está pendiente"
        aceptada = accion == "sg1"
        if aceptada:
            aprendizaje.aplicar_sugerencia(chat.perfil, pendiente)
        ahora = perfiles._ahora()
        aprendizaje.cambiar_ajustes(chat_id, lambda a: a.update(sugerencia=None, sugerencias=(
            a.get("sugerencias") or [])[-19:] + [{"fecha": ahora, "clave": pendiente["clave"], "valor": pendiente["valor"],
                                                  "resultado": "aceptada" if aceptada else "rechazada"}]))
        _editar(token, chat_id, mensaje_id, ("✅ Hecho. " if aceptada else "Vale, lo dejo como está. ")
                + _esc(pendiente["texto"]))
        logger.info("Sugerencia de fondo %s (%s).", "aceptada" if aceptada else "rechazada", pendiente["tipo"])
        return "sugerencia_" + ("aceptada" if aceptada else "rechazada"), "Hecho" if aceptada else "Vale"
    return "desconocido", "Botón desconocido."


def _boton_admin(chat: Chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    accion, args = partes[0], partes[1:]
    token, chat_id = chat.token, chat.chat_id
    if accion == "inv" and args and args[0] in perfiles.PLANTILLAS:
        if not perfiles.hay_hueco(chat.config):
            _editar(token, chat_id, mensaje_id, "No queda sitio: no se crea la invitación.")
            return "sin_hueco", "No queda sitio"
        codigo = perfiles.crear_invitacion(args[0])
        nombre = _nombre_del_bot(token)
        enlace = f"https://t.me/{nombre}?start={codigo}" if nombre else None
        texto = (f"🔗 Invitación de un solo uso ({'plantilla de soporte' if args[0] == 'soporte' else 'encuesta'}), "
                 f"caduca en {perfiles.DIAS_DE_UNA_INVITACION} días:\n\n")
        texto += (_esc(enlace) if enlace else f"(no sé el nombre del bot) que escriba <code>/start {codigo}</code>")
        texto += "\n\nQue entre por el enlace antes de escribir nada: sin él, el bot no le contesta."
        _editar(token, chat_id, mensaje_id, texto)
        logger.info("Invitación creada (%s).", args[0])
        return "invitacion", "Invitación creada"
    if accion == "li":
        _editar(token, chat_id, mensaje_id, *_texto_de_perfiles(chat.config))
        return "lista", ""
    if accion in ("fi", "pa", "re", "bo", "bs", "ri", "rs") and args:
        numero, perfil = _perfil_por_ref(args[0])
        if perfil is None:
            _editar(token, chat_id, mensaje_id, "Ese perfil ya no existe.")
            return "no_existe", "Ya no existe"
        es_el_titular = perfil["chat_id"] == perfiles.titular()
        if accion in ("pa", "re") and not es_el_titular and perfil.get("estado") in ("activo", "pausado"):
            perfiles.cambiar_estado(perfil["chat_id"], "pausado" if accion == "pa" else "activo")
            perfil = perfiles.obtener(perfil["chat_id"])
        elif accion == "bo" and not es_el_titular:
            _editar(token, chat_id, mensaje_id,
                    f"🗑 <b>¿Borrar el perfil {numero}?</b> Todo lo suyo, con copia en data/borrados. No se le avisa.",
                    {"inline_keyboard": [[_boton("Sí, borrar", f"adm1:bs:{args[0]}:{_minuto()}"),
                                          _boton("No", f"adm1:fi:{args[0]}")]]})
            return "confirmar_borrado", "Confirma"
        elif accion == "bs" and not es_el_titular:
            if len(args) < 2 or not _vigente(args[1]):
                _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado.", _teclado_de_ficha(perfil))
                return "caducado", "Caducado"
            copia, quitado = perfiles.copia_y_borrado(perfil["chat_id"])
            logger.info("Perfil borrado desde /perfiles: %s (copia en data/borrados/%s).",
                        ", ".join(f"{v} {k}" for k, v in quitado.items()), copia.name)
            _editar(token, chat_id, mensaje_id, f"Perfil {numero} borrado. Copia en data/borrados/{copia.name}.",
                    {"inline_keyboard": [[_boton("↩️ Lista", "adm1:li")]]})
            return "borrado", "Borrado"
        elif accion in ("ri", "rs") and not es_el_titular and perfil.get("estado") in ESTADOS_QUE_SE_REINICIAN:
            if accion == "ri":
                _editar(token, chat_id, mensaje_id,
                        f"♻️ <b>¿Reiniciar el perfil {numero}?</b> Se borra todo lo suyo —sus respuestas, sus ofertas, "
                        "lo que ha marcado, sus avisos y lo aprendido—, con copia en data/borrados, y vuelve a como "
                        "entró: con el cuestionario por hacer y sin avisos hasta que lo termine. Conserva su sitio.\n\n"
                        "<b>Se le avisa:</b> le llega un mensaje diciendo que quien administra el bot ha reiniciado "
                        "su perfil, y el cuestionario. No se puede deshacer desde el bot.",
                        {"inline_keyboard": [[_boton("♻️ Sí, reiniciar", f"adm1:rs:{args[0]}:{_minuto()}"),
                                              _boton("No", f"adm1:fi:{args[0]}")]]})
                return "confirmar_reinicio", "Confirma"
            if len(args) < 2 or not _vigente(args[1]):
                _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado.", _teclado_de_ficha(perfil))
                return "caducado", "Caducado"
            copia, quitado = perfiles.copia_y_borrado(perfil["chat_id"], reiniciar=True)
            logger.info("Perfil reiniciado desde /perfiles: %s (copia en data/borrados/%s).",
                        ", ".join(f"{v} {k}" for k, v in quitado.items()), copia.name)
            # Aquí sí se le escribe, al revés que al borrarlo: tiene que hacer el cuestionario, y si no se
            # le dice se queda sin avisos sin saber por qué.
            suyo = Chat(perfil["chat_id"], chat.config)
            avisado = suyo.decir("♻️ <b>Quien administra el bot ha reiniciado tu perfil.</b> Se ha borrado lo que "
                                 "tenías (tus respuestas, tus ofertas, lo que marcaste y lo aprendido) y hay que hacer "
                                 "el cuestionario otra vez: son 3-4 minutos. Hasta entonces no te llegan avisos.")
            encuesta.iniciar(suyo)
            _editar(token, chat_id, mensaje_id,
                    f"♻️ Perfil {numero} reiniciado: está otra vez en el cuestionario"
                    + ("" if avisado else " (no he podido avisarle: lo verá cuando escriba /start)")
                    + f". Copia en data/borrados/{copia.name}.",
                    {"inline_keyboard": [[_boton("↩️ Lista", "adm1:li")]]})
            return "reiniciado", "Reiniciado"
        _editar(token, chat_id, mensaje_id, ficha(perfil, chat.config, numero, es_el_titular), _teclado_de_ficha(perfil))
        return {"fi": "ficha", "pa": "pausado", "re": "reanudado"}.get(accion, "ficha"), ""
    if accion == "fr" and args and args[0].isdigit() and int(args[0]) in HORAS_DE_FRECUENCIA:
        escrito = perfiles.escribir_claves_de_config({"busqueda_automatica_horas": int(args[0])})
        chat.config = {**chat.config, "busqueda_automatica_horas": int(args[0])}
        _editar(token, chat_id, mensaje_id, *_texto_de_frecuencia(chat.config))
        logger.info("Frecuencia cambiada a %s h desde Telegram%s.", args[0], "" if escrito else " (ya lo estaba)")
        return "frecuencia", f"Cada {args[0]} h"
    if accion == "fa" and args and args[0] in ("0", "1"):
        perfiles.escribir_claves_de_config({"busqueda_automatica_activa": args[0] == "1"})
        chat.config = {**chat.config, "busqueda_automatica_activa": args[0] == "1"}
        _editar(token, chat_id, mensaje_id, *_texto_de_frecuencia(chat.config))
        logger.info("Búsqueda automática %s desde Telegram.", "encendida" if args[0] == "1" else "apagada")
        return "automatica", "Encendida" if args[0] == "1" else "Apagada"
    if accion in ("fu", "fs") and args:
        nombre = next((f for f in salud.fuentes() if _ref(f) == args[0]), None)
        if nombre is None or not salud.fuentes()[nombre].get("desactivada"):
            _editar(token, chat_id, mensaje_id, *_texto_de_fuentes(chat.config))
            return "ya_activa", "Ya está activa"
        if accion == "fu":
            _editar(token, chat_id, mensaje_id,
                    f"¿Reactivar <b>{_esc(nombre)}</b>? Se consultará en la próxima corrida. Si vuelve a bloquear, "
                    "se desactiva sola otra vez y no se insiste.",
                    {"inline_keyboard": [[_boton("Sí, reactivar", f"adm1:fs:{args[0]}:{_minuto()}"),
                                          _boton("No", "adm1:fn")]]})
            return "confirmar_fuente", "Confirma"
        if len(args) < 2 or not _vigente(args[1]):
            _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado. /fuentes")
            return "caducado", "Caducado"
        salud.reactivar(nombre)
        _editar(token, chat_id, mensaje_id, *_texto_de_fuentes(chat.config))
        return "fuente_reactivada", f"{nombre} reactivada"
    if accion == "fn":
        _editar(token, chat_id, mensaje_id, *_texto_de_fuentes(chat.config))
        return "cancelado", "Cancelado"
    return "desconocido", "Botón desconocido."


# ----------------------------------------------------------------------
# Al arrancar: quién es el bot y su menú
# ----------------------------------------------------------------------
def _nombre_del_bot(token: str) -> str | None:
    """El @usuario del bot, para el enlace de invitación. Se pregunta una vez por proceso."""
    if _BOT["nombre"] is None and token:
        respuesta = notificaciones.llamar(token, "getMe", {})
        if respuesta.ok and isinstance(respuesta.resultado, dict):
            _BOT["nombre"] = respuesta.resultado.get("username")
    return _BOT["nombre"]


def cuerpos_de_comandos(principal: str) -> list[dict]:
    cuerpos = [{"commands": [{"command": c, "description": d} for c, d in COMANDOS_DE_TODOS]}]
    if principal:
        cuerpos.append({"commands": [{"command": c, "description": d} for c, d in COMANDOS_DEL_TITULAR],
                        "scope": {"type": "chat", "chat_id": int(principal)}})
    return cuerpos


def registrar_comandos(config: dict) -> bool:
    """
    setMyCommands: el menú general y, con `scope` de chat, el del titular. Solo si ha cambiado desde
    la última vez (huella en `meta`): no hay por qué repetirlo en cada arranque. Devuelve si lo ha
    registrado; si Telegram falla no se guarda la huella y se intenta en el siguiente arranque.
    """
    token = (config.get("telegram_bot_token") or "").strip()
    principal = str(config.get("telegram_chat_id") or "").strip()
    cuerpos = cuerpos_de_comandos(principal)
    huella = hashlib.sha256(json.dumps(cuerpos, sort_keys=True).encode("utf-8")).hexdigest()
    if not token or perfiles.meta_get("huella_comandos") == huella:
        return False
    for cuerpo in cuerpos:
        respuesta = notificaciones.llamar(token, "setMyCommands", cuerpo)
        if not respuesta.ok:
            logger.warning("setMyCommands respondió %s: %s", respuesta.codigo, respuesta.descripcion[:200])
            return False
    perfiles.meta_set("huella_comandos", huella)
    logger.info("Menú de comandos registrado en Telegram (general y del titular).")
    return True


# ----------------------------------------------------------------------
# El hilo que escucha
# ----------------------------------------------------------------------
def escuchar(cargar_config, parar=lambda: False) -> None:
    """
    Bucle de long polling. Nunca se cae: cualquier fallo se registra sin el token, espera y vuelve
    a intentarlo, con espera creciente si se repite. Sin token duerme y vuelve a mirar (así
    ponerlo no exige reiniciar).

    Hasta MP5 este bucle estaba en telegram_botones.py y solo pedía pulsaciones; `telegram_botones`
    en config.json lo apagaba entero. Ya no: esa clave quita los botones de los avisos, pero el bot
    sigue escuchando, porque ahora es también por donde se administra.
    """
    desplazamiento = None
    espera = 5
    preparado = False
    while not parar():
        config = cargar_config()
        token = (config.get("telegram_bot_token") or "").strip()
        if not token:
            time.sleep(60)
            continue
        if not preparado:
            # Una vez por proceso (si Telegram falla, en el siguiente arranque): nada de esto puede
            # impedir que el bot escuche.
            preparado = True
            try:
                registrar_comandos(config)
                _nombre_del_bot(token)
            except Exception:
                logger.error("No se ha podido preparar el menú del bot: %s",
                             notificaciones.tachar_token(traceback.format_exc(limit=3), token))
        cuerpo = {"timeout": ESPERA_LARGA_SEGUNDOS, "allowed_updates": ["message", "callback_query"]}
        if desplazamiento is not None:
            cuerpo["offset"] = desplazamiento
        respuesta = notificaciones.llamar(token, "getUpdates", cuerpo, timeout=ESPERA_LARGA_SEGUNDOS + 15)
        if not respuesta.ok:
            # 409: otro programa lee este bot (o alguien le ha puesto un webhook).
            logger.warning("Telegram getUpdates %s: %s", respuesta.codigo or "sin respuesta",
                           respuesta.descripcion[:200])
            time.sleep(espera)
            espera = min(espera * 2, 300)
            continue
        espera = 5
        for update in respuesta.resultado or []:
            desplazamiento = update["update_id"] + 1
            try:
                procesar(update, config)
            except Exception:
                # Sin logger.exception a secas: una traza puede llevar la URL con el token.
                logger.error("No se ha podido atender una actualización de Telegram: %s",
                             notificaciones.tachar_token(traceback.format_exc(), token))
