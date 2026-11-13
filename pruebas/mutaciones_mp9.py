# -*- coding: utf-8 -*-
"""
Los controles negativos de MP9: rompe los avisos por perfil a propósito y exige que test_avisos_perfil.py
(o test_encuesta.py, para lo que escribe la encuesta) falle.

Mismo método que mutaciones_mp5.py, con su copia del árbol: el repositorio no se toca, y una mutación
solo cuenta como cazada si la prueba acaba con «FALLOS», es decir, si la caza una comprobación.

Lo que se rompe es lo que MP9 promete y que, roto, no se notaría en un aviso suelto: la hora de Madrid
(silencio que cruza la medianoche, día del tope, turnos, y los dos cambios de hora), el tope, que nada se
pierda en la cola, que caduque a los 7 días y no antes, que el resumen cambie solo la fila de la oferta
pulsada, que dos perfiles no se crucen (regla 5), que el descubrimiento respete silencio, modo y tope, los
plazos de la autopausa y que el titular quede fuera. Desde el 06-11, también lo de P1: que el titular pase
por el mismo `try` que los demás y que lo que no llega a Telegram espere en la cola en vez de quedarse
apuntado como avisado (que es lo que lo enterraba 14 días). Y lo del 07-11: que la calibración no
pueda poner el listón por encima de la mejor nota de esa persona, y que un perfil con ofertas y cero
avisos se detecte en vez de quedarse callado para siempre.

No está en todas.py porque tarda: es una pasada de la prueba entera por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_mp9.py [--solo <trozo del nombre>]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutaciones_mp5 as base  # noqa: E402

AV = "avisos_perfil.py"
# (nombre, fichero, texto de ahora, texto roto)
MUTACIONES = [
    # --- La hora de Madrid
    ("el silencio no cruza la medianoche", AV,
     "    return desde <= hora < hasta if desde < hasta else (hora >= desde or hora < hasta)",
     "    return desde <= hora < hasta"),
    ("el silencio con la hora UTC", AV, "    hora = en_madrid(ahora).hour\n    return desde", "    hora = _utc(ahora).hour\n    return desde"),
    ("el día del tope empieza a medianoche UTC", AV,
     "    return datetime.combine(local.date(), hora_del_dia(0), tzinfo=ZONA).astimezone(timezone.utc)",
     "    return _utc(ahora).replace(hour=0, minute=0, second=0, microsecond=0)"),
    ("Madrid siempre a UTC+1", AV, 'ZONA = ZoneInfo("Europe/Madrid")', "ZONA = timezone(timedelta(hours=1))"),
    ("los turnos a las 9:00 y 18:00 UTC", AV, "hora_del_dia(h), tzinfo=ZONA)", "hora_del_dia(h), tzinfo=timezone.utc)"),
    ("el segundo resumen a las 19:00", AV, '"dos_diarios": (9, 18)', '"dos_diarios": (9, 19)'),
    # --- El tope y la cola
    ("sin tope diario", AV, "    return max(0, avisos.max_dia - enviados_hoy(chat_id, ahora))", "    return None"),
    ("lo que sobra se pierde", AV,
     'perfiles.encolar_pendientes(chat_id, [(o["job_url"], o.get("relevancia")) for o in sobran], ahora_naive)',
     'perfiles.encolar_pendientes(chat_id, [(o["job_url"], o.get("relevancia")) for o in []], ahora_naive)'),
    ("sin tope de 10 por corrida", AV, "k = min(len(altas), por_corrida, len(altas) if libre is None else libre) if ahora_ya else 0",
     "k = min(len(altas), len(altas) if libre is None else libre) if ahora_ya else 0"),
    ("la primera corrida sin sus 5", "app.py", "altas[:AVISOS_EN_LA_PRIMERA_CORRIDA] if primera else altas", "altas"),
    ("el legado pasa por la cola", "app.py", "        if not avisos_perfil.config_de(perfil).legado:",
     "        if True:"),
    # --- El listón que no puede pasar del techo, y el perfil callado (07-11, `REVISION-PILOTO.md`)
    ("la calibración vuelve a pasarse del techo", "telegram_encuesta.py",
     "        umbral = min(umbral, float(techo))", "        umbral = umbral"),
    ("al perfil callado no se le baja el listón", "telegram_encuesta.py",
     "    perfiles.actualizar_datos(chat_id, cambiar)\n    return nuevo", "    return nuevo"),
    ("el perfil callado no se mira", "avisos_perfil.py",
     "                    _vigilar_silencio(config, p, ahora)", "                    pass"),
    ("el aviso del perfil callado se repite cada hora", "avisos_perfil.py",
     "    perfiles.meta_set(clave, _iso(ahora))\n    if nuevo is not None:", "    if nuevo is not None:"),
    ("al perfil callado se le escribe de madrugada", "avisos_perfil.py",
     "    if perfiles.meta_get(clave) is not None or not _en_horas_de_trato(ahora):",
     "    if perfiles.meta_get(clave) is not None:"),
    # --- P1 (CAMBIOS-MP10 §4): el titular por el mismo camino, y la cola como red de lo no entregado
    ("el titular vuelve a notificar fuera del try", "app.py",
     "        if es_titular and not automatica:\n            continue\n",
     "        if es_titular:\n            if automatica:\n                _notificar_si_alto_encaje(nuevas, perfil)\n"
     "            continue\n"),
    # La de «un aviso que falla gasta tope» vivía en _enviar_suelta hasta el 06-11; ahora el apunte se
    # deshace en un solo sitio, el emisor, y es esta misma mutación la que lo vigila para los dos caminos.
    ("el apunte de un aviso que no sale se queda", "notificaciones.py",
     "            if nueva:\n                perfiles.quitar_avisos_sin_responder(de_quien, [nueva])",
     "            if False:\n                perfiles.quitar_avisos_sin_responder(de_quien, [nueva])"),
    ("lo que Telegram no acepta se pierde", "app.py",
     "        _encolar_lo_no_entregado(chat_id, fallidas, quien)", "        pass"),
    ("una excepción al entregar pierde los avisos", "app.py",
     "        _encolar_lo_no_entregado(chat_id, las_de_ahora, quien)", "        pass"),
    ("la cola no caduca", AV,
     'if p["visto_ultima"] is None or str(p["visto_ultima"]) < corte or p["url"] in tratadas:', 'if p["url"] in tratadas:'),
    ("la cola caduca a los 3 días", AV, "DIAS_EN_COLA = 7", "DIAS_EN_COLA = 3"),
    ("la cola enseña lo ya clasificado", AV, 'str(p["visto_ultima"]) < corte or p["url"] in tratadas:', 'str(p["visto_ultima"]) < corte:'),
    ("la cola no junta la misma vacante", AV, "        if vacante in ya or vacante in vistas:", "        if False:"),
    # --- Cuándo sale
    ("sale en silencio", AV, "        if en_silencio(avisos, ahora):\n            return 0\n        turno", "        turno"),
    ("el resumen sale fuera de su turno", AV,
     "            if turno is None or (ultimo is not None and ultimo >= turno):\n                return 0",
     "            if turno is None:\n                return 0"),
    ("el turno sin nada no se da por hecho", AV,
     "        if perfiles.contar_pendientes(chat_id) == 0:\n            if avisos.horas:\n                perfiles.meta_set(marca, _iso(turno))\n            return 0",
     "        if perfiles.contar_pendientes(chat_id) == 0:\n            return 0"),
    ("el turno enviado no se anota", AV, "        if llegadas and avisos.horas:\n            perfiles.meta_set(marca, _iso(turno))",
     "        if False:\n            pass"),
    ("elegir un resumen no espera al siguiente turno", AV,
     '    perfiles.meta_set(f"resumen_ultimo:{chat_id}", _iso(turno) if turno else None)', "    pass"),
    # --- Si falla el envío
    ("un resumen que falla gasta tope", "notificaciones.py", "            perfiles.quitar_avisos_sin_responder(chat_id, nuevas)", "            pass"),
    ("sale de la cola aunque falle", AV, 'perfiles.quitar_pendientes(chat_id, [elegidas[i]["job_url"] for i in llegadas])',
     'perfiles.quitar_pendientes(chat_id, [o["job_url"] for o in elegidas])'),
    # --- El resumen y sus botones
    ("el resumen cambia todo el teclado", "telegram_botones.py",
     '    numero = _numero_en_resumen(mensaje.get("reply_markup"), clave)', "    numero = None"),
    ("el resumen pierde las otras filas", "telegram_botones.py", "        else:\n            salida.append(fila)", "        else:\n            pass"),
    # --- Aislamiento (regla 5)
    ("la cola de todos", "perfiles.py", '" WHERE p.chat_id = ? ORDER BY p.relevancia DESC, p.creada DESC, p.url"',
     '" WHERE ? IS NOT NULL ORDER BY p.relevancia DESC, p.creada DESC, p.url"'),
    ("lo tratado por cualquiera", "perfiles.py", 'f"SELECT url FROM {tabla} WHERE chat_id = ? AND url IN ({ph})"',
     'f"SELECT url FROM {tabla} WHERE ? IS NOT NULL AND url IN ({ph})"'),
    ("el tope cuenta lo de todos", "perfiles.py",
     "\"SELECT COUNT(*) FROM avisos WHERE chat_id = ? AND tipo IN ('aviso', 'descubrimiento')\"",
     "\"SELECT COUNT(*) FROM avisos WHERE ? IS NOT NULL AND tipo IN ('aviso', 'descubrimiento')\""),
    # --- Descubrimiento
    ("descubrimiento en silencio", AV, '    if avisos.modo != "instantaneo" or en_silencio(avisos, ahora):\n        return 0',
     '    if avisos.modo != "instantaneo":\n        return 0'),
    ("descubrimiento en un resumen", AV, '    if avisos.modo != "instantaneo" or en_silencio(avisos, ahora):\n        return 0',
     "    if en_silencio(avisos, ahora):\n        return 0"),
    ("descubrimiento sin tope", AV, "    return 99 if libre is None else max(0, libre - reservadas)", "    return 99"),
    ("descubrimiento le quita el sitio a los avisos", AV, "max(0, libre - reservadas)", "max(0, libre)"),
    # --- /top
    ("/top con las marcadas", "telegram_bot.py", "perfiles.mejores_vigentes(chat.chat_id, desde, CANDIDATAS_DEL_TOP, sin_marcar=True)",
     "perfiles.mejores_vigentes(chat.chat_id, desde, CANDIDATAS_DEL_TOP)"),
    ("/top de un día", "telegram_bot.py", "DIAS_DEL_TOP = 7", "DIAS_DEL_TOP = 1"),
    ("la cola enseña lo que /top ya ha enseñado", "perfiles.py", '        for tabla in ("marcas", "avisos"):',
     '        for tabla in ("marcas",):'),
    # --- /ajustes y la encuesta
    ("un tope cualquiera", "telegram_bot.py", "(int(args[0]) == 0 or int(args[0]) in OPCIONES_DE_TOPE)", "True"),
    ("el modo no deja hecho el turno", "telegram_bot.py", "        avisos_perfil.al_cambiar_modo(chat_id, modo)\n", ""),
    ("la encuesta no pone silencio", "telegram_encuesta.py", '            avisos.setdefault("silencio", list(avisos_perfil.SILENCIO_POR_DEFECTO))',
     "            pass"),
    ("rehacer pone silencio y tope", "telegram_encuesta.py", "        if not rehace and chat.chat_id != perfiles.titular():",
     "        if chat.chat_id != perfiles.titular():"),
    # --- Lo que escribe la encuesta en `avisos` sobrevive a los guardados de después (test_encuesta)
    ("la calibración reconstruye avisos", "telegram_encuesta.py",
     '            datos["arranque"] = {"estado": "hecho"}\n            datos["primera_pendiente"] = True',
     '            datos["arranque"] = {"estado": "hecho"}\n            datos["avisos"] = {"modo": "instantaneo"}\n            datos["primera_pendiente"] = True'),
    ("el botón de descubrimiento reconstruye avisos", "telegram_bot.py",
     'lambda d: d.setdefault("avisos", {}).update(descubrimiento=args[0] == "1"))',
     'lambda d: d.update(avisos={"descubrimiento": args[0] == "1"}))'),
    # --- Autopausa
    ("el titular también se pausa", AV, '        if p["chat_id"] == titular or p["chat_id"] < 0:', '        if p["chat_id"] < 0:'),
    ("la pregunta a los 10 días", AV, "DIAS_SIN_PULSAR = 30", "DIAS_SIN_PULSAR = 10"),
    ("pausa al día siguiente", AV, "DIAS_PARA_CONTESTAR = 7", "DIAS_PARA_CONTESTAR = 1"),
    ("contestar no evita la pausa", AV, "        if actividad > preguntada:", "        if False:"),
    ("se pregunta aunque no recibiera avisos", AV, '"aviso", _iso(ahora - timedelta(days=DIAS_SIN_PULSAR))) == 0):',
     '"aviso", _iso(ahora - timedelta(days=DIAS_SIN_PULSAR))) == 0 and False):'),
    ("se pregunta de madrugada", AV, "if (ahora - actividad < timedelta(days=DIAS_SIN_PULSAR) or not _en_horas_de_trato(ahora)",
     "if (ahora - actividad < timedelta(days=DIAS_SIN_PULSAR)"),
    ("el borrado a los 100 días", AV, "DIAS_PAUSADO_BORRADO = 120", "DIAS_PAUSADO_BORRADO = 100"),
    ("borrado sin los 7 días de aviso", AV, "DIAS_DEL_AVISO_PREVIO = 7", "DIAS_DEL_AVISO_PREVIO = 0"),
    ("borrado sin copia", AV, "        copia, _ = perfiles.copia_y_borrado(chat_id)",
     '        copia = type("C", (), {"name": "sin-copia"})()\n        perfiles.borrar(chat_id)'),
    ("el borrado no avisa al titular", AV, "        _decir(config, titular_chat,", "        (lambda *a: None)(config, titular_chat,"),
    ("el aviso de 60 días cada vez", AV, "perfiles.meta_get(k60) != firma and dias < dias_aviso", "True and dias < dias_aviso"),
    ("volver no reinicia los plazos", AV, '    reloj = max(filter(None, (_leer(p.get("actualizado")), _actividad(p))), default=None)',
     '    reloj = _leer(p.get("actualizado"))'),
]


def pasar_prueba(arbol: Path) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    entorno.update(PYTHONUTF8="1")
    codigo, salida = 0, ""
    for prueba in ("test_avisos_perfil.py", "test_encuesta.py"):
        proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / prueba)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
        codigo, salida = proceso.returncode, proceso.stdout + proceso.stderr
        if codigo:
            break
    return codigo, salida


base.MUTACIONES = MUTACIONES
base.pasar_prueba = pasar_prueba

if __name__ == "__main__":
    raise SystemExit(base.main())
