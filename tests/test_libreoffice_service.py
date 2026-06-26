import tempfile
import unittest
from pathlib import Path
from unittest import mock

from classroom_app.services import libreoffice_service as lo


class LibreOfficeServiceTests(unittest.TestCase):
    def test_user_installation_arg_uses_file_uri(self):
        with tempfile.TemporaryDirectory(prefix="lo profile ") as temp_dir:
            arg = lo.user_installation_arg(Path(temp_dir))

        self.assertTrue(arg.startswith("-env:UserInstallation=file://"))
        self.assertNotIn("\\", arg)

    def test_resolve_soffice_prefers_console_launcher_on_windows(self):
        seen = []

        def fake_which(name):
            seen.append(name)
            return r"C:\Program Files\LibreOffice\program\soffice.com" if name == "soffice.com" else None

        with mock.patch.object(lo.os, "name", "nt"), mock.patch.object(lo.shutil, "which", side_effect=fake_which):
            resolved = lo.resolve_soffice_command()

        self.assertEqual(resolved, r"C:\Program Files\LibreOffice\program\soffice.com")
        self.assertEqual(seen[0], "soffice.com")

    def test_convert_file_copies_input_and_uses_isolated_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.docx"
            source.write_bytes(b"fake")
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["cwd"] = kwargs.get("cwd")
                captured["env"] = kwargs.get("env")
                out_dir = Path(command[command.index("--outdir") + 1])
                (out_dir / "input.pdf").write_bytes(b"%PDF-1.4\n")
                return lo.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with mock.patch.object(lo, "resolve_soffice_command", return_value="soffice.com"):
                with mock.patch.object(lo.subprocess, "run", side_effect=fake_run):
                    result = lo.convert_office_file(source, "pdf")

        self.assertEqual(result.output_bytes, b"%PDF-1.4\n")
        self.assertIn("file://", captured["command"][1])
        self.assertNotIn("\\", captured["command"][1])
        self.assertEqual(Path(captured["command"][-1]).name, "input.docx")
        self.assertEqual(Path(captured["cwd"]).name, "work")
        self.assertNotIn("URE_BOOTSTRAP", captured["env"])


if __name__ == "__main__":
    unittest.main()
