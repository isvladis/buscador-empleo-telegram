"""
Aviso a Telegram cuando una búsqueda automática trae ofertas de «Encaje alto».

Un bot de Telegram no necesita librería aparte: crearlo con @BotFather da un token, y
mandar un mensaje es una petición HTTP normal a api.telegram.org (ya se usa `requests`
en el proyecto para InfoJobs, así que no añade ninguna dependencia nueva).

Desde MP5 es además EL emisor: toda llamada a la API, sea un aviso, la respuesta a un comando o
la edición de un botón, pasa por `llamar`, que es donde se cumplen el ritmo de un mensaje por
segundo, el `retry_after` de un 429 y el «bloqueado» de un 403.
"""

from __future__ import annotations

import contextlib
import html
import json
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metodo}"

# Un mensaje por oferta (2026-10-25). Antes era UN mensaje con hasta 10 ofertas y un
# «… y N más» al final: las ofertas a partir de la décima no se enviaban nunca y
# tampoco volvían, porque en la corrida siguiente ya no eran nuevas. Con un mensaje por
# oferta se puede abrir, leer y archivar cada una por separado, y ninguna se pierde: lo
# que pase del tope va a un resumen final en vez de desaparecer.
MAX_AVISOS_INDIVIDUALES = 10

# Límite duro de la API de Telegram. Un mensaje más largo se rechaza con 400 y se
# perdería ENTERO, así que los mensajes largos se trocean por líneas.
MAX_CARACTERES_TELEGRAM = 4096

# Ritmo de envío: como mucho un mensaje por segundo, en total y por tanto también por chat (MP5).
# Es lo que pide la FAQ de bots para un mismo chat («avoid sending more than one message per
# second»); para el total admite unos 30 por segundo, pero esto no es urgente y no hay ninguna
# razón para ir al límite de nadie. Lo aplica `llamar`, y no cada quien que envía: desde MP5
# mandan a la vez los avisos de una corrida, las respuestas a comandos y las ediciones de botones.
PAUSA_ENTRE_MENSAJES_SEGUNDOS = 1.0

# Los métodos que son un mensaje o lo cambian. getUpdates, answerCallbackQuery, getMe y
# setMyCommands no publican nada en ningún chat y no cuentan para el límite.
METODOS_CON_RITMO = frozenset({"sendMessage", "editMessageText", "editMessageReplyMarkup",
                               "sendDocument"})
# Un 429 trae en `parameters.retry_after` cuánto esperar. Se respeta, con un techo por si llegara
# un número absurdo: más de cinco minutos parado el emisor ya es otra avería, no un 429.
ESPERA_MAXIMA_429_SEGUNDOS = 300
# Lo que Telegram contesta con 403 cuando la persona ya no quiere mensajes. Solo estos dos: un 403
# por otra causa (un grupo del que echaron al bot, por ejemplo) no dice nada de una persona.
BLOQUEOS_DE_LA_PERSONA = ("blocked by the user", "user is deactivated")

_candado_envio = threading.Lock()
_ultimo_envio = [float("-inf")]

TIMEOUT_SEGUNDOS = 20
# Un solo reintento: en pruebas reales la primera petición HTTPS tras arrancar el
# contenedor (o tras estar un rato sin salir a internet) a veces se queda esperando
# respuesta más de lo normal y expira el timeout, aunque la conexión en sí funciona
# bien (la siguiente petición, sin cambiar nada, responde en milisegundos). Esto no es
# una búsqueda manual con alguien delante esperando: unos segundos más no cuestan nada,
# así que merece la pena reintentar una vez antes de darlo por fallido.
REINTENTOS = 1
ESPERA_ENTRE_REINTENTOS_SEGUNDOS = 3


def _esc(valor) -> str:
    """Escapa para parse_mode=HTML. Telegram solo necesita &, < y >."""
    return html.escape("" if valor is None else str(valor), quote=False)


def _num(valor) -> str:
    """Un decimal, con coma: el mensaje está en español."""
    return f"{valor:.1f}".replace(".", ",")


def _detalle(oferta: dict) -> str:
    """
    Segunda línea de cada oferta: dónde, modalidad, encaje y experiencia exigida. Cada
    dato solo aparece si se tiene (las notificaciones antiguas o las pruebas pueden no
    traerlo), así que la línea puede quedarse vacía y entonces no se pinta.
    """
    partes = []
    if oferta.get("location"):
        partes.append(f"📍 {_esc(oferta['location'])}")
    distancia = oferta.get("distancia_km")
    if isinstance(distancia, (int, float)):
        partes.append(f"{_num(distancia)} km")
    if oferta.get("modalidad_label") and oferta["modalidad_label"] != "No indicado":
        partes.append(_esc(oferta["modalidad_label"]))
    if oferta.get("relevancia") is not None:
        partes.append(f"encaje <b>{_num(oferta['relevancia'])}</b>")
    anos = oferta.get("anos_experiencia")
    # Si los años ya salen como alerta («pide 4 años (tu tope: 3)»), no se repiten aquí.
    ya_en_alertas = any(str(a).startswith("pide ") for a in oferta.get("alertas") or [])
    if anos == 0:
        partes.append("sin experiencia")
    elif anos is not None and not ya_en_alertas:
        partes.append(f"pide {anos} año{'s' if anos != 1 else ''}")
    return " · ".join(partes)


