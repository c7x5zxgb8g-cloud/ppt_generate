import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/ppt-master/scripts/svg_quality_checker.py"
SCRIPTS_DIR = SCRIPT.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
spec = importlib.util.spec_from_file_location("svg_quality_checker", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


SPEC_LOCK = """## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## colors
- bg: #FFFFFF
- text: #111111

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- body: 22

## courseware
- mode: enabled
- subject: math

## page_types
- P01: quiz | KP: KP01 | difficulty: medium | bloom: apply | minutes: 4

## layers
- required: layer-background, layer-content, layer-chrome
- feedback_layer_required_for: quiz, example
- raw_top_level_elements: forbidden
"""


class CoursewareLayerCheckerTests(unittest.TestCase):
    def test_courseware_mode_rejects_raw_top_level_elements(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            svg_dir = project / "svg_output"
            svg_dir.mkdir()
            (project / "spec_lock.md").write_text(SPEC_LOCK, encoding="utf-8")
            svg = svg_dir / "01_quiz.svg"
            svg.write_text(
                """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="80" font-family="Microsoft YaHei, Arial, sans-serif" font-size="22" fill="#111111">题目</text>
</svg>""",
                encoding="utf-8",
            )

            checker = module.SVGQualityChecker()
            result = checker.check_file(str(svg))

            self.assertFalse(result["passed"])
            self.assertTrue(any("raw top-level" in err for err in result["errors"]))
            self.assertTrue(any("layer-feedback" in err for err in result["errors"]))

    def test_courseware_mode_accepts_required_layers_and_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            svg_dir = project / "svg_output"
            svg_dir.mkdir()
            (project / "spec_lock.md").write_text(SPEC_LOCK, encoding="utf-8")
            svg = svg_dir / "01_quiz.svg"
            svg.write_text(
                """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<g id="layer-background"><rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/></g>
<g id="layer-content"><text x="80" y="80" font-family="Microsoft YaHei, Arial, sans-serif" font-size="22" fill="#111111">题目</text></g>
<g id="layer-feedback"><text x="80" y="160" font-family="Microsoft YaHei, Arial, sans-serif" font-size="22" fill="#111111">答案</text></g>
<g id="layer-chrome"><text x="1180" y="680" font-family="Microsoft YaHei, Arial, sans-serif" font-size="14" fill="#111111">P01</text></g>
</svg>""",
                encoding="utf-8",
            )

            checker = module.SVGQualityChecker()
            result = checker.check_file(str(svg))

            self.assertTrue(result["passed"], result["errors"])


if __name__ == "__main__":
    unittest.main()
