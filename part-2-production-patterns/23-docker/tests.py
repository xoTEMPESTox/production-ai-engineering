import unittest
from example import Docker

class TestDocker(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Docker()
        self.assertEqual(obj.run(), "Executing Docker operations...")

if __name__ == "__main__":
    unittest.main()
