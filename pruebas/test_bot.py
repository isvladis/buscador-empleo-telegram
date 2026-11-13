# -*- coding: utf-8 -*-
"""
Pruebas del bot que escucha (MP5, ver telegram_bot.py): invitaciones, comandos de cada persona y
del titular, el emisor con ritmo, 429 y 403, y el servicio sin Flask.

Ninguna prueba llama a Telegram ni a ningún portal: requests.post se sustituye por un espía que
apunta lo que se habría enviado y contesta como la API. El token y los chat_id son inventados.

    python pruebas/test_bot.py
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-bot-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B, C, D = 1111111, 2222222, 3333333, 4444444, 5555555
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
# Lo mismo que el config.json del servidor: los secretos vacíos y ya migrado (`_config_version`),
# que si no cargar_config lo reescribiría entero con los valores por defecto al leerlo.
CONFIG_EN_DISCO = {"_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "busqueda_automatica_horas": 1,
                   "hours_old": 38, "telegram_bot_token": "", "telegram_chat_id": "", "clave_que_no_se_toca": "sigue"}
(DATOS / "config.json").write_text(json.dumps(CONFIG_EN_DISCO, indent=2), encoding="utf-8")

import requests  # noqa: E402

import almacen  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import reglas  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402
from fuentes_extra import salud  # noqa: E402

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
    """Sustituye a requests.post y contesta como la API. `respuestas[metodo]` fuerza códigos."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.respuestas: dict[str, list] = {}
        self.updates: list[dict] = []
        self.mensaje_id = 100

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        cuerpo = json if json is not None else dict(data or {})
        self.llamadas.append({"metodo": metodo, "cuerpo": cuerpo, "files": files})
        cola = self.respuestas.get(metodo)
        if cola:
            codigo, datos = cola.pop(0)
            return Resp(datos, codigo)
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        if metodo == "getUpdates":
            _dormir_de_verdad(0.005)
            lote, self.updates = self.updates[:1], self.updates[1:]
            return Resp({"ok": True, "result": lote})
        if metodo in ("sendMessage", "sendDocument"):
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


_dormir_de_verdad = time.sleep
espia = Espia()
requests.post = espia.post
esperas: list[float] = []
notificaciones.time.sleep = esperas.append  # el mismo módulo time para todos


def config() -> dict:
    return config_mod.cargar_config()


def mensaje(texto, chat=TITULAR, tipo="private", n=[0]):
    n[0] += 1
    return {"update_id": n[0], "message": {"message_id": n[0], "text": texto,
                                           "chat": {"id": chat, "type": tipo}, "from": {"id": chat}}}


def pulsacion(datos, chat=TITULAR, mensaje_id=77, n=[1000]):
    n[0] += 1
    return {"update_id": n[0], "callback_query": {"id": "cb%d" % n[0], "data": datos, "from": {"id": chat},
                                                  "message": {"message_id": mensaje_id,
                                                              "chat": {"id": chat, "type": "private"}}}}


def escribe(texto, chat=TITULAR):
    return bot.procesar(mensaje(texto, chat), config())


def pulsa(datos, chat=TITULAR):
    return bot.procesar(pulsacion(datos, chat), config())


