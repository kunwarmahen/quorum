"""Central configuration.

Values come from two layers, resolved at call time (never frozen at import):

    DB settings override   >   environment variable / built-in default

The environment variables (see ``.env`` / ``docker-compose.yml``) provide the
initial defaults shown in the dashboard's Config modal. A user can override any
of them at runtime from that modal; the override is persisted in the DB
``settings`` table and used everywhere via the ``effective_*`` accessors below.
Saving ``Reset to defaults`` clears the overrides and falls back to the env.
"""
import os

# --- Environment defaults (the initial values surfaced in the Config modal) ---
# Keyed by the same setting key used in the DB and the UI.
_ENV_DEFAULTS = {
    # OBS (runs on the host)
    "obs_host": os.getenv("OBS_HOST", "host.containers.internal"),
    "obs_port": os.getenv("OBS_PORT", "4455"),
    "obs_password": os.getenv("OBS_PASSWORD", ""),
    # Whisper transcription (external OpenAI-compatible STT server). Base URL
    # must include the OpenAI-style /v1 suffix; the client posts to
    # {base}/audio/transcriptions.
    "stt_base_url": os.getenv("STT_BASE_URL", "http://whisper:9000/v1").rstrip("/"),
    "stt_model": os.getenv("STT_MODEL", "whisper-1"),
    "stt_api_key": os.getenv("STT_API_KEY", ""),  # many local servers ignore this
    # Ollama (minutes)
    "ollama_url": os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
    "ollama_model": os.getenv("OLLAMA_MODEL", "llama3.2"),
}

# Secret fields are rendered as password inputs and an empty value is honored
# (means "no auth") rather than falling back to the env default.
SECRET_KEYS = {"obs_password", "stt_api_key"}

# UI metadata: drives the Config modal's grouped form. Order matters.
CONFIG_FIELDS = [
    {"key": "obs_host",     "label": "Host",                   "group": "OBS",         "type": "text",
     "hint": "Where OBS Studio's WebSocket server is reachable."},
    {"key": "obs_port",     "label": "Port",                   "group": "OBS",         "type": "number"},
    {"key": "obs_password", "label": "WebSocket password",     "group": "OBS",         "type": "password",
     "hint": "From OBS → Tools → WebSocket Server Settings. Blank = no auth."},
    {"key": "stt_base_url", "label": "Base URL",               "group": "Whisper STT", "type": "text",
     "hint": "Must include the /v1 suffix."},
    {"key": "stt_model",    "label": "Model",                  "group": "Whisper STT", "type": "model", "models": "stt"},
    {"key": "stt_api_key",  "label": "API key",                "group": "Whisper STT", "type": "password",
     "hint": "Only if your server requires one; usually blank."},
    {"key": "ollama_url",   "label": "URL",                    "group": "Ollama",      "type": "text"},
    {"key": "ollama_model", "label": "Model (minutes)",        "group": "Ollama",      "type": "model", "models": "ollama"},
]

CONFIG_KEYS = {f["key"] for f in CONFIG_FIELDS}


def env_default(key: str) -> str:
    """The .env / built-in default for a setting (ignores any DB override)."""
    return _ENV_DEFAULTS.get(key, "")


def is_overridden(key: str) -> bool:
    """True if a DB override is set for this key (i.e. it differs from .env)."""
    import db  # local import to avoid a config <-> db import cycle
    return db.get_setting(key) is not None


def effective(key: str) -> str:
    """Resolve a setting: DB override if present, else the env default.

    An override stored as an empty string is honored only for secret fields
    (empty = no auth). For required fields (hosts, URLs, ports, models) an empty
    override falls back to the default so the app can't be bricked by clearing
    a field.
    """
    import db
    val = db.get_setting(key)
    if val is None:
        return _ENV_DEFAULTS.get(key, "")
    if val == "" and key not in SECRET_KEYS:
        return _ENV_DEFAULTS.get(key, "")
    return val


# --- Typed accessors used across the app ----------------------------------
def effective_obs_host() -> str:
    return effective("obs_host")


def effective_obs_port() -> int:
    try:
        return int(effective("obs_port"))
    except (TypeError, ValueError):
        return int(_ENV_DEFAULTS["obs_port"])


def effective_obs_password() -> str:
    return effective("obs_password")


def effective_stt_base_url() -> str:
    return effective("stt_base_url").rstrip("/")


def effective_stt_api_key() -> str:
    return effective("stt_api_key")


def effective_stt_model() -> str:
    return effective("stt_model")


def effective_ollama_url() -> str:
    return effective("ollama_url").rstrip("/")


def effective_ollama_model() -> str:
    return effective("ollama_model")


# --- Paths ---
# DATA_DIR is the container path; HOST_DATA_DIR is the same dir on the host.
# OBS reports host paths, so we translate host -> container with these two.
DATA_DIR = os.getenv("DATA_DIR", "/data")
HOST_DATA_DIR = os.getenv("HOST_DATA_DIR", DATA_DIR)

RECORDINGS_DIRNAME = "recordings"
RECORDINGS_DIR = os.path.join(DATA_DIR, RECORDINGS_DIRNAME)              # container
HOST_RECORDINGS_DIR = os.path.join(HOST_DATA_DIR, RECORDINGS_DIRNAME)   # host
DB_PATH = os.path.join(DATA_DIR, "meetings.db")

# Media we know how to process.
VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".webm", ".avi", ".flv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def host_to_container(path: str) -> str:
    """Translate a host filesystem path (as OBS reports) into the container path."""
    if path and path.startswith(HOST_DATA_DIR):
        return DATA_DIR + path[len(HOST_DATA_DIR):]
    return path
