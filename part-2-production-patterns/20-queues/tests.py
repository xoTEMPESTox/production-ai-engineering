import unittest
from example import Queues

class TestQueues(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Queues()
        self.assertEqual(obj.run(), "Executing Queues operations...")

if __name__ == "__main__":
    unittest.main()
