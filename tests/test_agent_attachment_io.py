import asyncio
import io
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from classroom_app.routers import agent_tasks


class AgentAttachmentIOTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversize_upload_reads_only_limit_plus_one_bytes(self):
        source = io.BytesIO(b"x" * 1000)
        upload = UploadFile(filename="large.txt", file=source)
        with patch.object(agent_tasks, "AGENT_TASK_ATTACHMENT_MAX_FILE_BYTES", 16):
            with self.assertRaises(HTTPException) as error:
                await agent_tasks._process_agent_attachment(upload)
        self.assertEqual(413, error.exception.status_code)
        self.assertEqual(17, source.tell())

    async def test_parse_threads_are_bounded_and_cancel_does_not_free_live_slot(self):
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        caller_thread = threading.get_ident()
        worker_threads = []

        def parse(filename, contents):
            worker_threads.append(threading.get_ident())
            started[int(filename[0])].set()
            if not release.wait(3):
                raise RuntimeError("Fixture did not release parser")
            return {"name": filename, "data": contents, "text": "parsed", "kind": "document"}

        with patch.object(agent_tasks, "_process_agent_attachment_bytes", side_effect=parse):
            jobs = [asyncio.create_task(agent_tasks._process_agent_attachment(UploadFile(filename=f"{n}.docx", file=io.BytesIO(b"fixture")))) for n in range(2)]
            try:
                self.assertTrue(await asyncio.to_thread(started[0].wait, 1))
                self.assertTrue(await asyncio.to_thread(started[1].wait, 1))
                self.assertTrue(all(identity != caller_thread for identity in worker_threads))
                jobs[0].cancel()
                await asyncio.sleep(0)
                with self.assertRaises(HTTPException) as error:
                    await agent_tasks._process_agent_attachment(UploadFile(filename="third.txt", file=io.BytesIO(b"third")))
                self.assertEqual(429, error.exception.status_code)
                self.assertFalse(jobs[0].done())
            finally:
                release.set()
                results = await asyncio.gather(*jobs, return_exceptions=True)
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertEqual("parsed", results[1]["text"])
        result = await agent_tasks._process_agent_attachment(UploadFile(filename="next.txt", file=io.BytesIO(b"actual text")))
        self.assertEqual("actual text", result["text"])
