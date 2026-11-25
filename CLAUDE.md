# ShiftSense Engineering Rules (CLAUDE.md)

## Non-negotiable architecture rules
1. **Zero LLM maths.** Never ask the Claude API to calculate wages, Employer NI, holiday accrual, overtime,
   rest gaps or totals. All calculations live in deterministic Python modules under `engine/`, with the
   statutory numbers in `config/`, and are covered by `pytest`. Claude receives only the compact JSON from
   `engine/claude_brief.py` (`build_verified_payload`, `build_india_payload`; budget under 1,500 tokens)
   and is told never to calculate or invent figures.
2. **UI isolation.** `app.py` is only the shell (page setup, region toggle, footer). Screens live in `ui/`
   and do presentation and state management only: no statutory maths or aggregation pipelines there.
3. **API security and cost control.** Read `ANTHROPIC_API_KEY` only from `st.secrets` or `os.environ`
   (`engine/claude_brief.get_api_key`). Never log or display keys. Never call Claude on page load or widget
   change: only on an explicit button click, through `ui/ai_panel.generate`, which caches by the SHA-256 of
   the payload and enforces `AI_CALLS_PER_SESSION` (default 5).
4. **Time zones and overnight shifts.** All shift timestamps are timezone-aware (`Europe/London` for UK,
   `Asia/Kolkata` for India). If an end time is at or before its start time, the shift ends the next day.
   Durations and rest gaps are measured in real elapsed time. Always sort shifts per `employee_id` across ALL
   sites before checking rest gaps.
5. **Graceful errors.** Never show a raw traceback in Streamlit. Ingestion problems become
   `IngestionError` (whole file) or rows in the issues table (single rows); API problems become `AIError`.
   Both carry plain-English messages, and the UI falls back to the template brief.
6. **Fairness.** The optimiser moves only overtime hours, never takes anyone below their contract, and never
   uses age, NI status or other protected characteristics to choose who gets hours
   (`tests/test_optimizer.py::test_age_is_never_used_to_choose_recipients`).
7. **Testing gate.** After any change to `engine/` or `config/`, run `pytest -v` and confirm 100% pass before
   finishing. Lint with `python -m pyflakes app.py ui engine config tests` (zero unused imports).

## Saved AI examples (free public demo)
`sample_data/saved_ai_examples.json` holds Claude-written examples keyed by the SHA-256 of the demo payloads. If a change alters those payloads, `tests/test_saved_examples.py` fails: rewrite the examples from the new payload (only figures present in it) and update the hashes. Never present a saved example as live output.

## Updating statutory rates
- UK: add a new `UKRates` instance in `config/uk_rates.py` (minimum wage changes 1 April, NI/SSP 6 April),
  add it to `RATE_TABLES`, cite the GOV.UK source in the module docstring, and add boundary tests.
- India: edit `config/india_rates.py` presets; keep the `note` honest about how sure we are.

## Layout
- `config/` statutory constants (versioned) · `engine/` all maths · `ui/` screens · `sample_data/` demo rota
  (regenerate with `python -m engine.uk_demo`) · `tests/` pytest suite.
