import importlib.util
import os
import unittest

from docker_stack_generator.dockerhub import format_search_result, sort_search_results


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


class DockerHubResultSortingTests(unittest.TestCase):
    def setUp(self):
        self.results = [
            {"name": "zeta", "star_count": 5, "pull_count": 20},
            {"name": "Alpha", "star_count": 50, "pull_count": 10},
            {"name": "beta", "star_count": 5, "pull_count": 30},
        ]

    def test_sorts_by_stars_pulls_and_case_insensitive_name(self):
        self.assertEqual(
            [result["name"] for result in sort_search_results(self.results, "stars")],
            ["Alpha", "zeta", "beta"],
        )
        self.assertEqual(
            [result["name"] for result in sort_search_results(self.results, "pulls")],
            ["beta", "zeta", "Alpha"],
        )
        self.assertEqual(
            [result["name"] for result in sort_search_results(self.results, "name")],
            ["Alpha", "beta", "zeta"],
        )

    def test_relevance_and_unknown_modes_preserve_original_order(self):
        self.assertEqual(sort_search_results(self.results, "relevance"), self.results)
        self.assertEqual(sort_search_results(self.results, "unknown"), self.results)
        self.assertEqual([result["name"] for result in self.results], ["zeta", "Alpha", "beta"])


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

    def test_sort_selector_reorders_visible_results(self):
        from PyQt6.QtCore import Qt

        results = [
            {"name": "zeta", "star_count": 5, "pull_count": 20},
            {"name": "Alpha", "star_count": 50, "pull_count": 10},
            {"name": "beta", "star_count": 5, "pull_count": 30},
        ]
        self.dialog._on_search_done(results)

        def visible_names():
            return [
                self.dialog._result_list.item(index).data(Qt.ItemDataRole.UserRole)["name"]
                for index in range(self.dialog._result_list.count())
            ]

        self.assertEqual(visible_names(), ["zeta", "Alpha", "beta"])
        self.dialog._sort_combo.setCurrentIndex(self.dialog._sort_combo.findData("stars"))
        self.assertEqual(visible_names(), ["Alpha", "zeta", "beta"])
        self.dialog._sort_combo.setCurrentIndex(self.dialog._sort_combo.findData("pulls"))
        self.assertEqual(visible_names(), ["beta", "zeta", "Alpha"])


if __name__ == "__main__":
    unittest.main()

