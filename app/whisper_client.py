"""Speech-to-text via an external OpenAI-compatible STT server.

Posts the audio to {STT_BASE_URL}/audio/transcriptions, the same shape as
OpenAI's transcription API (works with speaches / faster-whisper-server / etc.).
"""
import os

import requests

import config


def _auth_headers() -> dict:
    key = config.effective_stt_api_key()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def list_models() -> list:
    """Return the model ids the STT server reports via /v1/models (may raise)."""
    resp = requests.get(f"{config.effective_stt_base_url()}/models",
                        headers=_auth_headers(), timeout=5)
    resp.raise_for_status()
    ids = [m.get("id", "") for m in resp.json().get("data", [])]
    return sorted(i for i in ids if i)


def health() -> dict:
    """Liveness check for the STT server, plus whether the configured model is
    listed by /v1/models. Never raises; used by the dashboard health panel."""
    model = config.effective_stt_model()
    info = {"name": "Whisper STT", "url": config.effective_stt_base_url(), "ok": False,
            "model": model}
    try:
        ids = list_models()
        info["ok"] = True
        if ids:
            info["model_ready"] = model in ids
    except ValueError:
        info["ok"] = True  # server is up but didn't return JSON; reachable is enough
    except Exception as e:
        info["error"] = str(e)
    return info


def transcribe(audio_path: str) -> str:
    url = f"{config.effective_stt_base_url()}/audio/transcriptions"
    headers = _auth_headers()

    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
        # vad_filter skips silent stretches (supported by speaches /
        # faster-whisper-server). Without it Whisper can hallucinate over
        # silence and loop one sentence for the rest of the recording.
        data = {"model": config.effective_stt_model(), "response_format": "text",
                "vad_filter": "true"}
        resp = requests.post(url, headers=headers, files=files, data=data,
                             timeout=3600)
    resp.raise_for_status()

    # response_format=text returns plain text; some servers still answer JSON.
    body = resp.text.strip()
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype or body.startswith("{"):
        try:
            return resp.json().get("text", "").strip()
        except ValueError:
            pass
    return body