def botones(teclado) -> list[str]:
    return [b["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]


def ultimo_teclado(chat=TITULAR) -> dict:
    con = [c for c in espia.llamadas if str(c["cuerpo"].get("chat_id")) == str(chat) and "reply_markup" in c["cuerpo"]]
    return con[-1]["cuerpo"]["reply_markup"] if con else {}


def ultimo_texto(chat=TITULAR) -> str:
    con = [c for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")
           and str(c["cuerpo"].get("chat_id")) == str(chat)]
    return con[-1]["cuerpo"].get("text", "") if con else ""


def oferta(n, titulo, empresa, horas=1, relevancia=6.0):
    cuando = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
    return {"job_url": f"https://www.linkedin.com/jobs/view/{n}", "title": titulo, "company": empresa,
            "location": "Barcelona, Catalonia, Spain", "site": "linkedin", "is_remote": False,
            "description": f"Descripción de la oferta {n}: soporte técnico nivel 1, sin experiencia.",
            "relevancia": relevancia, "motivos_relevancia": [["motivo de la oferta %d" % n, 2.0]],
            "visto_por_primera_vez": cuando, "visto_por_ultima_vez": cuando}


def nueva_invitacion(plantilla="soporte") -> str:
    """La que crea el titular con /invitar y su botón: devuelve el código del enlace."""
    escribe("/invitar")
    pulsa(f"adm1:inv:{plantilla}")
    enlace = re.search(r"https://t\.me/bot_de_prueba\?start=([A-Za-z0-9_-]+)", ultimo_texto())
    return enlace.group(1) if enlace else ""


def restos_de(chat_id: int) -> list[str]:
    """Todo lo que en la base sigue diciendo algo de este chat_id: tablas y claves de `meta`."""
    conn = perfiles.conexion()
    restos = []
    for tabla in ("perfiles", "ofertas_perfil", "marcas", "avisos", "conversacion", "pendientes"):
        if conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE chat_id = ?", (chat_id,)).fetchone()[0]:
            restos.append(tabla)
    if conn.execute("SELECT COUNT(*) FROM invitaciones WHERE usada_por = ?", (chat_id,)).fetchone()[0]:
        restos.append("invitaciones")
    restos += [f"meta:{c}" for (c,) in conn.execute("SELECT clave FROM meta") if c.rpartition(":")[2] == str(chat_id)]
    return restos


def sin_nada_de_ofertas(texto: str, chat_id: int) -> bool:
    """¿El texto no lleva ningún título, empresa, URL ni motivo de lo que tiene ese perfil?"""
    prohibido = set()
    for o in perfiles.ofertas_de(chat_id):
        prohibido |= {o["job_url"], o.get("title"), o.get("company")}
        prohibido |= {m[0] for m in o.get("motivos_relevancia") or []}
    for info in [i for grupo in perfiles.marcas(chat_id).values() for i in grupo.values()]:
        prohibido |= {info.get("titulo"), info.get("motivo")}
    for aviso in perfiles.avisos(chat_id).values():
        prohibido |= {aviso.get("titulo"), aviso.get("url")}
    return not any(p and p in texto for p in prohibido)


# ----------------------------------------------------------------------
def caso_invitaciones() -> None:
    print("\n[1] Invitaciones: válida, usada dos veces, caducada, inventada y el tope")
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    codigo = nueva_invitacion("soporte")
    comprueba(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", codigo)), "el código cabe en `start`: [A-Za-z0-9_-], ≤ 64")
    comprueba(len(codigo) >= 20, "   y no se adivina (%d caracteres)" % len(codigo))
    comprueba(TOKEN not in ultimo_texto() and str(TITULAR) not in ultimo_texto(), "el enlace no lleva el token ni el chat_id")
    espia.limpiar()
    comprueba(escribe("/start " + codigo, A) == "start:alta", "con el enlace, alta")
    perfil = perfiles.obtener(A)
    comprueba(perfil["estado"] == "activo" and perfil["datos"] == perfiles.datos_de_plantilla(),
              "   con la plantilla: activo y titular-2026-10 con el tope de 1 año")
    bienvenida = " ".join(espia.textos(A))
    comprueba("No ve qué ofertas recibes" in bienvenida and "/borrar" in bienvenida,
              "   la bienvenida dice qué ve el titular y cómo borrarlo todo (D9)")
    comprueba(any("Alta nueva" in t for t in espia.textos(TITULAR)), "   y al titular le llega el alta")
    comprueba(not any(str(A) in t for t in espia.textos(TITULAR)), "   sin el número de nadie")

    espia.limpiar()
    comprueba(escribe("/start " + codigo, B) == "start:usada", "el mismo código otra vez: usado")
    comprueba(perfiles.obtener(B) is None and any("ya no vale" in t for t in espia.textos(B)),
              "   no entra, y se le dice que pida otra (el código existió)")
    vieja = perfiles.crear_invitacion("soporte", dias=-1)
    espia.limpiar()
    comprueba(escribe("/start " + vieja, B) == "start:caducada" and perfiles.obtener(B) is None,
              "una caducada: tampoco entra")
    espia.limpiar()
    for intento in ("/start inventado123", "/start no vale!", "/start", "hola", "/ayuda", "/perfiles"):
        escribe(intento, B)
    comprueba(espia.textos(B) == [], "un código inventado, mal escrito o ningún código: silencio (D4)")
    comprueba(escribe("/estado", B) == "silencio", "   y los comandos, también")

    print("\n    Dos a la vez con el mismo código: un solo perfil")
    doble = perfiles.crear_invitacion("encuesta")
    resultados: list[str] = []
    hilos = [threading.Thread(target=lambda c=c: resultados.append(perfiles.usar_invitacion(doble, c, config())))
             for c in (C, D)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    comprueba(sorted(resultados) == ["alta", "usada"], "uno entra y el otro no (%s)" % sorted(resultados))
    dentro = C if perfiles.obtener(C) else D
    fuera = D if dentro == C else C
    comprueba(perfiles.obtener(dentro)["estado"] == "encuesta" and perfiles.obtener(fuera) is None,
              "   el que entra, con la encuesta: estado «encuesta»")
    if dentro != C:  # que el resto de la prueba tenga a C dentro
        perfiles.borrar(D)
        perfiles.usar_invitacion(perfiles.crear_invitacion("encuesta"), C, config())

    print("\n    El tope (3, con el titular)")
    espia.limpiar()
    escribe("/invitar")
    comprueba(any("no queda sitio" in t for t in espia.textos(TITULAR)) and not ultimo_teclado().get("inline_keyboard"),
              "/invitar con el tope lleno: no ofrece crear nada")
    llena = perfiles.crear_invitacion("soporte")
    espia.limpiar()
    comprueba(escribe("/start " + llena, D) == "start:sin_hueco" and perfiles.obtener(D) is None,
              "un código bueno con el tope lleno: no entra")
    comprueba(any("no queda sitio" in t for t in espia.textos(D)), "   y se le dice")


def caso_plantilla() -> None:
    print("\n[2] La plantilla sale del juego, no de config.json")
    juego = reglas.obtener("titular-2026-10")
    cfg = config()
    del_adicional = {"v": 1, "reglas": "titular-2026-10", "experiencia_max_exigida": 1, "experiencia_estricta": True,
                     "avisos": {"modo": "instantaneo"}, "config": {**juego.config, "anos_experiencia_max": 1}}
    efectiva = perfiles.config_efectiva({"chat_id": None, "version": 0, "datos": perfiles.datos_de_plantilla()}, cfg)
    comprueba(efectiva == perfiles.config_efectiva({"chat_id": None, "version": 0, "datos": del_adicional}, cfg),
              "su config efectiva es la del bloque que tenía el adicional (copia del 01-11)")
    ciber = {**cfg, **reglas.obtener("titular-ciber").config}
    comprueba(perfiles.config_efectiva({"chat_id": None, "version": 0, "datos": perfiles.datos_de_plantilla()}, ciber)
              ["categorias"] == juego.config["categorias"], "aunque config.json sea ya el de ciber (MP4)")
    copiada = {**del_adicional, "config": {c: ciber[c] for c in reglas.CLAVES_DE_LA_PERSONA if c in ciber}}
    copiada["config"]["anos_experiencia_max"] = 1
    comprueba(perfiles.config_efectiva({"chat_id": None, "version": 0, "datos": copiada}, ciber)["categorias"]
              != juego.config["categorias"], "   (control: copiar de config.json, como hacía MP3, daría los términos de ciber)")


def sembrar() -> None:
    """Ofertas del titular, de A y una que comparten, con marcas y avisos de cada uno."""
    ahora = datetime.now().isoformat(timespec="seconds")
    del_titular = oferta(1, "Analista SOC Del Titular", "Empresa Del Titular", relevancia=7.5)
    de_a = oferta(2, "Técnico Helpdesk Solo De A", "Empresa Secreta De A", relevancia=6.5)
    comun = oferta(3, "Técnico Soporte Compartido", "Empresa Compartida", relevancia=6.0)
    perfiles.aplicar_cambios(TITULAR, escribir=[(del_titular, 0, 0), (comun, 0, 1)], ultima_busqueda=ahora)
    perfiles.aplicar_cambios(A, escribir=[(de_a, 0, 0), (comun, 0, 1)], ultima_busqueda=ahora)
    perfiles.fijar_marca(A, de_a["job_url"], "favorito", {"title": de_a["title"], "company": de_a["company"]},
                         {"origen": "telegram", "motivo": "motivo privado de A"})
    perfiles.fijar_marca(TITULAR, del_titular["job_url"], "favorito", {"title": del_titular["title"]})
    tb.registrar_aviso({**de_a, "relevancia": 6.5}, A)
    tb.registrar_aviso({**del_titular, "relevancia": 7.5}, TITULAR)
    perfiles.anotar_respuesta(A, tb.clave_de(de_a["job_url"]), "favorito")


def caso_perfiles_y_ficha() -> None:
    print("\n[3] /perfiles y la ficha: solo el titular, y sin nada de ofertas")
    espia.limpiar()
    comprueba(escribe("/perfiles", A) == "comando:desconocido", "otro perfil no tiene /perfiles")
    comprueba(espia.textos(A) == ["No conozco ese comando. Mira /ayuda."], "   ni se le da nada de los demás")
    comprueba(pulsa("adm1:fi:" + bot._ref(A), A) == "boton:rechazado", "   ni puede abrir una ficha con el botón")
    escribe("/perfiles")
    lista = ultimo_texto()
    comprueba("3 de 3" in lista and str(A) not in lista and str(C) not in lista, "el titular ve la lista, sin chat_id")
    fichas = [d for d in botones(ultimo_teclado()) if d.startswith("adm1:fi:")]
    comprueba(len(fichas) == 3 and all(str(c) not in "".join(fichas) for c in (TITULAR, A, C)),
              "un botón por perfil, que tampoco lleva el número")
    pulsa("adm1:fi:" + bot._ref(A))
    texto = ultimo_texto()
    comprueba("titular-2026-10" in texto and "junior soc" in texto and "hasta 1 año" in texto and "Barcelona" in texto,
              "la ficha de A: juego, términos, experiencia y zona")
    comprueba("se descartan" in texto, "   y que el tope es estricto")
    comprueba("Avisos en 7 días: 1" in texto and "100 % de 1" in texto, "   con sus cifras (D9)")
    comprueba(sin_nada_de_ofertas(texto, A), "sin títulos, empresas, URL ni motivos de A")
    comprueba(sin_nada_de_ofertas(texto, TITULAR), "   ni del titular")
    comprueba(not sin_nada_de_ofertas(texto + " Técnico Helpdesk Solo De A", A),
              "   (control: si la ficha llevara una oferta suya, la comprobación lo vería)")
    comprueba(str(A) not in texto, "   ni su chat_id")
    pulsa("adm1:pa:" + bot._ref(A))
    comprueba(perfiles.obtener(A)["estado"] == "pausado", "pausar desde la ficha")
    pulsa("adm1:re:" + bot._ref(A))
    comprueba(perfiles.obtener(A)["estado"] == "activo", "y reanudar")
    pulsa("adm1:fi:" + bot._ref(TITULAR))
    comprueba("adm1:bo:" + bot._ref(TITULAR) not in botones(ultimo_teclado()), "la ficha del titular no tiene «borrar»")
    espia.limpiar()
    escribe("/perfil", A)
    comprueba(sin_nada_de_ofertas(espia.textos(A)[0], A) and "Tu ficha" in espia.textos(A)[0], "/perfil: su ficha, igual de limpia")


def caso_top_y_misdatos() -> None:
    print("\n[4] /top y /misdatos: lo suyo y nada de otro")
    espia.limpiar()
    escribe("/top", A)
    textos = " ".join(espia.textos(A))
    comprueba("Compartido" in textos, "/top de A: sus ofertas")
    comprueba("Solo De A" not in textos, "   sin la que ya marcó con ⭐ (MP9: «sin marcar»)")
    # Se le quita la marca para seguir con lo de antes: botones, tipo «top» y que ni él ni nadie la ve cambiar de dueño.
    almacen.quitar_marcas("https://www.linkedin.com/jobs/view/2", chat_id=A)
    espia.limpiar()
    escribe("/top", A)
    textos = " ".join(espia.textos(A))
    comprueba("Solo De A" in textos and "Compartido" in textos, "   y sin marca, vuelve a salir")
    comprueba("Del Titular" not in textos, "   y ninguna del titular")
    con_botones = [c for c in espia.de("sendMessage", A) if "reply_markup" in c]
    comprueba(len(con_botones) == 2 and all(d.split(":")[0] in ("f", "a", "o") for c in con_botones
                                            for d in botones(c["reply_markup"])), "   cada una con los botones de siempre")
    clave = tb.clave_de("https://www.linkedin.com/jobs/view/3")
    comprueba(perfiles.aviso(A, clave) is not None and pulsa("f:" + clave, A) == "favorito", "   y los botones funcionan")
    cifras = perfiles.cifras_de_avisos(A)
    comprueba(cifras["semana"] == 1, "lo enseñado con /top no cuenta como aviso en las cifras")
    comprueba(perfiles.conexion().execute("SELECT tipo FROM avisos WHERE chat_id = ? AND clave = ?",
                                          (A, tb.clave_de("https://www.linkedin.com/jobs/view/2"))).fetchone()[0] == "aviso",
              "   y una oferta ya avisada sigue siendo «aviso» aunque salga en /top")
    espia.limpiar()
    escribe("/top", C)
    comprueba(any("preparación" in t for t in espia.textos(C)), "/top de quien está en encuesta: «en preparación»")

    espia.limpiar()
    escribe("/misdatos", A)
    documentos = [c for c in espia.llamadas if c["metodo"] == "sendDocument"]
    comprueba(len(documentos) == 1 and documentos[0]["cuerpo"]["chat_id"] == str(A), "/misdatos manda un fichero, a su chat")
    contenido = json.loads(gzip.decompress(documentos[0]["files"]["document"][1]))
    titulos = {o["title"] for o in contenido["ofertas"]}
    comprueba(titulos == {"Técnico Helpdesk Solo De A", "Técnico Soporte Compartido"}, "   con sus ofertas")
    crudo = json.dumps(contenido, ensure_ascii=False)
    comprueba("Del Titular" not in crudo and str(TITULAR) not in crudo, "   y nada del titular, ni su número")
    comprueba(contenido["perfil"]["datos"] == perfiles.datos_de_plantilla() and contenido["marcas"]["favoritas"],
              "   con su ficha y sus marcas")
    comprueba(all("description" not in o for o in contenido["ofertas"]), "   sin el texto de los anuncios")
    espia.limpiar()
    escribe("/misdatos")
    propio = json.loads(gzip.decompress([c for c in espia.llamadas if c["metodo"] == "sendDocument"][0]["files"]["document"][1]))
    comprueba("Analista SOC Del Titular" in {o["title"] for o in propio["ofertas"]},
              "   (control: el del titular sí lleva la suya)")


def caso_borrar() -> None:
    print("\n[5] /borrar: todo lo de ese chat y nada de otro")
    import buscador

    perfiles.meta_set(f"adicional_creado:{A}", "2026-11-01T10:00:00")
    perfiles.meta_set(f"adicional_avisado:{A}", "2026-11-01T10:05:00")
    perfiles.meta_set(f"otra_cosa:1{A}", "x")  # acaba en sus cifras, pero es otro número
    cfg = config()
    plan = buscador.planificar_consultas(perfiles.activos(), cfg)
    perfiles.anotar_consultas([(c.clave, perfiles.a_json(c.busqueda["params"]), perfiles.a_json(c.perfiles)) for c in plan],
                              {}, datetime.now().isoformat(timespec="seconds"))
    comprueba(any(str(A) in f[0] for f in perfiles.conexion().execute("SELECT perfiles FROM consultas")),
              "   (control: el plan de consultas lo nombra)")
    del_titular_antes = (perfiles.contar_ofertas(TITULAR), perfiles.marcas(TITULAR), perfiles.avisos(TITULAR))
    restos = restos_de(A)
    comprueba({"perfiles", "ofertas_perfil", "marcas", "avisos", "invitaciones",
               f"meta:adicional_creado:{A}", f"meta:adicional_avisado:{A}"} <= set(restos),
              "   (control: la comprobación ve lo que hay de A: %s)" % ", ".join(sorted(restos)))

    espia.limpiar()
    escribe("/borrar", A)
    confirmar = botones(ultimo_teclado(A))
    comprueba(confirmar[0].startswith("aj1:b1:") and confirmar[1] == "aj1:no", "pide confirmación")
    comprueba(any("copia" in t for t in espia.textos(A)), "   y dice que queda una copia (decidido el 03-11)")
    pulsa("aj1:b1:%d" % (bot._minuto() - bot.MINUTOS_DE_UNA_CONFIRMACION - 1), A)
    comprueba(perfiles.obtener(A) is not None, "una confirmación de hace rato no borra nada")
    pulsa("aj1:no", A)
    comprueba(perfiles.obtener(A) is not None, "«No» tampoco")
    pulsa(confirmar[0], A)
    comprueba(restos_de(A) == [], "con «Sí» no queda nada suyo: ni filas ni claves de meta (%s)" % restos_de(A))
    comprueba(perfiles.meta_get(f"otra_cosa:1{A}") == "x", "   la clave de otro número que acaba igual, sigue")
    conn = perfiles.conexion()
    comprueba(conn.execute("SELECT COUNT(*) FROM ofertas WHERE url = ?",
                           ("https://www.linkedin.com/jobs/view/2",)).fetchone()[0] == 0,
              "su oferta solo suya se va, con su descripción")
    comprueba(perfiles.descripcion_de(TITULAR, "https://www.linkedin.com/jobs/view/3"),
              "la que compartía con el titular se queda, con su texto")
    comprueba((perfiles.contar_ofertas(TITULAR), perfiles.marcas(TITULAR), perfiles.avisos(TITULAR)) == del_titular_antes,
              "y lo del titular, idéntico")
    copias = sorted((DATOS / "borrados").iterdir()) if (DATOS / "borrados").exists() else []
    copiada = copias[-1] / "perfil.json" if copias else None
    ficha_copiada = json.loads(copiada.read_text(encoding="utf-8")) if copiada and copiada.exists() else {}
    comprueba(((ficha_copiada.get("perfil") or {}).get("datos")) == perfiles.datos_de_plantilla(),
              "antes de borrar deja copia, con su ficha (perfil.json)")
    comprueba(any("dado de baja" in t for t in espia.textos(TITULAR)) and not any(str(A) in t for t in espia.textos(TITULAR)),
              "al titular le llega la baja, sin el número")
    plan = buscador.planificar_consultas(perfiles.activos(), cfg)
    perfiles.anotar_consultas([(c.clave, perfiles.a_json(c.busqueda["params"]), perfiles.a_json(c.perfiles)) for c in plan],
                              {}, datetime.now().isoformat(timespec="seconds"))
    comprueba(not any(str(A) in f[0] for f in conn.execute("SELECT perfiles FROM consultas")),
              "tras la corrida siguiente, el plan de consultas ya no lo nombra")
    espia.limpiar()
    comprueba(escribe("/ayuda", A) == "silencio" and espia.textos(A) == [], "y después, el bot ya no le contesta")


def caso_grupos_403_429() -> None:
    print("\n[6] Grupos, 403 y 429")
    espia.limpiar()
    grupo = -1001234567
    for _ in range(3):
        bot.procesar(mensaje("/ayuda", grupo, "group"), config())
    comprueba(len(espia.textos(grupo)) == 1, "en un grupo se contesta una vez")
    bot._GRUPOS_AVISADOS.clear()
    bot.procesar(mensaje("/ayuda", grupo, "supergroup"), config())
    comprueba(len(espia.textos(grupo)) == 1, "   y tampoco después de un reinicio (queda en meta)")

    codigo = nueva_invitacion("soporte")
    escribe("/start " + codigo, A)
    perfiles.cambiar_estado(A, "pausado")
    espia.respuestas["sendMessage"] = [(403, {"ok": False, "error_code": 403,
                                             "description": "Forbidden: bot was blocked by the user"})]
    escribe("/ayuda", A)
    perfil = perfiles.obtener(A)
    comprueba(perfil["estado"] == "bloqueado" and perfil["datos"].get("antes_de_bloquear") == "pausado",
              "403 «blocked by the user» -> bloqueado, recordando que estaba pausado")
    comprueba(A not in {p["chat_id"] for p in perfiles.activos()}, "   fuera de las corridas: sin consultas ni avisos")
    escribe("/start", A)
    comprueba(perfiles.obtener(A)["estado"] == "pausado" and "antes_de_bloquear" not in perfiles.obtener(A)["datos"],
              "un /start lo devuelve a como estaba")
    espia.respuestas["sendMessage"] = [(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})]
    escribe("/ayuda")
    comprueba(perfiles.obtener(TITULAR)["estado"] == "activo", "al titular no se le bloquea nunca")
    espia.respuestas["sendMessage"] = [(403, {"ok": False, "description": "Forbidden: bot is not a member of the group chat"})]
    escribe("/ayuda", A)
    comprueba(perfiles.obtener(A)["estado"] == "pausado", "otro 403 que no es de la persona no bloquea nada")

    esperas.clear()
    espia.limpiar()
    espia.respuestas["sendMessage"] = [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 7}})]
    escribe("/ayuda")
    comprueba(7 in esperas and len(espia.de("sendMessage", TITULAR)) == 2,
              "429: espera lo que dice retry_after (7 s) y repite (%s)" % esperas)
    esperas.clear()
    espia.respuestas["sendMessage"] = [(429, {"ok": False, "parameters": {"retry_after": 100000}})]
    escribe("/ayuda")
    comprueba(notificaciones.ESPERA_MAXIMA_429_SEGUNDOS in esperas, "   con techo de %d s" % notificaciones.ESPERA_MAXIMA_429_SEGUNDOS)

    print("\n    El ritmo: un mensaje por segundo, en total")
    original = notificaciones.time.monotonic
    reloj = [5000.0]
    notificaciones.time.monotonic = lambda: reloj[0]
    try:
        esperas.clear()
        notificaciones._ultimo_envio[0] = float("-inf")
        escribe("/ayuda")
        escribe("/ayuda", C)
        comprueba(esperas == [notificaciones.PAUSA_ENTRE_MENSAJES_SEGUNDOS],
                  "dos mensajes seguidos, aunque sean a chats distintos: el segundo espera 1 s (%s)" % esperas)
        esperas.clear()
        notificaciones.llamar(TOKEN, "answerCallbackQuery", {"callback_query_id": "x"})
        notificaciones.llamar(TOKEN, "getUpdates", {})
        comprueba(esperas == [], "   contestar un botón o leer no espera: no son mensajes")
        reloj[0] += 2
        escribe("/ayuda")
        comprueba(esperas == [], "   y si ya ha pasado el segundo, no espera")
    finally:
        notificaciones.time.monotonic = original


