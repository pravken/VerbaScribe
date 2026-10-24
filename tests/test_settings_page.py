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
# The working day is kept in whole hours; its controls show them as "09:00"
SHOWN_AS_TIME = {"work_day_start", "work_day_end"}
# Sound switches with no row of their own until first saved: the server takes them as on
# (database.effective_processing; denoise and loudnorm follow audio_enhance, on by default)
ON_WITHOUT_ROW = {"proc_denoise", "proc_loudnorm", "proc_leveling"}


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
            if key in ON_WITHOUT_ROW:
                self.assertNotIn(key, db, key)
                self.assertEqual(value, "true", key)
                continue
            self.assertIn(key, db, key)
            self.assertEqual(str(int(value[:2])) if key in SHOWN_AS_TIME else value, db[key], key)

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
