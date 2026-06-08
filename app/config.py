"""Central configuration, read from environment variables (see docker-compose.yml)."""
import os

# --- OBS (runs on the host) ---
OBS_HOST = os.getenv("OBS_HOST", "host.containers.internal")
OBS_PORT = int(os.getenv("OBS_PORT", "4455"))
OBS_PASSWORD = os.getenv("OBS_PASSWORD", "")

# --- Whisper transcription (external OpenAI-compatible STT server) ---
# Base URL must include the OpenAI-style /v1 suffix; the client posts to
# {STT_BASE_URL}/audio/transcriptions.
STT_BASE_URL = os.getenv("STT_BASE_URL", "http://whisper:9000/v1").rstrip("/")
STT_MODEL = os.getenv("STT_MODEL", "whisper-1")
STT_API_KEY = os.getenv("STT_API_KEY", "")  # many local servers ignore this

# --- Ollama (minutes) ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

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


# --- Active model selection ---
# The env vars above are the initial defaults; a user can override them at
# runtime from the dashboard's Config panel, which persists the choice in the
# DB settings table. These helpers resolve the effective model (DB > env).
def effective_ollama_model() -> str:
    import db  # local import to avoid a config <-> db import cycle
    return db.get_setting("ollama_model") or OLLAMA_MODEL


def effective_stt_model() -> str:
    import db
    return db.get_setting("stt_model") or STT_MODEL


def host_to_container(path: str) -> str:
    """Translate a host filesystem path (as OBS reports) into the container path."""
    if path and path.startswith(HOST_DATA_DIR):
        return DATA_DIR + path[len(HOST_DATA_DIR):]
    return path
