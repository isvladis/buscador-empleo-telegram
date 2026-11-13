# -*- coding: utf-8 -*-
"""
Pruebas de MP9 (avisos_perfil.py): cuándo y cuántos avisos le llegan a cada persona.

- Los tres modos (al momento, un resumen al día a las 9:00, dos a las 9:00 y a las 18:00), con la hora
  de Madrid y el reloj inyectado: la prueba no duerme, y los dos cambios de hora (25-10 y 28-03) son casos.
- El silencio de 22:00 a 8:00 (cruza la medianoche) y el tope diario: lo que no sale ahora espera en
  `pendientes` y sale en el siguiente envío; ninguna oferta se pierde.
- El resumen: 10 ofertas en un mensaje, cada una con su fila de botones, que marcan la oferta correcta del
  perfil correcto y cambian solo su fila.
- La caducidad de la cola (7 días sin verla), /top completo y /ajustes → Avisos.
- La autopausa (D9): los tres plazos, y el titular nunca.
- Dos perfiles con modos distintos en la misma corrida, sin cruzarse (regla 5).
- El titular por el mismo camino que los demás (P1): su fallo no les cuesta los avisos, y lo que no
  llega a Telegram espera en su cola en vez de quedarse «visto» y no volver nunca.
- El perfil al que no le llega NADA (07-11): con ofertas y cero avisos en 48 h se baja el listón a su
  techo y se le dice, o se avisa al titular si el listón no era la causa.

Ninguna prueba llama a Telegram ni a ningún portal: requests.post es un espía. Los chat_id y el token,
inventados. Las de lo importante llevan su control negativo (y pruebas/mutaciones_mp9.py rompe el código).

    python pruebas/test_avisos_perfil.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-avisos-perfil-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B, C = 1111111, 2222222, 3333333, 4444444
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
(DATOS / "config.json").write_text(json.dumps({
    "_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "hours_old": 38,
    "telegram_bot_token": "", "telegram_chat_id": ""}, indent=2), encoding="utf-8")

import requests  # noqa: E402

import almacen  # noqa: E402
import app  # noqa: E402
import avisos_perfil  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402

fallos: list[str] = []
hechas = 0
MADRID, UTC = ZoneInfo("Europe/Madrid"), timezone.utc


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos, codigo=200):
        self._datos, self.status_code, self.ok = datos, codigo, codigo < 400
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


class Espia:
    """requests.post: apunta lo que se habría enviado. `caido` hace fallar los sendMessage con un 500."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.n = 100
        self.caido = False

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        self.llamadas.append({"metodo": metodo, "cuerpo": json if json is not None else dict(data or {})})
        if metodo == "sendMessage":
            if self.caido:
                return Resp({"ok": False, "description": "Internal Server Error"}, 500)
            self.n += 1
            return Resp({"ok": True, "result": {"message_id": self.n}})
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        return Resp({"ok": True, "result": True})

    def mensajes(self, chat):
        return [c["cuerpo"] for c in self.llamadas if c["metodo"] == "sendMessage" and str(c["cuerpo"].get("chat_id")) == str(chat)]

    def textos(self, chat):
        return [m.get("text", "") for m in self.mensajes(chat)]

    def todo(self, chat):
        return "\n".join(self.textos(chat))

    def ediciones(self, chat):
        return [c["cuerpo"] for c in self.llamadas if c["metodo"] in ("editMessageReplyMarkup", "editMessageText")
                and str(c["cuerpo"].get("chat_id")) == str(chat)]

    def limpiar(self):
        self.llamadas.clear()


espia = Espia()
requests.post = espia.post
notificaciones.time.sleep = lambda *_: None


def config() -> dict:
    return config_mod.cargar_config()


