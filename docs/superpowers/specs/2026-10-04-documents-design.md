# Documents — Design Spec

Date: 2026-10-04
Extends: `2026-10-03-promptharness-design.md`

## Purpose

Let a test case attach documents of many kinds (text, PDF, Word, PowerPoint, Excel, OpenDocument, RTF, legacy `.doc`, and images), refer to them from the prompt template under a user-configurable name, and let the LLM judge see them too. Ship a three-document example, a mixed-formats example, and a second step-by-step guide.

## Decisions (from the design discussion)

1. Add runtime dependencies for reading documents (see Dependencies).
2. A PDF with no extractable text (a scan) is a clear per-case error in this version. No page rendering, no OCR.
3. Include office formats beyond PDF and Word: PowerPoint, Excel, OpenDocument, RTF, and legacy `.doc`.
4. An image sent to a model without vision support surfaces the server's error plus a hint. No OCR.
5. The judge always receives a case's documents automatically, and judge prompts may also refer to them by the configured name.
6. The reference name is configurable and defaults to `documents`. Exactly one name is active.

## Goals and non-goals

Goals: documents of the formats below in templates and judge prompts; images as vision input; configurable reference name; per-case, specific error messages; portable export of binary documents; no change to existing harnesses, hashes or run history.

Non-goals: OCR; rendering scanned PDF pages; extracting images embedded inside PDF/Word/PowerPoint files; audio or video; remote URLs (the app makes no network calls except to providers); password prompts for protected files; automatic truncation of long text (a server context error is shown per case, as today).

## Supported formats

A reader is chosen by file extension (case-insensitive) and sanity-checked against the file's leading bytes. The result is either text (`kind="text"`) or an image (`kind="image"`).

