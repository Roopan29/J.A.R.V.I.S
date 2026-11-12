# Developer tools

| File | Purpose |
|---|---|
| `report_capabilities.py` | Prints a short capability summary from `--diagnose` output (used by `install.bat`). |
| `dashboard_smoke_test.js` | Headless UI test: boots the real dashboard scripts against a running server and exercises every page and control. Catches runtime JS errors that a syntax check cannot. |

## Running the dashboard smoke test

```bash
npm install jsdom                      # once
python -m jarvis.main --no-browser &   # start JARVIS
node tools/dashboard_smoke_test.js http://127.0.0.1:8712
```

It reports `N/N checks passed` and exits non-zero on any failure.