def botones(teclado) -> list[str]:
    return [b["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]


def textos_de_botones(teclado) -> list[str]:
    return [b["text"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]


# ----------------------------------------------------------------------
# El reloj y el mundo
# ----------------------------------------------------------------------
def madrid(a, m, d, h=0, mi=0) -> datetime:
    """Esa hora de Madrid, en UTC y con zona (como la lee avisos_perfil)."""
    return datetime(a, m, d, h, mi, tzinfo=MADRID).astimezone(UTC)


def reloj(ahora: datetime) -> datetime:
    avisos_perfil.RELOJ = lambda: ahora
    return ahora


def iso(ahora: datetime) -> str:
    return avisos_perfil._iso(ahora)


_N = [0]
TITULOS = ["Soporte Técnico N1", "Analista SOC Junior", "Técnico Junior de Sistemas", "Administrador de Sistemas Junior",
           "Técnico de Sistemas", "Técnico Informático", "Técnico IT", "Técnico Helpdesk", "Técnico de Microinformática",
           "Junior SOC Analyst", "Técnico de Ciberseguridad", "Operador de Sistemas", "Técnico de Soporte", "Técnico de Redes"]


def oferta(titulo=None, empresa=None, visto=None, sitio="linkedin") -> dict:
    """Una oferta que el puntuador de la plantilla da por buena (≥ 5,5), vista en `visto` (UTC con zona)."""
    _N[0] += 1
    cuando = iso(visto) if visto else perfiles._ahora()
    return {"job_url": f"https://www.{sitio}.com/jobs/view/{_N[0]}", "title": titulo or TITULOS[_N[0] % len(TITULOS)],
            "company": empresa or f"Empresa {_N[0]}", "location": "Barcelona, Catalonia, Spain", "site": sitio,
            "is_remote": False, "description": "Puesto en Barcelona, trabajo en equipo y formación.",
            "relevancia": 6.5, "motivos_relevancia": [["motivo", 2.0]],
            "visto_por_primera_vez": cuando, "visto_por_ultima_vez": cuando}


def enlazar(chat, ofertas_):
    perfiles.aplicar_cambios(chat, escribir=[(o, 0, i) for i, o in enumerate(ofertas_)])


def nuevas(chat, n, visto, **k) -> list[dict]:
    """n ofertas enlazadas a ese perfil, como `ofertas_nuevas` de una corrida."""
    lista = [oferta(visto=visto, **k) for _ in range(n)]
    enlazar(chat, lista)
    return lista


def crear(chat, avisos=None, estado="activo", extra=None):
    datos = {**perfiles.datos_de_plantilla(), **(extra or {})}
    if avisos is not None:
        datos["avisos"] = avisos
    perfiles.guardar(chat, datos, estado)
    return perfiles.obtener(chat)


def corrida(chat, lista, posicion="2/3"):
    """Lo que hace la corrida con las ofertas nuevas de un perfil: sus avisos."""
    app._notificar_si_alto_encaje(lista, perfiles.obtener(chat), posicion=posicion)


def tick(ahora):
    return avisos_perfil.tick(config(), ahora)


def en_cola(chat) -> set[str]:
    return {p["url"] for p in perfiles.pendientes(chat)}


def urls_enviadas(chat) -> set[str]:
    """Las ofertas que ya se le han mandado a ese perfil (apuntadas en `avisos` como aviso)."""
    return {u for (u,) in perfiles.conexion().execute(
        "SELECT url FROM avisos WHERE chat_id = ? AND tipo = 'aviso'", (chat,))}


def pulsacion(datos, chat, markup=None, mensaje_id=77, n=[7000]):
    n[0] += 1
    msg = {"message_id": mensaje_id, "chat": {"id": chat, "type": "private"}}
    if markup is not None:
        msg["reply_markup"] = markup
    return {"update_id": n[0], "callback_query": {"id": "cb%d" % n[0], "data": datos, "from": {"id": chat}, "message": msg}}


def pulsa(datos, chat, markup=None):
    return bot.procesar(pulsacion(datos, chat, markup), config())


def mensaje(texto, chat, n=[0]):
    n[0] += 1
    return {"update_id": n[0], "message": {"message_id": n[0], "text": texto,
                                           "chat": {"id": chat, "type": "private"}, "from": {"id": chat}}}


def escribe(texto, chat):
    return bot.procesar(mensaje(texto, chat), config())


def borrar_todo():
    for tabla in ("pendientes", "avisos", "marcas", "ofertas_perfil", "conversacion"):
        perfiles.conexion().execute(f"DELETE FROM {tabla}")
    perfiles.conexion().execute("DELETE FROM meta WHERE clave LIKE 'resumen_ultimo:%' OR clave LIKE 'autopausa%'")
    perfiles.conexion().commit()
    espia.limpiar()
    espia.caido = False


# ----------------------------------------------------------------------
def caso_reloj_y_config():
    print("\n[1] Lo que ha elegido cada perfil, y el reloj de Madrid (silencio, día y turnos; octubre y marzo)")
    cfg = avisos_perfil.config_de
    comprueba(cfg({"datos": {}}).legado and cfg({"datos": {"avisos": {"modo": "instantaneo", "descubrimiento": True}}}).legado,
              "sin silencio ni tope y «al momento» es lo de siempre (legado)")
    comprueba(not cfg({"datos": {"avisos": {"silencio": [22, 8]}}}).legado and not cfg({"datos": {"avisos": {"max_dia": 10}}}).legado
              and not cfg({"datos": {"avisos": {"modo": "diario"}}}).legado, "   cualquier elección lo saca de ahí")
    rotos = {"modo": "cada_hora", "silencio": [25, 8], "max_dia": -3}
    c = cfg({"datos": {"avisos": rotos}})
    comprueba(c.modo == "instantaneo" and c.silencio is None and c.max_dia is None, "valores rotos = como si no estuvieran")
    comprueba(cfg({"datos": {"avisos": {"silencio": [8, 8], "max_dia": True}}}).legado, "   un silencio de 0 horas y un tope «True» tampoco valen")
    comprueba(cfg({"datos": {"avisos": {"silencio": "22-8", "max_dia": "10"}}}).legado, "   ni los textos")

    silencio = cfg({"datos": {"avisos": {"silencio": [22, 8]}}})
    # Invierno (UTC+1)
    casos = [(madrid(2026, 11, 5, 21, 59), False), (madrid(2026, 11, 5, 22, 0), True), (madrid(2026, 11, 5, 23, 59), True),
             (madrid(2026, 11, 6, 0, 0), True), (madrid(2026, 11, 6, 7, 59), True), (madrid(2026, 11, 6, 8, 0), False)]
    comprueba(all(avisos_perfil.en_silencio(silencio, t) is esperado for t, esperado in casos),
              "silencio de 22:00 a 8:00: cruza la medianoche (21:59 no, 22:00 sí … 7:59 sí, 8:00 no)")
    comprueba(avisos_perfil.en_silencio(silencio, datetime(2026, 11, 5, 21, 30, tzinfo=UTC))
              and not avisos_perfil.en_silencio(silencio, datetime(2026, 11, 5, 20, 30, tzinfo=UTC)),
              "   con el reloj del contenedor en UTC: 21:30 UTC son las 22:30 de Madrid en invierno")
    comprueba(avisos_perfil.en_silencio(silencio, datetime(2026, 7, 5, 20, 30, tzinfo=UTC))
              and not avisos_perfil.en_silencio(silencio, datetime(2026, 7, 5, 19, 30, tzinfo=UTC)),
              "   y 20:30 UTC en verano (UTC+2)")
    comprueba(avisos_perfil.en_silencio(silencio, datetime(2026, 11, 5, 21, 30)),
              "   un instante sin zona se toma como UTC")
    diurno = cfg({"datos": {"avisos": {"silencio": [13, 15]}}})
    comprueba(avisos_perfil.en_silencio(diurno, madrid(2026, 11, 5, 14, 0)) and not avisos_perfil.en_silencio(diurno, madrid(2026, 11, 5, 15, 0))
              and not avisos_perfil.en_silencio(diurno, madrid(2026, 11, 5, 3, 0)), "   y uno que no cruza (13–15) también")

    # El cambio de hora de octubre: el domingo 25-10 a las 3:00 CEST pasan a ser las 2:00 CET (el día dura 25 h).
    comprueba(datetime(2026, 10, 25, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=1)
              and datetime(2026, 10, 24, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=2),
              "octubre: el 24 aún es UTC+2 y el 25 ya UTC+1 (si Python o tzdata no lo supieran, el resto no valdría)")
    comprueba(avisos_perfil.inicio_del_dia(madrid(2026, 10, 25, 12)) == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
              and avisos_perfil.inicio_del_dia(madrid(2026, 10, 26, 12)) == datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
              "   la medianoche de Madrid del 25 es a las 22:00 UTC del 24 y la del 26 a las 23:00 UTC del 25 (día de 25 h)")
    comprueba(avisos_perfil.en_silencio(silencio, datetime(2026, 10, 24, 20, 0, tzinfo=UTC))
              and not avisos_perfil.en_silencio(silencio, datetime(2026, 10, 24, 19, 59, tzinfo=UTC)),
              "   el silencio del sábado empieza a las 20:00 UTC (22:00 CEST)")
    comprueba(not avisos_perfil.en_silencio(silencio, datetime(2026, 10, 25, 20, 59, tzinfo=UTC))
              and avisos_perfil.en_silencio(silencio, datetime(2026, 10, 25, 21, 0, tzinfo=UTC)),
              "   el del domingo empieza a las 21:00 UTC (22:00 CET): una hora más tarde en UTC que el del sábado")
    diario = cfg({"datos": {"avisos": {"modo": "diario"}}})
    comprueba(avisos_perfil.ultimo_turno(diario, datetime(2026, 10, 25, 7, 59, tzinfo=UTC)) == datetime(2026, 10, 24, 7, 0, tzinfo=UTC)
              and avisos_perfil.ultimo_turno(diario, datetime(2026, 10, 25, 8, 0, tzinfo=UTC)) == datetime(2026, 10, 25, 8, 0, tzinfo=UTC),
              "   el resumen del domingo 25-10 sale a las 8:00 UTC (9:00 CET) y el del sábado salió a las 7:00 UTC (9:00 CEST)")
    # Marzo: el domingo 28-03-2027 a las 2:00 CET pasan a ser las 3:00 CEST (el día dura 23 h).
    comprueba(datetime(2027, 3, 27, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=1)
              and datetime(2027, 3, 28, 12, tzinfo=MADRID).utcoffset() == timedelta(hours=2),
              "marzo: el 27 aún es UTC+1 y el 28 ya UTC+2")
    comprueba(avisos_perfil.inicio_del_dia(madrid(2027, 3, 28, 12)) == datetime(2027, 3, 27, 23, 0, tzinfo=UTC)
              and avisos_perfil.inicio_del_dia(madrid(2027, 3, 29, 12)) == datetime(2027, 3, 28, 22, 0, tzinfo=UTC),
              "   la medianoche del 28 es a las 23:00 UTC del 27 y la del 29 a las 22:00 UTC del 28 (día de 23 h)")
    comprueba(avisos_perfil.ultimo_turno(diario, datetime(2027, 3, 28, 6, 59, tzinfo=UTC)) == datetime(2027, 3, 27, 8, 0, tzinfo=UTC)
              and avisos_perfil.ultimo_turno(diario, datetime(2027, 3, 28, 7, 0, tzinfo=UTC)) == datetime(2027, 3, 28, 7, 0, tzinfo=UTC),
              "   el resumen del domingo 28-03 sale a las 7:00 UTC (9:00 CEST), una hora antes en UTC que el día anterior")
    dos = cfg({"datos": {"avisos": {"modo": "dos_diarios"}}})
    comprueba(avisos_perfil.ultimo_turno(dos, madrid(2027, 3, 28, 17, 59)) == madrid(2027, 3, 28, 9)
              and avisos_perfil.ultimo_turno(dos, madrid(2027, 3, 28, 18, 0)) == madrid(2027, 3, 28, 18)
              and avisos_perfil.ultimo_turno(dos, madrid(2027, 3, 28, 8, 59)) == madrid(2027, 3, 27, 18),
              "dos al día: 9:00 y 18:00 de Madrid, también el día del cambio")
    comprueba(avisos_perfil.ultimo_turno(cfg({"datos": {}}), madrid(2026, 11, 5, 12)) is None, "«al momento» no tiene turnos")


def caso_modos():
    print("\n[2] Los tres modos y el silencio, con su cola")
    borrar_todo()
    ahora0 = reloj(madrid(2026, 11, 5, 20, 0))

    # --- Al momento y sin elegir nada: lo de siempre, y nada va a la cola
    crear(A)
    lista = nuevas(A, 3, ahora0)
    corrida(A, lista)
    comprueba(len(espia.mensajes(A)) == 3 and all("🎯" in t for t in espia.textos(A)), "legado: 3 avisos sueltos, como siempre")
    comprueba(not en_cola(A), "   y la cola, vacía (no escribe nada nuevo)")
    espia.limpiar()
    corrida(A, nuevas(A, 12, ahora0))
    comprueba(len([m for m in espia.mensajes(A) if m.get("reply_markup")]) == 10 and "Y 2 más" in espia.textos(A)[-1] and not en_cola(A),
              "   con 12: 10 sueltas y el «Y 2 más» con enlaces de siempre, sin cola")

    # --- Silencio: de noche espera, a las 8:00 sale
    borrar_todo()
    crear(A, {"modo": "instantaneo", "silencio": [22, 8]})
    noche = reloj(madrid(2026, 11, 5, 23, 30))
    lista = nuevas(A, 4, noche)
    corrida(A, lista)
    comprueba(not espia.mensajes(A) and en_cola(A) == {o["job_url"] for o in lista}, "de noche no se manda nada y las 4 esperan")
    comprueba(tick(madrid(2026, 11, 6, 7, 59)) == 0 and not espia.mensajes(A), "   a las 7:59 siguen esperando")
    comprueba(tick(madrid(2026, 11, 6, 8, 0)) == 4, "   a las 8:00 salen las 4")
    mensajes = espia.mensajes(A)
    comprueba(len(mensajes) == 1 and "Lo que esperaba" in mensajes[0]["text"] and "4 ofertas" in mensajes[0]["text"],
              "   en un solo mensaje, «Lo que esperaba · 4 ofertas»")
    comprueba(not en_cola(A) and urls_enviadas(A) == {o["job_url"] for o in lista}, "   la cola queda vacía y quedan apuntadas como avisos")
    comprueba(tick(madrid(2026, 11, 6, 8, 1)) == 0 and len(espia.mensajes(A)) == 1, "   y no se repite")
    # Fuera del silencio, al momento: sale sin tocar la cola
    espia.limpiar()
    reloj(madrid(2026, 11, 6, 12, 0))
    lista = nuevas(A, 2, avisos_perfil.RELOJ())
    corrida(A, lista)
    comprueba(len(espia.mensajes(A)) == 2 and not en_cola(A), "a las 12:00 los avisos salen sueltos y sin pasar por la cola")
    # Una sola en la cola sale como un aviso normal
    espia.limpiar()
    reloj(madrid(2026, 11, 6, 22, 30))
    una = nuevas(A, 1, avisos_perfil.RELOJ())
    corrida(A, una)
    tick(madrid(2026, 11, 7, 8, 5))
    comprueba(len(espia.mensajes(A)) == 1 and "🎯" in espia.textos(A)[0], "una sola que esperaba sale como un aviso normal")

    # --- Un resumen al día
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    comprueba(perfiles.meta_get(f"resumen_ultimo:{A}") == iso(madrid(2026, 11, 5, 9)), "al elegirlo se da por hecho el turno de hoy")
    lista = nuevas(A, 5, avisos_perfil.RELOJ())
    corrida(A, lista)
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 5, "diario: la corrida de las 14:00 no manda nada, lo guarda")
    comprueba(tick(madrid(2026, 11, 5, 18, 0)) == 0 and tick(madrid(2026, 11, 5, 23, 0)) == 0, "   ni a las 18:00 ni de noche")
    comprueba(tick(madrid(2026, 11, 6, 8, 59)) == 0, "   ni a las 8:59")
    comprueba(tick(madrid(2026, 11, 6, 9, 0)) == 5, "   a las 9:00 sale el resumen con las 5")
    texto = espia.textos(A)[0]
    comprueba("Resumen de las 9:00" in texto and "5 ofertas" in texto, "   «Resumen de las 9:00 · 5 ofertas»")
    comprueba(tick(madrid(2026, 11, 6, 9, 1)) == 0 and tick(madrid(2026, 11, 6, 12, 0)) == 0, "   y no se vuelve a mandar ese día")
    espia.limpiar()
    reloj(madrid(2026, 11, 6, 14, 0))
    corrida(A, nuevas(A, 2, avisos_perfil.RELOJ()))
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 2 and tick(madrid(2026, 11, 6, 18, 0)) == 0,
              "   las de la tarde esperan, y las 18:00 no existen en «un resumen al día»")
    comprueba(tick(madrid(2026, 11, 7, 9, 0)) == 2, "   salen a las 9:00 del día siguiente")
    # El servicio estuvo parado al llegar el turno: sale en cuanto vuelve, una sola vez
    espia.limpiar()
    corrida(A, nuevas(A, 1, madrid(2026, 11, 7, 20)))
    comprueba(tick(madrid(2026, 11, 8, 13, 0)) == 1 and tick(madrid(2026, 11, 8, 13, 1)) == 0,
              "   el turno de las 9:00 que se pasó con el servicio parado sale a las 13:00, una vez")
    # Un turno sin nada que mandar también cuenta: lo que llegue a las 14:00 espera a mañana
    espia.limpiar()
    comprueba(tick(madrid(2026, 11, 9, 9, 0)) == 0, "   un turno con la cola vacía no manda nada")
    corrida(A, nuevas(A, 1, madrid(2026, 11, 9, 14)), "2/3")
    comprueba(tick(madrid(2026, 11, 9, 14, 5)) == 0 and not espia.mensajes(A),
              "   y marcado como pasado: lo que llega a las 14:00 no sale suelto")

    # --- Dos al día
    borrar_todo()
    crear(A, {"modo": "dos_diarios"})
    avisos_perfil.al_cambiar_modo(A, "dos_diarios", reloj(madrid(2026, 11, 5, 19, 0)))
    corrida(A, nuevas(A, 3, avisos_perfil.RELOJ()))
    comprueba(tick(madrid(2026, 11, 6, 9, 0)) == 3 and "Resumen de las 9:00" in espia.textos(A)[-1], "dos al día: a las 9:00 sale el primero")
    corrida(A, nuevas(A, 2, madrid(2026, 11, 6, 12)))
    comprueba(tick(madrid(2026, 11, 6, 17, 59)) == 0 and tick(madrid(2026, 11, 6, 18, 0)) == 2
              and "Resumen de las 18:00" in espia.textos(A)[-1], "   y a las 18:00 el segundo")
    comprueba(len(espia.mensajes(A)) == 2, "   en total dos mensajes")

    # --- El silencio de un modo de resumen retrasa el turno, no lo pierde
    borrar_todo()
    crear(A, {"modo": "diario", "silencio": [8, 10]})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    corrida(A, nuevas(A, 2, avisos_perfil.RELOJ()))
    comprueba(tick(madrid(2026, 11, 6, 9, 30)) == 0, "diario con silencio de 8 a 10: a las 9:30 sigue callado")
    comprueba(tick(madrid(2026, 11, 6, 10, 0)) == 2, "   y sale a las 10:00, al acabar")


def caso_tope():
    print("\n[3] El tope diario: nada se pierde, lo que sobra sale en el siguiente envío")
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 3})
    ahora = reloj(madrid(2026, 11, 6, 10, 0))
    lista = nuevas(A, 5, ahora)
    corrida(A, lista)
    comprueba(len(espia.mensajes(A)) == 3 and len(en_cola(A)) == 2, "tope de 3: salen 3 sueltas y 2 esperan")
    comprueba(urls_enviadas(A) | en_cola(A) == {o["job_url"] for o in lista} and not (urls_enviadas(A) & en_cola(A)),
              "   entre lo enviado y la cola están las 5, sin repetir")
    comprueba(tick(madrid(2026, 11, 6, 18, 0)) == 0 and tick(madrid(2026, 11, 6, 23, 59)) == 0, "   hoy no cabe nada más")
    espia.limpiar()
    corrida(A, nuevas(A, 2, madrid(2026, 11, 6, 20)))
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 4, "   lo que llega a las 20:00 también espera")
    comprueba(tick(madrid(2026, 11, 7, 0, 0)) == 3 and len(en_cola(A)) == 1, "a la medianoche de Madrid vuelve a haber hueco: salen 3")
    comprueba("Lo que esperaba" in espia.textos(A)[-1] and "1 más te espera" in espia.textos(A)[-1],
              "   «Lo que esperaba · 3 ofertas» y dice que 1 más espera")
    comprueba(tick(madrid(2026, 11, 7, 0, 1)) == 0, "   y ese día ya no caben más")
    comprueba(tick(madrid(2026, 11, 8, 0, 0)) == 1 and not en_cola(A), "   la última sale al día siguiente")
    comprueba(len(urls_enviadas(A)) == 7, "   en total las 7 llegaron, ninguna se perdió")

    # Más de 10 en una corrida: 10 sueltas y el resto a la cola (con sus botones cuando salga), no un «Y N más» sin botones
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 20})
    reloj(madrid(2026, 11, 6, 10, 0))
    corrida(A, nuevas(A, 12, avisos_perfil.RELOJ()))
    comprueba(len([m for m in espia.mensajes(A) if m.get("reply_markup")]) == 10 and len(en_cola(A)) == 2
              and not any("Y 2 más" in t for t in espia.textos(A)), "con tope 20 y 12 en una corrida: 10 sueltas y 2 a la cola")
    comprueba(tick(madrid(2026, 11, 6, 10, 1)) == 2 and len(urls_enviadas(A)) == 12, "   y las 2 salen enseguida, con botones")
    # La primera corrida de un perfil con elecciones: 5 como mucho y el resto queda como visto
    borrar_todo()
    crear(A, {"modo": "instantaneo", "silencio": [22, 8]})
    reloj(madrid(2026, 11, 6, 12, 0))
    lista = nuevas(A, 8, avisos_perfil.RELOJ())
    app._notificar_si_alto_encaje(lista, perfiles.obtener(A), posicion="2/3", primera=True)
    comprueba(len(espia.mensajes(A)) == 5 and not en_cola(A), "primera corrida: 5 como mucho, y las otras 3 no vuelven (quedan como vistas)")
    # Las ofertas para descubrir cuentan para el tope, y los avisos no se quedan sin su sitio
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 2}, extra={"umbral": 7.0})
    ahora = reloj(madrid(2026, 11, 6, 10, 0))
    perfil = perfiles.obtener(A)
    comprueba(avisos_perfil.hueco_para_descubrir(perfil, 0, ahora) == 2 and avisos_perfil.hueco_para_descubrir(perfil, 2, ahora) == 0,
              "con tope 2, el descubrimiento deja sitio a los avisos de la corrida (2 avisos → 0 descubrimientos)")
    perfiles.registrar_aviso(A, "x1", "https://x/1", tipo="descubrimiento", avisada=iso(ahora - timedelta(hours=1)))
    comprueba(avisos_perfil.hueco_para_descubrir(perfil, 0, ahora) == 1, "   y los que ya se mandaron hoy gastan tope")
    # El día de ayer no cuenta (medianoche de Madrid, no de UTC)
    perfiles.registrar_aviso(A, "x2", "https://x/2", tipo="aviso", avisada=iso(madrid(2026, 11, 5, 23, 30)))
    comprueba(avisos_perfil.enviados_hoy(A, ahora) == 1, "   y uno de las 23:30 de ayer no cuenta hoy")

    # Cambio de hora: el día del 25-10 dura 25 h y el tope es de ese día de Madrid, no del día UTC
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 2})
    temprano = reloj(datetime(2026, 10, 24, 22, 30, tzinfo=UTC))   # 00:30 CEST del domingo 25
    corrida(A, nuevas(A, 2, temprano))
    comprueba(len(espia.mensajes(A)) == 2, "25-10: a las 00:30 (22:30 UTC del sábado) salen 2")
    espia.limpiar()
    tarde = reloj(datetime(2026, 10, 25, 21, 30, tzinfo=UTC))      # 22:30 CET del mismo domingo
    corrida(A, nuevas(A, 1, tarde))
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 1,
              "   a las 22:30 del mismo día de Madrid (21:30 UTC del domingo, otro día UTC) el tope sigue lleno")
    comprueba(tick(datetime(2026, 10, 25, 22, 59, tzinfo=UTC)) == 0 and tick(datetime(2026, 10, 25, 23, 0, tzinfo=UTC)) == 1,
              "   y vuelve a haber hueco a las 23:00 UTC, la medianoche de Madrid tras el cambio")
    # Marzo: el día dura 23 h
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 2})
    corrida(A, nuevas(A, 2, reloj(datetime(2027, 3, 27, 23, 30, tzinfo=UTC))))   # 00:30 CET del domingo 28
    espia.limpiar()
    corrida(A, nuevas(A, 1, reloj(datetime(2027, 3, 28, 21, 30, tzinfo=UTC))))   # 23:30 CEST del mismo domingo
    comprueba(not espia.mensajes(A), "28-03: a las 23:30 CEST del mismo día de Madrid el tope sigue lleno")
    comprueba(tick(datetime(2027, 3, 28, 21, 59, tzinfo=UTC)) == 0 and tick(datetime(2027, 3, 28, 22, 0, tzinfo=UTC)) == 1,
              "   y a las 22:00 UTC (medianoche CEST) sale lo que esperaba")