def caso_botones_antiguos() -> None:
    print("\n[7] Los botones de las ofertas siguen marcando igual")
    o = oferta(10, "Técnico Soporte N1 Para Botones", "Botones SL")
    perfiles.aplicar_cambios(TITULAR, escribir=[(o, 0, 5)])
    clave = tb.registrar_aviso(o, TITULAR)
    with perfiles._transaccion() as conn:
        conn.execute("UPDATE perfiles SET ultima_actividad = NULL WHERE chat_id = ?", (TITULAR,))
    for datos, esperado, marca in (("f:", "favorito", {"favoritas"}), ("a:", "aplicada", {"aplicadas"}),
                                   ("o:", "oculta", {"ocultas"}), ("mx:", "motivo:x", {"ocultas"}), ("d:", "deshacer", set())):
        hecho = pulsa(datos + clave)
        marcas = {k for k, v in perfiles.marcas(TITULAR).items() if o["job_url"] in v}
        comprueba(hecho == esperado and marcas == marca, f"«{datos}» por el router nuevo -> {esperado}")
    comprueba((perfiles.obtener(TITULAR)["ultima_actividad"] or "")[:10] == datetime.now().date().isoformat(),
              "   y pulsar cuenta como actividad (D9)")
    comprueba(pulsa("f:" + clave, C) in ("rechazada", "desconocida") and o["job_url"] not in perfiles.marcas(C)["favoritas"],
              "   y un botón del titular pulsado en otro chat no marca nada")


