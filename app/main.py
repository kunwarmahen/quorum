"""FastAPI web app: dashboard + REST API for the meeting-minutes pipeline."""
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel

import config
import db
import obs_controller
import ollama_client
import pipeline
import whisper_client

app = FastAPI(title="Quorum")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Long-running stages run here so HTTP requests return immediately; the DB
# carries the live status that the dashboard polls.
_pool = ThreadPoolExecutor(max_workers=2)


@app.on_event("startup")
def _startup():
    db.init()
    scan_files()


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------
def _classify(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in config.VIDEO_EXTS:
        return "video"
    if ext in config.AUDIO_EXTS:
        return "audio"
    return None


def _organize(path: str) -> str:
    """Move a loose media file in the recordings root into its own folder so
    each meeting keeps its source + generated files together. Idempotent: a
    file already inside a subfolder is returned unchanged."""
    parent = os.path.abspath(os.path.dirname(path))
    if parent != os.path.abspath(config.RECORDINGS_DIR):
        return path  # already in a per-meeting folder
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(config.RECORDINGS_DIR, base)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(path))
    if os.path.abspath(dest) == os.path.abspath(path) or os.path.exists(dest):
        return dest if os.path.exists(dest) else path
    os.rename(path, dest)
    return dest


def register_media(path: str, kind: str = None, is_recording: bool = False) -> int:
    """Organize a media file into its folder and register it in the DB."""
    kind = kind or _classify(path) or "video"
    path = _organize(path)
    rec_id = db.add_file(path, kind, is_recording=is_recording)
    # Record size now; duration only once the file is final (not mid-recording).
    fields = {}
    if os.path.exists(path):
        fields["size_bytes"] = os.path.getsize(path)
    if not is_recording:
        dur = pipeline.probe_duration(path)
        if dur is not None:
            fields["duration_seconds"] = dur
    if fields:
        db.update(rec_id, **fields)
    return rec_id


def _with_media_meta(recs: list) -> list:
    """Refresh each row's file size from disk (it changes as a file grows),
    backfill duration once via ffprobe for older rows, and attach the file's
    own timestamp (file_mtime) so the UI can show the real file date rather
    than when it happened to be scanned into the app."""
    for r in recs:
        src = r.get("source_path")
        if src and os.path.exists(src):
            st = os.stat(src)
            if st.st_size != r.get("size_bytes"):
                db.update(r["id"], size_bytes=st.st_size)
                r["size_bytes"] = st.st_size
            # When the media file was last written — the closest reliable proxy
            # for "created" on Linux (true birth time isn't portable).
            r["file_mtime"] = datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds")
            if r.get("duration_seconds") is None and not r.get("is_recording"):
                dur = pipeline.probe_duration(src)
                if dur is not None:
                    db.update(r["id"], duration_seconds=dur)
                    r["duration_seconds"] = dur
    return recs


def scan_files() -> int:
    """Register any new media files found loose in the recordings root.
    Files already filed into per-meeting subfolders are left as-is."""
    added = 0
    os.makedirs(config.RECORDINGS_DIR, exist_ok=True)
    # Files we generated ourselves (e.g. the extracted .mp3) must not be picked
    # up as if they were new source media.
    known_artifacts = set()
    for r in db.list_all():
        for key in ("audio_path", "transcript_path", "minutes_path"):
            if r.get(key):
                known_artifacts.add(r[key])
    for entry in os.scandir(config.RECORDINGS_DIR):
        if not entry.is_file():
            continue  # subfolders (already-organized meetings) are skipped
        # Skip our own generated artefacts.
        if entry.name.endswith((".transcript.txt", ".md")):
            continue
        if entry.path in known_artifacts:
            continue
        kind = _classify(entry.path)
        if not kind:
            continue
        if not db.get_by_source(entry.path):
            register_media(entry.path, kind)
            added += 1
    return added


# --------------------------------------------------------------------------
# Background stage runner
# --------------------------------------------------------------------------
def _submit(fn, *args):
    def task():
        try:
            fn(*args)
        except Exception:
            pass  # error is already recorded on the row by the pipeline
    _pool.submit(task)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "levels": pipeline.MINUTES_LEVELS,
         "default_level": pipeline.DEFAULT_LEVEL},
    )