def _competencia(oferta: dict) -> str | None:
    """Cuánta gente ha aplicado ya, si el portal lo dijo al descubrirla."""
    candidatos = oferta.get("candidatos")
    if not isinstance(candidatos, (int, float)):
        return None
    texto = f"👥 {int(candidatos)}"
    if oferta.get("candidatos_tope"):
        texto += "+"
    texto += " candidatos"
    if oferta.get("publicada_en"):
        texto += f" · publicada {_esc(oferta['publicada_en'])}"
    return texto


def _mensaje_de_una_oferta(oferta: dict) -> str:
    """
    El aviso de una sola oferta, en HTML. Lleva el enlace en el título para que se pueda
    abrir con un toque, y debajo solo lo que ayuda a decidir si merece el clic.
    """
    titulo = _esc(oferta.get("title") or "Sin título")
    url = oferta.get("job_url") or ""
    cabecera = f'🎯 <a href="{_esc(url)}"><b>{titulo}</b></a>' if url else f"🎯 <b>{titulo}</b>"
    lineas = [cabecera]
    if oferta.get("company"):
        empresa = _esc(oferta["company"])
        if oferta.get("site"):
            empresa += f" · {_esc(oferta['site'])}"
        lineas.append(empresa)
    detalle = _detalle(oferta)
    if detalle:
        lineas.append(detalle)
    if oferta.get("motivos"):
        # Los tres motivos que más pesan en la puntuación, para saber por qué ha avisado.
        lineas.append(f"🧮 {_esc(oferta['motivos'])}")
    if oferta.get("salario"):
        lineas.append(f"💰 {_esc(oferta['salario'])}")
    competencia = _competencia(oferta)
    if competencia:
        lineas.append(competencia)
    if oferta.get("turnos"):
        lineas.append(f"🕑 {_esc(oferta['turnos'])}")
    for alerta in oferta.get("alertas") or []:
        lineas.append(f"⚠️ {_esc(alerta)}")
    return "\n".join(lineas)


def _mensaje_resumen(ofertas: list[dict]) -> str:
    """
    Las que pasan del tope de avisos individuales. No se pierden: van aquí, con su
    enlace, en vez del «… y N más» de antes, que las dejaba fuera para siempre.
    """
    lineas = [f"➕ Y {len(ofertas)} más de Encaje alto, por si acaso:"]
    for oferta in ofertas:
        titulo = _esc(oferta.get("title") or "Sin título")
        url = oferta.get("job_url") or ""
        encaje = oferta.get("relevancia")
        marca = f" ({_num(encaje)})" if isinstance(encaje, (int, float)) else ""
        enlace = f'<a href="{_esc(url)}">{titulo}</a>' if url else titulo
        empresa = f" — {_esc(oferta['company'])}" if oferta.get("company") else ""
        lineas.append(f"• {enlace}{empresa}{marca}")
    return "\n".join(lineas)


def _trocear(texto: str, limite: int = MAX_CARACTERES_TELEGRAM) -> list[str]:
    """
    Parte un mensaje largo en trozos que quepan, cortando por líneas enteras.

    Hace falta porque Telegram rechaza con 400 cualquier mensaje de más de 4.096
    caracteres, y el mensaje se perdía ENTERO: no había troceado ni reintento por
    partes. Una línea que ella sola pase del límite (un título absurdo) se corta por
    donde sea, que es mejor que no enviar nada.
    """
    if len(texto) <= limite:
        return [texto]
    trozos: list[str] = []
    actual = ""
    for linea in texto.split("\n"):
        while len(linea) > limite:
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(linea[:limite])
            linea = linea[limite:]
        if not actual:
            actual = linea
        elif len(actual) + 1 + len(linea) <= limite:
            actual += "\n" + linea
        else:
            trozos.append(actual)
            actual = linea
    if actual:
        trozos.append(actual)
    return trozos


