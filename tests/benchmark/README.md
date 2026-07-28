# Synthetic Scientific Paper Summary Benchmark

This package contains nine entirely fictional scientific papers for evaluating PDF extraction, OCR, section detection, factual summarization, and hallucination control. Every page is marked **SYNTHETIC BENCHMARK DOCUMENT - NOT A REAL PUBLICATION**.

## Composition

- `BENCH-SYN-001` to `003`: clean, text-layer PDFs.
- `BENCH-SYN-004` to `006`: clean PDFs with deliberate semantic distractors, critical negations, unused pilot conditions, or speculative discussion.
- `BENCH-SYN-007` to `009`: image-only degraded scans requiring OCR.

## Files

- `pdfs/`: benchmark papers.
- `ground_truth/`: one JSON answer key per paper.
- `manifest.json`: index.
- `tools/score_output.py`: deterministic lexical smoke scorer for one output.
- `tools/run_models.py`: Paper Organizer's real section/OCR/JSON pipeline runner for
  installed Ollama models.

## Run 0.6B / 1.7B / 4B / 8B at home

The runner never downloads or deletes a model. Install the desired models in Ollama
first, then run from the repository root:

```powershell
git submodule update --init
.\.venv\Scripts\python -m pip install -e ".[gui,build]"
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull qwen3:4b
ollama pull qwen3:8b
.\.venv\Scripts\python tests\benchmark\tools\run_models.py --resume
```

The default is a full, source-language run over all nine papers. Useful tuning
comparisons include:

```powershell
# Fast smoke run on one clean and one OCR paper
.\.venv\Scripts\python tests\benchmark\tools\run_models.py `
  --models qwen3:0.6b qwen3:1.7b qwen3:4b qwen3:8b `
  --documents BENCH-SYN-001 BENCH-SYN-008 `
  --mode quick --resume

# Compare Korean output under the same context policy
.\.venv\Scripts\python tests\benchmark\tools\run_models.py `
  --language ko --resource-profile balanced `
  --output tests\benchmark\results-ko --resume
```

Results are written to the selected output directory:

- `model_summary.csv`: model-level success, speed, coverage and forbidden-claim totals;
- `comparison.csv`: one row per model/document;
- `<model>/<document>.json`: exact preview, sections, token counts, score and model JSON;
- `run.json`: combined machine-readable result.

`results/` is ignored by Git. The reported runner peak memory covers the Python
preprocessor/OCR process, not the separate Ollama process; use Windows Task Manager
or another system monitor when comparing total RAM/VRAM. OCR papers require the
`build` optional dependencies. A failed model/document is recorded and the remaining
matrix continues.

This corpus is appropriate for prompt, context-window and model-selection tuning.
Nine synthetic papers are far too few for a production weight fine-tune; keep a
separate validation set before attempting parameter training.

Paper Organizer automatically uses section-summary → final-summary hierarchy for
0.6B, 1.7B and 4B Ollama models. It uses one direct pass over all cleaned sections
for 8B and larger models. Contributions and limitations are deliberately disabled
below 8B, so compare small models on factual coverage, negation preservation,
forbidden claims, speed and memory rather than those two fields.

## Recommended evaluation

Score at least these dimensions separately:

1. title and study identity accuracy;
2. research-question recovery;
3. method recovery;
4. numerical finding accuracy;
5. critical-negation preservation;
6. unsupported-claim count;
7. reference-section contamination;
8. processing time, peak RAM, and timeout rate.

The JSON keys `forbidden_claims` and `critical_negations` are intentionally important. A fluent summary that violates them should fail.

## Suggested prompt contract

Ask the model to report only information explicitly present in the document, separate results from discussion/speculation, and write `Not available` when a requested category is absent. For small local models, section-level extraction followed by evidence-grounded integration is recommended.

## Use notice

The papers, authors, institutions, data, and references are synthetic. They may be used, modified, and redistributed for benchmarking. Do not present them as real research or cite them as publications.