def caso_botones_del_resumen():
    print("\n[4] Los botones del resumen: la oferta correcta, del perfil correcto, y solo cambia su fila")
    borrar_todo()
    crear(A, {"modo": "diario"})
    crear(B, {"modo": "diario"})
    crear(C)
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    avisos_perfil.al_cambiar_modo(B, "diario", avisos_perfil.RELOJ())
    de_a = nuevas(A, 4, avisos_perfil.RELOJ(), empresa=None)
    de_b = nuevas(B, 3, avisos_perfil.RELOJ())
    corrida(A, de_a)
    corrida(B, de_b)
    tick(madrid(2026, 11, 6, 9, 0))
    mensaje_a, mensaje_b = espia.mensajes(A), espia.mensajes(B)
    comprueba(len(mensaje_a) == 1 and len(mensaje_b) == 1, "cada uno recibe su resumen, en un mensaje")
    teclado = mensaje_a[0]["reply_markup"]
    filas = teclado["inline_keyboard"]
    comprueba(len(filas) == 4 and all(len(f) == 3 for f in filas), "4 ofertas, 4 filas de 3 botones")
    comprueba(all(len(d.encode()) <= 64 for d in botones(teclado)) and max(len(d) for d in botones(teclado)) <= 14,
              "   cada callback_data cabe de sobra en los 64 bytes (%d)" % max(len(d) for d in botones(teclado)))
    comprueba([f[0]["text"] for f in filas] == ["1 ⭐", "2 ⭐", "3 ⭐", "4 ⭐"], "   cada fila lleva el número de su oferta")
    texto = mensaje_a[0]["text"]
    comprueba(all(f"{i}. " in texto for i in (1, 2, 3, 4)) and "href=" in texto, "   y el texto las numera, con enlace")
    # Los botones llevan la clave de la oferta de su fila
    for i, fila in enumerate(filas, 1):
        url = perfiles.aviso(A, fila[0]["callback_data"].split(":")[1])["url"]
        comprueba(f"{i}. " in texto and perfiles.aviso(A, fila[0]["callback_data"].split(":")[1])["titulo"] in texto
                  and url in {o["job_url"] for o in de_a}, f"   la fila {i} apunta a una oferta de A")
    # Pulsar ⭐ en la fila 2: marca la oferta 2 de A, y solo cambia su fila
    clave2 = filas[1][0]["callback_data"].split(":")[1]
    url2 = perfiles.aviso(A, clave2)["url"]
    espia.limpiar()
    comprueba(pulsa("f:" + clave2, A, teclado) == "favorito", "⭐ en la fila 2 se atiende")
    comprueba(url2 in perfiles.marcas(A)["favoritas"] and not perfiles.marcas(B)["favoritas"], "   marca esa oferta, en A")
    nuevo = espia.ediciones(A)[-1]["reply_markup"]
    comprueba(nuevo["inline_keyboard"][0] == filas[0] and nuevo["inline_keyboard"][-2:] == filas[2:] or nuevo["inline_keyboard"][-1] == filas[3],
              "   las otras filas se quedan como estaban")
    comprueba(any(t.startswith("2 ⭐ Te interesa") for t in textos_de_botones(nuevo)), "   y la 2 pasa a «2 ⭐ Te interesa …» (con su número)")
    comprueba(len([t for t in textos_de_botones(nuevo) if "↩️" in t]) == 1, "   con su «Deshacer»")
    # Deshacer: vuelve a su fila de tres
    deshacer = [b["callback_data"] for f in nuevo["inline_keyboard"] for b in f if b["callback_data"].startswith("d:")][0]
    comprueba(pulsa(deshacer, A, nuevo) == "deshacer" and url2 not in perfiles.marcas(A)["favoritas"], "↩️ Deshacer quita la marca")
    despues = espia.ediciones(A)[-1]["reply_markup"]
    comprueba(despues == teclado, "   y el teclado vuelve a ser exactamente el del principio")
    # 🙈 en la fila 3: despliega los motivos justo debajo de su fila; elegir uno la cierra
    clave3 = filas[2][2]["callback_data"].split(":")[1]
    pulsa("o:" + clave3, A, teclado)
    con_motivos = espia.ediciones(A)[-1]["reply_markup"]
    filas_3 = [f for f in con_motivos["inline_keyboard"] if any(b["callback_data"].endswith(":" + clave3) for b in f)]
    comprueba(len(con_motivos["inline_keyboard"]) == 3 + len(filas_3) and len(filas_3) >= 4 and filas_3[0][0]["text"].startswith("3 🙈"),
              "🙈 en la 3 despliega los motivos en su sitio (%d filas suyas) y el resto no se mueve" % len(filas_3))
    comprueba(con_motivos["inline_keyboard"][0] == filas[0] and con_motivos["inline_keyboard"][1] == filas[1]
              and con_motivos["inline_keyboard"][-1] == filas[3], "   las filas 1, 2 y 4 siguen donde estaban")
    motivo = [d for d in botones(con_motivos) if d.startswith("mp:")][0]
    pulsa(motivo, A, con_motivos)
    cerrado = espia.ediciones(A)[-1]["reply_markup"]["inline_keyboard"]
    comprueba(len(cerrado) == 5 and cerrado[2][0]["text"].startswith("3 🙈 Descartada: no es mi perfil"),
              "   elegir el motivo la deja en una fila «3 🙈 Descartada: no es mi perfil» + Deshacer")
    comprueba(perfiles.aviso(A, clave3)["motivo"] == "no es mi perfil" and perfiles.aviso(A, clave3)["respuesta"] == "oculta",
              "   y se anota el motivo en esa oferta")
    comprueba(len(botones({"inline_keyboard": con_motivos["inline_keyboard"]})) <= 100, "   (nunca pasa de los 100 botones de un teclado)")
    # Aislamiento: los botones de A no valen en B, ni los de B en A
    espia.limpiar()
    comprueba(pulsa("f:" + clave2, B, teclado) == "desconocida" and not perfiles.marcas(B)["favoritas"],
              "los botones del resumen de A pulsados desde B no marcan nada en B")
    clave_b = botones(mensaje_b[0]["reply_markup"])[0].split(":")[1]
    comprueba(pulsa("f:" + clave_b, A) == "desconocida" and pulsa("f:" + clave_b, B, mensaje_b[0]["reply_markup"]) == "favorito",
              "   ni los de B desde A; en su sitio sí funcionan")
    comprueba(perfiles.aviso(B, clave_b)["url"] in {o["job_url"] for o in de_b}, "   y marcan una oferta de B")
    for c in (A, B):
        otro = B if c == A else A
        comprueba(not (urls_enviadas(c) & urls_enviadas(otro)), "   las ofertas enviadas a %s no son las de la otra persona" % ("A" if c == A else "B"))
    # Un aviso suelto (sin números) sigue cambiando todo su teclado, como siempre
    espia.limpiar()
    corrida(C, nuevas(C, 1, avisos_perfil.RELOJ()))
    suelto = espia.mensajes(C)[0]["reply_markup"]
    clave_c = botones(suelto)[0].split(":")[1]
    pulsa("f:" + clave_c, C, suelto)
    comprueba(len(espia.ediciones(C)[-1]["reply_markup"]["inline_keyboard"]) == 2
              and not textos_de_botones(espia.ediciones(C)[-1]["reply_markup"])[0][0].isdigit(),
              "un aviso suelto: su teclado se cambia entero y sin números, como antes de MP9")

    # El resumen de 10 con los peores títulos cabe en un mensaje y en los límites de Telegram
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    largas = []
    for i in range(14):
        o = oferta(titulo=TITULOS[i % len(TITULOS)] + " " + "X" * 4000, empresa="E" * 500)
        o["description"] = "Puesto en Barcelona. " + "texto " * 800
        largas.append(o)
    enlazar(A, largas)
    perfiles.encolar_pendientes(A, [(o["job_url"], 6.5) for o in largas], iso(avisos_perfil.RELOJ()))
    tick(madrid(2026, 11, 6, 9, 0))
    mensajes = espia.mensajes(A)
    comprueba(len(mensajes) == 1 and len(mensajes[0]["text"]) <= 4096, "14 en la cola con títulos de 4.000 letras: un resumen de 10 en un mensaje de ≤ 4.096 (%d)" % len(mensajes[0]["text"]))
    comprueba(len(botones(mensajes[0]["reply_markup"])) == 30 and "4 más te esperan" in mensajes[0]["text"] and len(en_cola(A)) == 4,
              "   con sus 30 botones, el aviso de «4 más» y 4 que siguen esperando")
    # Si los bloques no cupieran en uno, cada mensaje lleva sus ofertas enteras y sus botones
    ofertas_ = [{"job_url": f"https://x.test/{i}", "title": "T" * 300, "company": "C" * 100, "alertas": ["a" * 300] * 2,
                 "location": "L" * 100, "relevancia": 6.0} for i in range(10)]
    partes = notificaciones.mensajes_de_resumen(ofertas_, "📬 Resumen", 0)
    comprueba(sum(h - d for _, d, h in partes) == 10 and all(len(t) <= 4096 for t, _, _ in partes),
              "con campos al máximo siguen cabiendo (%d mensaje/s de ≤ 4.096)" % len(partes))
    antes = notificaciones.MAX_CARACTERES_TELEGRAM
    notificaciones.MAX_CARACTERES_TELEGRAM = 900
    try:
        partes = notificaciones.mensajes_de_resumen(ofertas_, "📬 Resumen", 3)
    finally:
        notificaciones.MAX_CARACTERES_TELEGRAM = antes
    comprueba(len(partes) > 1 and [h for _, _, h in partes][-1] == 10 and all(partes[i][2] == partes[i + 1][1] for i in range(len(partes) - 1)),
              "   y si un día no cupieran en uno, se reparten sin dejar ninguna fuera ni partir una")
    comprueba("más te esperan" in partes[-1][0] and all("más te esperan" not in t for t, _, _ in partes[:-1]), "   con el aviso de «más» solo en el último")


