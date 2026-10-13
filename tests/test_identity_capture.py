"""Tests for conversational name capture on the web companion path."""

from __future__ import annotations

import pytest

from apps.api.routers import mini


@pytest.mark.parametrize(
    "text,expected",
    [
        ("My name is Karan and I'll call you Pixel", ("Karan", "Pixel")),
        ("I'm Karan", ("Karan", None)),
        ("call me Sara", ("Sara", None)),
        ("you can call you Umi", (None, "Umi")),
        ("your name is Maiko", (None, "Maiko")),
        ("this is Ravi here", ("Ravi", None)),
        ("I'm good, how are you", (None, None)),
        ("hello there", (None, None)),
        ("i'm feeling tired today", (None, None)),
    ],
)
def test_extract_names(text, expected):
    assert mini._extract_names(text) == expected


def test_extract_names_json_fallback():
    # Falls back to the model's optional JSON keys when the user's phrasing misses.
    raw = '{"emotion":"happy","response":"hi","user_name":"Neha","bot_name":"Robo"}'
    assert mini._extract_names("greetings", raw) == ("Neha", "Robo")


def test_user_text_wins_over_json():
    raw = '{"user_name":"Wrong"}'
    assert mini._extract_names("my name is Karan", raw)[0] == "Karan"


def test_clean_name_filters_placeholders():
    assert mini._clean_name("good") is None
    assert mini._clean_name("  ") is None
    assert mini._clean_name("karan") == "Karan"
