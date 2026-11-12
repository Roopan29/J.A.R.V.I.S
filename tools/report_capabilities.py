"""Print a short capability summary (used by install.bat after the self-test)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main(path: str) -> int:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        print("    (could not parse diagnostics)")
        return 1
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        print("    (diagnostics were not valid JSON)")
        return 1

    def line(label: str, value) -> None:
        print(f"    {label:<18} {value}")

    line("tools", data.get("tools"))
    line("microphones", len(data.get("microphones") or []))
    engines = (data.get("speech_engines") or {}).get("engines", {})
    line("speech engines", ", ".join(k for k, v in engines.items() if v) or "none")
    line("powershell", data.get("powershell_available"))
    caps = data.get("capabilities") or {}
    line("screen capture", caps.get("screen_capture"))
    line("OCR", caps.get("ocr"))
    line("nvidia GPU", caps.get("nvidia_gpu"))
    line("AI provider", f"{data.get('ai_provider')} / {data.get('ai_model')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "jarvis_diag.txt"))
