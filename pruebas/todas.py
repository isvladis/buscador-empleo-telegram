# -*- coding: utf-8 -*-
"""
Lanza todas las pruebas y el medidor, y resume en una línea si se puede desplegar.

    python pruebas/todas.py

Cada fichero de pruebas se ejecuta en su PROPIO proceso a propósito: varios de ellos
recargan `config` y `app` con variables de entorno distintas (JOBSPY_DATA_DIR, el token
de Telegram), y compartir intérprete haría que el orden en que se lanzan cambiara el
resultado. Un proceso por fichero es más lento y no se puede equivocar.

Ninguna prueba sale a la red: el envío a Telegram se sustituye por un espía y no se
consulta ningún portal de empleo.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent

PRUEBAS = [
    ("secretos", "ni el token ni los chat_id en config.json, la API o el log"),
    ("modalidad", "remoto/híbrido/presencial y el filtro geográfico"),
    ("pesos", "N2 y administrador de sistemas restan, no descartan"),
    ("bloqueos", "distinguir un 429 de «no hay nada nuevo»"),
    ("telegram", "un mensaje por oferta, escape, troceado, nada perdido"),
    ("republicaciones", "no avisar dos veces de la misma vacante"),
    ("empresas", "Evolve y Tech Nova se descartan; las que se parecen, no"),
    ("puntuacion", "motivos, pesos desde la config y las señales de la fase 3"),
    ("canario", "InfoJobs responde sin ofertas, anuncio único y escape del aviso"),
    ("tecnoempleo", "feed de Barcelona y sus cinco salvaguardas"),
    ("jooble", "API de Jooble: cupo de 500 peticiones y clave fuera del log"),
    ("botones", "botones de Telegram: marcar, deshacer, solo el titular, token fuera del log"),
    ("perfiles", "SQLite igual que el JSON, aislamiento, base corrupta y migración"),
    ("cache", "caché de descripciones: por enlace y sin cargarlo, igual que antes, importado una vez, roto no tumba"),
    ("evaluador", "filtros y puntuación por perfil, área por distancia, sin contaminar"),
    ("multiperfil", "una corrida para todos: carga, aislamiento, avisos y el titular igual que antes"),
    ("ciber", "titular-ciber, la orden de aplicarlo, el adicional igual y las dos líneas base"),
    ("bot", "comandos, invitaciones, /borrar y /misdatos, ritmo, 429 y 403, sin web"),
    ("aprendizaje", "capa aprendida: topes, protección, aislamiento, vigilancia, descubrimiento"),
    ("catalogo", "catálogo de familias: regex, muestras, cadenas de IT y choques entre puestos"),
    ("generador", "plan de perfil: seis personas, filtros, choca_con y consultas del §6"),
    ("encuesta", "encuesta y /ajustes: dos personas a la vez, reinicio, texto libre, calibración, reglas a mano"),
    ("arranque", "corrida de arranque: solo sus consultas, primera tanda limitada, sin tocar a los demás"),
    ("avisos_perfil", "avisos por perfil: modos, silencio, tope, cola, resumen con botones, /top, autopausa y cambios de hora"),
    ("copias", "copia de perfiles.db: el WAL dentro, base viva intacta, sin chat_id, restaurable y rotación"),
    ("reinicio", "reiniciar un perfil: aviso previo, copia, solo lo suyo, como el primer día, 24 h, el titular y la corrida en marcha"),
    ("fallos", "corridas fallidas: dos seguidas avisan al titular una vez, sin token, y se dice cuando vuelve"),
    ("interesan", "/interesan: las ⭐ sin aplicar, por páginas, con sus botones, solo las suyas y sin contar como avisos"),
    ("publicar", "copia pública: sale solo lo de la lista, el control caza secretos sin decirlos y no borra lo ajeno"),
]

# LAS MUESTRAS PRIVADAS (12-11-2026, al preparar la copia pública: `operacion/publicar.py`).
#
# Las muestras etiquetadas y el historial son ofertas reales con lo que el titular hizo con ellas
# (a cuáles aplicó, cuáles guardó, cuáles ocultó), con la descripción entera del anuncio. No salen
# del repositorio privado, y las líneas base que se miden con ellas tampoco: sin la muestra no
# significan nada. Estos bloques las leen, así que en la copia pública no se pueden pasar.
#
# Se omiten SOLO si no hay NINGUNA: eso es la copia pública. Si falta alguna y otras no, es que se
# ha perdido un fichero, y eso tiene que ser un rojo: omitir en silencio dejaría desplegar con las
# pruebas de la puntuación sin pasar (regla 4).
MUESTRAS_PRIVADAS = (
    "etiquetadas.json", "historial.json", "etiquetadas_ciber.json", "historial_ciber.json",
    "linea_base_mp0.json", "linea_base_mp4.json", "linea_base_fabrica.json",
)
NECESITAN_MUESTRAS = ("modalidad", "puntuacion", "ciber", "aprendizaje")
# Y estos tres comparan con el código «de antes», que sacan del historial de git por su commit
# (e732641, b5ecb7a, d9ede5e). La copia pública empieza su historial de cero y no los tiene: pasados
# allí el 12-11 daban los tres un rojo de `git show`/`git archive` (128). Se omiten con la MISMA
# señal que los de arriba y no con «si no existe el commit»: aquí, que falte el historial tiene que
# seguir siendo un rojo.
NECESITAN_HISTORIAL = ("perfiles", "cache", "multiperfil")


def muestras_privadas() -> tuple[bool, list[str]]:
    """(¿están?, las que faltan). Con todas o con ninguna no falta nada: lo raro es a medias."""
    faltan = [n for n in MUESTRAS_PRIVADAS if not (AQUI / n).exists()]
    if len(faltan) == len(MUESTRAS_PRIVADAS):
        return False, []
    return True, faltan


def finales_de_linea() -> list[str]:
    """
    Ningún script que se copie al servidor puede tener finales de línea de Windows.

    Está aquí porque pasó dos veces en la misma sesión: los scripts que escribí con
    Python en Windows salieron con CRLF, y `bash` en el Toshiba petó con
    «line 15: $'\\r': command not found». Los .py aguantan CRLF, los .sh no.
    """
    malos = []
    for patron in ("*.sh", "**/*.sh"):
        for fichero in sorted(AQUI.parent.glob(patron)):
            try:
                if b"\r\n" in fichero.read_bytes():
                    malos.append(str(fichero.relative_to(AQUI.parent)))
            except OSError:
                continue
    return sorted(set(malos))


def plantilla_sin_valores() -> list[str]:
    """
    `.env.example` SÍ está en git: no puede llevar valores reales.

    Está aquí porque pasó: al configurar el servidor, el token nuevo acabó escrito en la
    plantilla del PC en vez de solo en el `.env` del Toshiba. No llegó a comitearse, pero
    nada lo habría impedido. Esto lo impide.
    """
    plantilla = AQUI.parent / ".env.example"
    if not plantilla.exists():
        return []
    con_valor = []
    for linea in plantilla.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        if valor.strip():
            con_valor.append(clave.strip())
    return con_valor


def main() -> int:
    fallos = []
    con_valor = plantilla_sin_valores()
    if con_valor:
        print(".env.example tiene VALORES y está en git. Los valores van en .env, que no lo está:")
        for clave in con_valor:
            print("  FALLA  %s tiene un valor en .env.example" % clave)
        print("  Arréglalo con: git checkout -- .env.example   (y pon el valor solo en el .env del servidor)")
        print()
        fallos.append(("plantilla-con-valores", ", ".join(con_valor), ""))
    crlf = finales_de_linea()
    if crlf:
        print("Scripts de shell con finales de línea de Windows (romperán en el servidor):")
        for f in crlf:
            print("  FALLA  %s   -> arréglalo con: tr -d '\\r' < %s > tmp && mv tmp %s" % (f, f, f))
        print()
        fallos.append(("finales-de-linea", "\n".join(crlf), ""))
    hay_muestras, faltan = muestras_privadas()
    if faltan:
        print("Faltan muestras de pruebas/ (o están todas, o no hay ninguna):")
        for f in faltan:
            print("  FALLA  falta pruebas/%s" % f)
        print()
        fallos.append(("muestras-a-medias", ", ".join(faltan), ""))
    print("Pruebas\n")
    total, sin_contar, omitidos = 0, [], []
    for nombre, para_que in PRUEBAS:
        if not hay_muestras and nombre in NECESITAN_MUESTRAS + NECESITAN_HISTORIAL:
            print("  OMITE  %-18s %-52s %s" % (nombre, para_que, "necesita las muestras privadas" if nombre in NECESITAN_MUESTRAS
                                               else "necesita el historial de git privado"))
            omitidos.append(nombre)
            continue
        fichero = AQUI / f"test_{nombre}.py"
        proceso = subprocess.run(
            [sys.executable, str(fichero)], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        ultima = ""
        for linea in reversed((proceso.stdout or "").splitlines()):
            if linea.strip():
                ultima = linea.strip()
                break
        cuenta = re.fullmatch(r"TODO BIEN: (\d+) comprobaciones", ultima)
        if cuenta:
            total += int(cuenta.group(1))
        elif proceso.returncode == 0:
            # Un bloque que no dice cuántas hizo no se puede sumar, y uno que nadie cuenta es uno que nadie
            # echa de menos si un día desaparece (lo vio la sesión de seguimiento en MP10 con `copias`).
            sin_contar.append(nombre)
        if proceso.returncode == 0:
            print("  OK     %-18s %-52s %s" % (nombre, para_que, ultima))
        else:
            print("  FALLA  %-18s %-52s %s" % (nombre, para_que, ultima))
            fallos.append((nombre, proceso.stdout, proceso.stderr))

    if hay_muestras:
        print("\nMedidor de precisión (línea base para comparar)\n")
        medir = subprocess.run(
            [sys.executable, str(AQUI / "medir.py")], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        for linea in (medir.stdout or "").splitlines():
            if linea.startswith("|"):
                print("  " + linea)
            if linea.startswith("  ") and "descartadas por los filtros duros" in linea:
                print(" " + linea)

    if fallos:
        print("\n%d fichero(s) de pruebas con fallos. NO desplegar.\n" % len(fallos))
        for nombre, salida, error in fallos:
            print("--- test_%s ---" % nombre)
            print(salida[-2000:] if salida else "(sin salida)")
            if error:
                print(error[-800:])
        return 1
    if sin_contar:
        print("\nBloques que no terminan con «TODO BIEN: N comprobaciones»: %s. Arréglalos para que cuenten."
              % ", ".join(sin_contar))
        return 1
    if omitidos:
        # Otra frase a propósito: «Todas las pruebas pasan» no puede decirse de una pasada incompleta.
        print("\nPasan %d bloques, %d comprobaciones. OMITIDOS %d (%s) y el medidor: necesitan las muestras "
              "etiquetadas o el historial de git de la instalación original, que no se publican."
              % (len(PRUEBAS) - len(omitidos), total, len(omitidos), ", ".join(omitidos)))
        return 0
    print("\nTodas las pruebas pasan: %d bloques, %d comprobaciones." % (len(PRUEBAS), total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
