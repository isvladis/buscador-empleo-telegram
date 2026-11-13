# -*- coding: utf-8 -*-
"""
Pruebas de la encuesta inicial y de /ajustes (MP8, ver telegram_encuesta.py).

Ninguna prueba llama a Telegram, a Nominatim ni a ningún portal: requests.post se sustituye por un espía
que apunta lo que se habría enviado, y el geocodificador por una tabla de ciudades. El token y los chat_id
son inventados. Lo que sí es de verdad es el router del bot, la base (en un directorio temporal) y el
generador del plan (MP7) con el catálogo real.

    python pruebas/test_encuesta.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-encuesta-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B, C = 1111111, 2222222, 3333333, 4444444
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
CONFIG_EN_DISCO = {"_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "busqueda_automatica_horas": 1,
                   "hours_old": 38, "telegram_bot_token": "", "telegram_chat_id": ""}
(DATOS / "config.json").write_text(json.dumps(CONFIG_EN_DISCO, indent=2), encoding="utf-8")

import requests  # noqa: E402

import almacen  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import reglas  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402
import telegram_encuesta as enc  # noqa: E402
from perfilado import generador  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos, codigo=200):
        self._datos, self.status_code = datos, codigo
        self.ok = codigo < 400
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


class Espia:
    """Sustituye a requests.post y contesta como la API. `rechazar` hace fallar un editMessageText."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.mensaje_id = 100
        self.rechazar_edicion = False

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        cuerpo = json if json is not None else dict(data or {})
        self.llamadas.append({"metodo": metodo, "cuerpo": cuerpo})
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        if metodo == "editMessageText" and self.rechazar_edicion:
            return Resp({"ok": False, "description": "Bad Request: message to edit not found"}, 400)
        if metodo == "sendMessage":
            self.mensaje_id += 1
            return Resp({"ok": True, "result": {"message_id": self.mensaje_id}})
        return Resp({"ok": True, "result": True})

    def de(self, metodo, chat=None):
        return [c["cuerpo"] for c in self.llamadas if c["metodo"] == metodo
                and (chat is None or str(c["cuerpo"].get("chat_id")) == str(chat))]

    def textos(self, chat):
        return [c.get("text", "") for c in self.de("sendMessage", chat)]

    def limpiar(self):
        self.llamadas.clear()


espia = Espia()
requests.post = espia.post
notificaciones.time.sleep = lambda s: None

# El geocodificador de pruebas: una tabla de ciudades. Apunta lo que le preguntan.
CIUDADES = {
    "valencia": {"nombre": "Valencia", "region": "Comunitat Valenciana", "lat": 39.4699123, "lon": -0.3763123},
    "malaga": {"nombre": "Málaga", "region": "Andalucía", "lat": 36.7212, "lon": -4.4214},
    "madrid": {"nombre": "Madrid", "region": "Comunidad de Madrid", "lat": 40.4168, "lon": -3.7038},
    "barcelona": {"nombre": "Barcelona", "region": "Cataluña", "lat": 41.3874, "lon": 2.1686},
}
preguntas_al_geocodificador: list = []


def geocodificar_de_mentira(texto, coordenadas):
    preguntas_al_geocodificador.append((texto, coordenadas))
    if coordenadas is not None:
        return dict(CIUDADES["valencia"]) if abs(coordenadas[0] - 39.47) < 0.5 else None
    normal = "".join(c for c in (texto or "").casefold() if c.isalpha())
    normal = normal.replace("á", "a").replace("é", "e")
    return dict(CIUDADES[normal]) if normal in CIUDADES else None


enc.GEOCODIFICAR = geocodificar_de_mentira
lanzados: list[int] = []
enc.SERVICIO["lanzar_arranque"] = lanzados.append


def config() -> dict:
    return config_mod.cargar_config()


def mensaje(texto=None, chat=TITULAR, ubicacion=None, n=[0]):
    n[0] += 1
    cuerpo = {"message_id": n[0], "chat": {"id": chat, "type": "private"}, "from": {"id": chat}}
    if ubicacion:
        cuerpo["location"] = {"latitude": ubicacion[0], "longitude": ubicacion[1]}
    else:
        cuerpo["text"] = texto
    return {"update_id": n[0], "message": cuerpo}


def escribe(texto, chat=TITULAR):
    return bot.procesar(mensaje(texto, chat), config())


def comparte(lat, lon, chat=TITULAR):
    return bot.procesar(mensaje(chat=chat, ubicacion=(lat, lon)), config())


def pulsacion(datos, chat, mensaje_id, n=[5000]):
    n[0] += 1
    return {"update_id": n[0], "callback_query": {"id": "cb%d" % n[0], "data": datos, "from": {"id": chat},
                                                  "message": {"message_id": mensaje_id,
                                                              "chat": {"id": chat, "type": "private"}}}}


def id_del_paso(chat) -> int | None:
    conv = perfiles.conversacion(chat)
    return conv["mensaje_id"] if conv else None


def pulsa(datos, chat=TITULAR, mensaje_id="el_ultimo"):
    """Pulsa un botón del mensaje de la conversación (o de otro, si se dice)."""
    if mensaje_id == "el_ultimo":
        mensaje_id = id_del_paso(chat)
    return bot.procesar(pulsacion(datos, chat, mensaje_id), config())


def borrador(chat) -> dict:
    conv = perfiles.conversacion(chat)
    return conv["borrador"] if conv else {}


def paso(chat) -> str | None:
    return borrador(chat).get("paso")


def botones_de(cuerpo: dict) -> list[str]:
    return [b["callback_data"] for fila in (cuerpo.get("reply_markup") or {}).get("inline_keyboard", []) for b in fila]


def ultima_pantalla(chat) -> dict:
    """El último sendMessage o editMessageText con teclado inline a ese chat."""
    con = [c["cuerpo"] for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")
           and str(c["cuerpo"].get("chat_id")) == str(chat)
           and "inline_keyboard" in (c["cuerpo"].get("reply_markup") or {})]
    return con[-1] if con else {}


def textos_todos(chat) -> str:
    return "\n".join(c["cuerpo"].get("text", "") for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")
                     and str(c["cuerpo"].get("chat_id")) == str(chat))


def avisos_de(chat, tipo) -> list[str]:
    """Las URL que se le han apuntado a ese chat en `avisos` con ese tipo (la API de perfiles no lo enseña)."""
    return [f[0] for f in perfiles.conexion().execute("SELECT url FROM avisos WHERE chat_id = ? AND tipo = ?", (chat, tipo))]


def invitar(plantilla="encuesta") -> str:
    escribe("/invitar")
    pulsa(f"adm1:inv:{plantilla}", TITULAR, mensaje_id=77)
    enlaces = re.findall(r"https://t\.me/bot_de_prueba\?start=([A-Za-z0-9_-]+)", textos_todos(TITULAR))
    return enlaces[-1] if enlaces else ""  # el último: el que acaba de crear


def entrar(chat) -> None:
    codigo = invitar("encuesta")
    escribe("/start " + codigo, chat)


def oferta(n, titulo, empresa="ACME", ubicacion="Valencia, Spain", relevancia=6.0):
    cuando = datetime.now().isoformat(timespec="seconds")
    return {"job_url": f"https://www.linkedin.com/jobs/view/{n}", "title": titulo, "company": empresa,
            "location": ubicacion, "site": "linkedin", "is_remote": False,
            "description": f"Descripción de la oferta {n}.", "relevancia": relevancia,
            "motivos_relevancia": [["motivo %d" % n, 2.0]], "visto_por_primera_vez": cuando,
            "visto_por_ultima_vez": cuando}


# ----------------------------------------------------------------------
# Dos personas contestando a la vez
# ----------------------------------------------------------------------
GUION_A = [  # administrativa en Valencia, 3-5 años, inglés intermedio
    ("p", "ob1:0:go"), ("p", "ob1:se:t:admin"), ("p", "ob1:se:n"),
    ("p", "ob1:pu:t:admin.administrativo"), ("p", "ob1:pu:t:admin.auxiliar"), ("p", "ob1:pu:n"),
    ("p", "ob1:ex:s:3_5"), ("p", "ob1:ex:n"),
    ("p", "ob1:fo:s:fp_sup"), ("t", "CCNA, Prevención de riesgos"),
    ("t", "Valencia"), ("p", "ob1:ci:y"), ("p", "ob1:di:s:25"),
    ("p", "ob1:mo:t:presencial"), ("p", "ob1:mo:t:hibrido"), ("p", "ob1:mo:n"),
    ("p", "ob1:id:t:ca"), ("p", "ob1:id:t:en"), ("p", "ob1:id:n"), ("p", "ob1:id:i:intermedio"),
    ("p", "ob1:jo:t:completa"), ("p", "ob1:jo:n"), ("p", "ob1:sa:s:18000"),
    ("t", "contabilidad, a3"), ("t", "comercial, ett"), ("t", "Empresa Mala"),
    ("p", "ob1:av:n"),
]
GUION_B = [  # camarero en Málaga, sin experiencia, sin prácticas
    ("p", "ob1:0:go"), ("p", "ob1:se:t:hosteleria"), ("p", "ob1:se:n"),
    ("p", "ob1:pu:t:hosteleria.camarero"), ("p", "ob1:pu:n"),
    ("p", "ob1:ex:s:ninguna"), ("p", "ob1:ex:n"), ("p", "ob1:ex:p:0"),
    ("p", "ob1:fo:k"), ("p", "ob1:fo:k"),
    ("t", "Málaga"), ("p", "ob1:ci:y"), ("p", "ob1:di:s:60"),
    ("p", "ob1:mo:t:presencial"), ("p", "ob1:mo:n"),
    ("p", "ob1:id:n"), ("p", "ob1:jo:k"), ("p", "ob1:sa:k"),
    ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:av:n"),
]


def da_paso(chat, accion):
    tipo, valor = accion
    return pulsa(valor, chat) if tipo == "p" else escribe(valor, chat)


