# -*- coding: utf-8 -*-
"""
Pruebas de la copia pública (12-11): `operacion/publicar.py`, lo que decide qué se puede subir a GitHub.

Lo que tiene que cumplir, y lo que se comprueba:

- **sale lo que está en la lista y nada más**: ni un informe, ni las muestras etiquetadas, ni las líneas
  base, ni `CLAUDE.md`, ni lo de `operacion/` que lleva rutas de un PC. Un fichero NUEVO que nadie ha
  decidido publicar tampoco, que es para lo que la lista es de lo que sí;
- **el control de fugas encuentra** un chat_id, un token y una clave de verdad, un término que identifica
  a alguien, un correo, la ruta de la carpeta de un usuario, una IP de casa, una base de datos y un volcado
  grande, **y no dice el valor** al contarlo (regla 3);
- **no da falsas alarmas** con lo que hay a propósito: los tokens de mentira de `pruebas/`, los correos de
  ejemplo y una copia limpia (son los controles: sin ellos, un control que lo marcara todo pasaría);
- **los secretos se recogen** de las bases, de `config.json`, de `.env` y de `API keys/`, y con `--sin-env`
  esos dos últimos no se abren;
- **al escribir no se borra nada que no sea de la copia**, y no se escribe dentro del repositorio.

No toca los datos de verdad ni sale a la red: todo son carpetas temporales con valores inventados. Los
valores que el control tiene que cazar se montan a trozos para que este fichero, que también se publica,
no los lleve escritos.

    python pruebas/test_publicar.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "operacion"))
sys.path.insert(0, str(AQUI))

import publicar  # noqa: E402
import todas  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


# Inventados, y a trozos (ver la cabecera).
CHAT = "9100" + "0077"
TOKEN = "123456789:" + "ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
CLAVE = "c1c1c1c1-" + "d2d2-e3e3-f4f4-a5a5a5a5a5a5"
CORREO = "seleccion" + "@" + "una-empresa.es"
RUTA = "C:" + "\\Users\\" + "fulana\\proyecto"
IP = "192.168" + ".1.20"
TERMINO = "fulanadetal"


def que_sale() -> None:
    print("\n[1] Sale lo que está en la lista, y nada más")
    privados = ["CLAUDE.md", "README.md", "DESPLIEGUE.md", "AUDIT.md", "CAMBIOS-MP8.md", "REVISION-PILOTO.md",
                "prompts/multiperfil/00-REGLAS-COMUNES.md", "operacion/traer_copias.cmd", ".env", "config.json", "data/perfiles.db", "data/config.json",
                "pruebas/muestras/pagina.html", "pruebas/otra/cosa.py", "revision_etiquetas.csv",
                # Los generadores de las muestras privadas: llevan las etiquetas y el perfil del titular.
                "pruebas/generar_ciber.py", "pruebas/generar_fixtures.py", "pruebas/medir_mp4.py"]
    comprueba(all(publicar.sale(r) is None for r in privados),
              "ni los informes, ni CLAUDE.md, ni los prompts, ni lo de este PC, ni datos")
    comprueba(all(publicar.sale("pruebas/" + n) is None for n in todas.MUESTRAS_PRIVADAS),
              "ninguna de las %d muestras privadas de todas.py" % len(todas.MUESTRAS_PRIVADAS))
    # Para esto la lista es de lo que SÍ: con una de exclusiones, el próximo informe saldría solo.
    comprueba(publicar.sale("REVISION-2099-01-01.md") is None and publicar.sale("pruebas/etiquetadas_2099.json") is None
              and publicar.sale("carpeta_nueva/cosa.py") is None,
              "un fichero nuevo que nadie ha decidido publicar no sale")
    publicos = {"app.py": "app.py", "perfilado/catalogo.json": "perfilado/catalogo.json",
                "reglas/__init__.py": "reglas/__init__.py", "pruebas/test_bot.py": "pruebas/test_bot.py",
                "pruebas/fixtures/perfil_sombra.json": "pruebas/fixtures/perfil_sombra.json",
                "docker-compose.yml": "docker-compose.yml", ".env.example": ".env.example",
                "operacion/traer_copias.py": "operacion/traer_copias.py",
                "publico/README.md": "README.md", "publico/LICENSE": "LICENSE"}
    comprueba(all(publicar.sale(r) == d for r, d in publicos.items()),
              "el código, las pruebas y lo de publico/ (a la raíz) sí")

    copia, fuera = publicar.montar({"README.md": b"el cuaderno", "publico/README.md": b"el de fuera",
                                    "app.py": b"x", "CLAUDE.md": b"y"})
    comprueba(copia == {"README.md": b"el de fuera", "app.py": b"x"} and fuera == ["CLAUDE.md", "README.md"],
              "el README de la copia es el de publico/, no el cuaderno de la raíz")

    # Y con lo que hay de verdad en HEAD, si hay git (un zip descargado de GitHub no lo tiene).
    try:
        copia, fuera = publicar.montar(publicar.ficheros_de_head())
    except (OSError, subprocess.CalledProcessError):
        comprueba(True, "sin git a mano no se puede mirar HEAD (se omite)")
        return
    markdown = sorted(r for r in copia if r.endswith(".md"))
    comprueba(set(markdown) <= {"README.md"}, "en la copia de HEAD el único .md es el README (%s)" % markdown)
    comprueba(not any(Path(r).name in todas.MUESTRAS_PRIVADAS for r in copia),
              "en la copia de HEAD no va ninguna muestra privada")


def el_control() -> None:
    print("\n[2] El control de fugas")
    secretos = {CHAT: "chat_id", TOKEN: "token", CLAVE: "clave de API"}
    limpia = {"app.py": b"print('hola')\n# escribe a alguien@example.com\n",
              "pruebas/test_x.py": ("TOKEN = %r\n" % TOKEN.replace("ABC", "XYZ")).encode()}
    # CONTROLES: lo que tiene que pasar sin alarma. Un control que lo marcara todo no serviría de nada.
    comprueba(publicar.controlar(limpia, secretos, [TERMINO]) == [],
              "control: una copia limpia, con un token de mentira en pruebas/ y un correo de ejemplo, pasa")

    def caza(nombre: str, contenido: bytes, que: str) -> list:
        hallazgos = publicar.controlar({**limpia, nombre: contenido}, secretos, [TERMINO])
        comprueba(any(h[0] == nombre and que in h[2] for h in hallazgos), "caza %s (%s)" % (que, nombre))
        return hallazgos

    todo = []
    todo += caza("pruebas/test_y.py", ("TITULAR = %s\n" % CHAT).encode(), "un secreto de verdad (chat_id)")
    todo += caza("pruebas/test_z.py", ("T = '%s'\n" % TOKEN).encode(), "un secreto de verdad (token)")
    todo += caza("fuentes_extra/jooble.py", ("CLAVE = '%s'\n" % CLAVE).encode(), "un secreto de verdad (clave de API)")
    todo += caza("perfiles.py", ("# lo pidió %s\n" % TERMINO.upper()).encode(), "algo que te identifica")
    todo += caza("buscador.py", ("# escribir a %s\n" % CORREO).encode(), "un correo")
    todo += caza("app.py", ("RAIZ = r'%s'\n" % RUTA).encode(), "ruta de usuario")
    todo += caza("config.py", ("SERVIDOR = '%s'\n" % IP).encode(), "IP de red local")
    todo += caza("notificaciones.py", ("T = '%s'\n" % TOKEN.replace("ABC", "QQQ")).encode(), "forma de token")
    todo += caza("data/perfiles.db", b"SQLite format 3\0", "no es código")
    todo += caza("pruebas/volcado.json", b"x" * (publicar._TAMANO_MAXIMO + 1), "pesa")
    # Regla 3: el hallazgo dice dónde y qué clase de cosa es, nunca el valor.
    escrito = repr(todo)
    comprueba(not any(v in escrito for v in (CHAT, TOKEN, CLAVE, CORREO, TERMINO)),
              "ningún hallazgo lleva escrito el valor que ha encontrado")


def los_secretos() -> None:
    print("\n[3] De dónde salen los secretos con los que se compara")
    raiz = Path(tempfile.mkdtemp(prefix="test-publicar-"))
    (raiz / "data").mkdir()
    conn = sqlite3.connect(raiz / "data" / "perfiles.db")
    conn.execute("CREATE TABLE perfiles (chat_id INTEGER, estado TEXT)")
    conn.execute("CREATE TABLE meta (clave TEXT, valor TEXT)")
    conn.executemany("INSERT INTO perfiles VALUES (?, 'activo')", [(int(CHAT),), (91000078,), (-5,)])
    conn.execute("INSERT INTO meta VALUES ('titular', '91000079')")
    conn.commit()
    conn.close()
    (raiz / "data" / "rota.db").write_bytes(b"esto no es una base")
    (raiz / "config.json").write_text(json.dumps({"telegram_bot_token": TOKEN, "telegram_chat_id": "91000080",
                                                  "hours_old": 168000}), encoding="utf-8")
    (raiz / ".env").write_text("# comentario\nJOBSPY_JOOBLE_KEY=%s\nVACIA=\n" % CLAVE, encoding="utf-8")
    (raiz / ".env.example").write_text("JOBSPY_JOOBLE_KEY=valor-de-la-plantilla\n", encoding="utf-8")
    (raiz / "API keys").mkdir()
    (raiz / "API keys" / "otra.txt").write_text("clave: zzzzYYYYxxxxWWWW1234\n", encoding="utf-8")

    secretos, fuentes = publicar.secretos_locales(raiz)
    comprueba({CHAT, "91000078", "91000079", "91000080"} <= set(secretos),
              "los chat_id de la base (perfiles y meta) y de config.json")
    comprueba("5" not in secretos and "168000" not in secretos,
              "ni el perfil sombra (negativo) ni un número cualquiera de la config")
    comprueba(TOKEN in secretos and TOKEN.split(":")[1] in secretos, "el token, entero y sin el número del bot")
    comprueba(CLAVE in secretos and "zzzzYYYYxxxxWWWW1234" in secretos, "los valores de .env y las claves de «API keys/»")
    comprueba("valor-de-la-plantilla" not in secretos, "lo de .env.example no es un secreto")
    comprueba(fuentes["bases"] == 1, "una base rota no cuenta ni tumba el control")

    sin_env, _ = publicar.secretos_locales(raiz, con_env=False)
    comprueba(CLAVE not in sin_env and "zzzzYYYYxxxxWWWW1234" not in sin_env and CHAT in sin_env,
              "con --sin-env no se abren .env ni «API keys/», y lo demás sí se compara")


def escribir() -> None:
    print("\n[4] Escribir: fuera del repositorio y sin borrar lo que no es de la copia")
    comprueba(publicar.comprobar_destino(publicar.RAIZ / "publico" / "salida") is not None
              and publicar.comprobar_destino(publicar.RAIZ.parent) is not None,
              "ni dentro de este repositorio ni en una carpeta que lo contenga")
    destino = Path(tempfile.mkdtemp(prefix="test-publicar-destino-"))
    comprueba(publicar.comprobar_destino(destino) is None and publicar.comprobar_destino(destino / "nueva") is None,
              "una carpeta vacía o que aún no existe vale")
    (destino / "apuntes.txt").write_text("míos", encoding="utf-8")
    comprueba(publicar.comprobar_destino(destino) is not None,
              "una carpeta con cosas que no es un repositorio no se toca")

    def git(*orden: str) -> bool:
        try:
            return subprocess.run(["git", *orden], cwd=destino, capture_output=True).returncode == 0
        except OSError:
            return False

    (destino / "viejo.py").write_text("ya no existe", encoding="utf-8")
    (destino / "app.py").write_text("el de antes", encoding="utf-8")
    if not (git("init", "-q") and git("add", "viejo.py", "app.py")):
        comprueba(True, "sin git a mano no se puede probar la escritura (se omite)")
        return
    comprueba(publicar.comprobar_destino(destino) is None, "si ya es un repositorio, sí")
    quitados = publicar.escribir({"app.py": b"el nuevo\n", "pruebas/test_a.py": b"a\n"}, destino)
    comprueba(quitados == 1 and not (destino / "viejo.py").exists(),
              "lo que el repositorio de la copia tenía y ya no sale, se quita")
    comprueba((destino / "apuntes.txt").read_text(encoding="utf-8") == "míos" and (destino / ".git").is_dir(),
              "lo que no estaba apuntado en su git, y su historial, se quedan")
    comprueba((destino / "app.py").read_bytes() == b"el nuevo\n" and (destino / "pruebas" / "test_a.py").exists(),
              "y queda la copia, con sus carpetas")


def las_sustituciones() -> None:
    print("\n[5] Los comentarios con datos de la búsqueda del titular: cambiados en la copia, intactos aquí")
    comprueba(publicar.sale(publicar.SUSTITUCIONES) is None, "el fichero de sustituciones no sale: lleva el texto original")
    # Cifras inventadas y a trozos: este fichero también se publica, y el control no puede verlas en él.
    frase = "con 12" + " candidaturas enviadas"
    copia = {"a.py": ("# " + frase + "\nx = 1\n").encode(), "b.py": b"# nada\n"}
    bien = [{"fichero": "a.py", "viejo": frase, "nuevo": "con unas pocas candidaturas"}]
    salida, problemas = publicar.sustituir(copia, bien)
    comprueba(problemas == [] and salida["a.py"] == "# con unas pocas candidaturas\nx = 1\n".encode()
              and salida["b.py"] == copia["b.py"], "cambia el comentario y nada más")
    comprueba(copia["a.py"].startswith(("# " + frase).encode()), "   sin tocar lo que recibe")
    for que, lista, otra in (
            ("si el texto original ya no está", [{"fichero": "a.py", "viejo": "con 13" + " candidaturas", "nuevo": "x"}], None),
            ("si está dos veces", [{"fichero": "a.py", "viejo": "candidaturas", "nuevo": "x"}],
             {"a.py": b"# candidaturas y candidaturas\n"}),
            ("si el fichero no está en la copia", [{"fichero": "c.py", "viejo": "12", "nuevo": "x"}], None),
            ("si le falta el «nuevo»", [{"fichero": "a.py", "viejo": frase}], None),
            ("si el original sigue en otro fichero", bien, {**copia, "b.py": ("# " + frase + "\n").encode()})):
        _, problemas = publicar.sustituir(otra or copia, lista)
        comprueba(bool(problemas), "se para %s" % que)

    rx = dict(publicar._FORMAS)["una cifra de candidaturas, aplicadas o favoritas"]
    comprueba(rx.search("# validado contra " + frase) and rx.search("(40" + " aplicadas / 900 ocultas)")
              and rx.search("3" + " favoritas de 9") and not rx.search('comprueba(estado == {"favoritas"})')
              and not rx.search("las aplicadas/favoritas"), "el control caza una cifra de candidaturas, aplicadas o favoritas")

    # Con lo de verdad: lo que dice el fichero, sobre HEAD. Solo aquí: la copia pública no lleva el fichero.
    camino = publicar.RAIZ / publicar.SUSTITUCIONES
    if not camino.exists():
        comprueba(True, "sin el fichero de sustituciones (la copia pública) no hay nada que mirar (se omite)")
        return
    try:
        copia, _ = publicar.montar(publicar.ficheros_de_head())
    except (OSError, subprocess.CalledProcessError):
        comprueba(True, "sin git a mano no se puede mirar HEAD (se omite)")
        return
    lista = json.loads(camino.read_text(encoding="utf-8"))["sustituciones"]
    sustituida, problemas = publicar.sustituir(copia, lista)
    comprueba(problemas == [], "las %d sustituciones encajan con HEAD%s" % (len(lista), ": " + "; ".join(problemas) if problemas else ""))
    cifras = [r for r, c in sustituida.items() if rx.search(c.decode("utf-8", "replace"))]
    comprueba(cifras == [], "tras ellas, ninguna cifra de candidaturas en la copia (%s)" % (", ".join(cifras) or "ninguna"))
    # CONTROL NEGATIVO: sin las sustituciones, las cifras están, y el control las ve.
    sin = [r for r, c in copia.items() if rx.search(c.decode("utf-8", "replace"))]
    comprueba(len(sin) >= 3, "control: sin las sustituciones el control las encuentra (%d ficheros)" % len(sin))
    comprueba(all(s["viejo"] in (publicar.RAIZ / s["fichero"]).read_text(encoding="utf-8") for s in lista),
              "   y aquí, en el cuaderno, siguen los comentarios originales")


def main() -> int:
    print("Pruebas de la copia pública")
    que_sale()
    el_control()
    los_secretos()
    escribir()
    las_sustituciones()
    if fallos:
        print("\nFALLAN %d de %d:" % (len(fallos), hechas))
        for f in fallos:
            print("  - " + f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