# --------------------------------------------------------------------------
# API: health of external dependencies (OBS, Whisper STT, Ollama)
# --------------------------------------------------------------------------
def _obs_health() -> dict:
    """Normalize obs_controller.status() into the shared health shape."""
    st = obs_controller.status()
    h = {
        "name": "OBS",
        "url": f"{config.OBS_HOST}:{config.OBS_PORT}",
        "ok": bool(st.get("connected")),
        "recording": bool(st.get("recording")),
    }
    if st.get("error"):
        h["error"] = st["error"]
    return h


def gather_health() -> dict:
    """Probe every external service we depend on. Each probe is best-effort and
    never raises, so the dashboard always gets a complete picture."""
    services = [_obs_health(), whisper_client.health(), ollama_client.health()]
    return {"ok": all(s["ok"] for s in services), "services": services}


@app.get("/api/health")
def api_health():
    return gather_health()


# --------------------------------------------------------------------------
# API: model selection (config panel)
# --------------------------------------------------------------------------
def _model_list(lister, current: str) -> dict:
    """Best-effort fetch of a server's model list for the config dropdowns.
    Always includes the current selection so the dropdown can show it even if
    the server is unreachable."""
    try:
        models = lister()
        return {"ok": True, "models": models, "current": current}
    except Exception as e:
        return {"ok": False, "models": [], "current": current, "error": str(e)}


@app.get("/api/models")
def api_models():
    return {
        "ollama": _model_list(ollama_client.list_models,
                              config.effective_ollama_model()),
        "stt": _model_list(whisper_client.list_models,
                          config.effective_stt_model()),
    }


class ConfigBody(BaseModel):
    ollama_model: str | None = None
    stt_model: str | None = None


@app.post("/api/config")
def api_set_config(body: ConfigBody):
    if body.ollama_model:
        db.set_setting("ollama_model", body.ollama_model.strip())
    if body.stt_model:
        db.set_setting("stt_model", body.stt_model.strip())
    return {
        "ok": True,
        "ollama_model": config.effective_ollama_model(),
        "stt_model": config.effective_stt_model(),
    }


# --------------------------------------------------------------------------
# API: recordings
# --------------------------------------------------------------------------
@app.get("/api/recordings")
def api_recordings():
    return {"recordings": _with_media_meta(db.list_all()),
            "obs": obs_controller.status()}


@app.get("/api/recordings/{rec_id}")
def api_recording(rec_id: int):
    rec = db.get(rec_id)
    if not rec:
        raise HTTPException(404, "not found")
    return rec


@app.post("/api/scan")
def api_scan():
    return {"added": scan_files()}


class StageBody(BaseModel):
    level: str = pipeline.DEFAULT_LEVEL


@app.post("/api/recordings/{rec_id}/convert")
def api_convert(rec_id: int):
    if not db.get(rec_id):
        raise HTTPException(404, "not found")
    db.update(rec_id, audio_status="running")
    _submit(pipeline.convert_to_mp3, rec_id)
    return {"ok": True}


@app.post("/api/recordings/{rec_id}/transcribe")
def api_transcribe(rec_id: int):
    if not db.get(rec_id):
        raise HTTPException(404, "not found")
    db.update(rec_id, transcript_status="running")
    _submit(pipeline.transcribe, rec_id)
    return {"ok": True}


@app.post("/api/recordings/{rec_id}/minutes")
def api_minutes(rec_id: int, body: StageBody):
    if not db.get(rec_id):
        raise HTTPException(404, "not found")
    db.update(rec_id, minutes_status="running", minutes_level=body.level)
    _submit(pipeline.generate_minutes, rec_id, body.level)
    return {"ok": True}


@app.post("/api/recordings/{rec_id}/run")
def api_run_all(rec_id: int, body: StageBody):
    if not db.get(rec_id):
        raise HTTPException(404, "not found")
    db.update(rec_id, status="processing")
    _submit(pipeline.run_all, rec_id, body.level)
    return {"ok": True}


# --------------------------------------------------------------------------
# API: OBS recording control
# --------------------------------------------------------------------------
@app.get("/api/obs/status")
def api_obs_status():
    return obs_controller.status()


@app.post("/api/obs/start")
def api_obs_start():
    try:
        obs_controller.start_record()
    except obs_controller.OBSError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/obs/stop")
