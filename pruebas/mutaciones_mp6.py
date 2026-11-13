# -*- coding: utf-8 -*-
"""
Los controles negativos de MP6: rompe la capa aprendida a propósito y exige que test_aprendizaje.py
falle.

Mismo método que mutaciones_mp5.py, con su copia del árbol: el repositorio no se toca, y una mutación
solo cuenta como cazada si la prueba acaba con «FALLOS», es decir, si la caza una comprobación.

Lo que se rompe es lo que MP6 promete y que, roto, no se notaría en un aviso suelto: el mínimo de
etiquetas y de apariciones, los topes, la protección de las ✅ y ⭐, que un perfil no aprenda de otro
(regla 5), que lo de la web no cuente, el corte del cambio de sector, olvidar y empezar de cero, el
mínimo de muestra de la vigilancia, el tope y la memoria del descubrimiento, y la guarda y el
enfriamiento de las sugerencias.

No está en todas.py porque tarda: es una pasada de la prueba entera por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_mp6.py [--solo <trozo del nombre>]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutaciones_mp5 as base  # noqa: E402

# (nombre, fichero, texto de ahora, texto roto)
MUTACIONES = [
    # --- Cuándo se aprende y cuánto
    ("con 1 etiqueta ya hay capa", "aprendizaje.py", "MIN_ETIQUETAS = 30", "MIN_ETIQUETAS = 1"),
    ("un rasgo visto una vez ya pesa", "aprendizaje.py", "MIN_APARICIONES = 3", "MIN_APARICIONES = 1"),
    ("sin tope por rasgo", "aprendizaje.py", "        valor = max(-TOPE_RASGO, min(TOPE_RASGO, valor))\n", ""),
    ("sin tope por oferta", "aprendizaje.py",
     "    return round(max(-TOPE_OFERTA, min(TOPE_OFERTA, total)), 1)", "    return round(total, 1)"),
    ("la capa no entra en la puntuación", "buscador.py", "    if capa is None:\n        return base\n",
     "    return base\n"),
    # --- La protección
    ("sin protección", "aprendizaje.py",
     "        protegidas = proteger(pesos, _positivas_para_proteger(perfil, config_global, umbral, pesos), umbral)",
     "        protegidas = dict(pesos)"),
    # --- Qué es una etiqueta, y de quién
    ("las etiquetas de todos los perfiles", "perfiles.py",
     '        f" WHERE chat_id = ? AND origen IN {_EN_ORIGENES}", (_exigir(chat_id),))]',
     '        f" WHERE ? IS NOT NULL AND origen IN {_EN_ORIGENES}", (_exigir(chat_id),))]'),
    ("las marcas de la web cuentan", "perfiles.py",
     '        f" WHERE chat_id = ? AND origen IN {_EN_ORIGENES}", (_exigir(chat_id),))]',
     '        f" WHERE chat_id = ? AND (origen IS NULL OR origen IN {_EN_ORIGENES})", (_exigir(chat_id),))]'),
    ("👍 sin origen", "telegram_botones.py", '_con_rasgos(chat_id, url, {"origen": "descubrimiento"})',
     "_con_rasgos(chat_id, url, {})"),
    ("lo de antes del reenfoque cuenta", "aprendizaje.py",
     '    salida["desde"] = salida.get("desde") or DESDE_POR_JUEGO.get(datos.get("reglas"))',
     '    salida["desde"] = salida.get("desde")'),
    ("las de antes del corte cuentan", "aprendizaje.py",
     "        if (desde and fecha <= desde) or (hasta and fecha >= hasta):", "        if hasta and fecha >= hasta:"),
    ("la ficha enseña lo aprendido", "telegram_bot.py",
     '    return f"Etiquetas (botones): {n} · lo aprendido: {capa}"',
     '    return f"Etiquetas (botones): {n} · lo aprendido: {capa} " + " ".join('
     '(e.get("empresa") or "") for e in perfiles.etiquetas(perfil["chat_id"]))'),
    # --- Ver y deshacer
    ("olvidar no olvida", "aprendizaje.py", "        if n < MIN_APARICIONES or rasgo in olvidados:",
     "        if n < MIN_APARICIONES:"),
    ("empezar de cero no corta", "telegram_bot.py",
     "a.update(desde=ahora, olvidados=[], apagada=None, semanas=[])", "a.update(olvidados=[], apagada=None, semanas=[])"),
    ("empezar de cero con una confirmación vieja", "telegram_bot.py",
     "        if not args or not _vigente(args[0]):\n            _editar(token, chat_id, mensaje_id, \"Esa confirmación ha caducado.\",",
     "        if not args:\n            _editar(token, chat_id, mensaje_id, \"Esa confirmación ha caducado.\","),
    # --- Vigilancia
    ("la vigilancia sin mínimo de muestra", "aprendizaje.py",
     "    valida = len(respondidos) >= MIN_RESPONDIDOS_SEMANA and cambiados >= MIN_CAMBIADOS_SEMANA",
     "    valida = True"),
    # --- Descubrimiento
    ("descubrimiento sin tope diario", "aprendizaje.py",
     '    return max(0, DESCUBRIMIENTO_AL_DIA - perfiles.contar_avisos(chat_id, "descubrimiento", hoy))',
     "    return DESCUBRIMIENTO_AL_DIA"),
    ("descubrimiento de lo ya enseñado", "app.py",
     "            if perfiles.aviso(chat_id, notificaciones_clave(url)):\n                continue", "            if False:\n                continue"),
    ("descubrimiento apagado que sigue", "aprendizaje.py",
     '.get("descubrimiento", True) is not False', '.get("descubrimiento", True) or True'),
    ("descubrimiento fuera del borde", "aprendizaje.py",
     "        if not (umbral - 1 <= relevancia < umbral):\n            continue\n", ""),
    # --- Sugerencias
    ("sugerencia sin guarda", "telegram_bot.py", "    if not aprendizaje.segura(perfil, config_global, sugerencia):",
     "    if False:"),
    ("sugerencia sin enfriamiento", "aprendizaje.py",
     "    if ultima and datetime.fromisoformat(ultima) > ahora - timedelta(days=DIAS_ENTRE_SUGERENCIAS):\n        return None\n",
     ""),
    ("cualquier botón aplica la sugerencia", "telegram_bot.py",
     '        if not pendiente or pendiente.get("id") != args[0]:', "        if not pendiente:"),
]


def pasar_prueba(arbol: Path) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    entorno.update(PYTHONUTF8="1")
    proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / "test_aprendizaje.py")],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
    return proceso.returncode, proceso.stdout + proceso.stderr


base.MUTACIONES = MUTACIONES
base.pasar_prueba = pasar_prueba

if __name__ == "__main__":
    raise SystemExit(base.main())