def caso_cola_y_caducidad():
    print("\n[5] La cola caduca a los 7 días sin verla, y no vuelve a enseñar lo ya visto")
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    ahora = madrid(2026, 11, 16, 9, 0)
    lo_nuevo = nuevas(A, 2, ahora - timedelta(hours=2))
    en_el_limite = nuevas(A, 1, ahora - timedelta(days=6, hours=23))
    vieja = nuevas(A, 1, ahora - timedelta(days=7, hours=1))
    clasificada = nuevas(A, 1, ahora - timedelta(hours=3))
    ya_vista = nuevas(A, 1, ahora - timedelta(hours=3))
    sin_enlace = nuevas(A, 1, ahora - timedelta(hours=3))
    todas = lo_nuevo + en_el_limite + vieja + clasificada + ya_vista + sin_enlace
    perfiles.encolar_pendientes(A, [(o["job_url"], 6.0) for o in todas], iso(ahora - timedelta(days=8)))
    perfiles.fijar_marca(A, clasificada[0]["job_url"], "favorito", {"title": "x"}, {"origen": "telegram"})
    tb.registrar_aviso({**ya_vista[0], "relevancia": 6.0}, A, tipo="top")
    perfiles.aplicar_cambios(A, quitar=[sin_enlace[0]["job_url"]])
    comprueba(len(en_cola(A)) == 7, "la cola trae 7 (2 buenas, 1 en el límite, 1 vieja, 1 clasificada, 1 ya vista, 1 sin enlace)")
    reloj(ahora)
    comprueba(avisos_perfil.en_cola(A, ahora) == 3, "solo 3 le esperan de verdad")
    espia.limpiar()
    tick(ahora)
    texto = espia.textos(A)[0]
    enviadas = urls_enviadas(A) - {ya_vista[0]["job_url"]}
    comprueba(enviadas == {o["job_url"] for o in lo_nuevo + en_el_limite}, "el resumen lleva solo esas 3 (la de 6 d 23 h cuenta, la de 7 d 1 h no)")
    comprueba("3 ofertas" in texto and not en_cola(A), "   y no queda nada en la cola: lo caducado se quitó")
    comprueba(len(espia.mensajes(A)) == 1, "   sin ningún mensaje sobre lo que caducó (se quita sin avisar)")
    # Dos de la cola con la misma vacante: una
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    gemelas = nuevas(A, 2, madrid(2026, 11, 5, 15), titulo="Soporte Técnico N1", empresa="Misma SL")
    otra = nuevas(A, 1, madrid(2026, 11, 5, 15))
    perfiles.encolar_pendientes(A, [(o["job_url"], 6.0) for o in gemelas + otra], iso(madrid(2026, 11, 5, 15)))
    tick(madrid(2026, 11, 6, 9, 0))
    comprueba("2 ofertas" in espia.textos(A)[0] and len(urls_enviadas(A)) == 2, "dos publicaciones de la misma vacante salen como una")
    # Las que se enseñan con /top salen de la cola
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    # Vistas AHORA de verdad, no el 05-11: /top mira los últimos 7 días con el reloj real
    # (telegram_bot._top no usa avisos_perfil.RELOJ), y con la fecha fija esta comprobación y la de
    # «Te esperan 3 más» pasaron a rojo el 12-11-2026 a las 15:00, siete días justos después, sin que
    # nadie tocara nada (visto ese día en HEAD limpio, en 1518c36 y en 6ca78e3). La cola sigue en el 05-11.
    lista = nuevas(A, 3, datetime.now(UTC))
    perfiles.encolar_pendientes(A, [(o["job_url"], 6.0) for o in lista], iso(madrid(2026, 11, 5, 15)))
    espia.limpiar()
    escribe("/top", A)
    comprueba(not en_cola(A) and "esperan" not in espia.todo(A), "/top las enseña y las quita de la cola (no se repiten en el resumen)")
    tick(madrid(2026, 11, 6, 9, 0))
    comprueba(len(espia.mensajes(A)) == 4 and not any("Resumen" in t for t in espia.textos(A)), "   y el resumen de las 9:00 no sale vacío")


