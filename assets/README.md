# Hermes Family Doctor hero — visual receipt

## Outcome

PASS. Original deterministic technical infographic for the public GitHub README. The image presents the project as a local-first safety coordinator, not an autonomous doctor or diagnosis system.

## Artifacts

- `hermes-family-doctor-hero.png` — release image, 1600×900 PNG.
- `hermes-family-doctor-hero.svg` — editable vector source, 1600×900 viewBox.

## Visual direction

- Dark clinical navy/charcoal canvas.
- Cyan/teal system rail and control surfaces.
- Restrained red accent only on triage and medication stop cues.
- Four-stage left-to-right workflow:
  1. `TRIAGE`
  2. `ISOLATED PATIENT CARD`
  3. `CONFIRMED WRITE`
  4. `MEDICATION + EVIDENCE GATES`
- Main descriptor: `LOCAL-FIRST SAFETY COORDINATOR`.

The sequence is grounded in the public repository contract: urgency screening before record access, one isolated patient namespace, preview/confirmation before durable writes, and medication/evidence safety gates.

## Jesse Visual OTK

Verdict: **PASS**

- First focus: product title, then the four connected safety stages.
- Hierarchy: title remains readable at 400×225; stage icons and ordering remain distinct.
- Typography: all visible phrases were checked after rasterization; no generated or pseudo text.
- Crop/safe zones: no clipping at 1600×900, 800×450, or 400×225.
- Privacy: no names, patient data, identifiers, local paths, chat IDs, or private provenance.
- Claims: no diagnosis, prescription, clinical-validation, or autonomous-doctor claim.
- Visual quality: controlled engineering aesthetic; no face, stock medical scene, brand imitation, or generic AI clutter.

## Verification

- SVG XML parse: PASS.
- PNG decode: PASS.
- PNG dimensions: 1600×900.
- Full-canvas bounding box: 1600×900.
- GitHub-width preview: 800×450, PASS.
- Small preview: 400×225, PASS.
- Portable CoreSVG render: PASS with no unsupported-filter warnings.
- Repository test suite: PASS with `PYTHONPATH=src python -m pytest -q` (124 tests).
- Privacy scan on `assets/`: PASS, zero findings.
- Final PNG SHA-256: `947f4ad0e6d85b0210323d889599e9ce524533ec0f0f38ab84cbc0efce442017`.
- Final SVG SHA-256: `ba09ed0489388dd977f50ff2a08787b1150d83bcb5c74ac133a11993126dbaff`.

## README placement

Recommended near the top of the root README, directly after the H1 and before the opening product description:

```html
<p align="center">
  <img src="assets/hermes-family-doctor-hero.png" alt="Hermes Family Doctor local-first safety workflow: triage, isolated patient card, confirmed write, and medication plus evidence gates" width="100%">
</p>
```

Keep the existing textual safety disclaimer visible immediately after the hero. Do not use the image as a replacement for the disclaimer.

## Changed state and rollback

Only new files under `assets/` were created. No README, code, config, database, profile, Git history, remote, release, or public state was changed.

Rollback: remove the three new `assets/` files before integration. No service restart or data migration is involved.

## Remaining risk

The compact stage labels are secondary at very small embed widths, but the title and four-stage structure remain legible at the checked 400×225 preview. Keep the README image at full content width for best label readability.
