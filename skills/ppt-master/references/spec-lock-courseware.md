# Courseware Execution Lock Extension

Use this as an extension to `templates/spec_lock_reference.md` when generating
teen / K-12 courseware. Keep the standard `canvas`, `colors`, `typography`,
`icons`, `images`, `page_rhythm`, and `forbidden` sections. Add the sections
below.

## courseware
- mode: enabled
- subject: math
- grade_level: 七年级
- lesson_minutes: 40
- pedagogy: objectives -> concept -> example -> quiz -> summary
- cognitive_load: max_3_new_terms_per_slide

## knowledge_points
- KP01: 方程的定义 | easy | remember | 4min
- KP02: 等式的性质 | medium | understand | 6min
- KP03: 解一元一次方程 | hard | apply | 8min

## page_types
- P01: objectives | KP: none | difficulty: easy | bloom: understand | minutes: 2
- P02: concept | KP: KP01 | difficulty: easy | bloom: remember | minutes: 4
- P03: example | KP: KP01 | difficulty: medium | bloom: apply | minutes: 5
- P04: quiz | KP: KP01 | difficulty: medium | bloom: apply | minutes: 4
- P05: summary | KP: all | difficulty: easy | bloom: understand | minutes: 3

## layers
- required: layer-background, layer-content, layer-chrome
- optional: layer-context, layer-interaction, layer-guidance, layer-feedback
- reveal_order: layer-context, layer-content, layer-interaction, layer-guidance, layer-feedback
- feedback_layer_required_for: quiz, example
- raw_top_level_elements: forbidden

## courseware_forbidden
- More than 3 new terms on one concept slide
- More than 5 visible solving steps on one example slide
- Quiz slide without answer/explanation in `layer-feedback`
- Teaching objective that is not observable
- Student-facing page with teacher-only notes visible on canvas