def caso_administracion() -> None:
    print("\n[8] /estado, /corrida, /fuentes y /frecuencia")
    espia.limpiar()
    escribe("/estado")
    estado = ultimo_texto()
    comprueba("Memoria del proceso" in estado and "data/" in estado and "Jooble" in estado and "Consultas:" in estado,
              "/estado: corrida, consultas, fuentes, Jooble, memoria y data/")
    comprueba(TOKEN not in estado and str(TITULAR) not in estado, "   sin token ni chat_id")
    comprueba(escribe("/estado", C) == "comando:desconocido", "   y solo para el titular")

    # La última copia (MP10): sin copias, una de hace 5 h, una de hace 40 h y una ejecución fallida.
    copias = DATOS / "copias"
    comprueba("⚠️ Copias de perfiles.db: <b>ninguna</b>" in estado, "/estado avisa si no hay ninguna copia")
    copias.mkdir()
    ahora = datetime(2026, 11, 6, 8, 10)
    (copias / "copia-20261106_031000.tar.gz").write_bytes(b"x")
    (copias / "copia-20261105_031000.tar.gz").write_bytes(b"x")
    (copias / "copia-hola.tar.gz").write_bytes(b"x")          # no cuenta: no es nombre de copia
    linea = bot._linea_de_copias(ahora)
    comprueba(linea == "Última copia: 06-11 03:10 (hace 5,0 h) · 2 en data/copias",
              f"/estado dice la fecha de la última copia y cuántas hay: {linea!r}")
    linea = bot._linea_de_copias(ahora + timedelta(hours=35))
    comprueba(linea.startswith("⚠️") and "más de 36 h sin copia" in linea,
              "control: con 40 h sin copia, /estado lo marca (la fecha sola no bastaría)")
    (copias / "registro.log").write_text("2026-11-06 03:10:01 UTC OK copia-20261106_031000.tar.gz\n"
                                         "2026-11-07 03:10:02 UTC FALLA CopiaFallida: no hay perfiles.db\n",
                                         encoding="utf-8")
    comprueba("la última ha fallado" in bot._linea_de_copias(ahora), "/estado dice si la última ejecución falló")
    escribe("/estado")
    comprueba("Última copia: 06-11 03:10" in ultimo_texto(), "   y la línea sale en /estado de verdad")
    for f in copias.iterdir():
        f.unlink()
    copias.rmdir()

    lanzadas, cola = [], []
    bot.configurar(lanzar_corrida=lambda: lanzadas.append(1) or {"iniciada": True, "total": 33},
                   encolar=lambda nombre, trabajo: cola.append(nombre) or len(cola),
                   estado_corrida=lambda: {"activa": False, "duracion_s": 140.2, "resumen": {"nuevas": 3}})
    try:
        espia.limpiar()
        escribe("/corrida")
        comprueba(lanzadas == [1] and "33 consultas" in ultimo_texto() and cola == ["fin de /corrida"],
                  "/corrida la lanza y deja detrás el aviso de que ha acabado")
        bot.configurar(lanzar_corrida=lambda: {"error": "Ya hay una búsqueda en curso"})
        escribe("/corrida")
        comprueba("Ya hay una búsqueda en curso" in ultimo_texto(), "   si ya hay una, lo dice")
        bot._fin_de_corrida(bot.Chat(TITULAR, config()))
        comprueba("140,2 s" in ultimo_texto() and "3 oferta" in ultimo_texto(), "   y al acabar, cuánto ha tardado")
    finally:
        bot.configurar(lanzar_corrida=None, encolar=None, estado_corrida=None)

    salud_json = {"fuentes": {"infojobs": {"desactivada": True, "motivo": "página anti-bot <html>", "desactivada_en": "2026-10-29T10:00:00"},
                              "empresa:smartrecruiters-con-un-nombre-larguisimo-de-verdad": {"desactivada": True, "motivo": "403"},
                              "linkedin": {"desactivada": False}}, "incidencias_pendientes": []}
    salud.SALUD_PATH.write_text(json.dumps(salud_json), encoding="utf-8")
    espia.limpiar()
    escribe("/fuentes")
    texto, reactivar = ultimo_texto(), botones(ultimo_teclado())
    comprueba("⛔ <b>infojobs</b>" in texto and "&lt;html&gt;" in texto, "/fuentes: la desactivada, con su motivo escapado")
    comprueba(len(reactivar) == 2 and all(d.startswith("adm1:fu:") for d in reactivar), "   con [Reactivar] en las dos")
    ref = bot._ref("infojobs")
    pulsa("adm1:fu:" + ref)
    sello = [d for d in botones(ultimo_teclado()) if d.startswith("adm1:fs:")]
    comprueba(salud.fuentes()["infojobs"]["desactivada"] and len(sello) == 1, "   pide confirmación antes de reactivar")
    pulsa("adm1:fs:%s:%d" % (ref, bot._minuto() - 60))
    comprueba(salud.fuentes()["infojobs"]["desactivada"], "   una confirmación vieja no reactiva")
    pulsa(sello[0])
    comprueba(not salud.fuentes()["infojobs"]["desactivada"], "   con «Sí», reactivada (lo que antes pedía sudo docker exec)")
    comprueba(pulsa(sello[0], C) == "boton:rechazado", "   y nadie más puede")

    antes = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    escribe("/frecuencia")
    comprueba(botones(ultimo_teclado()) == ["adm1:fr:1", "adm1:fr:2", "adm1:fr:3", "adm1:fr:6", "adm1:fa:1"],
              "/frecuencia: 1, 2, 3 o 6 h, y encender")
    pulsa("adm1:fr:3")
    pulsa("adm1:fa:1")
    despues = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba(despues == {**antes, "busqueda_automatica_horas": 3, "busqueda_automatica_activa": True},
              "en config.json cambian esas dos claves y nada más")
    crudo = (DATOS / "config.json").read_text(encoding="utf-8")
    comprueba(TOKEN not in crudo and str(TITULAR) not in crudo and "hours_old" in crudo and "max_busquedas_por_corrida" not in crudo,
              "   ni secretos ni valores por defecto que nadie ha decidido")
    marca = (DATOS / "config.json").stat().st_mtime_ns
    comprueba(perfiles.escribir_claves_de_config({"busqueda_automatica_horas": 3}) is False
              and (DATOS / "config.json").stat().st_mtime_ns == marca, "   y si no cambia nada, no escribe")
    comprueba(config()["busqueda_automatica_horas"] == 3, "   el planificador lo lee en su siguiente vuelta")


