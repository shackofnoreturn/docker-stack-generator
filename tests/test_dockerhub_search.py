import importlib.util
import os
import unittest

from docker_stack_generator.dockerhub import format_search_result


class DockerHubResultFormattingTests(unittest.TestCase):
    def test_long_name_keeps_star_count_visible_and_tooltip_is_complete(self):
        image_name = "registry.example.org/a-very-long-image-repository-name/application"
        description = "A service image description that is longer than eighty characters and remains available in the tooltip."
        text, tooltip = format_search_result(
            {
                "name": image_name,
                "official": True,
                "star_count": 123456,
                "short_description": description,
            }
        )

        self.assertEqual(
            text.splitlines(),
            [image_name + " ✓", "★ 123,456", description[:80]],
        )
        self.assertIn(description, tooltip)


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "PyQt6 is not installed")
class DockerHubSearchResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from docker_stack_generator.service_dialog import ServiceDialog

        self.dialog = ServiceDialog()
        self.addCleanup(self.dialog.close)

    def test_long_image_names_wrap_and_star_count_has_separate_line(self):
        image_name = "registry.example.org/a-very-long-image-repository-name/application"
        self.dialog._on_search_done(
            [
                {
                    "name": image_name,
                    "official": False,
                    "star_count": 123456,
                    "short_description": "A service image",
                }
            ]
        )

        item = self.dialog._result_list.item(0)
        self.assertTrue(self.dialog._result_list.wordWrap())
        self.assertEqual(item.text().splitlines(), [image_name, "★ 123,456", "A service image"])
        self.assertIn(image_name, item.toolTip())
        self.assertIn("★ 123,456", item.toolTip())


if __name__ == "__main__":
    unittest.main()
