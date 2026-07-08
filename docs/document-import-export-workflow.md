# Document Import Parsing And Deterministic Export Notes

This document records the workflow and pitfalls discovered while improving the
teacher-side lesson-plan import/generation/export feature. It is meant for
future AI agents and engineers who add similar "upload an existing school
document, parse it into structured data, fill/generate data, then export a
pixel-faithful Word/PDF document" features.

For a fuller Chinese implementation guide that covers multiple document types,
AI prompt boundaries, signature handling, preview/export parity, classroom and
academic field mapping, and the required QA checklist, start with
[`document-feature-ai-implementation-guide.md`](document-feature-ai-implementation-guide.md).

## Current Lesson-Plan Code Map

- API routes: `classroom_app/routers/lesson_plans.py`
  - `POST /api/lesson-plans/import`: saves uploads to a temp dir, creates a
    placeholder lesson-plan row, then starts `run_import_job(...)`.
  - `POST /api/lesson-plans/generate`: creates a placeholder row, then starts
    `run_generation_job(...)` for a selected classroom.
  - `GET /api/lesson-plans/{plan_id}/export?fmt=docx|pdf|png`: loads the
    normalized plan and calls `build_lesson_plan_docx(...)`, then optionally
    converts to PDF/PNG.
- Import parser: `classroom_app/services/lesson_plan_import_service.py`
  - Reuses `material_ai_import_service.extract_material_content(...)` for
    DOC/DOCX/PDF/image/text extraction.
  - Sends extracted text and page images to the AI gateway with
    `response_format="json"`.
  - Falls back to `_repair_json_text(...)` when the AI returned useful but
    malformed JSON-like text.
  - Normalizes through `lesson_plan_service.normalize_lesson_plan_payload(...)`
    before saving.
- Classroom generation: `classroom_app/services/lesson_plan_generation_service.py`
  - Reads `class_offering_sessions`.
  - Gathers classroom context and bound teaching-material text.
  - Generates session data one session at a time, persists progress after each
    session, and uses JSON extraction/repair helpers for model output.
- Export renderer: `classroom_app/services/lesson_plan_docx_service.py`
  - Builds the DOCX with explicit page, table, font, line-spacing, border, and
    row-height constants.
  - Uses `classroom_app/services/assets/gxufl_lesson_plan_header.png` for the
    GXUFL cover header.
  - Uses fixed OOXML table grids instead of Word auto-fit.
  - Uses non-breaking spaces for cover underlined fields, because Word does not
    reliably draw underlines across trailing normal spaces.
- Tests: `tests/test_lesson_plan_docx.py`
  - Verifies page setup, cover shape, absence of extra session captions, outer
    table grids, nested activity table grids, and required labels.

## Shared Document Renderer

`classroom_app/services/document_render_service.py` is the canonical preview
renderer for Word/Excel/PDF exports. A preview must no longer be a hand-written
HTML mock or a browser PDF iframe when the user expects print fidelity. The
flow is:

1. Build the final downloadable artifact first (`.docx`, `.xlsx`, or `.pdf`).
2. Pass those exact bytes to `document_render_service.render_artifact(...)`.
3. The renderer stores the final document in
   `DATA_ROOT/tmp/document_renderer/<hash>/`, converts Office files to PDF with
   the shared LibreOffice wrapper when needed, and renders page PNGs with
   PyMuPDF.
4. The preview page uses `document_render_service.render_preview_html(...)` to
   show one 3D page card per rendered page. Clicking a page requests a cached or
   lazily rendered large PNG for that page. The medium image remains visible as
   a fallback while the large image is being prepared; failed large-image loads
   show a retry action instead of leaving a blank preview.
5. The preview download button returns the already-rendered final document via
   `/api/document-renderer/jobs/{key}/download`, so preview-page export does not
   regenerate the document.
6. `/api/document-renderer/jobs/{key}/metadata` exposes lightweight job metadata
   for diagnostics or future UI enhancements.

The current first-class consumers are:

- `assessment_plan_service.render_preview_html(...)`
- `teacher_evaluation_service.render_preview_html(...)`
- `lesson_plan_render_service.render_plan_html(...)`
- `GET /api/materials/ai-import-records/{record_id}/render-preview` for
  final-material AI import exports, including Excel grade-record sheets.

Concurrency and caching:

- The renderer key is a SHA-256 hash over renderer version, source format,
  render profile (page-image zooms and page cap), and final document bytes.
  Identical exports reuse the same cached document, PDF, and page PNGs.
- Per-key locks deduplicate concurrent requests for the same document.
- A process-local semaphore plus filesystem slot locks limit expensive
  LibreOffice/PyMuPDF work across app workers on the same host. Configure with
  `LANSHARE_DOCUMENT_RENDER_MAX_CONCURRENCY` (default `1`) and
  `LANSHARE_DOCUMENT_RENDER_QUEUE_TIMEOUT_SECONDS` (default `45`).
