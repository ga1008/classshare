"""Focused admission-tool contracts; formal load remains an explicit command."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tools import career_admission_wave_probe as probe
from classroom_app.services.career_lifecycle_service import validate_answers


class AdmissionWaveTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_full_answers_match_current_published_contract_for_ten_majors(self):
        counts = set()
        for sid, major in enumerate(probe.MAJORS, 1):
            questions = probe.career.get_questions(mode="full",major_key=major)
            answers = probe.complete_answers(questions,sid)
            self.assertEqual(len(validate_answers(answers,mode="full",major_key=major,complete=True)),len(questions))
            counts.add(len(questions))
        self.assertEqual(counts,{11})

    async def test_open_loop_collects_already_finished_task_errors(self):
        async def broken(item):
            raise KeyError("invalid response")
        # Leave headroom for Windows timer granularity and other test processes.
        report=await probe.open_loop(list(range(3)),.3,5,broken)
        self.assertEqual(report["task_errors"],["KeyError"]*3)
        self.assertFalse(report["skipped"])

    async def test_inflight_saturation_is_reported_as_skipped_not_hidden_reduced_load(self):
        import asyncio
        async def slow(item):
            await asyncio.sleep(.05)
        report=await probe.open_loop(list(range(10)),.02,1,slow)
        self.assertGreater(len(report["skipped"]),0)
        self.assertFalse(report["task_errors"])

    def test_formal_waves_require_explicit_switch(self):
        for scenario,users,duration in (("entry",1000,60),("quiz",300,30)):
            args=probe.parser().parse_args(["--scenario",scenario,"--users",str(users),"--duration",str(duration)])
            with self.assertRaises(ValueError):probe.validate_args(args)
            args.formal=True;probe.validate_args(args)

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_ADMISSION_PROBE")=="1","opt-in local PostgreSQL failure evidence check")
    def test_failure_preserves_report_and_still_verifies_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="career-wave-test-") as directory:
            args=probe.parser().parse_args(["--users","10","--duration",".1","--output",str(Path(directory)/"failure.json")])
            with patch.object(probe,"exercise",AsyncMock(side_effect=RuntimeError("intentional probe failure"))):
                report=probe.run(args)
            self.assertFalse(report["ok"])
            self.assertEqual(report["failure"]["error_type"],"RuntimeError")
            self.assertTrue(report["schema_removed"])
            self.assertEqual(report["owned_connections_remaining"],0)
            self.assertTrue(Path(args.output).is_file())


if __name__=="__main__":unittest.main()
