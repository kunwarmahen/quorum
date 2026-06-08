"""The processing pipeline: video -> mp3 -> transcript -> meeting minutes.

Each stage updates the DB so the dashboard can show live status, and stages
are resumable: rerunning a file picks up wherever it left off.
"""
import os
import subprocess

import config
import db
import ollama_client
import whisper_client

# Detail levels the user can pick for the generated minutes.
MINUTES_LEVELS = {
    "brief": {
        "label": "Brief",
        "instructions": (
            "Write a very concise summary (5-8 bullet points max) capturing only "
            "the key outcomes. Then list Decisions and Action Items. Keep it tight."
        ),
    },
    "standard": {
        "label": "Standard",
        "instructions": (
            "Write well-structured meeting minutes with these sections: "
            "Summary (one short paragraph), Key Discussion Points (bullets), "
            "Decisions, and Action Items (with owner and due date if mentioned)."
        ),
    },
    "detailed": {
        "label": "Detailed",
        "instructions": (
            "Write comprehensive meeting minutes with these sections: "
            "Attendees (only if identifiable), Agenda/Topics, a topic-by-topic "
            "breakdown of the discussion, Decisions made (with rationale), "
            "Action Items (owner, task, due date), Risks/Open Questions, and "
            "Next Steps. Be thorough and faithful to the transcript."
        ),
    },
}
DEFAULT_LEVEL = "standard"


def _minutes_prompt(level: str, transcript: str) -> str:
    spec = MINUTES_LEVELS.get(level, MINUTES_LEVELS[DEFAULT_LEVEL])
    return (
        "You are an expert meeting-minutes assistant. Based ONLY on the meeting "
        "transcript below, produce clean, professional meeting minutes in "
        "Markdown.\n\n"
        f"{spec['instructions']}\n\n"
        "Do not invent information that is not supported by the transcript. If "
        "something (like attendee names or dates) is not stated, omit it rather "
        "than guessing.\n\n"
        "=== TRANSCRIPT START ===\n"
        f"{transcript}\n"
        "=== TRANSCRIPT END ===\n\n"
        "Meeting Minutes:"
    )


def probe_duration(path: str):
    """Return media duration in seconds via ffprobe, or None if unknown.
    Works for both video and audio (any container ffprobe understands)."""
    if not path or not os.path.exists(path):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            check=True, capture_output=True, text=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def _sidecar(source_path: str, suffix: str) -> str:
    # Artifacts live next to the source, i.e. inside the meeting's own folder.
    base = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(os.path.dirname(source_path), f"{base}{suffix}")


# --- Stage 1: video -> mp3 -------------------------------------------------
def convert_to_mp3(rec_id: int) -> dict:
    rec = db.get(rec_id)
    if not rec:
        raise ValueError("recording not found")
    if rec["kind"] == "audio":
        db.update(rec_id, audio_path=rec["source_path"], audio_status="done")
        return db.get(rec_id)

    src = rec["source_path"]
    if not os.path.exists(src):
        db.update(rec_id, audio_status="error",
                  error=f"Source file missing: {src}")
        raise FileNotFoundError(src)

    out = _sidecar(src, ".mp3")
    db.update(rec_id, audio_status="running", status="processing", error=None)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vn", "-acodec", "libmp3lame",
             "-q:a", "2", out],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        db.update(rec_id, audio_status="error",
                  error=f"ffmpeg failed: {e.stderr[-500:]}")
        raise
    db.update(rec_id, audio_path=out, audio_status="done")
    return db.get(rec_id)


# --- Stage 2: mp3 -> transcript -------------------------------------------
def transcribe(rec_id: int) -> dict:
    rec = db.get(rec_id)
    if not rec:
        raise ValueError("recording not found")
    audio = rec["audio_path"]
    if not audio or not os.path.exists(audio):
        rec = convert_to_mp3(rec_id)
        audio = rec["audio_path"]

    db.update(rec_id, transcript_status="running", status="processing", error=None)
    try:
        text = whisper_client.transcribe(audio)
    except Exception as e:
        db.update(rec_id, transcript_status="error", error=f"Whisper error: {e}")
        raise
    out = _sidecar(rec["source_path"], ".transcript.txt")
    with open(out, "w") as f:
        f.write(text)
    db.update(rec_id, transcript_text=text, transcript_path=out,
              transcript_status="done")
    return db.get(rec_id)


# --- Stage 3: transcript -> minutes ---------------------------------------
def generate_minutes(rec_id: int, level: str = DEFAULT_LEVEL) -> dict:
    rec = db.get(rec_id)
    if not rec:
        raise ValueError("recording not found")
    if level not in MINUTES_LEVELS:
        level = DEFAULT_LEVEL
    transcript = rec["transcript_text"]
    if not transcript:
        rec = transcribe(rec_id)
        transcript = rec["transcript_text"]

    db.update(rec_id, minutes_status="running", minutes_level=level,
              status="processing", error=None)
    try:
        text = ollama_client.generate(_minutes_prompt(level, transcript))
    except Exception as e:
        db.update(rec_id, minutes_status="error", error=f"Ollama error: {e}")
        raise
    out = _sidecar(rec["source_path"], f".minutes.{level}.md")
    with open(out, "w") as f:
        f.write(text)
    db.update(rec_id, minutes_text=text, minutes_path=out,
              minutes_status="done", status="done")
    return db.get(rec_id)


# --- Full pipeline ---------------------------------------------------------
def run_all(rec_id: int, level: str = DEFAULT_LEVEL) -> dict:
    convert_to_mp3(rec_id)
    transcribe(rec_id)
    return generate_minutes(rec_id, level)
