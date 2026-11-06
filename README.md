# Laboratorio IA

Un bot de Telegram, en una Raspberry Pi, para hablar con un modelo y generar imagen, vídeo y voz desde el mismo chat.

## Qué puedes hacer

Desde el botón **Generación**:

- **Generar imagen.** Crea una imagen a partir de un texto, con Flux.
- **Modificar imagen.** Cambia la foto que envías y conserva a la persona. La fuerza del cambio es 0,75.
- **Animar imagen.** La foto es el primer fotograma. Tú describes qué tiene que pasar.
- **Avatar hablando.** Escribes un texto, el bot lo dice con tu voz y Kling mueve a la persona de la foto. No deja la imagen quieta para pintar solo los labios.
- **Texto a voz.** Convierte cualquier texto en audio.
- **Mi voz.** Le mandas notas de voz. Las junta como muestra (unos 6 segundos, hasta 30). No entrena un modelo en la Pi: la voz se copia de esos audios.

El resto del teclado sigue siendo el chat: cambiar el modelo de texto, borrar el historial y ver el estado del servidor. Ahí abajo sale lo que queda en Fal y en OpenRouter.

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

Para cambiar el modelo de imagen o de vídeo sin tocar el código, en `.env`:

- `CUSTOM_IMAGE_MODEL`
- `CUSTOM_VIDEO_MODEL`
- `CUSTOM_VIDEO_FALLBACK_MODEL`

Si esas líneas están vacías, usa Flux, MiniMax y Kling.

## Puesta en marcha

1. Copia `.env.example` a `.env` y rellena las claves.
2. En la Raspberry, el servicio `bot-ia` arranca `bot.py` con ese `.env`.
3. `deploy.sh` copia el código a la Pi. La IP y el usuario están en el script. La contraseña no está en el repositorio: se exporta `PI_PASSWORD` antes de lanzarlo.

Las claves, el token de Telegram y la contraseña del panel no van en este repositorio.
