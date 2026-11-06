"""Bot de Telegram y panel web: texto e imágenes por OpenRouter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, session, stream_with_context
from flask_cors import CORS
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

def _load_env_file() -> None:
    path = Path(__file__).resolve().with_name(".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

MODELOS: list[dict[str, str]] = [
    {
        "nombre": "Dolphin Venice",
        "id": "cognitivecomputations/dolphin-mistral-24b-venice-edition",
        "descripcion": "Sin censura (Venice, 24B). Charla general directa, con menos rechazos que un modelo comercial.",
    },
    {
        "nombre": "Cydonia 24B",
        "id": "thedrummer/cydonia-24b-v4.1",
        "descripcion": "Sin censura. Escritura creativa: recuerda el hilo y se ciñe al prompt.",
    },
    {
        "nombre": "Skyfall 36B",
        "id": "thedrummer/skyfall-36b-v2",
        "descripcion": "Relatos y roleplay. Prosa con más matices y menos respuestas planas.",
    },
    {
        "nombre": "Euryale 70B",
        "id": "sao10k/l3.1-euryale-70b",
        "descripcion": "Roleplay largo y personajes. Modelo grande: más lento y más caro.",
    },
    {
        "nombre": "Lunaris 8B",
        "id": "sao10k/l3-lunaris-8b",
        "descripcion": "Pequeño y rápido. Charla y rol ligero cuando no hace falta un modelo grande.",
    },
    {
        "nombre": "UnslopNemo 12B",
        "id": "thedrummer/unslopnemo-12b",
        "descripcion": "Aventura y roleplay. Menos frases genéricas que un chat normal.",
    },
    {
        "nombre": "MythoMax 13B",
        "id": "gryphe/mythomax-l2-13b",
        "descripcion": "Clásico de descripciones ricas y roleplay. El más corto de contexto de la lista.",
    },
    {
        "nombre": "Magnum 72B",
        "id": "anthracite-org/magnum-v4-72b",
        "descripcion": "Prosa literaria. Textos largos cuidados, al estilo de un modelo de escritura.",
    },
    {
        "nombre": "Hermes 3 70B",
        "id": "nousresearch/hermes-3-llama-3.1-70b",
        "descripcion": "Razonar, agentes y tareas generales. Menos estricto que Llama oficial.",
    },
    {
        "nombre": "Hermes 3 405B",
        "id": "nousresearch/hermes-3-llama-3.1-405b",
        "descripcion": "El mismo Hermes 3, a 405B. Más capacidad y más coste.",
    },
    {
        "nombre": "Hermes 4 405B",
        "id": "nousresearch/hermes-4-405b",
        "descripcion": "Razonamiento híbrido de Nous sobre Llama 405B. El más pesado de la lista.",
    },
    {
        "nombre": "Qwen3 Coder",
        "id": "qwen/qwen3-coder-30b-a3b-instruct",
        "descripcion": "Código, revisión de lógica y scripts. Este sí lleva filtros.",
    },
    {
        "nombre": "Command R+",
        "id": "cohere/command-r-plus-08-2024",
        "descripcion": "Documentos largos, síntesis y arquitectura. Con filtros.",
    },
]
MODELS: tuple[str, ...] = tuple(item["id"] for item in MODELOS)
_from_env = os.environ.get("MODEL_ID", MODELS[0])
MODEL_ID = _from_env if _from_env in MODELS else MODELS[0]

API_KEY = os.environ.get("API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
FAL_KEY = os.environ.get("FAL_KEY", "").strip()
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "").strip()
VOICE_REF_URL = os.environ.get("VOICE_REF_URL", "https://filebin.net/jzdfd280xpdmn4v1").strip()
def _env_model(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


IMAGE_MODEL = _env_model("CUSTOM_IMAGE_MODEL", "IMAGE_MODEL", default="fal-ai/flux/dev")
# URL directa de un LoRA de Flux (.safetensors). Vacía: no se envía el campo.
LORA_URL = ""
# Image-to-image: strength alto aplica el prompt y conserva la pose.
IMAGE_EDIT_MODEL = "fal-ai/flux/dev/image-to-image"
EDIT_STRENGTH = 0.75
VIDEO_MODEL = _env_model(
    "CUSTOM_VIDEO_MODEL",
    "VIDEO_MODEL",
    default="minimax/h3-max/image-to-video",
)
VIDEO_FALLBACK_MODEL = _env_model(
    "CUSTOM_VIDEO_FALLBACK_MODEL",
    "VIDEO_FALLBACK_MODEL",
    default="fal-ai/kling-video/v3/standard/image-to-video",
)
# Foto + voz en un solo vídeo. No es el lipsync que deja la imagen quieta.
AVATAR_MODEL = "fal-ai/kling-video/ai-avatar/v2/pro"
LIPSYNC_MODEL = "fal-ai/latentsync"
XTTS_MODEL = "lucataco/xtts-v2"
IMAGE_SIZE = "portrait_4_3"
VIDEO_SECONDS = 5
# Precio publicado por Replicate para lucataco/xtts-v2: unos 0,0096 USD por voz.
AUDIO_RUN_USD = 0.0096
_AUDIO_LEDGER = Path(__file__).resolve().parent / "audio_gasto.json"
_VOICE_DIR = Path(__file__).resolve().parent / "voz_usuario"
_VOICE_META = _VOICE_DIR / "voz.json"
VOICE_MIN_SECONDS = 6.0
VOICE_MAX_SECONDS = 30.0
_audio_lock = threading.Lock()
_voice_lock = threading.Lock()
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))
_STATIC = Path(__file__).resolve().parent / "static"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    (
        "Eres un Asistente Técnico de Diagnóstico y Control de Calidad de Software. "
        "Tu función es ayudar en la revisión de código, identificar cuellos de botella "
        "y proponer arquitecturas eficientes para la administración de servidores y bases de datos. "
        "Mantén un enfoque analítico y proporciona los scripts de corrección necesarios. "
        "No des exploits, payloads ni pasos para atacar sistemas."
    ),
)
ALLOWED_TELEGRAM_ID = int(os.environ.get("ALLOWED_TELEGRAM_ID", "39624065"))

MAX_HISTORY_MESSAGES = 20
TELEGRAM_CHUNK = 4000
HTTP_TIMEOUT = 180
MAX_NEW_TOKENS = 1024
MAX_IMAGE_BYTES = 8_000_000

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot-ia")

_state_lock = threading.Lock()
_model_id = MODEL_ID
_histories: dict[int, list[dict[str, str]]] = defaultdict(list)
_web_history: list[dict[str, str]] = []
_pending_photo: dict[int, dict[str, object]] = {}
_flow: dict[int, str] = {}
_avatar_look: dict[int, str] = {}
_avatar_image: dict[int, bytes] = {}
_media_locks: dict[int, asyncio.Lock] = {}
_media_guard = threading.Lock()
_llm: OpenAI | None = None

MENU_MODEL = "🧠 Cambiar Modelo"
MENU_CLEAR = "🧹 Borrar Historial"
MENU_STATUS = "⚙️ Estado de Servidor"
MENU_IMAGE = "🎛 Generación"
MENU_IMAGE_OLD = "🖼 Imagen"
CB_GEN = "img:gen"
CB_ANIM = "img:anim"
CB_AVATAR = "img:avatar"
CB_EDIT = "img:edit"
CB_TTS = "img:tts"
CB_VOICE = "img:voice"
CB_CANCEL = "img:cancel"
DEFAULT_MOTION = (
    "Movimiento suave y natural. El sujeto cobra vida sin cambiar de identidad ni de encuadre."
)


def _openrouter() -> OpenAI:
    global _llm
    if _llm is None:
        _llm = OpenAI(
            api_key=API_KEY,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "http://localhost",
                "X-Title": "BotRaspberry",
            },
        )
    return _llm


def get_model() -> str:
    with _state_lock:
        return _model_id


def set_model(model_id: str) -> str:
    global _model_id
    with _state_lock:
        _model_id = model_id
        return _model_id


def _model_label(model_id: str) -> str:
    for item in MODELOS:
        if item["id"] == model_id:
            return item["nombre"]
    return model_id


def _catalog_public() -> list[dict[str, str]]:
    return [
        {"id": item["id"], "nombre": item["nombre"], "descripcion": item["descripcion"]}
        for item in MODELOS
    ]


def _model_menu_text() -> str:
    current = get_model()
    lines = ["Elige un modelo. El activo lleva ✅.", ""]
    for index, item in enumerate(MODELOS, start=1):
        mark = "✅ " if item["id"] == current else ""
        lines.append(f"{mark}{index}. {item['nombre']}")
        lines.append(item["descripcion"])
        lines.append("")
    lines.append("Este menú solo cambia el texto. Imagen y voz van dentro de Generación.")
    return "\n".join(lines).strip()


def _model_choice() -> InlineKeyboardMarkup:
    current = get_model()
    rows = []
    for index, item in enumerate(MODELOS):
        prefix = "✅ " if item["id"] == current else ""
        rows.append([
            InlineKeyboardButton(f"{prefix}{item['nombre']}", callback_data=f"mdl:{index}")
        ])
    return InlineKeyboardMarkup(rows)


def _keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[MENU_MODEL, MENU_CLEAR], [MENU_STATUS, MENU_IMAGE]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _image_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🖼️ Generar Imagen", callback_data=CB_GEN)],
            [InlineKeyboardButton("🎥 Animar Imagen", callback_data=CB_ANIM)],
            [InlineKeyboardButton("🗣️ Avatar Hablando", callback_data=CB_AVATAR)],
            [InlineKeyboardButton("🖌️ Modificar Imagen", callback_data=CB_EDIT)],
            [InlineKeyboardButton("🔊 Texto a voz", callback_data=CB_TTS)],
            [InlineKeyboardButton("🎙️ Mi voz", callback_data=CB_VOICE)],
            [InlineKeyboardButton("🧹 Cancelar", callback_data=CB_CANCEL)],
        ]
    )


def _clear_flow(user_id: int) -> None:
    _flow.pop(user_id, None)
    _avatar_look.pop(user_id, None)
    _avatar_image.pop(user_id, None)
    _pending_photo.pop(user_id, None)


def _media_lock(user_id: int) -> asyncio.Lock:
    with _media_guard:
        lock = _media_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _media_locks[user_id] = lock
        return lock


def _is_allowed(user_id: int | None) -> bool:
    return user_id is not None and user_id == ALLOWED_TELEGRAM_ID


def _trim(history: list[dict[str, str]]) -> None:
    extra = len(history) - MAX_HISTORY_MESSAGES
    if extra > 0:
        del history[:extra]


def _chunks(text: str, limit: int = TELEGRAM_CHUNK) -> list[str]:
    if len(text) <= limit:
        return [text or " "]
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n ")
    return [part for part in parts if part]


def _ask(history: list[dict[str, str]]) -> str:
    response = _openrouter().chat.completions.create(
        model=get_model(),
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
        max_tokens=MAX_NEW_TOKENS,
        temperature=0.4,
    )
    return (response.choices[0].message.content or "").strip()


def _probe_status() -> int:
    if not API_KEY:
        return 0
    try:
        response = requests.get(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=20,
        )
    except requests.RequestException:
        return 0
    return response.status_code


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(amount: float) -> str:
    if amount == 0 or abs(amount) >= 0.01:
        decimals = 2
    else:
        decimals = 4
    return f"{amount:.{decimals}f} USD".replace(".", ",")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _openrouter_data(path: str) -> dict | None:
    if not API_KEY:
        return None
    try:
        response = requests.get(
            f"{OPENROUTER_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=20,
        )
    except requests.RequestException:
        log.warning("No pude consultar el gasto de OpenRouter")
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def spend_summary() -> dict[str, float | None]:
    """Gasto en USD. El total de la cuenta tiene prioridad; si no, el de esta clave."""
    credits = _openrouter_data("/credits")
    key = _openrouter_data("/key")
    total = left = None
    if credits is not None:
        used = _as_float(credits.get("total_usage"))
        bought = _as_float(credits.get("total_credits"))
        if used is not None:
            total = used
            if bought is not None:
                left = bought - used
    today = month = None
    if key is not None:
        if total is None:
            total = _as_float(key.get("usage"))
        today = _as_float(key.get("usage_daily"))
        month = _as_float(key.get("usage_monthly"))
    return {"total": total, "today": today, "month": month, "left": left}


def _secrets() -> list[str]:
    found = []
    for value in (API_KEY, TELEGRAM_TOKEN, FAL_KEY, REPLICATE_API_TOKEN):
        if value and len(value) >= 6:
            found.append(value)
    return found


def _public_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    for secret in _secrets():
        text = text.replace(secret, "***")
    low = text.lower()
    policy = any(
        marker in low
        for marker in ("content policy", "content_policy", "policy violation", "política de contenido")
    )
    if policy:
        code = _http_code(text)
        if code:
            return f"Fal ha rechazado el trabajo por su política de contenido (HTTP {code})."
        return "Fal ha rechazado el trabajo por su política de contenido."
    return (text or exc.__class__.__name__)[:500]


def _http_code(text: str) -> int:
    match = re.search(r"HTTP\s+(\d{3})|\bFal\s+(\d{3})\b", text)
    if not match:
        return 0
    return int(match.group(1) or match.group(2) or 0)


def _fal_reject(code: int, raw: str) -> RuntimeError:
    body = " ".join((raw or "").split())
    low = body.lower()
    policy = any(
        marker in low
        for marker in ("content policy", "content_policy", "policy violation", "safety checker", "nsfw")
    )
    if policy:
        if code:
            return RuntimeError(f"Fal ha rechazado el trabajo por su política de contenido (HTTP {code}).")
        return RuntimeError("Fal ha rechazado el trabajo por su política de contenido.")
    if code and body:
        return RuntimeError(f"Fal ha rechazado el trabajo (HTTP {code}). {body[:180]}")
    if code:
        return RuntimeError(f"Fal ha rechazado el trabajo (HTTP {code}).")
    return RuntimeError(f"Fal falló: {body[:180] or 'error'}")


class ImageApiError(RuntimeError):
    """La API rechazó la imagen. El texto ya es el aviso de Telegram."""


IMAGE_API_ERROR = "⚠️ Error de la API al generar la imagen."


def _require_fal() -> None:
    if not FAL_KEY:
        raise RuntimeError("Falta FAL_KEY en el .env del bot.")


def _require_replicate() -> None:
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("Falta REPLICATE_API_TOKEN en el .env del bot.")


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise RuntimeError("No he recibido texto.")
    if len(cleaned) > limit:
        raise RuntimeError(f"El texto supera los {limit} caracteres. Acórtalo y vuelve a enviarlo.")
    return cleaned


def _unwrap_fal(result: dict) -> dict:
    for key in ("response", "data", "payload"):
        nested = result.get(key)
        if isinstance(nested, dict) and any(name in nested for name in ("images", "video")):
            return nested
    return result


def _image_url_from(result: dict) -> str:
    payload = _unwrap_fal(result)
    images = payload.get("images") or []
    if images and isinstance(images[0], dict):
        url = images[0].get("url")
        if isinstance(url, str) and url.startswith("http"):
            if any(payload.get("has_nsfw_concepts") or []):
                log.info("Fal marcó la imagen y había archivo; la entrego")
            return url
    if any(payload.get("has_nsfw_concepts") or []):
        raise RuntimeError("Fal bloqueó la imagen con el filtro de seguridad.")
    raise RuntimeError("Fal no devolvió la URL de la imagen.")


def _video_url_from(result: dict) -> str:
    payload = _unwrap_fal(result)
    video = payload.get("video")
    if isinstance(video, dict):
        url = video.get("url")
        if isinstance(url, str) and url.startswith("http"):
            return url
    raise RuntimeError("Fal no devolvió la URL del vídeo.")


def _queue_app(model: str) -> str:
    parts = [part for part in model.split("/") if part]
    if len(parts) < 2:
        return model.strip("/")
    return f"{parts[0]}/{parts[1]}"


def _queue_url(url: str, model: str, request_id: str, *, status: bool) -> str:
    """La cola solo conoce owner/app. La ruta larga del modelo responde 404."""
    app = _queue_app(model)
    suffix = "/status" if status else ""
    canonical = f"https://queue.fal.run/{app}/requests/{request_id}{suffix}"
    if not url or not request_id or "/requests/" not in url:
        return canonical
    head = urlparse(url).path.split("/requests/", 1)[0].strip("/")
    if head == app:
        return url
    return canonical


def fal_run(model: str, arguments: dict, timeout: int) -> dict:
    """Cola REST de Fal. Un POST y luego polling; no se relanza el trabajo."""
    _require_fal()
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    try:
        submit = requests.post(
            f"https://queue.fal.run/{model}",
            headers=headers,
            json=arguments,
            timeout=120,
        )
        submit.raise_for_status()
    except requests.HTTPError as exc:
        response = exc.response
        code = response.status_code if response is not None else 0
        raw = response.text if response is not None else ""
        raise _fal_reject(code, raw) from exc
    except requests.RequestException as exc:
        raise RuntimeError("Fal no respondió al enviar el trabajo.") from exc
    body = submit.json()
    if isinstance(body, dict) and any(key in body for key in ("images", "video")):
        return body
    request_id = str(body.get("request_id") or "").strip() if isinstance(body, dict) else ""
    if not request_id:
        raise RuntimeError("Fal no devolvió el seguimiento del trabajo.")
    status_url = _queue_url(
        str(body.get("status_url") or "") if isinstance(body, dict) else "",
        model,
        request_id,
        status=True,
    )
    response_url = _queue_url(
        str(body.get("response_url") or "") if isinstance(body, dict) else "",
        model,
        request_id,
        status=False,
    )
    if not status_url or not response_url:
        raise RuntimeError("Fal no devolvió el seguimiento del trabajo.")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = requests.get(status_url, headers=headers, timeout=30)
        except requests.RequestException:
            time.sleep(3)
            continue
        if status.status_code >= 500:
            time.sleep(3)
            continue
        if status.status_code == 404 and "NOT_FOUND" in status.text:
            raise RuntimeError(
                "Fal no tiene ese trabajo. La clave puede leer el saldo, "
                "pero no ejecutar el modelo: en fal.ai/dashboard/keys activa "
                "models:requests:submit."
            )
        if status.status_code >= 400:
            raise _fal_reject(status.status_code, status.text)
        try:
            payload = status.json()
        except ValueError:
            time.sleep(3)
            continue
        state = str(payload.get("status") or "").upper()
        if payload.get("error") or state in {"FAILED", "ERROR"}:
            detail = str(payload.get("error") or state)
            raise _fal_reject(0, detail)
        if state in {"COMPLETED", "SUCCEEDED"}:
            try:
                result = requests.get(response_url, headers=headers, timeout=60)
            except requests.RequestException:
                time.sleep(3)
                continue
            if result.status_code >= 500:
                time.sleep(3)
                continue
            if result.status_code >= 400:
                raise _fal_reject(result.status_code, result.text)
            data = result.json()
            return data if isinstance(data, dict) else {"raw": data}
        time.sleep(3)
    raise RuntimeError(f"{model} superó el tiempo de espera. No lo relanzo para no cobrarlo dos veces.")


def image_data_uri(data: bytes, content_type: str = "") -> str:
    """Data URI para image_url. La foto no pasa por un hosting de terceros."""
    if not data:
        raise RuntimeError("La imagen está vacía.")
    mime = content_type if content_type.startswith("image/") else ""
    if not mime:
        if data.startswith(b"\x89PNG"):
            mime = "image/png"
        elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            mime = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def resolve_speaker(url: str) -> str:
    _require_replicate()
    cleaned = url.strip()
    path = urlparse(cleaned).path.lower()
    if path.endswith((".wav", ".mp3", ".m4a", ".ogg", ".flac")):
        return cleaned
    host = urlparse(cleaned).netloc.lower()
    parts = [part for part in urlparse(cleaned).path.split("/") if part]
    if "filebin.net" not in host or not parts:
        raise RuntimeError(
            "VOICE_REF_URL tiene que ser el enlace directo a un wav o mp3 de al menos 6 segundos."
        )
    response = requests.get(
        f"https://filebin.net/{parts[0]}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError("No pude leer el filebin de la voz de referencia.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("El filebin de la voz no devolvió la lista de archivos.") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        files = []
    for item in files:
        if not isinstance(item, dict):
            continue
        name = str(item.get("filename") or "")
        if name.lower().endswith((".wav", ".mp3", ".m4a", ".ogg", ".flac")):
            return f"https://filebin.net/{parts[0]}/{quote(name)}"
    raise RuntimeError(
        "En VOICE_REF_URL no hay un audio directo. Sube un wav o mp3 de al menos 6 segundos."
    )


def _xtts_version(headers: dict[str, str]) -> str:
    response = requests.get(
        f"https://api.replicate.com/v1/models/{XTTS_MODEL}",
        headers=headers,
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Replicate {response.status_code}: {response.text[:300]}")
    version = str(((response.json() or {}).get("latest_version") or {}).get("id") or "").strip()
    if not version:
        raise RuntimeError("Replicate no publicó la versión de la voz.")
    return version


def replicate_speech(text: str, speaker: str) -> str:
    _require_replicate()
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    created = requests.post(
        "https://api.replicate.com/v1/predictions",
        headers=headers,
        json={
            "version": _xtts_version(headers),
            "input": {"text": text, "speaker": speaker, "language": "es"},
        },
        timeout=30,
    )
    if created.status_code == 429:
        raise RuntimeError(
            "Replicate ha limitado la voz por poco crédito. "
            "Recarga en replicate.com o espera un minuto y reinténtalo."
        )
    if created.status_code >= 400:
        raise RuntimeError(f"Replicate {created.status_code}: {created.text[:300]}")
    body = created.json()
    deadline = time.time() + 300
    while time.time() < deadline:
        status = str(body.get("status") or "").lower()
        if status == "succeeded":
            output = body.get("output")
            if isinstance(output, list) and output:
                output = output[0]
            if isinstance(output, str) and output.startswith("http"):
                _record_audio_spend()
                return output
            raise RuntimeError("Replicate no devolvió el audio.")
        if status in {"failed", "canceled"}:
            raise RuntimeError(f"Replicate falló: {str(body.get('error') or status)[:300]}")
        follow = (body.get("urls") or {}).get("get")
        if not follow:
            raise RuntimeError("Replicate no devolvió el seguimiento del audio.")
        time.sleep(3)
        try:
            polled = requests.get(follow, headers=headers, timeout=30)
        except requests.RequestException:
            continue
        if polled.status_code >= 500:
            continue
        if polled.status_code >= 400:
            raise RuntimeError(f"Replicate {polled.status_code}: {polled.text[:300]}")
        try:
            body = polled.json()
        except ValueError:
            continue
    raise RuntimeError("La voz tardó demasiado. No la relanzo para no cobrarla dos veces.")


def _voice_meta() -> dict:
    if not _VOICE_META.is_file():
        return {}
    try:
        data = json.loads(_VOICE_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_voice_meta(payload: dict) -> None:
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    _VOICE_META.write_text(json.dumps(payload), encoding="utf-8")


def _fal_upload(data: bytes, name: str, content_type: str) -> str:
    _require_fal()
    started = requests.post(
        "https://rest.alpha.fal.ai/storage/upload/initiate",
        params={"storage_type": "fal-cdn-v3"},
        headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
        json={"file_name": name, "content_type": content_type},
        timeout=30,
    )
    if started.status_code >= 400:
        raise RuntimeError("No pude preparar la subida del audio.")
    body = started.json()
    upload_url = str(body.get("upload_url") or "")
    file_url = str(body.get("file_url") or "")
    if not upload_url.startswith("http") or not file_url.startswith("http"):
        raise RuntimeError("Fal no dio una dirección para el audio.")
    put = requests.put(upload_url, data=data, headers={"Content-Type": content_type}, timeout=120)
    if put.status_code >= 400:
        raise RuntimeError("No pude subir el audio.")
    return file_url


def _audio_seconds(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if probe:
        done = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            return max(0.0, float((done.stdout or "").strip()))
        except ValueError:
            pass
    if path.suffix.lower() == ".wav":
        size = path.stat().st_size
        if size > 44:
            return (size - 44) / (22050 * 2)
    return 0.0


def _store_voice_clip(data: bytes, suffix: str) -> float:
    """Junta los audios del usuario y publica la muestra. No entrena un modelo."""
    if not data:
        raise RuntimeError("El audio está vacío.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Falta FFmpeg para preparar la voz.")
    with _voice_lock:
        clips = _VOICE_DIR / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        numbers = []
        for item in clips.glob("clip-*"):
            piece = item.stem.rsplit("-", 1)[-1]
            if piece.isdigit():
                numbers.append(int(piece))
        raw = clips / f"clip-{max(numbers, default=0) + 1:03d}{suffix}"
        raw.write_bytes(data)
        wavs: list[Path] = []
        sources = sorted(
            item for item in clips.iterdir()
            if item.is_file() and item.suffix.lower() in {".ogg", ".mp3", ".m4a", ".flac", ".wav"}
        )
        for source in sources:
            if source.suffix.lower() != ".wav" and source.with_suffix(".wav").exists() and source.with_suffix(".wav") != source:
                continue
            target = source if source.suffix.lower() == ".wav" else source.with_suffix(".wav")
            if source.suffix.lower() != ".wav":
                done = subprocess.run(
                    [ffmpeg, "-y", "-i", str(source), "-ar", "22050", "-ac", "1", str(target)],
                    capture_output=True,
                    timeout=90,
                    check=False,
                )
                if done.returncode != 0 or not target.is_file():
                    log.warning("ffmpeg no convirtió un audio: %s", done.stderr[-180:])
                    continue
            if target.is_file():
                wavs.append(target)
        ordered: list[Path] = []
        seen: set[Path] = set()
        for wav in wavs:
            if wav not in seen:
                seen.add(wav)
                ordered.append(wav)
        measured = [(wav, _audio_seconds(wav)) for wav in ordered]
        total = sum(seconds for _wav, seconds in measured)
        while total > VOICE_MAX_SECONDS and len(measured) > 1:
            oldest, seconds = measured.pop(0)
            total -= seconds
            for extra in oldest.parent.glob(oldest.stem + ".*"):
                extra.unlink(missing_ok=True)
        if not measured:
            raise RuntimeError("No pude leer el audio.")
        list_file = _VOICE_DIR / "lista.txt"
        lines = []
        for wav, _seconds in measured:
            escaped = wav.as_posix().replace("'", "'\\''")
            lines.append(f"file '{escaped}'\n")
        list_file.write_text("".join(lines), encoding="utf-8")
        combined = _VOICE_DIR / "voz.wav"
        merged = subprocess.run(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-t", str(int(VOICE_MAX_SECONDS)), "-ar", "22050", "-ac", "1", str(combined),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if merged.returncode != 0 or not combined.is_file():
            log.warning("ffmpeg no juntó la voz: %s", merged.stderr[-180:])
            raise RuntimeError("No pude juntar los audios.")
        seconds = _audio_seconds(combined)
        url = _fal_upload(combined.read_bytes(), "voz.wav", "audio/wav")
        _save_voice_meta({"url": url, "seconds": round(seconds, 1), "clips": len(measured)})
        return seconds


def _speaker_url() -> str:
    meta = _voice_meta()
    url = str(meta.get("url") or "").strip()
    seconds = meta.get("seconds")
    if url.startswith("http"):
        try:
            heard = float(seconds)
        except (TypeError, ValueError):
            heard = VOICE_MIN_SECONDS
        if heard < VOICE_MIN_SECONDS:
            raise RuntimeError(
                f"Tu voz dura {str(round(heard, 1)).replace('.', ',')} s. "
                "Manda otro audio en Mi voz hasta unos 6 s."
            )
        return url
    return resolve_speaker(VOICE_REF_URL)


def _speak(script: str) -> str:
    return replicate_speech(script, _speaker_url())


def _flux_payload(prompt: str) -> dict:
    payload = {
        "prompt": prompt,
        "image_size": IMAGE_SIZE,
        "num_images": 1,
        "output_format": "jpeg",
        "enable_safety_checker": False,
    }
    lora = LORA_URL.strip()
    if lora:
        payload["loras"] = [{"path": lora, "scale": 1.0}]
    return payload


def generate_image_url(prompt: str) -> str:
    try:
        result = fal_run(IMAGE_MODEL, _flux_payload(prompt), HTTP_TIMEOUT)
        return _image_url_from(result)
    except ImageApiError:
        raise
    except Exception as exc:
        log.warning("Fal no generó la imagen: %s", _public_error(exc))
        raise ImageApiError(IMAGE_API_ERROR) from exc


def _edit_prompt(prompt: str) -> str:
    text = prompt.strip()
    return (
        f"{text}. Keep the same person and the same pose from the input photo. "
        "Apply this change clearly."
    )


def edit_image_url(image_url: str, prompt: str) -> str:
    payload = {
        "image_url": image_url,
        "prompt": _edit_prompt(prompt),
        "strength": EDIT_STRENGTH,
        "num_images": 1,
        "output_format": "jpeg",
        "enable_safety_checker": True,
    }
    lora = LORA_URL.strip()
    if lora:
        payload["loras"] = [{"path": lora, "scale": 1.0}]
    result = fal_run(IMAGE_EDIT_MODEL, payload, HTTP_TIMEOUT)
    return _image_url_from(result)


def _motion_prompt(prompt: str) -> str:
    text = prompt.strip() or DEFAULT_MOTION
    return (
        f"{text}. Start from the input image and keep the same person. "
        "Follow the description for the action."
    )


def animate_image_url(image_url: str, prompt: str) -> str:
    motion = _motion_prompt(prompt)
    try:
        result = fal_run(
            VIDEO_MODEL,
            {
                "prompt": motion,
                "image_url": image_url,
                "duration": float(VIDEO_SECONDS),
                "resolution": "768P",
                "prompt_expansion_mode": "disabled",
            },
            900,
        )
        return _video_url_from(result)
    except RuntimeError as exc:
        message = str(exc)
        if "política de contenido" in message:
            raise
        if not any(f"HTTP {code}" in message or f"Fal {code}" in message for code in ("400", "401", "403", "404", "422")):
            raise
        log.warning("MiniMax no arrancó, pruebo Kling: %s", _public_error(exc))
    result = fal_run(
        VIDEO_FALLBACK_MODEL,
        {
            "prompt": motion,
            "start_image_url": image_url,
            "duration": str(VIDEO_SECONDS),
            "generate_audio": False,
        },
        900,
    )
    return _video_url_from(result)


def talking_avatar_url(image_url: str, audio_url: str) -> str:
    result = fal_run(
        AVATAR_MODEL,
        {
            "image_url": image_url,
            "audio_url": audio_url,
            "prompt": (
                "The same person speaks naturally to the camera. "
                "Real head movement, expressions and lip sync. Keep their identity."
            ),
        },
        900,
    )
    return _video_url_from(result)


def lipsync_url(video_url: str, audio_url: str) -> str:
    result = fal_run(
        LIPSYNC_MODEL,
        {
            "video_url": video_url,
            "audio_url": audio_url,
            "guidance_scale": 1.5,
            "loop_mode": "loop",
        },
        900,
    )
    return _video_url_from(result)


def _spend_kind(endpoint: str) -> str:
    name = endpoint.lower()
    if name in {IMAGE_MODEL, IMAGE_EDIT_MODEL} or "flux" in name:
        return "image"
    if name in {VIDEO_MODEL, VIDEO_FALLBACK_MODEL, LIPSYNC_MODEL}:
        return "video"
    if any(token in name for token in ("video", "latentsync", "minimax", "kling")):
        return "video"
    return "other"


def _fal_json(path: str, params: dict[str, str]) -> tuple[int, dict | None]:
    if not FAL_KEY:
        return 0, None
    try:
        response = requests.get(
            f"https://api.fal.ai/v1/{path}",
            params=params,
            headers={"Authorization": f"Key {FAL_KEY}", "Accept": "application/json"},
            timeout=20,
        )
    except requests.RequestException:
        log.warning("Fal no respondió en %s", path)
        return 0, None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 400:
        log.warning("Fal %s respondió HTTP %s", path, response.status_code)
    return response.status_code, body if isinstance(body, dict) else None


def _usage_split(start: str, timeframe: str) -> dict[str, float] | None:
    code, body = _fal_json(
        "models/usage",
        {
            "expand": "summary",
            "start": start,
            "timezone": "Europe/Madrid",
            "timeframe": timeframe,
        },
    )
    if code != 200 or body is None:
        return None
    totals = {"image": 0.0, "video": 0.0, "other": 0.0}
    for row in body.get("summary") or []:
        if not isinstance(row, dict):
            continue
        cost = _as_float(row.get("cost_total"))
        if cost is None:
            continue
        totals[_spend_kind(str(row.get("endpoint_id") or ""))] += cost
    return totals


def _fal_balance() -> tuple[float | None, str]:
    if not FAL_KEY:
        return None, "falta FAL_KEY"
    code, body = _fal_json("account/billing", {"expand": "credits"})
    if code in {401, 403}:
        return None, "la clave no puede leer la factura"
    if code != 200 or body is None:
        return None, "no disponible"
    credits = body.get("credits") if isinstance(body.get("credits"), dict) else {}
    amount = _as_float(credits.get("current_balance"))
    if amount is None:
        return None, "no disponible"
    return amount, ""


def _audio_ledger() -> tuple[int, float]:
    with _audio_lock:
        if not _AUDIO_LEDGER.is_file():
            return 0, 0.0
        try:
            data = json.loads(_AUDIO_LEDGER.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0, 0.0
        count = data.get("count") if isinstance(data, dict) else 0
        usd = data.get("usd") if isinstance(data, dict) else 0
        try:
            return max(0, int(count)), max(0.0, float(usd))
        except (TypeError, ValueError):
            return 0, 0.0


def _record_audio_spend() -> None:
    with _audio_lock:
        count, usd = 0, 0.0
        if _AUDIO_LEDGER.is_file():
            try:
                data = json.loads(_AUDIO_LEDGER.read_text(encoding="utf-8"))
                count = int(data.get("count") or 0)
                usd = float(data.get("usd") or 0)
            except (OSError, ValueError, TypeError):
                count, usd = 0, 0.0
        payload = {"count": count + 1, "usd": round(usd + AUDIO_RUN_USD, 4)}
        _AUDIO_LEDGER.write_text(json.dumps(payload), encoding="utf-8")


def fal_done_caption() -> str:
    try:
        amount, _note = _fal_balance()
    except Exception:
        log.warning("Saldo Fal no disponible", exc_info=True)
        amount = None
    if amount is None:
        return "✅ Completado\n\nSaldo: No disponible"
    return f"✅ Completado\n\n💳 Saldo Fal\n{_money(amount)}"


def server_report() -> tuple[str, str, int]:
    code = _probe_status()
    summary = spend_summary()
    carried = _usage_split("2024-01-01", "month")
    try:
        balance, _note = _fal_balance()
    except Exception:
        log.warning("Saldo Fal no disponible", exc_info=True)
        balance = None
    _voices, audio_usd = _audio_ledger()
    text_spent = summary.get("total")
    text_left = summary.get("left")
    image = carried["image"] if carried else None
    video = carried["video"] if carried else None
    other = carried["other"] if carried else None

    def show(value: float | None) -> str:
        return "No disponible" if value is None else _money(value)

    known: list[float] = []
    for value in (text_spent, image, video, audio_usd):
        if isinstance(value, (int, float)):
            known.append(float(value))
    if isinstance(other, (int, float)) and other > 0:
        known.append(float(other))
    total = sum(known)

    if code == 200:
        server = "🟢 En línea"
    elif code == 0:
        server = "🔴 Sin conexión"
    else:
        server = f"🟠 HTTP {code}"
    model = _model_label(get_model())

    plain_rows = [
        "⚙️ Estado",
        "",
        server,
        model,
        "",
        "💬 Texto",
        "Gastado",
        show(text_spent if isinstance(text_spent, float) else None),
        "",
        "Queda",
        show(text_left if isinstance(text_left, float) else None),
        "",
        "🖼 Imagen",
        show(image),
        "",
        "🎬 Vídeo",
        show(video),
    ]
    if isinstance(other, (int, float)) and other > 0:
        plain_rows.extend(["", "🧩 Otros", _money(float(other))])
    plain_rows.extend([
        "",
        "🔊 Audio",
        _money(audio_usd),
        "",
        "📊 Total gastado",
        _money(total),
        "",
        "💳 Te queda",
        "",
        "Fal",
        show(balance),
        "",
        "OpenRouter",
        show(text_left if isinstance(text_left, float) else None),
    ])
    plain = "\n".join(plain_rows)

    def title(label: str) -> str:
        return f"<b>{label}</b>"

    html_rows = [
        title("⚙️ Estado"),
        "",
        server,
        _esc(model),
        "",
        title("💬 Texto"),
        "Gastado",
        show(text_spent if isinstance(text_spent, float) else None),
        "",
        "Queda",
        show(text_left if isinstance(text_left, float) else None),
        "",
        title("🖼 Imagen"),
        show(image),
        "",
        title("🎬 Vídeo"),
        show(video),
    ]
    if isinstance(other, (int, float)) and other > 0:
        html_rows.extend(["", title("🧩 Otros"), _money(float(other))])
    html_rows.extend([
        "",
        title("🔊 Audio"),
        _money(audio_usd),
        "",
        title("📊 Total gastado"),
        title(_money(total)),
        "",
        title("💳 Te queda"),
        "",
        title("Fal"),
        title(show(balance)),
        "",
        title("OpenRouter"),
        title(show(text_left if isinstance(text_left, float) else None)),
    ])
    return plain, "\n".join(html_rows), code


def generate_image(prompt: str) -> bytes:
    url = generate_image_url(prompt)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


def edit_image(image: bytes, prompt: str, content_type: str = "image/jpeg") -> bytes:
    mime = content_type if content_type.startswith("image/") else "image/jpeg"
    url = edit_image_url(image_data_uri(image, mime), prompt)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


async def _reply(update: Update, text: str, *, with_keyboard: bool = False) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _chunks(text)
    for index, part in enumerate(parts):
        markup = _keyboard() if with_keyboard and index == len(parts) - 1 else None
        await message.reply_text(part, reply_markup=markup)


async def _keep_typing(chat, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await chat.send_action(ChatAction.TYPING)
        except Exception:
            log.debug("No se pudo enviar el estado escribiendo", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except (TimeoutError, asyncio.TimeoutError):
            continue


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id if user else None):
        return
    await _reply(
        update,
        "Listo. Escríbeme o pulsa Generación: imagen, vídeo y voz. "
        "El modelo, el historial y el estado siguen en su sitio. "
        "/reset borra el historial.",
        with_keyboard=True,
    )


async def on_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        return
    _histories.pop(user.id, None)
    _clear_flow(user.id)
    await _reply(update, "Historial borrado.", with_keyboard=True)


def _photo_file(message) -> tuple[str, int | None] | None:
    if message is None:
        return None
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, photo.file_size
    document = message.document
    if document and (document.mime_type or "").startswith("image/"):
        return document.file_id, document.file_size
    return None


async def _download_photo(context: ContextTypes.DEFAULT_TYPE, message) -> bytes:
    found = _photo_file(message)
    if found is None:
        raise RuntimeError("No veo una imagen. Responde a una foto del chat o envíala ahora.")
    file_id, size = found
    if size and size > MAX_IMAGE_BYTES:
        raise RuntimeError("La imagen es demasiado grande.")
    telegram_file = await context.bot.get_file(file_id)
    data = bytes(await telegram_file.download_as_bytearray())
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError("La imagen es demasiado grande.")
    return data


async def _run_job(message, user_id: int, work) -> None:
    lock = _media_lock(user_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.05)
    except (TimeoutError, asyncio.TimeoutError):
        await message.reply_text(
            "Ya hay una generación en curso. Puedes seguir usando el chat, el modelo y el estado.",
            reply_markup=_keyboard(),
        )
        return
    status = await message.reply_text("⏳ Procesando...")
    try:
        await work(status)
    except Exception as exc:
        log.exception("Fallo de imagen user=%s", user_id)
        text = f"⚠️ No se pudo completar.\n{_public_error(exc)}"
        try:
            await status.edit_text(text)
        except Exception:
            await message.reply_text(text, reply_markup=_keyboard())
    finally:
        lock.release()


async def _edit_status(status, text: str) -> None:
    try:
        await status.edit_text(text)
    except Exception:
        log.debug("No pude actualizar el estado", exc_info=True)


async def _pulse(status, label: str, started: float) -> None:
    while True:
        await asyncio.sleep(15)
        elapsed = int(time.time() - started)
        await _edit_status(status, f"{label}\n{elapsed} s")


async def _call(status, label: str, func, *args):
    await _edit_status(status, label)
    started = time.time()
    pulse = asyncio.create_task(_pulse(status, label, started))
    try:
        return await asyncio.to_thread(func, *args)
    finally:
        pulse.cancel()
        try:
            await pulse
        except asyncio.CancelledError:
            pass


async def _safe_caption() -> str:
    try:
        return await asyncio.to_thread(fal_done_caption)
    except Exception:
        log.warning("No pude leer el saldo al cerrar el trabajo", exc_info=True)
        return "✅ Completado\n\nSaldo: No disponible"


async def _drop_status(status) -> None:
    try:
        await status.delete()
    except Exception:
        log.debug("No pude borrar el aviso de proceso", exc_info=True)


async def _send_photo_url(message, url: str, caption: str) -> None:
    try:
        await message.reply_photo(photo=url, caption=caption, reply_markup=_keyboard())
        return
    except Exception:
        log.exception("Telegram no aceptó la foto por URL")
    response = await asyncio.to_thread(requests.get, url, timeout=120)
    response.raise_for_status()
    await message.reply_photo(photo=response.content, caption=caption, reply_markup=_keyboard())


async def _send_video_url(message, url: str, caption: str) -> None:
    try:
        await message.reply_video(video=url, caption=caption, reply_markup=_keyboard(), supports_streaming=True)
        return
    except Exception:
        log.exception("Telegram no aceptó el vídeo por URL")
    await message.reply_text(f"{caption}\nEnlace directo:\n{url}", reply_markup=_keyboard())


async def _job_generate(message, user_id: int, prompt: str) -> None:
    async def work(status) -> None:
        try:
            url = await _call(status, "⏳ Paso 1: Procesando imagen...", generate_image_url, prompt)
        except ImageApiError:
            await _edit_status(status, IMAGE_API_ERROR)
            return
        caption = await _safe_caption()
        await _drop_status(status)
        await _send_photo_url(message, url, caption)

    await _run_job(message, user_id, work)


async def _job_animate(message, user_id: int, image: bytes, prompt: str) -> None:
    async def work(status) -> None:
        await _edit_status(status, "⏳ Paso 1: Procesando imagen...")
        public = image_data_uri(image)
        url = await _call(status, "⏳ Paso 2: Animando la imagen...", animate_image_url, public, prompt)
        caption = await _safe_caption()
        await _drop_status(status)
        await _send_video_url(message, url, caption)

    await _run_job(message, user_id, work)


async def _job_edit(message, user_id: int, image: bytes, prompt: str) -> None:
    async def work(status) -> None:
        await _edit_status(status, "⏳ Paso 1: Procesando imagen...")
        public = image_data_uri(image)
        url = await _call(status, "⏳ Paso 2: Modificando la imagen...", edit_image_url, public, prompt)
        caption = await _safe_caption()
        await _drop_status(status)
        await _send_photo_url(message, url, caption)

    await _run_job(message, user_id, work)


async def _job_tts(message, user_id: int, text: str) -> None:
    async def work(status) -> None:
        url = await _call(status, "⏳ Generando voz...", _speak, text)
        caption = await _safe_caption()
        await _drop_status(status)
        try:
            await message.reply_audio(audio=url, caption=caption, reply_markup=_keyboard(), title="Voz")
            return
        except Exception:
            log.exception("Telegram no aceptó la voz por URL")
        response = await asyncio.to_thread(requests.get, url, timeout=120)
        response.raise_for_status()
        await message.reply_audio(
            audio=response.content,
            caption=caption,
            filename="voz.wav",
            reply_markup=_keyboard(),
            title="Voz",
        )

    await _run_job(message, user_id, work)


async def _job_avatar(
    message,
    user_id: int,
    script: str,
    image: bytes,
) -> None:
    async def work(status) -> None:
        image_url = None
        try:
            await _edit_status(status, "⏳ Paso 1: Procesando imagen...")
            if not image:
                await _edit_status(status, "Envía la foto. El avatar no se genera de cero.")
                return
            if image.startswith(b"\x89PNG"):
                mime, ext = "image/png", "png"
            elif len(image) >= 12 and image.startswith(b"RIFF") and image[8:12] == b"WEBP":
                mime, ext = "image/webp", "webp"
            else:
                mime, ext = "image/jpeg", "jpg"
            image_url = await asyncio.to_thread(_fal_upload, image, f"avatar.{ext}", mime)
            audio_remote = await _call(status, "⏳ Paso 2: Generando voz...", _speak, script)
            audio_file = await asyncio.to_thread(requests.get, audio_remote, timeout=120)
            audio_file.raise_for_status()
            audio_type = audio_file.headers.get("Content-Type", "audio/wav").split(";", 1)[0].strip()
            if not audio_type.startswith("audio/"):
                audio_type = "audio/wav"
            audio_url = await asyncio.to_thread(
                _fal_upload, audio_file.content, "voz.wav", audio_type
            )
            final_url = await _call(
                status,
                "⏳ Paso 3: Animando a la persona...",
                talking_avatar_url,
                image_url,
                audio_url,
            )
        except ImageApiError:
            await _edit_status(status, IMAGE_API_ERROR)
            return
        except Exception as exc:
            note = _public_error(exc)[:300]
            await _edit_status(status, "⚠️ El avatar no llegó al vídeo final. Te dejo la foto.")
            try:
                await message.reply_photo(photo=image, caption=note, reply_markup=_keyboard())
            except Exception:
                log.exception("No pude entregar la foto de respaldo")
            return
        caption = await _safe_caption()
        await _drop_status(status)
        await _send_video_url(message, final_url, caption)

    await _run_job(message, user_id, work)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id) or _photo_file(message) is None:
        return
    try:
        data = await _download_photo(context, message)
    except Exception as exc:
        await _reply(update, _public_error(exc), with_keyboard=True)
        return
    _pending_photo[user.id] = {"image": data}
    flow = _flow.get(user.id)
    motion = (message.caption or "").strip()
    if flow == "anim":
        _pending_photo[user.id] = {"image": data, "stage": "describe"}
        if motion:
            _flow.pop(user.id, None)
            await _job_animate(message, user.id, data, motion)
            return
        await message.reply_text(
            "Describe qué quieres ver en el vídeo.",
            reply_markup=_keyboard(),
        )
        return
    if flow == "look":
        _avatar_image[user.id] = data
        _avatar_look.pop(user.id, None)
        _flow[user.id] = "script"
        await message.reply_text(
            "Usaré esta foto.\n\nEscribe el texto que va a decir. "
            "El vídeo mueve la cara con la voz. Kling cobra unos 0,12 USD por segundo.",
            reply_markup=_keyboard(),
        )
        return
    if flow == "edit":
        _flow[user.id] = "edit"
        _pending_photo[user.id]["stage"] = "describe"
        await message.reply_text(
            "¿Qué quieres cambiar? Por ejemplo: cambia la camiseta por una chaqueta de cuero negra.",
            reply_markup=_keyboard(),
        )
        return
    await message.reply_text(
        "Foto lista. Puedes animarla, modificarla o generar otra desde cero.",
        reply_markup=_image_menu(),
    )


async def on_photo_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    message = query.message if query is not None else None
    if query is None or user is None or message is None or not _is_allowed(user.id):
        return
    await query.answer()
    choice = query.data or ""
    if choice == CB_CANCEL:
        _clear_flow(user.id)
        await message.reply_text("Cancelado.", reply_markup=_keyboard())
        return
    if choice == CB_GEN:
        _flow[user.id] = "gen"
        _pending_photo.pop(user.id, None)
        await message.reply_text(
            "Describe la imagen. La genero con Flux, en vertical.",
            reply_markup=_keyboard(),
        )
        return
    if choice == CB_ANIM:
        stored = _pending_photo.get(user.id)
        image = stored.get("image") if stored else None
        if isinstance(image, (bytes, bytearray)):
            _flow[user.id] = "anim"
            stored["stage"] = "describe"
            await message.reply_text(
                "Describe el vídeo: qué quieres ver a partir de esta foto.",
                reply_markup=_keyboard(),
            )
            return
        _flow[user.id] = "anim"
        await message.reply_text(
            "Envía la foto o responde a una del chat.\n\nDespués describe qué quieres ver en el vídeo.",
            reply_markup=_keyboard(),
        )
        return
    if choice == CB_AVATAR:
        photo: bytes | None = None
        stored = _pending_photo.get(user.id)
        raw = stored.get("image") if stored else None
        if isinstance(raw, (bytes, bytearray)):
            photo = bytes(raw)
        elif _photo_file(message.reply_to_message):
            try:
                photo = await _download_photo(context, message.reply_to_message)
            except Exception as exc:
                await message.reply_text(f"⚠️ {_public_error(exc)}", reply_markup=_keyboard())
                return
        if photo:
            _avatar_image[user.id] = photo
            _avatar_look.pop(user.id, None)
            _flow[user.id] = "script"
            await message.reply_text(
                "Usaré esta foto.\n\nEscribe el texto que va a decir. "
            "El vídeo mueve la cara con la voz. Kling cobra unos 0,12 USD por segundo.",
                reply_markup=_keyboard(),
            )
            return
        _avatar_image.pop(user.id, None)
        _flow[user.id] = "look"
        await message.reply_text(
            "Envía la foto o responde a una del chat.\n\nEl vídeo sale de esa foto. Después te pido el texto.",
            reply_markup=_keyboard(),
        )
        return
    if choice == CB_EDIT:
        stored = _pending_photo.get(user.id)
        if stored and isinstance(stored.get("image"), (bytes, bytearray)):
            _flow[user.id] = "edit"
            stored["stage"] = "describe"
            await message.reply_text(
                "¿Qué quieres cambiar? Por ejemplo: cambia la camiseta por una chaqueta de cuero negra.",
                reply_markup=_keyboard(),
            )
            return
        _flow[user.id] = "edit"
        await message.reply_text(
            "Responde a la foto que quieres modificar, o envíala ahora.",
            reply_markup=_keyboard(),
        )
        return
    if choice == CB_TTS:
        _flow[user.id] = "tts"
        if str(_voice_meta().get("url") or "").startswith("http"):
            text = "Escribe el texto. Lo digo con tu voz."
        else:
            text = "Escribe el texto. Todavía no tengo tu voz, así que uso la de ejemplo."
        await message.reply_text(text, reply_markup=_keyboard())
        return
    if choice == CB_VOICE:
        _flow[user.id] = "voice"
        await message.reply_text(
            "Mándame notas de voz o archivos de audio.\n\n"
            "Los junto como muestra. Con unos 6 segundos ya vale, y puedes seguir mandando más.\n"
            "No entreno un modelo nuevo: la voz se copia de estos audios.",
            reply_markup=_keyboard(),
        )
        return


async def on_model_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not _is_allowed(user.id):
        return
    await query.answer()
    try:
        index = int((query.data or "").split(":", 1)[1])
        item = MODELOS[index]
    except (ValueError, IndexError):
        return
    set_model(item["id"])
    if query.message is not None:
        await query.message.reply_text(
            f"Modelo activo: {item['nombre']}\n{item['descripcion']}",
            reply_markup=_keyboard(),
        )


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id):
        return
    choice = (message.text or "").strip()
    _clear_flow(user.id)
    if choice == MENU_CLEAR:
        _histories.pop(user.id, None)
        await _reply(update, "Historial borrado.", with_keyboard=True)
        return
    if choice == MENU_MODEL:
        await message.reply_text(_model_menu_text(), reply_markup=_model_choice())
        return
    if choice in {MENU_IMAGE, MENU_IMAGE_OLD}:
        await message.reply_text(
            "Generación: imagen, vídeo y voz. El chat sigue en los botones de abajo.",
            reply_markup=_image_menu(),
        )
        return
    if choice == MENU_STATUS:
        plain, html, _code = await asyncio.to_thread(server_report)
        try:
            await message.reply_text(html, parse_mode="HTML", reply_markup=_keyboard())
        except Exception:
            log.debug("El estado HTML no salió, mando texto plano", exc_info=True)
            await message.reply_text(plain, reply_markup=_keyboard())
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return

    flow = _flow.get(user.id)
    try:
        if flow == "gen":
            prompt = _clip(text, 1500)
            _flow.pop(user.id, None)
            await _job_generate(message, user.id, prompt)
            return
        if flow == "tts":
            _flow.pop(user.id, None)
            await _job_tts(message, user.id, _clip(text, 800))
            return
        if flow == "look":
            if _photo_file(message.reply_to_message):
                data = await _download_photo(context, message.reply_to_message)
                _avatar_image[user.id] = data
                _avatar_look.pop(user.id, None)
                _pending_photo[user.id] = {"image": data}
                _flow[user.id] = "script"
                await message.reply_text(
                    "Usaré esta foto.\n\nEscribe el texto que va a decir. "
            "El vídeo mueve la cara con la voz. Kling cobra unos 0,12 USD por segundo.",
                    reply_markup=_keyboard(),
                )
                return
            await message.reply_text(
                "Envía la foto o responde a una del chat. El vídeo sale de esa foto.",
                reply_markup=_keyboard(),
            )
            return
        if flow == "script":
            script = _clip(text, 800)
            image = _avatar_image.pop(user.id, None)
            _avatar_look.pop(user.id, None)
            _flow.pop(user.id, None)
            if not isinstance(image, (bytes, bytearray)):
                await message.reply_text(
                    "Falta la foto. Entra otra vez en Avatar Hablando y envíala.",
                    reply_markup=_keyboard(),
                )
                return
            await _job_avatar(message, user.id, script, bytes(image))
            return
        if flow == "anim":
            source = message.reply_to_message if _photo_file(message.reply_to_message) else None
            if source is None:
                stored = _pending_photo.get(user.id)
                image = stored.get("image") if stored else None
                if not isinstance(image, (bytes, bytearray)):
                    await message.reply_text(
                        "Responde a una foto del chat o envíala ahora.",
                        reply_markup=_keyboard(),
                    )
                    return
                data = bytes(image)
            else:
                data = await _download_photo(context, source)
            motion = text.strip()
            if len(motion) < 3:
                await message.reply_text(
                    "Describe qué quieres ver en el vídeo.",
                    reply_markup=_keyboard(),
                )
                return
            _flow.pop(user.id, None)
            _pending_photo.pop(user.id, None)
            await _job_animate(message, user.id, data, motion)
            return
        pending = _pending_photo.get(user.id)
        if flow == "edit" and pending and pending.get("stage") == "describe":
            image = pending.get("image")
            _clear_flow(user.id)
            if not isinstance(image, (bytes, bytearray)):
                await message.reply_text("No tengo la imagen. Vuelve a enviarla.", reply_markup=_keyboard())
                return
            await _job_edit(message, user.id, bytes(image), _clip(text, 1500))
            return
        if flow == "edit" and _photo_file(message.reply_to_message):
            data = await _download_photo(context, message.reply_to_message)
            _clear_flow(user.id)
            await _job_edit(message, user.id, data, _clip(text, 1500))
            return
        if flow == "edit":
            await message.reply_text(
                "Responde a la foto que quieres modificar, o envíala ahora.",
                reply_markup=_keyboard(),
            )
            return
    except RuntimeError as exc:
        await message.reply_text(f"⚠️ {_public_error(exc)}", reply_markup=_keyboard())
        return

    history = _histories[user.id]
    history.append({"role": "user", "content": text})
    _trim(history)
    snapshot = list(history)
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(message.chat, stop))
    try:
        reply = await asyncio.to_thread(_ask, snapshot)
    except Exception:
        log.exception("Fallo al llamar al modelo (user_id=%s)", user.id)
        if history and history[-1].get("role") == "user":
            history.pop()
        await _reply(update, "No pude obtener respuesta del modelo. Inténtalo de nuevo.")
        return
    finally:
        stop.set()
        await typing_task
    if not reply:
        reply = "(El modelo devolvió una respuesta vacía.)"
    history.append({"role": "assistant", "content": reply})
    _trim(history)
    await _reply(update, reply)


def _incoming_audio(message):
    if message is None:
        return None
    if message.voice is not None:
        return message.voice, ".ogg"
    if message.audio is not None:
        mime = message.audio.mime_type or ""
        name = message.audio.file_name or ""
        if "mpeg" in mime or name.lower().endswith(".mp3"):
            return message.audio, ".mp3"
        if "wav" in mime or name.lower().endswith(".wav"):
            return message.audio, ".wav"
        if "mp4" in mime or name.lower().endswith(".m4a"):
            return message.audio, ".m4a"
        return message.audio, ".ogg"
    document = message.document
    if document is not None and (document.mime_type or "").startswith("audio/"):
        name = (document.file_name or "").lower()
        if name.endswith(".mp3"):
            return document, ".mp3"
        if name.endswith(".wav"):
            return document, ".wav"
        if name.endswith(".m4a"):
            return document, ".m4a"
        return document, ".ogg"
    return None


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id):
        return
    found = _incoming_audio(message)
    if found is None:
        return
    if _flow.get(user.id) != "voice":
        await message.reply_text(
            "Para usar este audio, entra en Generación y pulsa Mi voz.",
            reply_markup=_keyboard(),
        )
        return
    media, suffix = found
    if media.file_size and media.file_size > 15_000_000:
        await message.reply_text("El audio es demasiado grande.", reply_markup=_keyboard())
        return
    try:
        telegram_file = await context.bot.get_file(media.file_id)
        data = bytes(await telegram_file.download_as_bytearray())
        seconds = await asyncio.to_thread(_store_voice_clip, data, suffix)
    except Exception as exc:
        await message.reply_text(f"⚠️ {_public_error(exc)}", reply_markup=_keyboard())
        return
    heard = str(round(seconds, 1)).replace(".", ",")
    if seconds < VOICE_MIN_SECONDS:
        await message.reply_text(
            f"Audio guardado. Llevo {heard} s. Manda otro hasta unos 6 s.",
            reply_markup=_keyboard(),
        )
        return
    await message.reply_text(
        f"Voz lista ({heard} s). Texto a voz y el avatar usarán esta voz. "
        "Puedes mandar otro audio para ampliarla, hasta 30 s.",
        reply_markup=_keyboard(),
    )


def _web_token() -> str:
    secret = str(web.secret_key).encode()
    return hmac.new(secret, WEB_PASSWORD.encode(), hashlib.sha256).hexdigest()


def _web_authed() -> bool:
    if session.get("ok") is True:
        return True
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    given = header[7:].strip()
    if not given:
        return False
    return hmac.compare_digest(given, _web_token())


web = Flask(__name__)
web.secret_key = os.environ.get("WEB_SECRET", "bot-ia-local")
CORS(
    web,
    resources={r"/api/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"],
)


@web.get("/")
def web_index():
    return send_from_directory(_STATIC, "index.html")


@web.get("/style.css")
def web_css():
    return send_from_directory(_STATIC, "style.css")


@web.get("/script.js")
def web_js():
    return send_from_directory(_STATIC, "script.js")


@web.get("/api/session")
def web_session():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True, "model": get_model(), "models": _catalog_public()})


@web.get("/api/status")
def web_status():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    plain, _html, code = server_report()
    return jsonify({
        "ok": code == 200,
        "code": code,
        "model": get_model(),
        "gasto": plain,
    })


@web.post("/api/login")
def web_login():
    data = request.get_json(silent=True) or {}
    if str(data.get("password") or "") != WEB_PASSWORD:
        return jsonify({"ok": False}), 401
    session["ok"] = True
    return jsonify({
        "ok": True,
        "token": _web_token(),
        "model": get_model(),
        "models": _catalog_public(),
    })


@web.post("/api/model")
def web_model():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    chosen = str(data.get("model") or "")
    if chosen not in MODELS:
        return jsonify({"ok": False, "error": "modelo no permitido"}), 400
    set_model(chosen)
    return jsonify({"ok": True, "model": get_model()})


@web.post("/api/reset")
def web_reset():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    with _state_lock:
        _web_history.clear()
    return jsonify({"ok": True})


@web.post("/api/chat")
def web_chat():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "mensaje vacío"}), 400
    with _state_lock:
        _web_history.append({"role": "user", "content": text})
        _trim(_web_history)
        snapshot = list(_web_history)
    try:
        reply = _ask(snapshot)
    except Exception:
        log.exception("Fallo al llamar al modelo desde la web")
        with _state_lock:
            if _web_history and _web_history[-1].get("role") == "user":
                _web_history.pop()
        return jsonify({"ok": False, "error": "No pude obtener respuesta del modelo."}), 502
    if not reply:
        reply = "(El modelo devolvió una respuesta vacía.)"
    with _state_lock:
        _web_history.append({"role": "assistant", "content": reply})
        _trim(_web_history)
    return jsonify({"ok": True, "reply": reply, "model": get_model()})


@web.post("/api/edit")
def web_edit():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    upload = request.files.get("image")
    prompt = str(request.form.get("prompt") or "").strip()
    if upload is None or not prompt:
        return jsonify({"ok": False, "error": "faltan la imagen o la descripción"}), 400
    image = upload.read(MAX_IMAGE_BYTES + 1)
    if len(image) > MAX_IMAGE_BYTES:
        return jsonify({"ok": False, "error": "imagen demasiado grande"}), 400
    content_type = upload.mimetype or "image/jpeg"
    try:
        edited = edit_image(image, prompt, content_type)
    except Exception as exc:
        log.exception("Fallo al editar imagen desde la web")
        return jsonify({"ok": False, "error": _public_error(exc)}), 502
    encoded = base64.b64encode(edited).decode("ascii")
    mime = "image/png" if edited.startswith(b"\x89PNG") else "image/jpeg"
    return jsonify({"ok": True, "image": f"data:{mime};base64,{encoded}"})


_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
_audit_lock = threading.Lock()
_audit_bundle: dict[str, object] = {"url": "", "source": "", "turns": []}


def _redact(text: str) -> str:
    patterns = (
        r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b",
        r"\bsk-(?:or-)?[A-Za-z0-9_-]{20,}\b",
        r"\bhf_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bfal_sk_[A-Za-z0-9:_-]{10,}\b",
        r"\br8_[A-Za-z0-9]{10,}\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, "[secreto omitido]", text)
    return text


def _looks_blocked(status: int, text: str) -> bool:
    low = text.lower()
    markers = (
        "just a moment",
        "cf-browser-verification",
        "cf-challenge",
        "attention required",
        "checking your browser",
        "enable javascript and cookies",
    )
    if status in (401, 403, 429, 503) and any(marker in low for marker in markers):
        return True
    return any(marker in low for marker in markers) and "<html" in low and len(text) < 8000


def _decode_body(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace").replace("\x00", "")
    try:
        return content.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        pass
    match = re.search(br"charset=[\"']?([A-Za-z0-9._-]+)", content[:4000], re.I)
    encoding = match.group(1).decode("ascii", "ignore") if match else "cp1252"
    try:
        text = content.decode(encoding, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    text = text.replace("\x00", "")
    if text.count("\ufffd") > max(30, len(text) // 40):
        raise RuntimeError("ilegible")
    return text


def _download(url: str) -> tuple[int, str]:
    response = requests.get(url, headers=_PAGE_HEADERS, timeout=25, allow_redirects=True)
    return response.status_code, _decode_body(response.content or b"")


def _reply_useless(text: str) -> bool:
    visible = text.replace("\ufffd", "").strip()
    letters = sum(1 for ch in visible if ch.isalpha())
    return letters < 12 or (text.count("\ufffd") > 0 and letters < 40)


def _script_urls(page_url: str, html: str) -> list[str]:
    host = urlparse(page_url).netloc
    found: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.I):
        absolute = urljoin(page_url, src)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != host or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= 4:
            break
    return found


def _fetch_page(url: str, progress) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("url")
    progress(12, "Conectando con la página…")
    status, html = _download(url)
    direct_ok = status == 200 and bool(html.strip()) and not _looks_blocked(status, html)
    parts: list[str] = []
    if direct_ok:
        progress(32, f"Página descargada ({len(html)} caracteres).")
        parts.append(f"<!-- pagina {url} -->\n{html[:28000]}")
        scripts = _script_urls(url, html)
        if scripts:
            progress(40, f"Hay {len(scripts)} script(s) del mismo sitio.")
        else:
            progress(48, "La página no enlaza scripts propios. Sigo con el HTML.")
        for index, script_url in enumerate(scripts, start=1):
            progress(42 + index * 6, f"Descargando script {index} de {len(scripts)}…")
            try:
                script_status, body = _download(script_url)
            except requests.RequestException:
                progress(42 + index * 6, f"Script {index} no respondió. Sigo con el resto.")
                continue
            if script_status != 200 or _looks_blocked(script_status, body):
                continue
            parts.append(f"\n<!-- script {script_url} -->\n{body[:12000]}")
        progress(70, "Código reunido. Ahora lo lee el modelo.")
    else:
        progress(24, "La descarga directa no vale. Probando otra vía…")
        mirrored = requests.get(
            "https://r.jina.ai/" + url,
            headers={"X-Return-Format": "markdown", "X-Retain-Images": "none"},
            timeout=40,
        )
        mirrored.encoding = "utf-8"
        mirror = mirrored.text.encode("utf-8", "ignore").decode("utf-8").strip()
        if mirrored.status_code != 200 or not mirror or _looks_blocked(mirrored.status_code, mirror):
            raise RuntimeError("bloqueada")
        progress(58, f"Copia de la página recibida ({len(mirror)} caracteres).")
        parts.append(mirror[:28000])
    source = (
        _redact("\n".join(parts))
        .encode("utf-8", "ignore")
        .decode("utf-8")
        .replace("\ufffd", "")
        .strip()
    )
    if len(source) < 40:
        raise RuntimeError("vacio")
    return source[:60000], len(source)


def _audit_event(kind: str, **payload) -> str:
    body = {"type": kind, **payload}
    return json.dumps(body, ensure_ascii=False) + "\n"


def _audit_stream(url: str, instruccion: str, refetch: bool):
    label = _model_label(get_model())
    events: queue.Queue = queue.Queue()

    def progress(pct: int, step: str) -> None:
        events.put(("progress", pct, step))

    outcome: dict[str, object] = {}

    def work() -> None:
        try:
            with _audit_lock:
                cached = _audit_bundle["url"] == url and bool(_audit_bundle["source"]) and not refetch
                cached_source = str(_audit_bundle["source"] or "")
            if cached:
                progress(70, "Usando el código ya descargado. No vuelve a bajar la página.")
                source = cached_source
                nbytes = len(source)
            else:
                if not url:
                    outcome["error"] = "Falta la URL."
                    outcome["code"] = 400
                    events.put(("end",))
                    return
                source, nbytes = _fetch_page(url, progress)
                with _audit_lock:
                    _audit_bundle["url"] = url
                    _audit_bundle["source"] = source
                    _audit_bundle["turns"] = []
            with _audit_lock:
                prior = list(_audit_bundle["turns"])  # type: ignore[arg-type]
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": (
                        "Eres un analizador estático. Solo puedes usar el código fuente que viene en el mensaje. "
                        "NO des guías, NO expliques herramientas externas y NO digas qué debería hacer el usuario. "
                        "Si un dato no está escrito en ese código, responde exactamente «No encontrado». "
                        "Puedes citar IDs de analítica, comentarios, enlaces y rutas que aparezcan literalmente, "
                        "y enlaces públicos de Telegram (t.me). "
                        "No copies secretos, contraseñas ni tokens de API: si aparece uno, escribe solo «secreto presente». "
                        "No des exploits, payloads ni pasos para atacar sistemas."
                    ),
                },
                {"role": "user", "content": "--- CODIGO FUENTE ---\n" + source},
                {"role": "assistant", "content": "Código recibido. Pregunta sobre ese texto."},
            ]
            for turn in prior[-6:]:
                if isinstance(turn, dict):
                    messages.append({
                        "role": str(turn.get("role") or "user"),
                        "content": str(turn.get("content") or ""),
                    })
            messages.append({"role": "user", "content": instruccion})
            progress(78, f"{label} está leyendo el código. Puede tardar. Sigue en marcha.")
            response = None
            failure = None
            for attempt in (1, 2):
                try:
                    if attempt == 2:
                        progress(84, f"{label} no ha respondido. Segundo intento…")
                    response = _openrouter().chat.completions.create(
                        model=get_model(),
                        messages=messages,
                        max_tokens=4000,
                        temperature=0.1,
                    )
                    failure = None
                    break
                except Exception as exc:
                    failure = exc
                    status = getattr(exc, "status_code", 0) or 0
                    log.warning("Auditoría rechazada por el modelo (HTTP %s, intento %s)", status, attempt)
                    if status not in (0, 400, 408, 409, 429, 500, 502, 503) or attempt == 2:
                        break
            if failure is not None or response is None:
                status = getattr(failure, "status_code", 0) or 0
                if status == 402:
                    outcome["error"] = "OpenRouter no tiene crédito para esta consulta."
                elif status == 429:
                    outcome["error"] = f"{label} está saturado. Espera un momento y pulsa otra vez."
                elif status in (400, 404):
                    outcome["error"] = (
                        f"{label} ha rechazado esta página. "
                        "Pulsa otra vez o cambia a Dolphin Venice o Qwen3 Coder."
                    )
                else:
                    outcome["error"] = f"No pude obtener respuesta de {label}. Pulsa otra vez."
                events.put(("end",))
                return
            reply = _redact((response.choices[0].message.content or "").strip()).replace("\ufffd", "")
            if _reply_useless(reply):
                progress(90, f"{label} ha devuelto basura. Repito la lectura con Dolphin Venice.")
                fallback = _openrouter().chat.completions.create(
                    model="cognitivecomputations/dolphin-mistral-24b-venice-edition",
                    messages=messages,
                    max_tokens=4000,
                    temperature=0.2,
                )
                reply = _redact((fallback.choices[0].message.content or "").strip()).replace("\ufffd", "")
                if _reply_useless(reply):
                    outcome["error"] = (
                        f"{label} no ha leído el código (respuesta ilegible). "
                        "Cambia a Qwen3 Coder y pulsa otra vez."
                    )
                    events.put(("end",))
                    return
                reply = "Leído con Dolphin Venice, porque " + label + " no devolvió texto útil.\n\n" + reply
            reply = reply or "No encontrado"
            with _audit_lock:
                turns = _audit_bundle["turns"]
                if isinstance(turns, list):
                    turns.append({"role": "user", "content": instruccion})
                    turns.append({"role": "assistant", "content": reply})
                    del turns[:-8]
            outcome["ok"] = True
            outcome["result"] = reply
            outcome["bytes"] = nbytes
            outcome["url"] = url
        except requests.RequestException:
            log.warning("Descarga de auditoría fallida")
            outcome["error"] = "Error 500: Objetivo inaccesible"
        except RuntimeError as exc:
            if str(exc) == "bloqueada":
                outcome["error"] = "La web ha bloqueado la descarga automática. No hay código real que analizar."
            elif str(exc) == "ilegible":
                outcome["error"] = "La descarga no es texto legible. No hay código que analizar."
            else:
                outcome["error"] = "Error 500: Objetivo inaccesible"
        events.put(("end",))

    threading.Thread(target=work, name="audit", daemon=True).start()
    last_pct = 5
    last_step = "Empieza la lectura…"
    seconds = 0
    yield _audit_event("progress", pct=last_pct, step=last_step)
    while True:
        try:
            item = events.get(timeout=2)
        except queue.Empty:
            seconds += 2
            last_pct = min(96, last_pct + 1)
            yield _audit_event(
                "progress",
                pct=last_pct,
                step=f"{last_step} Sigue trabajando ({seconds} s).",
            )
            continue
        if item[0] == "end":
            break
        _, last_pct, last_step = item
        seconds = 0
        yield _audit_event("progress", pct=int(last_pct), step=str(last_step))
    if outcome.get("ok"):
        yield _audit_event(
            "done",
            ok=True,
            result=outcome.get("result"),
            bytes=outcome.get("bytes"),
            url=outcome.get("url"),
        )
        return
    yield _audit_event("done", ok=False, error=str(outcome.get("error") or "Error 500: Objetivo inaccesible"))


@web.post("/api/audit_url")
def web_audit():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    instruccion = str(data.get("instruccion") or "").strip()
    refetch = bool(data.get("refetch", True))
    if not instruccion:
        return jsonify({"ok": False, "error": "Falta la instrucción."}), 400
    return Response(
        stream_with_context(_audit_stream(url, instruccion, refetch)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_web() -> None:
    web.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False)


def main() -> None:
    missing = [
        name
        for name, value in (
            ("API_KEY", API_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("WEB_PASSWORD", WEB_PASSWORD),
        )
        if not str(value).strip()
    ]
    if missing:
        raise SystemExit("Rellena estas variables en el .env: " + ", ".join(missing))
    if ALLOWED_TELEGRAM_ID <= 0:
        raise SystemExit("Define ALLOWED_TELEGRAM_ID con tu ID numérico de Telegram.")

    threading.Thread(target=_run_web, name="web", daemon=True).start()
    log.info("Panel web en http://0.0.0.0:%s", WEB_PORT)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .request(HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=60.0,
            write_timeout=180.0,
            pool_timeout=30.0,
        ))
        .concurrent_updates(True)
        .build()
    )
    menu_filter = filters.Regex(
        "^(" + "|".join(
            re.escape(label) for label in (MENU_MODEL, MENU_CLEAR, MENU_STATUS, MENU_IMAGE, MENU_IMAGE_OLD)
        ) + ")$"
    )
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("menu", on_start))
    app.add_handler(CommandHandler("reset", on_reset))
    app.add_handler(CallbackQueryHandler(on_model_choice, pattern=r"^mdl:"))
    app.add_handler(CallbackQueryHandler(on_photo_choice, pattern=r"^img:"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, on_voice))
    app.add_handler(MessageHandler(menu_filter, on_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot en marcha. modelo=%s allowed_id=%s", get_model(), ALLOWED_TELEGRAM_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
