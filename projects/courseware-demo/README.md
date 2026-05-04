# Courseware Demo

Minimal MVP demo for Courseware Mode.

- Source: `sources/七年级数学_一元一次方程.md`
- Extracted outline: `sources/course_outline.json`
- Courseware lock: `spec_lock.md`
- Layered SVG sample: `svg_output/01_objectives.svg`

Run:

```bash
python3 skills/ppt-master/scripts/extract_knowledge_points.py \
  projects/courseware-demo/sources/七年级数学_一元一次方程.md \
  -o projects/courseware-demo/sources/course_outline.json \
  --subject math --grade 七年级

python3 skills/ppt-master/scripts/svg_quality_checker.py projects/courseware-demo
```