def caso_conversacion_completa() -> None:
    print("\n[1] Conversación completa de dos personas a la vez, sin cruzarse")
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    entrar(A)
    entrar(B)
    comprueba(perfiles.obtener(A)["estado"] == "encuesta" and perfiles.obtener(B)["estado"] == "encuesta",
              "las dos entran con la encuesta: estado «encuesta»")
    comprueba(paso(A) == "bienvenida" and paso(B) == "bienvenida", "las dos empiezan en la bienvenida")
    bienvenida = ultima_pantalla(A).get("text", "")
    comprueba("puede ver tu ficha" in bienvenida and "no qué ofertas recibes" in bienvenida and "/borrar" in bienvenida,
              "la bienvenida dice que el titular ve su ficha, pero no qué ofertas recibe (D9)")
    comprueba("copia de seguridad al día" in bienvenida and "ordenador de quien administra" in bienvenida
              and "5 semanas" in bienvenida,
              "   y que hay copias diarias, una en el ordenador del titular, de las que /borrar sale en ~5 semanas (MP10)")
    comprueba(botones_de(ultima_pantalla(A))[:1] == ["ob1:0:go"] and "ob1:0:dat" in botones_de(ultima_pantalla(A)),
              "   con [Empezar] y [Qué datos guardas]")
    ids_a, ids_b = {id_del_paso(A)}, {id_del_paso(B)}
    comprueba(None not in ids_a | ids_b and ids_a != ids_b, "cada una tiene su mensaje de conversación")

    espia.limpiar()
    for i in range(max(len(GUION_A), len(GUION_B))):
        for chat, guion in ((A, GUION_A), (B, GUION_B)):
            if i < len(guion):
                da_paso(chat, guion[i])
    # Cada edición apunta al mensaje de SU conversación y a SU chat, y nunca al de la otra.
    ediciones = [c["cuerpo"] for c in espia.llamadas if c["metodo"] == "editMessageText"]
    mensajes = {A: ids_a, B: ids_b}
    cruces = [e for e in ediciones if e["message_id"] not in mensajes.get(e["chat_id"], set())]
    comprueba(len(ediciones) > 30, "se edita el mismo mensaje en cada paso (%d ediciones)" % len(ediciones))
    vistos = {A: {e["message_id"] for e in ediciones if e["chat_id"] == A},
              B: {e["message_id"] for e in ediciones if e["chat_id"] == B}}
    comprueba(vistos[A] == ids_a and vistos[B] == ids_b and not (vistos[A] & vistos[B]) and not cruces,
              "   cada edición apunta al message_id correcto de su chat: un único mensaje por persona")
    comprueba(paso(A) == "ficha" and paso(B) == "ficha", "las dos llegan a la ficha (paso 13)")
    ficha_a, ficha_b = ultima_pantalla(A)["text"], ultima_pantalla(B)["text"]
    comprueba("dministrativo" in ficha_a and "Valencia" in ficha_a and "Málaga" not in ficha_a and "amarero" not in ficha_a,
              "la ficha de A habla de A: administrativa en Valencia")
    comprueba("amarero" in ficha_b and "Málaga" in ficha_b and "Valencia" not in ficha_b and "dministrativo" not in ficha_b,
              "la ficha de B habla de B: camarero en Málaga")
    comprueba(botones_de(ultima_pantalla(A)) == ["ob1:fi:c", "ob1:fi:m", "ob1:fi:b"], "   con [Confirmar] [Cambiar algo]")

    guardado_a = json.dumps(borrador(A)["r"], sort_keys=True)
    comprueba(pulsa("ob1:fi:c", B) == "boton:ob1:confirmado", "B confirma")
    comprueba(json.dumps(borrador(A)["r"], sort_keys=True) == guardado_a and paso(A) == "ficha",
              "   y con eso no se mueve nada del borrador de A (control de aislamiento)")
    # El botón de A pulsado desde el chat de B solo toca la conversación de B (que ya no tiene ninguna).
    espia.limpiar()
    pulsa("ob1:fi:c", A, mensaje_id=id_del_paso(B) or 1)
    comprueba(perfiles.obtener(A)["estado"] == "encuesta" and paso(A) == "ficha",
              "un botón de A con el message_id de B no confirma a A")
    comprueba(pulsa("ob1:fi:c", A) == "boton:ob1:confirmado", "A confirma")
    pa, pb = perfiles.obtener(A), perfiles.obtener(B)
    comprueba(pa["estado"] == "activo" and pb["estado"] == "activo", "las dos quedan activas")
    comprueba(pa["datos"]["reglas"] == "catalogo" and pb["datos"]["reglas"] == "catalogo", "   con reglas = «catalogo»")
    plan_a, plan_b = pa["datos"]["plan"], pb["datos"]["plan"]
    comprueba(plan_a["area"]["nombre"] == "Valencia" and plan_b["area"]["nombre"] == "Málaga"
              and plan_a["area"]["lat"] == 39.47 and plan_a["area"]["lon"] == -0.38,
              "   cada plan con SU ciudad, y las coordenadas a 2 decimales (D9)")
    comprueba(all("amarero" not in t.casefold() for t in plan_a["terminos"])
              and all("dministrativ" not in t.casefold() for t in plan_b["terminos"]),
              "   y sus términos de búsqueda no se mezclan")
    comprueba(plan_a["experiencia_max_exigida"] == 5 and plan_a["experiencia_estricta"] is True
              and plan_b["experiencia_max_exigida"] == 1 and plan_b["practicas"] is False,
              "   el tope y «descarta» salen del paso 3: A 5 años, B 1 año y sin prácticas")
    comprueba(plan_a["claves_mas"][:2] == ["CCNA", "Prevención de riesgos"] or "contabilidad" in plan_a["claves_mas"],
              "   las certificaciones y las palabras clave entran en el plan")
    comprueba(plan_a["empresas_excluidas"] == ["Empresa Mala"] and "comercial" in plan_a["claves_menos"],
              "   y las empresas y lo que no quiere")
    comprueba(plan_a["idiomas_permitidos"] == ["es", "ca", "en"] and plan_a["ingles"] == "intermedio"
              and plan_b["idiomas_permitidos"] == ["es"], "   los idiomas, con el nivel de inglés de A")
    comprueba(plan_a["salario_min"] == 18000 and plan_b["salario_min"] is None, "   y el salario mínimo")
    comprueba(perfiles.config_efectiva(pa, config())["umbral"] == 5.0 and perfiles.config_efectiva(pb, config())["area"]["nombre"] == "Málaga",
              "   perfiles.config_efectiva los entiende")
    comprueba(pa["datos"]["avisos"].get("silencio") == [22, 8] and pa["datos"]["avisos"].get("max_dia") == 10
              and pb["datos"]["avisos"].get("silencio") == [22, 8] and pb["datos"]["avisos"].get("modo") == "instantaneo",
              "   quien entra empieza con silencio de 22:00 a 8:00 y tope de 10 al día (provisional, MP9), al momento")
    comprueba(perfiles.conversacion(A) is None and perfiles.conversacion(B) is None, "la conversación se borra al confirmar")
    comprueba(lanzados == [B, A], "se lanza la corrida de arranque de cada una (la suya)")
    comprueba(pa["datos"]["arranque"] == {"estado": "pendiente"}, "   y queda apuntada como pendiente, por si se reinicia")
    comprueba(any("Perfil guardado" in t for t in [c["text"] for c in espia.de("editMessageText", A)]), "   y se le dice")
    # El titular puede ver la ficha de configuración de cada una, y /perfil no dice nada de ofertas.
    espia.limpiar()
    escribe("/perfil", A)
    texto = textos_todos(A)
    comprueba("Valencia" in texto and "catálogo" in texto, "/perfil de A: su ficha, con el juego «catálogo»")


def caso_reinicio() -> None:
    print("\n[2] Reinicio a mitad: se recrea el router y se sigue en el mismo paso")
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    perfiles.borrar(B)
    entrar(C)
    for accion in GUION_A[:7]:  # hasta la experiencia (la fila 7 es el nivel)
        da_paso(C, accion)
    antes = borrador(C)
    viejo = id_del_paso(C)
    comprueba(antes["paso"] == "experiencia" and antes["sub"] == "tope", "C está en el tope de experiencia")
    # «Reiniciar»: el estado en memoria se pierde (el menú del catálogo, los grupos avisados…); la base queda.
    enc._MENU.update(firma=None, familias=[], puestos={}, familia_de={}, visible_en={})
    bot._GRUPOS_AVISADOS.clear()
    espia.limpiar()
    comprueba(escribe("/start", C) == "comando:start", "tras el reinicio, /start")
    nuevo = id_del_paso(C)
    comprueba(borrador(C)["paso"] == "experiencia" and borrador(C)["sub"] == "tope" and borrador(C)["r"] == antes["r"],
              "sigue en el MISMO paso y con las mismas respuestas")
    comprueba(nuevo != viejo and any("Paso 3/12" in t for t in espia.textos(C)), "   en un mensaje nuevo (con «Paso 3/12»)")
    comprueba(pulsa("ob1:ex:t:3", C, mensaje_id=viejo) == "boton:ob1:antiguo", "un botón del mensaje de antes del reinicio no hace nada")
    comprueba(borrador(C)["r"] == antes["r"], "   y no cambia ninguna respuesta")
    comprueba(pulsa("ob1:ex:t:3", C) == "boton:ob1:paso:experiencia" and borrador(C)["r"]["experiencia_max_exigida"] == 3,
              "el del mensaje nuevo, sí")
    # Un mensaje de texto sin borrador (se borró) retoma la encuesta en vez de contestar «no te entiendo».
    perfiles.borrar_conversacion(C)
    espia.limpiar()
    comprueba(escribe("hola", C) in ("paso:" + "enc:bienvenida", "encuesta:reanudada", "texto") or paso(C) == "bienvenida",
              "escribir sin borrador retoma la encuesta desde la bienvenida")
    comprueba(paso(C) == "bienvenida" and not any("No te entiendo" in t for t in espia.textos(C)), "   sin «no te entiendo»")