def _mensaje_incidencias(incidencias: list[dict]) -> str:
    """
    Aviso de fuentes con problemas. Hay dos clases, y el mensaje no las mezcla porque lo
    que hay que hacer con cada una es distinto: una fuente desactivada no vuelve hasta
    que alguien la reactive a mano; una que solo avisa (LinkedIn, Indeed...) sigue
    consultándose y a lo mejor se arregla sola (ver salud.FUENTES_QUE_AVISAN_SIN_DESACTIVARSE).
    """
    # TODO lo que viene de fuera se escapa: el motivo de un bloqueo lleva el HTML crudo de
    # la página que bloqueó («<!DOCTYPE HTML PUBLIC…» de EURES), y con parse_mode=HTML
    # Telegram rechazaría el mensaje con un 400. Como la cola solo se vacía si el envío
    # funciona, el aviso de una fuente caída no llegaría NUNCA. (Lo di por arreglado en la
    # Fase 2 y no lo estaba: el cambio no se aplicó. Ahora lo cubre una prueba.)
    def linea(i):
        return f"\n• <b>{_esc(i.get('fuente'))}</b>\n  {_esc(i.get('motivo'))}"

    # Las que no traen "tipo" son bloqueos; las que tampoco traen "desactivada" son de
    # antes de que existiera la clave, y entonces eran siempre desactivaciones.
    vacias = [i for i in incidencias if i.get("tipo") == "vacia"]
    recuperadas = [i for i in incidencias if i.get("tipo") == "recuperada"]
    presupuesto = [i for i in incidencias if i.get("tipo") == "presupuesto"]
    sistema = [i for i in incidencias if i.get("tipo") == "sistema"]
    operacion = [i for i in incidencias if i.get("tipo") == "operacion"]
    bloqueos = [i for i in incidencias
                if i.get("tipo") not in ("vacia", "recuperada", "presupuesto", "sistema", "operacion")]
    desactivadas = [i for i in bloqueos if i.get("desactivada", True)]
    solo_avisan = [i for i in bloqueos if not i.get("desactivada", True)]

    bloques = []
    # La primera: si esto pasa no hay corridas ni botones hasta que alguien lo arregle a mano,
    # así que no puede quedar debajo de un aviso de cupo.
    if sistema:
        bloques.append("\n".join(
            ["🛑 El sistema está PARADO:"] + [linea(i) for i in sistema]
            + ["\nNo se consulta ningún portal y los botones no marcan nada hasta que se "
               "resuelva a mano. Pasos en DESPLIEGUE.md, «Si perfiles.db falla»."]))
    if desactivadas:
        bloques.append("\n".join(
            ["⚠️ Fuente(s) desactivada(s) automáticamente por bloqueo:"]
            + [linea(i) for i in desactivadas]
            + ["\nNo se volverán a consultar. Para reactivarlas, pon \"desactivada\": false "
               "en salud_fuentes.json."]))
    if solo_avisan:
        bloques.append("\n".join(
            ["⚠️ Fuente(s) que están bloqueando pero siguen activas:"]
            + [linea(i) for i in solo_avisan]
            + ["\nSe siguen consultando: un 429 suele levantarse en un minuto. Si el aviso "
               "se repite, hay que bajar el ritmo (menos términos por corrida o más horas "
               "entre ellas)."]))
    if vacias:
        bloques.append("\n".join(
            ["🕳️ Fuente(s) que responden pero no traen NINGUNA oferta:"]
            + [linea(i) for i in vacias]
            + ["\nNo es un bloqueo: el portal contesta bien. Casi seguro ha cambiado su "
               "página y el lector ya no la entiende, así que hay que revisarlo en el código. "
               "Mientras tanto se sigue consultando; si vuelve a traer ofertas, se avisa."]))
    if recuperadas:
        bloques.append("\n".join(
            ["✅ Fuente(s) que vuelven a funcionar:"] + [linea(i) for i in recuperadas]))
    if presupuesto:
        bloques.append("\n".join(
            ["🎟️ Cupo de peticiones de una API:"] + [linea(i) for i in presupuesto]))
    if operacion:
        bloques.append("\n".join(
            ["⚙️ Operación del sistema:"] + [linea(i) for i in operacion]))
    return "\n\n".join(bloques)


def _chat_ids(config: dict) -> list[str]:
    """
    Devuelve todos los chat_id configurados: el principal (`telegram_chat_id`, el
    campo histórico, sigue funcionando igual) más los adicionales, si los hay.

    Desde MP3 esto ya no es la lista de destinatarios: cada chat recibe solo lo suyo. Queda
    para decir cuántos hay configurados, en la web.

    `telegram_chat_ids_adicionales` acepta una lista o una cadena con varios chat_id
    separados por comas (más cómodo de teclear en un único campo del panel web).
    """
    ids: list[str] = []
    principal = (config.get("telegram_chat_id") or "").strip()
    if principal:
        ids.append(principal)

    adicionales = config.get("telegram_chat_ids_adicionales") or []
    if isinstance(adicionales, str):
        adicionales = adicionales.split(",")
    for chat_id in adicionales:
        chat_id = (chat_id or "").strip()
        if chat_id and chat_id not in ids:
            ids.append(chat_id)

    return ids


def _principal(config: dict) -> list[str]:
    principal = str(config.get("telegram_chat_id") or "").strip()
    return [principal] if principal else []


def tachar_token(texto, token: str) -> str:
    """El token va en la URL de la API, y los errores de requests llevan la URL entera
    («Max retries exceeded with url: /bot<token>/sendMessage»). Todo lo que se loguee de
    un fallo pasa por aquí. (Corregido en la Fase 5: antes el error se logueaba tal cual.)"""
    texto = str(texto)
    return texto.replace(token, "<token>") if token else texto


