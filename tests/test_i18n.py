"""Tests for the message table itself.

It tests structural defects (a missing translation, a mismatched placeholder, a duplicate
key); any of those reaching a release shows the user a ``KeyError`` or a stray ``{count}``.
"""

from __future__ import annotations

import ast
import pathlib
import string
from collections import Counter
from collections.abc import Iterator

import pytest

from mcpdump import i18n

#: Every environment variable the language detection reads
LOCALE_KEYS = ("MCPDUMP_LANG", "LC_ALL", "LC_MESSAGES", "LANG")


def _fields(template: str) -> list[tuple[str, str, str]]:
    """Extract the ``(field, conversion, format spec)`` triples from a template."""
    found: list[tuple[str, str, str]] = []
    for _, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        found.append((field, conversion or "", spec or ""))
    return found


def _sample_arguments(template: str) -> dict[str, object]:
    """Build sample values for a template: numbers for spec'd fields, strings otherwise."""
    arguments: dict[str, object] = {}
    for name, _, spec in _fields(template):
        arguments[name] = 1.0 if spec else "X"
    return arguments


@pytest.fixture
def clean_locale(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear every locale variable so detection depends only on what the test sets."""
    for key in LOCALE_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


class TestTableIntegrity:
    def test_every_key_has_both_languages(self) -> None:
        for key, entry in i18n.MESSAGES.items():
            assert set(entry) == set(i18n.LANGUAGES), (
                f"{key} does not have every language: {sorted(entry)}"
            )

    def test_no_entry_is_empty(self) -> None:
        for key, entry in i18n.MESSAGES.items():
            for language, text in entry.items():
                assert text.strip(), f"{key}[{language}] is an empty string"

    def test_every_template_formats_cleanly(self) -> None:
        for key, entry in i18n.MESSAGES.items():
            for language, template in entry.items():
                rendered = template.format(**_sample_arguments(template))
                assert rendered, f"{key}[{language}] is empty after formatting"

    def test_placeholders_match_across_languages(self) -> None:
        """The most hidden form of a missing translation: Chinese drops ``{count}``, English
        is fine, and only Chinese users break.

        Only the multiset is compared, not the order (``str.format`` looks up arguments by
        name), but conversions and format specs must still match item by item.
        """
        for key, entry in i18n.MESSAGES.items():
            signatures = {language: Counter(_fields(text)) for language, text in entry.items()}
            reference = signatures[i18n.DEFAULT_LANGUAGE]
            for language, signature in signatures.items():
                assert signature == reference, (
                    f"{key} placeholders differ between {language} and "
                    f"{i18n.DEFAULT_LANGUAGE}; they must match"
                )

    def test_keys_are_unique_in_source(self) -> None:
        """A duplicate key in a ``dict`` literal is silently overwritten by Python; only the
        AST can see it, and one message was quietly dropped.
        """
        source = pathlib.Path(i18n.__file__).read_text(encoding="utf-8")
        node = next(
            item.value
            for item in ast.parse(source).body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == "MESSAGES"
        )
        assert isinstance(node, ast.Dict)
        # String keys only: ``ast.Constant.value`` is typed as "any literal", and
        # without the filter the ``sorted`` below would refuse a possible ``None`` key.
        keys = [
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert duplicates == [], f"duplicate message keys: {duplicates}"

    def test_key_prefix_is_declared(self) -> None:
        """Keys are always ``module.purpose``: a key with no prefix does not know who owns it."""
        for key in i18n.MESSAGES:
            assert "." in key, f"{key} is missing a module prefix"


class TestLookup:
    def test_t_returns_the_requested_language(self) -> None:
        assert i18n.t("render.next_steps", lang="en") == "Next"
        assert i18n.t("render.next_steps", lang="zh") == "下一步"

    def test_t_uses_current_language_by_default(self) -> None:
        i18n.set_language("zh")
        assert i18n.t("render.next_steps") == "下一步"

    def test_t_fills_placeholders(self) -> None:
        """The duration is an already converted string: unit conversion lives in the render
        layer, see ``format_duration``.
        """
        text = i18n.t(
            "wire.summary_multi",
            lang="en",
            count=3,
            total="3m 3.5s",
            mark="<-",
            method="tools/call",
            elapsed="12.3 ms",
        )
        assert text == "3 round trips · 3m 3.5s total · slowest <- tools/call 12.3 ms"

    def test_t_rejects_unknown_key(self) -> None:
        """A misspelled key must blow up at once, not quietly fall back to an empty string."""
        with pytest.raises(KeyError):
            i18n.t("render.does_not_exist")


class TestLanguageSwitching:
    def test_default_language_is_english(self) -> None:
        assert i18n.DEFAULT_LANGUAGE == "en"

    def test_unknown_language_falls_back_instead_of_raising(self) -> None:
        i18n.set_language("klingon")
        assert i18n.current_language() == i18n.DEFAULT_LANGUAGE

    def test_known_language_is_applied(self) -> None:
        i18n.set_language("zh")
        assert i18n.current_language() == "zh"


class TestDetection:
    def test_no_locale_at_all_falls_back_to_english(self, clean_locale: None) -> None:
        assert i18n._detect_language() == i18n.DEFAULT_LANGUAGE

    def test_mcpdump_lang_wins_over_locale(
        self, clean_locale: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("MCPDUMP_LANG", "zh")
        assert i18n._detect_language() == "zh"

    def test_unknown_mcpdump_lang_is_not_a_locale_hint(
        self, clean_locale: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicitly unknown language must not fall through to guessing the locale."""
        monkeypatch.setenv("LANG", "zh_CN.UTF-8")
        monkeypatch.setenv("MCPDUMP_LANG", "fr")
        assert i18n._detect_language() == i18n.DEFAULT_LANGUAGE

    def test_chinese_locale_is_detected(
        self, clean_locale: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "zh_CN.UTF-8")
        assert i18n._detect_language() == "zh"

    def test_lc_all_takes_precedence_over_lang(
        self, clean_locale: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
        assert i18n._detect_language() == "zh"

    def test_english_locale_stays_english(
        self, clean_locale: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert i18n._detect_language() == i18n.DEFAULT_LANGUAGE


class TestCapabilityLabel:
    def test_known_capabilities_are_translated(self) -> None:
        assert i18n.capability_label("tools") == "tools"
        i18n.set_language("zh")
        assert i18n.capability_label("tools") == "工具"

    def test_unknown_capability_is_kept_verbatim(self) -> None:
        """A capability outside the spec is the one most worth seeing, not to blank out."""
        assert i18n.capability_label("future-thing") == "future-thing"
