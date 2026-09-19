# AOMB → DIPTYCH (v0.2 full-8)

**Mandate:** Implement **all 8** operators as substantive fixtures (conforming+violating each). Not RESEED-only.

Order suggestion (does not relax the gate): RESEED, SCHEMAX, FREEZEDRY, SIGNFLIP, SATEXTEND, HISTSWAP, then TRAJSWAP+VARSCALE with `crn_closed_loop`.

Emit under `diptych_schema: "0.2"` probe-pair JSON (see CONTRACT.md).
No lab AUROC / no fabricated scores. Public ranking card = smoke narrative only.

CI must fail if coverage manifest lists any operator as stub/TODO/missing violating twin.
