import unittest
from example import Evaluationpipelines

class TestEvaluationpipelines(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Evaluationpipelines()
        self.assertEqual(obj.run(), "Executing Evaluation Pipelines operations...")

if __name__ == "__main__":
    unittest.main()
