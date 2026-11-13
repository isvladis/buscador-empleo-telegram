# -*- coding: utf-8 -*-
"""
Pruebas de que los secretos no vuelven a config.json ni salen por el bot.

Lo que se protege aquí es el hallazgo §0.2 de AUDIT.md: el token del bot estaba en
claro en config.json y `GET /api/config` lo servía entero a cualquiera de la red local.
Desde MP5 no hay web, y lo que puede sacar algo es el bot (/estado, /perfiles, /misdatos) o
/frecuencia, que escribe en config.json. Estas pruebas fallan si alguien reintroduce la fuga.

    python pruebas/test_secretos.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Valor de pruebas, inventado: tiene la forma de un token de Telegram (dígitos, dos
# puntos, 35 caracteres) para que las comprobaciones de fuga lo detecten si se escapa.
TOKEN_FALSO = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CHAT_FALSO = "999888777"
# Un segundo destinatario, para que el log tenga que distinguir «1/2» de «2/2».
OTRO_CHAT_FALSO = "888777666"

fallos: list[str] = []
hechas = 0


def comprueba(condicion: bool, que: str) -> None:
    global hechas
    hechas += 1
    if condicion:
        print("  OK   %s" % que)
    else:
        print("  FALLA %s" % que)
        fallos.append(que)


def recargar(dir_datos: Path, entorno: dict[str, str]):
    """Importa config y app de cero con un JOBSPY_DATA_DIR y un entorno dados.

    Hay que recargarlos porque config.py fija CONFIG_PATH y lee el entorno en el
    momento del import, igual que hace el contenedor al arrancar.
    """
    for var in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_ID",
                "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
        os.environ.pop(var, None)
    os.environ.update(entorno)
    os.environ["JOBSPY_DATA_DIR"] = str(dir_datos)
    for modulo in ("config", "app", "buscador", "almacen", "notificaciones"):
        sys.modules.pop(modulo, None)
    import config as config_mod
    import app as app_mod
    return config_mod, app_mod


def caso_sin_entorno_no_rompe_nada():
    """Transición: sin variables de entorno, el token de config.json sigue valiendo."""
    print("\n[1] Sin variables de entorno (estado de partida del servidor)")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "config.json").write_text(json.dumps({
            "telegram_bot_token": TOKEN_FALSO, "telegram_chat_id": CHAT_FALSO,
            "_config_version": 2,
        }), encoding="utf-8")
        config_mod, _ = recargar(d, {})
        cfg = config_mod.cargar_config()
        comprueba(cfg["telegram_bot_token"] == TOKEN_FALSO,
                  "el token de config.json se sigue leyendo (no se pierden los avisos)")
        estado = config_mod.estado_telegram(cfg)
        comprueba(estado["token_configurado"] is True, "estado_telegram dice que está configurado")
        comprueba("config.json" in estado["origen"], "y avisa de que el origen es config.json")
        comprueba(TOKEN_FALSO not in json.dumps(estado), "estado_telegram NO incluye el valor")
        config_mod.guardar_config(cfg)
        en_disco = (d / "config.json").read_text(encoding="utf-8")
        comprueba(TOKEN_FALSO in en_disco,
                  "al guardar se conserva el token mientras no haya entorno que lo sustituya")


def caso_con_entorno_limpia_el_fichero():
    """Con el entorno puesto: el entorno manda y el fichero se limpia al guardar."""
    print("\n[2] Con variables de entorno (estado al que se quiere llegar)")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "config.json").write_text(json.dumps({
            "telegram_bot_token": "token-viejo-del-fichero", "telegram_chat_id": "111",
            "_config_version": 2,
        }), encoding="utf-8")
        config_mod, _ = recargar(d, {
            "JOBSPY_TELEGRAM_TOKEN": TOKEN_FALSO,
            "JOBSPY_TELEGRAM_CHAT_ID": CHAT_FALSO,
            "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA": "777666555, 444333222",
        })
        cfg = config_mod.cargar_config()
        comprueba(cfg["telegram_bot_token"] == TOKEN_FALSO, "el entorno manda sobre el fichero")
        comprueba(cfg["telegram_chat_id"] == CHAT_FALSO, "y también con el chat_id")
        import notificaciones
        comprueba(notificaciones._chat_ids(cfg) == [CHAT_FALSO, "777666555", "444333222"],
                  "los chat_id adicionales del entorno se parsean (coma y espacios)")
        config_mod.guardar_config(cfg)
        en_disco = (d / "config.json").read_text(encoding="utf-8")
        comprueba(TOKEN_FALSO not in en_disco and "token-viejo-del-fichero" not in en_disco,
                  "al guardar, el token desaparece de config.json")
        comprueba(CHAT_FALSO not in en_disco, "y el chat_id también")
        comprueba("_config_version" in en_disco, "el resto de la config se sigue guardando")


def caso_el_bot_no_filtra():
    """Ni las respuestas del bot ni lo que /frecuencia escribe llevan el token o el chat_id."""
    print("\n[3] El bot (lo que era la API web)")
    import gzip
    import io as io_mod

    import requests

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "config.json").write_text(json.dumps({
            "_config_version": 2, "busqueda_automatica_horas": 1, "telegram_bot_token": "",
            "telegram_chat_id": ""}), encoding="utf-8")
        config_mod, _ = recargar(d, {"JOBSPY_TELEGRAM_TOKEN": TOKEN_FALSO, "JOBSPY_TELEGRAM_CHAT_ID": CHAT_FALSO})
        for modulo in ("telegram_bot", "telegram_botones"):
            sys.modules.pop(modulo, None)
        import perfiles
        import telegram_bot

        perfiles.cerrar()
        perfiles.guardar(int(CHAT_FALSO), perfiles.PERFIL_GLOBAL["datos"], "activo")
        enviado: list[dict] = []
        urls: list[str] = []

        class Resp:
            status_code, ok, text = 200, True, "{}"

            def json(self):
                return {"ok": True, "result": {"message_id": 1, "username": "bot"}}

        def espia_post(url, json=None, timeout=None, data=None, files=None):
            urls.append(url)
            cuerpo = dict(json if json is not None else data or {})
            cuerpo.pop("chat_id", None)  # el destino es el chat_id, claro: lo que importa es el contenido
            if files:
                cuerpo["documento"] = gzip.decompress(files["document"][1]).decode("utf-8")
            enviado.append(cuerpo)
            return Resp()

        capturado = io_mod.StringIO()
        manejador = logging.StreamHandler(capturado)
        logging.getLogger().addHandler(manejador)
        logging.getLogger().setLevel(logging.INFO)
        guardado = requests.post
        requests.post = espia_post
        try:
            for n, texto in enumerate(("/estado", "/perfil", "/perfiles", "/misdatos", "/frecuencia", "/ayuda")):
                telegram_bot.procesar({"update_id": n, "message": {"message_id": n, "text": texto, "chat": {
                    "id": int(CHAT_FALSO), "type": "private"}}}, config_mod.cargar_config())
            telegram_bot.procesar({"update_id": 50, "callback_query": {"id": "c", "data": "adm1:fr:2", "message": {
                "message_id": 3, "chat": {"id": int(CHAT_FALSO), "type": "private"}}}}, config_mod.cargar_config())
        finally:
            requests.post = guardado
            logging.getLogger().removeHandler(manejador)
            perfiles.cerrar()
        todo = json.dumps(enviado, ensure_ascii=False)
        comprueba(any(TOKEN_FALSO in u for u in urls), "   (control: el token sí va en la URL de la API)")
        comprueba(len(enviado) >= 6 and "documento" in todo, "   (control: han salido las respuestas y el fichero)")
        comprueba(TOKEN_FALSO not in todo, "ninguna respuesta del bot lleva el token")
        comprueba(CHAT_FALSO not in todo, "ni el chat_id, tampoco dentro de /misdatos")
        en_disco = (d / "config.json").read_text(encoding="utf-8")
        comprueba('"busqueda_automatica_horas": 2' in en_disco, "/frecuencia ha escrito en config.json")
        comprueba(TOKEN_FALSO not in en_disco and CHAT_FALSO not in en_disco,
                  "   sin el token ni el chat_id del entorno")
        comprueba(TOKEN_FALSO not in capturado.getvalue() and CHAT_FALSO not in capturado.getvalue(),
                  "y el log tampoco los lleva")


def caso_sin_nada_configurado():
    print("\n[4] Sin token en ningún sitio")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        config_mod, _ = recargar(d, {})
        cfg = config_mod.cargar_config()
        estado = config_mod.estado_telegram(cfg)
        comprueba(estado["token_configurado"] is False, "estado_telegram dice que no está configurado")
        comprueba(estado["destinatarios"] == 0, "y que no hay destinatarios")
        import notificaciones
        comprueba(notificaciones.notificar_ofertas_alto_encaje(cfg, [{"title": "x"}]) is False,
                  "notificar devuelve False en vez de fallar")


class CapturaDeLog(logging.Handler):
    """Apunta lo que se logueó, ya formateado: es lo que acabaría en los logs del Docker."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lineas: list[str] = []

    def emit(self, registro: logging.LogRecord) -> None:
        self.lineas.append(registro.getMessage())


