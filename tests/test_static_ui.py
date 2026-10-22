"""The page itself: markup that holds together and texts in both languages (no browser needed)."""
import tests  # noqa: F401
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
I18N = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def _no_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _keys(block: str) -> set:
    return set(re.findall(r"^\s{4}([A-Za-z0-9_]+):", block, flags=re.M))


class StaticUiTest(unittest.TestCase):
    def test_settings_tabs_are_balanced(self):
        """Each settings tab closes every <div> it opens (a lost tag shifts the next tab into it)."""
        for tab in re.finditer(r'<div data-stab="([a-z]+)"', HTML):
            nxt = re.compile(r'<div data-stab="|<!-- Sticky Modal Footer -->').search(HTML, tab.end())
            body = _no_comments(HTML[tab.start():nxt.start()])
            opened, closed = len(re.findall(r"<div\b", body)), len(re.findall(r"</div>", body))
            # the last tab is followed by the closing of the scroll area
            self.assertIn(opened - closed, (0, -1), f"settings tab {tab.group(1)}: {opened} <div> and {closed} </div>")

    def test_page_is_balanced(self):
        body = _no_comments(HTML)
        self.assertEqual(len(re.findall(r"<div\b", body)), len(re.findall(r"</div>", body)))

    def test_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"$]+)"', _no_comments(HTML))
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dupes, [])

    def test_every_text_has_both_languages(self):
        en_at = I18N.index("\n  en: {")
        ru, en = _keys(I18N[:en_at]), _keys(I18N[en_at:])
        used = set(re.findall(r'data-i18n(?:-title|-placeholder)?="([A-Za-z0-9_]+)"', HTML))
        self.assertEqual(sorted(used - ru), [], "no Russian text")
        self.assertEqual(sorted(used - en), [], "no English text")
        self.assertEqual(sorted(ru - en), [], "Russian text without English")


if __name__ == "__main__":
    unittest.main()
