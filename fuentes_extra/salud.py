"""
Salud de las fuentes propias: detecta cuándo un portal empieza a bloquear, desactiva
sola la fuente afectada y deja anotada la incidencia para que se avise por Telegram.

Por qué existe esto y no basta con el log: una fuente que empieza a responder 403 no
rompe la búsqueda (cada módulo de fuentes_extra devuelve un DataFrame vacío y sigue),
así que el fallo es SILENCIOSO — la búsqueda parece ir bien, solo que esa fuente deja
de aportar nada y no hay forma de enterarse salvo leyendo el log a mano. Peor aún:
seguiríamos machacando un host que ya ha dicho que no, que es exactamente lo que no
queremos hacer.

La política es por tanto: a los BLOQUEOS_PARA_DESACTIVAR bloqueos consecutivos, la
fuente se marca desactivada en salud_fuentes.json y deja de consultarse en las
búsquedas siguientes. No se reactiva sola por tiempo a propósito: si un host nos ha
bloqueado, volver a probar en unas horas es insistir, no recuperarse. Para reactivarla
hay que ponerle "desactivada": false a mano en salud_fuentes.json (o borrar el archivo),
que es una decisión consciente de una persona y no un reintento automático.

Un bloqueo NO es lo mismo que un fallo de red: un timeout o un DNS caído son problemas
nuestros, no del host diciéndonos que paremos, y no cuentan para desactivar nada.
Solo cuentan los códigos de RESPUESTAS_DE_BLOQUEO (el host contestó, y contestó que no).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

# Mismo criterio que buscador.py/almacen.py: con JOBSPY_DATA_DIR el estado va al volumen
# de Docker; sin ella, junto al código.
DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent.parent)))
SALUD_PATH = DIR_BASE / "salud_fuentes.json"

# Códigos con los que un host dice explícitamente «no»: 401/403 (prohibido), 407 (proxy),
# 429 (demasiadas peticiones), 451 (bloqueo legal). El 503 entra porque es lo que
# devuelven los WAF tipo Cloudflare cuando cortan por sospecha de bot, aunque a veces sea
# una caída de verdad; en el peor caso desactivamos algo que se habría recuperado, y eso
# es preferible al contrario.
RESPUESTAS_DE_BLOQUEO = {401, 403, 407, 429, 451, 503}

BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO = 3
# SmartRecruiters con 1: se usa yendo contra la preferencia de su robots.txt
# (User-agent: * -> Disallow: /, con excepción solo para LinkedInBot), así que al primer
# «no» explícito se para. No se le dan tres oportunidades a un host que ya había pedido
# que no le rascáramos: si contesta 403 una vez, se acabó.
BLOQUEOS_PARA_DESACTIVAR = {"smartrecruiters": 1}
# Tecnoempleo, también con 1: bloquea por nombre a los agentes de IA y su robots.txt cierra
# los feeds a los rastreadores. Se lee su feed con salvaguardas por decisión del usuario
# (ver fuentes_extra/tecnoempleo.py); al primer «no» explícito del servidor, se para.
BLOQUEOS_PARA_DESACTIVAR["tecnoempleo"] = 1
# Jooble, también con 1: es una API con 500 peticiones. Un 403 es una clave que no vale y
# un 429 el límite agotado; reintentarlo en cada corrida solo gastaría lo que queda.
BLOQUEOS_PARA_DESACTIVAR["jooble"] = 1

# Fuentes que AVISAN pero no se desactivan solas (añadido el 2026-10-25).
#
# Un 429 de LinkedIn no es un «no» permanente: se levanta en torno a un minuto y es la
# respuesta normal a una ráfaga. Desactivar por eso la fuente que aporta el 86 % de las
# ofertas de la base —y que no se reactiva sola, por diseño— sería un fallo peor que el
# bloqueo: el sistema se quedaría ciego hasta que alguien editara un fichero a mano, y
# si el usuario está de viaje eso son días.
#
# Así que para estas fuentes el ciclo es: se cuentan los bloqueos, al llegar al límite
# se encola la incidencia para que llegue por Telegram, y el contador se reinicia. Sigue
# consultándose. Lo que se gana es enterarse; lo que no se hace es apagar nada solo.
# Para apagar una de estas a mano: "desactivada": true en salud_fuentes.json.
FUENTES_QUE_AVISAN_SIN_DESACTIVARSE = {"linkedin", "indeed", "glassdoor", "google"}

_lock = Lock()


def _vacio() -> dict:
    return {"fuentes": {}, "incidencias_pendientes": []}


def _leer() -> dict:
    if not SALUD_PATH.exists():
        return _vacio()
    try:
        datos = json.loads(SALUD_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _vacio()
    if not isinstance(datos, dict):
        return _vacio()
    datos.setdefault("fuentes", {})
    datos.setdefault("incidencias_pendientes", [])
    return datos


def _escribir(datos: dict) -> None:
    # Escritura atómica, igual que almacen.py: este archivo se toca desde varios hilos
    # de búsqueda a la vez y no puede quedarse a medias.
    try:
        tmp = SALUD_PATH.with_suffix(SALUD_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SALUD_PATH)
    except OSError:
        # Si no se puede persistir la salud, la búsqueda debe seguir igualmente: perder
        # el contador es mucho menos grave que tirar una búsqueda de varios minutos.
        logger.exception("No se ha podido guardar %s", SALUD_PATH.name)


def _limite(fuente: str) -> int:
    """Umbral de esta fuente. Acepta tanto el nombre completo como el sufijo, porque las
    fuentes compuestas se registran con prefijo ("empresas:smartrecruiters") para que un
    bloqueo de un ATS no arrastre a los demás, y el umbral se define por el ATS."""
    if fuente in BLOQUEOS_PARA_DESACTIVAR:
        return BLOQUEOS_PARA_DESACTIVAR[fuente]
    sufijo = fuente.rpartition(":")[2]
    return BLOQUEOS_PARA_DESACTIVAR.get(sufijo, BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO)


def esta_activa(fuente: str) -> bool:
    """¿Se puede consultar esta fuente, o se desactivó sola por bloqueos?"""
    with _lock:
        info = _leer()["fuentes"].get(fuente) or {}
    return not info.get("desactivada", False)


def motivo_desactivacion(fuente: str) -> str | None:
    with _lock:
        info = _leer()["fuentes"].get(fuente) or {}
    return info.get("motivo") if info.get("desactivada") else None


def registrar_exito(fuente: str) -> None:
    """Una petición ha ido bien: se olvida la racha de bloqueos anterior.

    Solo escribe en disco si había algo que borrar, para no reescribir el archivo en
    cada una de las decenas de peticiones correctas de una búsqueda normal.
    """
    with _lock:
        datos = _leer()
        info = datos["fuentes"].get(fuente)
        if not info or not info.get("bloqueos_consecutivos"):
            return
        info["bloqueos_consecutivos"] = 0
        info["ultimo_exito"] = datetime.now().isoformat(timespec="seconds")
        _escribir(datos)


# ----------------------------------------------------------------------
# CANARIO: el portal responde bien pero no trae NADA (2026-10-26)
#
# El fallo que un bloqueo no cubre: el portal contesta 200, pero el lector ya no
# reconoce la página (han cambiado el marcado) y devuelve cero ofertas. Para el sistema
# eso era idéntico a «no hay nada nuevo» y podía durar indefinidamente. Caso real que lo
# motivó: fuentes_extra/infojobs.py hace `if not cards: break` sin dejar rastro.
#
# Se cuenta por CORRIDA, no por petición: una búsqueda suelta sin resultados es normal
# («siem analyst» puede no tener ninguna), pero una corrida entera —una veintena de
# búsquedas— sin una sola tarjeta, con el portal respondiendo bien, no lo es.
#
# Avisa UNA vez por racha y no desactiva nada: si el portal ha cambiado, seguir
# consultando no cuesta nada, y cuando vuelva a traer resultados se avisa también de
# que se ha recuperado, para que el primer aviso no quede colgado.
# ----------------------------------------------------------------------
CORRIDAS_VACIAS_PARA_AVISAR = 2


def registrar_corrida_vacia(fuente: str, detalle: str = "") -> bool:
    """El portal ha respondido en toda la corrida pero sin una sola oferta. Devuelve True
    si ESTA llamada es la que encola el aviso."""
    with _lock:
        datos = _leer()
        info = datos["fuentes"].setdefault(fuente, {})
        info["corridas_vacias_consecutivas"] = int(info.get("corridas_vacias_consecutivas", 0)) + 1
        info["ultima_corrida_vacia"] = datetime.now().isoformat(timespec="seconds")
        avisa = (info["corridas_vacias_consecutivas"] >= CORRIDAS_VACIAS_PARA_AVISAR
                 and not info.get("aviso_vacio_enviado"))
        if avisa:
            info["aviso_vacio_enviado"] = True
            datos["incidencias_pendientes"].append({
                "fuente": fuente,
                "tipo": "vacia",
                "desactivada": False,
                "cuando": info["ultima_corrida_vacia"],
                "motivo": (
                    "%d corridas seguidas respondiendo bien pero SIN NINGUNA oferta%s. "
                    "Lo más probable es que el portal haya cambiado su página y el lector "
                    "ya no la reconozca." % (info["corridas_vacias_consecutivas"],
                                             "; " + detalle[:120] if detalle else "")
                ),
            })
        _escribir(datos)
    if avisa:
        logger.error("Fuente '%s': %s corridas seguidas sin ofertas con el portal respondiendo "
                     "bien. Se avisa por Telegram.", fuente, CORRIDAS_VACIAS_PARA_AVISAR)
    else:
        logger.warning("Fuente '%s': corrida sin ninguna oferta aunque el portal responde (%s "
                       "seguida/s).", fuente, info["corridas_vacias_consecutivas"])
    return avisa


def registrar_corrida_con_resultados(fuente: str) -> None:
    """La fuente ha traído algo: se cierra la racha. Si ya se había avisado de que estaba
    vacía, se avisa de que vuelve a funcionar. Solo escribe si había algo que cerrar."""
    with _lock:
        datos = _leer()
        info = datos["fuentes"].get(fuente)
        if not info or not (info.get("corridas_vacias_consecutivas") or info.get("aviso_vacio_enviado")):
            return
        if info.get("aviso_vacio_enviado"):
            datos["incidencias_pendientes"].append({
                "fuente": fuente, "tipo": "recuperada", "desactivada": False,
                "cuando": datetime.now().isoformat(timespec="seconds"),
                "motivo": "vuelve a traer ofertas tras %s corrida/s vacías."
                          % info.get("corridas_vacias_consecutivas", 0),
            })
        info["corridas_vacias_consecutivas"] = 0
        info["aviso_vacio_enviado"] = False
        _escribir(datos)


def registrar_aviso_presupuesto(fuente: str, motivo: str) -> None:
    """Una API con cupo (Jooble) se acerca al límite o lo ha alcanzado. Solo encola el
    aviso: cuándo avisar y cuándo pararse lo decide la propia fuente."""
    with _lock:
        datos = _leer()
        datos["incidencias_pendientes"].append({
            "fuente": fuente, "tipo": "presupuesto", "desactivada": False,
            "cuando": datetime.now().isoformat(timespec="seconds"), "motivo": motivo,
        })
        _escribir(datos)
    logger.warning("Fuente '%s': %s", fuente, motivo)


def registrar_incidencia_sistema(fuente: str, motivo: str) -> None:
    """
    Un problema del propio sistema, no de un portal (MP1: perfiles.db corrupta o ausente).

    Va por esta cola porque es la única vía de aviso que no depende de la base que acaba
    de fallar: se guarda en salud_fuentes.json y app.py la manda por Telegram igual que
    cualquier otra incidencia, reintentando si el envío falla.
    """
    with _lock:
        datos = _leer()
        datos["incidencias_pendientes"].append({
            "fuente": fuente, "tipo": "sistema", "desactivada": False,
            "cuando": datetime.now().isoformat(timespec="seconds"), "motivo": motivo,
        })
        _escribir(datos)
    logger.critical("Sistema, '%s': %s", fuente, motivo)


def registrar_aviso_operacion(fuente: str, motivo: str) -> None:
    """
    Algo de la operación que el titular tiene que saber y que no es de ningún portal (MP3): la
    rotación de consultas no llega, o un perfil se ha quedado sin corrida. No para nada.
    """
    with _lock:
        datos = _leer()
        datos["incidencias_pendientes"].append({
            "fuente": fuente, "tipo": "operacion", "desactivada": False,
            "cuando": datetime.now().isoformat(timespec="seconds"), "motivo": motivo,
        })
        _escribir(datos)
    logger.warning("Operación, '%s': %s", fuente, motivo)


# Una fuente desactivada se anunciaba en el log en CADA búsqueda que la incluía: unas 25
# líneas por corrida solo para EURES, que llenaban el log y hacían que un `grep` de
# «bloque» pareciera una avalancha de bloqueos. Ahora se anuncia como mucho una vez por
# este intervalo (las corridas son horarias y duran unos minutos: una vez por corrida).
_ULTIMO_ANUNCIO_DESACTIVADA: dict[str, float] = {}
SEGUNDOS_ENTRE_ANUNCIOS_DESACTIVADA = 30 * 60


def anunciar_desactivada(fuente: str) -> bool:
    """¿Toca escribir en el log que `fuente` está desactivada? Como mucho una vez cada media hora."""
    import time

    ahora = time.monotonic()
    with _lock:
        ultimo = _ULTIMO_ANUNCIO_DESACTIVADA.get(fuente)
        if ultimo is not None and ahora - ultimo < SEGUNDOS_ENTRE_ANUNCIOS_DESACTIVADA:
            return False
        _ULTIMO_ANUNCIO_DESACTIVADA[fuente] = ahora
        return True


def registrar_bloqueo(fuente: str, codigo: int | None, detalle: str = "") -> bool:
    """
    Anota que `fuente` ha respondido con un bloqueo. Devuelve True si ESTA llamada es
    la que la ha desactivado (para poder loguearlo una sola vez, no en cada petición).

    La incidencia se encola en el propio archivo, no se manda desde aquí: este módulo
    no sabe nada de Telegram ni de config, igual que almacen.py no sabe nada de
    relevancia. Quien decide notificar es la capa de arriba (app.py).
    """
    with _lock:
        datos = _leer()
        info = datos["fuentes"].setdefault(fuente, {})
        info["bloqueos_consecutivos"] = int(info.get("bloqueos_consecutivos", 0)) + 1
        info["ultimo_bloqueo"] = datetime.now().isoformat(timespec="seconds")
        info["ultimo_codigo"] = codigo
        info["ultimo_detalle"] = (detalle or "")[:300]

        limite = _limite(fuente)
        al_limite = info["bloqueos_consecutivos"] >= limite
        solo_avisa = fuente.rpartition(":")[2] in FUENTES_QUE_AVISAN_SIN_DESACTIVARSE

        if solo_avisa:
            # Avisa y sigue (ver FUENTES_QUE_AVISAN_SIN_DESACTIVARSE). El contador se
            # reinicia al avisar para no repetir el mensaje en cada corrida: si el
            # bloqueo persiste, vuelve a avisar al acumular otros `limite` seguidos.
            if al_limite:
                motivo = (
                    f"{info['bloqueos_consecutivos']} bloqueo(s) consecutivo(s) "
                    f"(último: HTTP {codigo}{'; ' + detalle[:120] if detalle else ''}). "
                    f"La fuente SIGUE activa: no se desactiva sola."
                )
                info["bloqueos_consecutivos"] = 0
                info["ultimo_aviso"] = info["ultimo_bloqueo"]
                datos["incidencias_pendientes"].append({
                    "fuente": fuente, "motivo": motivo, "codigo": codigo,
                    "cuando": info["ultimo_bloqueo"], "desactivada": False,
                })
            _escribir(datos)
            if al_limite:
                logger.error(
                    "Fuente '%s' bloqueada repetidamente (HTTP %s). Se avisa por Telegram "
                    "y se sigue consultando: es una fuente que no se desactiva sola.",
                    fuente, codigo,
                )
            else:
                logger.warning(
                    "Fuente '%s': respuesta de bloqueo HTTP %s (%s). Avisará al llegar "
                    "a %s seguidos.", fuente, codigo, detalle[:150] or "sin detalle", limite,
                )
            return False

        recien_desactivada = not info.get("desactivada", False) and al_limite
        if recien_desactivada:
            motivo = (
                f"{info['bloqueos_consecutivos']} bloqueo(s) consecutivo(s) "
                f"(último: HTTP {codigo}{'; ' + detalle[:120] if detalle else ''})"
            )
            info["desactivada"] = True
            info["desactivada_en"] = info["ultimo_bloqueo"]
            info["motivo"] = motivo
            datos["incidencias_pendientes"].append(
                {
                    "fuente": fuente,
                    "motivo": motivo,
                    "codigo": codigo,
                    "cuando": info["ultimo_bloqueo"],
                }
            )
        _escribir(datos)

    if recien_desactivada:
        logger.error(
            "FUENTE DESACTIVADA AUTOMÁTICAMENTE: '%s' tras %s bloqueo(s) consecutivo(s) "
            "(HTTP %s). No se volverá a consultar. Para reactivarla, pon "
            "\"desactivada\": false en %s.",
            fuente, _limite(fuente), codigo, SALUD_PATH.name,
        )
    else:
        logger.warning(
            "Fuente '%s': respuesta de bloqueo HTTP %s (%s). %s",
            fuente, codigo, detalle[:150] or "sin detalle",
            f"Se desactivará sola al llegar a {_limite(fuente)} seguidos.",
        )
    return recien_desactivada


def incidencias_pendientes() -> list[dict]:
    """Incidencias que todavía no se han avisado. No las consume (ver `marcar_avisadas`)."""
    with _lock:
        return list(_leer()["incidencias_pendientes"])


def marcar_avisadas(incidencias: list[dict]) -> None:
    """
    Saca de la cola las incidencias ya avisadas. Se pasan las mismas que devolvió
    `incidencias_pendientes` en vez de vaciar la cola entera: si mientras se enviaba el
    mensaje se ha encolado otra incidencia, no debe perderse sin haberse avisado.
    """
    if not incidencias:
        return
    ya = {(i.get("fuente"), i.get("cuando")) for i in incidencias}
    with _lock:
        datos = _leer()
        datos["incidencias_pendientes"] = [
            i for i in datos["incidencias_pendientes"]
            if (i.get("fuente"), i.get("cuando")) not in ya
        ]
        _escribir(datos)


def fuentes() -> dict[str, dict]:
    """El estado de cada fuente que ha dado algo que anotar, como copia: lo leen /fuentes y /estado."""
    with _lock:
        return json.loads(json.dumps(_leer()["fuentes"]))


def reactivar(fuente: str) -> None:
    """Vuelve a habilitar una fuente desactivada (decisión manual, ver el docstring)."""
    with _lock:
        datos = _leer()
        info = datos["fuentes"].get(fuente)
        if not info:
            return
        info["desactivada"] = False
        info["bloqueos_consecutivos"] = 0
        info.pop("motivo", None)
        info.pop("desactivada_en", None)
        _escribir(datos)
    logger.info("Fuente '%s' reactivada manualmente.", fuente)
