import unittest
from example import Redischeckpointing

class TestRedischeckpointing(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Redischeckpointing()
        self.assertEqual(obj.run(), "Executing Redis Checkpointing operations...")

if __name__ == "__main__":
    unittest.main()
