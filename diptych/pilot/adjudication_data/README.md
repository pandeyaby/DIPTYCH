# Blinded adjudication data (paper §VIII-D)

Five model-based raters (three Claude Opus 5.5, two Claude Sonnet) each judged
the same 42 (controller, requirement) items, blind to every DIPTYCH verdict.
A sixth seat planned for Fable 5.1 could not run (no usage credits) and was
re-run on Opus. **These are model raters, not people:** this is an independent
re-judgment of the ground truth, not human validation.

| File | Contents |
|------|----------|
| `KEY.json` | Item id → task, requirement, category, and the paired / basic / strong verdicts (never shown to raters) |
| `answers_r1.json` … `answers_r5.json` | Each rater's verdict, confidence, method, and rationale per item |
| `SCORE.json` | Fleiss' kappa, per-category agreement, per-item votes and majority |

Rebuild the packets (deterministic, seed 2026) and re-score:

```bash
python -m diptych.pilot.adjudication build --out /tmp/adj --raters 5
cp diptych/pilot/adjudication_data/answers_r*.json ...   # one per packet-r*/answers.json
python -m diptych.pilot.adjudication score --out /tmp/adj
```

Each rater worked only inside its own packet (task text, one requirement, the
controller, the task simulator). An audit of all 185 rater tool calls found no
access to the repository, the key, or another rater's packet.