| Extension(s) | Kind | How it is read |
|---|---|---|
| `.png` `.jpg` `.jpeg` `.gif` `.webp` | image | Bytes kept as-is; MIME from the leading bytes (not the extension); max 20 MB |
| `.pdf` | text | `pypdf`: text of each page, pages separated by a `--- page N ---` line |
| `.docx` | text | `python-docx`: paragraphs and tables in document order; table rows as ` \| `-separated lines |
| `.pptx` | text | `python-pptx`: per slide, `--- slide N ---`, then shape text, tables, and speaker notes |
| `.xlsx` | text | `openpyxl` (read-only, cached values): per sheet, `--- sheet NAME ---`, then tab-separated rows |
| `.xls` | text | `xlrd`: same layout as `.xlsx` |
| `.odt` `.ods` `.odp` | text | Standard library `zipfile` + `ElementTree` on `content.xml` |
| `.rtf` | text | `striprtf` |
| `.doc` (legacy Word) | text | Best effort through an external converter if one is installed: `antiword`, else `soffice`/`libreoffice --headless --convert-to txt`; 60 s timeout. Otherwise a clear error says to convert the file to `.docx` |
| anything else | text | Must be valid UTF-8 without NUL bytes (today's behaviour), else `unsupported` |

Limits and warnings:
- Spreadsheets read at most 5,000 rows per sheet; truncation adds a document warning.
- A PDF page with no text adds a warning (`<name>: page N has no extractable text`). If no page has text the document is an error (`no extractable text; it may be a scan. Convert the pages to images and attach those`).
- An encrypted PDF is tried with an empty password; if that fails it is an error.
- A corrupt file, a missing file, an extension that does not match the content (for example a `.pdf` that does not start with `%PDF`), an image over 20 MB, and an unsupported image type (anything but the four above) are each a per-case `error` whose text starts with `unsupported:` and names the file and the reason.
- Document warnings are copied into the case result's warnings.

## Document model and templates

`core/render.py` keeps `Document` and `load_documents(paths) -> list[Document]` (now backed by a `core/documents.py` reader registry):

```python
@dataclass(frozen=True)
class Document:
    name: str                 # file base name
    text: str                 # extracted text; for an image, "[attached image: <name>]"
    kind: Literal["text", "image"] = "text"
    mime: str = "text/plain"
    index: int = 0            # position in the case's documents list
    pages: int | None = None  # PDF pages, PPTX slides, XLSX sheets when known
    warnings: tuple[str, ...] = ()
    _data: bytes | None = None  # image bytes; underscore-prefixed so the template sandbox cannot read it
```

Templates see each document's `name`, `text`, `kind`, `mime`, `index` and `pages`.

`render_user(template, input, documents, documents_name="documents")` makes the list available under `documents_name` (and `input` as before). Only that name is defined, so a template using any other name gets the existing clear `'x' is undefined` error.

## Configurable reference name

`PromptVersion.documents_name: str = "documents"`. It must be a valid Python identifier, not a keyword, and not `input` or `output`; otherwise validation fails with a clear message.

- **Identity:** `PromptVersion.hash` omits the field when it is the default, so every existing prompt keeps its hash and existing run history stays comparable.
- **Storage:** DB migration 3 adds `documents_name TEXT NOT NULL DEFAULT 'documents'` to `prompt_versions`. `save_harness` and `get_harness` read and write it.
- **Export/import:** `prompt.documents_name` is written only when non-default and is optional on import. `format_version` stays 1.
- **Studio:** a "Docs as" input beside temperature and max tokens. It takes part in prompt history and in the in-session prompt hash. An invalid name shows a notification and the run does not start.

## Building the model request

New `core/messages.py`:
- `build_user_content(text, documents) -> str | list[dict]`. With no image documents it returns the string unchanged. Otherwise it returns content parts: one `{"type": "text", "text": text}` followed by one `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,<data>"}}` per image, in document order.
- `redact_images(messages) -> list[dict]` returns a copy with each data URL replaced by `data:<mime>;base64,<N bytes omitted>`.

`OpenAIChatClient.chat` sends the real messages but records the redacted copy in `ChatResult.request`, so stored runs do not hold megabytes of base64. The recorded `request` also gains `"documents": [{"name", "kind", "mime", "chars" or "bytes", "pages"}]` so the UI can show what was attached.

When images were sent and the server answers with a client error of kind `other`, the case error is `other: <server message> (the model may not support image input)`.

## Judge

- `run_judge(client, provider, model, judge_prompt, case_input, output, documents=(), documents_name="documents")`.
- The judge prompt is rendered as a sandboxed Jinja template with `input`, `output`, and the documents under `documents_name`. A plain prompt renders unchanged. A template error raises `JudgeError("judge prompt template error: …")`, so the case ends `judge_error` with that message and never crashes. `{% raw %}…{% endraw %}` escapes literal braces.
- The judge user message has the sections `Criteria`, `Input`, `Documents` (only when the case has documents), and `Output`. Each text document appears as `[<index>] <name>` followed by its text. Each image document appears as its marker, and the image itself is attached as an image part. `output` is still stripped of surrounding whitespace.
- The runner passes the case's already-loaded documents and the prompt's `documents_name`. The judge system prompt gains one sentence saying documents may be given as reference material.

## Portable export and import

`export --inline-documents` writes, per case, `document_texts: [{name, text}]` for documents that are valid UTF-8 text (as today) and `document_files: [{name, mime, base64}]` for every other document. Each inlined file is limited to 10 MB; larger files raise `PortableError` naming the file. Import restores a missing document under `<data dir>/documents/<harness>/<safe basename>` exactly as for text today (basename only, no path traversal, collisions renamed), including decoding `document_files`, and rewrites the case's path. Existing paths are left alone. Failures roll back as they do today.

## UI

- **Studio case editor:** the documents field stays a comma-separated path list. Its label states the supported formats.
- **Result display:** the result header lists the attached documents (`a.docx (text) · b.pdf (text, 2 pages) · c.png (image)`) from the recorded request, and shows document warnings.
- **Studio:** "Docs as" input as above.
- **TUI import box and CLI:** unchanged except that bundled example names now include the two new examples.

## Bundled examples

Both ship in `src/promptharness/examples/` and import by name.

- `three-documents`: three short invoices as text documents (inlined with `document_texts`). The template refers to `documents[0]`, `documents[1]` and `documents[2]` explicitly. Cases: `total-and-due-date` (JSON with a number and a date, checked with a JSON Schema and a must-include) and `largest-invoice` (the invoice number, with a judge prompt that refers to the documents by name).
- `mixed-formats`: the same three invoices as a `.docx`, a `.pdf` and a `.png` receipt (inlined with `document_files`). Needs a vision-capable model. A non-vision model shows the image-support hint.
- `scripts/build_document_examples.py` generates both YAML files and their embedded files from one source of truth, so they are reproducible. It uses Pillow and a small PDF writer, which are development-only dependencies of the script.

## Guides

- `README.md` gains a Documents section: the formats table, how to refer to documents, the configurable name, non-vision models, limits, and the `.doc` converter note.
- `docs/working-with-documents.md` (new), run against LM Studio with real screenshots, taking a reader through: attaching three documents in the Studio, writing a deliberately vague first prompt, reading the weak result, editing and re-running until it is right, adding checks and a judge prompt that refers to the documents, saving the harness, and re-running it on a second model. It is written from what actually happens on the real runs.
- `scripts/make_documents_guide_screenshots.py` regenerates its screenshots against a real server, like the getting-started script.

## Dependencies

Runtime: `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `xlrd`, `striprtf` (transitively `lxml`, `Pillow`, `XlsxWriter`). Licenses are verified when each is added and recorded in the commit; any non-permissive license stops the work. Development only: whatever `scripts/build_document_examples.py` needs to draw the sample image and PDF.

## Compatibility

- Existing harnesses, prompt hashes, runs and exports are unchanged (a test pins a known hash).
- A new export read by an older version ignores the optional keys it does not know.
- Plain-text cases send exactly the same request as before.

## Error handling summary

Every document problem is a per-case `error` and never crashes a run. An unusable document does not stop the other cases. Judge problems stay `judge_error`. The judge not receiving images (a non-vision judge model) surfaces as the same hinted error.

## Testing

- Readers: one test per format with generated fixtures (python-docx, python-pptx and openpyxl write theirs; ODF is built as a zip; RTF is a string; PDFs are small hand-written files; an `.xls` fixture is committed), plus the error paths: corrupt, mismatched extension, scan-only PDF, encrypted PDF, oversized image, unsupported image type, spreadsheet truncation warning, and the `.doc` converter present/absent (mocked).
- Messages: plain string without images, content parts with images in order, redaction, request `documents` summary, the non-vision hint.
- Configurable name: validation, hash stability (a literal known hash), DB migration 3 from version 2, export/import round trip, template error for the wrong name, Studio field.
- Judge: documents in the request, images attached, template rendering of `input`/`output`/documents, template error → `judge_error`, plain prompts unchanged.
- Portable: binary inline round trip, size limit, safe names.
- Studio and UI: documents summary in the result header; "Docs as" input.
- Examples: both parse, import, and their checks accept good answers and reject bad ones.
- Manual verification against LM Studio during implementation (not in the automated suite): text documents, a PDF, a Word file and an image on a vision-capable model (`prism-ml/bonsai-27b`), and the hinted error on a non-vision model (`openai/gpt-oss-20b`).

## Deliverables

Working feature with the tests above, the two examples, the README section, the new guide with screenshots, and the two scripts.
