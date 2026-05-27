"""pdfplumber scrambled-text detection in FOCUS CEO extraction.

Covers ``_looks_like_pdfplumber_garbage`` — the heuristic that rejects
pdfplumber output when its font /ToUnicode CMap is broken and the
returned text is a glyph-stream of underscore-wrapped characters
(e.g. ``"_W__ill_ia_m__ D__. _H_a_w_t_h_o_rn_e_"`` instead of
``"William D. Hawthorne"``).

The fix landed after a real prod run wrote AVID CAPITAL ADVISORS LLC
with the scrambled CEO name above. The detector trips on either:
  - 3+ underscores anywhere in the text (real names/emails virtually
    never reach this count); or
  - the diagnostic ``_<char>_<char>_`` pattern that is impossible in
    natural text but appears throughout the font-mangle output.

When the helper returns True the caller falls through to Gemini vision
instead of persisting the garbage.
"""

from __future__ import annotations

from app.services.focus_ceo_extraction import _looks_like_pdfplumber_garbage


# ──────────────── garbage detector ────────────────


def test_legitimate_name_is_not_flagged() -> None:
    assert not _looks_like_pdfplumber_garbage("Joel D. Magerman")


def test_legitimate_email_with_one_underscore_is_not_flagged() -> None:
    # ``first_last@example.com`` is a real email shape.
    assert not _looks_like_pdfplumber_garbage("john_doe@example.com")


def test_legitimate_email_with_two_underscores_is_not_flagged() -> None:
    # Two underscores in an email is unusual but possible (e.g. service
    # accounts, role-based aliases). Below the >=3 threshold so accepted.
    assert not _looks_like_pdfplumber_garbage("john_q_public@example.com")


def test_avid_capital_name_is_rejected() -> None:
    # Verbatim from the prod run that triggered this fix.
    scrambled = (
        "_W__ill_ia_m__ D__. _H_a_w_t_h_o_rn_e_ _ _ _ _ _ _ _ "
        "_(4_1_5_)_ 9_4_8_-_5_6_7_2_ _ _ _ _ _ _ _  ______"
    )
    assert _looks_like_pdfplumber_garbage(scrambled)


def test_avid_capital_email_is_rejected() -> None:
    # Also from the same prod row.
    scrambled = "w__ill_@__a_v_id_c_a_p_it_a_la_d_v_i_so__rs_._co_m__"
    assert _looks_like_pdfplumber_garbage(scrambled)


def test_three_underscore_threshold_triggers_detection() -> None:
    # Boundary check: exactly 3 underscores is the trip line.
    assert _looks_like_pdfplumber_garbage("a_b_c_d")


def test_two_underscores_just_below_threshold_passes() -> None:
    # Same string with 2 underscores — still legitimate.
    assert not _looks_like_pdfplumber_garbage("a_b_c")


def test_char_wrapped_pattern_alone_triggers_detection() -> None:
    # The ``_<char>_<char>_`` signature catches font-mangle outputs that
    # happen to fall below the raw underscore count (short names).
    assert _looks_like_pdfplumber_garbage("_W_e_")


def test_empty_and_none_inputs_pass() -> None:
    # None / empty strings are not garbage — caller will treat them as
    # "no data found" and still fall through to Gemini via the existing
    # ``success`` / name-empty gates.
    assert not _looks_like_pdfplumber_garbage(None)
    assert not _looks_like_pdfplumber_garbage("")
    assert not _looks_like_pdfplumber_garbage(None, "", None)


def test_any_garbage_field_triggers_detection() -> None:
    # Multi-arg call: detector trips when ANY arg is scrambled, even if
    # others look clean. This matches the call site that passes name +
    # email + phone together.
    assert _looks_like_pdfplumber_garbage(
        "Clean Name",
        "clean@example.com",
        "_W__ill__",
    )
