# `data_example/`

This directory contains sanitized example data for public GitHub display.

It is meant to show the expected file shapes used by the runtime without exposing personal information, private logs, or local state.

Suggested usage:

1. Copy the files you need into `data/`
2. Replace the example values with your own
3. Keep your real `data/` contents local and untracked

Example mapping:

- `data_example/profile.json` -> `data/profile.json`
- `data_example/job_targets.md` -> `data/job_targets.md`
- `data_example/resume.md` -> `data/resume.md`
- `data_example/memory.json` -> `data/memory.json`

Notes:

- The real repo should not commit personal resumes, private contact details, SQLite runtime files, logs, traces, screenshots, or approval state.
- `config.yaml` is also local-only in the current setup. Consider adding a separate `config.example.yaml` if you want a fuller public template.
