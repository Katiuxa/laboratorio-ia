# Laboratorio IA

Un bot de Telegram en una Raspberry Pi. El mismo sitio sirve para hablar con un modelo de texto y para generar imagen, vídeo y voz. El mismo catálogo está en Telegram y en un panel local.

## Texto

El texto va por OpenRouter. En **Cambiar modelo** se elige uno y se queda activo. La lista está en el bot y va cambiando. Guarda el hilo de la conversación y **Borrar historial** lo vacía. En **Estado de servidor** sale lo que queda en Fal y en OpenRouter.

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