def caso_comandos_registrados() -> None:
    print("\n[9] setMyCommands: general y del titular, solo si cambia")
    espia.limpiar()
    comprueba(bot.registrar_comandos(config()) is True, "la primera vez se registran")
    llamadas = espia.de("setMyCommands")
    comprueba(len(llamadas) == 2 and "scope" not in llamadas[0]
              and llamadas[1]["scope"] == {"type": "chat", "chat_id": TITULAR}, "uno general y otro con scope del titular")
    nombres = [c["command"] for c in llamadas[1]["commands"]]
    comprueba(all(re.fullmatch(r"[a-z0-9_]{1,32}", n) for n in nombres)
              and all(1 <= len(c["description"]) <= 256 for c in llamadas[1]["commands"]), "   nombres y textos válidos para la API")
    comprueba("estado" not in [c["command"] for c in llamadas[0]["commands"]], "   el general no enseña los de administración")
    espia.limpiar()
    comprueba(bot.registrar_comandos(config()) is False and not espia.de("setMyCommands"), "la segunda, nada (huella en meta)")
    original = bot.COMANDOS_DE_TODOS
    bot.COMANDOS_DE_TODOS = original + (("nuevo", "Uno nuevo"),)
    try:
        comprueba(bot.registrar_comandos(config()) is True, "si cambian, otra vez")
    finally:
        bot.COMANDOS_DE_TODOS = original
        perfiles.meta_set("huella_comandos", None)