def caso_fallos_de_red():
    print("\n[6] Si Telegram falla, nada se pierde ni cuenta como enviado")
    borrar_todo()
    crear(A, {"modo": "instantaneo", "silencio": [22, 8], "max_dia": 5})
    reloj(madrid(2026, 11, 5, 23, 0))
    lista = nuevas(A, 3, avisos_perfil.RELOJ())
    corrida(A, lista)
    espia.caido = True
    comprueba(tick(madrid(2026, 11, 6, 8, 0)) == 0, "con Telegram caído, el volcado de las 8:00 no envía nada")
    comprueba(len(en_cola(A)) == 3 and not urls_enviadas(A), "   las 3 siguen en la cola y no quedan apuntadas como avisos")
    comprueba(avisos_perfil.enviados_hoy(A, madrid(2026, 11, 6, 8, 0)) == 0, "   ni gastan tope")
    espia.caido = False
    comprueba(tick(madrid(2026, 11, 6, 8, 1)) == 3 and not en_cola(A), "   cuando vuelve, salen las 3")
    # Un aviso suelto que falla en horario normal va a la cola
    borrar_todo()
    crear(A, {"modo": "instantaneo", "max_dia": 5})
    reloj(madrid(2026, 11, 6, 12, 0))
    espia.caido = True
    lista = nuevas(A, 2, avisos_perfil.RELOJ())
    corrida(A, lista)
    comprueba(len(en_cola(A)) == 2 and avisos_perfil.enviados_hoy(A) == 0, "un aviso que no se puede enviar se encola, y no cuenta")
    espia.caido = False
    comprueba(tick(madrid(2026, 11, 6, 12, 5)) == 2, "   y sale en cuanto se puede")
    # Resumen a medias: el primer mensaje llega y el segundo no
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    ofertas_ = [oferta(visto=avisos_perfil.RELOJ()) for _ in range(3)]
    enlazar(A, ofertas_)
    antes = notificaciones.MAX_CARACTERES_TELEGRAM
    notificaciones.MAX_CARACTERES_TELEGRAM = 420
    try:
        perfiles.encolar_pendientes(A, [(o["job_url"], 6.0) for o in ofertas_], iso(avisos_perfil.RELOJ()))
        envios = [0]
        post = espia.post

        def a_medias(url, json=None, **k):
            if url.endswith("sendMessage"):
                envios[0] += 1
                if envios[0] == 2:
                    return Resp({"ok": False}, 500)
            return post(url, json=json, **k)

        requests.post = a_medias
        tick(madrid(2026, 11, 6, 9, 0))
    finally:
        requests.post = espia.post
        notificaciones.MAX_CARACTERES_TELEGRAM = antes
    comprueba(0 < len(urls_enviadas(A)) < 3 and len(en_cola(A)) == 3 - len(urls_enviadas(A)),
              "un resumen en dos mensajes con el segundo caído: lo que llegó sale de la cola y lo demás espera (%d y %d)"
              % (len(urls_enviadas(A)), len(en_cola(A))))


def caso_descubrimiento():
    print("\n[7] El descubrimiento (MP6) respeta el silencio, el modo y el tope")
    titulos = ["Técnico de Sistemas", "Técnico Informático", "Técnico IT", "Técnico Helpdesk", "Técnico de Microinformática",
               "Técnico de Soporte", "Técnico de Ciberseguridad", "Operador de Sistemas"]

    def con(avisos, ahora, altas=0):
        borrar_todo()
        crear(A, avisos, extra={"umbral": 7.0})
        reloj(ahora)
        lista = [oferta(t, f"Borde {i}", visto=ahora) for i, t in enumerate(titulos)]
        lista += [oferta("Soporte Técnico N1", f"Alta {i}", visto=ahora) for i in range(altas)]
        enlazar(A, lista)
        corrida(A, lista)
        return [t for t in espia.textos(A) if "¿Te encaja?" in t]

    comprueba(len(con({"modo": "instantaneo", "silencio": [22, 8], "max_dia": 10}, madrid(2026, 11, 6, 12))) == 2,
              "al momento, de día y con tope de sobra: 2 de descubrimiento, como siempre")
    comprueba(not con({"modo": "instantaneo", "silencio": [22, 8], "max_dia": 10}, madrid(2026, 11, 6, 23)), "en silencio: ninguna")
    comprueba(not con({"modo": "diario"}, madrid(2026, 11, 6, 12)), "en un modo de resumen: ninguna (un aviso suelto contradice lo elegido)")
    comprueba(len(con({"modo": "instantaneo", "max_dia": 10}, madrid(2026, 11, 6, 12))) == 2, "con solo un tope holgado: 2")
    comprueba(len(con({"modo": "instantaneo", "max_dia": 1}, madrid(2026, 11, 6, 12))) == 1, "con tope 1: solo 1, que ocupa el sitio")
    comprueba(len(con({"modo": "instantaneo", "max_dia": 2}, madrid(2026, 11, 6, 12), altas=2)) == 0
              and len(espia.mensajes(A)) == 2 and all("🎯" in t for t in espia.textos(A)),
              "con tope 2 y 2 avisos en la corrida: los 2 avisos, ningún descubrimiento (los avisos no pierden su sitio)")
    comprueba(len(con({"modo": "instantaneo", "descubrimiento": True}, madrid(2026, 11, 6, 23))) == 2,
              "legado (sin silencio ni tope): igual que antes de MP9, también de noche")


def caso_dos_perfiles():
    print("\n[8] Dos perfiles con modos distintos en la misma corrida, sin cruzarse")
    borrar_todo()
    crear(TITULAR)
    crear(A, {"modo": "diario"})
    crear(B, {"modo": "instantaneo", "silencio": [22, 8], "max_dia": 2})
    ahora = reloj(madrid(2026, 11, 5, 14, 0))
    avisos_perfil.al_cambiar_modo(A, "diario", ahora)
    comunes = [oferta(visto=ahora) for _ in range(4)]
    solo_a = [oferta(titulo="Analista SOC Junior", empresa="Secreta de A", visto=ahora)]
    solo_b = [oferta(titulo="Soporte Técnico N1", empresa="Secreta de B", visto=ahora)]
    enlazar(TITULAR, comunes)
    enlazar(A, comunes + solo_a)
    enlazar(B, comunes + solo_b)
    espia.limpiar()
    app._notificar_perfiles([(perfiles.obtener(TITULAR), {"ofertas_nuevas": comunes}, False),
                             (perfiles.obtener(A), {"ofertas_nuevas": comunes + solo_a}, False),
                             (perfiles.obtener(B), {"ofertas_nuevas": comunes + solo_b}, False)], True)
    comprueba(len(espia.mensajes(TITULAR)) == 4, "el titular (sin elegir nada) recibe sus 4 sueltas, como siempre")
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 5, "A (un resumen al día) no recibe nada ahora y guarda sus 5")
    comprueba(len(espia.mensajes(B)) == 2 and len(en_cola(B)) == 3, "B (tope 2) recibe 2 y guarda 3")
    comprueba(not en_cola(TITULAR), "el titular no guarda nada")
    comprueba(en_cola(A) <= {o["job_url"] for o in comunes + solo_a} and en_cola(B) <= {o["job_url"] for o in comunes + solo_b},
              "cada cola solo tiene ofertas de su perfil")
    comprueba("Secreta de A" not in espia.todo(B) and "Secreta de B" not in espia.todo(A), "   y nada de uno aparece en el chat del otro")
    espia.limpiar()
    tick(madrid(2026, 11, 6, 9, 0))
    comprueba(len(espia.mensajes(A)) == 1 and "Resumen de las 9:00" in espia.textos(A)[0] and "5 ofertas" in espia.textos(A)[0],
              "a las 9:00 A recibe su resumen de 5")
    comprueba(len(espia.mensajes(B)) == 1 and "Lo que esperaba" in espia.textos(B)[0],
              "   y B, que ya tiene hueco otro día, lo que esperaba (no un «Resumen de las 9:00»)")
    comprueba(not espia.mensajes(TITULAR), "   y el titular, nada")
    comprueba("Secreta de B" not in espia.todo(A) and "Secreta de A" not in espia.todo(B), "   sin cruzarse")
    comprueba(urls_enviadas(A) == {o["job_url"] for o in comunes + solo_a}, "   A ha recibido exactamente las suyas")
    # El ritmo y los topes de uno no gastan los del otro
    comprueba(avisos_perfil.enviados_hoy(B, madrid(2026, 11, 6, 9, 5)) == 2 and len(en_cola(B)) == 1
              and avisos_perfil.enviados_hoy(A, madrid(2026, 11, 6, 9, 5)) == 5,
              "cada uno cuenta lo suyo para su tope")


