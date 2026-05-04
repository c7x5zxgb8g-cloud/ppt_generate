# Courseware Layer Model

Courseware slides must be layered. A flat pile of text boxes and shapes is not
acceptable for teaching, because teachers need staged reveal, animation anchors,
quick editing, and clear separation between instruction, activity, and feedback.

Use this model whenever `courseware.mode: enabled` appears in `spec_lock.md`, or
when the user asks for youth education / K-12 / classroom courseware.

## 1. Required Top-Level SVG Structure

Every courseware SVG must use top-level `<g id="layer-...">` groups. Direct
children of `<svg>` should be `<defs>` plus layer groups only.

Canonical order:

```xml
<svg width="1280" height="720" viewBox="0 0 1280 720" ...>
  <defs>...</defs>
  <g id="layer-background">...</g>
  <g id="layer-context">...</g>
  <g id="layer-content">...</g>
  <g id="layer-interaction">...</g>
  <g id="layer-guidance">...</g>
  <g id="layer-feedback">...</g>
  <g id="layer-chrome">...</g>
</svg>
```

Only include layers that carry content on the page, except:

- `layer-background` is mandatory
- `layer-content` is mandatory
- `layer-chrome` is mandatory

## 2. Layer Semantics

| Layer | Purpose | Typical Elements |
| ----- | ------- | ---------------- |
| `layer-background` | Page base and low-salience atmosphere | page bg, subtle texture, full-bleed image |
| `layer-context` | Scenario or prior-knowledge setup | story scene, question stem, source excerpt |
| `layer-content` | Main teaching content | definition, concept map, example steps |
| `layer-interaction` | Student action surface | quiz options, discussion prompt, blank response area |
| `layer-guidance` | Teacher / scaffolding cues | hints, arrows, key-step highlight |
| `layer-feedback` | Answer and explanation | correct answer, misconception note, reveal panel |
| `layer-chrome` | Persistent UI | title, section label, page number, footer |

## 3. Nested Group Rules

Inside each layer, group every editable teaching unit:

```xml
<g id="layer-content">
  <g id="concept-definition">...</g>
  <g id="key-points">...</g>
</g>
```

Do not put the entire content layer into one anonymous group. Names must describe
the teaching unit, not the visual primitive.

## 4. Animation / Reveal Guidance

- Animate layers in teaching order: context → content → interaction → guidance → feedback.
- `layer-feedback` should be last and independently selectable.
- Keep `layer-background` and `layer-chrome` visible from slide start.
- For quiz pages, place answer/explanation only in `layer-feedback`.
- For example pages, place the problem in `layer-context`, steps in `layer-content`, and key-step hints in `layer-guidance`.

## 5. Compatibility With Existing PPT Master

The layer groups are plain SVG `<g>` elements, so the existing converter treats
them as PowerPoint groups. The model adds structure without requiring a new PPTX
backend.

Existing top-level semantic groups are still allowed inside a layer. What changes
in courseware mode is that raw top-level primitives are forbidden.

## 6. Minimal Layer Checklist

Before running post-processing, every courseware SVG should answer:

- Does it have `layer-background`, `layer-content`, and `layer-chrome`?
- Are student actions isolated in `layer-interaction`?
- Are answers/hints isolated in `layer-feedback` or `layer-guidance`?
- Can the teacher delete or reorder a teaching unit without hunting through raw shapes?
- Does the top-level order match the intended reveal order?
