import unittest
from example import Checkpointing

class TestCheckpointing(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Checkpointing()
        self.assertEqual(obj.run(), "Executing Checkpointing operations...")

if __name__ == "__main__":
    unittest.main()
