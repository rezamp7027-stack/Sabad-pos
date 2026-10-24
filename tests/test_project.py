import unittest
from pathlib import Path


class TestProjectStructure(unittest.TestCase):
    def test_entrypoint_exists(self):
        self.assertTrue(Path("main.py").is_file())

    def test_core_modules_exist(self):
        self.assertTrue(Path("sabad/db.py").is_file())
        self.assertTrue(Path("sabad/services.py").is_file())
        self.assertTrue(Path("sabad/app.py").is_file())


if __name__ == "__main__":
    unittest.main()
