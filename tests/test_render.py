import pytest
from pydantic import ValidationError

from promptharness.core.models import Case, Harness, ModelRef, PromptVersion
from promptharness.core.render import (
    Document,
    DocumentError,
    TemplateRenderError,
    load_documents,
    render_user,
)


def test_render_input_and_documents():
    t = "{{ input }}|{% for d in documents %}{{ d.name }}={{ d.text }};{% endfor %}"
    assert render_user(t, "hi", [Document(name="a.txt", text="A")]) == "hi|a.txt=A;"


def test_undefined_variable_raises():
    with pytest.raises(TemplateRenderError):
        render_user("{{ nope }}", "", [])


def test_syntax_error_raises():
    with pytest.raises(TemplateRenderError):
        render_user("{% if %}", "", [])


def test_load_documents_text(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("A")
    assert load_documents([str(p)]) == [Document(name="a.txt", text="A")]


def test_load_documents_binary_unsupported(tmp_path):
    p = tmp_path / "b.bin"
    p.write_bytes(b"\x00\x01")
    with pytest.raises(DocumentError, match="unsupported"):
        load_documents([str(p)])


def test_load_documents_missing(tmp_path):
    with pytest.raises(DocumentError, match="unsupported"):
        load_documents([str(tmp_path / "nope.txt")])


def test_prompt_hash_stable_and_sensitive():
    a = PromptVersion(system="s", template="t", temperature=0.1)
    b = PromptVersion(system="s", template="t", temperature=0.1)
    c = PromptVersion(system="s", template="t", temperature=0.2)
    assert a.hash == b.hash and len(a.hash) == 12
    assert a.hash != c.hash


def test_duplicate_case_names_rejected():
    with pytest.raises(ValidationError):
        Harness(
            name="h",
            prompt=PromptVersion(template="t"),
            cases=[Case(name="a"), Case(name="a")],
        )


def test_modelref_parse():
    assert str(ModelRef.parse("openai:gpt-4o")) == "openai:gpt-4o"
    with pytest.raises(ValueError):
        ModelRef.parse("nocolon")
    assert ModelRef.parse("or:meta/llama:free").model == "meta/llama:free"


def test_render_with_custom_name():
    docs = [Document(name="a.txt", text="A")]
    assert render_user("{{ doc[0].text }}", "hi", docs, documents_name="doc") == "A"
    with pytest.raises(TemplateRenderError, match="doc"):
        render_user("{{ doc[0].text }}", "hi", docs)
    with pytest.raises(TemplateRenderError, match="documents"):
        render_user("{{ documents[0].text }}", "hi", docs, documents_name="doc")


def test_render_passes_variables_as_dict():
    docs = [Document(name="a.txt", text="A")]
    assert render_user("{{ input }}{{ files[0].text }}", "hi", docs, "files") == "hiA"
