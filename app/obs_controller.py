"""Control OBS Studio (running on the host) over the OBS WebSocket v5 protocol.

A fresh connection is made per call so the app tolerates OBS being restarted.
"""
import config


class OBSError(RuntimeError):
    pass


def _client():
    try:
        import obsws_python as obs
    except ImportError as e:  # pragma: no cover
        raise OBSError("obsws-python not installed") from e
    try:
        return obs.ReqClient(
            host=config.OBS_HOST,
            port=config.OBS_PORT,
            password=config.OBS_PASSWORD or "",
            timeout=5,
        )
    except Exception as e:
        raise OBSError(
            f"Cannot reach OBS at {config.OBS_HOST}:{config.OBS_PORT}. "
            f"Is OBS running with the WebSocket server enabled? ({e})"
        ) from e


def status() -> dict:
    """Return OBS connection + recording status without raising on failure."""
    try:
        cl = _client()
        rec = cl.get_record_status()
        return {
            "connected": True,
            "recording": bool(getattr(rec, "output_active", False)),
            "paused": bool(getattr(rec, "output_paused", False)),
            "timecode": getattr(rec, "output_timecode", None),
        }
    except Exception as e:
        return {"connected": False, "recording": False, "error": str(e)}


def start_record() -> None:
    """Point OBS at our recordings dir, then start recording."""
    cl = _client()
    # Best-effort: make OBS write into the mounted recordings folder so the
    # resulting file lands inside the container's /data volume.
    try:
        cl.set_record_directory(config.HOST_RECORDINGS_DIR)
    except Exception:
        pass  # older OBS without SetRecordDirectory; user must set it manually
    rec = cl.get_record_status()
    if getattr(rec, "output_active", False):
        raise OBSError("OBS is already recording.")
    cl.start_record()


def stop_record() -> str:
    """Stop recording and return the host path of the saved file."""
    cl = _client()
    resp = cl.stop_record()
    # obs-websocket v5 returns the path in StopRecord's response.
    path = getattr(resp, "output_path", None)
    return path
