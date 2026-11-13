# -*- coding: utf-8 -*-
"""
Pruebas del reinicio de un perfil (11-11, ver telegram_bot.py: «Reiniciar un perfil»): lo que pide la
persona con /reiniciar y lo que hace el titular desde la ficha de /perfiles.

Ninguna prueba llama a Telegram, a Nominatim ni a ningún portal: requests.post es un espía, el
geocodificador una tabla, y la corrida (la de siempre y la de arranque) un doble que solo llama a
`guardar`. Los chat_id y el token son inventados. Lo que sí es de verdad es el router del bot, la encuesta,
la base (en un directorio temporal) y el generador del plan.

Lo que el reinicio promete, y aquí se comprueba:

- **avisa antes**: dice qué se pierde, que queda una copia y que no se puede deshacer; sin el «Sí» no
  cambia nada, y un «Sí» de hace más de 10 minutos tampoco vale;
- **se va todo lo de esa persona y nada de otra**: respuestas, plan, ofertas, marcas, avisos, cola,
  claves de `meta` y lo aprendido; lo que compartía con otro perfil se queda para el otro;
- **antes, copia** en data/borrados (regla 7), sin el chat_id en el nombre;
- **conserva su sitio**: la fila y la fecha de alta siguen, y no hace falta hueco ni invitación;
- **queda como el primer día**: al repetir el cuestionario sale el mismo perfil que la primera vez, con
  su corrida de arranque, y los botones de los avisos de antes ya no hacen nada;
- **una vez cada 24 h** por persona (cada reinicio son hasta 8 consultas de arranque);
- **el titular** reinicia a otro desde su ficha, con su aviso, sin ver nada suyo, y a esa persona se le
  dice; y nadie más puede reiniciar a otro;
- **el titular se reinicia a sí mismo** (desde el 12-11; antes se negaba): con su propio aviso, copia, sin
  tocar a nadie, sin avisarse a sí mismo, sin dejar de administrar ni de correr para los demás; también
  sin fila (una instalación nueva); y al acabar el cuestionario es un perfil nuevo como cualquiera:
  silencio de noche, tope de 10 y la primera tanda limitada;
- **una corrida en marcha** no le guarda ni le avisa a un perfil reiniciado o borrado de lo que buscó
  para el de antes.

    python pruebas/test_reinicio.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-reinicio-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B = 71000001, 72000002, 73000003
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
CONFIG_EN_DISCO = {"_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "busqueda_automatica_horas": 1,
                   "hours_old": 38, "telegram_bot_token": "", "telegram_chat_id": ""}
(DATOS / "config.json").write_text(json.dumps(CONFIG_EN_DISCO, indent=2), encoding="utf-8")

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import almacen  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402
import telegram_encuesta as enc  # noqa: E402

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
    """Sustituye a requests.post y contesta como la API. `todas` guarda también lo ya limpiado."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.todas: list[dict] = []
        self.mensaje_id = 100

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        cuerpo = json if json is not None else dict(data or {})
        llamada = {"metodo": metodo, "cuerpo": cuerpo}
        self.llamadas.append(llamada)
        self.todas.append(llamada)
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        if metodo == "sendMessage":
            self.mensaje_id += 1
            return Resp({"ok": True, "result": {"message_id": self.mensaje_id}})
        return Resp({"ok": True, "result": True})

    def de(self, metodo, chat=None):
        return [c["cuerpo"] for c in self.llamadas if c["metodo"] == metodo
                and (chat is None or str(c["cuerpo"].get("chat_id")) == str(chat))]

    def limpiar(self):
        self.llamadas.clear()


espia = Espia()
requests.post = espia.post
notificaciones.time.sleep = lambda s: None

CIUDADES = {
    "valencia": {"nombre": "Valencia", "region": "Comunitat Valenciana", "lat": 39.4699123, "lon": -0.3763123},
    "malaga": {"nombre": "Málaga", "region": "Andalucía", "lat": 36.7212, "lon": -4.4214},
}


def geocodificar_de_mentira(texto, coordenadas):
    normal = "".join(c for c in (texto or "").casefold() if c.isalpha()).replace("á", "a")
    return dict(CIUDADES[normal]) if normal in CIUDADES else None


enc.GEOCODIFICAR = geocodificar_de_mentira
lanzados: list[int] = []
enc.SERVICIO["lanzar_arranque"] = lanzados.append

# Los mismos guiones que test_encuesta.py: una administrativa en Valencia y un camarero en Málaga.
GUION_A = [
    ("p", "ob1:0:go"), ("p", "ob1:se:t:admin"), ("p", "ob1:se:n"),
    ("p", "ob1:pu:t:admin.administrativo"), ("p", "ob1:pu:t:admin.auxiliar"), ("p", "ob1:pu:n"),
    ("p", "ob1:ex:s:3_5"), ("p", "ob1:ex:n"),
    ("p", "ob1:fo:s:fp_sup"), ("t", "CCNA, Prevención de riesgos"),
    ("t", "Valencia"), ("p", "ob1:ci:y"), ("p", "ob1:di:s:25"),
    ("p", "ob1:mo:t:presencial"), ("p", "ob1:mo:t:hibrido"), ("p", "ob1:mo:n"),
    ("p", "ob1:id:t:ca"), ("p", "ob1:id:t:en"), ("p", "ob1:id:n"), ("p", "ob1:id:i:intermedio"),
    ("p", "ob1:jo:t:completa"), ("p", "ob1:jo:n"), ("p", "ob1:sa:s:18000"),
    ("t", "contabilidad, a3"), ("t", "comercial, ett"), ("t", "Empresa Mala"),
    ("p", "ob1:av:n"), ("p", "ob1:fi:c"),
]
GUION_B = [
    ("p", "ob1:0:go"), ("p", "ob1:se:t:hosteleria"), ("p", "ob1:se:n"),
    ("p", "ob1:pu:t:hosteleria.camarero"), ("p", "ob1:pu:n"),
    ("p", "ob1:ex:s:ninguna"), ("p", "ob1:ex:n"), ("p", "ob1:ex:p:0"),
    ("p", "ob1:fo:k"), ("p", "ob1:fo:k"),
    ("t", "Málaga"), ("p", "ob1:ci:y"), ("p", "ob1:di:s:60"),
    ("p", "ob1:mo:t:presencial"), ("p", "ob1:mo:n"),
    ("p", "ob1:id:n"), ("p", "ob1:jo:k"), ("p", "ob1:sa:k"),
    ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:cl:k"), ("p", "ob1:av:n"), ("p", "ob1:fi:c"),
]


def config() -> dict:
    return config_mod.cargar_config()


def mensaje(texto, chat, n=[0]):
    n[0] += 1
    return {"update_id": n[0], "message": {"message_id": n[0], "text": texto, "chat": {"id": chat, "type": "private"},
                                           "from": {"id": chat}}}


