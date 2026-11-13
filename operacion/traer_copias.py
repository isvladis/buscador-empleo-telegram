# -*- coding: utf-8 -*-
"""
Trae al PC la copia más nueva de perfiles.db del Toshiba, la comprueba y rota las del PC (MP10).

    PYTHONUTF8=1 D:\\Dev\\Envs\\venvs\\jobspy312\\Scripts\\python.exe operacion\\traer_copias.py
    operacion\\traer_copias.cmd --si-han-pasado 20      # lo que lanza la tarea programada (DESPLIEGUE.md)

**Por qué fuera del Toshiba.** El disco del servidor ya falló una vez (21-10-2026): una copia que
vive en el mismo disco que la base no sobrevive a la avería que más miedo da. El PC es el otro
sitio que ya tiene la historia del proyecto, y `backups_toshiba/` está en `.gitignore`.

**Qué hace, y solo eso:** lista por SSH las copias de `~/proyectos/jobspy/data/copias/` (lectura),
trae la más nueva por `scp` si aún no está, la comprueba con `pruebas/comprobar_copia.py
--con-el-codigo` y, si sirve, rota las del PC con la misma regla que el servidor (7 diarias y 4
semanales). En el servidor no escribe nada. Una copia que no sirve se aparta a `rechazadas/` y no
cuenta para la rotación, para que una mala no empuje fuera a una buena.

**Pensado para un PC que no está siempre encendido** (petición del titular, 06-11). Lo lanza una tarea
programada al iniciar sesión y una vez al día «en cuanto se pueda», así que puede ejecutarse varias
veces seguidas o con el servidor apagado:

- **`--si-han-pasado H`**: si hace menos de H horas que se habló con el servidor (un `ls` que contestó,
  traiga o no algo), no se le vuelve a preguntar. La hora va en `ultimo_contacto`, un fichero de una línea.
  **Primero lo escribí con la edad de la copia más nueva del PC** («con una de menos de 20 h no puede haber
  otra más nueva allí, porque el cron hace una cada 24 h»), y la primera ejecución real lo desmintió el
  06-11: la del PC era una hecha a mano a las 21:30 UTC, tenía 12,4 h, se saltó, y en el servidor ya estaba
  la del cron de las 03:10. Lo mismo pasaría con el cambio de hora (el cron va en hora de Madrid). Con el
  último contacto no hace falta saber nada del cron: lo peor que pasa es tardar H horas en ver una nueva.
  **Se usa 6 h**: dos encendidos en una mañana no repiten nada, y si se mira justo antes de las 04:10 la
  nueva se recoge en el siguiente encendido pasadas 6 h. Lo único que cuesta mirar es un `ssh ls`: la
  comprobación entera solo se hace con una copia que no estaba.
- **Si ya tiene la más nueva**, no la vuelve a comprobar: ya se comprobó al traerla.
- **Servidor apagado o sin red: no es un error.** `ssh` con `ConnectTimeout` y `BatchMode` (nunca se
  queda esperando una contraseña); si no conecta, una línea en el registro y **sale con 0**, para que la
  tarea programada no lo convierta en una alerta cada vez que el Toshiba esté apagado. Con un límite: si
  la copia más nueva del PC tiene más de una semana, sale con 1, porque un `ssh` que no conecta también
  puede ser una clave rechazada, y eso no se arregla esperando.
- **Lo que sí es grave sale con 1:** que la copia traída no sirva, que el servidor no tenga ninguna o que
  la más nueva del servidor tenga más de 36 h (el cron ha dejado de hacerlas).
- **`backups_toshiba/copias/traidas.log`**: una línea por ejecución que trae algo, no puede o encuentra algo
  grave (las que se saltan por la guarda y las que ya tenían la más nueva no escriben), y cada ejecución
  empieza diciendo cuándo fue la última traída: si hay un hueco de días, se ve al abrirlo.

**Qué hay dentro de las copias:** lo de todas las personas que usan el bot, también sus marcas y sus
avisos. Es para restaurar, no para mirar: la comprobación solo cuenta filas (D9). Y por eso las del PC rotan
igual que las del servidor: un perfil borrado desaparece también de aquí en unas 5 semanas.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))

import copia_perfiles  # noqa: E402

REMOTO = "~/proyectos/jobspy/data/copias"
REGISTRO = "traidas.log"
# El cron del Toshiba hace una al día; con más de esto, ha dejado de hacerlas (lo mismo que /estado).
HORAS_SIN_COPIA_EN_EL_SERVIDOR = 36
# Que el servidor no conteste unos días es normal (apagado, sin red); una semana sin traer ninguna, no.
HORAS_DE_SILENCIO_SIN_SERVIDOR = 7 * 24
# Unos segundos bastan en la LAN o por la VPN; más que eso es que no está.
OPCIONES_SSH = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
# ssh devuelve 255 cuando el fallo es suyo (no conecta, no resuelve, clave rechazada), y el código del
# comando remoto en cualquier otro caso. scp devuelve 1 tanto si no conecta como si falta el fichero.
SSH_NO_CONECTA = 255
# La tarea programada lo lanza con pythonw.exe, sin consola (DESPLIEGUE.md): sin esto, cada ssh, scp y la
# comprobación abrirían su propia ventana negra un instante. conhost --headless no valía: no devuelve el
# código de salida, y el planificador no distinguiría «bien» de «la copia no sirve» (probado el 06-11).
SIN_VENTANA = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def edad_en_horas(fecha: datetime, ahora: datetime) -> float:
    return (ahora - fecha).total_seconds() / 3600


def registrar(destino: Path, linea: str, ahora: datetime) -> None:
    """Una línea en traidas.log, que se queda en las últimas 200. Su fallo no tapa el resultado."""
    try:
        destino.mkdir(parents=True, exist_ok=True)
        camino = destino / REGISTRO
        anteriores = camino.read_text(encoding="utf-8").splitlines() if camino.exists() else []
        lineas = (anteriores + [f"{ahora:%Y-%m-%d %H:%M:%S} UTC {linea}"])[-copia_perfiles.LINEAS_DE_REGISTRO:]
        temporal = camino.with_name(camino.name + ".tmp")
        temporal.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        temporal.replace(camino)
    except OSError:
        pass


CONTACTO = "ultimo_contacto"


def ultimo_contacto(destino: Path) -> datetime | None:
    """Cuándo contestó el servidor por última vez a este PC, o None (nunca, o el fichero no se lee)."""
    try:
        return datetime.fromisoformat((destino / CONTACTO).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def apuntar_contacto(destino: Path, ahora: datetime) -> None:
    try:
        destino.mkdir(parents=True, exist_ok=True)
        (destino / CONTACTO).write_text(ahora.isoformat(timespec="seconds") + "\n", encoding="utf-8")
    except OSError:
        pass  # sin él, la próxima vez se vuelve a mirar: cuesta un ssh, no una copia


def ultima_traida(destino: Path) -> str:
    """«Última traída: <copia> (hace N h)», leído de traidas.log; o que no hay ninguna."""
    camino = destino / REGISTRO
    try:
        for linea in reversed(camino.read_text(encoding="utf-8").splitlines()):
            if " OK traída " in linea:
                return "Última traída: " + linea.split(" OK traída ", 1)[1].split(" ", 1)[0] + f" ({linea[:16]} UTC)."
    except OSError:
        pass
    return "Todavía no se ha traído ninguna copia a este PC."


def main(argv: list[str] | None = None, ejecutar=subprocess.run, ahora: datetime | None = None) -> int:
    p = argparse.ArgumentParser(description="Trae y comprueba la última copia del Toshiba.")
    p.add_argument("--servidor", default="toshiba")
    p.add_argument("--destino", type=Path, default=RAIZ / "backups_toshiba" / "copias")
    p.add_argument("--si-han-pasado", type=float, default=None, metavar="HORAS",
                   help="no contactar con el servidor si se habló con él hace menos de HORAS (la tarea usa 6)")
    args = p.parse_args(argv)
    ahora = ahora or _ahora()
    destino = args.destino

    locales = copia_perfiles.copias_en(destino)
    print(ultima_traida(destino))
    contacto = ultimo_contacto(destino)
    if args.si_han_pasado is not None and contacto is not None:
        edad = edad_en_horas(contacto, ahora)
        if 0 <= edad < args.si_han_pasado:
            print(f"Se habló con el servidor hace {edad:.1f} h (< {args.si_han_pasado:g}): no hace falta mirar.")
            return 0

    lista = ejecutar(["ssh", *OPCIONES_SSH, args.servidor, f"ls -1 {REMOTO}"],
                     capture_output=True, text=True, timeout=90, **SIN_VENTANA)
    if lista.returncode == SSH_NO_CONECTA:
        # 255 también es una clave rechazada o un host que cambió, que no se arreglan solos. Por eso el
        # silencio tiene un límite: con la copia más nueva del PC de hace más de una semana, ya es grave.
        vieja = not locales or edad_en_horas(locales[0][0], ahora) > HORAS_DE_SILENCIO_SIN_SERVIDOR
        registrar(destino, "SIN SERVIDOR: no conecta (apagado, sin red o la clave)"
                  + ("; ⚠️ más de una semana sin traer ninguna" if vieja else "; se intentará la próxima vez"), ahora)
        print("El Toshiba no contesta (apagado, sin red o la clave): se intentará la próxima vez."
              + (" ⚠️ Y ya hace más de una semana que no se trae ninguna copia." if vieja else ""))
        return 1 if vieja else 0
    if lista.returncode == 0:
        apuntar_contacto(destino, ahora)
    nombres = sorted(n.strip() for n in (lista.stdout or "").splitlines() if copia_perfiles.NOMBRE.match(n.strip()))
    if not nombres:
        registrar(destino, "FALLA: el servidor no tiene ninguna copia (¿el cron?)", ahora)
        print(f"No hay copias en {args.servidor}:{REMOTO} (¿está instalado el cron?).", file=sys.stderr)
        return 1
    ultima = nombres[-1]
    fecha_ultima = datetime.strptime(copia_perfiles.NOMBRE.match(ultima).group(1),
                                     "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    grave = 0
    if edad_en_horas(fecha_ultima, ahora) > HORAS_SIN_COPIA_EN_EL_SERVIDOR:
        registrar(destino, f"AVISO: la más nueva del servidor, {ultima}, tiene más de "
                           f"{HORAS_SIN_COPIA_EN_EL_SERVIDOR} h: el cron no está haciendo copias", ahora)
        print(f"⚠️ La copia más nueva del servidor tiene más de {HORAS_SIN_COPIA_EN_EL_SERVIDOR} h.", file=sys.stderr)
        grave = 1

    destino.mkdir(parents=True, exist_ok=True)
    local = destino / ultima
    if local.exists():
        print(f"{ultima} ya estaba en el PC (y se comprobó al traerla).")
        return grave
    parcial = local.with_name(local.name + ".parcial")
    r = ejecutar(["scp", "-q", *OPCIONES_SSH, f"{args.servidor}:{REMOTO}/{ultima}", str(parcial)], timeout=600,
                 **SIN_VENTANA)
    if r.returncode != 0 or not parcial.exists():
        parcial.unlink(missing_ok=True)
        # Ya ha contestado al ssh, así que lo normal es un corte a mitad: se reintenta la próxima vez.
        registrar(destino, f"SIN SERVIDOR: el scp de {ultima} no ha terminado; se intentará la próxima vez", ahora)
        print("El scp no ha terminado: se intentará la próxima vez.")
        return grave
    parcial.replace(local)

    comprobacion = ejecutar([sys.executable, str(RAIZ / "pruebas" / "comprobar_copia.py"), str(local),
                             "--con-el-codigo"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env={**os.environ, "PYTHONUTF8": "1"}, **SIN_VENTANA)
    print((comprobacion.stdout or "").rstrip())
    if comprobacion.returncode != 0:
        rechazadas = destino / "rechazadas"
        rechazadas.mkdir(exist_ok=True)
        shutil.move(str(local), rechazadas / local.name)
        registrar(destino, f"FALLA: {ultima} no sirve; apartada en rechazadas/ y sin rotar", ahora)
        print(f"La copia NO sirve: apartada en {rechazadas}. No se rota nada.", file=sys.stderr)
        return 1
    borradas = copia_perfiles.rotar(destino, 7, 4)
    quedan = len(copia_perfiles.copias_en(destino))
    registrar(destino, f"OK traída {ultima} {local.stat().st_size / 1048576:.1f} MiB, comprobada; "
                       f"rotadas {len(borradas)}; quedan {quedan}", ahora)
    print(f"Traída {ultima} ({local.stat().st_size / 1048576:.1f} MiB). En el PC quedan {quedan} copias"
          + (f" (rotadas {len(borradas)})." if borradas else "."))
    return grave


if __name__ == "__main__":
    raise SystemExit(main())
