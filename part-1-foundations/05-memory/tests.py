import unittest
from example import Memory

class TestMemory(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Memory()
        self.assertEqual(obj.run(), "Executing Memory operations...")

if __name__ == "__main__":
    unittest.main()
