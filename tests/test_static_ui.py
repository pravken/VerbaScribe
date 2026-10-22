"""The page itself: markup that holds together and texts in both languages (no browser needed)."""
import tests  # noqa: F401
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from app.page import render  # noqa: E402

HTML = render(ROOT / "static")  # index.html with its parts
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

    def test_every_part_is_included(self):
        self.assertNotIn("@include", HTML)
        for part in (ROOT / "static" / "parts").glob("*.html"):
            self.assertIn(f"parts/{part.name}", (ROOT / "static" / "index.html").read_text(encoding="utf-8"), part.name)

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

    def test_placeholders_take_text_as_is(self):
        """t('…{title}…').replace('{title}', title): a title with "$'" or "$&" would be read as a pattern;
        a function's result is taken as is."""
        bad = []
        for f in sorted((ROOT / "static" / "js").glob("*.js")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"""\.replace\((['"])\{[A-Za-z_]+\}\1,(?!\s*\(\) =>)""", line):
                    bad.append(f"{f.name}:{n}: {line.strip()[:100]}")
        self.assertEqual(bad, [])

    def test_vendor_files_match_package_json(self):
        """static/vendor is what `npm run vendor` built from the versions in package.json."""
        import json

        deps = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["devDependencies"]
        head = (ROOT / "static" / "vendor" / "purify.min.js").read_text(encoding="utf-8")[:120]
        self.assertIn(f"DOMPurify {deps['dompurify']} ", head)
        head = (ROOT / "static" / "vendor" / "marked.min.js").read_text(encoding="utf-8")[:200]
        self.assertIn(f"marked v{deps['marked']} ", head)

    def test_no_text_in_inline_handlers(self):
        """Data in an onclick="..." goes through jsArg() (or Number()): a quote in a name must not end the code."""
        bad = []
        for f in sorted((ROOT / "static" / "js").glob("*.js")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                # a quoted ${...} in a handler, or a handler in single quotes (JSON.stringify keeps ' as is)
                if re.search(r"""\bon[a-z]+='|\bon[a-z]+="[^"]*'\$\{|\(`[^`]*'\$\{[^}]*\}'""", line):
                    bad.append(f"{f.name}:{n}: {line.strip()[:120]}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