def escribe(texto, chat=TITULAR):
    return bot.procesar(mensaje(texto, chat), config())


def pulsa(datos, chat=TITULAR, mensaje_id=77, n=[5000]):
    """Pulsa un botón. Los de la encuesta (`ob1:` de un paso) van siempre al mensaje de su conversación."""
    n[0] += 1
    if datos.startswith("ob1:") and datos.split(":")[1] not in ("cal", "E", "H"):
        conv = perfiles.conversacion(chat)
        mensaje_id = conv["mensaje_id"] if conv else mensaje_id
    return bot.procesar({"update_id": n[0], "callback_query": {
        "id": "cb%d" % n[0], "data": datos, "from": {"id": chat},
        "message": {"message_id": mensaje_id, "chat": {"id": chat, "type": "private"}}}}, config())


def contesta(chat, guion) -> None:
    for tipo, valor in guion:
        pulsa(valor, chat) if tipo == "p" else escribe(valor, chat)


def textos(chat) -> list[str]:
    return [c["cuerpo"].get("text", "") for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")
            and str(c["cuerpo"].get("chat_id")) == str(chat)]


def ultimo(chat) -> dict:
    con = [c["cuerpo"] for c in espia.llamadas if c["metodo"] in ("sendMessage", "editMessageText")
           and str(c["cuerpo"].get("chat_id")) == str(chat)]
    return con[-1] if con else {}


def botones(cuerpo: dict) -> list[str]:
    return [b["callback_data"] for fila in (cuerpo.get("reply_markup") or {}).get("inline_keyboard", []) for b in fila]


def entrar(chat, guion) -> None:
    """Como entra cualquiera: invitación del titular, /start con el código y el cuestionario entero."""
    escribe("/invitar")
    pulsa("adm1:inv:encuesta")
    codigo = re.findall(r"https://t\.me/bot_de_prueba\?start=([A-Za-z0-9_-]+)", "\n".join(textos(TITULAR)))[-1]
    escribe("/start " + codigo, chat)
    contesta(chat, guion)


def oferta(n, titulo, empresa, relevancia=6.0) -> dict:
    cuando = datetime.now().isoformat(timespec="seconds")
    return {"job_url": f"https://www.linkedin.com/jobs/view/{n}", "title": titulo, "company": empresa,
            "location": "Valencia, Spain", "site": "linkedin", "is_remote": False,
            "description": f"Descripción de la oferta {n}.", "relevancia": relevancia,
            "motivos_relevancia": [["motivo %d" % n, 2.0]], "visto_por_primera_vez": cuando,
            "visto_por_ultima_vez": cuando}


SOLO_DE_A = oferta(1, "Administrativa Solo De A", "Empresa Secreta De A", 7.0)
SOLO_DE_B = oferta(2, "Camarero Solo De B", "Empresa Secreta De B", 6.5)
COMUN = oferta(3, "Auxiliar Compartido", "Empresa Compartida", 6.0)


def sembrar(chat, propia) -> None:
    """Lo que tiene un perfil con unos días de uso: ofertas, una marca, avisos, cola, marcas de `meta` y lo aprendido."""
    ahora = datetime.now().isoformat(timespec="seconds")
    perfiles.aplicar_cambios(chat, escribir=[(propia, 0, 0), (COMUN, 0, 1)], ultima_busqueda=ahora)
    perfiles.fijar_marca(chat, propia["job_url"], "favorito", {"title": propia["title"], "company": propia["company"]},
                         {"origen": "telegram", "motivo": "motivo privado"})
    tb.registrar_aviso(propia, chat)
    tb.registrar_aviso(COMUN, chat)
    perfiles.anotar_respuesta(chat, tb.clave_de(propia["job_url"]), "favorito")
    perfiles.encolar_pendientes(chat, [(COMUN["job_url"], 6.0)])
    perfiles.meta_set(f"autopausa_60:{chat}", ahora)
    perfiles.meta_set(f"silencio_avisado:{chat}", ahora)

    def con_uso(d):
        d["arranque"] = {"estado": "hecho"}
        d["umbral"] = 4.5
        d["plan"]["umbral"] = 4.5
        d["calibracion"] = {"base": 5.0, "mejores": [propia["job_url"]], "borde": [], "baja": [], "estado": "hecha"}
        d["ajustes_aprendidos"] = {"olvidados": ["empresa:acme"], "desde": "2026-11-01T00:00:00"}
        d["historial"] = [{"fecha": ahora, "seccion": "mo", "antes": {}}]

    perfiles.actualizar_datos(chat, con_uso)


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
    return sorted(restos)


def foto(chat_id: int) -> str:
    """Todo lo de un perfil, para comparar el antes y el después de lo que le pasa a OTRO."""
    conn = perfiles.conexion()
    perfil = perfiles.obtener(chat_id)
    return json.dumps({
        "perfil": {k: perfil[k] for k in ("estado", "datos", "version", "creado")} if perfil else None,
        "ofertas": sorted(o["job_url"] for o in perfiles.ofertas_de(chat_id)),
        "textos": sorted(u for u in perfiles.descripciones_de(chat_id)),
        "marcas": perfiles.marcas(chat_id), "avisos": perfiles.avisos(chat_id),
        "pendientes": [p["url"] for p in perfiles.pendientes(chat_id)],
        "conversacion": conn.execute("SELECT COUNT(*) FROM conversacion WHERE chat_id = ?", (chat_id,)).fetchone()[0],
        "meta": sorted(c for (c,) in conn.execute("SELECT clave FROM meta") if c.rpartition(":")[2] == str(chat_id)),
    }, sort_keys=True, default=str)


def copias() -> list[Path]:
    return sorted((DATOS / "borrados").iterdir()) if (DATOS / "borrados").exists() else []


def hace_un_rato() -> int:
    return bot._minuto() - bot.MINUTOS_DE_UNA_CONFIRMACION - 1


PRIMERA_VEZ: dict = {}  # los datos de cada uno al acabar su primer cuestionario