def caso_ajustes():
    print("\n[9] /ajustes → Avisos: el modo, el silencio y el tope, y quien no toca nada no cambia")
    borrar_todo()
    crear(A)
    perfil = perfiles.obtener(A)
    version = perfil["version"]
    pulsa("aj1:av", A)
    texto = espia.ediciones(A)[-1]["text"]
    comprueba("al momento" in texto and "sin silencio" in texto and "sin tope" in texto, "Avisos dice cómo está hoy: al momento, sin silencio, sin tope")
    teclado = espia.ediciones(A)[-1]["reply_markup"]
    comprueba({"aj1:am:i", "aj1:am:d", "aj1:am:2", "aj1:as:1", "aj1:as:0", "aj1:at:5", "aj1:at:10", "aj1:at:20", "aj1:at:0",
               "aj1:ad:0", "aj1:aj"} <= set(botones(teclado)), "   con los botones de modo, silencio, tope, descubrimiento y volver")
    comprueba(all(len(d.encode()) <= 64 for d in botones(teclado)), "   todos de ≤ 64 bytes")
    comprueba(pulsa("aj1:as:0", A) == "boton:aj1:silencio_0" and perfiles.obtener(A)["version"] == version,
              "«Sin silencio» en quien no tiene silencio no escribe nada")
    pulsa("aj1:as:1", A)
    comprueba(perfiles.obtener(A)["datos"]["avisos"]["silencio"] == [22, 8] and "22:00–8:00" in espia.ediciones(A)[-1]["text"],
              "🌙 22:00–8:00 lo guarda y lo dice")
    pulsa("aj1:at:5", A)
    comprueba(perfiles.obtener(A)["datos"]["avisos"]["max_dia"] == 5 and "5 al día" in espia.ediciones(A)[-1]["text"], "el tope de 5 lo guarda")
    comprueba("✅ 5" in textos_de_botones(espia.ediciones(A)[-1]["reply_markup"]), "   y marca el elegido")
    pulsa("aj1:at:7", A)
    comprueba(perfiles.obtener(A)["datos"]["avisos"]["max_dia"] == 5, "un tope que no está en la lista no se acepta")
    pulsa("aj1:am:x", A)
    comprueba(perfiles.obtener(A)["datos"]["avisos"].get("modo") != "x", "   ni un modo que no existe")
    comprueba(pulsa("aj1:am:d", A) == "boton:aj1:modo_diario" and perfiles.obtener(A)["datos"]["avisos"]["modo"] == "diario", "un resumen al día lo guarda")
    comprueba(perfiles.meta_get(f"resumen_ultimo:{A}") is not None, "   y deja hecho el turno de hoy (no vuelca la cola a media tarde)")
    pulsa("aj1:am:2", A)
    comprueba(perfiles.obtener(A)["datos"]["avisos"]["modo"] == "dos_diarios" and "9:00 y 18:00" in espia.ediciones(A)[-1]["text"], "dos al día, también")
    pulsa("aj1:at:0", A)
    pulsa("aj1:as:0", A)
    pulsa("aj1:am:i", A)
    comprueba(avisos_perfil.config_de(perfiles.obtener(A)).legado, "y quitándolo todo vuelve a ser legado")
    comprueba(perfiles.obtener(A)["datos"]["avisos"].get("descubrimiento", True) is not False, "   sin tocar lo del descubrimiento")
    # Lo de una persona no llega a otra
    crear(B)
    pulsa("aj1:at:10", B)
    comprueba("max_dia" not in perfiles.obtener(A)["datos"]["avisos"] and perfiles.obtener(B)["datos"]["avisos"]["max_dia"] == 10,
              "el tope de B no cambia el de A")
    # Quien pasa a resumen no recibe la cola de golpe a media tarde
    borrar_todo()
    crear(A, {"modo": "instantaneo", "silencio": [22, 8]})
    reloj(madrid(2026, 11, 6, 23, 0))
    corrida(A, nuevas(A, 2, avisos_perfil.RELOJ()))
    reloj(madrid(2026, 11, 7, 14, 0))
    pulsa("aj1:am:d", A)
    comprueba(tick(madrid(2026, 11, 7, 14, 1)) == 0 and tick(madrid(2026, 11, 8, 9, 0)) == 2,
              "quien pasa a «un resumen al día» a las 14:00 recibe lo que esperaba a las 9:00 del día siguiente")


def caso_top():
    print("\n[10] /top completo: 5 mejores de los últimos 7 días, sin clasificar, con botones")
    borrar_todo()
    crear(A, {"modo": "diario"})
    ahora = datetime.now(UTC)
    lista = []
    for i, dias in enumerate((0.1, 1, 3, 6.5, 8, 2, 4)):
        o = oferta(titulo=TITULOS[i], empresa=f"Top {i}", visto=ahora - timedelta(days=dias))
        o["relevancia"] = 9.0 - i * 0.2
        lista.append(o)
    enlazar(A, lista)
    perfiles.fijar_marca(A, lista[5]["job_url"], "favorito", {"title": lista[5]["title"]}, {"origen": "telegram"})
    perfiles.fijar_marca(A, lista[6]["job_url"], "oculta", {"title": lista[6]["title"]}, {"origen": "telegram"})
    espia.limpiar()
    escribe("/top", A)
    textos = espia.todo(A)
    comprueba("Tus 4 mejores" in textos and "últimos 7 días" in textos, "7 vistas, 1 de hace 8 días, 1 con ⭐ y 1 con 🙈 → salen 4 (%s)" % textos.split(chr(10))[0][:60])
    comprueba("Top 4" not in textos, "   no sale la de hace 8 días")
    comprueba("Top 5" not in textos and "Top 6" not in textos, "   ni la que tiene ⭐ ni la que tiene 🙈")
    con_botones = [m for m in espia.mensajes(A) if m.get("reply_markup")]
    comprueba(len(con_botones) == 4 and all(botones(m["reply_markup"])[0].split(":")[0] == "f" for m in con_botones),
              "   cada una con sus tres botones de siempre")
    # Con más de 5: salen 5
    borrar_todo()
    crear(A)
    mas = []
    for i in range(8):
        o = oferta(titulo=TITULOS[i], empresa=f"Mas {i}", visto=ahora - timedelta(hours=i))
        mas.append(o)
    enlazar(A, mas)
    espia.limpiar()
    escribe("/top", A)
    comprueba("Tus 5 mejores" in espia.todo(A) and len([m for m in espia.mensajes(A) if m.get("reply_markup")]) == 5, "con 8 candidatas, 5")
    comprueba(pulsa("f:" + botones([m for m in espia.mensajes(A) if m.get("reply_markup")][0]["reply_markup"])[0].split(":")[1], A) == "favorito",
              "   y sus botones funcionan")
    # Solo las suyas
    crear(B)
    ajena = nuevas(B, 2, ahora)
    espia.limpiar()
    escribe("/top", A)
    comprueba(not any(o["company"] in espia.todo(A) for o in ajena), "ninguna oferta de otro perfil")
    # Con lo que espera
    borrar_todo()
    crear(A, {"modo": "diario"})
    avisos_perfil.al_cambiar_modo(A, "diario", reloj(madrid(2026, 11, 5, 14, 0)))
    cola = nuevas(A, 8, datetime.now(UTC))  # vistas ahora de verdad: ver la nota de «/top las enseña» más arriba
    perfiles.encolar_pendientes(A, [(o["job_url"], 6.0) for o in cola], iso(avisos_perfil.RELOJ()))
    espia.limpiar()
    escribe("/top", A)
    comprueba("Te esperan 3 más para tu próximo envío" in espia.todo(A), "dice cuántas le esperan en la cola (8 − las 5 que acaba de ver = 3)")


