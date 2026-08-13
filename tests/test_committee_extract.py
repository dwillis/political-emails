"""Regression tests for the deterministic committee extractor and its bug fixes."""

import pytest

from committee_extract import (
    extract_committee,
    extract_from_tail,
    looks_confident,
    strip_parenthetical,
    trim_after_comma,
)


# --- bug fix (a): legal-entity comma no longer beheads the name ---

def test_comma_keeps_legal_entity_suffix():
    assert trim_after_comma("Never Surrender, Inc. blah") == "Never Surrender, Inc. blah"
    assert trim_after_comma("Trump National Committee JFC, Inc.") == (
        "Trump National Committee JFC, Inc."
    )


def test_comma_still_trims_address():
    assert trim_after_comma("Smith PAC, 123 Main St") == "Smith PAC"


def test_extract_keeps_inc():
    assert extract_committee("Paid for by Never Surrender, Inc. and not authorized") == (
        "Never Surrender, Inc"
    )


# --- bug fix (b): CamelCase surnames are not split ---

def test_camelcase_surname_preserved():
    assert extract_committee("Paid for by Ron DeSantis for President. www.x.com") == (
        "Ron DeSantis for President"
    )


# --- bug fix (c): meaningful parentheticals kept, junk stripped ---

def test_short_parenthetical_kept():
    assert strip_parenthetical("Nicole for New York (Federal)") == (
        "Nicole for New York (Federal)"
    )
    assert extract_committee("Paid for by Nicole for New York (Federal). PO Box 5") == (
        "Nicole for New York (Federal)"
    )


def test_long_parenthetical_stripped():
    assert strip_parenthetical("Foo (a long parenthetical clause here)") == "Foo"


def test_looks_confident_paren_balance():
    assert looks_confident("Nicole for New York (Federal)") is True
    assert looks_confident("Foo (Bar") is False


# --- narrowed legal-suffix truncation: common words no longer truncate ---

@pytest.mark.parametrize(
    "body,expected",
    [
        ("Paid for by Heritage Action for America 123 Main St", "Heritage Action for America"),
        ("Paid for by Democratic Party of Georgia. Unsubscribe here", "Democratic Party of Georgia"),
    ],
)
def test_common_words_not_truncated(body, expected):
    assert extract_committee(body) == expected


# --- bug fix (d) + abbreviation periods ---

def test_email_is_boilerplate_stops():
    assert extract_committee(
        "Paid for by Seth for Massachusetts Email is a critical way to stay in touch"
    ) == "Seth for Massachusetts"


def test_abbreviation_period_not_sentence_boundary():
    assert extract_committee("Paid for by Dr. Kim Schrier for Congress") == (
        "Dr. Kim Schrier for Congress"
    )


# --- ported behaviors that must still hold ---

def test_duplicated_phrase_collapse():
    assert extract_from_tail("Levine for VA Levine for VA") == "Levine for VA"


def test_leading_the_dropped():
    assert extract_committee("Paid for by The Collective PAC. Unsubscribe") == "Collective PAC"


def test_no_disclaimer_returns_none():
    assert extract_committee("Just a normal email with no disclaimer at all") is None
    assert extract_committee("") is None
    assert extract_committee(None) is None
