import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/ppt-master/scripts/extract_knowledge_points.py"
spec = importlib.util.spec_from_file_location("extract_knowledge_points", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ExtractKnowledgePointsTests(unittest.TestCase):
    def test_extracts_course_outline_and_page_plan(self):
        text = """# 七年级数学 一元一次方程

## 方程的定义
含有未知数的等式叫方程。

## 等式的性质
等式两边同时加减同一个数，结果仍然相等。

## 解方程的步骤
通过去括号、移项、合并同类项解决问题。
"""
        outline = module.extract_knowledge_points(text, subject="math", grade_level="七年级", max_points=5)

        self.assertEqual(outline.subject, "math")
        self.assertEqual(outline.grade_level, "七年级")
        self.assertGreaterEqual(len(outline.knowledge_graph), 3)
        self.assertNotEqual(outline.knowledge_graph[0].title, "七年级数学 一元一次方程")
        self.assertEqual(outline.page_plan[0].page_type, "objectives")
        self.assertEqual(outline.page_plan[-1].page_type, "summary")
        self.assertTrue(any(page.page_type == "example" for page in outline.page_plan))

    def test_assigns_quiz_after_three_knowledge_points(self):
        points = [
            module.KnowledgePoint(id=f"KP{i:02d}", title=f"知识点{i}", description="描述", difficulty="medium")
            for i in range(1, 5)
        ]
        plan = module.assign_page_types(points)

        self.assertTrue(any(page.page_type == "quiz" for page in plan))


if __name__ == "__main__":
    unittest.main()