- Page images are written to temporary files and atomically replaced, so readers
  do not observe partially written PNGs.
- Cached artifacts are touched on access and removed opportunistically after
  `LANSHARE_DOCUMENT_RENDER_TTL_SECONDS` (default `86400`). The cleanup runs at
  most once every ten minutes per process.
- `LANSHARE_DOCUMENT_RENDER_MAX_PAGES` (default `80`) protects the small server
  from very large documents. Increase it only after considering CPU, memory, and
  storage pressure.
- `/api/internal/health` includes a `document_renderer` block with root path,
  concurrency/TTL settings, cached job count, cached byte count, and rendered
  medium/large page counts.

Security and access:

- Business routes must still perform normal permission checks before building
  an artifact. The generic image/download/metadata routes also require a
  logged-in user and a short-lived signed render token generated from
  `SECRET_KEY`. The token is bound to the current user id + role and embedded
  only in preview HTML returned after the business permission check. Configure
  the token life with `LANSHARE_DOCUMENT_RENDER_TOKEN_TTL_SECONDS` (default
  `7200`, capped at 24 hours).
- The renderer cache is temporary runtime data. Do not commit generated
  documents, PDFs, PNGs, or `.codex-temp/` QA screenshots.

Extension rule:

- For a new document-export feature, expose preview by building the same final
  artifact used for download and then calling the shared renderer. Avoid adding
  a feature-specific HTML/PDF iframe preview unless the output is not a
  print-layout document.

## Import Parsing Pattern

1. Save uploads to a temp directory with safe file names and limits.
   - Limit file count and per-file size.
   - Validate file extensions.
   - Always clean temp files in a `finally` block.
2. Extract locally before calling AI.
   - Prefer a shared extraction service that handles DOC/DOCX/PDF/images and
     returns text, page images, and warnings.
   - Keep per-file text budgets so a single long document cannot blow up
     context.
   - Include page images when layout or handwriting/scan fidelity matters.
3. Ask AI for structured JSON, not prose.
   - Use `response_format="json"` where the gateway/model supports it.
   - The system prompt must contain an exact schema and "no extra output"
     requirement.
   - Still assume the model may return fenced JSON, a JSON array, mixed prose,
     or malformed JSON.
4. Parse defensively.
   - Try structured fields from the gateway first, then parse `response_text`.
   - Strip markdown fences.
   - Scan for the first JSON object/array if the reply includes prose.
   - If data is present but malformed, call a fast JSON-capable model to repair
     into the schema.
5. Normalize and validate before persistence.
   - Convert aliases, defaults, and missing keys into a stable internal payload.
   - Reject empty parsed results with actionable errors.
   - Persist source type/status/progress so the UI can recover from failures.

## Classroom Generation Pattern

1. Treat classroom-bound sessions as the source of truth for the number and
   order of sessions. Do not infer a fixed 15-session semester.
2. Build a preview/editing plan before generation.
   - Pull schedule, week, weekday, section text, classroom title, and bound
     material metadata.
   - Cache quick AI summaries for repeated material/session metadata.
   - Let users reorder, delete, and add session cards before generation.
3. Generate lesson plans per session, not as one huge semester prompt.
   - Per-session generation is easier to retry and produces better detail.
   - Include course, classroom, textbook, teaching unit, schedule, neighbouring
     session context, and bound material snippets.
   - Persist after every session so progress and partial success are visible.
4. If a session has no teaching material, infer a topic from neighbouring
   sessions and the user prompt, then generate the full structured session.
5. Keep generation output in the same schema consumed by import and export.

## Template Replication Workflow

Use this workflow when an exported file must visually match a school-provided
DOCX/PDF sample.

1. Render the reference document first.
   - On Windows, Microsoft Word COM export to PDF is often the closest to the
     user's real printing result.
   - LibreOffice can hang or render differently for some DOCX files; use it for
     server-side preview, but do not treat it as the only visual authority for
     pixel-level replication.
   - If Chinese or cloud-sync paths confuse tools, copy the file to an English
     temp path before rendering.
2. Convert rendered PDF pages to PNG and inspect visually.
   - Keep reference PNGs and current-export PNGs side by side.
   - Use pixel checks for bounding boxes and table/underline line positions,
     not just subjective screenshots.
