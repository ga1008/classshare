---
name: lanshare-artifacts
description: Create Word, Excel, PDF, PowerPoint and image artifacts in the current task workspace using the image's fixed Python libraries.
---

Use `/opt/lanshare-dsh/python/bin/python3` (the default `python3` in shell tools)
with the installed `docx`, `openpyxl`, `reportlab`, `PIL`
and `pptx` modules. Write only to `/workspace`; use relative, task-specific file
names. Keep files within the task's disk and file-count budgets. Do not install
packages or download executables at runtime.

Inspect input structure before editing. Reopen generated DOCX/XLSX/PPTX files
using the same library and verify content, sheet/slide count and intended values.
Check PDF page construction and export errors. This image has no Office or
LibreOffice renderer; do not claim that successful generation proves visual
fidelity or that a document has been visually inspected.

A local file is not a platform publication. Use the platform's supported
artifact/publication capability if it is available, respecting the current
actor and actual operation receipt. Report the generated filename and actual
delivery status. Do not invent a download link or claim upload/publish success
from a shell exit code.