def caso_navegacion_y_texto() -> None:
    print("\n[3] Atrás, Saltar, «Otro», texto demasiado largo, ciudad inexistente y ubicación compartida")
    pulsa("ob1:0:go", C)
    comprueba(paso(C) == "sector" and "ob1:se:b" not in botones_de(ultima_pantalla(C)), "el primer paso no tiene «Atrás»")
    antes = json.dumps(borrador(C), sort_keys=True)
    comprueba(pulsa("ob1:ex:s:3_5", C) == "boton:ob1:otro_paso" and json.dumps(borrador(C), sort_keys=True) == antes,
              "un botón de otro paso (el de la experiencia, estando en el sector) no hace nada")
    # Otro (sector): pide texto con ForceReply, que se borra al salir
    espia.limpiar()
    pulsa("ob1:se:t:otro", C)
    fr = [c for c in espia.de("sendMessage", C) if "force_reply" in (c.get("reply_markup") or {})]
    comprueba(len(fr) == 1 and borrador(C)["sub"] == "otro" and borrador(C).get("pregunta"),
              "«Otro» pide el texto con ForceReply")
    espia.limpiar()
    escribe("x" * 61, C)
    comprueba(borrador(C)["sub"] == "otro" and not borrador(C)["r"].get("sector_otro")
              and any("demasiado largo" in t for t in espia.textos(C)), "un «Otro» de más de 60 letras se rechaza y no se guarda")
    espia.limpiar()
    escribe("recepcionista de <b>spa</b> & co", C)
    comprueba(borrador(C)["r"]["sector_otro"] == "recepcionista de <b>spa</b> & co" and borrador(C)["sub"] is None,
              "uno corto se guarda tal cual")
    comprueba(any(c["metodo"] == "deleteMessage" for c in espia.llamadas), "   y la pregunta ForceReply se borra al salir")
    # Máximo 3 sectores
    for f in ("admin", "atencion", "comercial"):
        pulsa(f"ob1:se:t:{f}", C)
    comprueba(len(borrador(C)["r"]["sectores"]) + 1 <= 3 and "comercial" not in borrador(C)["r"]["sectores"],
              "como mucho 3 sectores (con «Otro» ya son 3)")
    pulsa("ob1:se:t:otro", C)  # lo quita
    comprueba(not borrador(C)["r"].get("sector_otro"), "pulsar «Otro» otra vez lo quita")
    pulsa("ob1:se:t:comercial", C)
    comprueba(borrador(C)["r"]["sectores"] == ["admin", "atencion", "comercial"], "   y ya caben los tres")
    pulsa("ob1:se:t:atencion", C)
    pulsa("ob1:se:t:comercial", C)
    pulsa("ob1:se:n", C)
    comprueba(paso(C) == "puestos", "Siguiente lleva a los puestos")
    comprueba(any("ob1:pu:t:admin.recepcion" in botones_de(ultima_pantalla(C)) for _ in [0]),
              "   y la recepcionista aparece también en el menú de otras familias (tambien_en)")
    # Máximo 5 puestos
    for p in ("admin.administrativo", "admin.auxiliar", "admin.recepcion", "admin.asistente", "admin.backoffice",
              "admin.comercial"):
        pulsa(f"ob1:pu:t:{p}", C)
    comprueba(len(borrador(C)["r"]["puestos"]) <= 5, "como mucho 5 puestos")
    # Atrás conserva
    pulsa("ob1:pu:b", C)
    comprueba(paso(C) == "sector" and borrador(C)["r"]["sectores"] == ["admin"], "Atrás vuelve al sector con lo elegido")
    pulsa("ob1:se:n", C)
    comprueba(len(borrador(C)["r"]["puestos"]) == 5, "   y los puestos siguen elegidos al volver a entrar")
    # «Otro» de puestos
    pulsa("ob1:pu:o", C)
    escribe("recepcionista de spa", C)
    comprueba(borrador(C)["r"]["otros"] == ["recepcionista de spa"], "«Otro» en puestos: una palabra clave")
    pulsa("ob1:pu:t:admin.asistente", C)  # quita uno
    pulsa("ob1:pu:n", C)
    # Experiencia con Atrás entre subpasos
    pulsa("ob1:ex:s:menos_1", C)
    comprueba(borrador(C)["sub"] == "tope" and borrador(C)["r"]["experiencia_max_exigida"] == 1
              and borrador(C)["r"]["experiencia_estricta"] is True, "menos de 1 año: tope sugerido 1 y «descarta»")
    pulsa("ob1:ex:e:0", C)
    pulsa("ob1:ex:t:x", C)
    comprueba(borrador(C)["r"]["experiencia_max_exigida"] is None and borrador(C)["r"]["experiencia_estricta"] is False,
              "se puede quitar el límite y elegir «solo bájalas»")
    pulsa("ob1:ex:t:1", C)
    pulsa("ob1:ex:e:1", C)
    pulsa("ob1:ex:n", C)
    comprueba(borrador(C)["sub"] == "practicas", "sin experiencia se pregunta por las prácticas")
    pulsa("ob1:ex:b", C)
    comprueba(borrador(C)["sub"] == "tope" and paso(C) == "experiencia", "Atrás vuelve al tope")
    pulsa("ob1:ex:n", C)
    pulsa("ob1:ex:p:1", C)
    comprueba(borrador(C)["r"]["practicas"] is True and paso(C) == "formacion", "«Sí» a las prácticas")
    # Saltar
    pulsa("ob1:fo:k", C)
    comprueba(paso(C) == "formacion" and borrador(C)["sub"] == "cert" and "formacion" not in borrador(C)["r"],
              "Saltar la formación pasa a las certificaciones, sin respuesta")
    escribe("a, b, c, d, e, f, g, h, i", C)
    comprueba(not borrador(C)["r"].get("certificaciones") and any("demasiadas" in t for t in espia.textos(C)),
              "más de 8 certificaciones: se rechaza")
    escribe("x" * 301, C)
    comprueba(any("demasiado largo" in t for t in espia.textos(C)) and not borrador(C)["r"].get("certificaciones"),
              "más de 300 caracteres: se rechaza")
    escribe("y" * 41, C)
    comprueba(any("muy larga" in t for t in espia.textos(C)), "un elemento de más de 40 letras: se rechaza")
    escribe("CCNA;  Excel avanzado\nPRL , ccna", C)
    comprueba(borrador(C)["r"]["certificaciones"] == ["CCNA", "Excel avanzado", "PRL"] and paso(C) == "ciudad",
              "coma, punto y coma y salto de línea separan; los repetidos se quitan")
    # Ciudad
    comprueba("keyboard" in " ".join(json.dumps(c) for c in espia.de("sendMessage", C)) and
              any("request_location" in json.dumps(c.get("reply_markup") or {}) for c in espia.de("sendMessage", C)),
              "el paso de ciudad ofrece «📍 Enviar mi ubicación» (teclado de respuesta)")
    espia.limpiar()
    escribe("Zzzzville", C)
    comprueba(borrador(C).get("candidata") is None and any("No encuentro" in t for t in espia.textos(C)),
              "una ciudad que no existe: se dice y se sigue esperando")
    comprueba(perfiles.meta_get("ciudad:" + __import__("hashlib").sha1(b"zzzzville").hexdigest()[:12] + ":v1") is None,
              "   y no se recuerda en el caché")
    escribe("Valencia", C)
    comprueba(borrador(C)["candidata"]["nombre"] == "Valencia" and borrador(C)["candidata"]["lat"] == 39.47,
              "una que existe: se confirma con sus coordenadas a 2 decimales")
    comprueba("¿Es <b>Valencia</b>" in ultima_pantalla(C)["text"] and "ob1:ci:y" in botones_de(ultima_pantalla(C)),
              "   «¿Es Valencia, Comunitat Valenciana?» [Sí] [No]")
    preguntas_al_geocodificador.clear()
    escribe("Valencia", C)  # ya está confirmándose: otro texto no es una ciudad nueva
    comprueba(borrador(C).get("candidata") is not None, "mientras se confirma, otro texto no rompe el paso")
    pulsa("ob1:ci:x", C)
    comprueba(borrador(C).get("candidata") is None and paso(C) == "ciudad", "«No»: vuelve a pedirla")
    # Ubicación compartida
    espia.limpiar()
    preguntas_al_geocodificador.clear()
    comparte(39.4699123456, -0.3763123456, C)
    comprueba(any(c["cuerpo"].get("reply_markup") == {"remove_keyboard": True} for c in espia.llamadas if c["metodo"] == "sendMessage"),
              "con la ubicación recibida se retira el teclado de ubicación (remove_keyboard) y se borra el mensaje")
    comprueba(preguntas_al_geocodificador and preguntas_al_geocodificador[-1][1] == (39.47, -0.38),
              "la ubicación compartida se redondea a 2 decimales ANTES de preguntar nada (D9)")
    comprueba(borrador(C)["candidata"]["nombre"] == "Valencia", "   y se confirma igual que el texto")
    comprueba("39.4699" not in json.dumps(borrador(C)) and "0.3763" not in json.dumps(borrador(C)),
              "   las coordenadas exactas no llegan al borrador")
    espia.limpiar()
    pulsa("ob1:ci:y", C)
    comprueba(paso(C) == "distancia" and not borrador(C).get("teclado_ubicacion"), "al confirmar se sigue con la distancia, sin teclado abierto")
    json_borrador = json.dumps(borrador(C))
    comprueba('"lat": 39.47' in json_borrador, "   guardada a 2 decimales")
    # Distancia y modalidad
    pulsa("ob1:di:s:0", C)
    comprueba(paso(C) == "modalidad" and borrador(C)["sub"] == "alcance" and borrador(C)["r"]["modalidad"] == ["remoto"],
              "«Solo remoto» en la distancia: la modalidad pregunta solo desde dónde")
    pulsa("ob1:mo:a:ue", C)
    comprueba(paso(C) == "idiomas" and borrador(C)["r"]["remoto_alcance"] == "ue", "   Unión Europea")
    pulsa("ob1:id:b", C)
    comprueba(paso(C) == "modalidad", "Atrás desde idiomas vuelve a la modalidad")
    pulsa("ob1:mo:b", C)
    comprueba(paso(C) == "distancia", "   y de ahí, a la distancia (sin pasar por el subpaso de antes)")
    pulsa("ob1:di:s:10", C)
    comprueba(borrador(C)["r"]["modalidad"] == [], "con presencial posible, la modalidad se vuelve a elegir")
    pulsa("ob1:mo:n", C)
    comprueba(paso(C) == "modalidad" and espia.llamadas, "   y no se puede seguir sin elegir ninguna")
    pulsa("ob1:mo:t:remoto", C)
    pulsa("ob1:mo:n", C)
    comprueba(borrador(C)["sub"] == "alcance" and borrador(C)["r"]["ubicacion"]["radio_km"] == 0,
              "solo «Remoto» marcado: radio 0 y se pregunta el alcance")
    pulsa("ob1:mo:a:es", C)
    # Idiomas
    pulsa("ob1:id:t:es", C)
    pulsa("ob1:id:n", C)
    comprueba(paso(C) == "idiomas" and borrador(C)["r"]["idiomas"] == [], "no se puede seguir sin ningún idioma")
    pulsa("ob1:id:t:es", C)
    pulsa("ob1:id:t:en", C)
    pulsa("ob1:id:n", C)
    comprueba(borrador(C)["sub"] == "ingles", "con inglés, se pregunta el nivel")
    pulsa("ob1:id:i:basico", C)
    comprueba(paso(C) == "jornada", "   y sigue")
    pulsa("ob1:jo:t:turnos", C)
    comprueba(borrador(C)["r"]["turnos_ok"] is True, "«Acepto turnos» se marca")
    pulsa("ob1:jo:k", C)
    pulsa("ob1:sa:s:40000", C)
    comprueba(borrador(C)["r"]["salario_min"] == 40000 and paso(C) == "claves", "salario 40.000+")
    pulsa("ob1:cl:b", C)
    comprueba(paso(C) == "salario", "Atrás desde las palabras clave vuelve al salario")
    pulsa("ob1:sa:k", C)
    escribe("<i>python</i> & co", C)
    comprueba(borrador(C)["sub"] == "menos" and borrador(C)["r"]["claves_mas"] == ["<i>python</i> & co"], "palabras clave: lo que interesa")
    pulsa("ob1:cl:b", C)
    comprueba(borrador(C)["sub"] == "mas", "   Atrás entre las tres preguntas de texto")
    pantalla = espia.de("editMessageText", C)[-1]["text"]
    comprueba("Ahora: &lt;i&gt;python&lt;/i&gt; &amp; co" in pantalla and "<i>python</i>" not in pantalla,
              "   y lo escrito se enseña escapado: nada de HTML suelto en el mensaje (notificaciones._esc)")
    pulsa("ob1:cl:k", C)
    escribe("Si", C)
    escribe("EmpresaX", C)
    comprueba(paso(C) == "avisos", "tras las tres, los avisos")
    # La ficha y «Cambiar algo»
    pulsa("ob1:av:d:0", C)
    comprueba(borrador(C)["r"]["descubrimiento"] is False, "avisos: las ofertas para afinar se apagan")
    pulsa("ob1:av:n", C)
    comprueba(paso(C) == "ficha" and "Tu perfil" in ultima_pantalla(C)["text"], "paso 13: la ficha")
    pulsa("ob1:fi:m", C)
    comprueba({"ob1:fi:g:id", "ob1:fi:g:ci", "ob1:fi:g:pu", "ob1:fi:g:av", "ob1:fi:v"} <= set(botones_de(ultima_pantalla(C))),
              "«Cambiar algo» enseña las secciones")
    pulsa("ob1:fi:g:id", C)
    comprueba(paso(C) == "idiomas" and borrador(C)["cola"] == ["idiomas"], "   una sección abre sus pasos")
    pulsa("ob1:id:b", C)
    comprueba(paso(C) == "ficha" and borrador(C)["cola"] is None, "   y Atrás desde el primero vuelve a la ficha")
    pulsa("ob1:fi:m", C)
    pulsa("ob1:fi:g:ci", C)
    escribe("Madrid", C)
    pulsa("ob1:ci:y", C)
    comprueba(paso(C) == "distancia", "Ciudad y distancia desde la ficha: primero la ciudad, luego la distancia")
    pulsa("ob1:di:s:25", C)
    comprueba(paso(C) == "ficha" and borrador(C)["r"]["ubicacion"]["nombre"] == "Madrid" and "Madrid" in ultima_pantalla(C)["text"],
              "   y al acabar la sección se vuelve a la ficha, ya con la ciudad nueva")
    pulsa("ob1:fi:m", C)
    pulsa("ob1:fi:v", C)
    comprueba("ob1:fi:c" in botones_de(ultima_pantalla(C)), "«Volver» deja la ficha con [Confirmar]")