3. Extract OOXML metrics from the reference DOCX.
   - Page size, margins, header/footer distances: `w:pgSz`, `w:pgMar`.
   - Table width/grid: `w:tblW`, `w:tblGrid`, `w:gridCol`.
   - Fixed layout: `w:tblLayout w:type="fixed"`.
   - Row heights: `w:trHeight`.
   - Cell widths/merges: `w:tcW`, `w:gridSpan`.
   - Borders: `w:tblBorders`.
   - Image sizes: drawing `wp:extent`.
   - Fonts, line spacing, indentation, and paragraph alignment from `w:rPr`
     and `w:pPr`.
4. Convert template facts into code constants.
   - Do not depend on a user-local Nutstore/reference path at runtime.
   - Avoid committing full source documents if they include course content or
     private data. Extract reusable assets and dimensions instead.
   - Commit only necessary neutral assets, such as a school header image, when
     allowed by the project owner.
5. Build DOCX deterministically.
   - Use explicit EMU/twip constants.
   - Disable table auto-fit.
   - Set fixed table grid and cell widths.
   - Set row heights and border sizes.
   - Use left/center alignment only where the reference uses it.
   - Avoid decorative headings or helper captions that are not present in the
     template.
6. Verify with screenshots after every layout change.
   - Export DOCX to PDF through Word when available for local QA.
   - Convert PDF to PNG at a stable DPI.
   - Compare page-level bounding boxes, table heavy columns/rows, and field
     underline segments.

## Word Underline Gotcha

Do not use trailing normal spaces to create underlined form fields. Microsoft
Word may not render underline for those trailing spaces after PDF export or
print. Use one of these instead:

- Non-breaking spaces (`\u00a0`) inside an underlined run.
- A tab stop with underline behavior, if the template uses tabs.
- A border-bottom paragraph/table-cell strategy, if the template is based on
  ruled boxes rather than underlined runs.

For the GXUFL lesson-plan cover, the reference template uses underlined runs.
The implementation therefore uses NBSP padding in `_cover_value_run(...)` and
field-specific `min_chars` values so the visible line segments match the
reference. The `学分/学时` row is special: it has two separate underlined
segments and non-underlined spacing between the labels.

## Export Verification Checklist

Before calling a document export feature "done":

- Render the original sample and generated export to page PNGs.
- Confirm page count and page breaks.
- Confirm page margins and main content bounding boxes.
- Confirm table left/right/top coordinates against the reference.
- Confirm cover field underline lengths.
- Confirm no extra headings, captions, gray fills, cards, or decorative text
  were added.
- Confirm long values wrap like the reference, especially textbook names.
- Run unit tests that assert structural contracts: page setup, table grids,
  required labels, absence of extra captions, and nested-table shape.
- For multi-session lesson plans, confirm each session starts on a new page and
  that export inserts page breaks between sessions.

## Extending To New Document Types

When adding a new document type:

1. Create a document-type schema.
   - `cover` or metadata fields.
   - Repeated sections/rows.
   - Free-text fields.
   - Queryable fields for search/filter if needed.
2. Add a normalizer.
   - Accept AI aliases and incomplete output.
   - Fill safe defaults.
   - Reject empty payloads.
3. Add an import service.
   - Reuse shared extraction.
   - Use JSON mode and malformed-output repair.
   - Save parse warnings and progress.
4. Add an export service.
   - Decide whether it is template-driven, deterministic-code-driven, or both.
   - Extract OOXML constants from the reference.
   - Commit reusable neutral assets.
   - Do not depend on local reference paths.
5. Add rendered QA artifacts during development.
   - Keep temporary screenshots under `.codex-temp/`.
   - Do not commit generated QA DOCX/PDF/PNG unless they are intended fixtures.
6. Add tests.
   - Parser tests for JSON tolerance.
   - Normalizer tests for missing/alias fields.
   - DOCX structure tests for margins, table grids, page breaks, and required
     labels.

## Practical Commands Used For QA

Render a DOCX with Microsoft Word COM on Windows:

```powershell
$docx = 'C:\path\to\input.docx'
$pdf = 'C:\path\to\output.pdf'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Open($docx, $false, $true)
  try { $doc.ExportAsFixedFormat($pdf, 17) }
  finally { $doc.Close($false) }
}
finally { $word.Quit() }
```

Convert PDF to PNG pages with the bundled runtime:

```powershell
$py = 'C:\Users\AngelWei\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PATH = 'C:\Users\AngelWei\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin;' + $env:PATH
@'
from pathlib import Path
from pdf2image import convert_from_path
pdf = Path(r'C:\path\to\output.pdf')
out_dir = pdf.with_suffix('')
out_dir.mkdir(parents=True, exist_ok=True)
for i, page in enumerate(convert_from_path(str(pdf), dpi=160), 1):
    page.save(out_dir / f'page-{i:03d}.png')
'@ | & $py -
```

Use a small Python/Pillow script to measure dark line segments in screenshots
when line length or table boundary precision matters. This is faster and more
reliable than repeated visual guessing.
