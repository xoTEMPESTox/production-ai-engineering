import unittest
from example import Statemachines

class TestStatemachines(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Statemachines()
        self.assertEqual(obj.run(), "Executing State Machines operations...")

if __name__ == "__main__":
    unittest.main()