class Respuesta:
    """Lo que ha contestado Telegram, ya leído. `codigo` es None si no llegó a contestar."""

    def __init__(self, ok: bool, codigo: int | None, datos: dict | None = None, descripcion: str = ""):
        self.ok, self.codigo, self.datos, self.descripcion = ok, codigo, datos or {}, descripcion

    @property
    def resultado(self):
        return self.datos.get("result")


def _leer_respuesta(respuesta) -> tuple[dict, str]:
    """El JSON de la respuesta y su texto, sin fallar si no es JSON (un proxy, un 502)."""
    texto = getattr(respuesta, "text", "") or ""
    try:
        datos = respuesta.json() if hasattr(respuesta, "json") else json.loads(texto or "{}")
    except ValueError:
        datos = {}
    return (datos if isinstance(datos, dict) else {}), texto


def _esperar_turno() -> None:
    """Deja pasar PAUSA_ENTRE_MENSAJES_SEGUNDOS desde el último envío. Se llama con el candado."""
    falta = PAUSA_ENTRE_MENSAJES_SEGUNDOS - (time.monotonic() - _ultimo_envio[0])
    if falta > 0:
        time.sleep(falta)


def _al_bloquear(chat_id) -> None:
    """403 de «blocked by the user»: ese perfil no vuelve a recibir nada hasta un /start."""
    try:
        import perfiles  # aquí y no arriba: perfiles importa este módulo

        if perfiles.bloquear(int(chat_id)):
            logger.warning("Un perfil ha bloqueado el bot: queda «bloqueado», sin consultas ni avisos.")
        else:
            logger.warning("Telegram dice que el titular ha bloqueado el bot. No se le marca: sin él no "
                           "hay administración.")
    except Exception:
        logger.exception("No se ha podido marcar como bloqueado el perfil que ha bloqueado el bot")


def llamar(token: str, metodo: str, cuerpo: dict | None = None, timeout: float = TIMEOUT_SEGUNDOS,
           archivos: dict | None = None) -> Respuesta:
    """
    EL emisor (MP5): toda llamada a la API de Telegram pasa por aquí, así que aquí se cumplen sus
    límites una sola vez para todos.

    - Los métodos de METODOS_CON_RITMO van de uno en uno y con PAUSA_ENTRE_MENSAJES_SEGUNDOS entre
      dos: lo mismo si el que envía es una corrida, una respuesta a /estado o un botón. El candado
      se suelta solo al acabar, así que dos hilos no se cuelan a la vez.
    - 429: se espera lo que diga `retry_after` y se repite una vez. Sin respetarlo, Telegram alarga
      el castigo.
    - 403 de una persona que ha bloqueado el bot: su perfil pasa a `bloqueado` (perfiles.bloquear).

    No lanza excepciones de red: devuelve `Respuesta(ok=False, codigo=None)` y quien llama decide si
    reintenta (`_enviar_a_uno` lo hace una vez). Nada de lo que se loguea aquí lleva el token.
    `archivos` va como multipart (sendDocument) y entonces `cuerpo` son los campos del formulario.
    """
    url = API.format(token=token, metodo=metodo)
    con_ritmo = metodo in METODOS_CON_RITMO
    with _candado_envio if con_ritmo else contextlib.nullcontext():
        for intento in (1, 2):
            if con_ritmo:
                _esperar_turno()
            try:
                if archivos:
                    r = requests.post(url, data=cuerpo or {}, files=archivos, timeout=timeout)
                else:
                    r = requests.post(url, json=cuerpo or {}, timeout=timeout)
            except requests.RequestException as e:
                return Respuesta(False, None, descripcion=tachar_token(e, token))
            finally:
                if con_ritmo:
                    _ultimo_envio[0] = time.monotonic()
            datos, texto = _leer_respuesta(r)
            codigo = getattr(r, "status_code", None)
            ok = bool(getattr(r, "ok", False)) and datos.get("ok", True) is not False
            descripcion = tachar_token(datos.get("description") or texto[:300], token)
            if codigo == 429 and intento == 1:
                espera = (datos.get("parameters") or {}).get("retry_after")
                espera = min(float(espera), ESPERA_MAXIMA_429_SEGUNDOS) if isinstance(espera, (int, float)) else 5.0
                logger.warning("Telegram pide esperar %g s (429) en %s: se espera y se repite.", espera, metodo)
                time.sleep(espera)
                continue
            if (codigo == 403 and cuerpo and cuerpo.get("chat_id") is not None
                    and any(b in descripcion.lower() for b in BLOQUEOS_DE_LA_PERSONA)):
                _al_bloquear(cuerpo["chat_id"])
            return Respuesta(ok, codigo, datos, descripcion)
    return Respuesta(False, 429, descripcion="429 dos veces seguidas")


