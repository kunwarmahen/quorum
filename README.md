# Quorum — AI meeting minutes

A self-hosted app that records a meeting with **OBS Studio**, then runs it
through a fully local pipeline:

```
OBS recording (.mkv)  →  mp3  →  transcript (Whisper)  →  meeting minutes (LLM)
```

Everything runs locally with **Podman**. The web dashboard lists every media
file, shows where each one is in the pipeline, lets you run any stage (or the
whole thing), pick how detailed the minutes should be, sort the list, copy the
result into Google Docs / Word with formatting intact, and delete a meeting and
all its files. State is stored in SQLite, so it survives restarts.

### Highlights

- **One-click pipeline** — record with OBS (or drop in a file) and go from video
  to polished minutes; run the whole chain or any single stage.
- **Choose your detail level** — Brief / Standard / Detailed minutes.
- **Runtime model picker** — switch the Whisper and Ollama models from the
  **⚙ Config** panel; the choice is saved and survives restarts.
- **Live health** — top-bar pills show OBS, Whisper, and Ollama status at a glance.
- **Per-meeting folders** — source + mp3 + transcript + minutes grouped together.
- **File details at a glance** — each row shows the recording's duration, file
  size, and date (the file's own timestamp, not when it was scanned in).
- **Sort & find** — order the list by newest/oldest (by file date) or name;
  hover the **ⓘ** next to a file for its date, type, and minutes level.
- **Copy, formatted** — copy minutes as rich text that pastes into Docs/Word
  with headings and lists preserved (or the transcript as plain text).
- **Safe delete** — a preview of exactly what will be removed, gated behind a
  type-**delete**-to-confirm step.
- **Optional HTTPS** — one script generates a (trusted, with mkcert) cert.

## Architecture

| Component       | Where                  | Purpose                                                                    |
| --------------- | ---------------------- | -------------------------------------------------------------------------- |
| **app**         | container (port 8080)  | Web dashboard + REST API + pipeline orchestration                          |
| **Ollama**      | your host (port 11434) | Local LLM that writes the minutes (reached via `host.containers.internal`) |
| **Whisper STT** | external server        | OpenAI-compatible transcription API (set via `STT_BASE_URL`)               |
| **OBS Studio**  | your host              | Captures screen + audio; controlled by the app over OBS WebSocket          |

Only the **app** runs in a container; OBS, Ollama, and the STT server all run
outside it. The app reaches OBS and Ollama on your host via
`host.containers.internal`.

OBS runs on the **host** (not in a container) so it can capture your real
display and audio. The app talks to it via OBS WebSocket at
`host.containers.internal:4455`. Recordings, the database, and all generated
files live in **`~/MeetingMinutes/`** (mounted into the app container at
`/data`), so they are copied straight into your home directory.

## One-time setup

### 1. Enable the OBS WebSocket server

In OBS: **Tools → WebSocket Server Settings** → tick _Enable WebSocket server_.
Note the **port** (default `4455`) and the **password** if you set one.

### 2. Configure environment

```bash
cd ~/Documents/ai/quorum
cp .env.example .env
# Edit .env and set OBS_PASSWORD to match the OBS WebSocket password.
```

Set the transcription server in `.env`:

- `STT_BASE_URL` — OpenAI-compatible STT endpoint, **including `/v1`**
  (e.g. `http://linux-al-ml-beast.singhs:11435/v1`). The app posts audio to
  `{STT_BASE_URL}/audio/transcriptions`.
- `STT_MODEL` — a model id from the server's `/v1/models`
  (e.g. `Systran/faster-whisper-base`, or `Systran/faster-whisper-large-v3`
  for best accuracy). This is the _initial default_ — once running you can
  switch models from the dashboard's **⚙ Config** panel without editing `.env`.
- `STT_API_KEY` — only if the server requires auth.

> If the STT server is addressed by a hostname that only resolves on your host
> (like a `.singhs` name), pin it in `docker-compose.yml` under the app
> service's `extra_hosts`, or just use its IP in `STT_BASE_URL` — otherwise the
> container can't resolve it.

Set the LLM in `.env`:

- `OLLAMA_MODEL` — must exist in your host's `ollama list` (e.g. `gemma4:12b`).
  Like `STT_MODEL`, this is the initial default; the **⚙ Config** panel in the
  dashboard lets you change it at runtime (the choice is saved and survives
  restarts).
- `OLLAMA_URL` — defaults to `http://host.containers.internal:11434`; change
  only if Ollama runs on another machine. Ollama must listen on all interfaces
  for the container to reach it (`OLLAMA_HOST=0.0.0.0 ollama serve`).

### 3. Start everything

```bash
./start.sh
```

This checks that your host Ollama is up (and has the configured model), then
builds and starts the app container.

Open the dashboard at **http://localhost:8080**.

> If the browser jumps to `https://localhost/` and shows `ERR_CONNECTION_REFUSED`,
> that's Chrome/Brave auto-upgrading HTTP to HTTPS. Either open
> **http://127.0.0.1:8080** instead, or enable HTTPS (below).

### 4. (Optional) Serve over HTTPS

To avoid the browser's HTTP→HTTPS auto-upgrade entirely, generate a cert and the
app serves HTTPS on the same port automatically:

```bash
./gen-cert.sh                # prefers mkcert (trusted); falls back to self-signed
podman compose up -d --build # restart to pick up ./certs
```

Then open **https://localhost:8080**. With [mkcert](https://github.com/FiloSottile/mkcert)
the cert is locally trusted (no warning) — run `sudo mkcert -install` once if the
CA install was skipped. Without a cert in `./certs`, the app stays on plain HTTP.

## Using it

**Record a meeting**

1. Make sure OBS is running (the top-right indicator turns green when connected).
2. Click **Start recording** → OBS begins recording into `~/MeetingMinutes/recordings`.
3. Click **Stop & process** → the `.mkv` is registered as a new file in the table.
4. Pick a **Minutes detail** level (Brief / Standard / Detailed) and click **Run all**.

**Process an existing file**
Drop any `.mkv`, `.mp4`, `.mp3`, `.wav`, etc. into `~/MeetingMinutes/recordings`,
click **Scan folder**, then run the pipeline on it.

**Walk through stages**
Each row shows `source → mp3 → transcript → minutes` with live status
(pending / running / done / error). Run them individually with the per-row
buttons, or all at once. Click **view** to read the transcript and the
generated minutes in the side drawer. Under each filename a line shows the
recording's **duration · size · date** — the date is the media file's own
timestamp (its last-modified time on disk), so files scanned together still
show their real, distinct dates. Hover the **ⓘ** for the type and minutes level.

**Copy the minutes (formatted)**
In the **view** drawer, the **⧉ Copy** button copies the active tab. On the
**Minutes** tab it copies rich text, so pasting into **Google Docs** or **Word**
keeps the headings, bullet/numbered lists, and bold — no raw Markdown. The
**Transcript** tab copies as plain text. (Rich copy needs a secure context;
`http://localhost` qualifies, and over HTTPS or a LAN IP it still works via a
fallback.)

**Sort & find**
Use the **Sort** dropdown above the table to order by _Newest first_,
_Oldest first_ (by the file's own date), or _Name (A–Z / Z–A)_. The choice
sticks across auto-refresh.

**Choose models**
Click the **⚙** (top-right) to open **Config — models** and pick the Whisper STT
model and the Ollama model used for minutes. The lists are pulled live from each
server; your selection is saved and applied to every subsequent run.

**Delete a meeting**
Click the **🗑** on a row. A dialog previews exactly what will be removed — the
meeting's folder and each file inside it — and the **Delete permanently** button
stays disabled until you type **delete**. The backend only ever removes paths
inside `~/MeetingMinutes/recordings`, and won't delete a meeting that's still
recording.

## Minutes detail levels

- **Brief** — tight bullet summary + decisions + action items.
- **Standard** — summary, key discussion points, decisions, action items.
- **Detailed** — attendees, topic-by-topic breakdown, decisions w/ rationale,
  action items, risks/open questions, next steps.

## Where files live

Each meeting gets its own folder (named after the recording) holding the
source plus everything generated from it:

```
~/MeetingMinutes/
├── meetings.db                           # SQLite state (survives restarts)
└── recordings/
    └── 2026-06-08 10-00-00/
        ├── 2026-06-08 10-00-00.mkv       # OBS recording (source)
        ├── 2026-06-08 10-00-00.mp3       # extracted audio
        ├── 2026-06-08 10-00-00.transcript.txt
        └── 2026-06-08 10-00-00.minutes.standard.md
```

Files you drop loose into `recordings/` are moved into their own folder when
you hit **Scan folder**.

## Common commands

```bash
podman compose up -d --build     # start / rebuild
podman compose logs -f app       # tail app logs
podman compose ps                # status
podman compose down              # stop (data is kept on the host)
ollama list                      # see installed LLMs (on your host)
```

## Troubleshooting

- **"OBS not connected"** — OBS isn't running, the WebSocket server is off, or
  `OBS_PASSWORD` in `.env` is wrong. Restart the app after editing `.env`:
  `podman compose up -d`.
- **Stop says the file wasn't found** — OBS recorded somewhere outside
  `~/MeetingMinutes/recordings`. Set OBS's recording path there (Settings →
  Output → Recording), or the app will set it automatically on the next
  _Start_.
- **Transcription fails / can't connect** — check `STT_BASE_URL` is reachable
  from the container (`podman exec mm_app curl -s $STT_BASE_URL/models`). If the
  hostname won't resolve, add it to `extra_hosts` or use the IP. Use a smaller
  `STT_MODEL` (`...tiny`/`...base`) for faster results.
- **Minutes step errors / can't reach Ollama** — confirm Ollama is running on
  the host and listening on all interfaces (`OLLAMA_HOST=0.0.0.0 ollama serve`),
  the model in `OLLAMA_MODEL` is present (`ollama list`), and the container can
  reach it (`podman exec mm_app curl -s http://host.containers.internal:11434/api/tags`).