# ----------------------------------------------------------------------
def caso_aviso_previo() -> None:
    print("\n[1] /reiniciar avisa antes: qué se pierde, la copia, y sin el «Sí» no pasa nada")
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    entrar(A, GUION_A)
    entrar(B, GUION_B)
    for chat in (A, B):
        PRIMERA_VEZ[chat] = perfiles.obtener(chat)["datos"]
    comprueba(perfiles.obtener(A)["estado"] == "activo" and perfiles.obtener(B)["estado"] == "activo" and lanzados == [A, B],
              "   (control: A y B entran con la encuesta, quedan activos y se lanza su arranque)")
    comprueba(not perfiles.hay_hueco(config()), "   (control: con el titular, A y B, el tope de 3 está lleno)")
    sembrar(A, SOLO_DE_A)
    sembrar(B, SOLO_DE_B)
    antes = foto(A)

    espia.limpiar()
    comprueba(escribe("/reiniciar", A) == "comando:reiniciar", "/reiniciar es un comando de cualquiera")
    aviso = ultimo(A)
    texto = aviso.get("text", "")
    comprueba(all(x in texto for x in ("respuestas", "ofertas", "marcado", "avisos", "aprendido")),
              "el aviso dice todo lo que se pierde: respuestas, ofertas, marcas, avisos y lo aprendido")
    comprueba("copia" in texto and "No se puede deshacer" in texto, "   que queda una copia y que no se puede deshacer")
    comprueba("no hace falta otra invitación" in texto and "/ajustes" in texto,
              "   que conserva su sitio, y que para cambiar algo suelto está /ajustes")
    teclado = botones(aviso)
    comprueba(len(teclado) == 2 and teclado[0].startswith("aj1:r1:") and teclado[1] == "aj1:no", "   con «Sí, reiniciar» y «No»")
    comprueba(foto(A) == antes, "pedirlo no cambia nada")
    comprueba(pulsa("aj1:r1:%d" % hace_un_rato(), A) == "boton:aj1:caducado" and foto(A) == antes,
              "un «Sí» de hace más de 10 minutos no reinicia nada")
    comprueba(pulsa("aj1:r1", A) == "boton:aj1:caducado" and foto(A) == antes, "   ni uno sin sello")
    comprueba(pulsa("aj1:no", A) == "boton:aj1:cancelado" and foto(A) == antes, "«No» tampoco")
    comprueba(copias() == [], "   y nada de eso deja una copia")
    espia.limpiar()
    escribe("/ayuda", A)
    comprueba("/reiniciar — " in "\n".join(textos(A)), "/ayuda lo enseña")


def caso_reinicio_propio() -> None:
    print("\n[2] Con el «Sí»: se va todo lo suyo y nada de otro, con copia, y conserva su sitio")
    alta = perfiles.obtener(A)["creado"]
    de_b, del_titular = foto(B), foto(TITULAR)
    restos = restos_de(A)
    comprueba({"perfiles", "ofertas_perfil", "marcas", "avisos", "pendientes", "invitaciones", f"meta:autopausa_60:{A}",
               f"meta:silencio_avisado:{A}"} <= set(restos),
              "   (control: la comprobación ve lo que hay de A: %s)" % ", ".join(r.split(":")[1] if r.startswith("meta:") else r for r in restos))
    comprueba(len(perfiles.etiquetas(A)) == 1, "   (control: A tiene una etiqueta de sus botones)")
    clave_vieja = tb.clave_de(SOLO_DE_A["job_url"])

    espia.limpiar()
    escribe("/reiniciar", A)
    si = botones(ultimo(A))[0]
    espia.limpiar()
    comprueba(pulsa(si, A) == "boton:aj1:reiniciado", "el «Sí» reinicia")
    perfil = perfiles.obtener(A)
    marca = (perfil or {}).get("datos", {}).get("reiniciado")
    comprueba(perfil is not None and perfil["estado"] == "encuesta" and perfil["creado"] == alta,
              "su fila sigue, con su fecha de alta, y vuelve a estar en `encuesta`")
    comprueba(perfil["datos"] == perfiles.datos_a_cero(reiniciado=marca) and bool(marca),
              "sus datos son los de quien acaba de entrar, más la marca del reinicio")
    comprueba(perfil["datos"] == {**perfiles.datos_a_cero(), "reiniciado": marca}
              and not any(k in perfil["datos"] for k in ("ajustes_aprendidos", "calibracion", "umbral", "historial", "avisos")),
              "   ni lo aprendido, ni la calibración, ni el listón, ni el historial, ni sus avisos")
    comprueba(restos_de(A) == ["conversacion", "perfiles"],
              "en la base solo queda su fila y el cuestionario recién abierto (%s)" % ", ".join(restos_de(A)))
    comprueba(perfiles.etiquetas(A) == [] and perfiles.contar_ofertas(A) == 0, "   sin etiquetas y sin ofertas")
    conn = perfiles.conexion()
    comprueba(conn.execute("SELECT COUNT(*) FROM ofertas WHERE url = ?", (SOLO_DE_A["job_url"],)).fetchone()[0] == 0,
              "la oferta que solo tenía A se va, con su texto")
    comprueba(bool(perfiles.descripcion_de(B, COMUN["job_url"])), "la que compartía con B se queda para B, con su texto")
    comprueba(foto(B) == de_b and foto(TITULAR) == del_titular, "lo de B y lo del titular, idéntico")
    comprueba(A not in [p["chat_id"] for p in perfiles.activos()], "mientras no acabe el cuestionario, ni se busca para A ni se le avisa")
    comprueba(len(perfiles.listar()) == 3 and not perfiles.hay_hueco(config()),
              "sigue siendo uno de los 3: conserva su sitio con el tope lleno, sin invitación")

    hechas_ = copias()
    ficha = json.loads((hechas_[0] / "perfil.json").read_text(encoding="utf-8")) if hechas_ else {}
    estado = json.loads((hechas_[0] / "estado.json").read_text(encoding="utf-8")) if hechas_ else {}
    comprueba(len(hechas_) == 1 and ((ficha.get("perfil") or {}).get("datos") or {}).get("sectores") == ["admin"]
              and SOLO_DE_A["job_url"] in (estado.get("favoritas") or {}),
              "antes de reiniciar deja UNA copia, con sus respuestas y sus marcas (regla 7)")
    comprueba(bool(hechas_) and str(A) not in hechas_[0].name, "   en una carpeta que no lleva su chat_id")

    de_a = "\n".join(textos(A))
    comprueba("Perfil reiniciado" in de_a and "ob1:0:go" in botones(ultimo(A)),
              "a A se le dice, y le llega el cuestionario con su «Empezar»")
    al_titular = "\n".join(textos(TITULAR))
    comprueba("se ha reiniciado" in al_titular and (hechas_[0].name if hechas_ else "?") in al_titular,
              "al titular le llega que alguien se ha reiniciado, y dónde está la copia")
    comprueba(str(A) not in al_titular and "Administrativa" not in al_titular, "   sin el número de nadie ni nada de lo suyo")

    espia.limpiar()
    comprueba(pulsa(si, A) == "boton:aj1:no_se_reinicia" and len(copias()) == 1,
              "pulsar el mismo «Sí» otra vez no reinicia dos veces ni deja otra copia")
    espia.limpiar()
    escribe("/reiniciar", A)
    comprueba("ya está a cero" in ultimo(A).get("text", "") and botones(ultimo(A)) == [],
              "/reiniciar en mitad del cuestionario: «ya está a cero», sin nada que confirmar")
    comprueba(pulsa("f:" + clave_vieja, A) != "favorito" and not perfiles.marcas(A)["favoritas"],
              "el botón ⭐ de un aviso de antes ya no marca nada")