def caso_autopausa():
    print("\n[11] Autopausa (D9): 30 días sin pulsar → pregunta; 7 más → pausa; pausado 60/113/120 días; el titular nunca")
    borrar_todo()
    crear(TITULAR)
    crear(C)
    ahora = reloj(madrid(2026, 11, 20, 10, 0))

    def poner(chat, actividad=None, actualizado=None):
        if actividad is not None:
            perfiles.conexion().execute("UPDATE perfiles SET ultima_actividad = ? WHERE chat_id = ?", (iso(actividad), chat))
        if actualizado is not None:
            perfiles.conexion().execute("UPDATE perfiles SET actualizado = ? WHERE chat_id = ?", (iso(actualizado), chat))
        perfiles.conexion().commit()

    def avisar(chat, hace_dias):
        perfiles.registrar_aviso(chat, "k%d" % _N[0], "https://x.test/%d" % _N[0], titulo="T", avisada=iso(ahora - timedelta(days=hace_dias)))
        _N[0] += 1

    # No hace nada con quien es reciente o no recibe avisos
    poner(C, actividad=ahora - timedelta(days=29, hours=20))
    avisar(C, 3)
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(not espia.mensajes(C), "29 días y 20 h sin pulsar: aún nada")
    poner(C, actividad=ahora - timedelta(days=31))
    borrar_avisos = perfiles.conexion().execute("DELETE FROM avisos WHERE chat_id = ?", (C,))
    perfiles.conexion().commit()
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(not espia.mensajes(C), "31 días sin pulsar pero sin haber recibido ningún aviso: no se le pregunta (no hay qué ignorar)")
    avisar(C, 40)
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(not espia.mensajes(C), "   ni si el último aviso fue hace 40 días")
    avisar(C, 3)
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 20, 3, 0))
    comprueba(not espia.mensajes(C), "   ni de madrugada (las 3:00): los mensajes de autopausa van de 9:00 a 21:00")
    avisos_perfil.vigilar_actividad(config(), ahora)
    textos = espia.textos(C)
    comprueba(len(textos) == 1 and "¿Sigues buscando trabajo?" in textos[0] and "30 días" in textos[0], "31 días recibiendo avisos sin pulsar: «¿Sigues buscando?»")
    comprueba(botones(espia.mensajes(C)[0]["reply_markup"]) == ["aj1:ap:s", "aj1:ap:p"], "   con [Sí] y [Pausar]")
    comprueba("enhorabuena" in textos[0], "   dicho sin reproche (quien no pulsa a lo mejor ya encontró trabajo)")
    avisos_perfil.vigilar_actividad(config(), ahora + timedelta(hours=5))
    comprueba(len(espia.mensajes(C)) == 1, "   una sola vez")
    avisos_perfil.vigilar_actividad(config(), ahora + timedelta(days=6, hours=23))
    comprueba(perfiles.obtener(C)["estado"] == "activo" and len(espia.mensajes(C)) == 1, "a los 6 días y 23 h sigue activo")
    avisos_perfil.vigilar_actividad(config(), ahora + timedelta(days=7))
    comprueba(perfiles.obtener(C)["estado"] == "pausado" and "Te he puesto en pausa" in espia.textos(C)[-1], "a los 7 días sin respuesta, pausa, con su mensaje")
    comprueba("/reanudar" in espia.textos(C)[-1], "   que dice cómo volver")
    # Contestar: Sí
    perfiles.cambiar_estado(C, "activo")
    perfiles.meta_set(f"autopausa_pregunta:{C}", None)
    espia.limpiar()
    poner(C, actividad=ahora - timedelta(days=31))
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(len(espia.mensajes(C)) == 1, "se le vuelve a preguntar si sigue sin hacer nada")
    comprueba(pulsa("aj1:ap:s", C) == "boton:aj1:autopausa_s" and perfiles.obtener(C)["estado"] == "activo"
              and perfiles.meta_get(f"autopausa_pregunta:{C}") is None, "«Sí, sigo buscando»: sigue activo y la pregunta se olvida")
    comprueba(perfiles.obtener(C)["ultima_actividad"][:10] == perfiles._ahora()[:10], "   y la pulsación cuenta como actividad")
    comprueba(pulsa("aj1:ap:p", C) == "boton:aj1:autopausa_p" and perfiles.obtener(C)["estado"] == "pausado", "«Pausar» pausa")
    # Contestar con cualquier mensaje o botón también cuenta
    perfiles.cambiar_estado(C, "activo")
    espia.limpiar()
    poner(C, actividad=ahora - timedelta(days=31))
    avisos_perfil.vigilar_actividad(config(), ahora)
    poner(C, actividad=ahora + timedelta(days=1))
    avisos_perfil.vigilar_actividad(config(), ahora + timedelta(days=8))
    comprueba(perfiles.obtener(C)["estado"] == "activo" and perfiles.meta_get(f"autopausa_pregunta:{C}") is None,
              "quien hace algo (un mensaje, un botón) después de la pregunta no se pausa, aunque no pulse el «Sí»")

    # El titular, nunca
    espia.limpiar()
    poner(TITULAR, actividad=ahora - timedelta(days=400))
    avisar(TITULAR, 2)
    avisos_perfil.vigilar_actividad(config(), ahora + timedelta(days=100))
    comprueba(not espia.mensajes(TITULAR) and perfiles.obtener(TITULAR)["estado"] == "activo", "el titular, 400 días sin actividad: ni pregunta ni pausa")
    perfiles.cambiar_estado(TITULAR, "pausado")
    poner(TITULAR, actualizado=ahora - timedelta(days=300), actividad=ahora - timedelta(days=300))
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(perfiles.obtener(TITULAR) is not None and not espia.mensajes(TITULAR), "   ni pausado 300 días: ni aviso ni borrado")
    perfiles.cambiar_estado(TITULAR, "activo")

    # Pausado: 60 días, aviso previo a los 113, borrado a los 120 con 7 días de aviso
    borrar_todo()
    perfiles.guardar(C, perfiles.datos_de_plantilla(), "pausado")
    base = ahora
    poner(C, actividad=base - timedelta(days=70), actualizado=base - timedelta(days=59, hours=20))
    avisos_perfil.vigilar_actividad(config(), base)
    comprueba(not espia.mensajes(C), "pausado 59 días y 20 h: nada")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(hours=5))
    textos = espia.textos(C)
    comprueba(len(textos) == 1 and "60 días en pausa" in textos[0] and "120" in textos[0], "a los 60 días: aviso, que dice que a los 120 se borra")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=5))
    comprueba(len(espia.mensajes(C)) == 1, "   una sola vez")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=53, hours=9))
    comprueba(len(espia.mensajes(C)) == 2 and "se borra en 7 días" in espia.textos(C)[-1], "a los 113 días: «Tu perfil se borra en 7 días»")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=58))
    comprueba(perfiles.obtener(C) is not None and len(espia.mensajes(C)) == 2, "   a los 119 días sigue ahí")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=61))
    comprueba(perfiles.obtener(C) is None, "a los 120 días, tras el aviso de hace 7 días, se borra")
    comprueba("he borrado tu perfil" in espia.textos(C)[-1], "   y se lo dice")
    comprueba(any("pausado 120 días se ha borrado" in t for t in espia.textos(TITULAR)), "   y avisa al titular")
    comprueba(not any(str(C) in t for t in espia.textos(TITULAR)), "   sin decir quién (ni su chat_id)")
    copias = list((DATOS / "borrados").glob("*")) if (DATOS / "borrados").exists() else []
    comprueba(len(copias) == 1, "   dejando una copia en data/borrados (regla 7)")
    # Sin aviso previo no se borra, aunque pasen 200 días
    perfiles.guardar(C, perfiles.datos_de_plantilla(), "pausado")
    poner(C, actividad=base - timedelta(days=300), actualizado=base - timedelta(days=200))
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), base)
    comprueba(perfiles.obtener(C) is not None and "se borra en 7 días" in espia.textos(C)[-1], "pausado 200 días y sin aviso previo: se avisa, no se borra")
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=6, hours=20))
    comprueba(perfiles.obtener(C) is not None, "   ni 6 días y 20 h después")
    # Volver a escribir reinicia los plazos
    poner(C, actividad=base + timedelta(days=5))
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=8))
    comprueba(perfiles.obtener(C) is not None, "si la persona escribe de nuevo, los plazos empiezan de cero")
    # Un pausado reciente y uno bloqueado no se tocan
    perfiles.guardar(B, perfiles.datos_de_plantilla(), "bloqueado")
    poner(B, actividad=base - timedelta(days=500), actualizado=base - timedelta(days=500))
    antes = len(espia.llamadas)
    avisos_perfil.vigilar_actividad(config(), base + timedelta(days=10))
    comprueba(perfiles.obtener(B) is not None and not espia.mensajes(B), "un perfil bloqueado no se toca")


def caso_ficha_y_encuesta():
    print("\n[12] La encuesta deja valores para quien entra, y a nadie más se le cambia")
    import telegram_encuesta as enc

    comprueba(avisos_perfil.SILENCIO_POR_DEFECTO == [22, 8] and avisos_perfil.MAX_DIA_POR_DEFECTO == 10
              and avisos_perfil.OFERTAS_POR_RESUMEN == 10, "los provisionales son los del esquema del plan: 22–8, 10 al día, 10 por resumen")
    comprueba(avisos_perfil.DIAS_EN_COLA == 7 and avisos_perfil.DIAS_SIN_PULSAR == 30 and avisos_perfil.DIAS_PARA_CONTESTAR == 7
              and avisos_perfil.DIAS_PAUSADO_AVISO == 60 and avisos_perfil.DIAS_PAUSADO_BORRADO == 120, "   y los plazos, los del contrato")


def caso_titular_y_lo_no_entregado():
    print("\n[13] El titular: su fallo no deja a los demás sin avisos, y lo que no sale espera en su cola (P1)")
    borrar_todo()
    crear(TITULAR)
    reloj(madrid(2026, 11, 6, 12, 0))
    suyas = nuevas(TITULAR, 2, avisos_perfil.RELOJ())
    espia.caido = True
    app._notificar_si_alto_encaje(suyas, perfiles.obtener(TITULAR))
    comprueba(en_cola(TITULAR) == {o["job_url"] for o in suyas},
              "con Telegram caído, los 2 avisos del titular esperan en su cola")
    comprueba(not urls_enviadas(TITULAR),
              "   y no quedan apuntados como avisados (si no, «no repetir» los entierra 14 días y no vuelven)")
    espia.caido = False
    comprueba(tick(madrid(2026, 11, 6, 12, 5)) == 2 and not en_cola(TITULAR),
              "   cuando Telegram vuelve, el planificador los manda")
    comprueba(urls_enviadas(TITULAR) == {o["job_url"] for o in suyas}, "   y entonces sí quedan apuntados")

    # Lo que no se entrega porque la entrega revienta (no porque Telegram diga que no) también se encola.
    borrar_todo()
    crear(TITULAR)
    reloj(madrid(2026, 11, 6, 12, 0))
    suyas = nuevas(TITULAR, 2, avisos_perfil.RELOJ())
    original_envio = notificaciones.notificar_ofertas_alto_encaje

    def revienta_el_envio(*a, **k):
        raise RuntimeError("Telegram ha contestado algo que no se esperaba")

    notificaciones.notificar_ofertas_alto_encaje = revienta_el_envio
    try:
        app._notificar_si_alto_encaje(suyas, perfiles.obtener(TITULAR))
    finally:
        notificaciones.notificar_ofertas_alto_encaje = original_envio
    comprueba(en_cola(TITULAR) == {o["job_url"] for o in suyas},
              "una excepción al entregar tampoco pierde las ofertas: quedan en la cola")

    # Y el reparto: si al titular le revienta la entrega, los demás reciben los suyos y la corrida sigue.
    borrar_todo()
    crear(TITULAR)
    crear(A, {"modo": "instantaneo", "max_dia": 5})
    reloj(madrid(2026, 11, 6, 12, 0))
    del_titular = nuevas(TITULAR, 1, avisos_perfil.RELOJ())
    de_a = nuevas(A, 1, avisos_perfil.RELOJ())
    original = app._notificar_si_alto_encaje

    def revienta(ofertas, perfil, posicion=None, primera=False):
        if perfil["chat_id"] == TITULAR:
            raise RuntimeError("el aviso del titular ha reventado")
        return original(ofertas, perfil, posicion, primera)

    app._notificar_si_alto_encaje = revienta
    fallo = None
    try:
        app._notificar_perfiles([(perfiles.obtener(TITULAR), {"ofertas_nuevas": del_titular}, False),
                                 (perfiles.obtener(A), {"ofertas_nuevas": de_a}, False)], True)
    except Exception as e:  # noqa: BLE001 — justo lo que la corrida NO debe ver
        fallo = e
    finally:
        app._notificar_si_alto_encaje = original
    comprueba(fallo is None,
              "si la entrega del titular revienta, la corrida no falla (antes su excepción salía del bucle)")
    comprueba(len(espia.mensajes(A)) == 1 and urls_enviadas(A) == {de_a[0]["job_url"]},
              "   y A recibe el suyo igual (antes se quedaba sin él)")

    # El titular solo recibe en las corridas automáticas, como siempre.
    borrar_todo()
    crear(TITULAR)
    reloj(madrid(2026, 11, 6, 12, 0))
    suyas = nuevas(TITULAR, 1, avisos_perfil.RELOJ())
    app._notificar_perfiles([(perfiles.obtener(TITULAR), {"ofertas_nuevas": suyas}, False)], False)
    comprueba(not espia.mensajes(TITULAR) and not en_cola(TITULAR),
              "una corrida no automática no le avisa ni le encola nada, como antes de MP10")