def caso_todo_cabe() -> None:
    print("\n[4] Todos los teclados: callback_data ≤ 64 bytes, mensajes ≤ 4.096")
    llamadas = [c["cuerpo"] for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")]
    # Se pintan todas las pantallas con el peor caso: 3 sectores, 5 puestos, 6 otros, todos los idiomas…
    chat = type("C", (), {"chat_id": C, "config": config(), "perfil": perfiles.obtener(C)})()
    peor = {"sectores": ["it", "admin", "atencion"], "sector_otro": None, "puestos": [], "otros": ["a" * 60, "b" * 60],
            "experiencia": "ninguna", "experiencia_max_exigida": 1, "experiencia_estricta": True, "practicas": True,
            "ubicacion": {"nombre": "Sant Cugat del Vallès " * 2, "lat": 41.47, "lon": 2.08, "radio_km": 25},
            "modalidad": ["presencial", "hibrido", "remoto"], "remoto_alcance": "ue", "idiomas": ["es", "ca", "en", "fr", "de", "pt", "it"],
            "ingles": "alto", "jornada": ["completa", "parcial", "indefinido", "temporal", "autonomo"], "turnos_ok": True,
            "salario_min": 40000, "claves_mas": ["x" * 40] * 8, "claves_menos": ["y" * 40] * 8,
            "empresas_excluidas": ["z" * 40] * 8, "certificaciones": ["c" * 40] * 8, "descubrimiento": True, "formacion": "grado"}
    peor["puestos"] = [p for p in enc._puestos_visibles(peor)][:5]
    pantallas = []
    for modo in ("encuesta", "editar", "rehacer"):
        for nombre, subs in list(enc.SUBS.items()) + [("bienvenida", (None,)), ("distancia", (None,)), ("jornada", (None,)),
                                                      ("salario", (None,)), ("avisos", (None,)), ("ficha", (None,))]:
            for sub in subs:
                b = {"modo": modo, "paso": nombre, "sub": sub, "pila": ["sector"], "r": peor, "cola": None}
                if nombre == "ciudad":
                    pantallas.append(enc._PANTALLAS[nombre](chat, {**b, "candidata": {"nombre": "Valencia", "region": "x" * 80}}))
                pantallas.append(enc._PANTALLAS[nombre](chat, b))
    pantallas.append(enc._PANTALLAS["ficha"](chat, {"modo": "encuesta", "paso": "ficha", "sub": None, "pila": [], "r": peor,
                                                    "menu_de_cambios": True}))
    for perfil_ in (perfiles.PERFIL_GLOBAL, {"estado": "activo", "datos": {"reglas": "catalogo"}}, {"estado": "pausado", "datos": {}}):
        pantallas.append(enc.texto_de_ajustes(perfil_))
    largos = [c for c in llamadas if len(c.get("text", "")) > 4096] + [t for t, _ in pantallas if len(t) > 4096]
    comprueba(not largos, "ningún mensaje pasa de 4.096 caracteres (%d pantallas del peor caso y %d de la prueba)" % (
        len(pantallas), len(llamadas)))
    todos = []
    for _, teclado in pantallas:
        todos += [b["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]
    todos += [b for c in llamadas for b in botones_de(c)]
    mayor = max(todos, key=lambda d: len(d.encode("utf-8")))
    comprueba(max(len(d.encode("utf-8")) for d in todos) <= 64, "ningún callback_data pasa de 64 bytes (el mayor, %d: %s)" % (
        len(mayor.encode("utf-8")), mayor.split(":")[0] + ":…"))
    comprueba(all(d.startswith(("ob1:", "aj1:")) for d in todos), "   todos llevan prefijo versionado ob1: o aj1:")
    comprueba(not any(re.search(r"\d{6,}", d) for d in todos), "   y ninguno lleva un número de chat")
    # Control negativo: un callback_data que pasara de 64 bytes se vería (la comprobación no es vacua)
    comprueba(len(("ob1:pu:t:" + "x" * 60).encode()) > 64, "   control: la comprobación sí vería uno largo")


def caso_arranque_y_calibracion() -> None:
    print("\n[5] Calibración y corrida de arranque: umbral en [4,0; 6,5] y ≤ 5 avisos")
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja", "no_encaja", "encaja"], ["no_encaja", "no_encaja"]) == 5.5,
              "rechaza 2 de las 3 mejores: +0,5")
    comprueba(enc.umbral_calibrado(5.0, ["encaja"] * 3, ["encaja", "encaja"]) == 4.5, "acepta 2 del borde: −0,5")
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja"] * 3, ["encaja", "encaja"]) == 5.0, "las dos cosas: se compensan")
    comprueba(enc.umbral_calibrado(6.5, ["no_encaja"] * 3, []) == 6.5 and enc.umbral_calibrado(4.0, [], ["encaja"] * 2) == 4.0,
              "nunca sale de [4,0; 6,5]")
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja", "encaja"], ["encaja", None]) == 5.0, "con un solo rechazo o un solo sí, nada")
    # El caso del piloto (07-11): seis 👎 subían el listón a 5,5 cuando su mejor oferta de siempre valía 5,0,
    # y se quedaba con CERO avisos para siempre. Con el techo, el listón no pasa de lo que puede recibir.
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja"] * 3, ["no_encaja"] * 2, 5.0) == 5.0,
              "con techo 5,0, tres rechazos NO suben el listón por encima de 5,0 (el caso del piloto)")
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja"] * 3, ["no_encaja"] * 2) == 5.5,
              "   control: sin techo, los mismos votos lo dejan en 5,5 (que es lo que le pasó)")
    comprueba(enc.umbral_calibrado(5.0, ["encaja"] * 3, ["encaja"] * 2, 9.0) == 4.5,
              "   un techo alto no sube el listón: el techo solo baja")
    comprueba(enc.umbral_calibrado(5.0, ["no_encaja"] * 3, [], 2.0) == 4.0,
              "   y el suelo de 4,0 manda sobre el techo: con un techo de 2,0 el problema es la puntuación")
    rango = [enc.umbral_calibrado(base, v1, v2) for base in (3.0, 4.0, 5.0, 6.0, 6.5, 8.0)
             for v1 in ([], ["no_encaja"] * 2, ["no_encaja"] * 3) for v2 in ([], ["encaja"] * 2)]
    comprueba(len(rango) == 36 and all(4.0 <= u <= 6.5 for u in rango), "las 36 combinaciones, incluidas bases fuera de rango, caen en [4,0; 6,5]")
    filas = [("u%d" % i, 9.0 - i * 0.5) for i in range(14)]
    sel = enc.elegir_calibracion(filas, 5.0)
    todos = sel["mejores"] + sel["borde"] + sel["baja"]
    comprueba(sel["mejores"] == ["u0", "u1", "u2"] and len(sel["borde"]) == 2 and len(sel["baja"]) == 1 and len(set(todos)) == 6,
              "seis ofertas: las 3 mejores, 2 del borde y 1 baja, sin repetir")
    notas = dict(filas)
    comprueba(all(abs(notas[u] - 5.0) <= 1.0 for u in sel["borde"]) and notas[sel["baja"][0]] <= 3.0,
              "   el borde queda cerca del umbral y la baja por debajo")
    comprueba(len(enc.elegir_calibracion(filas[:4], 5.0)["mejores"]) == 3 and
              sum(len(v) for v in enc.elegir_calibracion(filas[:2], 5.0).values()) == 2, "con pocas ofertas, las que haya")

    # --- con A de verdad: su lista (suya), el arranque y los avisos
    pa = perfiles.obtener(A)
    ofertas_a = [oferta(i, t, relevancia=r) for i, (t, r) in enumerate([
        ("Administrativo/a contable", 8.0), ("Auxiliar administrativo/a", 7.5), ("Administrativo de RRHH", 7.0),
        ("Administrativo/a de compras", 6.5), ("Auxiliar administrativo (Valencia)", 6.0), ("Administrativo/a comercial", 5.2),
        ("Recepcionista de oficina", 4.6), ("Asistente de dirección", 3.8), ("Contable junior", 2.5),
        ("Camarero/a", 0.5), ("Operario", 0.0)], 1)]
    perfiles.aplicar_cambios(A, escribir=[(o, 0, i) for i, o in enumerate(ofertas_a)], ultima_busqueda=datetime.now().isoformat())
    perfiles.aplicar_cambios(TITULAR, escribir=[(oferta(900, "Analista SOC N1", relevancia=9.0), 0, 0)])
    espia.limpiar()
    comprueba(enc.tras_arranque(A, config()) == "calibracion", "tras el arranque, calibración")
    textos = espia.textos(A)
    tarjetas = [c for c in espia.de("sendMessage", A) if any(b.startswith("ob1:cal:") for b in botones_de(c))]
    comprueba(len(tarjetas) == 6 and "11 ofertas" in textos[0], "se mandan 6 tarjetas con 👍 / 👎 y el intro dice cuántas ofertas hay")
    comprueba(all(len(botones_de(c)) == 2 for c in tarjetas) and "(1/6)" in tarjetas[0]["text"], "   cada una con sus dos botones")
    comprueba(len(avisos_de(A, "calibracion")) == 6, "   registradas en `avisos` como «calibracion»")
    avisos_normales = avisos_de(A, "aviso")
    comprueba(len(avisos_normales) <= 2 and len(avisos_normales) + 3 <= 5,
              "de las 5 mejores, las 3 primeras van en la calibración y como mucho 2 más como avisos (%d)" % len(avisos_normales))
    comprueba(not (set(avisos_de(A, "calibracion")) & set(avisos_normales)), "   sin solaparse: nada es calibración y aviso a la vez")
    comprueba("Analista SOC" not in " ".join(textos), "todo sale de SU lista: nada del titular (aislamiento)")
    comprueba(perfiles.obtener(A)["datos"]["primera_pendiente"] is True and perfiles.obtener(A)["datos"]["arranque"] == {"estado": "hecho"},
              "queda apuntado que su primera corrida normal también es «primera»")

    # Votos: rechaza 2 de las 3 mejores y acepta los 2 del borde
    cal = perfiles.obtener(A)["datos"]["calibracion"]
    claves = lambda urls: [tb.clave_de(u) for u in urls]  # noqa: E731
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.0, "el umbral de partida es 5,0")
    espia.limpiar()
    for clave in claves(cal["mejores"])[:2]:
        pulsa(f"ob1:cal:{clave}:n", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.5 and perfiles.obtener(A)["datos"]["umbral"] == 5.5,
              "rechazar 2 de las 3 mejores sube el umbral a 5,5 (en el plan y en las respuestas)")
    marcas = perfiles.marcas(A)
    comprueba(len(marcas.get("ocultas", {})) == 2 and all(i.get("origen") == "calibracion" for i in marcas["ocultas"].values()),
              "   cada voto es una etiqueta de MP6 con origen «calibracion»")
    for clave in claves(cal["borde"]):
        pulsa(f"ob1:cal:{clave}:s", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.0, "aceptar los 2 del borde lo vuelve a bajar medio punto")
    pulsa(f"ob1:cal:{claves(cal['mejores'])[2]}:n", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.0,
              "un tercer rechazo no suma otro +0,5: con los 2 del borde aceptados siguen compensándose")
    pulsa(f"ob1:cal:{claves(cal['borde'])[0]}:n", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.5,
              "cambiar un voto lo recalcula (el borde ya no tiene 2 aceptadas): es una función de los votos, no un acumulado")
    pulsa(f"ob1:cal:{claves(cal['borde'])[0]}:s", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["plan"]["umbral"] == 5.0, "   y volver a cambiarlo, también")
    pulsa(f"ob1:cal:{claves(cal['baja'])[0]}:n", A, mensaje_id=700)
    comprueba(perfiles.obtener(A)["datos"]["calibracion"]["estado"] == "hecha" and any("Gracias" in t for t in espia.textos(A)),
              "con los seis votos, la calibración acaba y se le da las gracias")
    comprueba(perfiles.config_efectiva(perfiles.obtener(A), config())["umbral"] == 5.0, "   y la config efectiva sale con el umbral de los votos")
    # Lo que escribe la encuesta en `avisos` sobrevive a la calibración y a tocar /ajustes → Avisos (MP9): ningún guardado
    # posterior reconstruye el diccionario. (Lo preguntó el titular al ver un perfil real sin `max_dia`.)
    escrito = lambda: {k: v for k, v in perfiles.obtener(A)["datos"]["avisos"].items() if k in ("silencio", "max_dia")}  # noqa: E731
    comprueba(escrito() == {"silencio": [22, 8], "max_dia": 10}, "tras la calibración, `avisos` sigue con 22–8 y 10 al día")
    for boton in ("aj1:av", "aj1:ad:0", "aj1:ad:1", "aj1:am:i", "aj1:am:d", "aj1:am:i", "aj1:as:1", "aj1:aj"):
        pulsa(boton, A)
        comprueba(escrito() == {"silencio": [22, 8], "max_dia": 10}, "   y tras %s" % boton)
    cuerpos = [c["cuerpo"] for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")]
    comprueba(all(len(d.encode("utf-8")) <= 64 for c in cuerpos for d in botones_de(c)) and all(len(c.get("text", "")) <= 4096 for c in cuerpos),
              "las tarjetas de la calibración también caben: callback_data ≤ 64 bytes y mensajes ≤ 4.096")
    # El voto de otro chat no toca la calibración de A
    antes = json.dumps(perfiles.obtener(A)["datos"], sort_keys=True)
    comprueba(pulsa(f"ob1:cal:{claves(cal['mejores'])[0]}:s", TITULAR, mensaje_id=700) in ("boton:ob1:desconocida", "boton:ob1:antiguo"),
              "una calibración pulsada desde otro chat no encuentra nada")
    comprueba(json.dumps(perfiles.obtener(A)["datos"], sort_keys=True) == antes, "   y no cambia nada de A")

    # Sin nada: se dice claro y con las fuentes que de verdad buscan
    espia.limpiar()
    entrar(C)
    perfiles.borrar_conversacion(C)
    pc = perfiles.obtener(C)
    plan_c = generador.generar_plan({"puestos": ["hosteleria.camarero"], "experiencia": "ninguna",
                                     "ubicacion": {"nombre": "Málaga", "lat": 36.72, "lon": -4.42, "radio_km": 60},
                                     "modalidad": ["presencial"]})
    perfiles.guardar(C, {"v": 1, "reglas": "catalogo", "plan": plan_c, "arranque": {"estado": "pendiente"}}, "activo")
    from fuentes_extra import salud
    salud_antes = salud.fuentes()
    espia.limpiar()
    comprueba(enc.tras_arranque(C, config()) == "sin_nada", "sin ofertas: «sin_nada»")
    texto = " ".join(espia.textos(C))
    comprueba("no he encontrado nada" in texto and "Ahora busco en" in texto and "Indeed" in texto and "LinkedIn" in texto,
              "se lo dice claro y qué fuentes buscan")
    activa = salud.esta_activa
    salud.esta_activa = lambda f: f not in ("eures", "infojobs")  # el servidor de hoy
    try:
        perfiles.actualizar_datos(C, lambda d: d.update(arranque={"estado": "pendiente"}))
        espia.limpiar()
        enc.tras_arranque(C, config())
        texto = " ".join(espia.textos(C))
        comprueba("Ahora busco en Indeed, Jooble y LinkedIn." in texto and "EURES e InfoJobs no están disponibles" in texto
                  and "hostelería, logística, comercio" in texto,
                  "con EURES e InfoJobs fuera: busca en Indeed, Jooble y LinkedIn, y dice que las otras no están (y por qué importa)")
    finally:
        salud.esta_activa = activa
    comprueba(perfiles.obtener(C)["datos"]["arranque"] == {"estado": "hecho"} and perfiles.obtener(C)["datos"].get("primera_pendiente"),
              "   y el arranque queda hecho, con su primera corrida normal limitada")


def caso_ficha_honesta() -> None:
    print("\n[5b] La ficha y el «no hay nada» dicen las fuentes que de verdad buscan")
    from fuentes_extra import salud

    chat = type("C", (), {"chat_id": C, "config": config(), "perfil": None})()
    base = {"sectores": ["hosteleria"], "puestos": ["hosteleria.camarero", "hosteleria.cocinero"], "experiencia": "ninguna",
            "practicas": False, "ubicacion": {"nombre": "Málaga", "lat": 36.72, "lon": -4.42, "radio_km": 25},
            "modalidad": ["presencial", "remoto"], "remoto_alcance": "es", "idiomas": ["es"], "descubrimiento": True}
    ficha = lambda r: enc._PANTALLAS["ficha"](chat, {"modo": "encuesta", "paso": "ficha", "sub": None, "pila": [], "r": r})[0]  # noqa: E731
    solo_remoto = dict(base, modalidad=["remoto"], ubicacion=dict(base["ubicacion"], radio_km=0))
    comprueba("Jooble" in ficha(base), "con búsqueda presencial, Jooble está entre las fuentes")
    comprueba("Jooble" not in ficha(solo_remoto) and "Tecnoempleo" not in ficha(solo_remoto),
              "solo remoto: Jooble y Tecnoempleo no responden a una consulta remota y no se cuentan")
    muchas = ficha(dict(base, sectores=["hosteleria", "logistica"], puestos=["hosteleria.camarero", "logistica.mozo"], modalidad=["presencial", "remoto"],
                        remoto_alcance="mundial", otros=["a b", "c d"]))
    comprueba("consultas por vuelta: lo nuevo te llegará más despacio" in muchas, "con más de 12 consultas por vuelta, la ficha avisa de que tardará más")
    comprueba("más despacio" not in ficha(base), "   y con pocas, no")
    activa = salud.esta_activa
    salud.esta_activa = lambda f: f not in ("eures", "infojobs")  # como en el servidor desde el 29-10
    try:
        texto = ficha(base)
        comprueba("Busco en: Indeed, Jooble y LinkedIn." in texto and "EURES e InfoJobs: desactivadas" in texto,
                  "con EURES e InfoJobs desactivadas, la ficha busca en Indeed, Jooble y LinkedIn, y dice cuáles no")
    finally:
        salud.esta_activa = activa


def caso_ajustes_de_catalogo() -> None:
    print("\n[6] /ajustes de un perfil de catálogo: regenera el plan, sube la versión y lo dice")
    pa = perfiles.obtener(A)
    comprueba(escribe("/ajustes", A) == "comando:ajustes", "/ajustes contesta")
    menu_ = ultima_pantalla(A)
    botones = botones_de(menu_)
    comprueba({"ob1:E:mo", "ob1:E:ci", "ob1:E:ex", "ob1:E:pu", "ob1:E:id", "ob1:E:sa", "ob1:E:cl", "aj1:av", "aj1:la",
               "ob1:E:pa", "ob1:E:sr"} <= set(botones), "el menú: las 7 secciones, Avisos y Lo aprendido, y los atajos Pausar y Solo remoto")
    id_menu = 555
    version = pa["version"]
    pulsa("ob1:E:ci", A, mensaje_id=id_menu)
    comprueba(paso(A) == "ciudad" and borrador(A)["modo"] == "editar" and id_del_paso(A) == id_menu,
              "la sección Ciudad y distancia abre el paso en el MISMO mensaje del menú")
    comprueba("Ahora: <b>Valencia</b>" in espia.de("editMessageText", A)[-1]["text"], "   enseñando la ciudad de ahora")
    pulsa("ob1:ci:n", A)
    comprueba(paso(A) == "distancia", "   Siguiente la deja y pasa a la distancia")
    espia.limpiar()
    pulsa("ob1:di:s:50", A)
    nuevo = perfiles.obtener(A)
    comprueba(nuevo["datos"]["plan"]["area"]["radio_km"] == 50.0 and nuevo["datos"]["ubicacion"]["radio_km"] == 50
              and nuevo["version"] > version, "al acabar la sección: 50 km en el plan y en las respuestas, y la versión sube")
    guardado = espia.de("editMessageText", A)[-1]["text"]
    comprueba("Guardado" in guardado and "Ahora busco" in guardado, "   «✅ Guardado. Ahora busco: …»")
    comprueba(perfiles.conversacion(A) is None, "   y la conversación se cierra")
    comprueba(perfiles.config_efectiva(nuevo, config())["max_distancia_km"] == 50, "   la config efectiva lo recoge")
    comprueba(nuevo["datos"]["plan"]["umbral"] == 5.0 and nuevo["datos"]["calibracion"]["estado"] == "hecha"
              and nuevo["datos"]["arranque"] == {"estado": "hecho"}, "   sin tocar la calibración ni el arranque")

    # Una ciudad nueva
    pulsa("ob1:E:ci", A, mensaje_id=556)
    escribe("Madrid", A)
    pulsa("ob1:ci:y", A)
    pulsa("ob1:di:s:25", A)
    cambio = perfiles.obtener(A)["datos"]
    comprueba(cambio["plan"]["area"]["nombre"] == "Madrid" and cambio["ubicacion"]["nombre"] == "Madrid",
              "cambiar de ciudad regenera el plan con la nueva")
    comprueba(all("Madrid" in a.get("location", "") for a in cambio["plan"]["ambitos"].values() if not a.get("is_remote")),
              "   y las búsquedas locales pasan a ser de Madrid")
    comprueba(not (cambio.get("ajustes_aprendidos") or {}).get("desde"), "   cambiar de ciudad no es cambiar de sector: lo aprendido sigue")
    # Experiencia (con Atrás desde el primer paso: cancela sin guardar)
    version = perfiles.obtener(A)["version"]
    pulsa("ob1:E:ex", A, mensaje_id=557)
    pulsa("ob1:ex:s:6_10", A)
    pulsa("ob1:ex:b", A)
    pulsa("ob1:ex:b", A)
    comprueba(perfiles.conversacion(A) is None and perfiles.obtener(A)["version"] == version,
              "Atrás desde el primer paso cancela: vuelve al menú sin guardar nada")
    comprueba("Ajustes" in espia.de("editMessageText", A)[-1]["text"] and "ob1:E:mo" in botones_de(espia.de("editMessageText", A)[-1]),
              "   y enseña el menú otra vez")
    # Cambio de sector: lo de antes deja de contar
    pulsa("ob1:E:pu", A, mensaje_id=558)
    pulsa("ob1:se:t:finanzas", A)
    pulsa("ob1:se:n", A)
    pulsa("ob1:pu:t:finanzas.contable", A)
    pulsa("ob1:pu:n", A)
    pa = perfiles.obtener(A)
    comprueba(pa["datos"]["sectores"] == ["admin", "finanzas"] and "finanzas.contable" in pa["datos"]["puestos"],
              "Sectores y puestos: cambia el sector y los puestos")
    comprueba(bool(pa["datos"]["ajustes_aprendidos"].get("desde")), "   y como es otro sector, lo marcado antes ya no cuenta (MP6)")
    comprueba("no cuenta para lo aprendido" in espia.de("editMessageText", A)[-1]["text"], "   y se lo dice")
    # Atajos
    espia.limpiar()
    comprueba(pulsa("ob1:E:pa", A, mensaje_id=559) == "boton:ob1:pausa" and perfiles.obtener(A)["estado"] == "pausado", "⏸️ Pausar")
    comprueba("ob1:E:re" in botones_de(espia.de("editMessageText", A)[-1]), "   y el menú ofrece Reanudar")
    pulsa("ob1:E:re", A, mensaje_id=559)
    comprueba(perfiles.obtener(A)["estado"] == "activo", "▶️ Reanudar")
    pulsa("ob1:E:sr", A, mensaje_id=560)
    pr = perfiles.obtener(A)["datos"]
    comprueba(pr["plan"]["area"]["radio_km"] == 0 and pr["modalidad"] == ["remoto"] and all(a.get("is_remote") for a in pr["plan"]["ambitos"].values()),
              "🏠 Solo remoto: radio 0, solo ámbitos remotos")
    comprueba(pulsa("ob1:E:sr", A, mensaje_id=560) == "boton:ob1:ya_solo_remoto", "   y pulsarlo otra vez no repite nada")
    # Modalidad: volver a presencial pone una distancia
    pulsa("ob1:E:mo", A, mensaje_id=561)
    pulsa("ob1:mo:t:presencial", A)
    pulsa("ob1:mo:n", A)
    pulsa("ob1:mo:a:es", A)
    pm = perfiles.obtener(A)["datos"]
    comprueba(pm["plan"]["area"]["radio_km"] == 25 and "local" in pm["plan"]["ambitos"],
              "Modalidad: aceptar presencial de nuevo le pone 25 km y la búsqueda local")
    # Una sugerencia aceptada (MP6) se conserva al regenerar
    pa = perfiles.obtener(A)
    import aprendizaje
    aprendizaje.aplicar_sugerencia(pa, {"clave": "anos_experiencia_max", "valor": 3, "antes": 5})
    aprendizaje.aplicar_sugerencia(perfiles.obtener(A), {"clave": "empresas_excluidas", "valor": "Mala SL", "antes": None})
    pulsa("ob1:E:id", A, mensaje_id=562)
    pulsa("ob1:id:n", A)
    pulsa("ob1:id:i:alto", A)
    pg = perfiles.obtener(A)["datos"]["plan"]
    comprueba(pg["experiencia_max_exigida"] == 3 and "Mala SL" in pg["empresas_excluidas"] and "Empresa Mala" in pg["empresas_excluidas"],
              "lo que aceptó con las sugerencias de MP6 sobrevive a regenerar el plan")
    # Falla la edición: se manda un mensaje nuevo
    espia.limpiar()
    espia.rechazar_edicion = True
    pulsa("ob1:E:cl", A, mensaje_id=563)
    espia.rechazar_edicion = False
    comprueba(len(espia.de("sendMessage", A)) >= 1 and id_del_paso(A) != 563, "si no se puede editar el mensaje, se manda uno nuevo y se sigue en él")
    perfiles.borrar_conversacion(A)
    # Encuesta sin acabar: /ajustes dice que primero se termine
    perfiles.guardar(C, {"v": 1, "reglas": "catalogo", "plan": {}}, "encuesta")
    espia.limpiar()
    escribe("/ajustes", C)
    comprueba(any("termina la encuesta" in t for t in espia.textos(C)), "/ajustes en plena encuesta: «primero termina la encuesta»")


def caso_ajustes_a_mano() -> None:
    print("\n[7] /ajustes de un perfil con reglas a mano: cambia solo esas claves y guarda la anterior")
    config_disco = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    # El titular, con config global, como en el servidor: su config está en config.json
    juego = reglas.obtener("titular-ciber")
    completa = {**config_disco, "puntuador": {"juego": "titular-ciber"}}
    for clave in ("ambitos", "max_distancia_km", "distancia_portal_millas", "anos_experiencia_max", "reglas_descarte",
                  "categorias", "centro_lat", "centro_lon", "centro_nombre", "exigir_remoto_fuera_de_barcelona"):
        completa[clave] = json.loads(json.dumps(juego.config[clave]))
    (DATOS / "config.json").write_text(json.dumps(completa, indent=2, ensure_ascii=False), encoding="utf-8")
    perfiles.guardar(TITULAR, {"v": 1, "reglas": "titular-ciber", "config": "global"}, "activo")
    antes_disco = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    espia.limpiar()
    escribe("/ajustes", TITULAR)
    botones = botones_de(ultima_pantalla(TITULAR))
    comprueba({"ob1:H:mo", "ob1:H:ci", "ob1:H:ex", "ob1:H:pu", "aj1:av", "aj1:la"} <= set(botones) and "ob1:E:id" not in botones,
              "el menú del titular: solo lo que sus reglas permiten cambiar, más Sectores y puestos (rehacer)")
    pulsa("ob1:H:ci", TITULAR, mensaje_id=801)
    pulsa("ob1:H:ci:s:25", TITULAR, mensaje_id=801)
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    diferentes = {k for k in set(antes_disco) | set(despues) if antes_disco.get(k) != despues.get(k)}
    comprueba(diferentes == {"max_distancia_km", "distancia_portal_millas"} and despues["max_distancia_km"] == 25,
              "Ciudad y distancia: cambia SOLO `max_distancia_km` y `distancia_portal_millas` de config.json (%s)" % sorted(diferentes))
    historial = perfiles.obtener(TITULAR)["datos"]["historial"]
    comprueba(len(historial) == 1 and historial[0]["antes"] == {"max_distancia_km": antes_disco["max_distancia_km"],
                                                                 "distancia_portal_millas": antes_disco["distancia_portal_millas"]},
              "   y la config anterior queda en el historial del perfil, con fecha")
    comprueba(historial[0]["seccion"] == "ci" and historial[0]["fecha"], "   con la sección y la fecha")
    comprueba(perfiles.obtener(TITULAR)["datos"]["reglas"] == "titular-ciber" and perfiles.obtener(TITULAR)["datos"]["config"] == "global",
              "   el perfil sigue con sus reglas y su config global")
    comprueba(any("Guardado" in c.get("text", "") for c in espia.de("editMessageText", TITULAR)), "   y se lo dice")
    # Experiencia
    pulsa("ob1:H:ex:s:2", TITULAR, mensaje_id=801)
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba(despues["anos_experiencia_max"] == 2 and despues["reglas_descarte"] == antes_disco["reglas_descarte"],
              "Experiencia: el tope, y sus 26 reglas de descarte intactas")
    # Un mismo valor otra vez no escribe nada nuevo en el historial
    n = len(perfiles.obtener(TITULAR)["datos"]["historial"])
    pulsa("ob1:H:ex:s:2", TITULAR, mensaje_id=801)
    comprueba(len(perfiles.obtener(TITULAR)["datos"]["historial"]) == n, "pulsar lo que ya está no escribe en el historial")
    # Modalidad: apagar un ámbito, pero no el último
    pulsa("ob1:H:mo", TITULAR, mensaje_id=801)
    ambitos = despues["ambitos"]
    orden = sorted(ambitos.items(), key=lambda kv: (kv[1].get("is_remote", False), kv[0]))
    i_remoto_mundial = [k for k, _ in orden].index("remoto_mundial")
    pulsa(f"ob1:H:mo:t:{[k for k, _ in orden].index('remoto_ue')}", TITULAR, mensaje_id=801)
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba(despues["ambitos"]["remoto_ue"]["activo"] is False and despues["ambitos"]["remoto_es"]["activo"] is True,
              "Modalidad: se apaga solo «remoto UE»")
    for clave, _ in orden:  # se apagan todos menos uno...
        if despues["ambitos"][clave].get("activo", True) and clave != "barcelona_local":
            pulsa(f"ob1:H:mo:t:{[k for k, _ in orden].index(clave)}", TITULAR, mensaje_id=801)
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba([k for k, a in despues["ambitos"].items() if a.get("activo", True)] == ["barcelona_local"], "...y queda solo el local")
    espia.limpiar()
    pulsa(f"ob1:H:mo:t:{[k for k, _ in orden].index('barcelona_local')}", TITULAR, mensaje_id=801)
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba(despues["ambitos"]["barcelona_local"].get("activo", True) is True, "el último ámbito activo no se puede apagar: queda como estaba")
    comprueba(any("Al menos una zona" in c.get("text", "") for c in espia.de("answerCallbackQuery")), "   y se le dice al pulsar")
    # El resto del config.json, intacto
    otras = {k for k in set(antes_disco) | set(despues) if antes_disco.get(k) != despues.get(k)}
    comprueba(otras <= {"max_distancia_km", "distancia_portal_millas", "anos_experiencia_max", "ambitos"},
              "en todo el rato solo cambian esas claves de config.json (%s)" % sorted(otras))
    # Rehacer con la encuesta
    espia.limpiar()
    pulsa("ob1:H:pu", TITULAR, mensaje_id=802)
    avisa = espia.de("editMessageText", TITULAR)[-1]["text"]
    comprueba("se pierden esas reglas" in avisa and "ob1:H:pu:r" in botones_de(espia.de("editMessageText", TITULAR)[-1]),
              "Sectores y puestos avisa de que se pierden las reglas a mano y ofrece rehacerlo")
    comprueba(perfiles.obtener(TITULAR)["datos"]["reglas"] == "titular-ciber", "   y hasta confirmar nada cambia")
    pulsa("ob1:H:pu:r", TITULAR, mensaje_id=802)
    comprueba(paso(TITULAR) == "sector" and borrador(TITULAR)["modo"] == "rehacer", "«Rehacer con la encuesta» empieza la encuesta en ese mensaje")
    pulsa("ob1:se:b", TITULAR)
    comprueba(perfiles.conversacion(TITULAR) is None and perfiles.obtener(TITULAR)["datos"]["reglas"] == "titular-ciber",
              "   y se puede salir: no se ha cambiado nada")
    # Un perfil con bloque (la plantilla): su config va en el bloque
    perfiles.guardar(B, perfiles.datos_de_plantilla(), "activo")
    escribe("/ajustes", B)
    comprueba("ob1:H:ex" in botones_de(ultima_pantalla(B)), "quien entró con la plantilla tiene el mismo menú de reglas a mano")
    disco_antes = (DATOS / "config.json").read_bytes()
    pulsa("ob1:H:ex:s:0", B, mensaje_id=810)
    bloque = perfiles.obtener(B)["datos"]["config"]
    comprueba(bloque["anos_experiencia_max"] == 0 and (DATOS / "config.json").read_bytes() == disco_antes,
              "   su cambio va a SU bloque `config`, no a config.json (el del titular)")
    comprueba(perfiles.obtener(B)["datos"]["historial"][0]["antes"] == {"anos_experiencia_max": 1}, "   con la anterior en su historial")
    # 🏠 Solo remoto, también en un perfil con reglas a mano: apaga los ámbitos que no son remotos y nada más
    activos_antes = {k: v.get("activo", True) for k, v in perfiles.config_efectiva(perfiles.obtener(B), config())["ambitos"].items()}
    comprueba(pulsa("ob1:E:sr", B, mensaje_id=811) == "boton:ob1:solo_remoto", "🏠 Solo remoto en un perfil con reglas a mano")
    despues_b = perfiles.config_efectiva(perfiles.obtener(B), config())["ambitos"]
    comprueba(all(not v.get("activo", True) for v in despues_b.values() if not v.get("is_remote"))
              and {k: v.get("activo", True) for k, v in despues_b.items() if v.get("is_remote")}
              == {k: a for k, a in activos_antes.items() if despues_b[k].get("is_remote")},
              "   apaga solo los locales; los remotos siguen como estaban")
    comprueba(perfiles.obtener(B)["datos"]["historial"][-1]["seccion"] == "sr", "   y deja la anterior en el historial")
    comprueba(pulsa("ob1:E:sr", B, mensaje_id=811) == "boton:ob1:ya_solo_remoto", "   y pulsarlo otra vez no repite nada")
    # Y los ajustes de uno no tocan al otro
    comprueba("historial" not in perfiles.obtener(A)["datos"] or all(h["seccion"] != "ex" for h in perfiles.obtener(A)["datos"].get("historial", [])),
              "   nada de eso aparece en el perfil de A")


def caso_rehacer_y_confirmar_a_mano() -> None:
    print("\n[8] Rehacer un perfil con reglas a mano: copia de lo anterior y vuelta atrás")
    efectiva_antes = perfiles.config_efectiva(perfiles.obtener(B), config())
    pulsa("ob1:H:pu:r", B, mensaje_id=820)
    antes = perfiles.obtener(B)["datos"]
    for accion in [("p", "ob1:se:t:it"), ("p", "ob1:se:n"), ("p", "ob1:pu:t:it.ciber"), ("p", "ob1:pu:n"), ("p", "ob1:ex:s:ninguna"),
                   ("p", "ob1:ex:n"), ("p", "ob1:ex:p:0"), ("p", "ob1:fo:k"), ("p", "ob1:fo:k"), ("t", "Barcelona"), ("p", "ob1:ci:y"),
                   ("p", "ob1:di:s:10"), ("p", "ob1:mo:t:presencial"), ("p", "ob1:mo:n"), ("p", "ob1:id:n"), ("p", "ob1:jo:k"),
                   ("p", "ob1:sa:k"), ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:av:n")]:
        da_paso(B, accion)
    comprueba("se sustituyen" in ultima_pantalla(B)["text"], "la ficha avisa de que se sustituyen las reglas a mano")
    lanzados.clear()
    perfiles.aplicar_cambios(B, escribir=[(oferta(777, "Técnico de soporte"), 0, 0)])  # ya tenía su lista
    pulsa("ob1:fi:c", B)
    despues = perfiles.obtener(B)["datos"]
    comprueba(despues["reglas"] == "catalogo" and despues["plan"]["area"]["nombre"] == "Barcelona", "ahora es de catálogo")
    h = despues["historial"][-1]
    comprueba(h["seccion"] == "rehacer con la encuesta" and h["datos_anteriores"]["reglas"] == antes["reglas"]
              and h["datos_anteriores"]["config"] == antes["config"], "   y lo de antes queda entero en su historial (vuelta atrás)")
    comprueba(bool(despues["ajustes_aprendidos"].get("desde")), "   lo marcado antes deja de contar para lo aprendido")
    comprueba(despues["arranque"] == {"estado": "no_hace_falta"} and despues.get("primera_pendiente") is True and lanzados == [],
              "   si ya tenía ofertas no hay arranque, y su primera corrida será limitada")
    comprueba("config" not in despues, "   y no queda ninguna clave suelta de las reglas a mano")
    comprueba("silencio" not in despues["avisos"] and "max_dia" not in despues["avisos"],
              "   y rehacer no le pone silencio ni tope que no había pedido (MP9)")
    # La vuelta atrás que explica CAMBIOS-MP8.md §2.5: con la copia del historial el perfil queda como estaba
    anterior = perfiles.obtener(B)["datos"]["historial"][-1]["datos_anteriores"]

    def volver(d):
        historial, aprendido = d.get("historial"), d.get("ajustes_aprendidos") or {}
        d.clear()
        d.update(anterior)
        d["historial"] = historial
        aprendido.pop("desde", None)
        if aprendido:
            d["ajustes_aprendidos"] = aprendido

    perfiles.actualizar_datos(B, volver)
    vuelto = perfiles.obtener(B)
    comprueba(vuelto["datos"]["reglas"] == "titular-2026-10" and perfiles.config_efectiva(vuelto, config()) == efectiva_antes,
              "   y la vuelta atrás desde el historial deja la config efectiva exactamente como estaba")


def caso_recordatorio() -> None:
    print("\n[9] Recordatorio a las 24 h (una vez) y borrado a los 7 días")
    perfiles.guardar(C, {"v": 1, "reglas": "catalogo", "plan": {}}, "encuesta")
    perfiles.borrar_conversacion(C)
    b = enc._nuevo_borrador("encuesta", {"descubrimiento": True})
    b.update(paso="sector")
    perfiles.guardar_conversacion(C, "enc:sector", b, 900)
    ahora = datetime.now()
    espia.limpiar()
    comprueba(enc.mantenimiento(config(), ahora + timedelta(hours=23), forzar=True) == {"recordadas": 0, "borradas": 0},
              "a las 23 h, nada")
    comprueba(enc.mantenimiento(config(), ahora + timedelta(hours=25), forzar=True) == {"recordadas": 1, "borradas": 0}
              and any("a medias" in t for t in espia.textos(C)), "a las 25 h, un recordatorio")
    espia.limpiar()
    comprueba(enc.mantenimiento(config(), ahora + timedelta(hours=49), forzar=True) == {"recordadas": 0, "borradas": 0}
              and not espia.textos(C), "   y no se repite")
    comprueba(perfiles.conversacion(C) is not None and perfiles.conversacion(C)["actualizado"] <= (ahora + timedelta(seconds=5)).isoformat(timespec="seconds"),
              "   y recordar no cuenta como actividad (el reloj de los 7 días sigue)")
    comprueba(enc.mantenimiento(config(), ahora + timedelta(days=6, hours=23), forzar=True)["borradas"] == 0, "a los 6 días y 23 h, el borrador sigue")
    comprueba(enc.mantenimiento(config(), ahora + timedelta(days=7, minutes=1), forzar=True)["borradas"] == 1
              and perfiles.conversacion(C) is None, "a los 7 días, el borrador se borra")
    comprueba(perfiles.obtener(C)["estado"] == "encuesta", "   el perfil sigue en «encuesta»: un /start empieza de nuevo")
    # Una conversación de otra persona no se recuerda con la de C
    b2 = enc._nuevo_borrador("encuesta", {"descubrimiento": True})
    b2.update(paso="sector")
    perfiles.guardar_conversacion(A, "enc:sector", b2, 901)
    espia.limpiar()
    enc.mantenimiento(config(), ahora + timedelta(hours=25), forzar=True)
    comprueba(any("a medias" in t for t in espia.textos(A)) and not espia.textos(TITULAR), "el recordatorio es de cada persona, y solo a ella")
    perfiles.borrar_conversacion(A)


def caso_rotulos_y_privacidad() -> None:
    print("\n[10] Privacidad: ningún chat_id en un botón, ni un dato de otro en un mensaje")
    espia.limpiar()
    perfiles.guardar(C, {"v": 1, "reglas": "catalogo", "plan": {}}, "encuesta")
    perfiles.borrar_conversacion(C)
    perfiles.borrar_conversacion(A)
    enc.iniciar(type("X", (), {"chat_id": C, "token": TOKEN, "config": config(), "perfil": perfiles.obtener(C), "es_titular": False,
                               "decir": lambda self, *a, **k: None})())
    pulsa("ob1:0:go", C)
    pulsa("ob1:se:t:comercial", C)
    comprueba(str(C) not in json.dumps([c["cuerpo"].get("reply_markup") for c in espia.llamadas]), "ningún teclado lleva el chat_id de nadie")
    comprueba(TOKEN not in json.dumps(espia.llamadas), "el token no está en ningún cuerpo de lo enviado")
    # Cada conversación solo aparece para su chat
    b = enc._nuevo_borrador("encuesta", {"descubrimiento": True, "sectores": ["it"]})
    b.update(paso="sector")
    perfiles.guardar_conversacion(A, "enc:sector", b, 910)
    comprueba(perfiles.conversacion(C)["borrador"]["r"].get("sectores") == ["comercial"] and perfiles.conversacion(A)["borrador"]["r"]["sectores"] == ["it"],
              "cada chat lee su borrador: ninguna lectura cruza chats")
    perfiles.borrar_conversacion(A)
    perfiles.borrar_conversacion(C)


def caso_regenerar_planes() -> None:
    print("\n[12] perfilado.regenerar: un arreglo del generador le llega a quien ya tenía plan, y a nadie más")
    from perfilado import regenerar

    R1, R2, R3 = 7001, 7002, 7003
    copia = lambda x: json.loads(json.dumps(x))  # noqa: E731
    r = {"sectores": ["admin"], "puestos": ["admin.recepcion"], "otros": ["Logopeda"], "experiencia": "mas_10",
         "ubicacion": {"nombre": "Barcelona", "lat": 41.39, "lon": 2.17, "radio_km": 5}, "modalidad": ["presencial"],
         "idiomas": ["es"], "umbral": 4.5}
    plan_de_hoy = enc.plan_de(r)
    # El plan que hacía el generador hasta el 11-11: lo escrito en «Otro», solo como palabra clave.
    plan_de_antes = copia(plan_de_hoy)
    plan_de_antes["puestos"] = [p for p in plan_de_hoy["puestos"] if not p["id"].startswith("otro:")]
    datos = {**r, "v": 1, "reglas": "catalogo", "plan": plan_de_antes,
             "avisos": {"modo": "diario", "max_dia": 7, "descubrimiento": True, "silencio": [23, 7]},
             "calibracion": {"estado": "hecha", "base": 5.0}, "arranque": {"estado": "hecho"},
             "ajustes_aprendidos": {"desde": "2026-11-08T18:31:03"}}
    perfiles.guardar(R1, copia(datos), "activo")
    perfiles.guardar(R2, copia(datos), "pausado")
    sin_otro = {k: v for k, v in r.items() if k != "otros"}
    perfiles.guardar(R3, {**copia(datos), **{"otros": []}, "plan": enc.plan_de(sin_otro)}, "activo")
    foto = {p["chat_id"]: (p["version"], p["actualizado"], json.dumps(p["datos"], sort_keys=True)) for p in perfiles.listar()}
    de_catalogo = [c for c, (_, _, d) in foto.items() if json.loads(d).get("reglas") == "catalogo"]
    comprueba(len(foto) > len(de_catalogo) >= 3, "hay perfiles de reglas a mano (el titular) y de catálogo en la misma base")

    def nota(chat):
        perfil = perfiles.obtener(chat)
        punt = perfiles.puntuador_de(perfil, config())
        fila = {"title": "Logopeda", "location": "Barcelona, Spain", "description": None, "is_remote": None}
        return buscador.explicar_relevancia("Logopeda", "Barcelona, Spain", None, None, punt, fila)[0]

    comprueba(nota(R1) == 2.5, "antes: con el plan viejo, el puesto que escribió vale 2,5 en su ciudad (listón 4,5: no llega)")

    # El ensayo no escribe nada.
    dicho: list[str] = []
    cuenta = regenerar.regenerar(False, dicho.append)
    texto = "\n".join(dicho)
    comprueba(cuenta["cambian"] >= 1 and cuenta["escritos"] == 0 and "Ensayo: no se ha escrito nada" in texto,
              "el ensayo dice que hay planes que cambian y que no ha escrito nada")
    comprueba({p["chat_id"]: (p["version"], p["actualizado"], json.dumps(p["datos"], sort_keys=True)) for p in perfiles.listar()} == foto,
              "   y es verdad: ningún perfil ha cambiado ni de versión ni de fecha")
    comprueba("otro" not in texto.lower() or "Logopeda" in texto, "   enseña qué puesto gana el plan")
    comprueba(not any(str(c) in texto for c in foto), "   sin ningún chat_id: los perfiles van numerados")

    # Con --confirmar.
    dicho.clear()
    cuenta = regenerar.regenerar(True, dicho.append)
    uno = perfiles.obtener(R1)
    comprueba(cuenta["escritos"] >= 1 and uno["datos"]["plan"] == plan_de_hoy and uno["version"] == foto[R1][0] + 1,
              "al confirmar, el activo pasa al plan de hoy y su versión sube")
    comprueba(nota(R1) == 5.0, "   y el puesto que escribió vale 5,0: ahora sí llega a su listón")
    resto = {k: v for k, v in uno["datos"].items() if k not in ("plan", "historial")}
    comprueba(resto == {k: v for k, v in datos.items() if k != "plan"} and uno["datos"]["plan"]["umbral"] == 4.5,
              "   sin tocar nada más: sus respuestas, su listón (4,5), sus avisos, su calibración y lo aprendido")
    ultimo = uno["datos"]["historial"][-1]
    comprueba(ultimo["seccion"] == "plan regenerado" and ultimo["plan_anterior"] == plan_de_antes and ultimo["fecha"],
              "   el plan de antes queda entero en su historial, con fecha (regla 7)")
    dos = perfiles.obtener(R2)
    comprueba((dos["version"], dos["actualizado"], json.dumps(dos["datos"], sort_keys=True)) == foto[R2],
              "el pausado no se toca: su `actualizado` es el reloj de la pausa")
    tres = perfiles.obtener(R3)
    comprueba((tres["version"], tres["actualizado"]) == foto[R3][:2], "el que ya tenía el plan de hoy no se reescribe")
    a_mano = [c for c in foto if c not in de_catalogo]
    comprueba(all((p["version"], p["actualizado"], json.dumps(p["datos"], sort_keys=True)) == foto[p["chat_id"]]
                  for p in perfiles.listar() if p["chat_id"] in a_mano),
              "ni los de reglas a mano (%d): no tienen plan que generar" % len(a_mano))
    comprueba(not any(str(c) in "\n".join(dicho) for c in foto), "   y tampoco al confirmar sale ningún chat_id")
    comprueba(not espia.llamadas or all(str(c["cuerpo"].get("chat_id")) not in (str(R1), str(R2), str(R3)) for c in espia.llamadas),
              "no se le manda nada a nadie (regla 1)")

    # Idempotente.
    version = perfiles.obtener(R1)["version"]
    cuenta = regenerar.regenerar(True, lambda *_: None)
    comprueba(cuenta["escritos"] == 0 and cuenta["cambian"] == 0 and perfiles.obtener(R1)["version"] == version,
              "repetirlo no escribe nada")

    # Unas respuestas que ya no generan (un puesto que se quitó del catálogo) no paran a los demás.
    perfiles.actualizar_datos(R3, lambda d: d.update(puestos=["admin.ya_no_existe"]))
    perfiles.actualizar_datos(R1, lambda d: d.update(plan=copia(plan_de_antes)))
    dicho.clear()
    cuenta = regenerar.regenerar(True, dicho.append)
    comprueba(cuenta["fallidos"] == 1 and perfiles.obtener(R1)["datos"]["plan"] == plan_de_hoy
              and "ya no generan un plan" in "\n".join(dicho),
              "un perfil cuyas respuestas ya no generan se queda con su plan, se dice, y los demás siguen")
    comprueba(regenerar.main(["--no-existe"]) == 2, "una opción que no existe no hace nada")

    # Lo que la encuesta le da al generador: el «Otro» del sector y el de los puestos van por separado.
    escrito = enc.plan_de({**r, "sectores": [], "puestos": [], "sector_otro": "Paisajismo", "otros": ["auxiliar vivero"]})
    comprueba([p["id"] for p in escrito["puestos"]] == ["otro:auxiliar vivero"]
              and escrito["claves_mas"] == ["Paisajismo", "auxiliar vivero"],
              "desde la encuesta: el puesto escrito es un puesto y el sector escrito, una palabra clave")
    for chat in (R1, R2, R3):
        perfiles.copia_y_borrado(chat)


CASOS = [caso_conversacion_completa, caso_reinicio, caso_navegacion_y_texto, caso_todo_cabe, caso_arranque_y_calibracion,
         caso_ficha_honesta, caso_ajustes_de_catalogo, caso_ajustes_a_mano, caso_rehacer_y_confirmar_a_mano, caso_recordatorio, caso_rotulos_y_privacidad,
         caso_regenerar_planes]


def main() -> int:
    for caso in CASOS:
        try:
            caso()
        except Exception as e:  # una excepción es un fallo, no una prueba que no corre
            import traceback

            traceback.print_exc()
            comprueba(False, "%s ha reventado: %r" % (caso.__name__, e))
    perfiles.cerrar()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
