---
name: modo-voz
description: La voz de Claude en tu Mac (SFlow la dice en voz alta). Tres usos — (1) resumen hablado en lenguaje sencillo, de una respuesta o de cada respuesta del chat; (2) narración en vivo de una demo en pantalla (con demo); (3) apagar. Úsala SIEMPRE que la persona pida voz, con sus palabras o con comando — "léeme el resumen", "dímelo en voz", "resumen en voz", "modo voz", "usa la skill de modo voz", "con demo", "sin demo", "modo demo", "nárrame la demo", "muéstrame en pantalla con voz", /modo-voz, /modo-voz demo — o cuando el mensaje trae la nota «[Modo coche]» (la persona habla desde el Orbe de Arbrain) — y para apagarla — "apaga el modo voz", "apaga el demo", "para la voz", "/modo-voz apagar".
---

# Modo voz — Claude habla contigo a través de SFlow

SFlow dice en voz alta, en tu Mac (con la voz que elegiste en su menú «Voz de lectura»), lo que le mandas con `decir.py` (sección 0, la forma preferida) y, de respaldo, cada línea que **empieza con 🔊**. El 🔊 es la marca que buscan los ganchos de Claude Code (`claude_hooks/voz.py`); es la única excepción si tus instrucciones piden no usar emojis.

## Cómo se habla (vale para todo)

- **El 🔊 va al inicio de su propia línea.** Nunca dentro de código ni a mitad de una frase: ahí no cuenta.
- **Es para oírse, no para leerse:** en el idioma de la persona y en tono de conversación. Sin código, rutas, comandos, siglas técnicas, tablas ni enlaces. Números solo si importan, dichos como se hablan.
- **El resto de tu respuesta no cambia.** Lo técnico sigue escrito igual, en líneas aparte, y no se oye.

## Qué se cuenta: como un colega que te cuenta su trabajo

La voz no lee la respuesta: la cuenta ya digerida, como alguien que trabajó contigo, para que entiendas lo que te
entrega casi sin leer. Lo escrito en el chat **no cambia nada** (mismo razonamiento, mismo detalle, mismas tablas): la
voz es un complemento encima de eso.

1. **Qué hice, qué te entrego y dónde quedó.** «Te dejé una tabla con los pagos de octubre en el chat», sin leerla.
2. **Lo importante, sin omitir:** decisiones que tomé, lo que cambió, riesgos, errores y lo que no pude verificar. Si
   algo salió mal o quedó a medias, se dice en voz, nunca solo por escrito.
3. **Lo que necesito de ti:** cada pregunta clara, una por una, y qué recomiendo. Si no necesito nada, se dice.
4. **Nunca se leen** tablas, código, listas largas, rutas, enlaces ni comandos: se nombran y se resumen («son cinco
   pendientes; los dos urgentes son…»).
5. **El largo sigue a la importancia:** corto si fue algo chico; más completo si hubo mucho trabajo o una decisión. Si
   la persona quiere el detalle, lo pide en el siguiente turno.

**Modo coche (nota «[Modo coche]» en el mensaje):** la persona te habla desde el Orbe de Arbrain, a veces manejando y
sin poder leer. Esa nota basta: responde para ser oído aunque no haya pedido el modo voz, siguiendo esta sección. En el
modo coche **el Orbe lee en voz alta tu respuesta escrita**: escríbela corta y conversacional (sin tablas, código ni
listas largas) y **no uses `decir.py` ni 🔊** en ese mensaje, o se oiría dos veces. La nota vale solo para ese mensaje:
no enciende el modo voz para los demás.

## 0 · Cómo suena: directo a SFlow (la forma preferida)

- **La voz va directa.** Cada frase se dice corriendo, por la terminal:
  `/usr/bin/python3 -I ~/.claude/skills/modo-voz/decir.py 'frase'`. El script le manda la frase a SFlow
  y **espera a que termine de decirla** antes de devolver el control: lo que se oye y lo que se ve en
  pantalla van juntos. No depende del gancho de las líneas 🔊 (que a veces no ve el texto previo a una acción).
- **La frase va entre comillas simples**, para que la terminal no toque los signos (`$`, `!`, comillas dobles).
- **Si hablaste directo, la respuesta NO lleva 🔊.** El gancho de cierre diría ese 🔊 otra vez y se oiría dos
  veces. (El gancho ya no relee una respuesta sin 🔊 cuando hablaste directo.)
- **El 🔊 queda de respaldo.** Si el script responde "SIN VOZ", dilo por escrito en una línea (SFlow no
  respondió) y usa la línea 🔊 al cierre como se describe abajo.
- **Respaldo escrito, siempre.** La voz nunca reemplaza el texto: todo lo que se dice en voz queda también
  escrito en la respuesta. En una demo, el mensaje final recoge lo narrado paso por paso en una tabla corta.
