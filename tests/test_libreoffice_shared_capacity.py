"""Real spawned processes/OS locks; synthetic subprocesses stand in for Office."""
import json
import asyncio
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import psutil
from fastapi import HTTPException

from classroom_app.services import libreoffice_service as lo
from classroom_app.services.document_render_service import DocumentRenderQueueBusy, DocumentRenderService


FAKE_CONVERTER = """
import json, os, pathlib, sys, time
output, logfile, delay, exit_code = sys.argv[1:5]
with open(logfile, 'a', encoding='utf-8') as stream:
    stream.write(json.dumps({'event':'start','pid':os.getpid(),'time':time.time()})+'\\n')
if sys.argv[6:] == ['child']:
    import subprocess
    child = "import json,os,sys,time; f=open(sys.argv[2],'a',encoding='utf-8'); f.write(json.dumps({'event':'start_child','pid':os.getpid(),'time':time.time()})+'\\\\n'); f.close(); time.sleep(60)"
    subprocess.Popen([sys.executable,'-c',child,sys.argv[5],logfile])
time.sleep(float(delay))
if not int(exit_code):
    pathlib.Path(output, 'input.pdf').write_bytes(b'%PDF-1.4\\n')
with open(logfile, 'a', encoding='utf-8') as stream:
    stream.write(json.dumps({'event':'end','pid':os.getpid(),'time':time.time()})+'\\n')
sys.exit(int(exit_code))
"""


def _convert(root, capacity, barrier, results, delay=1, timeout=5, exit_code=0, pause_before_attach=False, spawn_child=False):
    lo.DATA_ROOT = Path(root) / "data"
    os.environ["LANSHARE_LIBREOFFICE_MAX_CONCURRENCY"] = str(capacity)
    actual_popen = subprocess.Popen
    def popen(command, **kwargs):
        outdir = command[command.index("--outdir")+1]
        return actual_popen([sys.executable,"-c",FAKE_CONVERTER,outdir,str(Path(root)/"events.jsonl"),str(delay),str(exit_code),command[1]]+(["child"] if spawn_child else []), **kwargs)
    if pause_before_attach:
        lo._ConversionLease.attach = lambda self,pid:time.sleep(30)
    if barrier:
        barrier.wait(timeout=60)
    started = time.perf_counter()
    try:
        with mock.patch.object(lo,"resolve_soffice_command",return_value=sys.executable), mock.patch.object(lo.subprocess,"Popen",side_effect=popen):
            lo.convert_office_file(Path(root)/"source.docx","pdf",timeout=timeout)
        outcome = "success"
    except lo.LibreOfficeBusy:
        outcome = "busy"
    except subprocess.TimeoutExpired:
        outcome = "timeout"
    except lo.LibreOfficeConversionError:
        outcome = "conversion_error"
    except Exception as exc:
        outcome = type(exc).__name__+":"+str(exc)
    results.put((outcome,time.perf_counter()-started))


def _hold_slot(root, ready):
    lo.DATA_ROOT = Path(root)/"data"
    os.environ["LANSHARE_LIBREOFFICE_MAX_CONCURRENCY"] = "1"
    with lo._conversion_slot():
        ready.set()
        time.sleep(60)


class LibreOfficeSharedCapacityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lo-capacity-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/"source.docx").write_bytes(b"synthetic input")
        self.context = multiprocessing.get_context("spawn")
        self.processes = []
        self.addCleanup(self._stop_processes)

    def _stop_processes(self):
        for process in self.processes:
            if process.is_alive():
                process.terminate()
            process.join(5)

    def spawn(self, target, args):
        process = self.context.Process(target=target,args=args)
        process.start()
        self.processes.append(process)
        return process

    def events(self):
        path = self.root/"events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def contention(self,count,capacity):
        barrier = self.context.Barrier(count+1)
        results = self.context.Queue()
        for _ in range(count):
            self.spawn(_convert,(str(self.root),capacity,barrier,results,2))
        barrier.wait(timeout=60)
        outcomes = [results.get(timeout=15) for _ in range(count)]
        for process in self.processes:
            process.join(5)
            self.assertEqual(process.exitcode,0)
        self.assertEqual(sum(outcome=="success" for outcome,_ in outcomes),capacity,outcomes)
        self.assertEqual(sum(outcome=="busy" for outcome,_ in outcomes),count-capacity,outcomes)
        self.assertLess(max(elapsed for outcome,elapsed in outcomes if outcome=="busy"),1)
        active = maximum = 0
        for event in sorted(self.events(),key=lambda event:event["time"]):
            active += 1 if event["event"]=="start" else -1
            maximum = max(maximum,active)
        self.assertEqual(maximum,capacity)
        self.assertEqual(active,0)

    def test_32_real_processes_default_capacity_one(self):
        self.contention(32,1)

    def test_20_real_processes_controlled_capacity_two(self):
        self.contention(20,2)

    def test_failed_and_timed_out_subprocess_release_slot(self):
        for delay,timeout,exit_code,expected in ((0,3,9,"conversion_error"),(10,.15,0,"timeout"),(0,3,0,"success")):
            results = self.context.Queue()
            process = self.spawn(_convert,(str(self.root),1,None,results,delay,timeout,exit_code))
            self.assertEqual(results.get(timeout=10)[0],expected)
            process.join(5)
            self.assertEqual(process.exitcode,0)

    def test_killed_conversion_subprocess_releases_slot(self):
        results = self.context.Queue()
        process = self.spawn(_convert,(str(self.root),1,None,results,30,40))
        deadline = time.monotonic()+10
        while not self.events() and time.monotonic()<deadline:
            time.sleep(.03)
        child_pid = self.events()[0]["pid"]
        os.kill(child_pid,signal.SIGTERM)
        self.assertEqual(results.get(timeout=10)[0],"conversion_error")
        process.join(5)
        self.spawn(_convert,(str(self.root),1,None,results,0,3))
        self.assertEqual(results.get(timeout=10)[0],"success")

    def test_killed_slot_owner_releases_kernel_lock(self):
        ready = self.context.Event()
        process = self.spawn(_hold_slot,(str(self.root),ready))
        self.assertTrue(ready.wait(10))
        process.terminate()
        process.join(5)
        results = self.context.Queue()
        self.spawn(_convert,(str(self.root),1,None,results,0,3))
        self.assertEqual(results.get(timeout=10)[0],"success")

    def test_killed_worker_cannot_overlap_orphan_even_before_pid_registration(self):
        results = self.context.Queue()
        owner = self.spawn(_convert,(str(self.root),1,None,results,30,2,0,True,True))
        deadline = time.monotonic()+10
        while len(self.events())<2 and time.monotonic()<deadline:
            time.sleep(.02)
        child_pid = self.events()[0]["pid"]
        self.addCleanup(lambda: self._kill_test_child(child_pid))
        grandchild_pid = self.events()[1]["pid"]
        self.addCleanup(lambda: self._kill_test_child(grandchild_pid))
        owner.terminate(); owner.join(5)
        self.assertTrue(psutil.pid_exists(child_pid))
        contender = self.spawn(_convert,(str(self.root),1,None,results,0,3))
        self.assertEqual(results.get(timeout=10)[0],"busy")
        contender.join(5)
        self.assertEqual(len(self.events()),2,"No replacement converter may overlap the orphan subtree")
        time.sleep(2.1)
        for _ in range(5):
            contender = self.spawn(_convert,(str(self.root),1,None,results,0,3))
            outcome = results.get(timeout=10)[0]
            contender.join(5)
            if outcome == "success":
                break
            self.assertEqual(outcome,"busy")
            time.sleep(.1)
        self.assertEqual(outcome,"success")
        self.assertFalse(psutil.pid_exists(child_pid) and psutil.Process(child_pid).status()!=psutil.STATUS_ZOMBIE)
        self.assertFalse(psutil.pid_exists(grandchild_pid) and psutil.Process(grandchild_pid).status()!=psutil.STATUS_ZOMBIE)

    def _kill_test_child(self,pid):
        try:
            process = psutil.Process(pid)
            if str(self.root/"events.jsonl") in process.cmdline():
                process.kill(); process.wait(5)
        except psutil.Error:
            pass

    def test_stale_metadata_never_kills_unrelated_reused_pid(self):
        foreign = subprocess.Popen([sys.executable,"-c","import time;time.sleep(15)"])
        self.addCleanup(lambda: foreign.poll() is None and foreign.kill())
        lock_root = self.root/"data/tmp/libreoffice/_locks"
        lock_root.mkdir(parents=True)
        (lock_root/"slot-0.json").write_text(json.dumps({"profile_arg":"-env:UserInstallation=file:///unique-not-the-foreign-process",
            "executable_name":Path(sys.executable).name,"started_at":time.time()-30,"deadline_monotonic":time.monotonic()-1,
            "processes":[{"pid":foreign.pid,"create_time":psutil.Process(foreign.pid).create_time()-100}]}),encoding="utf-8")
        results = self.context.Queue()
        self.spawn(_convert,(str(self.root),1,None,results,0,3))
        self.assertEqual(results.get(timeout=10)[0],"success")
        self.assertIsNone(foreign.poll())
        foreign.terminate(); foreign.wait(5)

    def test_document_renderer_maps_busy_and_does_not_retry_an_office_fallback(self):
        renderer = DocumentRenderService(root=self.root/"renderer")
        with mock.patch("classroom_app.services.document_render_service.convert_office_file",side_effect=lo.LibreOfficeBusy()) as convert:
            with self.assertRaises(DocumentRenderQueueBusy):
                renderer.render_artifact(b"synthetic",filename="test.docx",source_format="docx")
            convert.assert_called_once()
        # Its separate renderer budget was released when the shared LO slot was busy.
        with renderer._renderer_slot():
            pass

    def test_version_preflight_does_not_require_heavy_conversion_slot(self):
        with mock.patch.object(lo,"DATA_ROOT",self.root/"data"), mock.patch.dict(os.environ,{"LANSHARE_LIBREOFFICE_MAX_CONCURRENCY":"1"}):
            lo.soffice_is_runnable.cache_clear()
            with lo._conversion_slot(), mock.patch.object(lo,"resolve_soffice_command",return_value="synthetic-office"), mock.patch.object(lo.subprocess,"run",return_value=subprocess.CompletedProcess([],0)):
                self.assertTrue(lo.soffice_is_runnable())
            lo.soffice_is_runnable.cache_clear()

    def test_excel_busy_is_429_instead_of_invalid_file(self):
        from classroom_app.services import excel_upload_service as excel
        path = self.root/"legacy.xls"
        path.write_bytes(excel.XLS_OLE_MAGIC+b"synthetic")
        with mock.patch.object(excel,"convert_office_file",side_effect=lo.LibreOfficeBusy()):
            with self.assertRaises(HTTPException) as caught:
                excel.load_upload_workbook_bytes(path,"legacy.xls",material_label="测试材料")
        self.assertEqual(caught.exception.status_code,429)
        self.assertEqual(caught.exception.headers["Retry-After"],"10")

    def test_material_busy_propagates_without_lossy_fallback_then_maps_429(self):
        from classroom_app.services import material_ai_import_service as materials
        with mock.patch.object(materials,"convert_office_file",side_effect=lo.LibreOfficeBusy()), mock.patch.object(materials,"render_pdf_pages_to_data_urls",return_value=[]):
            for method,args in ((materials._extract_legacy_doc_via_libreoffice,(self.root/"input.doc",[])),
                                (materials._render_office_pages_to_images,(self.root/"input.docx",".docx",[]))):
                with self.assertRaises(lo.LibreOfficeBusy):
                    method(*args)
        ai = mock.AsyncMock()
        with mock.patch.object(materials,"resolve_material_ai_import_type",return_value={"group_key":"test","key":"test"}), mock.patch.object(materials,"extract_material_content",side_effect=lo.LibreOfficeBusy()):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(materials.parse_material_document(file_path=self.root/"input.doc",original_name="input.doc",document_group="test",document_type="test",ai_chat=ai))
        self.assertEqual(caught.exception.status_code,429)
        self.assertEqual(caught.exception.headers["Retry-After"],"10")
        ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
