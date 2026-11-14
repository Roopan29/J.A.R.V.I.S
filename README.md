# PROJECT J.A.R.V.I.S.

A real, local-first Windows AI assistant: a cinematic JARVIS-style dashboard backed by a
modular Python engine that listens, speaks, reasons, and safely controls your computer.

Not a mock-up. Every button on the dashboard calls a working endpoint, every metric is read
from your actual hardware, and every computer-control action is validated, permission-gated
and audit-logged.

```
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
```

---

## 1. Quick start (Windows 10 / 11)

```bat
:: 1. Create the virtual environment (checks Python, copies .env)
scripts\setup_environment.bat

:: 2. Install dependencies, verify hardware, optionally pull an AI model
scripts\install.bat

:: 3. Launch
scripts\start_jarvis.bat
```

The dashboard opens at **http://127.0.0.1:8712/**.

| Script | Purpose |
|---|---|
| `scripts\setup_environment.bat` | Verifies Python 3.10–3.14 (3.11/3.12 recommended), creates `.venv`, copies `.env.example` → `.env`, reports audio devices |
| `scripts\install.bat` | Installs `requirements.txt`, runs a self-test, offers to pull an Ollama model and a Piper voice |
| `scripts\start_jarvis.bat` | Starts backend + dashboard. Flags: `--minimised`, `--headless`, `--diagnose`, `--voice-test` |
| `scripts\run_tests.bat` | Runs the automated test suite |

No multiple terminals, no manual `uvicorn` calls.

> **Native window:** add `--window` (or install `pywebview`) to run the dashboard inside a
> real desktop window instead of a browser tab.

---

## 2. Project location

Everything lives in this repository. At runtime JARVIS keeps your data **outside** the repo:

| What | Where |
|---|---|
| Config | `%APPDATA%\Jarvis\config.json` |
| Database (memory, notes, tasks, audit) | `%APPDATA%\Jarvis\data\jarvis.db` |
| Logs | `%APPDATA%\Jarvis\logs\jarvis.log` (rotating, 5 × 2 MB) |
| Screenshots | `%APPDATA%\Jarvis\cache\screenshots\` |
| Piper voices | `%APPDATA%\Jarvis\models\piper\` |
| API keys | `.env` or the OS credential store — **never** `config.json` |

Set `JARVIS_HOME` to relocate everything (useful for portable installs).

---

## 3. Architecture

```
jarvis/
  main.py              CLI entry: server, --diagnose, --voice-test, --window
  config.py            Typed settings, JSON persistence, env overrides, secrets access
  logging_setup.py     Rotating file + console logging
  event_bus.py         Async pub/sub; the spine between backend and dashboard
  database/            SQLite schema, connection-per-thread, repositories
  security/            Command validator, audit logger, secret storage
  core/
    ai_engine.py       Ollama / OpenAI-compatible / Anthropic / offline providers
    conversation.py    Intent routing → tool loop → memory extraction → speech
    intent_router.py   Deterministic fast paths (no model needed)
    tool_registry.py   Tool catalogue, permission enforcement, invocation
    permissions.py     Capability gates + human-in-the-loop confirmation
    tools/             69 tool definitions, grouped by domain
  computer/            app_control, file_manager, mouse_keyboard, window_manager,
                       system_control, powershell, screen_vision, platform_support
  intelligence/        web_search (Brave/DDG/Bing), memory, context_manager
  automation/          task_queue, task_executor (planner), custom_commands
  productivity/        reminders, timers, notes (notes + todos)
  voice/               wake_word (openWakeWord), speech_recognition (faster-whisper),
                       speech_output (Piper/SAPI5), voice_manager (orchestration)
  api/                 FastAPI server: REST + WebSocket, background metrics/health
  desktop/             Optional native window host (pywebview / WebView2)
  dashboard/           The cinematic UI (HTML/CSS/JS, no build step)
  tests/               119 automated tests
