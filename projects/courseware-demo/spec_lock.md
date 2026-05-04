## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## colors
- bg: #F7FBFF
- primary: #2563EB
- accent: #F59E0B
- text: #111827
- text_secondary: #4B5563
- border: #BFDBFE
- success: #16A34A
- white: #FFFFFF

## typography
- font_family: Microsoft YaHei, PingFang SC, Arial, sans-serif
- body: 24
- title: 40
- subtitle: 30
- annotation: 17

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
- KP03: 移项法则 | medium | apply | 6min
- KP04: 解一元一次方程的步骤 | hard | apply | 8min
- KP05: 应用题建模 | hard | analyze | 8min

## page_types
- P01: objectives | KP: none | difficulty: easy | bloom: understand | minutes: 2

## layers
- required: layer-background, layer-content, layer-chrome
- optional: layer-context, layer-interaction, layer-guidance, layer-feedback
- reveal_order: layer-context, layer-content, layer-interaction, layer-guidance, layer-feedback
- feedback_layer_required_for: quiz, example
- raw_top_level_elements: forbidden

## page_rhythm
- P01: anchor

## forbidden
- Mixing icon libraries
- rgba()
- `<style>`, `class`, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<script>`, `<iframe>`, `<symbol>`+`<use>`
- `<g opacity>` (set opacity on each child element individually)