def api_obs_stop():
    try:
        host_path = obs_controller.stop_record()
    except obs_controller.OBSError as e:
        raise HTTPException(400, str(e))

    # OBS may take a moment to flush/close the file.
    container_path = None
    for _ in range(20):
        if host_path:
            cp = config.host_to_container(host_path)
            if os.path.exists(cp):
                container_path = cp
                break
        time.sleep(0.5)

    if not container_path:
        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "warning": (
                    "Recording stopped, but the file wasn't found in the mounted "
                    "recordings folder. Make sure OBS records into "
                    f"{config.HOST_RECORDINGS_DIR}. Reported path: {host_path}"
                ),
            },
        )

    kind = _classify(container_path) or "video"
    rec_id = register_media(container_path, kind)
    db.update(rec_id, status="new", is_recording=0)
    return {"ok": True, "id": rec_id, "path": db.get(rec_id)["source_path"]}


# --------------------------------------------------------------------------
# API: download generated artefacts
# --------------------------------------------------------------------------
@app.get("/api/recordings/{rec_id}/download/{kind}")
def api_download(rec_id: int, kind: str):
    rec = db.get(rec_id)
    if not rec:
        raise HTTPException(404, "not found")
    path = {
        "audio": rec.get("audio_path"),
        "transcript": rec.get("transcript_path"),
        "minutes": rec.get("minutes_path"),
        "source": rec.get("source_path"),
    }.get(kind)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "file not available")
    return FileResponse(path, filename=os.path.basename(path))


# --------------------------------------------------------------------------
# API: delete a meeting and its dependent files
# --------------------------------------------------------------------------
def _under_recordings(path: str) -> bool:
    """True only if `path` is strictly inside the recordings dir."""
    recdir = os.path.abspath(config.RECORDINGS_DIR)
    ap = os.path.abspath(path)
    return ap.startswith(recdir + os.sep)


def _delete_plan(rec: dict) -> dict:
    """Work out what removing this meeting would delete, without deleting.

    If the source lives in its own per-meeting subfolder, the whole folder
    goes (source + mp3 + transcript + minutes + any extras). For legacy flat
    files, only the DB-referenced files are removed. Anything that isn't
    safely inside the recordings dir is never touched.
    """
    src = os.path.abspath(rec["source_path"])
    recdir = os.path.abspath(config.RECORDINGS_DIR)
    folder = os.path.dirname(src)

    if folder != recdir and _under_recordings(folder):
        files = []
        if os.path.isdir(folder):
            files = sorted(os.path.join(folder, n) for n in os.listdir(folder))
        return {"folder": folder, "files": files}

    # Legacy flat layout: only the known artefacts for this row.
    files, seen = [], set()
    for key in ("source_path", "audio_path", "transcript_path", "minutes_path"):
        p = rec.get(key)
        if not p:
            continue
        ap = os.path.abspath(p)
        if ap in seen or not _under_recordings(ap):
            continue
        seen.add(ap)
        files.append(ap)
    return {"folder": None, "files": files}


@app.get("/api/recordings/{rec_id}/delete-preview")
def api_delete_preview(rec_id: int):
    rec = db.get(rec_id)
    if not rec:
        raise HTTPException(404, "not found")
    plan = _delete_plan(rec)
    # Report only what actually exists on disk, plus the row itself.
    existing = [p for p in plan["files"] if os.path.exists(p)]
    return {
        "id": rec_id,
        "name": rec["name"],
        "folder": plan["folder"],
        "files": existing,
        "whole_folder": plan["folder"] is not None,
    }


@app.delete("/api/recordings/{rec_id}")
def api_delete(rec_id: int):
    rec = db.get(rec_id)
    if not rec:
        raise HTTPException(404, "not found")
    if rec.get("is_recording"):
        raise HTTPException(409, "This meeting is still recording; stop it first.")

    plan = _delete_plan(rec)
    deleted = []
    recdir = os.path.abspath(config.RECORDINGS_DIR)

    folder = plan["folder"]
    if folder and folder != recdir and _under_recordings(folder) and os.path.isdir(folder):
        shutil.rmtree(folder)
        deleted.append(folder)
    else:
        for p in plan["files"]:
            # Re-check each path is inside recordings right before unlinking.
            if _under_recordings(p) and os.path.isfile(p):
                os.remove(p)
                deleted.append(p)

    db.delete(rec_id)
    return {"ok": True, "deleted": deleted}