def caso_como_la_primera_vez() -> None:
    print("\n[3] Al repetir el cuestionario queda como la primera vez")
    lanzados.clear()
    espia.limpiar()
    contesta(A, GUION_A)
    perfil = perfiles.obtener(A)
    comprueba(perfil["estado"] == "activo" and lanzados == [A], "contesta lo mismo: activo, y se lanza SU corrida de arranque")
    sin_marca = {k: v for k, v in perfil["datos"].items() if k != "reiniciado"}
    comprueba(sin_marca == PRIMERA_VEZ[A], "sus datos son exactamente los de su primer cuestionario (más la marca)")
    comprueba(perfil["datos"]["arranque"] == {"estado": "pendiente"} and perfil["datos"]["avisos"].get("silencio") == [22, 8]
              and perfil["datos"]["avisos"].get("max_dia") == 10,
              "   con el arranque pendiente y los avisos de quien entra: silencio de noche y tope de 10")
    comprueba(perfiles.config_efectiva(perfil, config())["umbral"] == 5.0, "   y el listón otra vez en 5,0 (lo había dejado en 4,5)")
    import aprendizaje

    estado = aprendizaje.estado(perfil, config())
    comprueba(estado["etiquetas"] == 0 and not estado["activa"] and aprendizaje.ajustes(perfil)["olvidados"] == [],
              "lo aprendido, a cero: ni etiquetas ni rasgos olvidados")
    comprueba(perfiles.cifras_de_avisos(A)["semana"] == 0, "   y sus cifras de avisos, también")

    # Y con otras respuestas sale otro perfil, sin rastro del de antes.
    perfiles.actualizar_datos(A, lambda d: d.update(reiniciado=(datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")))
    escribe("/reiniciar", A)
    pulsa(botones(ultimo(A))[0], A)
    contesta(A, GUION_B)
    plan = perfiles.obtener(A)["datos"]["plan"]
    comprueba(plan["area"]["nombre"] == "Málaga" and all("dministrativ" not in t.casefold() for t in plan["terminos"])
              and "contabilidad" not in plan.get("claves_mas", []),
              "reiniciar y contestar otra cosa: camarero en Málaga, sin nada de la administrativa de Valencia")
    # Se deja a A como estaba para lo que sigue: administrativa, con uso.
    perfiles.borrar(A, reiniciar=True)
    escribe("/start", A)  # sin pasar por el bot no hay cuestionario abierto: /start lo abre
    contesta(A, GUION_A)
    sembrar(A, SOLO_DE_A)


def caso_una_vez_al_dia() -> None:
    print("\n[4] Una vez cada 24 horas por persona")
    antes = foto(A)
    comprueba(perfiles.obtener(A)["estado"] == "activo", "   (control: A vuelve a estar activa, recién reiniciada)")
    espia.limpiar()
    escribe("/reiniciar", A)
    comprueba("24 horas" in ultimo(A).get("text", "") and "/ajustes" in ultimo(A).get("text", "") and botones(ultimo(A)) == [],
              "/reiniciar otra vez el mismo día: se le dice cuándo podrá, y que /ajustes no borra nada")
    comprueba(pulsa("aj1:r1:%d" % bot._minuto(), A) == "boton:aj1:no_se_reinicia" and foto(A) == antes,
              "   y un «Sí» fabricado tampoco lo salta")
    hace = lambda horas: (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")  # noqa: E731
    perfiles.actualizar_datos(A, lambda d: d.update(reiniciado=hace(23)))
    comprueba(bot._horas_para_reiniciar(perfiles.obtener(A)) == 1, "a las 23 horas le queda 1 hora (hacia arriba)")
    perfiles.actualizar_datos(A, lambda d: d.update(reiniciado=hace(bot.HORAS_ENTRE_REINICIOS + 1)))
    espia.limpiar()
    escribe("/reiniciar", A)
    comprueba(bot._horas_para_reiniciar(perfiles.obtener(A)) == 0 and botones(ultimo(A))[:1] != []
              and botones(ultimo(A))[0].startswith("aj1:r1:"), "pasadas las 24 horas, vuelve a ofrecerlo")
    comprueba(bot._horas_para_reiniciar({"datos": {"reiniciado": "no es una fecha"}}) == 0
              and bot._horas_para_reiniciar({"datos": {}}) == 0, "una marca ilegible, o ninguna, no bloquea a nadie")
    pulsa("aj1:no", A)


def caso_titular() -> None:
    print("\n[5] El titular: reinicia a otro desde su ficha, con aviso; y nadie más puede")
    ref_a, ref_b, ref_t = bot._ref(A), bot._ref(B), bot._ref(TITULAR)
    del_titular = foto(TITULAR)
    # Lo suyo con /reiniciar, en el caso [6b]. Aquí, que borrarle sin más sigue sin poderse (D4).
    try:
        perfiles.borrar(TITULAR)
        comprueba(False, "al titular no se le borra sin conservar su fila")
    except ValueError:
        comprueba(foto(TITULAR) == del_titular, "al titular no se le borra sin conservar su fila")
    try:
        perfiles.borrar(79999999, reiniciar=True)
        comprueba(False, "reiniciar a quien no existe no crea ningún perfil")
    except ValueError:
        comprueba(perfiles.obtener(79999999) is None, "reiniciar a quien no existe no crea ningún perfil")

    pulsa("adm1:fi:" + ref_b)
    comprueba("adm1:ri:" + ref_b in botones(ultimo(TITULAR)), "la ficha de B tiene «♻️ Reiniciar»")
    pulsa("adm1:fi:" + ref_t)
    comprueba(not any(d.startswith("adm1:ri:") for d in botones(ultimo(TITULAR))), "la del titular, no")
    perfil_b = perfiles.obtener(B)
    comprueba(all(not any(d.startswith("adm1:ri:") for d in botones({"reply_markup": bot._teclado_de_ficha({**perfil_b, "estado": e})}))
                  for e in ("encuesta", "bloqueado"))
              and any(d.startswith("adm1:ri:") for d in botones({"reply_markup": bot._teclado_de_ficha({**perfil_b, "estado": "pausado"})})),
              "ni la de quien está en el cuestionario o ha bloqueado el bot; la de un pausado, sí")

    de_a, de_b = foto(A), foto(B)
    comprueba(pulsa("adm1:ri:" + ref_b, A) == "boton:rechazado" and pulsa("adm1:rs:%s:%d" % (ref_b, bot._minuto()), A) == "boton:rechazado"
              and foto(B) == de_b, "otro perfil no puede reiniciar a nadie: ni pedirlo ni confirmarlo")
    espia.limpiar()
    comprueba(pulsa("adm1:ri:" + ref_b) == "boton:adm1:confirmar_reinicio" and foto(B) == de_b, "el titular lo pide: primero, el aviso")
    aviso = ultimo(TITULAR)
    texto = aviso.get("text", "")
    comprueba(all(x in texto for x in ("respuestas", "ofertas", "marcado", "avisos", "aprendido", "copia"))
              and "Se le avisa" in texto and "No se puede deshacer" in texto,
              "   dice qué se le borra, que hay copia, que a esa persona se le avisa y que no se deshace")
    comprueba(str(B) not in texto and "Camarero" not in texto and "Málaga" not in texto, "   sin su número ni nada de lo suyo")
    teclado = botones(aviso)
    comprueba(len(teclado) == 2 and teclado[0].startswith("adm1:rs:" + ref_b + ":") and teclado[1] == "adm1:fi:" + ref_b,
              "   con «Sí, reiniciar» y un «No» que vuelve a la ficha")
    comprueba(pulsa(teclado[1]) == "boton:adm1:ficha" and foto(B) == de_b, "«No»: la ficha, y B como estaba")
    comprueba(pulsa("adm1:rs:%s:%d" % (ref_b, hace_un_rato())) == "boton:adm1:caducado" and foto(B) == de_b,
              "un «Sí» de hace más de 10 minutos no reinicia a nadie")
    comprueba(pulsa("adm1:rs:" + ref_b) == "boton:adm1:caducado" and foto(B) == de_b, "   ni uno sin sello")
    comprueba(pulsa("adm1:rs:%s:%d" % (ref_t, bot._minuto())) == "boton:adm1:ficha" and foto(TITULAR) == del_titular,
              "el botón apuntando al propio titular solo enseña su ficha")
    n_copias = len(copias())
    alta_b = perfiles.obtener(B)["creado"]
    espia.limpiar()
    comprueba(pulsa(teclado[0]) == "boton:adm1:reiniciado", "con el «Sí», B se reinicia")
    b = perfiles.obtener(B)
    comprueba(b["estado"] == "encuesta" and b["creado"] == alta_b
              and b["datos"] == perfiles.datos_a_cero(reiniciado=b["datos"].get("reiniciado")) and restos_de(B) == ["conversacion", "perfiles"],
              "B queda como A antes: su fila, a cero y con el cuestionario abierto")
    comprueba(len(copias()) == n_copias + 1, "   con su copia")
    de_b_textos = "\n".join(textos(B))
    comprueba("Quien administra el bot ha reiniciado tu perfil" in de_b_textos and "ob1:0:go" in botones(ultimo(B)),
              "a B se le dice quién lo ha hecho y le llega el cuestionario")
    al_titular = "\n".join(textos(TITULAR))
    comprueba("reiniciado" in al_titular and copias()[-1].name in al_titular and str(B) not in al_titular
              and "Camarero" not in al_titular, "al titular, que está hecho y dónde está la copia, sin nada de B")
    comprueba(foto(A) == de_a and foto(TITULAR) == del_titular, "lo de A y lo del titular, idéntico")
    comprueba(bool(perfiles.descripcion_de(A, COMUN["job_url"]))
              and perfiles.conexion().execute("SELECT COUNT(*) FROM ofertas WHERE url = ?", (SOLO_DE_B["job_url"],)).fetchone()[0] == 0,
              "   la oferta compartida sigue para A, y la que solo tenía B se va")
    espia.limpiar()
    comprueba(pulsa(teclado[0]) == "boton:adm1:ficha" and len(copias()) == n_copias + 1,
              "el mismo «Sí» otra vez ya no hace nada: B está en el cuestionario")
    # Reiniciado por el titular, B termina su cuestionario como cualquiera.
    lanzados.clear()
    contesta(B, GUION_B)
    comprueba(perfiles.obtener(B)["estado"] == "activo" and lanzados == [B]
              and {k: v for k, v in perfiles.obtener(B)["datos"].items() if k != "reiniciado"} == PRIMERA_VEZ[B],
              "B contesta y queda como la primera vez, con su arranque")


def caso_recien_reiniciado_no_es_un_perfil_callado() -> None:
    print("\n[5b] El vigilante de «48 h sin avisos» cuenta desde el reinicio, no desde el alta")
    import avisos_perfil

    # A con una semana de alta, 25 ofertas, nada en la cola y ni un aviso: lo que el vigilante busca.
    conn = perfiles.conexion()
    # El vigilante trabaja en UTC, como el contenedor: el «ahora» que se le pasa y las fechas, también.
    mediodia = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)  # horas de trato en Madrid
    hace = lambda dias, horas=0: (mediodia - timedelta(days=dias, hours=horas)).replace(tzinfo=None).isoformat(timespec="seconds")  # noqa: E731
    with perfiles._transaccion() as c:
        c.execute("UPDATE perfiles SET creado = ? WHERE chat_id = ?", (hace(7), A))
        c.execute("DELETE FROM avisos WHERE chat_id = ?", (A,))
        c.execute("DELETE FROM pendientes WHERE chat_id = ?", (A,))
    perfiles.aplicar_cambios(A, escribir=[(oferta(100 + i, "Administrativa %d" % i, "Empresa %d" % i, 4.0), 0, 2 + i) for i in range(25)])
    clave = f"silencio_sin_avisos:{A}"

    def vigila(reiniciado) -> int:
        """Pasa el vigilante con esa marca de reinicio y devuelve cuántos mensajes han salido (a quien sea)."""
        perfiles.actualizar_datos(A, lambda d: d.update(reiniciado=reiniciado) if reiniciado else d.pop("reiniciado", None))
        perfiles.meta_set(clave, None)
        espia.limpiar()
        avisos_perfil._vigilar_silencio(config(), perfiles.obtener(A), mediodia)
        return len(espia.de("sendMessage"))

    comprueba(perfiles.contar_ofertas(A) >= avisos_perfil.OFERTAS_PARA_MIRAR_EL_SILENCIO and conn.execute(
        "SELECT COUNT(*) FROM avisos WHERE chat_id = ?", (A,)).fetchone()[0] == 0, "   (control: A tiene ofertas de sobra y ningún aviso)")
    comprueba(vigila(None) == 1 and perfiles.meta_get(clave) is not None,
              "   (control: sin reinicio, con una semana de alta y nada recibido, el vigilante avisa)")
    comprueba(vigila(hace(0, 1)) == 0 and perfiles.meta_get(clave) is None,
              "recién reiniciada (hace 1 hora), no: ni se le escribe a ella ni al titular")
    comprueba(vigila(hace(avisos_perfil.DIAS_SIN_AVISOS, 1)) == 1, "pasadas las 48 horas desde el reinicio, vuelve a vigilarla")
    perfiles.meta_set(clave, None)
    perfiles.actualizar_datos(A, lambda d: d.update(reiniciado=hace(2)))


def caso_corrida_en_marcha() -> None:
    print("\n[6] Una corrida en marcha no le guarda ni le avisa a un perfil reiniciado o borrado")
    sembrar(B, SOLO_DE_B)
    guardadas: list[int] = []
    avisados: list[int] = []
    originales = (buscador.buscar_para_perfiles, buscador.buscar_arranque, almacen.fusionar_ofertas, app._notificar_si_alto_encaje)

    def fusionar_espia(registros, reemplazar=False, chat_id=None, **k):
        guardadas.append(chat_id)
        return {"ofertas_nuevas": [dict(COMUN)]}

    def avisar_espia(nuevas, perfil, posicion=None, primera=False):
        avisados.append(perfil["chat_id"])

    def corrida_con(en_medio):
        """Una corrida que, con las consultas ya hechas, se encuentra con lo que haga `en_medio`."""
        def doble(activos, config_, guardar, excluidas, **k):
            en_medio()
            for perfil in activos:
                guardar(perfil, pd.DataFrame())

        buscador.buscar_para_perfiles = doble
        guardadas.clear()
        avisados.clear()
        app.BUSQUEDA.update(activa=False, error=None)
        app._ejecutar_busqueda(config(), False, True)
        return app.BUSQUEDA["error"]

    almacen.fusionar_ofertas = fusionar_espia
    app._notificar_si_alto_encaje = avisar_espia
    try:
        error = corrida_con(lambda: None)
        comprueba(error is None and guardadas == [TITULAR, A, B] and avisados == [TITULAR, A, B],
                  "   (control: sin nada en medio, la corrida guarda y avisa a los tres: %s)" % (error or "sin error"))
        error = corrida_con(lambda: perfiles.borrar(A, reiniciar=True))
        comprueba(error is None and guardadas == [TITULAR, B], "A se reinicia a mitad: lo que se buscó para ella no se guarda")
        comprueba(avisados == [TITULAR, B], "   ni se le avisa; al titular y a B, sí")
        # Y aunque acabe el cuestionario antes de que la corrida guarde: sigue sin ser el perfil de antes.
        escribe("/start", A)
        contesta(A, GUION_A)
        comprueba(perfiles.obtener(A)["estado"] == "activo", "   (control: A ya ha acabado su cuestionario y está activa)")
        viejo = {**perfiles.obtener(A), "datos": {k: v for k, v in perfiles.obtener(A)["datos"].items() if k != "reiniciado"}}
        comprueba(app._es_el_de_antes(perfiles.obtener(A)) and not app._es_el_de_antes(viejo),
                  "aunque A acabe el cuestionario a tiempo, el perfil con el que empezó la corrida ya no es el suyo")
        comprueba(app._es_el_de_antes(perfiles.obtener(B)) and app._es_el_de_antes({**perfiles.obtener(B), "estado": "pausado"}),
                  "   uno que no se ha reiniciado sigue siendo el mismo, también si se pausa a mitad")

        # Entre guardar y avisar: los avisos de lo guardado tampoco salen.
        perfil_b = perfiles.obtener(B)
        perfiles.borrar(B, reiniciar=True)
        avisados.clear()
        app._notificar_perfiles([(perfiles.obtener(TITULAR), {"ofertas_nuevas": [dict(COMUN)]}, False),
                                 (perfil_b, {"ofertas_nuevas": [dict(SOLO_DE_B)]}, False)], True)
        comprueba(avisados == [TITULAR], "B se reinicia entre guardar y avisar: no le sale ningún aviso de lo de antes")

        # La corrida de arranque de alguien que se reinicia mientras se hacen sus consultas.
        escribe("/start", B)
        contesta(B, GUION_B)
        comprueba(perfiles.obtener(B)["datos"].get("arranque") == {"estado": "pendiente"},
                  "   (control: B acaba otra vez y tiene el arranque pendiente)")

        def arranque_doble(perfil, config_, guardar, excluidas, **k):
            perfiles.borrar(B, reiniciar=True)
            guardar(perfil, pd.DataFrame())
            return 1

        buscador.buscar_arranque = arranque_doble
        guardadas.clear()
        espia.limpiar()
        app._arranques.clear()
        app._ejecutar_arranque(B)
        comprueba(guardadas == [] and perfiles.contar_ofertas(B) == 0 and textos(B) == [],
                  "B se reinicia durante su corrida de arranque: no se le guarda nada ni se le manda la calibración")

        # /borrar a mitad de una corrida: lo mismo, que es donde se quedaban enlaces de quien ya no está.
        error = corrida_con(lambda: perfiles.borrar(A))
        comprueba(error is None and guardadas == [TITULAR] and avisados == [TITULAR] and restos_de(A) == [],
                  "A se borra a mitad de una corrida: ni se le guarda ni se le avisa, y no queda nada suyo")
        sin_fila = {**perfiles.PERFIL_GLOBAL, "chat_id": TITULAR}
        fila = perfiles.conexion().execute("SELECT estado, datos FROM perfiles WHERE chat_id = ?", (TITULAR,)).fetchone()
        perfiles.conexion().execute("DELETE FROM perfiles WHERE chat_id = ?", (TITULAR,))
        comprueba(app._es_el_de_antes(sin_fila), "el titular sin fila (una base recién migrada) sigue siendo el titular")
        perfiles.guardar(TITULAR, json.loads(fila[1]), fila[0])
    finally:
        (buscador.buscar_para_perfiles, buscador.buscar_arranque, almacen.fusionar_ofertas, app._notificar_si_alto_encaje) = originales
        almacen.cerrar_cache()


def caso_el_titular_se_reinicia() -> None:
    print("\n[6b] El titular reinicia su propio perfil (12-11): como cualquiera, sin tocar a nadie ni dejar de administrar")
    propia = oferta(50, "Analista Solo Del Titular", "Empresa Del Titular", 7.5)
    ahora = datetime.now().isoformat(timespec="seconds")
    perfiles.aplicar_cambios(TITULAR, escribir=[(propia, 0, 0)], ultima_busqueda=ahora)
    perfiles.fijar_marca(TITULAR, propia["job_url"], "favorito", {"title": propia["title"]}, {"origen": "telegram"})
    tb.registrar_aviso(propia, TITULAR)
    perfiles.meta_set(f"silencio_avisado:{TITULAR}", ahora)
    clave_vieja = tb.clave_de(propia["job_url"])
    antes, de_a, de_b = foto(TITULAR), foto(A), foto(B)
    alta = perfiles.obtener(TITULAR)["creado"]
    comprueba(perfiles.obtener(TITULAR)["datos"].get("config") == "global" and perfiles.contar_ofertas(TITULAR) == 1,
              "   (control: el titular tiene sus reglas a mano, una oferta, una ⭐ y un aviso)")

    espia.limpiar()
    comprueba(escribe("/reiniciar") == "comando:reiniciar", "el /reiniciar del titular ya no se niega")
    aviso = ultimo(TITULAR)
    texto = aviso.get("text", "")
    comprueba(texto == bot.AVISO_DE_REINICIO_DEL_TITULAR, "   con su propio aviso, no el de los demás")
    comprueba(all(x in texto for x in ("ofertas", "marcado", "avisos", "aprendido", "reglas escritas a mano", "los demás",
                                       "copia", "no se puede deshacer")),
              "   que dice lo que pierde, que deja sus reglas a mano, que los demás siguen igual y que hay copia")
    teclado = botones(aviso)
    comprueba(len(teclado) == 2 and teclado[0].startswith("aj1:r1:") and teclado[1] == "aj1:no", "   con «Sí, reiniciar» y «No»")
    comprueba(pulsa("aj1:no") == "boton:aj1:cancelado" and foto(TITULAR) == antes, "«No» no cambia nada")
    comprueba(pulsa("aj1:r1:%d" % hace_un_rato()) == "boton:aj1:caducado" and foto(TITULAR) == antes,
              "   ni un «Sí» de hace más de 10 minutos")

    n_copias = len(copias())
    espia.limpiar()
    comprueba(pulsa(teclado[0]) == "boton:aj1:reiniciado", "con el «Sí», se reinicia")
    perfil = perfiles.obtener(TITULAR)
    marca = perfil["datos"].get("reiniciado")
    comprueba(perfil["estado"] == "encuesta" and perfil["creado"] == alta and bool(marca)
              and perfil["datos"] == perfiles.datos_a_cero(reiniciado=marca),
              "su fila sigue, con su alta, a cero y en `encuesta`: ya no tiene reglas a mano")
    comprueba(restos_de(TITULAR) == ["conversacion", "perfiles"], "   sin nada más suyo en la base (%s)" % ", ".join(restos_de(TITULAR)))
    comprueba(pulsa("f:" + clave_vieja) != "favorito" and not perfiles.marcas(TITULAR)["favoritas"],
              "   y el ⭐ de un aviso de antes ya no marca nada")
    comprueba(len(copias()) == n_copias + 1 and propia["job_url"] in json.loads(
        (copias()[-1] / "estado.json").read_text(encoding="utf-8")).get("favoritas", {}),
        "antes deja su copia, con sus marcas (regla 7)")
    comprueba(perfiles.titular() == TITULAR, "sigue siendo el titular")
    suyo = "\n".join(textos(TITULAR))
    comprueba("Perfil reiniciado" in suyo and copias()[-1].name in suyo and "ob1:0:go" in botones(ultimo(TITULAR)),
              "se le dice dónde está la copia y le llega el cuestionario")
    comprueba("Un perfil se ha reiniciado" not in suyo, "   y no se avisa a sí mismo como si fuera otro")
    comprueba(foto(A) == de_a and foto(B) == de_b, "lo de A y lo de B, idéntico")
    comprueba(TITULAR not in [p["chat_id"] for p in perfiles.activos()], "mientras contesta, no se busca para él")
    espia.limpiar()
    comprueba(escribe("/perfiles") == "comando:perfiles" and "Perfiles" in ultimo(TITULAR).get("text", ""),
              "   pero sigue administrando: /perfiles le contesta")
    escribe("/ayuda")
    comprueba("Administración" in "\n".join(textos(TITULAR)), "   y /ayuda le enseña los comandos de administración")

    lanzados.clear()
    contesta(TITULAR, GUION_B)
    perfil = perfiles.obtener(TITULAR)
    comprueba(perfil["estado"] == "activo" and perfil["datos"]["reglas"] == "catalogo" and lanzados == [TITULAR],
              "al acabar el cuestionario: activo, de catálogo, y con su corrida de arranque")
    comprueba(perfil["datos"]["avisos"].get("silencio") == [22, 8] and perfil["datos"]["avisos"].get("max_dia") == 10,
              "   con lo que el paso 12 le ha dicho: silencio de noche y tope de 10 al día")
    comprueba(perfiles.config_efectiva(perfil, config())["area"]["nombre"] == "Málaga", "   y busca lo suyo: Málaga")

    # La primera tanda, limitada como la de cualquiera: la marca la pone el arranque (tras_arranque).
    vistas: list[tuple[int, bool]] = []
    originales = (buscador.buscar_para_perfiles, almacen.fusionar_ofertas, app._notificar_si_alto_encaje)

    def corrida() -> None:
        vistas.clear()
        app.BUSQUEDA.update(activa=False, error=None)
        app._ejecutar_busqueda(config(), False, True)

    buscador.buscar_para_perfiles = lambda activos, config_, guardar, excluidas, **k: [guardar(p, pd.DataFrame()) for p in activos]
    almacen.fusionar_ofertas = lambda registros, reemplazar=False, chat_id=None, **k: {"ofertas_nuevas": [dict(COMUN)]}
    app._notificar_si_alto_encaje = lambda nuevas, perfil, posicion=None, primera=False: vistas.append((perfil["chat_id"], primera))
    try:
        corrida()
        comprueba((TITULAR, False) in vistas, "   (control: sin la marca, su corrida es la de siempre, sin tope de primera tanda)")
        perfiles.actualizar_datos(TITULAR, lambda d: d.update(primera_pendiente=True))
        corrida()
        comprueba((TITULAR, True) in vistas, "con la marca del arranque, su primera tanda va limitada, como la de cualquiera")
        comprueba("primera_pendiente" not in perfiles.obtener(TITULAR)["datos"], "   y después se quita la marca: no se queda limitado")
    finally:
        buscador.buscar_para_perfiles, almacen.fusionar_ofertas, app._notificar_si_alto_encaje = originales

    espia.limpiar()
    escribe("/reiniciar")
    comprueba("24 horas" in ultimo(TITULAR).get("text", "") and botones(ultimo(TITULAR)) == [],
              "otra vez el mismo día: las 24 horas valen también para él")

    # Una instalación nueva: el titular aún no tiene fila (no ha habido ninguna corrida).
    perfiles.conexion().execute("DELETE FROM perfiles WHERE chat_id = ?", (TITULAR,))
    perfiles.conexion().commit()
    comprueba(perfiles.obtener(TITULAR) is None, "   (control: el titular sin fila)")
    espia.limpiar()
    escribe("/reiniciar")
    teclado = botones(ultimo(TITULAR))
    comprueba(ultimo(TITULAR).get("text", "") == bot.AVISO_DE_REINICIO_DEL_TITULAR and teclado[:1] != []
              and teclado[0].startswith("aj1:r1:"), "sin fila también se le ofrece, con su aviso")
    comprueba(pulsa(teclado[0]) == "boton:aj1:reiniciado" and perfiles.obtener(TITULAR)["estado"] == "encuesta"
              and "ob1:0:go" in botones(ultimo(TITULAR)), "   y con el «Sí» se le crea la fila, a cero, y le llega el cuestionario")
    comprueba(foto(A) == de_a, "   sin tocar a nadie más")
    # Lo deja como estaba para lo que sigue: el titular de siempre.
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    perfiles.borrar_conversacion(TITULAR)


def caso_el_titular_estrena() -> None:
    print("\n[6c] Una instalación nueva: el primer /start del titular abre el cuestionario, no el perfil de fábrica")

    def sin_nada() -> None:
        """El titular como en una instalación recién hecha: ni fila, ni ofertas, ni marcas."""
        perfiles.borrar(TITULAR, conservar_perfil=True)
        perfiles.conexion().execute("DELETE FROM perfiles WHERE chat_id = ?", (TITULAR,))
        perfiles.conexion().commit()

    sin_nada()
    de_a, de_b = foto(A), foto(B)
    comprueba(perfiles.obtener(TITULAR) is None and perfiles.contar_ofertas(TITULAR) == 0
              and bot._por_estrenar(bot.Chat(TITULAR, config())), "   (control: el titular sin fila, sin ofertas y sin marcas)")
    lanzados.clear()
    espia.limpiar()
    comprueba(escribe("/start") == "comando:start", "el primer /start del titular")
    perfil = perfiles.obtener(TITULAR)
    comprueba(perfil is not None and perfil["estado"] == "encuesta" and perfil["datos"] == perfiles.datos_a_cero(),
              "   le crea su fila a cero y en `encuesta`: sin el perfil de fábrica")
    suyo = textos(TITULAR)
    comprueba(bot.BIENVENIDA_DEL_TITULAR in suyo and "ob1:0:go" in botones(ultimo(TITULAR)),
              "   le dice que administra el bot y le manda el cuestionario")
    comprueba("/frecuencia" in bot.BIENVENIDA_DEL_TITULAR, "   y cómo encender la búsqueda cuando acabe")
    comprueba(TITULAR not in [p["chat_id"] for p in perfiles.activos()], "hasta que lo termine, no se busca nada para él")
    espia.limpiar()
    escribe("/start")
    comprueba(perfiles.obtener(TITULAR)["estado"] == "encuesta" and "ob1:0:go" in botones(ultimo(TITULAR))
              and bot.BIENVENIDA_DEL_TITULAR not in textos(TITULAR), "otro /start sigue con el cuestionario, sin repetir la bienvenida")
    contesta(TITULAR, GUION_A)
    perfil = perfiles.obtener(TITULAR)
    comprueba(perfil["estado"] == "activo" and perfil["datos"]["reglas"] == "catalogo" and lanzados == [TITULAR],
              "al acabarlo: activo, su perfil del cuestionario y su corrida de arranque")
    comprueba(perfil["datos"]["avisos"].get("silencio") == [22, 8] and perfil["datos"]["avisos"].get("max_dia") == 10,
              "   con el silencio de noche y el tope de 10 que le promete el paso 12")
    comprueba(perfiles.titular() == TITULAR and foto(A) == de_a and foto(B) == de_b, "sigue siendo el titular, y nadie más cambia")

    # CONTROL: quien ya ha usado el perfil de fábrica (lanzó /corrida antes de escribir nada) no se toca.
    sin_nada()
    perfiles.aplicar_cambios(TITULAR, escribir=[(oferta(60, "Técnico de soporte", "Empresa Cualquiera"), 0, 0)])
    comprueba(perfiles.obtener(TITULAR) is None and perfiles.contar_ofertas(TITULAR) == 1,
              "   (control: el titular sin fila, pero con una oferta del perfil de fábrica)")
    espia.limpiar()
    escribe("/start")
    comprueba(perfiles.obtener(TITULAR) is None and "Eres el titular" in ultimo(TITULAR).get("text", ""),
              "con ofertas, su /start es el de siempre: ni se le crea fila ni se le cambia el perfil (para eso, /reiniciar)")
    # Lo deja como estaba para lo que sigue: el titular de siempre.
    perfiles.borrar(TITULAR, conservar_perfil=True)
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")


def caso_todo_cabe() -> None:
    print("\n[7] Todo cabe en Telegram, y nada lleva un chat_id")
    datos = {d for c in espia.todas for d in botones(c["cuerpo"])}
    datos |= {"adm1:rs:%s:%d" % (bot._ref(B), bot._minuto()), "aj1:r1:%d" % bot._minuto()}
    largos = [d for d in datos if len(d.encode("utf-8")) > 64]
    comprueba(any(d.startswith("adm1:ri:") for d in datos) and not largos, "ningún callback_data pasa de 64 bytes (%d mirados)" % len(datos))
    todos = [c["cuerpo"].get("text", "") for c in espia.todas if c["metodo"] in ("sendMessage", "editMessageText")]
    comprueba(all(len(t) <= notificaciones.MAX_CARACTERES_TELEGRAM for t in todos), "ningún mensaje pasa de 4.096 caracteres")
    comprueba(len(bot.AVISO_DE_REINICIO) < 1000, "   y el aviso se lee de un vistazo (%d caracteres)" % len(bot.AVISO_DE_REINICIO))
    comprueba(len(bot.AVISO_DE_REINICIO_DEL_TITULAR) < 1000,
              "   y el del titular también (%d caracteres)" % len(bot.AVISO_DE_REINICIO_DEL_TITULAR))
    comprueba(not any(str(c) in d for d in datos for c in (TITULAR, A, B))
              and not any(str(c) in t for t in todos for c in (TITULAR, A, B)), "ni un botón ni un texto llevan un chat_id")
    comprueba(all(re.fullmatch(r"[a-z0-9_]{1,32}", c) and 1 <= len(d) <= 256 for c, d in bot.COMANDOS_DE_TODOS)
              and "reiniciar" in dict(bot.COMANDOS_DE_TODOS), "/reiniciar está en el menú de todos, con un nombre que la API admite")


CASOS = [caso_aviso_previo, caso_reinicio_propio, caso_como_la_primera_vez, caso_una_vez_al_dia, caso_titular,
         caso_recien_reiniciado_no_es_un_perfil_callado, caso_corrida_en_marcha, caso_el_titular_se_reinicia,
         caso_el_titular_estrena, caso_todo_cabe]


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