def _enviar_a_uno(token: str, chat_id: str, texto: str, que_es: str,
                  teclado: dict | None = None, destinatario: str = "1/1") -> bool:
    """
    Manda un mensaje a un chat_id concreto. Devuelve si se ha llegado a enviar.

    `destinatario` es la POSICIÓN en la lista de destinatarios («2/3»), y es lo único que
    va al log: el chat_id es el identificador personal de quien recibe el aviso y la
    regla 3 dice que no se escribe en ningún sitio. Hasta el 31-10-2026 se logueaba el
    número en siete líneas, y la de «enviada correctamente» es INFO, así que salía en
    cada aviso de cada corrida; con varios perfiles (MP3) el log tendría el número de
    cada persona. La posición basta para seguir un envío en el log, que es para lo que se
    usaba. Ver §7, punto 6 de CAMBIOS-MP1.md.
    """
    # El token nunca se loguea entero (es la credencial completa del bot); enmascarado
    # basta para confirmar en el log que se está usando el que el usuario puso en el
    # panel y no uno vacío o de una prueba anterior.
    token_enmascarado = f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "***"
    logger.info(
        "Llamando a la API de Telegram: POST /bot%s/sendMessage, destinatario=%s (%s).",
        token_enmascarado, destinatario, que_es,
    )
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "disable_web_page_preview": True,
        # HTML y no Markdown: los títulos de las ofertas vienen con guiones, asteriscos,
        # barras y paréntesis («Técnico/a Soporte N1 (24x7) | Barcelona»), que en Markdown
        # hay que escapar uno a uno y a la primera que se escape mal Telegram devuelve 400
        # y el aviso se pierde. En HTML basta con escapar &, < y > (ver _esc).
        "parse_mode": "HTML",
    }
    if teclado:
        payload["reply_markup"] = teclado

    for intento in range(1, REINTENTOS + 2):
        # Por el emisor: ritmo, 429 y 403 son cosa suya (ver `llamar`). Aquí solo el reintento
        # de un fallo de red, que es lo que sí depende de quién envía.
        respuesta = llamar(token, "sendMessage", payload)
        if respuesta.codigo is None:
            if intento <= REINTENTOS:
                logger.warning(
                    "Intento %s/%s de notificar a Telegram (destinatario=%s) ha fallado (%s); "
                    "reintentando...", intento, REINTENTOS + 1, destinatario, respuesta.descripcion,
                )
                time.sleep(ESPERA_ENTRE_REINTENTOS_SEGUNDOS)
                continue
            # La descripción ya viene sin el token (llamar la pasa por tachar_token).
            logger.error(
                "No se ha podido enviar la notificación a Telegram (destinatario=%s): %s",
                destinatario, respuesta.descripcion,
            )
            return False

        logger.info(
            "Telegram respondió %s (destinatario=%s, intento %s).",
            respuesta.codigo, destinatario, intento,
        )
        if not respuesta.ok:
            logger.warning(
                "Telegram respondió %s al notificar al destinatario %s: %s",
                respuesta.codigo, destinatario, respuesta.descripcion,
            )
            return False

        logger.info("Notificación enviada a Telegram correctamente (destinatario=%s).",
                    destinatario)
        return True

    return False


def _enviar(config: dict, texto: str, que_es: str, teclado: dict | None = None,
            chat_ids: list | None = None, posicion: str | None = None) -> bool:
    """
    Manda un mensaje a esos chat_id, y sin decir a quién, solo al titular.

    Hasta MP3, sin decir a quién iba a todos los configurados: los adicionales recibían una copia
    de los avisos y también las incidencias de las fuentes. Ya no: cada chat recibe solo lo suyo,
    y lo que no es de nadie (operación, ver notificar_incidencias_fuentes) es del titular.

    Cada destinatario es independiente: que uno falle (por ejemplo, porque nunca
    inició conversación con el bot y Telegram rechaza el envío) no impide que llegue
    al resto. Devuelve True si se ha llegado a enviar a AL MENOS uno.

    `teclado` va al primer destinatario y en el último trozo. `posicion` es lo que sale en
    el log cuando el destinatario es un perfil de la corrida (ver _enviar_a_uno).
    """
    token = (config.get("telegram_bot_token") or "").strip()
    chat_ids = [str(c) for c in chat_ids] if chat_ids else _principal(config)
    if not token or not chat_ids:
        logger.info(
            "Notificación a Telegram omitida (%s): falta configurar "
            "JOBSPY_TELEGRAM_TOKEN y JOBSPY_TELEGRAM_CHAT_ID en el .env del servidor "
            "(ver .env.example).", que_es,
        )
        return False

    # Troceado antes de enviar: un mensaje de más de 4.096 caracteres lo rechaza
    # Telegram con un 400 y se perdería entero.
    trozos = _trocear(texto)
    if len(trozos) > 1:
        logger.info("El mensaje (%s) ocupa %s caracteres: se parte en %s trozos.",
                    que_es, len(texto), len(trozos))
    resultados = []
    # La posición en la lista («2/3») es lo que se loguea en vez del chat_id: ver
    # _enviar_a_uno. Como el orden lo fija quien llama (el titular primero), basta para
    # saber a quién se refiere cada línea sin escribir el número de nadie.
    for n, chat_id in enumerate(chat_ids, 1):
        destinatario = posicion or f"{n}/{len(chat_ids)}"
        enviados = []
        for i, trozo in enumerate(trozos):
            # La pausa entre trozos la pone el emisor (ver `llamar`).
            con_botones = teclado if (chat_id == chat_ids[0] and i == len(trozos) - 1) else None
            enviados.append(
                _enviar_a_uno(token, chat_id, trozo, que_es, con_botones, destinatario))
        # Un trozo perdido no invalida el resto, pero sí se quiere saber: si alguno
        # falla, este destinatario no ha recibido el mensaje completo.
        if not all(enviados):
            logger.warning("Al destinatario %s le han llegado %s de %s trozos (%s).",
                           destinatario, sum(enviados), len(trozos), que_es)
        resultados.append(any(enviados))
    if len(chat_ids) > 1:
        logger.info(
            "Notificación a Telegram (%s): %s/%s destinatario(s) recibieron el mensaje.",
            que_es, sum(resultados), len(chat_ids),
        )
    return any(resultados)


