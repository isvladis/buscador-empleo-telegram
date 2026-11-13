# -*- coding: utf-8 -*-
"""
El catálogo de familias profesionales y el generador del plan de perfil (MP7,
EVOLUCION-MULTIPERFIL.md §5.1 y §5.5).

- `fuente.py`: las familias y los puestos, escritos en Python para que cada regex lleve su porqué.
- `catalogo.json`: lo que sale de `fuente.py` con `python -m perfilado.construir`. Es lo que lee el
  generador; una prueba comprueba que no se ha quedado atrás.
- `generador.py`: `generar_plan(respuestas)`, de las respuestas de la encuesta a un plan de perfil.

**La corrida no lee nada de aquí.** El plan es autosuficiente (§5.5): la encuesta (MP8) genera el
plan una vez, se guarda en el perfil y `perfiles.config_efectiva` solo mira el plan. Así el catálogo
no ocupa RAM en el contenedor del Toshiba, que va por 304 MiB de 512, ni se parsea por corrida.
"""
