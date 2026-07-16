import unittest
from example import Persistence

class TestPersistence(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Persistence()
        self.assertEqual(obj.run(), "Executing Persistence operations...")

if __name__ == "__main__":
    unittest.main()