def notificar_incidencias_fuentes(config: dict, incidencias: list[dict]) -> bool:
    """
    Avisa de que una fuente se ha desactivado sola por empezar a devolver bloqueos.

    Va aparte de las ofertas y se manda también en las búsquedas MANUALES, al revés que
    el aviso de Encaje alto: que una fuente deje de funcionar es justo lo que no se ve
    mirando el listado (sigue habiendo ofertas, solo que menos), así que enterarse solo
    en las automáticas dejaría el fallo escondido durante horas.
    """
    if not incidencias:
        return False
    return _enviar(config, _mensaje_incidencias(incidencias),
                   f"{len(incidencias)} incidencia(s) de fuentes")


def enviar_a_un_perfil(config: dict, chat_id: int, texto: str, que_es: str) -> bool:
    return _enviar(config, texto, que_es, chat_ids=[chat_id], posicion="1/1")


def _bloque_de_resumen(numero: int, oferta: dict) -> str:
    """
    Una oferta dentro de un resumen (MP9): tres líneas como mucho, porque son hasta 10 en un mensaje.
    Título con enlace, empresa y portal; el detalle de siempre (dónde, distancia, modalidad, encaje,
    años); y las alertas juntas. Los motivos de la puntuación, el sueldo y los candidatos se quedan en
    el aviso suelto: aquí lo que importa es decidir a cuál tocar. Los campos se acortan para que 10
    bloques quepan siempre en los 4.096 caracteres, aunque el título sea absurdo.
    """
    titulo = _esc((oferta.get("title") or "Sin título")[:120])
    url = oferta.get("job_url") or ""
    cabeza = f'{numero}. <a href="{_esc(url)}"><b>{titulo}</b></a>' if url else f"{numero}. <b>{titulo}</b>"
    if oferta.get("company"):
        cabeza += f" — {_esc(str(oferta['company'])[:60])}"
        if oferta.get("site"):
            cabeza += f" · {_esc(oferta['site'])}"
    lineas = [cabeza]
    detalle = _detalle(oferta)
    if detalle:
        lineas.append("   " + detalle)
    alertas = "; ".join(str(a) for a in oferta.get("alertas") or [])
    if alertas:
        lineas.append(f"   ⚠️ {_esc(alertas[:160])}")
    return "\n".join(lineas)


def mensajes_de_resumen(ofertas: list[dict], cabecera: str, restantes: int = 0) -> list[tuple[str, int, int]]:
    """
    El resumen de MP9 troceado: [(texto, desde, hasta)], donde las ofertas de ese mensaje son
    `ofertas[desde:hasta]`. Cada mensaje lleva SUS ofertas enteras y, en el teclado, sus botones
    (ver `notificar_resumen`): trocear el texto por líneas con `_trocear` habría dejado una oferta
    partida entre dos mensajes y los botones solo en el último. Con los campos acortados 10 ofertas
    caben en uno (medido en las pruebas); si alguna vez no, se reparten en varios y cada uno vale solo.
    `_trocear` queda de red de seguridad en `_enviar`.
    """
    pie = (f"\n➕ {restantes} más te {'espera' if restantes == 1 else 'esperan'} para el próximo envío."
           if restantes > 0 else "")
    cabeza = f"{cabecera} · {len(ofertas)} oferta{'s' if len(ofertas) != 1 else ''}\n"
    mensajes, actual, desde = [], cabeza, 0
    for i, oferta in enumerate(ofertas):
        bloque = _bloque_de_resumen(i + 1, oferta)
        if i > desde and len(actual) + 1 + len(bloque) + len(pie) > MAX_CARACTERES_TELEGRAM:
            mensajes.append((actual, desde, i))
            actual, desde = "", i
        actual += ("\n" if actual else "") + bloque
    mensajes.append((actual + pie, desde, len(ofertas)))
    return mensajes


