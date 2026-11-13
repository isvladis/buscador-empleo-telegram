"""
Botones en los avisos de Telegram (Fase 5, 2026-10-26).

Cada aviso individual de Encaje alto lleva tres botones: «⭐ Me interesa», «✅ Aplicada» y
«🙈 No me interesa». Marcan la oferta igual que los botones de la web, con
`"origen": "telegram"` para poder medir después qué pasó con los avisos. Al descartar se
ofrece, opcional, el motivo en un toque: es la etiqueta que más ayuda a afinar las reglas,
y es fiable, a diferencia de las ocultas de la web, que son barridos en lote.

CÓMO LLEGAN LAS PULSACIONES: el Toshiba no tiene una URL pública, así que no hay webhook.
Un hilo hace «long polling» a getUpdates (espera hasta 50 s a que haya algo; no consume
CPU mientras espera). Comprobado el 26-10: el bot no tenía webhook ni actualizaciones
pendientes. Desde MP5 ese hilo es el lector de `telegram_bot.py`, que atiende también los
mensajes y los comandos y le pasa aquí las pulsaciones de las ofertas (`f:`, `a:`, `o:`, `m?:`,
`d:`), que siguen siendo las de siempre.

ETIQUETAS (MP6): cada pulsación es una etiqueta del perfil que pulsa (aprendizaje.py). Por eso, al
marcar, se apuntan en la marca los datos de sus rasgos (fuente, modalidad, años que pide): la poda se
lleva la descripción de las ocultas a los 30 días. Las ofertas de descubrimiento («🔍 ¿Te encaja?»)
llevan 👍 (`s:`) y 👎 (`n:`): 👍 la deja en «Me interesa» y 👎 la descarta, las dos con origen
«descubrimiento», y en `avisos` la respuesta es «encaja» o «no_encaja».

SEGURIDAD (MP3): cada chat con perfil tiene sus botones, y una pulsación solo mira los avisos
y las marcas del chat que pulsa. Un botón que llegue a otro chat no encuentra su aviso y no
marca nada, y un chat sin perfil se rechaza. Lo que marca otra persona es suyo: ni el título de
la oferta va al log que lee el titular (regla 5). El token nunca se loguea (tachar_token).

LO QUE CABE EN UN BOTÓN: Telegram limita callback_data a 64 bytes, y un enlace de oferta no
cabe. Cada aviso se apunta en la tabla `avisos` de perfiles.db con una clave corta (12
caracteres de la huella del enlace). Ese índice sirve además para medir la precisión real:
de lo que se avisó, qué se marcó y cómo. La clave es la misma para todos los que reciban esa
oferta, y por eso la tabla va por (chat_id, clave).
"""

from __future__ import annotations

import hashlib
import logging

import almacen
import notificaciones
import perfiles

logger = logging.getLogger(__name__)

DIAS_EN_EL_INDICE = 90
TIMEOUT = 20

ACCIONES = {"f": "favorito", "a": "aplicada", "o": "oculta"}
MOTIVOS = {
    "x": "pide experiencia o es senior",
    "u": "ubicación o modalidad",
    "p": "no es mi perfil",
    "e": "la empresa",
    # MP6 (§4.4). Los cuatro de antes no cambian de código: los botones ya enviados siguen valiendo.
    "s": "salario o condiciones",
}
DESCUBRIMIENTO = {"s": ("favorito", "encaja"), "n": ("oculta", "no_encaja")}