def caso_lector() -> None:
    print("\n[10] El hilo que escucha")
    o = oferta(11, "Para El Lector", "Lector SL")
    perfiles.aplicar_cambios(TITULAR, escribir=[(o, 0, 6)])
    clave = tb.registrar_aviso(o, TITULAR)
    espia.limpiar()
    bot._BOT["nombre"] = None
    espia.updates = [pulsacion("f:" + clave), mensaje("/ayuda"), {"update_id": 9999, "message": None}]
    ids = [u["update_id"] for u in espia.updates]
    vueltas = {"n": 0}

    def parar():
        vueltas["n"] += 1
        return vueltas["n"] > 4

    bot.escuchar(lambda: config(), parar)
    pedidas = espia.de("getUpdates")
    comprueba(pedidas[0]["allowed_updates"] == ["message", "callback_query"] and pedidas[0]["timeout"] == 50,
              "pide mensajes y pulsaciones, con espera larga de 50 s")
    comprueba([p.get("offset") for p in pedidas[1:4]] == [i + 1 for i in ids],
              "   y confirma cada actualización (offset = última + 1)")
    comprueba(o["job_url"] in perfiles.marcas(TITULAR)["favoritas"] and any("Qué hace" in t for t in espia.textos(TITULAR)),
              "atiende el botón y el comando")
    comprueba(espia.de("setMyCommands") and espia.de("getMe"), "al arrancar registra el menú y pregunta su nombre")

    capturado = []

    class Recoge(logging.Handler):
        def emit(self, record):
            capturado.append(record.getMessage())

    manejador = Recoge(level=logging.DEBUG)
    logging.getLogger().addHandler(manejador)
    original = bot.procesar
    try:
        bot.procesar = lambda update, cfg: (_ for _ in ()).throw(RuntimeError("fallo con /bot%s/x" % TOKEN))
        espia.updates = [mensaje("/ayuda"), mensaje("/ayuda")]
        vueltas["n"] = 0
        bot.escuchar(lambda: config(), parar)
        comprueba(sum("No se ha podido atender" in m for m in capturado) == 2, "una excepción no lo tira: sigue con la siguiente")
        comprueba(not any(TOKEN in m for m in capturado) and any("<token>" in m for m in capturado),
                  "   y se registra con el token tachado")
        bot.procesar = original
        vueltas["n"], capturado[:] = 0, []
        espia.respuestas["getUpdates"] = [(502, {"ok": False, "description": "Bad Gateway"})] * 3
        esperas.clear()
        bot.escuchar(lambda: config(), parar)
        comprueba(esperas[:3] == [5, 10, 20], "si Telegram falla, espera 5, 10, 20 s… (%s)" % esperas[:3])
    finally:
        bot.procesar = original
        logging.getLogger().removeHandler(manejador)
        espia.respuestas.pop("getUpdates", None)
    vueltas["n"] = 0
    esperas.clear()
    espia.limpiar()
    bot.escuchar(lambda: {**config(), "telegram_bot_token": ""}, parar)
    comprueba(espia.llamadas == [] and esperas[:3] == [60, 60, 60], "sin token no llama a nada; mira cada minuto")


