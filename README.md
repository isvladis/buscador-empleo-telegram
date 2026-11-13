# Buscador de empleo por Telegram

Un servicio pequeño que busca ofertas de empleo **en España** cada pocas horas, las filtra y las
puntúa contra el perfil de cada persona, y le avisa por **Telegram** solo de las que encajan. Cada
aviso lleva botones (⭐ Me interesa · ✅ Aplicada · 🙈 No me interesa) y lo que marcas afina lo que
te llega después.

- **Multiperfil:** un perfil por chat de Telegram. Tú lo instalas y eres quien lo administra (el
  «titular»); puedes invitar a otras personas y cada una tiene su búsqueda, sus filtros y sus
  avisos. Nadie ve lo de otro.
- **De cualquier sector:** el perfil sale de una encuesta de 3-4 minutos dentro del propio bot, con
  un catálogo de 14 familias y 94 puestos (informática, administración, sanidad, hostelería,
  logística, oficios…) y la opción de escribir un puesto que no esté.
- **Sin web y sin puertos abiertos:** todo se hace desde el bot. El servicio solo sale hacia
  Telegram y hacia los portales.
- **Pensado para una máquina modesta:** nació en un portátil viejo con 2 GB de RAM; el contenedor
  va limitado a 512 MB.

Fuentes: LinkedIn e Indeed (a través de [python-jobspy](https://github.com/speedyapply/JobSpy)),
Jooble (su API, con clave gratuita) y Tecnoempleo (solo informática en Barcelona). El código para
EURES e InfoJobs está, pero esas fuentes vienen desactivadas: ver «Uso responsable».

> Es una herramienta personal que se ha ido abriendo, no un producto. Los textos del bot y los
> comentarios del código están en español, y el mercado para el que está afinado es el español.

## Origen

Este proyecto **nació de [JobSpy](https://github.com/speedyapply/JobSpy)**, la librería de
[speedyapply](https://github.com/speedyapply) para buscar ofertas en LinkedIn, Indeed y otros
portales. Partí de ella y la fui adaptando, con ayuda de IA, a lo que necesitaba para mi propia
búsqueda de empleo: filtros y puntuación para el mercado español, avisos por Telegram, varios
perfiles en el mismo bot, un cuestionario para crearlos y lo que cada perfil aprende de sus
botones. Así llegó a lo que hay aquí.

JobSpy sigue siendo la pieza que consulta LinkedIn e Indeed: se instala como librería
(`python-jobspy`, en `requirements.txt`) y no hay código suyo copiado en este repositorio. Es de
sus autores y tiene su propia licencia (MIT, © 2023 Cullen Watson). Todo el mérito de esa parte es
suyo; si te sirve, dale una estrella allí también.

## Puesta en marcha

Necesitas una máquina que esté encendida (un servidor casero, una Raspberry Pi, un VPS) con
Docker, y una cuenta de Telegram.

### 1. Crea tu bot

1. En Telegram, habla con **@BotFather**, escribe `/newbot` y sigue los pasos. Te da un **token**.
2. Habla con **@userinfobot**: te dice tu **número de chat** (un número, no tu @usuario).
3. Abre el chat de tu bot recién creado y escríbele `/start`. Todavía no contesta, pero Telegram no
   deja que un bot escriba a quien no le ha hablado primero.

### 2. Configura y arranca

```bash
git clone <la URL de este repositorio> buscador-empleo
cd buscador-empleo
cp .env.example .env
nano .env                      # pega el token y tu número de chat
chmod 600 .env
docker compose up -d --build
docker compose logs -f         # para ver que arranca
```

`.env` no está en git ni entra en la imagen de Docker. Todo lo que el servicio guarda vive en la
carpeta `data/`, que tampoco está en git.

### 3. Contesta el cuestionario

Escribe `/start` a tu bot. Como es la primera vez, te dice que eres quien lo administra y te manda el
cuestionario: **empiezas de cero**, sin ningún perfil previo, igual que cualquier persona que invites.

1. Contesta (3-4 minutos): sector, puestos, experiencia, ciudad y distancia, modalidad, idiomas,
   avisos. Si tu puesto no está en la lista, «✏️ Otro» y lo escribes.
2. Al confirmar, el bot busca para ti y te enseña unas pocas ofertas para que digas si acierta
   (👍/👎). Con eso ajusta a partir de qué nota te avisa.

`/ayuda` enseña todos los comandos. Para cambiar algo después, `/ajustes`; para empezar otra vez de
cero, `/reiniciar` (una vez cada 24 horas). Mientras contestas no se busca nada para ti, pero el bot
sigue funcionando para las personas que hayas invitado y tú sigues pudiendo administrarlo.

Si lanzas una búsqueda (`/corrida`) antes de escribir `/start`, se hace con el perfil de ejemplo que
trae el proyecto (soporte informático y ciberseguridad en Barcelona); en ese caso, `/reiniciar` lo
deja a cero.

### 4. Enciende la búsqueda automática

Viene **apagada**. `/frecuencia` la enciende y elige cada cuántas horas se busca. `/corrida` lanza
una búsqueda en el momento, y `/estado` enseña cómo van las corridas, las fuentes, la memoria y el
disco.

### Sin Docker

Con Python 3.12:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export JOBSPY_TELEGRAM_TOKEN=... JOBSPY_TELEGRAM_CHAT_ID=... JOBSPY_DATA_DIR=./data
mkdir -p data && python app.py
```

## Comandos del bot

| Quién | Comandos |
|---|---|
| Todos | `/perfil` (tu ficha: qué se busca para ti) · `/ajustes` · `/top` (tus 5 mejores sin clasificar) · `/interesan` (lo guardado con ⭐ y sin aplicar) · `/resumen` (tu semana) · `/pausa` · `/reanudar` · `/misdatos` (todo lo tuyo, en un fichero) · `/reiniciar` (empezar de cero) · `/borrar` · `/ayuda` |
| Titular | además `/invitar` · `/perfiles` · `/estado` · `/corrida` · `/fuentes` · `/frecuencia` |

## Invitar a otras personas

1. `/invitar` te da un enlace `t.me/<tu bot>?start=<código>` de **un solo uso** que caduca a los 7
   días. Al crearlo eliges cómo entra la persona: con la **encuesta** (lo normal) o con la
   plantilla de soporte informático.
2. La persona abre el enlace, contesta la encuesta y empieza a recibir sus avisos.

Quien escriba al bot sin invitación no recibe respuesta. El tope de perfiles es `max_perfiles` (3
por defecto, contándote a ti): está en `data/config.json`. Súbelo con cuidado: todos los perfiles
comparten el mismo presupuesto de consultas a los portales (ver más abajo).

## Qué se puede configurar, y dónde

| Qué | Dónde |
|---|---|
| Sector, puestos, ciudad, distancia, modalidad, experiencia, idiomas | En el bot: `/ajustes` (cada persona lo suyo) |
| Cuándo llegan los avisos: al momento o en resumen, horario de silencio, tope diario | En el bot: `/ajustes → 🔔 Avisos` |
| Lo que el bot ha aprendido de tus botones (verlo, deshacerlo) | En el bot: `/ajustes → 🧠 Lo aprendido` |
| Cada cuántas horas se busca | En el bot: `/frecuencia` (titular) |
| Activar o desactivar una fuente | En el bot: `/fuentes` (titular) |
| Token, chat del titular, clave de Jooble | `.env` (plantilla en `.env.example`) |
| Tope de perfiles, consultas por corrida, antigüedad de las ofertas, empresas excluidas… | `data/config.json`. Se crea solo; las claves y sus valores de fábrica, comentados, están en `config.py` (`CONFIG_POR_DEFECTO`) |
| Los puestos del catálogo: añadir uno, cambiar qué casa con cada uno | `perfilado/fuente.py`, y después `python -m perfilado.construir` (regenera `perfilado/catalogo.json`; ese JSON no se edita a mano) |
| Reglas escritas a mano para un perfil concreto | `reglas/` (los dos juegos que hay son el ejemplo: soporte informático y ciberseguridad) |
| Memoria y límites del contenedor | `docker-compose.yml` |

`data/config.json` solo necesita las claves que quieras cambiar; lo demás sale de los valores de
fábrica. Por ejemplo:

```json
{
  "max_perfiles": 5,
  "hours_old": 168,
  "empresas_excluidas": []
}
```

## Cómo funciona

- **Una sola tanda de descargas por corrida para todos los perfiles.** Las consultas de todos se
  juntan, se quitan las repetidas y se hacen como mucho 33 por corrida. Si entre todos piden más,
  rotan: cada una tarda más en repetirse, pero la carga sobre los portales no sube.
- **La oferta se guarda una vez** y cada perfil enlaza solo las que pasan *sus* filtros (idioma,
  zona y distancia, experiencia exigida, puestos descartados, empresas excluidas).
- **La puntuación es explicable:** cada aviso dice por qué tiene la nota que tiene. Se avisa a
  partir de 5,0 («Encaje alto»); la calibración inicial puede mover ese listón para cada persona.
- **A partir de 30 botones pulsados** se enciende la capa aprendida de ese perfil: entre −3 y +3
  puntos, visible y reversible.
- **No se avisa dos veces de la misma vacante** aunque el portal la republique con otro enlace.
- **Quien deja de usarlo se pausa solo**, con aviso antes; y si dos corridas seguidas fallan, el
  titular recibe un aviso.

Todo vive en `data/perfiles.db` (SQLite). El código principal:

| Fichero | Qué hace |
|---|---|
| `app.py` | El servicio: planificador, lector de Telegram y un trabajador para lo largo |
| `buscador.py` | Consultas a los portales, filtros y puntuación |
| `perfiles.py` | La base de datos: perfiles, ofertas, marcas, avisos, invitaciones |
| `telegram_bot.py`, `telegram_encuesta.py`, `telegram_botones.py` | Comandos, encuesta y `/ajustes`, botones de cada aviso |
| `avisos_perfil.py`, `notificaciones.py` | Cuándo y cómo se manda cada aviso |
| `aprendizaje.py` | La capa aprendida de cada perfil |
| `perfilado/` | El catálogo de puestos y el generador que convierte una encuesta en un perfil |
| `reglas/` | Juegos de reglas escritos a mano |
| `fuentes_extra/` | Las fuentes que no vienen de python-jobspy, y la salud de cada fuente |
| `operacion/copia_perfiles.py` | Copia diaria de `perfiles.db`, para el cron del servidor |

## Datos y privacidad

- De cada persona se guarda su **número de chat de Telegram**, sus respuestas a la encuesta y lo
  que marca con los botones. Ni nombre, ni teléfono, ni CV. La ciudad se guarda con las
  coordenadas redondeadas a ~1 km.
- El titular puede ver la **ficha** de cada perfil (qué se busca para esa persona) y cifras
  (cuántos avisos, qué parte le interesa). No ve qué ofertas recibe ni lo que marca.
- `/misdatos` le manda a cada persona todo lo suyo y `/borrar` lo borra (queda una copia en
  `data/borrados/` del servidor, por si fue un error).
- **Si administras un bot para otras personas, sus datos son tu responsabilidad:** `data/` y las
  copias llevan los perfiles de todos. No las subas a ningún sitio.

## Uso responsable

Este proyecto consulta portales de empleo, y lo hace con cuidado a propósito:

- **No esquiva defensas anti-robot:** ni navegadores sin cabeza, ni resolver CAPTCHAs, ni proxies.
  Cuando una fuente bloquea o pide verificación, se desactiva sola y avisa; `/fuentes` la reactiva
  a mano si procede.
- **El presupuesto de consultas es fijo** (33 por corrida) y no crece con el número de perfiles.
  No lo subas a la ligera: es lo que evita los bloqueos.
- Respeta las condiciones de uso de cada portal y la legislación de tu país. Úsalo para tu propia
  búsqueda de empleo y la de quien invites, no para recopilar ofertas en masa ni para
  redistribuirlas.

## Pruebas

```bash
PYTHONUTF8=1 python pruebas/todas.py
```

Ninguna prueba sale a la red ni escribe a Telegram: los envíos y las consultas a los portales se
sustituyen por espías. En esta copia se **omiten siete bloques de 29 y el medidor de precisión**
(`todas.py` lo dice al terminar): cuatro necesitan una muestra de ofertas reales etiquetadas a mano
por el autor, que no se publica porque es su historial de búsqueda, y tres comparan con versiones
anteriores del código que sacan del historial de git de la instalación original, que tampoco.

En Windows, `PYTHONUTF8=1` no es opcional (los mensajes llevan emoji). Usa Python 3.12 con las
versiones de `requirements.txt`: varias pruebas dependen de la versión fijada de python-jobspy.

## Sobre los comentarios del código

El código está comentado en español y explica *por qué* se hizo cada cosa, a menudo con el dato
medido. Verás referencias a documentos internos que **no** están en este repositorio (`AUDIT.md`,
`CAMBIOS-MPn.md`, `DESPLIEGUE.md`, `EVOLUCION-MULTIPERFIL.md`, `REVISION-*.md`), a fases («MP5»,
«MP8»), a decisiones («D4», «D9») y a «el Toshiba», que es el portátil donde corre la instalación
original. Son el cuaderno de trabajo de esa instalación; el comentario se entiende sin ellos.

## Licencia

MIT. Ver `LICENSE`. Sin garantía de ningún tipo: úsalo bajo tu responsabilidad.

Las librerías de `requirements.txt` tienen cada una su licencia; la de
[JobSpy](https://github.com/speedyapply/JobSpy), de la que nació este proyecto, es también MIT.