class RespuestaFalsa:
    """Lo justo de requests.Response que mira notificaciones._enviar_a_uno."""

    def __init__(self, codigo: int, texto: str = "") -> None:
        self.status_code = codigo
        self.ok = codigo == 200
        self.text = texto


def caso_el_log_no_lleva_chat_id():
    """
    Los avisos de Telegram se loguean por POSICIÓN del destinatario, no por chat_id.

    Hasta el 31-10-2026 `notificaciones.py` escribía el chat_id en siete líneas, y la de
    «enviada correctamente» es INFO: salía en cada aviso de cada corrida, y salieron los
    dos al pegar los logs de la primera corrida de MP1. Choca con la regla 3 de CLAUDE.md,
    y con un perfil por chat (MP3) dejaría en el log el número de cada persona. Ver §7,
    punto 6 de CAMBIOS-MP1.md.

    El envío se fuerza a pasar por las SIETE líneas: un reintento que acaba bien, un
    fallo definitivo, un 400 y un destinatario al que le falta un trozo.
    """
    print("\n[5] Los chat_id no salen en el log de Telegram")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        config_mod, _ = recargar(d, {
            "JOBSPY_TELEGRAM_TOKEN": TOKEN_FALSO,
            "JOBSPY_TELEGRAM_CHAT_ID": CHAT_FALSO,
            "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA": OTRO_CHAT_FALSO,
        })
        import notificaciones
        import requests

        cfg = config_mod.cargar_config()
        chats = notificaciones._chat_ids(cfg)
        comprueba(chats == [CHAT_FALSO, OTRO_CHAT_FALSO], "hay dos destinatarios configurados")

        # El error lleva la URL con el token, como los de requests de verdad.
        error = requests.RequestException(
            "Max retries exceeded with url: /bot%s/sendMessage" % TOKEN_FALSO)
        # Un 400 con el token en el cuerpo: así se ve que la respuesta también se tacha.
        respuestas = [error, RespuestaFalsa(200), RespuestaFalsa(400, "Bad Request %s" % TOKEN_FALSO),
                      error, error, RespuestaFalsa(200)]
        enviados: list[dict] = []

        def espia_post(url, json=None, timeout=None):
            enviados.append(json or {})
            siguiente = respuestas.pop(0) if respuestas else RespuestaFalsa(200)
            if isinstance(siguiente, Exception):
                raise siguiente
            return siguiente

        captura = CapturaDeLog()
        logger = logging.getLogger("notificaciones")
        nivel = logger.level
        logger.addHandler(captura)
        logger.setLevel(logging.DEBUG)
        guardados = notificaciones.requests.post, notificaciones.time.sleep
        notificaciones.requests.post = espia_post
        notificaciones.time.sleep = lambda segundos: None
        try:
            # Texto largo a propósito: se parte en dos trozos, y así un destinatario se
            # queda con uno de los dos (la séptima línea).
            notificaciones._enviar(cfg, "Oferta de prueba.\n" * 400, "prueba", chat_ids=chats)
        finally:
            notificaciones.requests.post, notificaciones.time.sleep = guardados
            logger.removeHandler(captura)
            logger.setLevel(nivel)

        log = "\n".join(captura.lineas)
        comprueba(all(chat not in log for chat in chats),
                  "ningún chat_id aparece en el log (%d líneas)" % len(captura.lineas))
        comprueba(TOKEN_FALSO not in log, "ni el token, ni en el error ni en la respuesta")
        comprueba("destinatario=1/2" in log and "destinatario 2/2" in log,
                  "en su lugar va la posición del destinatario (1/2, 2/2)")

        # Que se han ejercitado las siete líneas, y no solo una: si alguna se queda sin
        # pasar, el chat_id podría seguir ahí sin que esto se enterara.
        esperadas = ["POST /bot", "ha fallado", "No se ha podido enviar la notificación",
                     "Telegram respondió", "al notificar al destinatario",
                     "enviada a Telegram correctamente", "le han llegado"]
        faltan = [t for t in esperadas if t not in log]
        comprueba(not faltan, "pasan las siete líneas que llevaban el chat_id%s"
                  % ("" if not faltan else " (faltan: %s)" % faltan))

        # Lo que se ENVÍA no cambia: el chat_id sigue yendo en el payload, claro.
        comprueba({e.get("chat_id") for e in enviados} == set(chats),
                  "el chat_id sigue viajando en el payload (solo cambia el log)")

        # CONTROL NEGATIVO: la comprobación de arriba no es vacía. Con el formato de antes
        # del arreglo, la misma búsqueda encuentra el número.
        antes = "Notificación enviada a Telegram correctamente (chat_id=%s)." % CHAT_FALSO
        comprueba(any(chat in antes for chat in chats),
                  "control: con el formato de antes la comprobación detecta el chat_id")


