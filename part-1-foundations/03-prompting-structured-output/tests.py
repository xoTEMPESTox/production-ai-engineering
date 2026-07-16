import unittest
from example import Promptingstructuredoutput

class TestPromptingstructuredoutput(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Promptingstructuredoutput()
        self.assertEqual(obj.run(), "Executing Prompting Structured Output operations...")

if __name__ == "__main__":
    unittest.main()