# ----------------------------------------------------------------------
# El índice de avisos
# ----------------------------------------------------------------------
def clave_de(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def registrar_aviso(oferta: dict, chat_id: int | None = None, tipo: str = "aviso", avisada: str | None = None) -> str:
    """Apunta en el índice de ese perfil (del titular, si no se dice) la oferta que se va a
    avisar, y devuelve su clave corta. `tipo` «top» es la que se enseña con /top (MP5): lleva
    botones, pero no es un aviso (ver perfiles.registrar_aviso). `avisada` (MP9) es el momento del
    envío en UTC sin zona, como lo escribe el contenedor; sin él, el de ahora. Existe para que el
    tope diario y las pruebas con el reloj inyectado cuenten con la hora que dice quien envía."""
    url = oferta.get("job_url") or ""
    if not url:
        raise ValueError("oferta sin job_url")
    clave = clave_de(url)
    perfiles.podar_avisos(DIAS_EN_EL_INDICE)
    perfiles.registrar_aviso(
        perfiles.resolver(chat_id), clave, url, titulo=oferta.get("title"), empresa=oferta.get("company"),
        relevancia=oferta.get("relevancia"), fuente=oferta.get("site"),
        vacante=perfiles.vacante_de(oferta), tipo=tipo, avisada=avisada,
    )
    return clave


# ----------------------------------------------------------------------
# Los teclados (cada estado del mensaje tiene el suyo)
# ----------------------------------------------------------------------
def _boton(texto: str, datos: str) -> dict:
    return {"text": texto, "callback_data": datos}


def teclado_inicial(clave: str) -> dict:
    return {"inline_keyboard": [[
        _boton("⭐ Me interesa", f"f:{clave}"),
        _boton("✅ Aplicada", f"a:{clave}"),
        _boton("🙈 No me interesa", f"o:{clave}"),
    ]]}


def fila_de_resumen(numero: int | str, clave: str) -> list[dict]:
    """
    Los botones de la oferta número `numero` dentro de un resumen (MP9): los mismos tres códigos de
    siempre (`f:`, `a:`, `o:`, de 14 bytes con la clave: el límite es 64), con el número delante
    para saber a cuál de la lista se refieren. Una fila por oferta: 10 ofertas son 30 botones de
    los 100 que admite un teclado.
    """
    return [_boton(f"{numero} ⭐", f"f:{clave}"), _boton(f"{numero} ✅", f"a:{clave}"),
            _boton(f"{numero} 🙈", f"o:{clave}")]


def teclado_de_resumen(numeradas: list[tuple[int, str]]) -> dict:
    return {"inline_keyboard": [fila_de_resumen(n, clave) for n, clave in numeradas]}


def _numero_en_resumen(teclado: dict | None, clave: str) -> str | None:
    """
    El número de esta oferta si el mensaje que se ha pulsado es un resumen: el botón de su fila
    empieza por un número («3 ⭐»). En un aviso suelto los botones no lo llevan. Se lee del propio
    mensaje (Telegram devuelve su teclado con la pulsación), así no hay que guardar qué ofertas
    iban en qué mensaje.
    """
    for fila in (teclado or {}).get("inline_keyboard") or []:
        for b in fila:
            if str(b.get("callback_data", "")).endswith(":" + clave):
                numero, _, resto = str(b.get("text", "")).partition(" ")
                return numero if numero.isdigit() and resto else None
    return None


def teclado_en_resumen(actual: dict, clave: str, numero: str, filas: list[list[dict]]) -> dict:
    """
    El teclado del resumen con SOLO las filas de esa oferta cambiadas por `filas`; las demás quedan
    como están. Las filas de una oferta son todas las que llevan su clave: los tres botones, o el
    «Descartada. ¿Por qué?» con sus motivos y el deshacer.
    """
    de_esta = lambda fila: any(str(b.get("callback_data", "")).endswith(":" + clave) for b in fila)  # noqa: E731
    nuevas = [[dict(b) for b in fila] for fila in filas]
    nuevas[0][0] = {**nuevas[0][0], "text": f"{numero} {nuevas[0][0]['text']}"} if not nuevas[0][0]["text"].startswith(f"{numero} ") \
        else nuevas[0][0]
    salida, puesto = [], False
    for fila in actual.get("inline_keyboard") or []:
        if de_esta(fila):
            if not puesto:
                salida.extend(nuevas)
                puesto = True
        else:
            salida.append(fila)
    return {"inline_keyboard": salida}


def teclado_descubrimiento(clave: str) -> dict:
    return {"inline_keyboard": [[_boton("👍 Sí, me encaja", f"s:{clave}"), _boton("👎 No", f"n:{clave}")]]}


def teclado_tras(accion: str, clave: str, motivo: str | None = None) -> dict:
    deshacer = _boton("↩️ Deshacer", f"d:{clave}")
    if accion == "favorito":
        # «Me interesa» es un interés pendiente: lo natural es que luego se aplique.
        return {"inline_keyboard": [[_boton("⭐ Te interesa · ✅ Ya he aplicado", f"a:{clave}")],
                                    [deshacer]]}
    if accion == "aplicada":
        return {"inline_keyboard": [[_boton("✅ Aplicada", f"a:{clave}"), deshacer]]}
    if motivo:
        return {"inline_keyboard": [[_boton("🙈 Descartada: " + MOTIVOS[motivo], f"m{motivo}:{clave}")],
                                    [deshacer]]}
    # Descartada sin motivo todavía: se ofrece, sin obligar (ya está descartada).
    filas = [[_boton(texto.capitalize(), f"m{codigo}:{clave}") for codigo, texto in list(MOTIVOS.items())[i:i + 2]]
             for i in range(0, len(MOTIVOS), 2)]
    return {"inline_keyboard": [[_boton("🙈 Descartada. ¿Por qué? (opcional)", f"o:{clave}")], *filas, [deshacer]]}


# ----------------------------------------------------------------------
# Una pulsación
# ----------------------------------------------------------------------
def _llamar(token: str, metodo: str, cuerpo: dict, timeout: float = TIMEOUT) -> dict | None:
    """Por el emisor de notificaciones (ritmo, 429 y 403, MP5). Devuelve el JSON o None si falló."""
    respuesta = notificaciones.llamar(token, metodo, cuerpo, timeout=timeout)
    if respuesta.codigo is None:
        logger.warning("Telegram %s ha fallado: %s", metodo, respuesta.descripcion)
        return None
    if not respuesta.ok:
        logger.warning("Telegram %s respondió %s: %s", metodo, respuesta.codigo, respuesta.descripcion[:200])
        return None
    return respuesta.datos


def _perfil_del_chat(chat: str, principal: str) -> int | None:
    """
    De quién es este chat. El principal es el titular aunque no tenga fila en `perfiles`, y sin
    él configurado no hay titular: sus botones no valen. Cualquier otro tiene que tener perfil.
    """
    if principal and chat == principal:
        return perfiles.titular()
    try:
        chat_id = int(chat)
    except ValueError:
        return None
    return chat_id if perfiles.obtener(chat_id) is not None else None


def _con_rasgos(chat_id: int, url: str, extra: dict) -> dict:
    """La etiqueta con los datos de sus rasgos. Si falla, se marca igual: la capa los completa luego."""
    try:
        import aprendizaje  # diferido: arrastra buscador

        oferta = almacen.ofertas_por_url([url], chat_id=chat_id).get(url)
        if oferta:
            return {**extra, "rasgos": aprendizaje.datos_de_rasgos(oferta)}
    except Exception:
        logger.exception("No se han podido apuntar los rasgos de la etiqueta; se completan después.")
    return extra


def procesar(update: dict, config: dict) -> str | None:
    """
    Atiende una actualización de getUpdates. Devuelve lo que se ha hecho (para el log y
    las pruebas) o None si no era una pulsación nuestra.

    Idempotente: Telegram puede repetir una actualización si el contenedor se reinicia
    antes de confirmarla, y pulsar dos veces deja lo mismo (almacen.fijar_marca).
    """
    pulsacion = update.get("callback_query")
    if not pulsacion:
        return None
    token = (config.get("telegram_bot_token") or "").strip()
    principal = str(config.get("telegram_chat_id") or "").strip()
    mensaje = pulsacion.get("message") or {}
    chat = str((mensaje.get("chat") or {}).get("id", ""))

    def responder(texto: str) -> None:
        _llamar(token, "answerCallbackQuery", {"callback_query_id": pulsacion.get("id"), "text": texto})

    chat_id = _perfil_del_chat(chat, principal)
    if chat_id is None:
        responder("Este botón no funciona en este chat.")
        logger.warning("Pulsación rechazada: viene de un chat sin perfil.")
        return "rechazada"

    codigo, _, clave = (pulsacion.get("data") or "").partition(":")
    # Solo en el índice de ESTE chat: un aviso de otro aquí no existe, y la respuesta es la misma
    # que para una clave cualquiera, así que tampoco se puede deducir que otro lo tenga (regla 5).
    aviso = perfiles.aviso(chat_id, clave)
    if not aviso:
        responder("Esta oferta ya no está en el índice de avisos.")
        return "desconocida"
    url = aviso["url"]
    datos_oferta = {"title": aviso.get("titulo"), "company": aviso.get("empresa")}

    if codigo in ACCIONES:
        accion = ACCIONES[codigo]
        extra = _con_rasgos(chat_id, url, {"origen": "telegram"})
        almacen.fijar_marca(url, accion, datos_oferta, extra, chat_id=chat_id)
        perfiles.anotar_respuesta(chat_id, clave, accion)
        teclado = teclado_tras(accion, clave)
        texto = {"favorito": "⭐ Guardada en «Me interesa».", "aplicada": "✅ Marcada como aplicada.",
                 "oculta": "🙈 Descartada. Si quieres, dime por qué."}[accion]
        hecho = accion
    elif codigo.startswith("m") and codigo[1:] in MOTIVOS:
        motivo = codigo[1:]
        almacen.fijar_marca(url, "oculta", datos_oferta,
                            _con_rasgos(chat_id, url, {"origen": "telegram", "motivo": MOTIVOS[motivo]}),
                            chat_id=chat_id)
        perfiles.anotar_respuesta(chat_id, clave, "oculta", MOTIVOS[motivo])
        teclado = teclado_tras("oculta", clave, motivo)
        texto = "Anotado: " + MOTIVOS[motivo] + "."
        hecho = "motivo:" + motivo
    elif codigo in DESCUBRIMIENTO:
        accion, respuesta = DESCUBRIMIENTO[codigo]
        almacen.fijar_marca(url, accion, datos_oferta, _con_rasgos(chat_id, url, {"origen": "descubrimiento"}),
                            chat_id=chat_id)
        perfiles.anotar_respuesta(chat_id, clave, respuesta)
        teclado = teclado_tras(accion, clave)
        texto = ("👍 Anotado: te encaja. La guardo en «Me interesa»." if codigo == "s"
                 else "👎 Anotado: no te encaja. Si quieres, dime por qué.")
        hecho = respuesta
    elif codigo == "d":
        almacen.quitar_marcas(url, chat_id=chat_id)
        perfiles.anotar_respuesta(chat_id, clave, None)
        teclado = teclado_inicial(clave)
        texto = "↩️ Deshecho: la oferta queda sin marcar."
        hecho = "deshacer"
    else:
        responder("Botón desconocido.")
        return "desconocida"

    # En un resumen (MP9) el teclado es de varias ofertas: solo cambia la fila de la pulsada.
    numero = _numero_en_resumen(mensaje.get("reply_markup"), clave)
    if numero is not None:
        filas = ([fila_de_resumen(numero, clave)] if codigo == "d" else teclado["inline_keyboard"])
        teclado = teclado_en_resumen(mensaje["reply_markup"], clave, numero, filas)

    responder(texto)
    _llamar(token, "editMessageReplyMarkup", {"chat_id": mensaje["chat"]["id"],
                                              "message_id": mensaje.get("message_id"),
                                              "reply_markup": teclado})
    if chat_id == perfiles.titular():
        logger.info("Botón de Telegram: %s en «%s».", hecho, (aviso.get("titulo") or "")[:60])
    else:
        logger.info("Botón de Telegram de otro perfil.")
    return hecho
