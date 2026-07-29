# Synthetic Scientific Paper Summary Benchmark

This package contains nine entirely fictional scientific papers. The model benchmark
uses the six text-layer papers to evaluate section detection, factual summarization,
and hallucination control. Every page is marked **SYNTHETIC BENCHMARK DOCUMENT - NOT
A REAL PUBLICATION**.

## Composition

- `BENCH-SYN-001` to `003`: clean, text-layer PDFs.
- `BENCH-SYN-004` to `006`: clean PDFs with deliberate semantic distractors, critical negations, unused pilot conditions, or speculative discussion.
- `BENCH-SYN-007` to `009`: legacy image-only OCR fixtures, excluded from model
  benchmarking.

## Files

- `pdfs/`: benchmark papers.
- `ground_truth/`: one JSON answer key per paper.
- `manifest.json`: index.
- `scoring_rubric.json`: shared 100-point rubric and penalty thresholds.
- `paper_scorecard.csv`: maximum points assigned to every benchmark paper.
- `tools/score_output.py`: deterministic lexical smoke scorer for one output.
- `tools/run_models.py`: Paper Organizer's real section/JSON pipeline runner for
  installed Ollama models.

The runner follows Paper Organizer's saved GPU-first policy, lets Ollama fall back
to CPU, and records the actual `GPU`, `CPU`, or mixed placement reported by
`/api/ps` with each result.

The frontier reference is the canonical answer key with evaluation-only
`forbidden_claims` and `scoring_notes` removed. It scores 100/100 on every paper;
model scores therefore read directly as a percentage of that reference. A model
that translates fields despite `--language source` loses lexical fidelity points,
which is intentional because preserving the requested output language is part of
the benchmark.

## Run Qwen3 and cross-family 3B/4B candidates

The runner never downloads or deletes a model. When the ignored `tests/Real`
workspace is present, the default run uses those private user papers and keeps
their outputs outside Git. Install the desired models in Ollama first, then run
from the repository root:

```powershell
git submodule update --init
.\.venv\Scripts\python -m pip install -e ".[gui,build]"
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull granite3.3:2b
ollama pull ministral-3:3b-instruct-2512-q4_K_M
ollama pull phi4-mini
ollama pull qwen3:4b
ollama pull gemma3:4b-it-qat
ollama pull qwen3:8b
.\.venv\Scripts\python tests\benchmark\tools\run_models.py --resume
```

The default is a full, source-language run over the private papers. Pass
`--synthetic` explicitly to use the six repository text-layer fixtures. OCR quality
is not part of model selection. Useful tuning comparisons include:

```powershell
# Fast smoke run on two text-layer papers
.\.venv\Scripts\python tests\benchmark\tools\run_models.py `
  --synthetic `
  --models phi4-mini gemma3:4b-it-qat ministral-3:3b-instruct-2512-q4_K_M `
  --documents BENCH-SYN-001 BENCH-SYN-004 `
  --mode quick --resume

# Compare Korean output under the same context policy
.\.venv\Scripts\python tests\benchmark\tools\run_models.py `
  --synthetic `
  --language ko --resource-profile balanced `
  --output tests\benchmark\results-ko --resume
```

Results are written to the selected output directory:

- `model_summary.csv`: model-level success, speed, coverage and forbidden-claim totals;
- `recommendation.json`: observed quality, speed and completion-rate ranking plus
  the recommended model;
- `comparison.csv`: one row per model/document;
- `paper_scores.csv`: the 100-point score and category breakdown for every
  model/document pair;
- `<model>/<document>.json`: exact preview, sections, token counts, score and model JSON;
- `run.json`: combined machine-readable result.

`results/` is ignored by Git. The reported runner peak memory covers the Python
preprocessor process, not the separate Ollama process; use Windows Task Manager
or another system monitor when comparing total RAM/VRAM. A failed model/document
is recorded and the remaining matrix continues.

Every paper uses the same 100-point rubric: title 10, research question 10,
methods 15, key findings 25, numerical findings 15, critical negations 20, and
conclusion 5. Each detected forbidden claim deducts 15 points, down to a minimum
of zero. Category points are divided evenly among that paper's answer-key items.
This deterministic lexical score is intended for repeatable smoke comparisons;
borderline outputs should still receive human semantic review.

The recommendation is based on measured runs, not the static model catalog.
`eco` gives speed more weight, `balanced` favors quality while retaining a speed
penalty, and `performance` gives quality the highest weight. A model must finish
at least 80% of the selected papers and average at least 50/100 to be eligible.
The hardware catalog remains a safety filter for RAM and disk capacity; it is not
treated as evidence of summary quality.

This corpus is appropriate for prompt, context-window and model-selection tuning.
Nine synthetic papers are far too few for a production weight fine-tune; keep a
separate validation set before attempting parameter training.

Paper Organizer automatically uses plain-text section evidence → structured
final-summary hierarchy for 0.6B, 1.7B and 4B Ollama models. It uses one direct
structured pass over all cleaned sections for 8B and larger models. Contributions
and limitations are deliberately disabled below 8B, so compare small models on
factual coverage, negation preservation, forbidden claims, speed and memory rather
than those two fields.

`comparison.csv` also reports a separate bibliography score for exact title,
authors, year, and venue matches. Only fields present in each ground-truth record
are included, so this score does not change the existing 100-point summary rubric.

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