scripts/               Windows install/launch scripts
```

**Design principles**

- **Event-driven.** Modules publish to `event_bus`; the WebSocket layer fans events out to
  every connected dashboard. Nothing blocks the UI.
- **No fake data.** Metrics come from `psutil`, `nvidia-smi` and Win32. If a sensor is
  absent the UI says "n/a" — it never shows a plausible-looking lie.
- **Honest failure.** If Whisper, Piper, Ollama or the screen-capture permission is
  missing, JARVIS reports it plainly instead of silently degrading.
- **Single source of truth for tools.** The AI, the dashboard and custom commands all call
  the same registry, so permissions apply everywhere.

---

## 4. Configuring AI

JARVIS is **local-first**. Pick one:

### A. Ollama (recommended, fully offline)

```bat
:: install from https://ollama.com/download/windows, then:
ollama pull llama3.1          :: best tool-calling for 8B-class models (~4.7 GB)
ollama pull qwen2.5:7b        :: lighter alternative
ollama serve                  :: normally starts automatically
```

Settings → AI Provider = `ollama`, model `llama3.1`.

### B. Cloud provider (optional)

Any OpenAI-compatible endpoint works (OpenAI, Groq, OpenRouter, Together, LM Studio, vLLM):

```
# .env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
JARVIS_CLOUD_MODEL=gpt-4o-mini
```

Or enter the key in **Settings → AI → API key** — it is stored via `keyring` when available,
otherwise in an encrypted local store, and is **never** written to `config.json` or returned
to the browser (the API only exposes a boolean "configured" flag).

### C. No model

Set provider `none`. Voice commands that map to deterministic routes (open apps, timers,
system status, files, notes, reminders, volume, screenshots, PowerShell) still work. Free-form
conversation will tell you the model is offline — it will not invent an answer.

---

## 5. Configuring the microphone

1. **Settings → Voice Input → Microphone** — choose your device (defaults to the system mic).
2. **Whisper model** — `base` is a good CPU default; `small`/`medium` are more accurate but
   slower. The model downloads on first use (`%USERPROFILE%\.cache\huggingface`).
3. Test it: `scripts\start_jarvis.bat --voice-test` records 5 s, transcribes, and speaks back.
4. Permission: Windows Settings → Privacy → Microphone → allow desktop apps.

### Voice modes

| Mode | How |
|---|---|
| **Wake word** | Say **"Hey JARVIS"** → it replies with your greeting → say your command |
| **Push to talk** | Dashboard mic button, or the **Listen** button on the core |
| **Continuous** | Settings → or `POST /api/voice/start?mode=continuous` |
| **Hotkeys** | `Ctrl+Shift+M` push-to-talk, `Ctrl+Shift+X` emergency stop |

Wake word uses openWakeWord's pretrained `hey_jarvis` model. If it isn't installed, JARVIS
falls back to push-to-talk and tells you why.

### Speech output

Priority: **Piper** (neural, natural) → **SAPI5** (always present on Windows) → **espeak**.
Install a Piper voice via `scripts\install.bat`, or drop an `.onnx` + `.onnx.json` pair into
`%APPDATA%\Jarvis\models\piper\`. Adjust rate/volume in Settings. Speech is serialised — it
never overlaps — and "Stop speaking" interrupts mid-sentence.

---

## 6. Example commands

**Applications & windows**
```
Hey JARVIS                                        → "Yes, sir. How may I assist you?"
Open Chrome.
Launch Visual Studio Code.
Open Spotify and play something.
Close the active window.
Switch to the previous window.
Minimise all windows.
Focus Visual Studio Code.
```

**Files**
```
Create a folder called Projects on my desktop.
Create a text file with my meeting notes.
Find my latest PDF.
Search for files called project in my documents.
Show me files larger than 1 GB.
Find duplicate files in Downloads.
Preview a Downloads cleanup.
Rename report.txt to final-report.txt.
```

**System**
```
What is my CPU usage?
How much RAM is free?
Check my battery.
What is my IP address?
Show me the running processes.
Lock the computer.
Set volume to 40.
Set brightness to 60.
Take a screenshot.
[Settings → Security: allow power actions, then] Shut down the computer.
```

**Productivity**
```
Set a timer for 10 minutes.
Remind me to finish my project at 8 PM.
Create a note about my project ideas.
Add buy milk to my todo list.
Summarise my tasks for today.
```

**Intelligence**
```
Research the latest developments in artificial intelligence.
Find the best Python courses and compare them.
What's on my screen?                        (needs screen permission + a vision model)
Explain this error.                          (needs screen permission)
Remember that I prefer dark mode.
What do you remember about me?
```

**Automation**
```
Prepare my laptop for programming.           → plans & runs a multi-step workflow
Coding mode.                                 → custom command
```

**PowerShell** (Dashboard → System Control, or voice with the model)
```powershell
Get-Process | Sort-Object CPU -Descending | Select-Object -First 5
```

---

## 7. What is fully working

Verified by the automated suite and manual testing in this repository.

- ✅ Cinematic dashboard: animated AI core with 5 visual states, rotating rings, radial
  waveform, live per-core CPU bars, glass panels, scan lines, boot sequence, 8 pages
- ✅ Real telemetry every 2 s over WebSocket (CPU, per-core, RAM, GPU via nvidia-smi,
  battery, disks, network, uptime) — pushed live, never simulated
- ✅ Voice input: faster-whisper STT, energy/VAD utterance segmentation, device selection,
  push-to-talk, continuous mode, push-to-talk fallback when the wake word engine is absent
- ✅ Wake word "Hey JARVIS" via openWakeWord, with cooldown and graceful degradation
- ✅ Speech output: Piper and SAPI5 engines, queueing without overlap, interruptible
- ✅ AI: Ollama, OpenAI-compatible and Anthropic providers with native tool calling,
  5-round tool loop that feeds real results back to the model, context window, memory
  injection, and an honest offline mode
- ✅ Deterministic intent router (45+ command shapes) so common commands work instantly and
  offline — even with no language model
- ✅ 69 tools: apps, windows, files, input, clipboard, system, PowerShell, vision, web,
  memory, productivity, automation
- ✅ File operations with protected-path guards, Recycle Bin deletes, duplicate detection
  (SHA-256), dry-run organisers
- ✅ PowerShell module: 30+ blocklist rules, 15 obfuscation rules, confirmation for risky
  patterns, timeouts, output capture, full audit trail, never elevated
- ✅ Permission system: capability gates + async confirmation dialogs that never block the
  event loop, with timeout, "remember for this session", and voice announcements
- ✅ Persistent memory in SQLite with a sensitivity filter (passwords/keys refused), CRUD,
  enable/disable, and automatic fact extraction from conversation
- ✅ Automation: persistent task queue with progress, cancellation, retry/backoff; built-in
  and LLM-generated plans; custom voice commands
- ✅ Productivity: reminders (scheduler thread, repeats), timers (asyncio), notes, todos,
  daily summary
- ✅ Web intelligence: Brave API → DuckDuckGo → Bing fallback chain, page extraction,
  multi-source research
- ✅ Screen vision: capture, OCR, window inventory, vision-model analysis with OCR fallback
- ✅ Emergency stop: stops listening, stops speech, cancels tasks, denies pending approvals
- ✅ Proactive alerts: low battery, high CPU/RAM, low disk (rate-limited)
- ✅ Install scripts, self-test, diagnostics, launch-at-login (opt-in)
- ✅ 119 automated tests

---

## 8. What needs extra setup

Nothing here is faked — it just needs something you have to install or provide.

| Feature | Requirement |
|---|---|
| Conversation, research, multi-step AI planning | **Ollama** with a model, or a cloud API key |
| Wake word "Hey JARVIS" | `openwakeword` package (installed by `install.bat`); downloads a 2 MB model on first run |
| Natural voice | **Piper** voice model (~65 MB) — `install.bat` offers to download it |
| Speech recognition | `faster-whisper` (installed); first run downloads the model |
| Screen text reading (OCR) | **Tesseract** binary — https://github.com/UB-Mannheim/tesseract/wiki |
| Screen understanding ("explain this error") | A **vision-capable model**: `ollama pull llava`, or a `gpt-4o`-class cloud model |
| GPU metrics | An **NVIDIA** GPU with `nvidia-smi` on PATH (AMD/Intel GPUs are not reported) |
| Brightness control | Internal laptop panel (WMI); external monitors usually refuse |
| Precise volume control | `pip install pycaw comtypes` (install.bat pulls these on Windows) |
| Native desktop window | `pip install pywebview` + WebView2 runtime (present on Win11) |
| Launch at login | Opt-in toggle in Settings (writes `HKCU\...\Run`, no admin needed) |
| Calendar / email | **Not implemented.** No provider is configured, so no button pretends otherwise |

---

## 9. Security model

- **Local-first.** The only outbound traffic is to (a) your chosen AI provider, (b) search
  engines when you ask for web research, (c) model downloads during install.
- **No stealth.** Microphone and screen-capture state are shown in the UI. Screen capture is
  off by default and every capture is announced on the event bus.
- **Command validation.** PowerShell goes through a 30+ pattern blocklist (credential theft,
  Defender tampering, disk wipe, shadow-copy deletion, remote shells, fork bombs), 15
  obfuscation patterns (base64, hidden windows, download-and-execute), and a confirmation
  gate for 15 risky-but-legitimate patterns. Anything blocked is refused even with approval.
- **Path protection.** Writes/deletes under `C:\Windows`, `C:\Program Files`, `C:\ProgramData`
  and drive roots are refused outright — on any OS, via textual Windows-path matching.
- **Destructive actions need approval.** Delete (Recycle Bin by default), move, power
  actions, custom-command creation, memory deletion and PowerShell all prompt first. The
  prompt is a real `await`ed future with a timeout — silence means "no".
- **Never bypass UAC.** The PowerShell module refuses elevation requests entirely.
- **Audit trail.** Every tool call is written to SQLite and the log file with arguments,
  outcome and duration. View it in Settings → Audit Log or `GET /api/audit`.
- **Secrets.** Never in `config.json`, never logged, never sent to the browser; `keyring`
  when available, otherwise an encrypted local store documented as such.
- **Memory filter.** Passwords, API keys, tokens, PINs and card numbers are refused by
  `intelligence/memory.py` and skipped by conversation extraction.

### What JARVIS deliberately does not do

No keylogging, no credential extraction, no hidden/remote access, no screen recording
without permission, no security-software tampering, no UAC bypass, no arbitrary shell
without validation.

---

## 10. Troubleshooting

**`start_jarvis.bat` says Python was not found**
Install Python 3.11 or 3.12 from python.org and tick "Add python.exe to PATH".

**Install fails on Python 3.13 / 3.14**
Core features work there, but some optional packages (older `piper-tts`,
`pyautogui` builds, `uvloop`) have no wheels. `install.bat` now installs the core
stack first and skips optional extras with a warning, so JARVIS still runs with
voice output via SAPI5 and mouse/keyboard automation disabled. For the full
feature set, install Python 3.12 (you can keep 3.14 too — just delete `.venv`
and re-run `setup_environment.bat` with 3.12 on PATH first).

**Port 8712 already in use**
Another instance is running (the launcher detects this and just opens the dashboard).
Override with `start_jarvis.bat --port 9000` or set `JARVIS_PORT` in `.env`.

**"AI · offline" pill**
Run `ollama serve` and confirm `ollama list` shows your model. Check the exact model name in
Settings → AI. If you use a cloud provider, confirm the key is set (the key field shows a
preview, never the key itself).

**Microphone not detected**
Run `scripts\start_jarvis.bat --diagnose` — it lists input devices. Check Windows privacy
settings, then pick the device explicitly in Settings → Voice Input.

**Speech recognition is slow**
Use the `tiny` or `base` Whisper model; `medium`/`large-v3` are heavy on CPU.

**No voice output**
Check Settings → Speech Output → engine. SAPI5 always works on Windows. For Piper, verify a
`.onnx` file exists in `%APPDATA%\Jarvis\models\piper\`. `--voice-test` confirms the chain.

**Wake word never triggers**
Run `--diagnose` and look at `wake_word.detail`. If openWakeWord is missing, install it, or
use push-to-talk (`Ctrl+Shift+M`).

**"Screen capture is disabled"**
Settings → Security → Allow screen capture. This is off by default by design.

**OCR says Tesseract is missing**
Install it and add its folder to PATH, then restart JARVIS.

**A tool is denied unexpectedly**
Settings → Security. PowerShell, screen capture, file writes, app launching and power actions
are individually gated.

**Something else**
`%APPDATA%\Jarvis\logs\jarvis.log` (rotating) plus Dashboard → Activity Log have the details.
Run with `--debug` for verbose logging, and `--diagnose` for a capability report.

---

## 11. Development

```bat
.venv\Scripts\python -m pytest jarvis\tests -v     :: 119 tests
.venv\Scripts\python -m jarvis.main --diagnose     :: capability report
.venv\Scripts\python -m jarvis.main --debug        :: verbose logs
```

**Adding a tool** — one file in `jarvis/core/tools/`:

```python
from ..tool_registry import ToolSpec, registry
from ...database.models import ActionResult