# ----------------------------------------------------------------------
# La imagen de Docker no lleva el .env ni las copias (MP10)
# ----------------------------------------------------------------------

# Lo que hay de verdad en ~/proyectos/jobspy del Toshiba (listado el 05-11-2026), que es el contexto
# de `docker compose build`: lo de la izquierda no puede entrar en la imagen, lo de la derecha sí.
NO_ENTRA = [".env", ".env.bak-jooble-20261026_012828", "backups/20261105_182309-data/perfiles.db",
            "backups/20261105_182309-data/config.json", "backups/codigo-pre-mp9-20261105_182309.tar.gz",
            "backups_ui/x/estado.json", "backups_toshiba/20261030_013626_mp0/perfiles.db",
            "API keys/jooble.txt", "app.py.bak-20261024", "README.md.bak-20261021", ".claude/settings.json",
            "data/perfiles.db", "data/copias/copia-20261106_031000.tar.gz", "config.json", "perfiles.db-wal"]
ENTRA = ["app.py", "perfiles.py", "operacion/copia_perfiles.py", "perfilado/catalogo.json",
         "reglas/titular_ciber.py", "requirements.txt", "fuentes_extra/jooble.py", "pruebas/comprobar_copia.py"]


def _patron_de_docker(patron: str):
    """
    Un patrón de .dockerignore como lo entiende Docker (moby/patternmatcher): relativo a la raíz
    del contexto, `*` y `?` sin cruzar «/», `**` cualquier número de directorios, y un patrón que
    nombra un directorio lo quita entero. Es justo lo que hacía falta probar: que «config.json» NO
    quita «backups/x/config.json», que fue el agujero.
    """
    import re
    patron = patron.strip().lstrip("/").rstrip("/")
    regex, i = "", 0
    while i < len(patron):
        if patron.startswith("**/", i):
            regex, i = regex + "(?:.*/)?", i + 3
        elif patron.startswith("**", i):
            regex, i = regex + ".*", i + 2
        elif patron[i] == "*":
            regex, i = regex + "[^/]*", i + 1
        elif patron[i] == "?":
            regex, i = regex + "[^/]", i + 1
        else:
            regex, i = regex + re.escape(patron[i]), i + 1
    return re.compile("^" + regex + "(?:/.*)?$")