def caso_perfil_callado():
    print("\n[14] Un perfil con ofertas y CERO avisos: se detecta, y si el listón es la causa se baja (07-11)")
    borrar_todo()
    crear(TITULAR)

    def con_techo(chat, techo, umbral, cuantas=25, creado="2026-11-01T10:00:00"):
        """Un perfil con `cuantas` ofertas cuya mejor nota es `techo`, su listón en `umbral` y ya veterano."""
        crear(chat, {"modo": "instantaneo"}, extra={"umbral": umbral, "plan": {"umbral": umbral}})
        lista = [oferta(visto=avisos_perfil.RELOJ()) for _ in range(cuantas)]
        enlazar(chat, lista)
        conn = perfiles.conexion()
        conn.execute("UPDATE ofertas_perfil SET relevancia = ? WHERE chat_id = ?", (techo, chat))
        conn.execute("UPDATE perfiles SET creado = ? WHERE chat_id = ?", (creado, chat))
        conn.commit()
        return lista

    ahora = reloj(madrid(2026, 11, 10, 12, 0))
    con_techo(A, 5.0, 5.5)
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), ahora)
    comprueba(perfiles.obtener(A)["datos"]["umbral"] == 5.0,
              "listón 5,5 con techo 5,0 y 48 h sin avisos: se baja a su techo")
    comprueba((perfiles.obtener(A)["datos"].get("plan") or {}).get("umbral") == 5.0, "   también en su plan (es lo que mira la corrida)")
    comprueba(len(espia.mensajes(A)) == 1 and "No te estaba llegando nada" in espia.todo(A) and "5,0" in espia.todo(A),
              "   y se le dice, con el número nuevo")
    comprueba(not espia.mensajes(TITULAR), "   al titular no se le dice nada: lo de esa persona es suyo")
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 10, 13, 0))
    # Nada a NADIE: sin la marca, a la hora siguiente el listón ya está en su techo, así que la segunda
    # pasada se iría por la rama del titular y le contaría lo mismo cada hora (lo cazó su mutación).
    comprueba(not espia.llamadas, "   y a la hora siguiente no se escribe a nadie, ni a la persona ni al titular")

    # Control: el listón ya está en su techo, así que bajarlo no arregla nada -> lo sabe el titular
    borrar_todo()
    crear(TITULAR)
    con_techo(B, 5.0, 5.0)
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 10, 12, 0))
    comprueba(perfiles.obtener(B)["datos"]["umbral"] == 5.0, "si el listón ya está en su techo, no se toca")
    comprueba(not espia.mensajes(B) and len(espia.mensajes(TITULAR)) == 1 and "sin recibir ningún aviso" in espia.todo(TITULAR),
              "   y se avisa al titular, no a la persona")
    comprueba(str(B) not in espia.todo(TITULAR), "   sin su chat_id (regla 3)")

    # Control: con pocas ofertas, callarse es «todavía no hay nada» y no se dice nada
    borrar_todo()
    crear(TITULAR)
    con_techo(C, 5.0, 5.5, cuantas=5)
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 10, 12, 0))
    comprueba(perfiles.obtener(C)["datos"]["umbral"] == 5.5 and not espia.llamadas,
              "con 5 ofertas no se toca nada ni se dice nada")

    # Control: si ha recibido un aviso hace 10 h, no está callado (y la marca se olvida)
    borrar_todo()
    crear(TITULAR)
    lista = con_techo(A, 5.0, 5.5)
    perfiles.meta_set("silencio_sin_avisos:%d" % A, iso(madrid(2026, 11, 9, 12, 0)))
    tb.registrar_aviso({**lista[0], "relevancia": 5.0}, A, avisada=iso(madrid(2026, 11, 10, 2, 0)))
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 10, 12, 0))
    comprueba(perfiles.obtener(A)["datos"]["umbral"] == 5.5 and not espia.mensajes(A),
              "con un aviso de hace 10 h no está callado: no se toca su listón")
    comprueba(perfiles.meta_get("silencio_sin_avisos:%d" % A) is None, "   y la marca se olvida, para poder volver a avisar si recae")

    # Control: fuera de las horas de trato no se escribe a nadie
    borrar_todo()
    crear(TITULAR)
    con_techo(A, 5.0, 5.5)
    espia.limpiar()
    avisos_perfil.vigilar_actividad(config(), madrid(2026, 11, 10, 4, 0))
    comprueba(not espia.llamadas and perfiles.obtener(A)["datos"]["umbral"] == 5.5,
              "a las 4:00 no se escribe ni se toca nada (las horas de trato son 9-21)")


def caso_orden_de_la_cola():
    print("\n[15] A igualdad de nota sale lo más reciente, no lo que tenga el enlace antes en el alfabeto")
    # La función sola, con los cuatro dominios de verdad. Hasta el 11-11 el desempate era el enlace:
    # es.indeed.com < es.jooble.org < europa.eu < www.linkedin.com, es decir, elegía el portal.
    def o(url, nota, publicada=None):
        return {"job_url": url, "relevancia": nota, "date_posted": publicada}

    lista = [o("https://es.indeed.com/a", 5.0), o("https://www.linkedin.com/b", 5.0),
             o("https://es.jooble.org/c", 5.0, "2026-11-08"), o("https://es.jooble.org/d", 5.0, "2026-11-10"),
             o("https://europa.eu/e", 6.0, "2026-11-01")]
    creada = {"https://es.indeed.com/a": "2026-11-10T04:31:00", "https://www.linkedin.com/b": "2026-11-11T13:32:00",
              "https://es.jooble.org/c": "2026-11-11T04:31:00", "https://es.jooble.org/d": "2026-11-11T04:31:00",
              "https://europa.eu/e": "2026-11-09T10:00:00"}
    orden = [x["job_url"][8:] for x in avisos_perfil.orden_de_salida(lista, creada)]
    comprueba(orden[0] == "europa.eu/e", "la mejor nota va primero aunque sea la más antigua")
    comprueba(orden[1:] == ["www.linkedin.com/b", "es.jooble.org/d", "es.jooble.org/c", "es.indeed.com/a"],
              "a igualdad: la que entró más tarde; en la misma corrida, la publicada más tarde; y la más vieja, la última")
    comprueba(orden == [x["job_url"][8:] for x in avisos_perfil.orden_de_salida(list(reversed(lista)), creada)],
              "   y el orden no depende de cómo venga la lista")
    de_antes = [x["job_url"][8:] for x in sorted(lista, key=lambda x: (-(x.get("relevancia") or 0), x["job_url"]))]
    comprueba(de_antes[1] == "es.indeed.com/a" and de_antes[-1] == "www.linkedin.com/b" and de_antes != orden,
              "control: con el desempate de antes iba primero Indeed y última LinkedIn, justo al revés que por fecha")

    # Y entera, como le pasaba al perfil que más recibe: silencio de noche, tope al día, y en la cola más
    # de lo que cabe, todo con la misma nota. Tres tandas: LinkedIn a las 23:00, Indeed a las 3:00 y
    # LinkedIn otra vez a las 6:00. A las 8:00 caben 3.
    borrar_todo()
    crear(A, {"modo": "instantaneo", "silencio": [22, 8], "max_dia": 3})
    igual = dict(titulo="Técnico de Soporte")
    reloj(madrid(2026, 11, 5, 23, 0))
    viejas = nuevas(A, 2, avisos_perfil.RELOJ(), sitio="linkedin", **igual)
    corrida(A, viejas)
    reloj(madrid(2026, 11, 6, 3, 0))
    medias = nuevas(A, 3, avisos_perfil.RELOJ(), sitio="indeed", **igual)
    corrida(A, medias)
    reloj(madrid(2026, 11, 6, 6, 0))
    recientes = nuevas(A, 2, avisos_perfil.RELOJ(), sitio="linkedin", **igual)
    corrida(A, recientes)
    comprueba(not espia.mensajes(A) and len(en_cola(A)) == 7, "de noche las 7 esperan")
    notas = {x["relevancia"] for x in avisos_perfil._preparar(perfiles.obtener(A), sorted(en_cola(A)))}
    comprueba(len(notas) == 1, "   y las 7 valen lo mismo (%s): el desempate es todo el orden" % notas)
    comprueba(tick(reloj(madrid(2026, 11, 6, 8, 0))) == 3, "a las 8:00 salen las 3 del tope")
    enviadas = urls_enviadas(A)
    comprueba({x["job_url"] for x in recientes} <= enviadas,
              "las dos de las 6:00 van dentro, aunque su enlace sea el último del alfabeto")
    comprueba(len(enviadas & {x["job_url"] for x in medias}) == 1 and not enviadas & {x["job_url"] for x in viejas},
              "   la tercera es de la tanda de las 3:00, y las de las 23:00 son las que esperan")
    comprueba(en_cola(A) == {x["job_url"] for x in viejas + medias} - enviadas and len(en_cola(A)) == 4,
              "   nada se pierde: las otras 4 siguen en la cola")
    comprueba(tick(reloj(madrid(2026, 11, 7, 8, 0))) == 3 and tick(reloj(madrid(2026, 11, 8, 8, 0))) == 1 and not en_cola(A),
              "   y salen los días siguientes si no llega nada más nuevo")


CASOS = [caso_reloj_y_config, caso_modos, caso_tope, caso_botones_del_resumen, caso_cola_y_caducidad, caso_fallos_de_red,
         caso_descubrimiento, caso_dos_perfiles, caso_ajustes, caso_top, caso_autopausa, caso_ficha_y_encuesta,
         caso_titular_y_lo_no_entregado, caso_perfil_callado, caso_orden_de_la_cola]


def main() -> int:
    perfiles.conexion()
    for caso in CASOS:
        try:
            caso()
        except Exception as e:  # noqa: BLE001 — un caso que revienta es una comprobación que falla, no un fallo de la prueba
            import traceback

            traceback.print_exc()
            comprueba(False, f"{caso.__name__} ha reventado antes de acabar: {type(e).__name__}: {e}")
    print()
    if fallos:
        print("FALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("TODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
