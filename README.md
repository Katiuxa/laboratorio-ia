# AI Lab

A Telegram bot on a Raspberry Pi. The same place is used to talk to a text model and to generate images, video, and voice. The same catalog is available on Telegram and on a local panel.

## Text

Text goes through OpenRouter. **Change model** picks one and leaves it active. The list lives in the bot and changes over time. It keeps the conversation thread, and **Clear history** empties it. **Server status** shows what remains on Fal and on OpenRouter.

## Generation

The **Generation** button opens six paths:

- **Generate image.** Creates the picture from a prompt, using Flux.
- **Edit image.** Starts from the photo you send and keeps the person. The strength of the change is 0.75.
- **Animate image.** That photo is the first frame. You describe what should happen. It uses MiniMax and, if that fails, Kling.
- **Talking avatar.** The text is spoken in your voice and Kling moves the face. It does not leave the image still just to paint the lips.
- **Text to speech.** Turns a sentence into audio.
- **My voice.** You send voice notes. It joins them into a sample, about 6 to 30 seconds, and copies that voice. It does not train a model on the Pi.

The image model and the video model can be changed in the configuration, without touching the code.

## How it is built

A single process, `bot.py`.

| Part | Where it runs |
| --- | --- |
| Chat | OpenRouter |
| Image, video, and avatar | Fal |
| Voice | Replicate, XTTS model |
| Web panel | Flask, port 5000 |
| Service | systemd, unit `bot-ia` |

The Pi has little memory. That is why there are no heavy SDKs and no local training: calls go over HTTP, and the voice is a sample, not a new model.

To change the image or video model, set these in `.env`:

- `CUSTOM_IMAGE_MODEL`
- `CUSTOM_VIDEO_MODEL`
- `CUSTOM_VIDEO_FALLBACK_MODEL`

If those lines are empty, it uses Flux, MiniMax, and Kling.

## Getting started

1. Copy `.env.example` to `.env` and fill in the keys.
2. On the Raspberry Pi, the `bot-ia` service starts `bot.py` with that `.env`.
3. `deploy.sh` copies the code to the Pi. The IP and the user are in the script. The password is not in the repository: export `PI_PASSWORD` before running it.

The keys, the Telegram token, and the panel password are not in this repository.