def excluidos_por(texto: str, rutas: list[str]) -> set[str]:
    reglas = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        negada = linea.startswith("!")
        reglas.append((negada, _patron_de_docker(linea[1:] if negada else linea)))
    fuera = set()
    for ruta in rutas:
        excluida = False
        for negada, regex in reglas:      # la última que casa manda, como en Docker
            if regex.match(ruta):
                excluida = not negada
        if excluida:
            fuera.add(ruta)
    return fuera


def caso_la_imagen_no_lleva_secretos():
    print("\n[6] .dockerignore: la imagen no lleva el .env ni las copias de data/")
    import subprocess
    actual = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
    fuera = excluidos_por(actual, NO_ENTRA + ENTRA)
    cuelan = [r for r in NO_ENTRA if r not in fuera]
    comprueba(not cuelan, "nada de secretos, copias ni datos entra en la imagen"
              + (" (se cuelan: %s)" % ", ".join(cuelan) if cuelan else ""))
    quitados = [r for r in ENTRA if r in fuera]
    comprueba(not quitados, "el código sí entra" + (" (faltarían: %s)" % ", ".join(quitados) if quitados else ""))
    # CONTROL NEGATIVO: el .dockerignore de antes de MP10, sacado de git. Con él la prueba falla.
    viejo = subprocess.run(["git", "show", "5e91156:.dockerignore"], cwd=RAIZ, capture_output=True,
                           text=True, encoding="utf-8")
    if viejo.returncode == 0:
        colados = [r for r in NO_ENTRA if r not in excluidos_por(viejo.stdout, NO_ENTRA)]
        comprueba(".env" in colados and "backups/20261105_182309-data/perfiles.db" in colados
                  and "backups/20261105_182309-data/config.json" in colados,
                  "control: con el .dockerignore de antes se colaban el .env y las copias (%d rutas)" % len(colados))
    else:
        comprueba(True, "control: sin git a mano no se puede leer el .dockerignore de antes (se omite)")


def main() -> int:
    caso_sin_entorno_no_rompe_nada()
    caso_con_entorno_limpia_el_fichero()
    caso_el_bot_no_filtra()
    caso_sin_nada_configurado()
    caso_el_log_no_lleva_chat_id()
    caso_la_imagen_no_lleva_secretos()
    if fallos:
        print("\nFALLOS (%d de %d comprobaciones):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
