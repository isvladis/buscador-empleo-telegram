# -*- coding: utf-8 -*-
"""
Los controles negativos de MP5: rompe el bot a propósito y exige que test_bot.py falle.

Mismo método que mutaciones_mp3.py, con su copia del árbol: el repositorio no se toca, y una
mutación solo cuenta como cazada si la prueba acaba con «FALLOS», es decir, si la caza una
comprobación y no una excepción cualquiera. Antes de nada se pasa la prueba sin mutar.

Lo que se rompe es lo que MP5 promete y que, roto, no se notaría usando el bot: que nadie sin
invitación reciba nada (D4), que ninguna respuesta lleve lo de otro perfil (regla 5), que /borrar no
se deje nada, y el ritmo, el 429 y el 403 del emisor.

No está en todas.py porque tarda: es una pasada de la prueba entera por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_mp5.py [--solo <trozo del nombre>]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutaciones_mp3 import OBREROS, copiar_arbol  # noqa: E402

# (nombre, fichero, texto de ahora, texto roto)
MUTACIONES = [
    # --- Altas (D4)
    ("un chat sin perfil recibe respuesta", "telegram_bot.py",
     '        logger.info("Mensaje de un chat sin perfil: no se contesta (D4).")\n        return "silencio"',
     '        return _atender_comando(chat, comando or "ayuda", argumento)'),
    ("un código inventado recibe respuesta", "telegram_bot.py",
     '        logger.info("Un /start con un código que no existe: no se contesta (D4).")\n        return "silencio"',
     '        chat.decir("Esta invitación no vale.")\n        return "silencio"'),
    ("una invitación caducada se da por usada", "perfiles.py",
     '        if fila[0] <= ahora:\n            return "caducada"\n', ""),
    ("el tope de perfiles no se mira", "perfiles.py",
     '        if not _hueco(conn, config_global):\n            return "sin_hueco"\n', ""),
    # --- Aislamiento (regla 5)
    ("/perfiles para cualquiera", "telegram_bot.py",
     "if funcion is None or (comando in _SOLO_TITULAR and not chat.es_titular):", "if funcion is None:"),
    ("los botones de administración para cualquiera", "telegram_bot.py",
     '(prefijo == "adm1" and not chat.es_titular) or ', ""),
    ("la ficha lleva una oferta", "telegram_bot.py",
     '    cifras = perfiles.cifras_de_avisos(perfil["chat_id"])',
     '    lineas += [o.get("title") or "" for o in perfiles.ofertas_de(perfil["chat_id"])][:1]\n'
     '    cifras = perfiles.cifras_de_avisos(perfil["chat_id"])'),
    ("/misdatos con las ofertas del titular", "perfiles.py",
     "for i, oferta in enumerate(iterar_ofertas(chat_id, con_texto=False)):",
     "for i, oferta in enumerate(iterar_ofertas(titular(), con_texto=False)):"),
    ("/top con la lista del titular", "telegram_bot.py",
     "urls = perfiles.mejores_vigentes(chat.chat_id, desde, CANDIDATAS_DEL_TOP, sin_marcar=True)",
     "urls = perfiles.mejores_vigentes(perfiles.titular(), desde, CANDIDATAS_DEL_TOP, sin_marcar=True)"),
    ("el botón lleva el chat_id", "telegram_bot.py",
     '    return hashlib.sha1(str(valor).encode("utf-8")).hexdigest()[:8]', "    return str(valor)"),
    # --- /borrar
    ("/borrar se deja las claves de meta", "perfiles.py",
     "        claves = [f[0] for f in conn.execute(\"SELECT clave FROM meta\")\n"
     "                  if f[0].rpartition(\":\")[2] == str(chat_id)]", "        claves = []"),
    ("/borrar se deja la invitación", "perfiles.py",
     '        conn.execute("DELETE FROM invitaciones WHERE usada_por = ?", (chat_id,))\n', ""),
    ("/borrar sin copia", "perfiles.py", "    exportar(chat_id, copia)\n", ""),
    ("una confirmación vieja vale", "telegram_bot.py",
     "        if len(partes) < 2 or not _vigente(partes[1]):", "        if len(partes) < 2:"),
    ("el titular se borra a la primera", "telegram_bot.py", 'if chat.es_titular and accion == "b1":', "if False:"),
    # --- Emisor (§2)
    ("sin ritmo", "notificaciones.py", "            if con_ritmo:\n                _esperar_turno()\n", ""),
    ("el 429 no espera", "notificaciones.py",
     "                time.sleep(espera)\n                continue", "                continue"),
    ("el 403 no bloquea", "notificaciones.py", '                _al_bloquear(cuerpo["chat_id"])', "                pass"),
    ("el titular se bloquea", "perfiles.py",
     'if perfil is None or chat_id == titular() or perfil["estado"] == "bloqueado":',
     'if perfil is None or perfil["estado"] == "bloqueado":'),
    # --- Lo demás
    ("en un grupo se contesta siempre", "telegram_bot.py",
     "    if chat_id in _GRUPOS_AVISADOS or perfiles.meta_get(clave) is not None:", "    if False:"),
    ("los botones de las ofertas no llegan a telegram_botones", "telegram_bot.py",
     'if prefijo not in ("adm1", "aj1", "ob1"):', "if prefijo in ():"),
    ("setMyCommands en cada arranque", "telegram_bot.py",
     'if not token or perfiles.meta_get("huella_comandos") == huella:', "if not token:"),
    ("/frecuencia escribe la config con los valores por defecto", "perfiles.py",
     "    nueva = {**config, **claves}", '    nueva = {**__import__("config").cargar_config(), **claves}'),
]


def pasar_prueba(arbol: Path) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    entorno.update(PYTHONUTF8="1")
    proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / "test_bot.py")],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
    return proceso.returncode, proceso.stdout + proceso.stderr


def una(mutacion: tuple) -> tuple[str, str]:
    nombre, fichero, viejo, nuevo = mutacion
    arbol = Path(tempfile.mkdtemp(prefix="mutacion-")) / "arbol"
    try:
        copiar_arbol(arbol)
        ruta = arbol / fichero
        texto = ruta.read_bytes().decode("utf-8")
        if texto.count(viejo) != 1:
            return nombre, "NO APLICA: el texto aparece %d veces" % texto.count(viejo)
        ruta.write_bytes(texto.replace(viejo, nuevo).encode("utf-8"))
        codigo, salida = pasar_prueba(arbol)
        if codigo == 0:
            return nombre, "VIVA: la prueba pasa con el código roto"
        if "\nFALLOS (" in salida:
            primera = re.search(r"\n  - (.+)", salida[salida.index("\nFALLOS ("):])
            return nombre, "cazada: " + (primera.group(1) if primera else "?")
        return nombre, "ROTA: la prueba reventó sin llegar a una comprobación\n" + salida[-600:]
    finally:
        shutil.rmtree(arbol.parent, ignore_errors=True)


def main() -> int:
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else ""
    base = Path(tempfile.mkdtemp(prefix="mutacion-base-")) / "arbol"
    copiar_arbol(base)
    codigo, salida = pasar_prueba(base)
    shutil.rmtree(base.parent, ignore_errors=True)
    if codigo:
        print("La prueba falla sin mutar nada: no se puede medir.\n" + salida[-1500:])
        return 2
    print("Sin mutar, en verde.\n")
    elegidas = [m for m in MUTACIONES if solo in m[0]]
    with ThreadPoolExecutor(max_workers=OBREROS) as ex:
        resultados = list(ex.map(una, elegidas))
    malas = 0
    for nombre, resultado in resultados:
        cazada = resultado.startswith("cazada")
        malas += not cazada
        print("  %s %s\n         %s" % ("OK   " if cazada else "FALLA", nombre, resultado))
    print("\n%d de %d cazadas." % (len(resultados) - malas, len(resultados)))
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
