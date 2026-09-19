# Anonymization / camera-ready switch — DIPTYCH

Venue is **TBD** (see [`SUBMISSION.md`](SUBMISSION.md) §0). Do not invent a
conference name. Use this file when a chosen CFP requires double-blind review
or when restoring the named author block for camera-ready.

**Corresponding author (camera-ready):** Abhinav Pandey · `pandey.aby@gmail.com`  
**Co-author:** Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com`

**CI note:** GitHub Actions job `paper-pdf` builds the **named** (default)
author block only. Anonymization is a **local / pre-upload** dry-run — do
**not** fail CI if an anonymous PDF is not produced. Optional future job may
build anon as a non-blocking artifact; never gate merge on it.

HOLD for Abhinav merge yes before treating any anonymized PDF as submission-final.

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

Rebuild with `make paper` (or CI `paper-pdf` only after the switch is committed
on a pre-upload branch — default `main` stays named).

---

## 2. Exact dry-run steps (verified)

Verified locally on the living source (commented authors path works; rebuild
succeeds; PDF title block shows “Anonymous Author(s)” and does **not** contain
Abhinav / Abhishek / author emails). Header `%%` comments (including the public
GitHub URL) do not appear in PDF text.

```bash
# From repository root — work on a throwaway copy or revert after.
cp paper/one-trace-is-not-enough.tex /tmp/diptych-tex-named.backup

# 1) In paper/one-trace-is-not-enough.tex, under "% --- Author block switch":
#    - Wrap the named \author{...} block in %{ ... %} line comments
#      (or delete temporarily), AND
#    - Uncomment the anonymous \author{...} block (remove leading "% " on each line).

# 2) Rebuild
make paper
# or: cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex

# 3) Verify title block
pdftotext paper/one-trace-is-not-enough.pdf - | head -20
# expect: Anonymous Author(s) / Paper under double-blind review
# expect: NO "Abhinav" / "Abhishek" / pandey.aby@ / pandeyabhi

pdftotext paper/one-trace-is-not-enough.pdf - | rg -n 'Abhinav|Abhishek|pandey\.aby@|pandeyabhi' \
  && echo 'FAIL: deanonymizing string in PDF' || echo 'OK: no author-name leaks'

# 4) Restore named authors before commit (default tree stays named)
cp /tmp/diptych-tex-named.backup paper/one-trace-is-not-enough.tex
make paper   # restore named PDF locally
```

**Do not commit** the anonymous author switch unless preparing a double-blind
upload branch. Keep `main` / default PR drafts named unless Abhinav says otherwise.

---

## 3. Deanonymizing strings to redact for double-blind

| Item | Living draft | Double-blind action |
|------|--------------|---------------------|
| Author names / emails | Named `\author` block | Use anonymous block above |
| GitHub URL `pandeyaby/DIPTYCH` | Header `%%` comment only (not in PDF body today) | If added to visible text: “supplementary anonymous repo” or zip-only artifact |
| Personal emails in PDF | Author block | Keep contact in CMT/HotCRP only |
| Adapter pin SHAs | ZeroDay / AOMB commits | **May remain** (product commits, not author identity) |
| Self-citations that reveal identity | Avoid | Prefer third-person / omit |

---

## 4. Camera-ready restore

1. Restore named `\author` block (Abhinav corresponding).
2. Restore public artifact URL if redacted.
3. Confirm affiliation lines against the venue’s camera-ready checklist
   (`TODO` in [`SUBMISSION.md`](SUBMISSION.md) §0).
4. Do **not** invent AUROC / model scores while polishing.

---

## 5. Non-claims (unchanged under anonymity)

- No fabricated LLM / model scores; `tab:placeholder` stays `---`.
- No AUROC / `model_grade` in envelopes.
- `inconclusive` ≠ green.
- ZeroDay / AOMB are pins only; no product-tree edits in this repo.
