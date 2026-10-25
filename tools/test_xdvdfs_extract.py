import unittest

from xdvdfs_extract import safe_components


class SafeComponentsTest(unittest.TestCase):
    def test_accepts_normal_nested_path(self):
        self.assertEqual(safe_components("data/teams.bin"), ["data", "teams.bin"])

    def test_rejects_parent_traversal(self):
        with self.assertRaises(RuntimeError):
            safe_components("../outside")

    def test_rejects_windows_separator_and_drive(self):
        with self.assertRaises(RuntimeError):
            safe_components("data\\outside")
        with self.assertRaises(RuntimeError):
            safe_components("C:outside")

    def test_rejects_empty_component(self):
        with self.assertRaises(RuntimeError):
            safe_components("data//file")


if __name__ == "__main__":
    unittest.main()
