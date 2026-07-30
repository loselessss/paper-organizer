# Real-paper score history

This directory keeps only sanitized benchmark measurements that are safe to
commit. The private corpus remains under the ignored `tests/Real paper` and
`tests/Real` directories.

Committed score files may contain:

- anonymous paper IDs and broad `research`/`review` document type;
- model IDs;
- success status, deterministic score and bibliography score;
- elapsed time, token counts and CPU/GPU processor label;
- a non-identifying hardware description.

Never add PDF paths, paper titles, ground-truth claims, prompts, raw model
outputs, API keys or user-specific filesystem paths here.
