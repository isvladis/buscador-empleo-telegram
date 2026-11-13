# -*- coding: utf-8 -*-
"""
Prepara la COPIA PÚBLICA del proyecto: lo que se puede subir a GitHub, y nada más (12-11-2026).

    python operacion/publicar.py <carpeta>              # ensayo: dice qué saldría y pasa el control
    python operacion/publicar.py <carpeta> --confirmar  # la escribe (solo si el control pasa)

**Por qué una copia y no este repositorio.** Este repositorio es el cuaderno de trabajo de una
instalación real: sus `CAMBIOS-*.md`, `REVISION-*.md`, `DESPLIEGUE.md` y `CLAUDE.md` cuentan el
servidor, las fechas, lo que le pasó a cada perfil y lo que escribieron otras personas en su
encuesta; `pruebas/etiquetadas*.json` e `historial*.json` son ofertas reales con lo que el titular
hizo con cada una (a cuáles aplicó), con la descripción entera del anuncio y, en algunos, el
correo de quien la publicó. Y todo eso está también en los 171 commits del historial. Limpiarlo
aquí sería reescribir el historial y dejar de apuntar las cosas como se apuntan. Así que el
repositorio privado sigue igual, y lo público es una foto filtrada de `HEAD` que empieza su
historial de cero.

**Qué sale: una lista de lo que SÍ (`sale`), no de lo que no.** Un fichero nuevo que nadie haya
decidido publicar se queda fuera. Con una lista de exclusiones sería al revés: el próximo
`REVISION-<fecha>.md` saldría solo. Lo de `publico/` (el README de fuera, la licencia) va a la
raíz de la copia.

**Sale de `HEAD`, no del disco:** lo que no está en un commit no existe para este script, así que
ni un `.env`, ni `data/`, ni una copia de la base pueden colarse por estar en la carpeta.

**Los comentarios con datos de la búsqueda del titular** (cuántas candidaturas, a qué puestos aplicó, qué
idiomas habla) se cambian en la copia por una versión sin ellos, con `operacion/sustituciones_publicas.json`
(que no sale). Aquí se quedan como están: son el dato medido que pide la regla 8. Si una sustitución deja
de encajar, se para.

**El control de fugas** se pasa sobre la copia ya montada, y si encuentra algo no se escribe nada:

1. Los secretos DE VERDAD de esta máquina, buscados uno a uno: los chat_id de toda `perfiles.db`
   que haya por aquí (también en las copias), el token y los chat_id de todo `config.json`, cada
   valor de `.env` y cada clave de `API keys/`. Se leen para compararlos y **nunca se imprimen**:
   solo cuántos hay y en qué fichero de la copia ha aparecido uno (regla 3).
2. Quién eres: tu usuario del sistema, el nombre y el correo de `git config`, el nombre del
   equipo, y lo que escribas en `terminos_privados.txt` (una línea por término; no está en git).
3. Formas que no deberían estar: correos, rutas de la carpeta de un usuario, IP de red local,
   algo con forma de token de Telegram fuera de `pruebas/` (los de dentro son de mentira y el
   punto 1 ya ha comprobado que ninguno es el de verdad), y ficheros que no son código (bases,
   comprimidos, o más de 300 kB).

No hace `git init`, ni commit, ni push: eso lo decide quien publica, después de mirar la copia.
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import platform
import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parent.parent

# ----------------------------------------------------------------------
# QUÉ SALE
# ----------------------------------------------------------------------
# Carpeta de este repositorio cuyo contenido va a la RAÍZ de la copia: lo que solo tiene sentido
# fuera (el README para quien llega de nuevas, la licencia). El README de aquí es otra cosa: 1.600
# líneas de cuaderno, con las cifras del historial del titular.
SUPERPUESTO = "publico/"

DE_LA_RAIZ = {"Dockerfile", "docker-compose.yml", "requirements.txt", ".dockerignore",
              ".gitattributes", ".gitignore", ".env.example"}
CARPETAS_ENTERAS = ("perfilado/", "fuentes_extra/", "reglas/")
# `traer_copias.cmd` se queda: es la tarea programada de este PC, con la ruta de su entorno. El
# `.py` sale porque `pruebas/test_copias.py` lo importa (sin él ese bloque revienta en la copia) y
# porque el servidor y la carpeta son argumentos.
DE_OPERACION = {"operacion/copia_perfiles.py", "operacion/traer_copias.py", "operacion/publicar.py"}
# De pruebas/, el código y lo que se ha escrito a mano para ellas. Las muestras etiquetadas, el
# historial y las líneas base NO (ver pruebas/todas.py, MUESTRAS_PRIVADAS): `todas.py` omite los
# cuatro bloques que las leen cuando no hay ninguna (y los tres que piden el historial de git de
# aquí), y lo dice.
DE_PRUEBAS = {"pruebas/catalogo_muestras.json"}
# Y de los scripts de pruebas/, estos no: son los que hicieron las muestras privadas a partir de los datos del
# titular, y llevan escritas sus etiquetas (las ofertas que marcó como relevantes, con su empresa) y la
# descripción de su perfil. Sin sus datos no hacen nada (12-11). `medir_mp4.py` importa `generar_ciber` y solo
# mide sus datos: sin él se rompería (lo usa `test_ciber`, que en la copia se omite).
NO_SALEN = {"pruebas/generar_ciber.py", "pruebas/generar_fixtures.py", "pruebas/medir_mp4.py"}


def sale(ruta: str) -> str | None:
    """Dónde va en la copia un fichero de HEAD, o None si no sale. `ruta` con «/», como en git."""
    if ruta.startswith(SUPERPUESTO):
        return ruta[len(SUPERPUESTO):] or None
    if ruta in NO_SALEN:
        return None
    if "/" not in ruta:
        return ruta if (ruta in DE_LA_RAIZ or ruta.endswith(".py")) else None
    if ruta.startswith(CARPETAS_ENTERAS) or ruta in DE_OPERACION or ruta in DE_PRUEBAS:
        return ruta
    if ruta.startswith("pruebas/fixtures/"):
        return ruta
    if ruta.startswith("pruebas/") and ruta.count("/") == 1 and ruta.endswith(".py"):
        return ruta
    return None


# Comentarios con datos de la búsqueda de empleo del titular (cuántas candidaturas, a qué puestos aplicó,
# qué idiomas habla), que en la copia pública se cambian por una versión sin ellos (12-11, a petición del
# titular). Aquí se quedan: son el dato medido que pide la regla 8, y quitarlos dejaría el cuaderno sin
# el porqué. El fichero no está en `sale()`, y tiene que seguir así: lleva el texto original.
SUSTITUCIONES = "operacion/sustituciones_publicas.json"


def sustituir(copia: dict[str, bytes], lista: list[dict]) -> tuple[dict[str, bytes], list[str]]:
    """
    (la copia con las sustituciones, los problemas). Cada «viejo» tiene que estar EXACTAMENTE una vez en
    su fichero: si un comentario se reescribe aquí y la sustitución ya no encaja, se dice y no se publica,
    en vez de dejar pasar el texto original. Y después ningún «viejo» puede quedar en ningún fichero.
    """
    salida, problemas = dict(copia), []
    for i, s in enumerate(lista, 1):
        fichero, viejo, nuevo = s.get("fichero"), s.get("viejo") or "", s.get("nuevo")
        if fichero not in salida or not viejo or nuevo is None:
            problemas.append("sustitución %d: no hay fichero %s en la copia, o le falta «viejo» o «nuevo»" % (i, fichero))
            continue
        texto = salida[fichero].decode("utf-8")
        veces = texto.count(viejo)
        if veces != 1:
            problemas.append("sustitución %d (%s): el texto original está %d veces, no 1" % (i, fichero, veces))
            continue
        salida[fichero] = texto.replace(viejo, nuevo).encode("utf-8")
    for i, s in enumerate(lista, 1):
        viejo = (s.get("viejo") or "").encode("utf-8")
        quedan = sorted(r for r, c in salida.items() if viejo and viejo in c)
        if quedan:
            problemas.append("sustitución %d: el texto original sigue en %s" % (i, ", ".join(quedan)))
    return salida, problemas


def ficheros_de_head() -> dict[str, bytes]:
    """{ruta: contenido} de HEAD, tal y como está en git (LF incluidos)."""
    tar = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=RAIZ, capture_output=True, check=True).stdout
    salida = {}
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        for miembro in t.getmembers():
            if miembro.isfile():
                salida[miembro.name] = t.extractfile(miembro).read()
    return salida


def montar(ficheros: dict[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    """(la copia, lo que se queda fuera). Lo de `publico/` gana a lo de la raíz con el mismo nombre."""
    copia, fuera = {}, []
    for ruta in sorted(ficheros, key=lambda r: (r.startswith(SUPERPUESTO), r)):
        destino = sale(ruta)
        if destino is None:
            fuera.append(ruta)
        else:
            copia[destino] = ficheros[ruta]
    return copia, fuera


# ----------------------------------------------------------------------
# EL CONTROL DE FUGAS
# ----------------------------------------------------------------------
_TOKEN = re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{30,}")
# Con el lookbehind solo empieza a buscar al principio de una palabra: sin él, una línea larga sin «@» (un
# volcado de 300 kB) se recorría una vez por cada carácter, y la prueba tardaba 1 min 41 s (medido el 12-11).
_CORREO = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Rutas de la carpeta de alguien e IP de una red de casa. «~/» y «/app» no dicen de quién son.
_FORMAS = (
    ("ruta de usuario", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+\w|/home/\w+/|/Users/\w+/")),
    ("IP de red local", re.compile(r"(?<![\d.])(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?![\d.])")),
    # Una cifra de lo que alguien hizo con sus ofertas (cuántas candidaturas o favoritas): lo que quitan las sustituciones,
    # y la red para el próximo comentario que lo cuente. Tras ellas, ninguna línea de la copia lo tiene.
    ("una cifra de candidaturas, aplicadas o favoritas",
     re.compile(r"\b\d[\d.,]*\s+(?:candidaturas|aplicadas|favoritas|ocultaciones)\b", re.IGNORECASE)),
)
# Correos que no son de nadie: los de ejemplo y el de los commits sin correo de GitHub.
_CORREO_INOCUO = re.compile(r"@(?:example\.(?:com|org|net)|[\w.-]*\.invalid|users\.noreply\.github\.com)$", re.I)
_NO_ES_CODIGO = (".db", ".sqlite", ".gz", ".zip", ".tar", ".env", ".pkl", ".bak")
_TAMANO_MAXIMO = 300_000  # catalogo.json, lo mayor que sale, son 155 kB; las muestras privadas, 390-520


def _apuntar(secretos: dict[str, str], valor, tipo: str) -> None:
    valor = str(valor).strip().lstrip("-")
    if len(valor) >= 6:  # más corto casa con cualquier cosa, y ningún chat_id ni clave lo es
        secretos.setdefault(valor, tipo)


def _de_una_base(camino: Path, secretos: dict[str, str]) -> bool:
    try:
        conn = sqlite3.connect(f"file:{camino.as_posix()}?mode=ro", uri=True)
        try:
            tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "perfiles" in tablas:
                for (chat,) in conn.execute("SELECT chat_id FROM perfiles"):
                    if isinstance(chat, int) and chat > 0:  # los negativos son perfiles sombra, inventados
                        _apuntar(secretos, chat, "chat_id")
            if "meta" in tablas:
                for (valor,) in conn.execute("SELECT valor FROM meta WHERE clave = 'titular'"):
                    _apuntar(secretos, valor, "chat_id")
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False  # una copia rota o a medias: no hay nada que leer de ella


def _de_un_json(camino: Path, secretos: dict[str, str]) -> None:
    try:
        datos = json.loads(camino.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    def bajar(cosa, clave=""):
        if isinstance(cosa, dict):
            for k, v in cosa.items():
                bajar(v, str(k))
        elif isinstance(cosa, list):
            for v in cosa:
                bajar(v, clave)
        elif isinstance(cosa, (str, int)) and not isinstance(cosa, bool):
            texto = str(cosa).strip()
            if _TOKEN.fullmatch(texto):
                _apuntar(secretos, texto, "token")
                _apuntar(secretos, texto.split(":", 1)[1], "token")
            elif "chat_id" in clave:
                for parte in re.split(r"[,\s]+", texto):
                    if re.fullmatch(r"-?\d{6,}", parte):
                        _apuntar(secretos, parte, "chat_id")
            elif re.search(r"key|clave|token|secret", clave, re.I) and len(texto) >= 16:
                _apuntar(secretos, texto, "clave")

    bajar(datos)


def _de_un_env(camino: Path, secretos: dict[str, str]) -> None:
    try:
        lineas = camino.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for linea in lineas:
        linea = linea.strip()
        if linea.startswith("#") or "=" not in linea:
            continue
        valor = linea.partition("=")[2].strip().strip("'\"")
        for parte in re.split(r"[,\s]+", valor):
            _apuntar(secretos, parte, "valor de .env")
            if _TOKEN.fullmatch(parte):
                _apuntar(secretos, parte.split(":", 1)[1], "valor de .env")


def secretos_locales(raiz: Path = RAIZ, con_env: bool = True) -> tuple[dict[str, str], dict[str, int]]:
    """({valor: tipo}, de dónde han salido). Los valores no se imprimen en ninguna parte."""
    secretos: dict[str, str] = {}
    fuentes = {"bases": 0, "config.json": 0, ".env": 0, "API keys": 0}
    for camino in raiz.rglob("*"):
        if ".git" in camino.parts or not camino.is_file():
            continue
        nombre = camino.name
        if nombre.endswith((".db", ".sqlite")) or re.search(r"\.db\.[\w-]+$", nombre):
            fuentes["bases"] += _de_una_base(camino, secretos)
        elif nombre == "config.json":
            _de_un_json(camino, secretos)
            fuentes["config.json"] += 1
        elif con_env and nombre.startswith(".env") and nombre != ".env.example":
            _de_un_env(camino, secretos)
            fuentes[".env"] += 1
        elif con_env and "API keys" in camino.parts:
            try:
                texto = camino.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for clave in re.findall(r"[A-Za-z0-9_-]{16,}", texto):
                _apuntar(secretos, clave, "clave de API")
            fuentes["API keys"] += 1
    return secretos, fuentes


def _de_git(clave: str) -> str:
    try:
        return subprocess.run(["git", "config", clave], cwd=RAIZ, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.strip()
    except OSError:
        return ""


def quien_eres(raiz: Path = RAIZ) -> list[str]:
    """Términos que te identifican, en minúsculas. Los de menos de 4 letras casarían con cualquier cosa."""
    terminos = {getpass.getuser(), Path.home().name, platform.node(), _de_git("user.name")}
    correo = _de_git("user.email")
    terminos.add(correo)
    # El correo sin correo de GitHub es «12345+usuario@users.noreply.github.com»: interesa «usuario».
    terminos.add(correo.partition("@")[0].rpartition("+")[2])
    propios = raiz / "terminos_privados.txt"
    if propios.exists():
        terminos |= {l.strip() for l in propios.read_text(encoding="utf-8").splitlines() if not l.startswith("#")}
    return sorted({t.lower() for t in terminos if t and len(t.strip()) >= 4})


def controlar(copia: dict[str, bytes], secretos: dict[str, str], terminos: list[str]) -> list[tuple[str, int, str]]:
    """Los hallazgos: (fichero, línea, qué). Vacío es que se puede publicar. Nunca lleva el valor."""
    hallazgos = []
    for ruta, contenido in sorted(copia.items()):
        nombre = PurePosixPath(ruta).name
        if nombre.endswith(_NO_ES_CODIGO) or (nombre.startswith(".env") and nombre != ".env.example"):
            hallazgos.append((ruta, 0, "un fichero que no es código"))
            continue
        if len(contenido) > _TAMANO_MAXIMO:
            hallazgos.append((ruta, 0, "pesa %d kB: ¿es una muestra o un volcado?" % (len(contenido) // 1000)))
        texto = contenido.decode("utf-8", errors="replace")
        en_pruebas = ruta.startswith("pruebas/")
        for numero, linea in enumerate(texto.splitlines(), 1):
            for valor, tipo in secretos.items():
                if valor in linea:
                    hallazgos.append((ruta, numero, "un secreto de verdad (%s)" % tipo))
            minusculas = linea.lower()
            for termino in terminos:
                if termino in minusculas:
                    hallazgos.append((ruta, numero, "algo que te identifica (un término de %d letras)" % len(termino)))
            for correo in (_CORREO.findall(linea) if "@" in linea else ()):
                if not _CORREO_INOCUO.search(correo):
                    hallazgos.append((ruta, numero, "un correo"))
            for que, forma in _FORMAS:
                if forma.search(linea):
                    hallazgos.append((ruta, numero, que))
            if not en_pruebas and _TOKEN.search(linea):
                hallazgos.append((ruta, numero, "algo con forma de token de Telegram"))
    return hallazgos


# ----------------------------------------------------------------------
# ESCRIBIR
# ----------------------------------------------------------------------
def _dentro(hijo: Path, padre: Path) -> bool:
    try:
        hijo.resolve().relative_to(padre.resolve())
        return True
    except ValueError:
        return False


def comprobar_destino(destino: Path) -> str | None:
    """Por qué no se puede escribir ahí, o None. Nunca se borra nada que no sea de la copia."""
    if _dentro(destino, RAIZ) or _dentro(RAIZ, destino):
        return "la copia no puede estar dentro de este repositorio (ni contenerlo)"
    if destino.exists() and not destino.is_dir():
        return "no es una carpeta"
    if destino.exists() and any(destino.iterdir()) and not (destino / ".git").exists():
        return "la carpeta tiene cosas y no es un repositorio de git: no sé qué es de la copia y qué no"
    return None


def escribir(copia: dict[str, bytes], destino: Path) -> int:
    """Deja en `destino` exactamente la copia. De lo que hubiera, solo quita lo que su git tiene apuntado."""
    quitados = 0
    if (destino / ".git").exists():
        viejos = subprocess.run(["git", "ls-files", "-z"], cwd=destino, capture_output=True, check=True).stdout
        for ruta in viejos.decode("utf-8").split("\0"):
            if ruta and ruta not in copia and (destino / ruta).is_file():
                (destino / ruta).unlink()
                quitados += 1
    for ruta, contenido in copia.items():
        camino = destino / ruta
        camino.parent.mkdir(parents=True, exist_ok=True)
        camino.write_bytes(contenido)
    return quitados


def main(argumentos: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Prepara la copia pública del proyecto.")
    p.add_argument("destino", type=Path, help="carpeta de la copia (fuera de este repositorio)")
    p.add_argument("--confirmar", action="store_true", help="escribirla; sin esto, solo el ensayo")
    p.add_argument("--sin-env", action="store_true",
                   help="no abrir .env ni «API keys/» para el control (el resto de secretos sí se compara)")
    a = p.parse_args(argumentos)

    pegas = comprobar_destino(a.destino)
    if pegas:
        print("No se puede usar %s: %s." % (a.destino, pegas))
        return 1
    sucio = subprocess.run(["git", "status", "--porcelain"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    if sucio:
        print("AVISO: hay %d cambio(s) sin commit. La copia sale de HEAD: lo que no esté en un commit no sale.\n"
              % len(sucio.splitlines()))

    de_head = ficheros_de_head()
    copia, fuera = montar(de_head)
    if "README.md" not in copia or "LICENSE" not in copia:
        print("Faltan publico/README.md o publico/LICENSE en HEAD: sin ellos no hay copia que publicar.")
        return 1
    if SUSTITUCIONES not in de_head or SUSTITUCIONES in copia:
        print("Falta %s en HEAD, o saldría en la copia: no se publica sin él." % SUSTITUCIONES)
        return 1
    lista = json.loads(de_head[SUSTITUCIONES].decode("utf-8")).get("sustituciones") or []
    copia, problemas = sustituir(copia, lista)
    if problemas:
        print("NO SE PUEDE PUBLICAR: las sustituciones de %s no encajan. No se ha escrito nada." % SUSTITUCIONES)
        for p in problemas:
            print("  " + p)
        return 1
    print("Sustituciones: %d, todas aplicadas." % len(lista))
    secretos, fuentes = secretos_locales(con_env=not a.sin_env)
    terminos = quien_eres()
    print("Copia: %d ficheros (%d kB). Se quedan fuera %d de HEAD." % (
        len(copia), sum(map(len, copia.values())) // 1000, len(fuera)))
    por_tipo: dict[str, int] = {}
    for tipo in secretos.values():
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
    print("Control: %d secretos de verdad que buscar (%s), de %s; %d términos que te identifican." % (
        len(secretos), ", ".join("%d %s" % (n, t) for t, n in sorted(por_tipo.items())) or "ninguno",
        ", ".join("%d %s" % (n, f) for f, n in fuentes.items()), len(terminos)))
    if a.sin_env:
        print("  (sin abrir .env ni «API keys/»: pásalo una vez sin --sin-env antes de publicar)")
    if not secretos:
        print("  OJO: no he encontrado ningún secreto con el que comparar. ¿Es esta la máquina con los datos?")

    hallazgos = controlar(copia, secretos, terminos)
    if hallazgos:
        print("\nNO SE PUEDE PUBLICAR: %d hallazgo(s). No se ha escrito nada." % len(hallazgos))
        for ruta, numero, que in hallazgos[:60]:
            print("  %s%s: %s" % (ruta, ":%d" % numero if numero else "", que))
        if len(hallazgos) > 60:
            print("  … y %d más." % (len(hallazgos) - 60))
        return 1
    print("Control de fugas: nada.")

    if not a.confirmar:
        print("\nEnsayo. Con --confirmar se escribe en %s." % a.destino)
        return 0
    a.destino.mkdir(parents=True, exist_ok=True)
    quitados = escribir(copia, a.destino)
    print("\nEscrita en %s (%d ficheros; %d de antes quitados)." % (a.destino, len(copia), quitados))
    print("Los commits que hagas ahí llevarán el autor «%s» y un correo de @%s." % (
        _de_git("user.name") or "(sin nombre)", (_de_git("user.email").partition("@")[2] or "(sin correo)")))
    print("Ahora, en esa carpeta: git init -b main (la primera vez), git add -A, git status y míralo antes del commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