def caso_limites() -> None:
    print("\n[11] Ningún callback_data pasa de 64 bytes ni ningún mensaje de 4.096 caracteres")
    datos = set()
    for llamada in espia.todas:
        datos |= set(botones(llamada["cuerpo"].get("reply_markup")))
    cfg = config()
    datos |= set(botones(bot._texto_de_perfiles(cfg)[1]))
    for perfil in bot._lista_de_perfiles():
        for estado in ("activo", "pausado", "encuesta"):
            datos |= set(botones(bot._teclado_de_ficha({**perfil, "estado": estado})))
        ref = bot._ref(perfil["chat_id"])
        datos |= {f"adm1:bs:{ref}:{bot._minuto()}"}
    for activa in (True, False):
        datos |= set(botones(bot._texto_de_frecuencia({**cfg, "busqueda_automatica_activa": activa})[1]))
    datos |= set(botones(bot._texto_de_fuentes(cfg)[1] or {}))
    clave = tb.clave_de("https://www.linkedin.com/jobs/view/1")
    for teclado in [tb.teclado_inicial(clave), tb.teclado_tras("favorito", clave), tb.teclado_tras("aplicada", clave),
                    tb.teclado_tras("oculta", clave)] + [tb.teclado_tras("oculta", clave, m) for m in tb.MOTIVOS]:
        datos |= set(botones(teclado))
    datos |= {"adm1:inv:soporte", "adm1:inv:encuesta", f"aj1:b1:{bot._minuto()}", f"aj1:b2:{bot._minuto()}",
              f"adm1:fs:{bot._ref('x')}:{bot._minuto()}"}
    prefijos = {d.split(":")[0] for d in datos}
    comprueba({"adm1", "aj1", "f", "a", "o", "d"} <= prefijos and len(datos) > 25,
              "   (recorridos %d botones de %d prefijos)" % (len(datos), len(prefijos)))
    mayor = max(len(d.encode("utf-8")) for d in datos)
    comprueba(mayor <= 64, "el callback_data más largo ocupa %d bytes" % mayor)
    comprueba(len(f"adm1:inv:{'x' * 43}:{TITULAR}:{A}".encode()) > 64,
              "   (control: con el código y el chat_id dentro no cabría)")
    largo = {"v": 1, "reglas": "titular-2026-10", "experiencia_max_exigida": 1, "experiencia_estricta": True,
             "config": {"categorias": {f"c{i}": {"etiqueta": "x", "activa": True, "terminos": ["término larguísimo " * 6] * 8}
                                       for i in range(10)}}}
    textos = [c["cuerpo"].get("text", "") for c in espia.todas if c["metodo"] in ("sendMessage", "editMessageText")]
    textos += [bot.ficha({"chat_id": 9, "version": 1, "estado": "activo", "datos": largo}, cfg, 3), bot.texto_de_estado(cfg),
               bot._texto_de_perfiles(cfg)[0], bot._texto_de_fuentes(cfg)[0]]
    comprueba(max(len(t) for t in textos) <= notificaciones.MAX_CARACTERES_TELEGRAM,
              "el mensaje más largo de %d tiene %d caracteres" % (len(textos), max(len(t) for t in textos)))


