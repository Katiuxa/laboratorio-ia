# Laboratorio IA

Un bot de Telegram en una Raspberry Pi. El mismo sitio sirve para hablar con un modelo de texto y para generar imagen, vídeo y voz. El mismo catálogo está en Telegram y en un panel local.

## Texto

El texto va por OpenRouter. En **Cambiar modelo** se elige uno y se queda activo. Guarda el hilo de la conversación y **Borrar historial** lo vacía. En **Estado de servidor** sale lo que queda en Fal y en OpenRouter.

| Modelo | Para qué sirve |
| --- | --- |
| Dolphin Venice | Charla directa, con menos rechazos que un modelo comercial |
| Cydonia 24B | Escritura creativa: recuerda el hilo y se ciñe al prompt |
| Skyfall 36B | Relatos y roleplay, con más matices |
| Euryale 70B | Roleplay largo y personajes. Más lento y más caro |
| Lunaris 8B | Charla y rol ligero, cuando no hace falta un modelo grande |
| UnslopNemo 12B | Aventura y roleplay, con menos frases genéricas |
| MythoMax 13B | Descripciones y roleplay. El de contexto más corto |
| Magnum 72B | Prosa literaria y textos largos |
| Hermes 3 70B | Razonar, agentes y tareas generales |
| Hermes 3 405B | El mismo Hermes 3, con más capacidad y más coste |
| Hermes 4 405B | Razonamiento. El más pesado de la lista |
| Qwen3 Coder | Código, revisión y scripts. Este lleva filtros |
| Command R+ | Documentos largos, síntesis y arquitectura. Con filtros |

## Generación

El botón **Generación** abre seis vías:

- **Generar imagen.** Crea la foto a partir de un texto, con Flux.
- **Modificar imagen.** Parte de la foto que envías y conserva a la persona. La fuerza del cambio es 0,75.
- **Animar imagen.** Esa foto es el primer fotograma. Tú describes qué tiene que pasar. Usa MiniMax y, si falla, Kling.
- **Avatar hablando.** El texto se dice con tu voz y Kling mueve la cara. No deja la imagen quieta para pintar solo los labios.
- **Texto a voz.** Convierte una frase en audio.
- **Mi voz.** Le mandas notas de voz. Las junta como muestra, de unos 6 a 30 segundos, y copia esa voz. No entrena un modelo en la Pi.

El modelo de imagen y el de vídeo se pueden cambiar en la configuración, sin tocar el código.

## Cómo está hecho

Un solo proceso, `bot.py`.

| Parte | Dónde corre |
| --- | --- |
| Chat | OpenRouter |
| Imagen, vídeo y avatar | Fal |
| Voz | Replicate, modelo XTTS |
| Panel web | Flask, puerto 5000 |
| Servicio | systemd, unidad `bot-ia` |

La Pi tiene poca memoria. Por eso no hay SDKs pesados ni un entrenamiento local: las llamadas van por HTTP y la voz es una muestra, no un modelo nuevo.

Para cambiar el modelo de imagen o de vídeo, en `.env`:

- `CUSTOM_IMAGE_MODEL`
- `CUSTOM_VIDEO_MODEL`
- `CUSTOM_VIDEO_FALLBACK_MODEL`

Si esas líneas están vacías, usa Flux, MiniMax y Kling.

## Puesta en marcha

1. Copia `.env.example` a `.env` y rellena las claves.
2. En la Raspberry, el servicio `bot-ia` arranca `bot.py` con ese `.env`.
3. `deploy.sh` copia el código a la Pi. La IP y el usuario están en el script. La contraseña no está en el repositorio: se exporta `PI_PASSWORD` antes de lanzarlo.

Las claves, el token de Telegram y la contraseña del panel no van en este repositorio.
