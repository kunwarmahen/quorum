"""Client for the local Ollama pod used to generate meeting minutes."""
import requests

import config


def list_models() -> list:
    """Return the names of all models installed in Ollama (may raise)."""
    resp = requests.get(f"{config.effective_ollama_url()}/api/tags", timeout=5)
    resp.raise_for_status()
    names = [m.get("name", "") for m in resp.json().get("models", [])]
    return sorted(n for n in names if n)


def health() -> dict:
    """Liveness check for the Ollama server, plus whether the configured model
    is available. Never raises; used by the dashboard health panel."""
    model = config.effective_ollama_model()
    info = {"name": "Ollama", "url": config.effective_ollama_url(), "ok": False,
            "model": model}
    try:
        names = list_models()
        info["ok"] = True
        info["model_ready"] = any(
            n == model or n.split(":")[0] == model for n in names
        )
    except Exception as e:
        info["error"] = str(e)
    return info


def _model_present(model: str) -> bool:
    try:
        resp = requests.get(f"{config.effective_ollama_url()}/api/tags", timeout=10)
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models", [])}
        # tags come back as "llama3.2:latest"; match with or without the tag.
        return any(n == model or n.split(":")[0] == model for n in names)
    except Exception:
        return False


def ensure_model(model: str = None) -> None:
    """Pull the model if it isn't available yet (blocking; first run is slow)."""
    model = model or config.effective_ollama_model()
    if _model_present(model):
        return
    resp = requests.post(
        f"{config.effective_ollama_url()}/api/pull",
        json={"name": model, "stream": False},
        timeout=3600,
    )
    resp.raise_for_status()


def generate(prompt: str, model: str = None) -> str:
    """Run a single-shot generation and return the text."""
    model = model or config.effective_ollama_model()
    ensure_model(model)
    resp = requests.post(
        f"{config.effective_ollama_url()}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=3600,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()
