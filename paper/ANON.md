# Anonymization / camera-ready switch — DIPTYCH

Venue is **TBD** (see [`SUBMISSION.md`](SUBMISSION.md)). Do not invent a
conference name. Use this file when a chosen CFP requires double-blind review
or when restoring the named author block for camera-ready.

**Corresponding author (camera-ready):** Abhinav Pandey · `pandey.aby@gmail.com`  
**Co-author:** Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com`

---

## 1. Switch in `one-trace-is-not-enough.tex`

Near the top of the living source, after `\begin{document}`:

1. **Named (default / camera-ready / single-blind):** leave the `\author{...}`
   block with Abhinav + Abhishek as committed.
2. **Double-blind:** comment out the named `\author{...}` block and uncomment
   the anonymous block immediately below it (already present as LaTeX comments):

```latex
% \author{
% 	\IEEEauthorblockN{Anonymous Author(s)}
% 	\IEEEauthorblockA{Paper under double-blind review\\
% 		Anonymous Institution(s)\\
% 		Correspondence via conference submission system only}
% }
```

Rebuild with `make paper` (or CI `paper-pdf`).

---

## 2. Deanonymizing strings to redact for double-blind

| Item | Living draft | Double-blind action |
|------|--------------|---------------------|
| Author names / emails | Named `\author` block | Use anonymous block above |
| GitHub URL `pandeyaby/DIPTYCH` | Header comment, abstract-adjacent | “supplementary anonymous repo” or zip-only artifact |
| Personal emails in PDF | Author block | Keep contact in CMT/HotCRP only |
| Adapter pin SHAs | ZeroDay / AOMB commits | **May remain** (product commits, not author identity) |
| Self-citations that reveal identity | Avoid | Prefer third-person / omit |

---

## 3. Camera-ready restore

1. Restore named `\author` block (Abhinav corresponding).
2. Restore public artifact URL if redacted.
3. Confirm affiliation lines against the venue’s camera-ready checklist.
4. Do **not** invent AUROC / model scores while polishing.

---

## 4. Non-claims (unchanged under anonymity)

- No fabricated LLM / model scores; `tab:placeholder` stays `---`.
- No AUROC / `model_grade` in envelopes.
- `inconclusive` ≠ green.
- ZeroDay / AOMB are pins only; no product-tree edits in this repo.

HOLD for Abhinav merge yes before treating any anonymized PDF as submission-final.