registry.register(ToolSpec(
    name="my_tool",
    description="What it does — the AI reads this to decide when to call it.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]},
    handler=lambda path: ActionResult(ok=True, message=f"Handled {path}", verified=True),
    category="files",
    capability="allow_file_write",     # gate from Settings → Security
    risk="medium",
    requires_confirmation=False,
))
```

It is immediately available to the AI, the dashboard (`POST /api/tools/invoke`), and custom
commands. Add a test in `jarvis/tests/` that asserts its schema and a real behaviour.

**API** — interactive docs at `http://127.0.0.1:8712/api/docs`; live events at `ws://…/ws`.

---

## 12. Feature status at a glance

| Area | Status |
|---|---|
| Dashboard (8 pages, all controls wired) | Fully working |
| System telemetry | Fully working (real sensors) |
| Voice input / wake word | Working; needs `openwakeword` + a Whisper model download |
| Voice output | Working (SAPI5 everywhere; Piper natural voice when installed) |
| AI conversation & tool calling | Working with Ollama or a cloud key; explicit offline mode otherwise |
| Computer control (apps, files, windows, input) | Windows-only; honest refusal elsewhere |
| PowerShell (validated) | Windows-only |
| Screen vision | Capture + OCR working; semantic understanding needs a vision model |
| Memory | Fully working |
| Productivity | Fully working |
| Automation / planning | Working; LLM planning needs a model, built-in workflows don't |
| Web research | Working (DuckDuckGo needs no key) |
| Calendar / email integration | Not implemented |
| GPU metrics (non-NVIDIA) | Not supported |

---

*J.A.R.V.I.S. — Just A Rather Very Intelligent System.*
