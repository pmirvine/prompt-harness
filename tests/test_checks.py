from promptharness.core.checks import normalize, run_checks
from promptharness.core.models import Expectation, Match


def by_name(results):
    return {r.name: r for r in results}


def test_include_substring_pass_and_fail():
    exp = Expectation(must_include=[Match(pattern="Hello")])
    assert run_checks("say Hello", exp)[0].passed is True
    r = run_checks("say hello", exp)[0]
    assert r.passed is False
    assert r.name == "include:Hello"


def test_must_not_include_fails_when_present():
    exp = Expectation(must_not_include=[Match(pattern="bad")])
    r = run_checks("this is bad", exp)[0]
    assert r.name == "exclude:bad"
    assert r.passed is False
    assert run_checks("fine", exp)[0].passed is True


def test_regex_include():
    exp = Expectation(must_include=[Match(pattern=r"\d{3}", regex=True)])
    assert run_checks("call 555", exp)[0].passed is True
    assert run_checks("call", exp)[0].passed is False


def test_invalid_regex_is_failed_check_not_exception():
    exp = Expectation(must_include=[Match(pattern="(", regex=True)])
    r = run_checks("x", exp)[0]
    assert r.passed is False
    assert "invalid regex" in r.reason
    exp = Expectation(must_not_include=[Match(pattern="(", regex=True)])
    r = run_checks("x", exp)[0]
    assert r.passed is False
    assert "invalid regex" in r.reason


def test_exact_vs_normalized():
    out = " Hello   World\n"
    assert run_checks(out, Expectation(exact="Hello   World"))[0].passed is False
    assert run_checks(out, Expectation(normalized="hello world"))[0].passed is True
    assert normalize(out) == "hello world"


def test_json_output_valid_invalid():
    exp = Expectation(json_output=True)
    assert run_checks('{"a":1}', exp)[0].passed is True
    r = run_checks("nope", exp)[0]
    assert r.passed is False and r.reason
    assert run_checks('```json\n{"a":1}\n```', exp)[0].passed is True


def test_json_schema():
    exp = Expectation(json_schema={"type": "object", "required": ["a"]})
    r = run_checks('{"b":1}', exp)[0]
    assert r.name == "json_schema"
    assert r.passed is False and "a" in r.reason
    assert run_checks('{"a":1}', exp)[0].passed is True
    r = run_checks("nope", exp)[0]
    assert r.passed is False and "JSON" in r.reason


def test_order_of_checks():
    exp = Expectation(
        must_include=[Match(pattern="a")],
        must_not_include=[Match(pattern="z")],
        exact="a",
        normalized="a",
        json_output=True,
        json_schema={"type": "string"},
    )
    names = [r.name for r in run_checks("a", exp)]
    assert names == ["include:a", "exclude:z", "exact", "normalized", "json", "json_schema"]


def test_no_expectations_returns_empty_list():
    assert run_checks("anything", Expectation()) == []
