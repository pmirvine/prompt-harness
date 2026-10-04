import pytest
from pydantic import ValidationError

from promptharness.core.models import PromptVersion


def test_default_hashes_are_unchanged():
    assert PromptVersion(template="x").hash == "ff8205df1b67"
    rich = PromptVersion(
        system="You are terse.",
        template="{{ input }}\n{{ documents[0].text }}",
        temperature=0.2,
        max_tokens=512,
        extra_params={"top_p": 0.9},
    )
    assert rich.hash == "fb175e1773d8"


def test_non_default_name_changes_the_hash():
    assert PromptVersion(template="x", documents_name="doc").hash != PromptVersion(template="x").hash


@pytest.mark.parametrize("name", ["doc", "files", "_d1"])
def test_documents_name_valid(name):
    assert PromptVersion(template="x", documents_name=name).documents_name == name


@pytest.mark.parametrize("name", ["1x", "a-b", "class", "input", "output", "", "a b"])
def test_documents_name_validation(name):
    with pytest.raises(ValidationError):
        PromptVersion(template="x", documents_name=name)
