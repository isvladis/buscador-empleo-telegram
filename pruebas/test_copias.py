# -*- coding: utf-8 -*-
"""
Copias de perfiles.db (MP10): `operacion/copia_perfiles.py` y `pruebas/comprobar_copia.py`.

Lo que tiene que ser verdad para fiarse de una copia, cada cosa con su control negativo:

1. **Lleva lo que aún está en el WAL.** Con el servicio en marcha, lo último escrito vive en el
   `-wal` hasta el checkpoint. La copia lo trae; un `cp` del fichero suelto, no (control).
2. **No toca la base viva:** la abre con `mode=ro`; base y `-wal`, iguales byte a byte después.
3. **El manifiesto no lleva ningún chat_id** (D9): solo números. Control: el detector lo ve si se mete.
4. **`comprobar_copia.py` dice que sirve, y la abre el código del servicio** (esquema, titular,
   config efectiva, ficha y /estado). Y dice que NO sirve con un tar cortado, un fichero que no
   casa con su huella, una base dañada con huella «buena», recuentos que no cuadran o un miembro
   con ruta peligrosa: cinco controles negativos.
5. **Rotación:** 7 diarias + la más nueva de cada una de las 4 semanas anteriores; nunca borra
   lo que no tiene nombre exacto de copia, ni rota si la copia ha fallado.

Ninguna prueba sale a la red ni a Telegram. Los chat_id son inventados.

    PYTHONUTF8=1 python pruebas/test_copias.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "operacion"))
DATOS = Path(tempfile.mkdtemp(prefix="test-copias-"))
TITULAR, OTRA = 918273645, 564738291   # números que no pueden salir por casualidad en un recuento
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
for variable in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
    os.environ.pop(variable, None)
(DATOS / "config.json").write_text(json.dumps({"_config_version": 2, "max_perfiles": 3,
                                               "busqueda_automatica_activa": False}), encoding="utf-8")
(DATOS / "jooble_uso.json").write_text('{"peticiones": 3}', encoding="utf-8")

import copia_perfiles  # noqa: E402
import perfiles  # noqa: E402

COMPROBAR = RAIZ / "pruebas" / "comprobar_copia.py"
fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def oferta(n: int, titulo: str) -> dict:
    cuando = datetime.now().isoformat(timespec="seconds")
    return {"job_url": f"https://www.linkedin.com/jobs/view/{n}", "title": titulo, "company": f"Empresa {n}",
            "location": "Barcelona, Catalonia, Spain", "site": "linkedin", "is_remote": False,
            "description": f"Texto de la oferta {n}.", "relevancia": 6.0, "motivos_relevancia": [],
            "visto_por_primera_vez": cuando, "visto_por_ultima_vez": cuando}


def sembrar() -> None:
    perfiles.guardar(TITULAR, perfiles.PERFIL_GLOBAL["datos"], "activo")
    perfiles.guardar(OTRA, perfiles.datos_de_plantilla(), "activo")
    perfiles.aplicar_cambios(TITULAR, escribir=[(oferta(1, "Analista SOC"), 0, 0), (oferta(2, "Técnico N1"), 0, 1)])
    perfiles.aplicar_cambios(OTRA, escribir=[(oferta(3, "Soporte IT"), 0, 0)])
    perfiles.fijar_marca(OTRA, oferta(3, "")["job_url"], "favorito", {"title": "Soporte IT"}, {"origen": "telegram"})
    perfiles.meta_set("titular", str(TITULAR))   # lo que deja la migración en la base real
    perfiles.cerrar()


def comprobar(copia: Path, *extra: str) -> subprocess.CompletedProcess:
    entorno = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(COMPROBAR), str(copia), *extra], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=entorno)


def rehacer_tar(origen: Path, destino: Path, cambiar) -> None:
    """Copia el tar cambiando sus miembros con `cambiar(nombre, bytes) -> bytes | None` (None = quitar)."""
    with tarfile.open(origen, "r:gz") as tar:
        miembros = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
    with tarfile.open(destino, "w:gz") as tar:
        for nombre, datos in miembros:
            nuevo = cambiar(nombre, datos)
            if nuevo is None:
                continue
            info = tarfile.TarInfo(nombre)
            info.size = len(nuevo)
            tar.addfile(info, io.BytesIO(nuevo))


def caso_wal_y_base_intacta() -> Path:
    print("\n[1] La copia trae lo que aún está en el WAL, y no toca la base viva")
    base = DATOS / "perfiles.db"
    vivo = sqlite3.connect(base)
    vivo.execute("PRAGMA journal_mode=WAL")
    vivo.execute("PRAGMA wal_autocheckpoint=0")       # como un servicio que aún no ha hecho checkpoint
    vivo.execute("INSERT OR REPLACE INTO meta VALUES ('solo_en_el_wal', 'sí')")
    vivo.commit()
    wal = base.with_name(base.name + "-wal")
    comprueba(wal.exists() and wal.stat().st_size > 0, "la escritura está solo en el -wal (hay -wal sin checkpoint)")
    antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (base, wal)}

    destino = DATOS / "copias"
    copia = copia_perfiles.hacer_copia(DATOS, destino, datetime(2026, 11, 5, 3, 10, tzinfo=timezone.utc))
    despues = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (base, wal)}
    comprueba(antes == despues, "la base viva y su -wal, idénticos byte a byte tras copiar (mode=ro)")

    with tarfile.open(copia, "r:gz") as tar:
        extraida = DATOS / "extraida.db"
        extraida.write_bytes(tar.extractfile(copia_perfiles.BASE).read())
        nombres = tar.getnames()
    c = sqlite3.connect(extraida)
    comprueba(c.execute("SELECT valor FROM meta WHERE clave = 'solo_en_el_wal'").fetchone() == ("sí",),
              "la copia lleva la fila que solo estaba en el -wal")
    c.close()
    # Control negativo: un cp del fichero suelto, que es lo que hacía DESPLIEGUE.md con el servicio parado.
    suelto = DATOS / "cp-suelto.db"
    shutil.copy2(base, suelto)
    c = sqlite3.connect(suelto)
    comprueba(c.execute("SELECT valor FROM meta WHERE clave = 'solo_en_el_wal'").fetchone() is None,
              "control: un cp de perfiles.db sin el -wal NO la lleva")
    c.close()
    vivo.close()
    comprueba(nombres[0] == copia_perfiles.MANIFIESTO and "config.json" in nombres and "jooble_uso.json" in nombres,
              "el manifiesto va el primero, con config.json y lo pequeño")
    comprueba(oct(copia.stat().st_mode & 0o777) in ("0o600", "0o666"),  # Windows no tiene permisos de grupo
              "la copia queda con permisos 600")
    return copia


def caso_manifiesto_sin_chat_id(copia: Path) -> None:
    print("\n[2] El manifiesto: solo números, ningún chat_id")
    with tarfile.open(copia, "r:gz") as tar:
        texto = tar.extractfile(copia_perfiles.MANIFIESTO).read().decode("utf-8")

    def lleva_chat_id(t: str) -> bool:
        return str(TITULAR) in t or str(OTRA) in t

    comprueba(not lleva_chat_id(texto), "ningún chat_id en el manifiesto")
    comprueba(lleva_chat_id(texto + f' "{OTRA}"'), "control: el detector sí lo ve si se cuela")
    manifiesto = json.loads(texto)
    comprueba(manifiesto["base"]["filas"]["perfiles"] == 2 and manifiesto["base"]["ofertas_sin_enlace"] == 0,
              "recuentos: 2 perfiles y 0 ofertas sin enlace")


def caso_comprobar(copia: Path) -> None:
    print("\n[3] comprobar_copia.py: la buena sirve, y las malas no")
    r = comprobar(copia, "--con-el-codigo")
    comprueba(r.returncode == 0 and "La copia sirve" in r.stdout, "la copia sirve y la abre el código del servicio")
    comprueba("/estado sale entero" in r.stdout and "perfil 2/2" in r.stdout, "config efectiva, ficha y /estado de los dos")
    comprueba(str(TITULAR) not in r.stdout + r.stderr and str(OTRA) not in r.stdout + r.stderr,
              "la salida no lleva ningún chat_id")

    malas = DATOS / "malas"
    malas.mkdir()
    cortada = malas / "cortada.tar.gz"
    cortada.write_bytes(copia.read_bytes()[: copia.stat().st_size // 2])
    comprueba(comprobar(cortada).returncode == 1, "control: un tar cortado no sirve")

    def tocar_config(nombre, datos):
        return datos + b" " if nombre == "config.json" else datos
    otra_huella = malas / "huella.tar.gz"
    rehacer_tar(copia, otra_huella, tocar_config)
    r = comprobar(otra_huella)
    comprueba(r.returncode == 1 and "huella" in r.stdout, "control: un fichero que no casa con su huella no sirve")

    # Base dañada con la huella al día: solo la ve integrity_check.
    with tarfile.open(copia, "r:gz") as tar:
        base = bytearray(tar.extractfile(copia_perfiles.BASE).read())
        manifiesto = json.loads(tar.extractfile(copia_perfiles.MANIFIESTO).read())
    pagina = 4096
    for i in range(2 * pagina + 8, 3 * pagina - 8):   # el interior de la tercera página
        base[i] = 0xA5
    manifiesto["ficheros"][copia_perfiles.BASE]["sha256"] = hashlib.sha256(bytes(base)).hexdigest()

    def danar(nombre, datos):
        if nombre == copia_perfiles.BASE:
            return bytes(base)
        if nombre == copia_perfiles.MANIFIESTO:
            return json.dumps(manifiesto).encode("utf-8")
        return datos
    danada = malas / "danada.tar.gz"
    rehacer_tar(copia, danada, danar)
    r = comprobar(danada)
    comprueba(r.returncode == 1 and ("integrity_check" in r.stdout or "no se abre" in r.stdout),
              "control: una base dañada con la huella al día no sirve (integrity_check)")

    def descuadrar(nombre, datos):
        if nombre != copia_perfiles.MANIFIESTO:
            return datos
        m = json.loads(datos)
        m["base"]["filas"]["marcas"] += 1
        return json.dumps(m).encode("utf-8")
    descuadrada = malas / "recuentos.tar.gz"
    rehacer_tar(copia, descuadrada, descuadrar)
    r = comprobar(descuadrada)
    comprueba(r.returncode == 1 and "recuentos" in r.stdout, "control: recuentos que no son los del manifiesto no sirven")

    peligrosa = malas / "ruta.tar.gz"
    with tarfile.open(copia, "r:gz") as tar:
        miembros = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
    with tarfile.open(peligrosa, "w:gz") as tar:
        for nombre, datos in miembros + [("../fuera.txt", b"x")]:
            info = tarfile.TarInfo(nombre)
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
    r = comprobar(peligrosa, "--extraer", str(malas / "aqui"))
    comprueba(r.returncode == 1 and not (malas / "fuera.txt").exists(), "control: un miembro con ruta '..' no sirve ni se extrae")

    extraer = DATOS / "restaurar"
    r = comprobar(copia, "--extraer", str(extraer))
    comprueba(r.returncode == 0 and (extraer / "perfiles.db").exists() and (extraer / "config.json").exists(),
              "--extraer deja la base y lo pequeño, ya comprobados")
    r = comprobar(copia, "--extraer", str(extraer))
    comprueba(r.returncode == 1 and "no está vacía" in r.stdout, "--extraer no mezcla una copia con datos que ya hay")


def caso_rotacion() -> None:
    print("\n[4] Rotación: 7 diarias y 4 semanales; nada que no sea una copia")
    carpeta = DATOS / "rotacion"
    carpeta.mkdir()
    inicio = datetime(2026, 9, 20, 3, 10, tzinfo=timezone.utc)   # domingo
    for dia in range(45):
        (carpeta / f"copia-{(inicio + timedelta(days=dia)):%Y%m%d_%H%M%S}.tar.gz").write_bytes(b"x")
    ajenos = ["copia-hola.tar.gz", "registro.log", "copia-20260101_000000.tar.gz.parcial", "notas.txt"]
    for nombre in ajenos:
        (carpeta / nombre).write_bytes(b"x")
    borradas = copia_perfiles.rotar(carpeta, 7, 4)
    quedan = copia_perfiles.copias_en(carpeta)
    fechas = [f for f, _ in quedan]
    ultima = inicio + timedelta(days=44)
    comprueba(len(quedan) == 11 and len(borradas) == 34, f"quedan 11 (7 + 4) de 45; quedan {len(quedan)}")
    comprueba(fechas[:7] == [ultima - timedelta(days=d) for d in range(7)], "las 7 más nuevas, seguidas")
    semanas = [tuple(f.isocalendar()[:2]) for f in fechas]
    comprueba(len(set(semanas[7:])) == 4 and not set(semanas[7:]) & set(semanas[:7]),
              "4 semanales, cada una de una semana distinta y anterior a las diarias")
    comprueba(all(f == max(g for g in (inicio + timedelta(days=d) for d in range(45))
                           if tuple(g.isocalendar()[:2]) == tuple(f.isocalendar()[:2])) for f in fechas[7:]),
              "de cada semana, la más nueva")
    comprueba(all((carpeta / n).exists() for n in ajenos), "no toca lo que no tiene nombre exacto de copia")
    try:
        copia_perfiles.a_borrar(copia_perfiles.copias_en(carpeta), 0, 4)
        comprueba(False, "control: conservar 0 copias se rechaza")
    except ValueError:
        comprueba(True, "control: conservar 0 copias se rechaza")

    # Una copia que falla no rota: sin base no hay copia, y las de antes siguen todas.
    vacia = DATOS / "sin-base"
    vacia.mkdir()
    for dia in range(10):
        (vacia / "copias").mkdir(exist_ok=True)
        (vacia / "copias" / f"copia-{(inicio + timedelta(days=dia)):%Y%m%d_%H%M%S}.tar.gz").write_bytes(b"x")
    salida = copia_perfiles.main(["--datos", str(vacia)])
    comprueba(salida == 1 and len(copia_perfiles.copias_en(vacia / "copias")) == 10,
              "una copia fallida sale con 1 y no rota nada (siguen las 10)")
    registro = (vacia / "copias" / copia_perfiles.REGISTRO).read_text(encoding="utf-8")
    comprueba("FALLA" in registro, "el fallo queda en registro.log")
    comprueba(not any(p.name.startswith(".copia-") for p in (DATOS / "copias").iterdir()),
              "no quedan temporales en el destino")


class ServidorDeMentira:
    """
    Lo que traer_copias.py le pide a ssh y a scp, sin red: una carpeta hace de data/copias del Toshiba.
    La comprobación (`comprobar_copia.py`) sí se ejecuta de verdad.
    """

    def __init__(self, carpeta: Path, conecta: bool = True):
        self.carpeta, self.conecta, self.llamadas = carpeta, conecta, []

    def __call__(self, orden, **kw):
        self.llamadas.append(orden[0] if orden[0] in ("ssh", "scp") else "comprobar")
        if orden[0] == "ssh":
            if not self.conecta:
                return subprocess.CompletedProcess(orden, 255, "", "ssh: connect to host toshiba: timed out")
            return subprocess.CompletedProcess(orden, 0, "\n".join(sorted(p.name for p in self.carpeta.iterdir())), "")
        if orden[0] == "scp":
            nombre = orden[-2].rsplit("/", 1)[1]
            shutil.copy2(self.carpeta / nombre, orden[-1])
            return subprocess.CompletedProcess(orden, 0)
        kw.setdefault("env", dict(os.environ))
        kw["env"]["PYTHONUTF8"] = "1"
        return subprocess.run(orden, **kw)


def caso_traer_copias(copia: Path) -> None:
    print("\n[5] traer_copias: un PC que no siempre está encendido, y un servidor que a veces no contesta")
    import traer_copias

    remoto = DATOS / "remoto"
    remoto.mkdir()
    shutil.copy2(copia, remoto / copia.name)                    # copia-20261105_031000.tar.gz, buena
    (remoto / "registro.log").write_text("x", encoding="utf-8")  # lo que no es una copia no se trae
    pc = DATOS / "pc"
    ahora = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)   # la del servidor tiene 6,8 h

    servidor = ServidorDeMentira(remoto)
    salida = traer_copias.main(["--destino", str(pc), "--si-han-pasado", "20"], servidor, ahora)
    registro = (pc / traer_copias.REGISTRO).read_text(encoding="utf-8")
    comprueba(salida == 0 and (pc / copia.name).exists() and servidor.llamadas == ["ssh", "scp", "comprobar"],
              "sin copias en el PC: la trae y la comprueba con el código (sale con 0)")
    comprueba("OK traída copia-20261105_031000.tar.gz" in registro, "y lo deja en traidas.log")
    comprueba("copia-20261105_031000.tar.gz" in traer_copias.ultima_traida(pc), "y dice cuál fue la última traída")

    servidor = ServidorDeMentira(remoto)
    salida = traer_copias.main(["--destino", str(pc), "--si-han-pasado", "6"], servidor, ahora + timedelta(hours=2))
    comprueba(salida == 0 and servidor.llamadas == [], "2 h después de hablar con el servidor, con --si-han-pasado 6 ni contacta")
    servidor = ServidorDeMentira(remoto)
    salida = traer_copias.main(["--destino", str(pc), "--si-han-pasado", "6"], servidor, ahora + timedelta(hours=7))
    comprueba(servidor.llamadas == ["ssh"] and salida == 0,
              "control: 7 h después sí mira; y si ya tiene la más nueva, no la vuelve a comprobar")
    lineas = len((pc / traer_copias.REGISTRO).read_text(encoding="utf-8").splitlines())
    comprueba(lineas == 1, "ni la que se salta ni la que ya la tenía llenan el registro")

    # Lo que encontró la primera ejecución real (06-11): la copia más nueva del PC era una hecha a mano hace
    # 12 h, y la guarda de entonces (por la edad de la copia) se saltaba la del cron que ya estaba en el servidor.
    manual = DATOS / "pc-manual"
    manual.mkdir()
    shutil.copy2(copia, manual / "copia-20261104_213000.tar.gz")      # «a mano», de la víspera
    remoto2 = DATOS / "remoto2"
    remoto2.mkdir()
    shutil.copy2(copia, remoto2 / "copia-20261105_031000.tar.gz")      # la del cron, más nueva
    # Con 20 h, el umbral con el que falló: la guarda vieja se la saltaba (la copia del PC tiene 12,3 h).
    salida = traer_copias.main(["--destino", str(manual), "--si-han-pasado", "20"], ServidorDeMentira(remoto2),
                               datetime(2026, 11, 5, 9, 50, tzinfo=timezone.utc))
    comprueba(salida == 0 and (manual / "copia-20261105_031000.tar.gz").exists(),
              "con una copia a mano de hace 12 h en el PC, la guarda no se salta la del cron que ya está en el servidor")

    servidor = ServidorDeMentira(remoto, conecta=False)
    salida = traer_copias.main(["--destino", str(pc)], servidor, ahora + timedelta(days=2))
    comprueba(salida == 0 and "SIN SERVIDOR" in (pc / traer_copias.REGISTRO).read_text(encoding="utf-8"),
              "el servidor apagado no es un error: sale con 0 y deja su línea")
    salida = traer_copias.main(["--destino", str(pc)], ServidorDeMentira(remoto, conecta=False), ahora + timedelta(days=8))
    comprueba(salida == 1, "control: con más de una semana sin traer ninguna, el mismo silencio sí sale con 1")

    malo = DATOS / "remoto-malo"
    malo.mkdir()
    cortada = malo / "copia-20261105_090000.tar.gz"
    cortada.write_bytes(copia.read_bytes()[: copia.stat().st_size // 2])
    salida = traer_copias.main(["--destino", str(pc)], ServidorDeMentira(malo), ahora)
    comprueba(salida == 1 and (pc / "rechazadas" / cortada.name).exists() and not (pc / cortada.name).exists()
              and (pc / copia.name).exists(),
              "una copia que no sirve sale con 1, se aparta a rechazadas/ y la buena sigue")
    comprueba("FALLA" in (pc / traer_copias.REGISTRO).read_text(encoding="utf-8"), "   y queda en el registro como FALLA")

    vacio = DATOS / "remoto-vacio"
    vacio.mkdir()
    comprueba(traer_copias.main(["--destino", str(pc)], ServidorDeMentira(vacio), ahora) == 1,
              "un servidor sin ninguna copia (el cron) sale con 1")
    comprueba(traer_copias.main(["--destino", str(pc)], ServidorDeMentira(remoto), ahora + timedelta(hours=40)) == 1
              and "AVISO" in (pc / traer_copias.REGISTRO).read_text(encoding="utf-8"),
              "y uno cuya copia más nueva tiene más de 36 h, también: el cron ha dejado de hacerlas")


def main() -> int:
    sembrar()
    copia = caso_wal_y_base_intacta()
    caso_manifiesto_sin_chat_id(copia)
    caso_comprobar(copia)
    caso_rotacion()
    caso_traer_copias(copia)
    # La misma última línea que los demás bloques: todas.py la enseña y suma el total con ella.
    if fallos:
        print("\nFALLOS (%d de %d comprobaciones):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