def notificar_resumen(config: dict, chat_id: int, ofertas: list[dict], cabecera: str, restantes: int = 0,
                      posicion: str | None = None, avisada: str | None = None) -> list[int]:
    """
    Manda ese resumen a ese chat y devuelve los índices de las ofertas que han llegado (D10: cada una
    con sus botones). Se apuntan en `avisos` justo antes de enviar su mensaje, como un aviso suelto,
    porque los botones buscan ahí; si el envío de un mensaje falla se deshace el apunte de SUS ofertas
    y no cuenta para el tope. Las que no llegan las sigue teniendo quien llama, en la cola.
    """
    import perfiles
    import telegram_botones

    llegadas: list[int] = []
    for texto, desde, hasta in mensajes_de_resumen(ofertas, cabecera, restantes):
        nuevas, numeradas = [], []
        for i in range(desde, hasta):
            clave = telegram_botones.clave_de(ofertas[i]["job_url"])
            if perfiles.aviso(chat_id, clave) is None:
                nuevas.append(clave)
            telegram_botones.registrar_aviso(ofertas[i], chat_id, avisada=avisada)
            numeradas.append((i + 1, clave))
        teclado = telegram_botones.teclado_de_resumen(numeradas) if config.get("telegram_botones", True) else None
        if _enviar(config, texto, f"resumen de {hasta - desde} ofertas", teclado, [chat_id], posicion):
            llegadas.extend(range(desde, hasta))
        else:
            perfiles.quitar_avisos_sin_responder(chat_id, nuevas)
    logger.info("Resumen: %s de %s ofertas enviadas.", len(llegadas), len(ofertas))
    return llegadas


def notificar_descubrimiento(config: dict, oferta: dict, chat_id: int | None = None,
                             posicion: str | None = None, avisada: str | None = None) -> bool:
    """
    Una oferta del borde (MP6, D10): «🔍 ¿Te encaja?» con [👍] [👎]. No es un aviso: se apunta en
    `avisos` como «descubrimiento», que no cuenta en las cifras de avisos, y sus botones son etiquetas
    para la capa aprendida de ese perfil (aprendizaje.py). Cuenta para el tope diario (MP9).
    """
    import telegram_botones  # aquí y no arriba: telegram_botones importa este módulo

    clave = telegram_botones.registrar_aviso(oferta, chat_id, tipo="descubrimiento", avisada=avisada)
    texto = ("🔍 <b>¿Te encaja?</b> Está justo por debajo de lo que te aviso; tu respuesta me "
             "ayuda a afinar.\n\n" + _mensaje_de_una_oferta(oferta))
    destino = None if chat_id is None else [chat_id]
    return _enviar(config, texto, "oferta de descubrimiento", telegram_botones.teclado_descubrimiento(clave),
                   destino, posicion)


def preparar_aviso(oferta: dict, completa: dict, puntuador, anos_max, umbral: float | None = None) -> dict | None:
    """
    Lo que lleva el aviso de una oferta, puntuada con el puntuador de su perfil. Con `umbral`,
    None si no llega: así el contexto (turnos, alertas) solo se calcula para las que se avisan.

    `oferta` es el registro de la corrida y `completa` el de la base (descripción, modalidad…). La
    usan los avisos de cada corrida (app._notificar_si_alto_encaje) y /top (MP5): las dos tienen
    que decir lo mismo de la misma oferta, y por eso es una sola función. Se sacó tal cual de la
    corrida, que es la que tiene que seguir dando exactamente lo mismo (regla 4).
    """
    import buscador  # aquí y no arriba: buscador arrastra jobspy y pandas

    anos = buscador.extraer_anos_experiencia(completa.get("description"))
    relevancia, motivos = buscador.explicar_relevancia(
        oferta.get("title"), oferta.get("location"), anos, completa.get("description"), puntuador, completa
    )
    if umbral is not None and relevancia < umbral:
        return None
    # Con un mensaje por oferta cabe el contexto que ayuda a decidir si merece
    # el clic: salario, cuánta gente ha aplicado ya, modalidad real, distancia y
    # las alertas (idioma por encima del nivel, años de más, posible trampa).
    turnos = buscador.TURNOS.search(
        f"{oferta.get('title') or ''} {completa.get('description') or ''}"[:4000]
    )
    return {
        **oferta,
        "relevancia": relevancia,
        "motivos": buscador.texto_de_motivos(motivos),
        "anos_experiencia": anos,
        "modalidad_label": completa.get("modalidad_label"),
        "salario": completa.get("salario"),
        "site": completa.get("site"),
        "distancia_km": completa.get("distancia_km"),
        "candidatos": completa.get("candidatos"),
        "candidatos_tope": completa.get("candidatos_tope"),
        "publicada_en": completa.get("publicada_en"),
        "turnos": turnos.group(0) if turnos else None,
        "alertas": buscador.alertas(
            {**completa, "anos_experiencia": anos}, anos_max, puntuador
        ),
    }


