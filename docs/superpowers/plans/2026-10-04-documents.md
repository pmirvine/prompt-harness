# Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let test cases attach documents (text, PDF, Word, PowerPoint, Excel, OpenDocument, RTF, legacy `.doc`, images), refer to them from templates under a configurable name, and let the LLM judge see them; ship two examples and a step-by-step guide.

**Architecture:** A reader registry (`core/documents.py`, with format readers in `core/readers_office.py` and `core/readers_other.py`) turns each file into a `Document` of kind `text` or `image`. A new `core/messages.py` builds OpenAI-style content parts so images travel as data URLs. `PromptVersion.documents_name` (default `documents`) names the template variable, is stored by a DB migration, and is exported only when non-default. The judge prompt becomes a template and the judge request carries the documents.

**Tech Stack:** Python 3.11+, existing stack, plus `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `xlrd`, `striprtf`.

**Spec:** `docs/superpowers/specs/2026-10-04-documents-design.md` (extends `2026-10-03-promptharness-design.md`)

## Global Constraints

- Work on branch `feat/documents` created from `main`; never commit to `main` directly.
- Python 3.11+; `core/` must not import Textual; no network in tests; keys are never stored.
- Existing behaviour is unchanged: plain-text cases send exactly the same request; existing prompt hashes are unchanged (pinned by a test); existing harnesses, runs and exports keep working; export `format_version` stays 1.
- Every document problem is a per-case `error` (message starts `unsupported:` and names the file and reason) and never crashes a run or stops other cases.
- Templates and judge prompts run in the existing Jinja `SandboxedEnvironment` with `StrictUndefined`; image bytes are stored in an underscore-prefixed field so templates cannot read them.
- Stored runs must not contain base64 image data: requests are recorded with image data URLs replaced by `data:<mime>;base64,<N bytes omitted>`.
- Add each dependency with `uv add`, check its license (`uv pip show <pkg>` / package metadata) and put the license in the commit message; a non-permissive license stops the work and is reported.
- Do not `git add` anything under `.superpowers/`; commit trailer: `Co-Authored-By: <the model that wrote it> <noreply@anthropic.com>`.
- Run the full suite (`uv run pytest -q`, currently 293 tests) before each commit.

## Review Focus

1. **Prompt hash stability:** existing harnesses and run history must keep matching; the hash must omit `documents_name` when default (Task 4).
2. **Base64 in the database:** image data must never be persisted in `runs.request` (Task 5).
3. **Judge prompts with braces:** a plain judge prompt containing single braces or JSON (`{"pass": true}`) must render unchanged; a literal `{{` must give `judge_error`, not a crash (Task 6).
4. **Same-named documents:** two documents with the same base name in different folders must not overwrite each other on inline export/import (Task 7).
5. **One bad document:** an unreadable document in one case must not affect other cases, and a non-vision model must produce a hinted error, not a traceback (Task 5).

---

## File Structure

```
src/promptharness/core/documents.py      # Document model, ReadResult, registry, text + image readers, load_documents
src/promptharness/core/readers_office.py # pdf, docx, pptx
src/promptharness/core/readers_other.py  # xlsx, xls, odt/ods/odp, rtf, legacy doc
src/promptharness/core/messages.py       # build_user_content, redact_images, summarize_documents
src/promptharness/core/render.py         # re-exports Document/DocumentError/load_documents; render_user gains documents_name
src/promptharness/examples/{three-documents,mixed-formats}.harness.yaml
scripts/docfixtures.py                   # make_pdf/make_docx/make_pptx/make_xlsx/make_odt/make_ods/make_odp/make_png helpers (tests and build script)
scripts/build_document_examples.py       # regenerates the two example YAMLs and docs/sample-documents/*
scripts/make_documents_guide_screenshots.py
docs/sample-documents/                   # invoice-1041.docx, invoice-1042.pdf, invoice-1043.png
docs/working-with-documents.md
tests/{test_documents.py,test_readers_office.py,test_readers_other.py,test_messages.py,test_tui_documents.py}
```

---

### Task 1: Document model, reader registry, text and image readers

**Files:**
- Create: `src/promptharness/core/documents.py`, `tests/test_documents.py`
- Modify: `src/promptharness/core/render.py` (remove its own `Document`, `DocumentError`, `load_documents`; re-export them from `documents`), `tests/test_render.py` (only if imports need adjusting)

**Interfaces:**
- Produces (`core/documents.py`):
  - `MAX_IMAGE_BYTES = 20 * 1024 * 1024`
  - `class DocumentError(Exception)`; `class ReadError(Exception)` (raised by readers with only the reason text)
  - `@dataclass(frozen=True) class Document(name: str, text: str, kind: Literal["text","image"]="text", mime: str="text/plain", index: int=0, pages: int|None=None, warnings: tuple[str,...]=(), _data: bytes|None=None)`
  - `@dataclass(frozen=True) class ReadResult(text: str, mime: str="text/plain", pages: int|None=None, warnings: tuple[str,...]=())`
  - `Reader = Callable[[bytes, str], ReadResult]` (file bytes, base name); `READERS: dict[str, Reader]` keyed by lowercase extension with the dot; `register(*extensions: str)` decorator that adds a reader to `READERS`
  - `sniff_image(data: bytes) -> str | None` returning `image/png`, `image/jpeg`, `image/gif` or `image/webp` from the leading bytes
  - `read_document(path: str, index: int = 0) -> Document`; `load_documents(paths: list[str]) -> list[Document]` (assigns `index` 0..n-1)
  - `read_document` imports `promptharness.core.readers_office` and `promptharness.core.readers_other` lazily on first use (so registration happens and start-up stays fast), tolerating their absence until Tasks 2 and 3 exist.
- Consumes: nothing new. `core/render.py` and `core/runner.py` keep importing `Document`, `DocumentError`, `load_documents` from `promptharness.core.render`.

- [ ] **Step 1: Write failing tests** in `tests/test_documents.py` (use `tmp_path`; a 1x1 PNG, JPEG, GIF and WebP header are tiny byte constants in the test):
  - `test_text_file_becomes_text_document`: `.txt` → `kind=="text"`, `mime=="text/plain"`, `text` equals content, `name` is the base name.
  - `test_indexes_follow_list_order`: three files → `[d.index for d in docs] == [0,1,2]`.
  - `test_unknown_extension_utf8_text_is_accepted_and_binary_is_not`: `.log` with text loads; `.dat` containing `b"\x00\x01"` raises `DocumentError` containing `unsupported` and `binary`.
  - `test_missing_file`: `DocumentError` containing `unsupported` and `cannot read`.
  - `test_image_documents`: parametrised over png/jpg/jpeg/gif/webp bytes → `kind=="image"`, `mime` from the bytes (a `.jpg` file holding PNG bytes gets `image/png`), `text == "[attached image: <name>]"`, `_data == bytes`.
  - `test_file_named_png_that_is_not_an_image_is_rejected`: `.png` with text content → `DocumentError` containing `not a valid image`.
  - `test_unsupported_image_types`: `.bmp`, `.tiff`, `.svg`, `.heic` → `DocumentError` containing `unsupported image type`.
  - `test_image_over_limit`: monkeypatch `documents.MAX_IMAGE_BYTES` to 10 and a larger PNG → `DocumentError` containing `image too large`.
  - `test_reader_errors_are_wrapped`: register a throwaway reader for `.zzz` (restore `READERS` after) that raises `ReadError("boom")` → `DocumentError` equal to `unsupported: <path>: boom`; one that raises `ValueError("x")` → message contains `cannot read` and `ValueError`.
  - `test_template_cannot_read_image_bytes`: render `{{ documents[0]._data }}` with `render_user` over an image document → `TemplateRenderError`; `{{ documents[0].kind }} {{ documents[0].mime }} {{ documents[0].index }}` renders.
  - Existing `tests/test_render.py` still passes unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/test_documents.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `core/documents.py`. `read_document` reads bytes, lowercases the extension, and: image extensions (`.png .jpg .jpeg .gif .webp`) go through `sniff_image` (not an image → `not a valid image`; over the limit → `image too large (N bytes, limit 20 MB)`); extensions in `{.bmp .tif .tiff .heic .heif .svg .ico}` → `unsupported image type`; a registered reader is called and its `ReadError` wrapped as `DocumentError(f"unsupported: {path}: {reason}")`, any other exception as `cannot read <ext> file (<Type>: <msg>)`; otherwise today's behaviour (NUL byte → `binary file`, invalid UTF-8 → `not valid UTF-8 text`). All errors start `unsupported: <path>: `. Update `render.py` to re-export.
- [ ] **Step 4: Run** the new tests and then the full suite → PASS.
- [ ] **Step 5: Commit** `feat: document model and reader registry with text and image readers`.

---

### Task 2: PDF, Word and PowerPoint readers

**Files:**
- Create: `src/promptharness/core/readers_office.py`, `scripts/docfixtures.py`, `tests/test_readers_office.py`
- Modify: `pyproject.toml` / `uv.lock` (add `pypdf`, `python-docx`, `python-pptx`), `tests/conftest.py` (one line: put `scripts/` on `sys.path` so tests can `import docfixtures`)

**Interfaces:**
- Consumes: `register`, `ReadResult`, `ReadError` from Task 1.
- Produces: readers registered for `.pdf`, `.docx`, `.pptx`.
  - PDF text is `\n\n--- page N ---\n<text>` blocks (N from 1) joined; `pages` is the page count; a page with no text adds the warning `<name>: page N has no extractable text`; if no page has text raise `ReadError("no extractable text; it may be a scan. Convert the pages to images and attach those")`; encrypted PDFs are tried with an empty password, else `ReadError("password-protected")`; if `pypdf` needs the optional `cryptography` package, `ReadError("encrypted PDF needs the 'cryptography' package")`; data not starting with `%PDF` → `ReadError("not a valid PDF")`.
  - DOCX: body paragraphs and tables in document order, table rows rendered as cells joined by ` | `; data not a zip → `ReadError("not a valid .docx file")`.
  - PPTX: per slide a `--- slide N ---` line, then title/body text, table rows (` | `), then `Notes: <speaker notes>` when present; `pages` is the slide count.
- Produces (`scripts/docfixtures.py`): `make_pdf(pages: list[str]) -> bytes` (hand-written minimal PDF, one Helvetica text line per page, extractable by `pypdf`); `make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes`; `make_pptx(slides: list[dict]) -> bytes` with slide keys `title`, `body`, optional `notes`, optional `table`.

- [ ] **Step 1: Write failing tests** in `tests/test_readers_office.py` writing fixtures to `tmp_path` and loading with `load_documents`:
  - `test_pdf_text_and_pages`: `make_pdf(["Alpha 1", "Beta 2"])` → text contains `--- page 1 ---`, `Alpha 1`, `--- page 2 ---`, `Beta 2`; `pages == 2`; `kind == "text"`, `mime == "application/pdf"`.
  - `test_pdf_blank_page_warns`: pages `["Alpha", ""]` → text has Alpha, `warnings == ("<name>: page 2 has no extractable text",)`.
  - `test_pdf_with_no_text_is_an_error`: `make_pdf(["", ""])` → `DocumentError` containing `no extractable text`.
  - `test_pdf_not_a_pdf` (`.pdf` holding text) → `not a valid PDF`; `test_pdf_corrupt` (`%PDF-1.4` followed by junk) → `DocumentError` containing `unsupported`.
  - `test_pdf_encrypted`: encrypt a generated PDF with a user password via `pypdf` (pick an algorithm that needs no extra dependency, else add `cryptography` to the dev group only) → `DocumentError` containing `password-protected`; and a PDF encrypted with an *empty* user password reads normally; and monkeypatching `pypdf` to raise its `DependencyError` gives the `cryptography` message.
  - `test_docx_paragraphs_and_table_in_order`: paragraphs `["Invoice 1041", "Total follows"]` plus table `[["Item","Price"],["Widget","9.00"]]` → text lines in that order with `Item | Price` and `Widget | 9.00`; `mime` is the docx MIME type.
  - `test_docx_not_a_zip` → `not a valid .docx file`.
  - `test_pptx_slides_tables_and_notes`: two slides, the second with a table and notes → `--- slide 1 ---`, titles and bodies, `Widget | 9.00`, `Notes: <text>`; `pages == 2`.
- [ ] **Step 2: Run** `uv run pytest tests/test_readers_office.py -v` → FAIL.
- [ ] **Step 3: Add the dependencies** (`uv add pypdf python-docx python-pptx`), record licenses, create `scripts/docfixtures.py`, then implement the three readers with lazy imports of each library inside the reader function.
- [ ] **Step 4: Run** the new tests, then the full suite → PASS.
- [ ] **Step 5: Commit** `feat: read PDF, Word and PowerPoint documents` (licenses in the body).

---

### Task 3: Spreadsheet, OpenDocument, RTF and legacy Word readers

**Files:**
- Create: `src/promptharness/core/readers_other.py`, `tests/test_readers_other.py`, `tests/fixtures/sample.xls`
- Modify: `pyproject.toml` / `uv.lock` (add `openpyxl`, `xlrd`, `striprtf`), `scripts/docfixtures.py`

**Interfaces:**
- Consumes: Task 1 registry; Task 2 `scripts/docfixtures.py`.
- Produces: readers for `.xlsx`, `.xls`, `.odt`, `.ods`, `.odp`, `.rtf`, `.doc`; module constant `MAX_SHEET_ROWS = 5000`.
  - XLSX/XLS: per sheet a `--- sheet NAME ---` line then rows with cells joined by tab (empty cell → empty string); `pages` is the sheet count; more than `MAX_SHEET_ROWS` rows adds the warning `<name>: sheet 'X' truncated at 5000 rows`. XLSX opened read-only with cached values (`data_only=True`).
  - ODT/ODS/ODP: stdlib `zipfile` + `xml.etree` on `content.xml`; text/heading paragraphs in order; ODS tables as tab-separated rows under `--- sheet NAME ---`; ODP pages as `--- slide N ---`; not a zip or no `content.xml` → `ReadError("not a valid OpenDocument file")`.
  - RTF: `striprtf.striprtf.rtf_to_text`.
  - `.doc`: write the bytes to a temp file and try `antiword <file>` (via `shutil.which`), else `soffice` or `libreoffice` with `--headless --convert-to txt:Text --outdir <tmp> <file>`; 60 s timeout; non-zero exit or timeout → `ReadError` with the tool's message; no tool found → `ReadError("legacy .doc needs antiword or LibreOffice; convert the file to .docx")`.
- Produces (`scripts/docfixtures.py`): `make_xlsx(sheets: dict[str, list[list]]) -> bytes`, `make_odt(paragraphs: list[str]) -> bytes`, `make_ods(sheets: dict[str, list[list]]) -> bytes`, `make_odp(slides: list[list[str]]) -> bytes`.

- [ ] **Step 1: Write failing tests** in `tests/test_readers_other.py`:
  - `test_xlsx_sheets_rows_and_numbers`: two sheets → `--- sheet Data ---`, tab-separated rows, numbers rendered without trailing noise (`9.5`, `3`), `pages == 2`.
  - `test_xlsx_row_cap_warns`: monkeypatch `MAX_SHEET_ROWS=3` and write 5 rows → only 3 rows present and the truncation warning.
  - `test_xlsx_corrupt` → `DocumentError` containing `unsupported`.
  - `test_xls_fixture_reads`: `tests/fixtures/sample.xls` (generate once with a one-off `xlwt` script, commit the ~5 KB file, do not add `xlwt` as a dependency) → contains its known cell text.
  - `test_odt_ods_odp`: generated files → paragraphs in order; ODS `--- sheet ---` with tab rows; ODP `--- slide N ---`; a zip without `content.xml` → `not a valid OpenDocument file`.
  - `test_rtf`: a small RTF string with bold text and a paragraph break → plain text without control words.
  - `test_doc_uses_antiword_when_present`: monkeypatch `shutil.which` (antiword found) and `subprocess.run` (returns stdout `hello`) → text `hello`.
  - `test_doc_falls_back_to_soffice`: antiword absent, soffice present, the fake `run` writes the converted `.txt` into the `--outdir` → text read from it.
  - `test_doc_without_any_converter`: both absent → `DocumentError` containing `convert the file to .docx`.
  - `test_doc_converter_failure_and_timeout`: non-zero exit → error containing the stderr text; `subprocess.TimeoutExpired` → error containing `timed out`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Add the dependencies** (`uv add openpyxl xlrd striprtf`), record licenses, extend `docfixtures.py`, implement the readers (lazy library imports inside each reader).
- [ ] **Step 4: Run** the new tests and the full suite → PASS.
- [ ] **Step 5: Commit** `feat: read spreadsheets, OpenDocument, RTF and legacy Word files`.

---

### Task 4: Configurable documents name — model, storage, export

**Files:**
- Modify: `src/promptharness/core/models.py`, `core/render.py`, `core/db.py`, `core/portable.py`, `core/runner.py`
- Test: `tests/test_render.py`, `tests/test_db.py`, `tests/test_portable.py`, `tests/test_runner.py`, `tests/test_models.py` (create if absent)

**Interfaces:**
- Produces:
  - `PromptVersion.documents_name: str = "documents"`; a validator accepts only valid Python identifiers that are not keywords and not `input` or `output` (message names the rule). `PromptVersion.hash` omits the field when it equals `"documents"`, so hashes of existing prompts are unchanged.
  - `render_user(template, input, documents, documents_name="documents")`: the list is available only under `documents_name`.
  - DB migration 3 appended to `MIGRATIONS`: `ALTER TABLE prompt_versions ADD COLUMN documents_name TEXT NOT NULL DEFAULT 'documents'`; `save_harness` writes it, `get_harness` reads it.
  - Export writes `prompt.documents_name` only when non-default; import accepts it (and its absence).
  - `core/runner.py` passes `prompt.documents_name` to `render_user`.
- Consumes: Task 1 `Document`.

- [ ] **Step 1: Write failing tests:**
  - `test_default_hashes_are_unchanged`: `PromptVersion(template="x").hash == "ff8205df1b67"` and `PromptVersion(system="You are terse.", template="{{ input }}\n{{ documents[0].text }}", temperature=0.2, max_tokens=512, extra_params={"top_p": 0.9}).hash == "fb175e1773d8"` (values computed from the code before this change).
  - `test_non_default_name_changes_the_hash`; `test_documents_name_validation` parametrised: `"doc"`, `"files"`, `"_d1"` valid; `"1x"`, `"a-b"`, `"class"`, `"input"`, `"output"`, `""`, `"a b"` raise `ValidationError`.
  - `test_render_with_custom_name`: template `{{ doc[0].text }}` with `documents_name="doc"` renders; the same template with the default name raises `TemplateRenderError` mentioning `doc`; `{{ documents[0].text }}` with `documents_name="doc"` raises `TemplateRenderError`.
  - `test_migration_3_adds_column`: build a v2 database (patch `MIGRATIONS` to the first two entries), insert a prompt row, reopen with all migrations → the row reads back with `documents_name == "documents"`; `schema_version() == len(MIGRATIONS)`.
  - `test_harness_roundtrip_keeps_documents_name` (save, `get_harness`, equal); two harnesses differing only in name are stored as two prompt versions.
  - `test_export_omits_default_name_and_includes_custom`: YAML for a default prompt has no `documents_name` key; for `"doc"` it has `documents_name: doc` under `prompt`; both round-trip through `parse_harness`.
  - `test_runner_uses_the_configured_name`: a harness with `documents_name="doc"` and template `{{ doc[0].text }}` run through the runner with a capturing fake client sends the document text; with a template using `documents[0]` the case ends `error` containing `undefined`.
- [ ] **Step 2: Run** the affected files → FAIL.
- [ ] **Step 3: Implement** the model field/validator/hash rule, the `render_user` parameter, migration 3 with the DB read/write changes, the export rule, and the runner call.
- [ ] **Step 4: Run** the full suite → PASS (existing hash-dependent tests must still pass unchanged).
- [ ] **Step 5: Commit** `feat: configurable documents variable name`.

---

### Task 5: Build the model request with images

**Files:**
- Create: `src/promptharness/core/messages.py`, `tests/test_messages.py`
- Modify: `src/promptharness/core/client.py`, `src/promptharness/core/runner.py`
- Test: `tests/test_client.py`, `tests/test_runner.py`

**Interfaces:**
- Produces (`core/messages.py`):
  - `build_user_content(text: str, documents: Sequence[Document]) -> str | list[dict]`: the string unchanged when no image documents; otherwise `[{"type":"text","text": text}, {"type":"image_url","image_url":{"url":"data:<mime>;base64,<b64>"}}, ...]` with images in document order.
  - `redact_images(messages: list[dict]) -> list[dict]`: a copy where every `data:<mime>;base64,…` URL becomes `data:<mime>;base64,<N bytes omitted>` (N = decoded size); messages with string content pass through.
  - `summarize_documents(documents: Sequence[Document]) -> list[dict]`: `{"name","kind","mime"}` plus `"chars"` (text) or `"bytes"` (image), plus `"pages"` when not None.
- Changes: `OpenAIChatClient.chat` records `redact_images(messages)` in `ChatResult.request["messages"]` (sending the real messages). `core/runner._evaluate` builds the user message with `build_user_content`, stores `request = {**chat.request, "messages": redact_images(chat.request["messages"]), "documents": summarize_documents(docs)}` on the `CaseResult`, appends every document's `warnings` to the result's warnings, and, when images were sent and `client.chat` raises `ClientError` of kind `other`, re-raises it with ` (the model may not support image input)` appended to the message.
- Consumes: Task 1 documents, Task 4 `documents_name`.

- [ ] **Step 1: Write failing tests:**
  - `tests/test_messages.py`: `test_text_only_is_a_plain_string`; `test_images_become_content_parts_in_order` (two images and one text doc → text part first, then two `image_url` parts whose URLs decode to the original bytes, in order); `test_redact_replaces_data_urls_and_keeps_text` (original list not mutated; `<N bytes omitted>` equals the decoded length); `test_summarize_documents_shape`.
  - `tests/test_client.py`: with the fake `AsyncOpenAI`, a content-list message is passed through to `create` unchanged while `ChatResult.request["messages"]` holds the redacted version (no base64 in `repr(result.request)`).
  - `tests/test_runner.py`: `test_image_case_sends_content_parts_and_stores_redacted_request` (FakeClient callable captures `messages`; result `request` contains no base64 and has `documents`); `test_plain_case_request_is_unchanged` (user content is a `str`); `test_document_warnings_reach_the_result` (a PDF with one blank page → warning in `CaseResult.warnings`); `test_one_bad_document_does_not_stop_other_cases` (two cases, the first with a binary `.dat`, the second plain → statuses `error`, `pass`); `test_non_vision_hint` (FakeClient raises `ClientError("other", "400 bad request")` for a case with an image → error contains `the model may not support image input`; the same error for a text-only case has no hint); `test_stored_run_has_no_base64` (save the run to a temp DB and read the `runs`/`case_results` rows back as text → no `base64,` payload longer than the placeholder).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `core/messages.py`, the client recording change, and the runner changes.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat: send images as content parts and record redacted requests`.

---

### Task 6: The judge sees the documents

**Files:**
- Modify: `src/promptharness/core/judge.py`, `src/promptharness/core/runner.py`
- Test: `tests/test_judge.py`, `tests/test_runner.py`

**Interfaces:**
- Produces: `run_judge(client, provider, model, judge_prompt: str, case_input: str, output: str, documents: Sequence[Document] = (), documents_name: str = "documents") -> CheckResult`.
  - The judge prompt is rendered with the sandboxed Jinja environment (`StrictUndefined`) with variables `input`, `output` and the documents under `documents_name`; a render error raises `JudgeError("judge prompt template error: <message>")`.
  - The user message sections, in order: `Criteria:`, `Input:`, `Documents:` (only when documents exist; each text document as `[<index>] <name>` then its text, each image as `[<index>] <name>` then its marker), `Output:` (stripped). With image documents the content is built by `build_user_content`, so images are attached.
  - `SYSTEM_PROMPT` gains: `Documents may be provided as reference material.`
  - The runner passes `docs` and `prompt.documents_name`.
- Consumes: Task 5 `build_user_content`; Task 4 name.

- [ ] **Step 1: Write failing tests** in `tests/test_judge.py`:
  - `test_documents_section_present_with_names_and_text` and order `Criteria` < `Input` < `Documents` < `Output`.
  - `test_no_documents_section_without_documents` (message identical in shape to today's).
  - `test_images_are_attached_for_the_judge` (content is a list with the image part).
  - `test_judge_prompt_can_reference_documents_by_the_configured_name`: prompt `Check {{ doc[1].name }} against {{ output }}` with `documents_name="doc"` renders the second file's name and the output into the Criteria section.
  - `test_plain_prompt_and_json_braces_render_unchanged`: prompt `Reply like {"pass": true}` appears verbatim.
  - `test_template_error_is_judge_error`: `{{ nope }}` and a literal `{{` raise `JudgeError` starting `judge prompt template error`; `{% raw %}{{ ok }}{% endraw %}` renders `{{ ok }}`.
  - Existing judge tests (including whitespace trimming and the retry logic) still pass unchanged.
  - `tests/test_runner.py`: `test_judge_receives_the_case_documents` (judge call's user message contains the document text) and `test_judge_template_error_gives_judge_error_status`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the changes in `judge.py` and pass the new arguments from the runner.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat: judge prompts are templates and the judge sees the documents`.

---

### Task 7: Portable export and import of binary documents

**Files:**
- Modify: `src/promptharness/core/portable.py`
- Test: `tests/test_portable.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: with `inline_documents=True`, per case `document_texts: [{name, text}]` for files that are valid UTF-8 text (as today) and `document_files: [{name, mime, base64}]` for all others; `MAX_INLINE_BYTES = 10 * 1024 * 1024` per file, over it → `PortableError("document <path> is larger than 10 MB and cannot be inlined")`. `_parse` also returns the decoded files (`dict[case name, dict[path, bytes]]`); invalid base64 or a missing key → `PortableError("invalid document_files in case '<name>'")`. `_restore_documents` writes missing binary files under `<data dir>/documents/<harness>/<safe basename>` with the same basename-only and collision rules as text, then rewrites the case path. Existing paths are left untouched. `parse_harness` keeps its public `(Harness, outputs)` return.
- Consumes: existing safe-name and rollback behaviour.

- [ ] **Step 1: Write failing tests:**
  - `test_binary_documents_round_trip`: a case with `invoice.docx` (from `make_docx`), `invoice.pdf` and a PNG; export with inline documents; delete the originals; import into a fresh DB → the case's paths exist under the data dir and `load_documents` gives the same kinds and text as before.
  - `test_text_and_binary_use_different_keys` (the YAML has `document_texts` for the `.txt` and `document_files` for the `.pdf`).
  - `test_existing_paths_are_left_alone` for binary files.
  - `test_oversized_file_is_refused` (monkeypatch `MAX_INLINE_BYTES` to 10) names the file.
  - `test_same_basename_in_different_folders_do_not_collide`: `a/report.pdf` and `b/report.pdf` with different content → both restored with distinct file names and correct content.
  - `test_bad_base64_is_a_portable_error_and_writes_nothing` and `test_path_traversal_name_in_document_files_is_neutralised`.
  - `tests/test_cli.py`: `export --inline-documents` then `import` of a harness with a PDF via the CLI works end to end.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** export/parse/restore changes.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat: inline binary documents in harness exports`.

---

### Task 8: Studio and result display

**Files:**
- Modify: `src/promptharness/tui/studio.py`, `src/promptharness/tui/studio_support.py`, `src/promptharness/tui/studio_modals.py`, `src/promptharness/tui/studio_persist.py` (only if prompt loading needs it)
- Test: `tests/test_tui_documents.py` (create)

**Interfaces:**
- Produces: a `#documents_name` `Input` (placeholder `documents`, label text "Docs as") in the Studio's top row. `current_prompt()` sets `documents_name` from it (blank → default); `apply_prompt` writes it back (blank when default); changes feed prompt history and `is_dirty`; an invalid name raises the existing `ValueError` path so a notification shows and the run does not start. `format_result` adds a line `documents: <name> (<kind>[, N pages]) · …` from `r.request["documents"]` when present. The case form's documents label states `(text, PDF, Word, PowerPoint, Excel, OpenDocument, RTF, images)`.
- Consumes: Tasks 4 and 5.

- [ ] **Step 1: Write failing tests** (Pilot, FakeClient; wait with the helpers used in `tests/test_tui_harnesses.py`, no fixed sleeps):
  - `test_docs_as_name_is_used_when_running`: set `#documents_name` to `doc`, template `{{ doc[0].text }}`, a case with one text file → the client received the file text.
  - `test_invalid_name_shows_a_notification_and_does_not_run` (`FakeClient` records zero calls).
  - `test_loading_a_harness_with_a_custom_name_shows_it`.
  - `test_name_participates_in_dirty_check_and_history` (change the name → `is_dirty()`; history undo restores it).
  - `test_result_header_lists_attached_documents_and_warnings`: a case with a text file and a PDF with a blank page → the output pane text contains `documents:`, both names and the warning.
  - `test_case_form_documents_label_lists_formats`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Keep `studio.py` under ~450 lines (split a helper into the existing support module if needed).
- [ ] **Step 4: Run** the full suite, running the new TUI tests three times for flakiness → PASS.
- [ ] **Step 5: Commit** `feat: documents name in the Studio and document details in results`.

---

### Task 9: Bundled examples and sample documents

**Files:**
- Create: `scripts/build_document_examples.py`, `src/promptharness/examples/three-documents.harness.yaml`, `src/promptharness/examples/mixed-formats.harness.yaml`, `docs/sample-documents/invoice-1041.docx`, `docs/sample-documents/invoice-1042.pdf`, `docs/sample-documents/invoice-1043.png`
- Modify: `scripts/docfixtures.py` (add `make_png(lines: list[str]) -> bytes` using Pillow's default font), `pyproject.toml` (add `pillow` to the dev group), `tests/test_examples.py`, `tests/test_cli_examples.py`, `tests/test_tui_examples.py` (update expected example lists)

**Interfaces:**
- Produces: bundled examples `three-documents` and `mixed-formats` (importable by name). The data: invoice 1041 (ACME Supplies, 120.00 EUR, due 2026-11-01), 1042 (Borealis Ltd, 80.50 EUR, due 2026-11-15), 1043 (Cobalt GmbH, 200.00 EUR, due 2026-12-01); sum 400.50, earliest due 2026-11-01, largest 1043.
  - `three-documents`: three text documents inlined as `document_texts` (`invoice-1041.txt` etc.); template refers to `documents[0]`, `documents[1]`, `documents[2]` with their names; cases `total-and-due-date` (JSON, schema requiring `total` number and `earliest_due` string matching `^\d{4}-\d{2}-\d{2}$`; must-include regexes `"earliest_due"\s*:\s*"2026-11-01"` and `400\.5`) and `largest-invoice` (must include `1043`, must not include `1041` or `1042`, with a `judge_prompt` using `{{ documents[0].name }}`, `{{ documents[1].name }}` and `{{ documents[2].name }}`). No accepted model.
  - `mixed-formats`: the same invoices as `.docx`, `.pdf` and `.png` inlined as `document_files`; template loops over `documents`; one case `total-and-due-date` with the same checks; the system prompt tells the model one document is an image. No accepted model.
  - The build script regenerates both YAMLs and `docs/sample-documents/*` deterministically from one data definition.
- Consumes: Tasks 1–3 readers, Task 7 inlining.

- [ ] **Step 1: Write failing tests** in `tests/test_examples.py` (and update the expected lists in the CLI/TUI example tests to `["mixed-formats","quickstart","summarize","three-documents"]`):
  - `test_three_documents_example_parses_and_imports`: after `import_harness` into a temp data dir, the cases' documents exist, `load_documents` gives three text documents containing `1041`, `1042`, `1043`, and the rendered prompt contains all three invoice texts in order.
  - `test_mixed_formats_example_restores_binary_documents`: `load_documents` gives kinds `["text","text","image"]` (docx text contains `1041`, pdf text contains `1042`), image mime `image/png`.
  - `test_checks_accept_good_and_reject_bad_answers` for both examples' `total-and-due-date` and the three-documents `largest-invoice` case (good: `{"total": 400.5, "earliest_due": "2026-11-01"}` and `1043`; bad: wrong total, wrong date, `1041`).
  - `test_build_script_output_matches_committed_files`: run the build script's function into a temp dir and compare the YAML text to the committed examples (guards drift).
  - `test_sample_documents_are_readable`: `load_documents` on `docs/sample-documents/*` works.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `make_png`, the build script and generate the committed files.
- [ ] **Step 4: Run** the full suite → PASS; run `promptharness examples` and confirm four entries.
- [ ] **Step 5: Commit** `feat: add three-documents and mixed-formats examples`.

---

### Task 10: README, real-model verification

**Files:**
- Modify: `README.md`
- Create: none (verification evidence goes in the task report)

**Interfaces:**
- Produces: a README "Documents" section covering the formats table, how to reference documents (`documents[0].text`, loops, `.name`, `.kind`, `.index`), the configurable name (and that only one name is active), images and vision models (and the hinted error), what the judge receives, limits and warnings (spreadsheet cap, scanned PDFs, 20 MB images, `.doc` converter requirement), inline export of binary files with the 10 MB limit, and the two new bundled examples (`promptharness import --example three-documents`). The subcommand mentions and Features list are updated.
- Consumes: everything above.

- [ ] **Step 1: Manual verification against LM Studio** (`http://localhost:1234/v1`). Load only the models you need through the REST API and unload each one afterwards; use a temp `PROMPTHARNESS_HOME`. Record exact commands and outputs in the report:
  - `three-documents` against `prism-ml/bonsai-27b` with `--judge` set to the same model: report the case statuses.
  - `mixed-formats` against `prism-ml/bonsai-27b` (vision): report whether the image content was read (the answer must use the PNG's invoice data to be correct) and the statuses.
  - `mixed-formats` against `openai/gpt-oss-20b` (no vision): report the hinted error text.
  - Any defect found goes in the report; fix only what belongs to this plan's code, with a test, in a separate commit.
- [ ] **Step 2: Write the README section** accurately from the code (check every flag, key and message by running it).
- [ ] **Step 3: Verify links and anchors** in the README resolve.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `docs: document the documents feature`.

---

### Task 11: Working-with-documents guide

**Files:**
- Create: `docs/working-with-documents.md`, `scripts/make_documents_guide_screenshots.py`, `docs/screenshots/documents-guide/*.png`
- Modify: `README.md` (link the guide), `docs/getting-started.md` (one-line pointer in "Where to go next")

**Interfaces:**
- Produces: a step-by-step guide that uses LM Studio locally and the sample files in `docs/sample-documents/`. It must be written from what really happens on real runs: attach the three documents in the Studio (case form with the three paths); write a deliberately vague first prompt and show its real, weak result; edit the system prompt/template and re-run until the answer is right, using prompt history to step back; add checks and a judge prompt that refers to the documents; save as a harness; run it against a second model in the regression matrix; export it. Uses `prism-ml/bonsai-27b` (vision-capable) so the PNG is read. Screenshots (PNG, width 125 columns like the getting-started set) come from `scripts/make_documents_guide_screenshots.py`, which drives the real TUI against a real server with `--base-url`, `--model`, `--only` options, mirroring `scripts/make_getting_started_screenshots.py`.
- Consumes: Tasks 1–10.

- [ ] **Step 1: Explore the real flow by hand-in-code** against LM Studio (temp data dir): try the vague prompt, see what the model does, find a real failure to fix, and note each prompt edit and result. If the vague prompt happens to pass, make it vaguer rather than inventing a failure; the guide must show only results that actually occurred.
- [ ] **Step 2: Write the screenshot script** and generate the images; view every PNG and fix any clipped or empty frame (adjust terminal sizes per shot).
- [ ] **Step 3: Write the guide** around those real results: short numbered steps, the key presses used, the exact prompt text at each iteration, and troubleshooting (non-vision model, scanned PDF, large documents, wrong name).
- [ ] **Step 4: Verify** every key press and command in the guide by scripting it with real key presses (as done for the getting-started guide); verify links and images resolve; check no private address appears in text or screenshots.
- [ ] **Step 5: Run** the full suite → PASS; **Commit** `docs: add the working-with-documents guide with screenshots`.