- **Una llamada por paso**, antes de la acción, nunca entre una acción y su verificación cuando el
  tiempo importa (sección 2). Cada frase toca a unas 2,5 palabras por segundo: la espera la marca su largo.
- **Diagnóstico:** cada frase directa deja una línea `directa ·` en `~/Library/Application Support/SFlow/voz.log`.
- **Requisito:** SFlow abierta y `claude_hooks/instalar.py` corrido una vez.

## 1 · Resumen hablado (sin demo)

- **Una vez** ("léeme el resumen", "dímelo en voz"): cierra ESA respuesta con el resumen hablado: una llamada a `decir.py` justo antes del mensaje final (de respaldo, un párrafo que empiece con 🔊, antes de la firma si la hay).
- **En cada respuesta** ("modo voz", "usa el modo voz", "sin demo", /modo-voz): haz lo mismo en todas las respuestas de este chat hasta que lo apague.
- **Cómo:** empieza con el nombre de la persona si lo sabes y sigue «Qué se cuenta» (arriba). Lo normal son 2 a 5 frases; más solo si el trabajo o la decisión lo piden.
- **Si piden comentarios en voz con varias ideas:** con voz directa, una sola llamada con la entrada y las ideas seguidas, cada una terminada en punto. De respaldo 🔊: una línea 🔊 de entrada y, **pegadas debajo sin renglón en blanco**, las ideas en viñetas (`- `), cada una en una o dos frases que terminen en punto. Ejemplo:

  ```
  🔊 Te doy mis comentarios en tres ideas.
  - Primera idea, en llano.
  - Segunda idea.
  - Tercera idea.
  ```

- **De respaldo 🔊: UN SOLO 🔊 por respuesta, y un solo bloque.** Fuera del modo demo, SFlow solo dice el **último** bloque 🔊 de la respuesta, y el bloque termina en el primer renglón en blanco. Un segundo 🔊 anula todo lo anterior, y un renglón en blanco corta lo que sigue.

## 2 · Narración en vivo de una demo ("con demo", "modo demo", /modo-voz demo, /modo-demo)

Mientras muestras algo en el navegador o en pantalla, cada frase la dice SFlow **antes** de tu siguiente acción, y la acción espera a que la voz termine: lo que se oye y lo que se ve van juntos.

**Con voz directa (sección 0) no hace falta encender nada:** cada paso se narra con `decir.py`. El modo demo del gancho (`/usr/bin/python3 "$HOME/Library/Application Support/SFlow/modo_demo.py" on`, se apaga solo a las 3 horas; la acción espera máximo ~10 s) queda para cuando se use el respaldo 🔊. Si no encuentra SFlow, avisa: la demo sigue igual, solo que sin voz.

**Narrar:**
- **Intro:** antes de la primera acción, una frase con lo que vas a mostrar (`decir.py`; de respaldo, una línea 🔊).
- **Un paso, una frase:** antes de cada paso **visible**, una llamada a `decir.py` (o, de respaldo, UNA línea 🔊) con lo que va a ver y qué significa, en llano. Narra el paso, no cada herramienta: una ráfaga de clic y captura lleva una sola frase, o ninguna.
- **Cierre:** al final, una frase o dos con qué se vio, qué quedó bien y qué hay que decidir (`decir.py`; de respaldo, un párrafo 🔊).

**Protecciones de calidad (obligatorias). Lo primero es la prueba; la narración es un extra:**
1. **Tiempos:** cada frase hace esperar a la acción siguiente. Nunca la pongas **entre una acción y su verificación** cuando el tiempo importa: un aviso que desaparece solo, una animación, medir cuánto tarda en cargar, una sesión que vence. Narra **antes** de empezar esa secuencia.
2. **Nada se omite por narrar:** mismas revisiones, mismo orden, mismas esperas y mismos criterios que sin voz. Si dudas entre narrar y verificar, verifica.
3. **Coherencia:** narra lo que vas a hacer justo antes de hacerlo. Si algo falla o sale distinto, dilo en la siguiente frase. No narres un éxito que no verificaste.
4. **Si no hay voz** (SFlow cerrada o silenciada), todo sigue exactamente igual: nada espera.

## 3 · Apagar

- **"Apaga el modo voz"** o "para la voz" (/modo-voz apagar): corre `/usr/bin/python3 "$HOME/Library/Application Support/SFlow/modo_demo.py" off` y deja de hablar: ni `decir.py` ni 🔊, en resúmenes y en narración. El texto escrito sigue igual.
- **"Apaga el demo"** o "sin demo": corre el mismo `off`, deja de narrar, y sigue con el resumen hablado si estaba encendido.
