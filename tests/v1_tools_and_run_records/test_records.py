from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeagent.records import RunRecorder


class RecordsTest(unittest.TestCase):
    def test_recorder_persists_json_record(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            recorder = RunRecorder(workspace)
            recorder.start("hello")
            recorder.record_step(llm_output="Final Answer: hi")
            recorder.complete("hi")

            path = recorder.save()
            data = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(str(path).startswith(str(Path(workspace).resolve() / ".codeagent" / "runs")))
            self.assertEqual(data["status"], "completed")
            self.assertEqual(data["user_input"], "hello")
            self.assertEqual(data["final_answer"], "hi")
            self.assertEqual(data["steps"][0]["llm_output"], "Final Answer: hi")


if __name__ == "__main__":
    unittest.main()
