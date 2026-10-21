"""TOML arrays of tables owned one table at a time ([[skills.config]] for Codex)."""

from kitctl import formats
from kitctl.formats import MISSING, detect_style

USER = '# my codex\nmodel = "gpt-5"\n\n[[skills.config]]\nname = "mine"\nenabled = false  # keep\n'
OURS = {"name": "kit-skill", "enabled": False}


def test_append_get_and_remove_keep_the_users_entries_and_comments() -> None:
    doc = formats.toml_load(USER)
    assert formats.toml_get(doc, "skills.config/-", OURS) is MISSING
    assert formats.toml_set(doc, "skills.config/-", OURS) == []  # the array was the user's
    assert formats.toml_get(doc, "skills.config/-", OURS) == OURS
    text = formats.toml_dump(doc, detect_style(USER))
    assert text.startswith(USER) and 'name = "kit-skill"' in text
    again = formats.toml_load(text)
    formats.toml_delete(again, "skills.config/-", [], OURS)
    assert formats.toml_dump(again, detect_style(text)) == USER


def test_an_array_kit_created_goes_away_with_its_last_table() -> None:
    doc = formats.toml_load('model = "gpt-5"\n')
    created = formats.toml_set(doc, "skills.config/-", OURS)
    assert created == ["skills", "skills.config"]
    text = formats.toml_dump(doc, detect_style(""))
    assert "[[skills.config]]" in text and "[skills]" not in text
    again = formats.toml_load(text)
    formats.toml_delete(again, "skills.config/-", created, OURS)
    assert formats.toml_dump(again, detect_style(text)).strip() == 'model = "gpt-5"'
