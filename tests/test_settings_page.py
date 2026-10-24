"""The settings page (static/parts/settings.html, static/js/settings.js): its sections, and the values
«Сбросить раздел» puts back."""
import tests  # noqa: F401
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
CORE = (ROOT / "static" / "js" / "core.js").read_text(encoding="utf-8")
DB = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
from app.page import render  # noqa: E402

HTML = render(ROOT / "static")
# Settings of the redesign the database does not list yet: the page falls back to these defaults
NEW_KEYS = {"workday_start", "workday_end", "meetings_autorecord", "meetings_ask_inperson"}


def _js_object(name: str) -> str:
    start = JS.index(f"const {name} = {{")
    return JS[start:JS.index("\n};", start)]


class SettingsPageTest(unittest.TestCase):
    def test_reset_defaults_are_the_database_defaults(self):
        block = DB[DB.index("default_settings = {"):]
        block = block[:block.index("\n        }\n")]
        db = dict(re.findall(r'^\s+"([a-z_0-9]+)": "([^"]*)"', block, flags=re.M))
        page = dict(re.findall(r"([a-z_0-9]+): '([^']*)'", _js_object("SETTINGS_DEFAULTS")))
        self.assertTrue(page)
        for key, value in page.items():
            if key in NEW_KEYS:
                continue
            self.assertIn(key, db, key)
            self.assertEqual(value, db[key], key)

    def test_every_reset_control_exists_and_has_a_default(self):
        page = set(re.findall(r"([a-z_0-9]+): '", _js_object("SETTINGS_DEFAULTS")))
        for el, key in re.findall(r"\['(set-[a-z0-9-]+)', '([a-z_0-9]+)', '(?:check|value)'\]", _js_object("SETTINGS_RESET")):
            self.assertIn(f'id="{el}"', HTML, el)
            self.assertIn(key, page, key)

    def test_every_section_has_its_button_and_body(self):
        tabs = re.search(r"const SETTINGS_TABS = \[([^\]]+)\]", CORE).group(1)
        for name in re.findall(r"'([a-z]+)'", tabs):
            self.assertIn(f'id="stab-{name}"', HTML, name)
            self.assertIn(f'data-stab="{name}"', HTML, name)

    def test_the_old_tab_names_still_open_a_section(self):
        for name in ("integrations",):
            self.assertIn(f"{name}: '", CORE)


if __name__ == "__main__":
    unittest.main()
