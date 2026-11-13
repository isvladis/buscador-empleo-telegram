"""
MP9 — Avisos por perfil: cuándo y cuántos le llegan a cada persona, y qué pasa con quien deja de buscar.

Cada perfil elige (en `datos.avisos`, desde /ajustes → Avisos):

- `modo`: «instantaneo» (al momento, como siempre), «diario» (un resumen a las 9:00) o «dos_diarios»
  (9:00 y 18:00). Las horas son de **Europe/Madrid**: el contenedor va en UTC y el huso de la persona
  cambia dos veces al año; con `zoneinfo` las 9:00 siguen siendo las 9:00 de allí.
- `silencio`: [22, 8] = de 22:00 a 8:00, cruzando la medianoche. Lo que llegue espera en la cola.
- `max_dia`: tope de mensajes de ofertas al día (avisos y ofertas para descubrir). Lo que sobra espera.

**Nada se pierde:** lo que no se manda ahora va a `pendientes` y sale en el siguiente envío. Solo se quita
de la cola lo que ya no existe: la oferta que dejó de aparecer en los portales 7 días (`visto_ultima`), la
que la persona ya clasificó (por ejemplo con /top) y la que ya se le enseñó.

**Una clave ausente significa «como antes».** Un perfil sin `silencio` ni `max_dia` y en «instantaneo» (el
del titular, y todo el que existía antes de MP9) sigue por el camino de siempre, línea por línea
(`config_de(...).legado`): lo decidió el titular el 05-11 para que su comportamiento no cambie por
sorpresa. Los valores de abajo marcados «provisional» son los que escribe la encuesta a quien entra desde
MP9, y los que quedan por calibrar con el piloto (CAMBIOS-MP9.md §6).

**El reloj se inyecta** (`RELOJ`): las pruebas lo fijan en el 25-10 o el 29-03 sin dormir, y es lo único
que protege del cambio de hora (dos veces al año, nadie lo ve venir si no se prueba).

Todo lo que toca la base pasa por funciones de `perfiles` que exigen el chat_id: una cola, un tope o un
resumen son siempre de un perfil, y los de dos perfiles en la misma corrida no se cruzan (regla 5).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time as hora_del_dia, timedelta, timezone
from zoneinfo import ZoneInfo

import almacen
import notificaciones
import perfiles

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("Europe/Madrid")
MODOS = ("instantaneo", "diario", "dos_diarios")
HORAS_DE_RESUMEN = {"diario": (9,), "dos_diarios": (9, 18)}

# PROVISIONAL, sin calibrar: no hay con qué (el piloto aún no existe) y el prompt de la fase dice que
# se elijan con datos. Son los del esquema del plan (§3.1) y los que escribe la encuesta a quien entra.
SILENCIO_POR_DEFECTO = [22, 8]
MAX_DIA_POR_DEFECTO = 10
OFERTAS_POR_RESUMEN = 10
# Esto sí es del contrato: la oferta que no aparece en 7 días se quita de la cola sin avisar.
DIAS_EN_COLA = 7

# Autopausa (D9). Los plazos son los del prompt; el aviso previo al borrado, de 7 días, es una lectura
# de «borrado con aviso previo» (CAMBIOS-MP9.md §5).
# Un perfil activo, con ofertas en su lista y CERO avisos durante este tiempo no es un perfil tranquilo:
# es uno al que no le puede llegar nada (07-11, con el piloto; `REVISION-PILOTO.md`). 2 días porque con
# uno solo basta una tarde floja de los portales, y porque el silencio y el tope ya retrasan hasta 24 h.
DIAS_SIN_AVISOS = 2
# Con menos ofertas que esto, callarse es «todavía no hay nada» y de eso ya avisa la encuesta al arrancar.
OFERTAS_PARA_MIRAR_EL_SILENCIO = 20
DIAS_SIN_PULSAR = 30
DIAS_PARA_CONTESTAR = 7
DIAS_PAUSADO_AVISO = 60
DIAS_PAUSADO_BORRADO = 120
DIAS_DEL_AVISO_PREVIO = 7
# Los mensajes de autopausa no se mandan de madrugada: preguntar «¿sigues buscando?» a las 3:00 es
# peor que esperar unas horas.
HORAS_DE_TRATO = (9, 21)

RELOJ = lambda: datetime.now(timezone.utc)  # noqa: E731 — las pruebas lo sustituyen
_candado = threading.RLock()


# ----------------------------------------------------------------------
# Tiempo
# ----------------------------------------------------------------------
def _utc(ahora: datetime | None) -> datetime:
    """Siempre con zona. Un datetime sin ella se toma como UTC, que es lo que escribe el contenedor."""
    ahora = ahora or RELOJ()
    return ahora.replace(tzinfo=timezone.utc) if ahora.tzinfo is None else ahora.astimezone(timezone.utc)


def _iso(ahora_utc: datetime) -> str:
    """Como se guardan las horas en la base: UTC, sin zona, a segundos."""
    return ahora_utc.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _leer(texto: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto)).replace(tzinfo=timezone.utc) if texto else None
    except ValueError:
        return None


def en_madrid(ahora: datetime | None = None) -> datetime:
    return _utc(ahora).astimezone(ZONA)


def inicio_del_dia(ahora: datetime | None = None) -> datetime:
    """La medianoche de Madrid de ese día, en UTC. El día tiene 23 h o 25 h cuando cambia la hora."""
    local = en_madrid(ahora)
    return datetime.combine(local.date(), hora_del_dia(0), tzinfo=ZONA).astimezone(timezone.utc)


# ----------------------------------------------------------------------
# Lo que ha elegido el perfil
# ----------------------------------------------------------------------
class Avisos:
    """`datos.avisos` ya validado: un valor roto se trata como si no estuviera, nunca como un error."""

    def __init__(self, modo: str, silencio: tuple[int, int] | None, max_dia: int | None):
        self.modo, self.silencio, self.max_dia = modo, silencio, max_dia

    @property
    def legado(self) -> bool:
        """Al momento, sin silencio y sin tope: el camino de antes de MP9, intacto."""
        return self.modo == "instantaneo" and self.silencio is None and self.max_dia is None

    @property
    def horas(self) -> tuple[int, ...]:
        return HORAS_DE_RESUMEN.get(self.modo, ())


def config_de(perfil: dict) -> Avisos:
    a = (perfil.get("datos") or {}).get("avisos") or {}
    modo = a.get("modo") if a.get("modo") in MODOS else "instantaneo"
    silencio = a.get("silencio")
    if not (isinstance(silencio, (list, tuple)) and len(silencio) == 2
            and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in silencio)
            and silencio[0] != silencio[1]):
        silencio = None
    tope = a.get("max_dia")
    if isinstance(tope, bool) or not isinstance(tope, int) or tope < 1:
        tope = None
    return Avisos(modo, tuple(silencio) if silencio else None, tope)


def en_silencio(avisos: Avisos, ahora: datetime | None = None) -> bool:
    """[22, 8] es de 22:00 a 8:00 (cruza la medianoche); [13, 15], de 13:00 a 15:00. Hora de Madrid."""
    if avisos.silencio is None:
        return False
    desde, hasta = avisos.silencio
    hora = en_madrid(ahora).hour
    return desde <= hora < hasta if desde < hasta else (hora >= desde or hora < hasta)


def ultimo_turno(avisos: Avisos, ahora: datetime | None = None) -> datetime | None:
    """El último resumen que ya tocaba mandar (hoy o ayer, hora de Madrid), en UTC; None si no es un modo de resumen."""
    local = en_madrid(ahora)
    turnos = [datetime.combine(local.date() - timedelta(days=d), hora_del_dia(h), tzinfo=ZONA)
              for d in (0, 1) for h in avisos.horas]
    pasados = [t for t in turnos if t <= local]
    return max(pasados).astimezone(timezone.utc) if pasados else None


def enviados_hoy(chat_id: int, ahora: datetime | None = None) -> int:
    return perfiles.contar_enviados(chat_id, _iso(inicio_del_dia(ahora)))


def hueco(avisos: Avisos, chat_id: int, ahora: datetime | None = None) -> int | None:
    """Cuántos mensajes de ofertas le caben hoy; None si no tiene tope."""
    if avisos.max_dia is None:
        return None
    return max(0, avisos.max_dia - enviados_hoy(chat_id, ahora))


def hueco_para_descubrir(perfil: dict, reservadas: int, ahora: datetime | None = None) -> int:
    """
    Cuántas ofertas de descubrimiento (MP6) puede recibir ahora, dentro de las mismas reglas: ninguna en un
    modo de resumen (un aviso suelto a media mañana contradice lo que eligió; y la cola no sabe de ellas),
    ninguna en silencio, y con tope, solo lo que quede después de los avisos de esta corrida (`reservadas`).
    """
    avisos = config_de(perfil)
    if avisos.legado:
        return 99
    if avisos.modo != "instantaneo" or en_silencio(avisos, ahora):
        return 0
    libre = hueco(avisos, perfil["chat_id"], ahora)
    return 99 if libre is None else max(0, libre - reservadas)


def al_cambiar_modo(chat_id: int, modo: str, ahora: datetime | None = None) -> None:
    """
    Quien pasa a un resumen espera al siguiente turno: sin esto, el primer tick vería «el turno de las 9:00
    no se ha mandado» y volcaría la cola a las 14:00.
    """
    avisos = Avisos(modo, None, None)
    turno = ultimo_turno(avisos, ahora)
    perfiles.meta_set(f"resumen_ultimo:{chat_id}", _iso(turno) if turno else None)


# ----------------------------------------------------------------------
# Una corrida trae ofertas
# ----------------------------------------------------------------------
def _enviar_suelta(config: dict, chat_id: int, oferta: dict, posicion: str | None, avisada: str) -> bool:
    """
    Un aviso suelto, y si no llega, sin rastro: `notificar_ofertas_alto_encaje` lo apunta en `avisos` antes
    de enviarlo (los botones lo buscan ahí) y **deshace el apunte si el envío falla**, porque un aviso
    apuntado que no se mandó gastaría tope y haría creer que ya se le avisó.

    Aquí se deshacía también, con su propia consulta, hasta que P1 (06-11) llevó eso dentro del emisor para
    que el camino del titular lo tuviera igual. Hacerlo dos veces no cambiaba nada y dejaba ciego al
    control negativo «un aviso que falla gasta tope», que así pasó a sobrevivir: una sola copia.
    """
    return notificaciones.notificar_ofertas_alto_encaje(config, [oferta], chat_id=chat_id, tope=1, con_resumen=False,
                                                        posicion=posicion, avisada=avisada)



def entregar(config: dict, perfil: dict, altas: list[dict], posicion: str | None = None,
             ahora: datetime | None = None) -> dict:
    """
    Las ofertas de Encaje alto de esta corrida para un perfil que NO es `legado`. Cada una se manda ahora
    (modo instantáneo, fuera del silencio y con hueco en el tope) o se encola; ninguna se pierde.
    Devuelve {"enviadas": n, "encoladas": n}.

    El camino rápido no escribe en la cola: lo que sale ahora se manda como un aviso de siempre, uno por
    mensaje y con sus botones, y solo lo que sobra toca `pendientes` (una escritura por corrida, no por oferta).
    """
    chat_id, avisos = perfil["chat_id"], config_de(perfil)
    ahora = _utc(ahora)
    with _candado:
        ahora_naive = _iso(ahora)
        ahora_ya = avisos.modo == "instantaneo" and not en_silencio(avisos, ahora)
        libre = hueco(avisos, chat_id, ahora) if ahora_ya else 0
        por_corrida = int(config.get("telegram_max_avisos_individuales", notificaciones.MAX_AVISOS_INDIVIDUALES))
        k = min(len(altas), por_corrida, len(altas) if libre is None else libre) if ahora_ya else 0
        enviadas, fallidas = 0, []
        for oferta in altas[:k]:
            if _enviar_suelta(config, chat_id, oferta, posicion, ahora_naive):
                enviadas += 1
            else:
                fallidas.append(oferta)
        sobran = fallidas + list(altas[k:])
        perfiles.encolar_pendientes(chat_id, [(o["job_url"], o.get("relevancia")) for o in sobran], ahora_naive)
    return {"enviadas": enviadas, "encoladas": len(sobran)}


# ----------------------------------------------------------------------
# La cola
# ----------------------------------------------------------------------
def _vigentes(chat_id: int, ahora: datetime) -> list[dict]:
    """
    La cola de este perfil sin lo que sobra, que se quita de la tabla: la oferta que ningún portal ha
    devuelto en 7 días (o que ya no enlaza), la que ya clasificó o ya vio, y la misma vacante repetida
    (se queda la mejor). Lo demás, por relevancia.
    """
    corte = _iso(ahora - timedelta(days=DIAS_EN_COLA))
    cola = perfiles.pendientes(chat_id)
    tratadas = perfiles.urls_ya_tratadas(chat_id, [p["url"] for p in cola])
    buenas, quitar = [], []
    for p in cola:
        if p["visto_ultima"] is None or str(p["visto_ultima"]) < corte or p["url"] in tratadas:
            quitar.append(p["url"])
        else:
            buenas.append(p)
    if quitar:
        perfiles.quitar_pendientes(chat_id, quitar)
        logger.info("Cola de un perfil: %d oferta(s) quitadas (caducadas o ya vistas).", len(quitar))
    return buenas


def en_cola(chat_id: int, ahora: datetime | None = None) -> int:
    """Cuántas ofertas le esperan de verdad (sin las caducadas ni las ya vistas, que se quitan al contar)."""
    return len(_vigentes(chat_id, _utc(ahora)))


def _preparar(perfil: dict, urls: list[str]) -> list[dict]:
    """Las ofertas de la cola como un aviso, puntuadas con el puntuador de HOY (igual que /top)."""
    import buscador  # aquí y no arriba: arrastra jobspy, y el servicio ya lo tiene cargado
    import config as config_mod

    chat_id = perfil["chat_id"]
    por_url = almacen.ofertas_por_url(urls, chat_id=chat_id)
    config_global = config_mod.cargar_config()
    puntuador = buscador.puntuador_para(perfil, config_global)
    anos_max = perfiles.config_efectiva(perfil, config_global).get("anos_experiencia_max")
    salida = []
    for url in urls:
        if url in por_url:
            salida.append(notificaciones.preparar_aviso(por_url[url], por_url[url], puntuador, anos_max))
    return salida


def _sin_repetir(chat_id: int, ofertas: list[dict], ahora: datetime) -> tuple[list[dict], list[str]]:
    """Dos de la cola con la misma vacante (republicada con otra URL) son una: se queda la mejor, y la otra se quita."""
    desde = _iso(ahora - timedelta(days=14))
    ya = {v for v, _, _ in perfiles.vacantes_avisadas(chat_id, desde)}
    vistas, buenas, sobran = set(), [], []
    for o in sorted(ofertas, key=lambda x: -(x.get("relevancia") or 0)):
        vacante = perfiles.vacante_de(o)
        if vacante in ya or vacante in vistas:
            sobran.append(o["job_url"])
        else:
            vistas.add(vacante)
            buenas.append(o)
    return buenas, sobran


def orden_de_salida(ofertas: list[dict], creada_de: dict[str, str]) -> list[dict]:
    """
    En qué orden sale la cola: la mejor nota primero y, **a igualdad de nota, la más reciente**: la que entró
    en la cola más tarde y, dentro de la misma corrida, la que el portal publicó más tarde. El enlace solo
    decide al final, para que el orden sea siempre el mismo.

    Hasta el 11-11 el desempate era el enlace, por orden alfabético, y eso es elegir por portal:
    `es.indeed.com` < `es.jooble.org` < `europa.eu` < `www.linkedin.com`. No se notaba con las notas del
    titular, que van de 5,0 a 11,5, pero en un perfil de catálogo casi todo lo que llega vale lo mismo
    (medido el 11-11 en el perfil que más recibe: 95 de sus 100 ofertas de Encaje alto son exactamente
    «puesto + ciudad» = 5,0), así que el desempate **es** el orden. Con su tope de 10 al día y unas 17
    nuevas, cada mañana le salían 10 elegidas por el nombre del portal: en su cola de ese día había 20 de
    Jooble y 3 de LinkedIn, dos de ellas con 21 h de espera, que la tanda de las 8:00 no sacó y la
    siguiente tampoco habría sacado, porque las 20 de Jooble van antes en el alfabeto
    (`REVISION-2026-11-11.md` §4).

    Lo más reciente primero, y no lo que más lleva esperando, porque el tope hace que no quepa todo: con
    «la más antigua primero» cada tanda sería la de ayer y lo de hoy esperaría a mañana, siempre un día
    tarde; así recibe lo último y lo que no cabe es lo que más tiempo lleva publicado. Sin tope, o con la
    cola por debajo de él, sale todo y el orden solo es el del mensaje.
    """
    def publicada(o: dict) -> str:
        return str(o.get("publicada_en") or o.get("date_posted") or "")[:10]

    # Ordenaciones estables encadenadas, de la clave que menos manda a la que más.
    salida = sorted(ofertas, key=lambda o: o["job_url"])
    salida.sort(key=publicada, reverse=True)
    salida.sort(key=lambda o: str(creada_de.get(o["job_url"]) or ""), reverse=True)
    salida.sort(key=lambda o: -(o.get("relevancia") or 0))
    return salida


def vaciar(config: dict, perfil: dict, ahora: datetime | None = None, posicion: str | None = None) -> int:
    """
    Manda de la cola de este perfil lo que le toque AHORA, y devuelve cuántas ofertas.

    - En silencio no sale nada.
    - Los modos de resumen solo sueltan la cola en su turno (9:00 / 18:00 de Madrid); el turno que pasa con
      la cola vacía también se da por hecho, y si no, una oferta que llegara a las 14:00 saldría suelta.
    - Al momento: sale en cuanto no hay silencio y hay hueco en el tope (a medianoche vuelve a haberlo).
    - Como mucho `OFERTAS_POR_RESUMEN` y lo que quede de su tope; el resto sigue en la cola.
    - Una sola oferta del modo instantáneo sale como un aviso normal; varias, como resumen.
    """
    if perfil.get("estado") != "activo" or not perfiles.recibe_avisos(perfil):
        return 0
    chat_id, avisos = perfil["chat_id"], config_de(perfil)
    ahora = _utc(ahora)
    with _candado:
        if en_silencio(avisos, ahora):
            return 0
        turno = ultimo_turno(avisos, ahora)
        marca = f"resumen_ultimo:{chat_id}"
        if avisos.horas:
            ultimo = _leer(perfiles.meta_get(marca))
            if ultimo is None and turno is not None and perfiles.meta_get(marca) is None:
                perfiles.meta_set(marca, _iso(turno))  # primera vez que lo vemos: espera al siguiente turno
                return 0
            if turno is None or (ultimo is not None and ultimo >= turno):
                return 0
        if perfiles.contar_pendientes(chat_id) == 0:
            if avisos.horas:
                perfiles.meta_set(marca, _iso(turno))
            return 0
        vigentes = _vigentes(chat_id, ahora)
        libre = hueco(avisos, chat_id, ahora)
        if not vigentes:
            if avisos.horas:
                perfiles.meta_set(marca, _iso(turno))
            return 0
        if libre == 0:
            return 0  # hoy ya no caben más; mañana, tras la medianoche de Madrid
        cuantas = OFERTAS_POR_RESUMEN if libre is None else min(OFERTAS_POR_RESUMEN, libre)
        ofertas = _preparar(perfil, [p["url"] for p in vigentes])
        ofertas, repetidas = _sin_repetir(chat_id, ofertas, ahora)
        if repetidas:
            perfiles.quitar_pendientes(chat_id, repetidas)
        ofertas = orden_de_salida(ofertas, {p["url"]: p["creada"] for p in vigentes})
        elegidas, resto = ofertas[:cuantas], len(ofertas) - min(len(ofertas), cuantas)
        if not elegidas:
            return 0
        ahora_naive = _iso(ahora)
        if len(elegidas) == 1 and not avisos.horas:
            llegadas = [0] if _enviar_suelta(config, chat_id, elegidas[0], posicion, ahora_naive) else []
        else:
            cabecera = (f"📬 <b>Resumen de las {en_madrid(turno).hour}:00</b>" if avisos.horas
                        else "📬 <b>Lo que esperaba</b>")
            llegadas = notificaciones.notificar_resumen(config, chat_id, elegidas, cabecera, resto, posicion, ahora_naive)
        perfiles.quitar_pendientes(chat_id, [elegidas[i]["job_url"] for i in llegadas])
        if llegadas and avisos.horas:
            perfiles.meta_set(marca, _iso(turno))
        logger.info("Cola: %d de %d oferta(s) enviadas%s.", len(llegadas), len(elegidas),
                    "" if posicion is None else f" (perfil {posicion})")
        return len(llegadas)


def tick(config: dict, ahora: datetime | None = None) -> int:
    """Lo que hace el planificador cada minuto: vaciar la cola de cada perfil activo que le toque. Sin chat_id en el log."""
    total = 0
    activos = perfiles.activos()
    for i, perfil in enumerate(activos, 1):
        try:
            total += vaciar(config, perfil, ahora, f"{i}/{len(activos)}")
        except perfiles.BaseNoDisponible:
            raise
        except Exception:
            logger.exception("Perfil %d/%d: no se ha podido vaciar su cola.", i, len(activos))
    return total


# ----------------------------------------------------------------------
# Autopausa (D9): quien deja de buscar deja de gastar consultas
# ----------------------------------------------------------------------
def _en_horas_de_trato(ahora: datetime) -> bool:
    return HORAS_DE_TRATO[0] <= en_madrid(ahora).hour < HORAS_DE_TRATO[1]


def _actividad(p: dict) -> datetime | None:
    return _leer(p.get("ultima_actividad") or p.get("creado"))


def teclado_de_pregunta() -> dict:
    return {"inline_keyboard": [[{"text": "✅ Sí, sigo buscando", "callback_data": "aj1:ap:s"},
                                 {"text": "⏸ Pausar", "callback_data": "aj1:ap:p"}]]}


def _pregunta(config: dict, chat_id: int, ahora: datetime) -> bool:
    texto = ("👋 <b>¿Sigues buscando trabajo?</b>\n\nLlevas 30 días recibiendo ofertas sin tocar ningún botón. Si "
             "ya has encontrado algo, ¡enhorabuena! Pulsa «Pausar» y dejo de buscar para ti. Si sigues buscando, "
             "pulsa «Sí». Y si te llegan cosas que no te sirven, /ajustes cambia lo que busco.\n\nSi no contestas en 7 días, lo pauso yo. "
             "Siempre puedes volver con /reanudar.")
    return notificaciones._enviar(config, texto, "autopausa: ¿sigues buscando?", teclado_de_pregunta(), [chat_id], "1/1")


def _decir(config: dict, chat_id: int, texto: str, que: str) -> bool:
    return notificaciones.enviar_a_un_perfil(config, chat_id, texto, que)


def _vigilar_silencio(config: dict, p: dict, ahora: datetime) -> None:
    """
    Un perfil con ofertas y **ningún** aviso en `DIAS_SIN_AVISOS` días: si el listón está por encima de su
    techo, se baja y se le dice; si no, se avisa al titular (sin decir quién es, regla 3).

    El caso que lo pide (07-11): el piloto votó 👎 sus seis de calibración, su listón subió a 5,5, su mejor
    oferta de siempre valía 5,0 y llevaba 48 h con cero avisos. Nadie se enteraba: desde fuera eso se
    parece a un bot roto, y desde dentro no había ninguna pantalla que lo dijera.

    No se mira si tiene cola (el tope o el silencio están reteniendo algo y `vaciar` lo soltará), si acaba
    de entrar, o si tiene pocas ofertas. Se avisa **una vez**: la marca de `meta` se borra en cuanto vuelve
    a recibir un aviso, así que si recae, se vuelve a avisar.
    """
    chat_id = p["chat_id"]
    clave = f"silencio_sin_avisos:{chat_id}"
    # «Acaba de entrar» también es haberse reiniciado (11-11): la fecha de alta se conserva, y sin esto a
    # quien repite el cuestionario y no tiene todavía nada que llegue al listón se le diría, en la primera
    # hora, que «lleva 2 días sin recibir nada» (o se le avisaría de eso al titular).
    nacido = max(filter(None, (_leer(p.get("creado")), _leer((p.get("datos") or {}).get("reiniciado")))), default=None)
    callado = (perfiles.recibe_avisos(p)
               and perfiles.contar_ofertas(chat_id) >= OFERTAS_PARA_MIRAR_EL_SILENCIO
               and perfiles.contar_pendientes(chat_id) == 0
               and nacido is not None and ahora - nacido >= timedelta(days=DIAS_SIN_AVISOS)
               and perfiles.contar_avisos(chat_id, "aviso", _iso(ahora - timedelta(days=DIAS_SIN_AVISOS))) == 0)
    if not callado:
        if perfiles.meta_get(clave) is not None:
            perfiles.meta_set(clave, None)  # vuelve a recibir: si recae, se avisa otra vez
        return
    if perfiles.meta_get(clave) is not None or not _en_horas_de_trato(ahora):
        return
    import telegram_encuesta  # aquí y no arriba: el listón vive en la encuesta, que importa el catálogo

    nuevo = telegram_encuesta.bajar_umbral_al_techo(chat_id)
    perfiles.meta_set(clave, _iso(ahora))
    if nuevo is not None:
        _decir(config, chat_id,
               "🔎 <b>No te estaba llegando nada.</b>\n\nTus 👍 y 👎 del principio me hicieron poner el "
               "listón más alto de lo que dan de sí las ofertas que encuentro para ti, y llevo "
               f"{DIAS_SIN_AVISOS} días sin poder avisarte de nada. Lo he bajado a "
               f"{notificaciones._num(nuevo)}, así que a partir de ahora te llegará lo mejor que salga.\n\n"
               "Si te parece mucho o poco, /ajustes lo cambia, y /top te enseña lo que ya tengo.",
               "silencio: listón bajado a su techo")
        logger.info("Un perfil llevaba %d días sin un solo aviso con el listón por encima de su techo: "
                    "bajado a %s.", DIAS_SIN_AVISOS, nuevo)
        return
    _decir(config, perfiles.titular(),
           f"🔇 <b>Un perfil lleva {DIAS_SIN_AVISOS} días sin recibir ningún aviso</b>, con "
           f"{perfiles.contar_ofertas(chat_id)} ofertas en su lista. Su listón no es la causa (ya está en "
           "lo más bajo que le sirve), así que o su sector se puntúa bajo o le faltan términos. /perfiles.",
           "silencio: aviso al titular")
    logger.info("Un perfil lleva %d días sin un solo aviso y el listón no es la causa: avisado el titular.",
                DIAS_SIN_AVISOS)


def _vigilar_activo(config: dict, p: dict, ahora: datetime) -> None:
    chat_id = p["chat_id"]
    clave = f"autopausa_pregunta:{chat_id}"
    actividad = _actividad(p)
    if actividad is None:
        return
    preguntada = _leer(perfiles.meta_get(clave))
    if preguntada is not None:
        if actividad > preguntada:
            perfiles.meta_set(clave, None)  # ha contestado (cualquier cosa cuenta): se olvida la pregunta
        elif ahora - preguntada >= timedelta(days=DIAS_PARA_CONTESTAR) and _en_horas_de_trato(ahora):
            perfiles.cambiar_estado(chat_id, "pausado")
            perfiles.meta_set(clave, None)
            _decir(config, chat_id, "⏸ <b>Te he puesto en pausa</b>: no he sabido de ti en 7 días. Dejo de buscar y de "
                                    "avisarte para no gastar consultas. Cuando quieras volver, /reanudar.", "autopausa: pausa")
            logger.info("Autopausa: un perfil pasa a pausado tras 7 días sin contestar.")
        return
    if (ahora - actividad < timedelta(days=DIAS_SIN_PULSAR) or not _en_horas_de_trato(ahora)
            or perfiles.contar_avisos(chat_id, "aviso", _iso(ahora - timedelta(days=DIAS_SIN_PULSAR))) == 0):
        return
    if _pregunta(config, chat_id, ahora):
        perfiles.meta_set(clave, _iso(ahora))
        logger.info("Autopausa: se pregunta «¿sigues buscando?» a un perfil (30 días sin pulsar).")


def _vigilar_pausado(config: dict, p: dict, ahora: datetime, titular_chat: int) -> None:
    chat_id = p["chat_id"]
    # Desde cuándo está así: lo último entre que se pausó (`actualizado`) y que la persona hizo algo. Las
    # marcas de abajo guardan ese instante: si cambia, volvió y los plazos empiezan de nuevo.
    reloj = max(filter(None, (_leer(p.get("actualizado")), _actividad(p))), default=None)
    if reloj is None:
        return
    dias = (ahora - reloj).days
    firma = _iso(reloj)
    k60, k113 = f"autopausa_60:{chat_id}", f"autopausa_aviso_borrado:{chat_id}"
    previo = perfiles.meta_get(k113) or ""
    previo_firma, _, previo_envio = previo.partition("|")
    dias_aviso = DIAS_PAUSADO_BORRADO - DIAS_DEL_AVISO_PREVIO
    if not _en_horas_de_trato(ahora):
        return  # ni avisos ni borrado de madrugada
    if dias >= DIAS_PAUSADO_BORRADO and previo_firma == firma and (_leer(previo_envio) is not None
                                                                   and ahora - _leer(previo_envio) >= timedelta(days=DIAS_DEL_AVISO_PREVIO)):
        copia, _ = perfiles.copia_y_borrado(chat_id)
        for tabla_meta in (k60, k113, f"autopausa_pregunta:{chat_id}"):
            perfiles.meta_set(tabla_meta, None)
        _decir(config, chat_id, "🗑 Como llevabas más de 120 días en pausa, he borrado tu perfil y todo lo tuyo, como te "
                                "avisé. Si quieres volver, pide otra invitación. ¡Suerte! 🍀", "autopausa: borrado")
        _decir(config, titular_chat, f"👋 Un perfil pausado 120 días se ha borrado por inactividad. Quedan "
                                     f"{len(perfiles.listar())}. Copia en data/borrados/{copia.name}.", "autopausa: aviso al titular")
        logger.info("Autopausa: un perfil pausado 120 días se borra (copia en data/borrados/%s).", copia.name)
    elif dias >= dias_aviso and previo_firma != firma:
        if _decir(config, chat_id, f"⚠️ <b>Tu perfil se borra en 7 días.</b> Llevas {DIAS_PAUSADO_BORRADO - DIAS_DEL_AVISO_PREVIO} días "
                                   "en pausa y a los 120 borro todo lo tuyo. Si quieres conservarlo, escribe /reanudar "
                                   "(o cualquier cosa) y empiezo a buscar otra vez.", "autopausa: aviso previo al borrado"):
            perfiles.meta_set(k113, f"{firma}|{_iso(ahora)}")
            logger.info("Autopausa: aviso previo al borrado de un perfil pausado.")
    elif dias >= DIAS_PAUSADO_AVISO and perfiles.meta_get(k60) != firma and dias < dias_aviso:
        if _decir(config, chat_id, f"⏸ Llevas {DIAS_PAUSADO_AVISO} días en pausa. Si no vuelves, a los {DIAS_PAUSADO_BORRADO} borro "
                                   "todo lo tuyo (te aviso otra vez antes). Para seguir: /reanudar.", "autopausa: 60 días"):
            perfiles.meta_set(k60, firma)
            logger.info("Autopausa: aviso de 60 días en pausa.")


def vigilar_actividad(config: dict, ahora: datetime | None = None) -> None:
    """
    D9. A quien lleva 30 días recibiendo avisos sin ninguna pulsación ni mensaje se le pregunta «¿Sigues
    buscando?»; sin respuesta en 7 días, pausa. Pausado 60 días, aviso; a los 113, aviso previo; a los 120,
    borrado (con copia, regla 7) y aviso al titular. El titular queda fuera de todo esto.

    Desde el 07-11 pasa también por aquí lo contrario: el perfil al que **no le llega nada**
    (`_vigilar_silencio`), que hasta ahora no se veía en ninguna pantalla.

    Se mira con la hora del contenedor y se escribe poco: la pregunta y los avisos dejan una marca en `meta`
    (no en el perfil: reescribirlo cambiaría `actualizado`, que es el reloj de la pausa).
    """
    ahora = _utc(ahora)
    titular = perfiles.titular()
    for p in perfiles.listar():
        if p["chat_id"] == titular or p["chat_id"] < 0:
            continue
        try:
            with _candado:
                if p["estado"] == "activo":
                    _vigilar_activo(config, p, ahora)
                    _vigilar_silencio(config, p, ahora)
                elif p["estado"] == "pausado":
                    _vigilar_pausado(config, p, ahora, titular)
        except perfiles.BaseNoDisponible:
            raise
        except Exception:
            logger.exception("Autopausa: ha fallado con un perfil.")


def responder_pregunta(chat_id: int, sigo: bool) -> str:
    """Los botones de «¿Sigues buscando?»: Sí lo deja como está, Pausar lo pausa. La pulsación ya cuenta como actividad."""
    perfiles.meta_set(f"autopausa_pregunta:{chat_id}", None)
    if sigo:
        return "✅ Perfecto, sigo buscando para ti."
    perfiles.cambiar_estado(chat_id, "pausado")
    return "⏸ En pausa: dejo de buscar y de avisarte. /reanudar cuando quieras."