def notificar_ofertas_alto_encaje(config: dict, ofertas: list[dict], chat_id: int | None = None,
                                  tope: int | None = None, con_resumen: bool = True,
                                  posicion: str | None = None, avisada: str | None = None,
                                  fallidas: list[dict] | None = None) -> bool:
    """
    Manda a Telegram las ofertas nuevas de Encaje alto: UN MENSAJE POR OFERTA.

    Antes era un único mensaje con hasta 10 ofertas y un «… y N más» al final. Eso tenía
    dos problemas: no se podía archivar ni compartir una oferta suelta, y sobre todo las
    ofertas a partir de la décima NO se enviaban nunca —y tampoco volvían, porque en la
    corrida siguiente ya no eran nuevas—. Ahora las que pasan del tope van en un resumen
    con su enlace: ninguna se pierde.

    Las más relevantes primero, por si el tope se queda corto.

    `chat_id` es el perfil al que van, el titular si no se dice: el aviso se registra en SU
    índice, y los botones solo valen en su chat. `tope` y `con_resumen` son para la primera corrida
    de un perfil, que manda las mejores y deja el resto por vistas (D8).

    Si falta el token o el chat_id no falla: la búsqueda automática no debe romperse
    porque el usuario todavía no haya configurado el bot, así que solo se loguea que la
    notificación se ha omitido. Devuelve si se ha llegado a enviar algo.

    `fallidas`, si se da, recibe las ofertas que NO han llegado, para que quien llama las encole
    (app._encolar_lo_no_entregado). Sin eso se quedaban guardadas y «vistas»: en la corrida
    siguiente ya no eran nuevas y no volvían nunca (P1 de CAMBIOS-MP10 §4).
    """
    if not ofertas:
        return False

    if tope is None:
        tope = int(config.get("telegram_max_avisos_individuales", MAX_AVISOS_INDIVIDUALES))
    ordenadas = sorted(
        ofertas,
        key=lambda o: -(o["relevancia"] if isinstance(o.get("relevancia"), (int, float)) else 0),
    )
    individuales, resto = ordenadas[:max(0, tope)], ordenadas[max(0, tope):]
    if not con_resumen:
        resto = []
    destino = None if chat_id is None else [chat_id]

    import perfiles  # aquí y no arriba, igual que en notificar_resumen
    import telegram_botones  # aquí y no arriba: telegram_botones importa este módulo

    # El perfil se resuelve la primera vez que hay que mirar su índice, no al entrar: con una oferta sin
    # `job_url` no hay nada que apuntar, y abrir perfiles.db de más deja el fichero tomado (lo cazó el
    # bloque `secretos`, que llama aquí con la base en un temporal que borra justo después).
    de_quien = None
    enviados = 0
    # Sin pausas aquí: un mensaje por segundo como mucho lo garantiza el emisor (`llamar`), que es
    # el único que sabe qué más se está enviando a la vez.
    for oferta in individuales:
        teclado, nueva = None, None
        if config.get("telegram_botones", True):
            try:
                if not (oferta.get("job_url") or ""):
                    raise ValueError("oferta sin job_url")  # lo mismo que dice registrar_aviso
                clave = telegram_botones.clave_de(oferta["job_url"])
                de_quien = perfiles.resolver(chat_id) if de_quien is None else de_quien
                ya_estaba = perfiles.aviso(de_quien, clave) is not None
                teclado = telegram_botones.teclado_inicial(
                    telegram_botones.registrar_aviso(oferta, chat_id, avisada=avisada))
                nueva = None if ya_estaba else clave
            except Exception:
                # Sin botones el aviso sigue valiendo; sin aviso, no hay nada.
                logger.exception("No se han podido preparar los botones de la oferta")
        if _enviar(config, _mensaje_de_una_oferta(oferta), "oferta de Encaje alto", teclado,
                   destino, posicion):
            enviados += 1
        else:
            # El apunte se hace ANTES de enviar, porque la clave de los botones va dentro del
            # mensaje. Si el envío falla y el apunte se queda, la oferta cuenta como avisada: «no
            # repetir» la entierra 14 días y en la corrida siguiente ya no es nueva. MP9 lo hacía
            # en sus dos caminos; este, el del titular y de los perfiles sin modo, era el que
            # faltaba (P1), y al traerlo aquí `avisos_perfil._enviar_suelta` ya no lo repite.
            if nueva:
                perfiles.quitar_avisos_sin_responder(de_quien, [nueva])
            if fallidas is not None:
                fallidas.append(oferta)

    if resto:
        if _enviar(config, _mensaje_resumen(resto), f"resumen de {len(resto)} ofertas más",
                   None, destino, posicion):
            enviados += 1
        elif fallidas is not None:
            # Estas no se apuntan nunca (van como una lista de enlaces, sin botones): si el mensaje
            # no llega, encolarlas es lo único que las salva, y volverán como avisos de verdad.
            fallidas.extend(resto)

    logger.info(
        "Avisos de Encaje alto: %s mensaje(s) enviado(s) de %s intento(s) "
        "(%s ofertas individuales%s).",
        enviados, len(individuales) + (1 if resto else 0), len(individuales),
        f" y {len(resto)} en el resumen" if resto else "",
    )
    return enviados > 0