def caso_servicio() -> None:
    print("\n[12] El servicio, sin Flask")
    raiz = Path(__file__).resolve().parent.parent
    proceso = subprocess.run(
        [sys.executable, "-c", "import sys; sys.modules['flask'] = None; sys.modules['werkzeug'] = None; "
                               "import app; print('ok')"],
        cwd=raiz, capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"})
    comprueba(proceso.returncode == 0 and proceso.stdout.strip().endswith("ok"),
              "app.py importa con Flask y Werkzeug prohibidos (%s)" % (proceso.stderr.strip().splitlines() or ["sin error"])[-1])
    requisitos = (raiz / "requirements.txt").read_text(encoding="utf-8").lower()
    comprueba(not any(p in requisitos for p in ("flask", "werkzeug", "jinja2")), "requirements.txt ya no los trae")
    compose = (raiz / "docker-compose.yml").read_text(encoding="utf-8")
    comprueba("ports:" not in compose and "5000" not in compose and "JOBSPY_HOST" not in compose,
              "el compose no publica ningún puerto")
    comprueba(not (raiz / "templates").exists(), "templates/ ya no está")

    import app

    hecho = threading.Event()
    parar = threading.Event()
    hilos = app.arrancar(parar)
    app.encolar("prueba", hecho.set)
    comprueba(hecho.wait(5), "arranca: el trabajador atiende la cola")
    comprueba(bot.SERVICIO["lanzar_corrida"] is app.lanzar_corrida, "   y el bot queda enganchado al servicio")
    parar.set()
    for hilo in hilos:
        hilo.join(10)
    comprueba(not any(h.is_alive() for h in hilos), "para: los dos hilos acaban al pedírselo")

    encoladas = []
    original = app.encolar
    app.encolar = lambda nombre, trabajo: encoladas.append(nombre) or 1
    try:
        perfiles.escribir_claves_de_config({"busqueda_automatica_activa": True, "busqueda_automatica_horas": 1})
        app._estado_scheduler.update(proxima_ejecucion=None, horas_conocidas=None)
        app._tick_scheduler()
        comprueba(encoladas == [] and app._estado_scheduler["proxima_ejecucion"], "el planificador programa la siguiente")
        app._estado_scheduler["proxima_ejecucion"] = time.time() - 1
        app._tick_scheduler()
        comprueba(encoladas == ["corrida"] and app.BUSQUEDA["activa"], "   y al vencer, encola una corrida")
        app._tick_scheduler()
        comprueba(encoladas == ["corrida"], "   sin encolar otra mientras esa no acaba")
    finally:
        app.encolar = original
        app.BUSQUEDA.update(activa=False)


def caso_titular_borra_lo_suyo() -> None:
    print("\n[13] El /borrar del titular: dos confirmaciones, y sigue siendo el administrador")
    antes = perfiles.contar_ofertas(TITULAR)
    comprueba(antes > 0, "   (control: el titular tiene ofertas)")
    espia.limpiar()
    escribe("/borrar")
    pulsa(botones(ultimo_teclado())[0])
    comprueba(perfiles.contar_ofertas(TITULAR) == antes and "Segunda confirmación" in ultimo_texto(),
              "la primera confirmación no borra: pregunta otra vez")
    pulsa([d for d in botones(ultimo_teclado()) if d.startswith("aj1:b2:")][0])
    comprueba(perfiles.contar_ofertas(TITULAR) == 0 and not perfiles.marcas(TITULAR)["favoritas"]
              and not perfiles.avisos(TITULAR), "la segunda borra sus ofertas, marcas y avisos")
    comprueba(perfiles.obtener(TITULAR) is not None and escribe("/estado") == "comando:estado",
              "pero su fila se queda y sigue administrando")
    try:
        perfiles.borrar(TITULAR)
        comprueba(False, "sin esa vía, el titular no se puede borrar")
    except ValueError:
        comprueba(True, "sin esa vía, el titular no se puede borrar")


def main() -> int:
    # Todo lo que pase por el espía, para recorrer después los botones y los textos (caso 11).
    todas: list[dict] = []
    limpiar = espia.limpiar

    def limpiar_y_guardar():
        todas.extend(espia.llamadas)
        limpiar()

    espia.limpiar = limpiar_y_guardar
    espia.todas = todas
    for caso in (caso_invitaciones, caso_plantilla, sembrar, caso_perfiles_y_ficha, caso_top_y_misdatos,
                 caso_borrar, caso_grupos_403_429, caso_botones_antiguos, caso_administracion,
                 caso_comandos_registrados, caso_lector, caso_limites, caso_servicio, caso_titular_borra_lo_suyo):
        if caso is caso_limites:
            todas.extend(espia.llamadas)
        try:
            caso()
        except Exception as e:
            # Un caso que revienta es un fallo, y los demás siguen: si no, una mutación cazada por una
            # comprobación quedaría como «la prueba reventó» (ver mutaciones_mp5.py).
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
