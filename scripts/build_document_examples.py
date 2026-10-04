"""Regenerate the document examples and the sample documents.

    uv run python scripts/build_document_examples.py

Writes src/promptharness/examples/{three-documents,mixed-formats}.harness.yaml and
docs/sample-documents/*. The harnesses are built in code, saved to a throwaway database
and written by the real exporter, so the YAML cannot drift from the export format.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docfixtures import make_docx, make_pdf, make_png  # noqa: E402

from promptharness.core.db import Database  # noqa: E402
from promptharness.core.models import Case, Expectation, Harness, Match, PromptVersion  # noqa: E402
from promptharness.core.portable import export_harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

INVOICES = [
    {"number": "1041", "supplier": "ACME Supplies", "total": "120.00", "due": "2026-11-01"},
    {"number": "1042", "supplier": "Borealis Ltd", "total": "80.50", "due": "2026-11-15"},
    {"number": "1043", "supplier": "Cobalt GmbH", "total": "200.00", "due": "2026-12-01"},
]
# Sum 400.50, earliest due 2026-11-01, largest invoice 1043.


def invoice_lines(inv: dict) -> list[str]:
    return [
        f"Invoice {inv['number']}",
        f"Supplier: {inv['supplier']}",
        f"Total: {inv['total']} EUR",
        f"Due date: {inv['due']}",
    ]


TOTAL_INPUT = (
    'Reply with only a JSON object with the keys "total" (number, the sum of all three '
    'invoice totals in EUR) and "earliest_due" (string, the earliest due date as YYYY-MM-DD).'
)
TOTAL_NOTES = "Sum is 400.50 EUR and the earliest due date is 2026-11-01."
TOTAL_EXPECTATION = Expectation(
    json_output=True,
    json_schema={
        "type": "object",
        "required": ["total", "earliest_due"],
        "properties": {
            "total": {"type": "number"},
            "earliest_due": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        },
    },
    must_include=[
        Match(pattern=r'"earliest_due"\s*:\s*"2026-11-01"', regex=True),
        Match(pattern=r"(?<![\d.])400\.50?(?!\d)", regex=True),
    ],
)
SYSTEM = "You are a precise assistant. Answer exactly as asked, using only the documents provided."


def _three_documents() -> Harness:
    template = "{{ input }}\n\n" + "\n\n".join(
        f"Invoice {k} ({{{{ documents[{i}].name }}}}):\n{{{{ documents[{i}].text }}}}"
        for i, k in enumerate("ABC")
    )
    names = [f"invoice-{inv['number']}.txt" for inv in INVOICES]
    judge = (
        "The answer must name the invoice with the highest total among these documents: "
        "{{ documents[0].name }}, {{ documents[1].name }} and {{ documents[2].name }}."
    )
    return Harness(
        name="three-documents",
        description=(
            "Three text invoices attached to every case: sum their totals, find the earliest "
            "due date and pick the largest invoice."
        ),
        prompt=PromptVersion(system=SYSTEM, template=template, temperature=0.0, max_tokens=2048),
        cases=[
            Case(
                name="total-and-due-date",
                input=TOTAL_INPUT,
                documents=names,
                notes=TOTAL_NOTES,
                expectation=TOTAL_EXPECTATION,
            ),
            Case(
                name="largest-invoice",
                input="Which invoice has the largest total? Reply with only the invoice number.",
                documents=names,
                notes="Invoice 1043 (200.00 EUR) is the largest.",
                expectation=Expectation(
                    must_include=[Match(pattern="1043")],
                    must_not_include=[Match(pattern="1041"), Match(pattern="1042")],
                    judge_prompt=judge,
                ),
            ),
        ],
    )


def _mixed_formats() -> Harness:
    return Harness(
        name="mixed-formats",
        description=(
            "The same three invoices as a Word file, a PDF and a PNG image. "
            "Needs a model that accepts images."
        ),
        prompt=PromptVersion(
            system=SYSTEM + " One of the documents is an image; read the text in it.",
            template=(
                "{{ input }}\n\n{% for d in documents %}Document {{ loop.index }}: {{ d.name }}\n"
                "{{ d.text }}\n\n{% endfor %}"
            ),
            temperature=0.0,
            max_tokens=2048,
        ),
        cases=[
            Case(
                name="total-and-due-date",
                input=TOTAL_INPUT,
                documents=["invoice-1041.docx", "invoice-1042.pdf", "invoice-1043.png"],
                notes=TOTAL_NOTES,
                expectation=TOTAL_EXPECTATION,
            )
        ],
    )


def _sample_files() -> dict[str, bytes]:
    a, b, c = INVOICES
    docx_blocks = ["Invoice " + a["number"], *invoice_lines(a)[1:]]
    return {
        "invoice-1041.docx": make_docx(docx_blocks),
        "invoice-1042.pdf": make_pdf([" | ".join(invoice_lines(b))]),
        "invoice-1043.png": make_png(invoice_lines(c)),
    }


def build(out_dir: Path, samples_dir: Path | None = None) -> None:
    """Write the example YAMLs to `out_dir` and the sample documents to `samples_dir`."""
    out_dir = Path(out_dir)
    samples_dir = Path(samples_dir) if samples_dir is not None else out_dir / "sample-documents"
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_dir.mkdir(parents=True, exist_ok=True)
    samples = _sample_files()
    for fn, data in samples.items():
        (samples_dir / fn).write_bytes(data)

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for inv in INVOICES:
            (work / f"invoice-{inv['number']}.txt").write_text(
                "\n".join(invoice_lines(inv)) + "\n", encoding="utf-8"
            )
        for fn, data in samples.items():
            (work / fn).write_bytes(data)
        db = Database(work / "build.db")
        db.migrate()
        try:
            os.chdir(work)  # bare relative document paths, so exported names are plain
            for h in (_three_documents(), _mixed_formats()):
                db.save_harness(h)
                text = export_harness(db, h.name, "yaml", inline_documents=True)
                (out_dir / f"{h.name}.harness.yaml").write_text(text, encoding="utf-8")
        finally:
            os.chdir(cwd)
            db.close()


def main() -> None:
    build(ROOT / "src" / "promptharness" / "examples", ROOT / "docs" / "sample-documents")


if __name__ == "__main__":
    main()
