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
            nxt = re.compile(r'<div data-stab="|<!-- Sticky Modal Footer -->|<!-- The end of the settings sections -->').search(HTML, tab.end())
            body = _no_comments(HTML[tab.start():nxt.start()])
            opened, closed = len(re.findall(r"<div\b", body)), len(re.findall(r"</div>", body))
            # the last tab is followed by the closing of the scroll area
            self.assertIn(opened - closed, (0, -1), f"settings tab {tab.group(1)}: {opened} <div> and {closed} </div>")

    def test_page_is_balanced(self):
        body = _no_comments(HTML)
        self.assertEqual(len(re.findall(r"<div\b", body)), len(re.findall(r"</div>", body)))

    def test_every_part_is_included(self):
        self.assertNotIn("@include", HTML)
        # a part is included by the page or by another part (a view inside the workspace)
        sources = [ROOT / "static" / "index.html", *(ROOT / "static" / "parts").glob("*.html")]
        text = "\n".join(f.read_text(encoding="utf-8") for f in sources)
        for part in (ROOT / "static" / "parts").glob("*.html"):
            self.assertIn(f"@include parts/{part.name}", text, part.name)

    def test_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"$]+)"', _no_comments(HTML))
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dupes, [])

    def test_every_text_has_both_languages(self):
        en_at = I18N.index("\n  en: {")
        ru, en = _keys(I18N[:en_at]), _keys(I18N[en_at:])
        used = set(re.findall(r'data-i18n(?:-title|-placeholder|-aria)?="([A-Za-z0-9_]+)"', HTML))
        self.assertEqual(sorted(used - ru), [], "no Russian text")
        self.assertEqual(sorted(used - en), [], "no English text")
        self.assertEqual(sorted(ru - en), [], "Russian text without English")
        for block in (I18N[:en_at], I18N[en_at:]):  # a key given twice: only the last one is ever shown
            names = re.findall(r"^\s{4}([A-Za-z0-9_]+):", block, flags=re.M)
            self.assertEqual(sorted({k for k in names if names.count(k) > 1}), [])

    def test_placeholders_take_text_as_is(self):
        """t('…{title}…').replace('{title}', title): a title with "$'" or "$&" would be read as a pattern;
        a function's result is taken as is."""
        bad = []
        for f in sorted((ROOT / "static" / "js").glob("*.js")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"""\.replace\((['"])\{[A-Za-z_]+\}\1,(?!\s*\(\) =>)""", line):
                    bad.append(f"{f.name}:{n}: {line.strip()[:100]}")
        self.assertEqual(bad, [])

    def test_every_server_message_has_english(self):
        """The server speaks Russian; static/server_text.js gives the English interface each message."""
        import ast
        import json

        table = (ROOT / "static" / "server_text.js").read_text(encoding="utf-8")
        plain = set(json.loads("{" + table.split("const SERVER_TEXT_EN = {", 1)[1].split("\n};", 1)[0].rstrip().rstrip(",") + "}"))
        patterns = {p[0] for p in json.loads("[" + table.split("const SERVER_PATTERNS_EN = [", 1)[1].split("\n];", 1)[0].rstrip().rstrip(",") + "]")}
        missing = []
        for f in sorted((ROOT / "app").rglob("*.py")):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", ""))
                        in ("HTTPException", "ValueError", "RuntimeError", "LlmError")):
                    continue
                for v in [kw.value for kw in node.keywords if kw.arg == "detail"] + list(node.args[:1]):
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        text, known = v.value, plain
                    elif isinstance(v, ast.JoinedStr):
                        text = "".join(x.value if isinstance(x, ast.Constant) else "{}" for x in v.values)
                        known = patterns
                    else:
                        continue
                    if re.search("[А-Яа-я]", text) and text not in known:
                        missing.append(f"{f.name}: {text[:80]}")
        self.assertEqual(missing, [])

    def test_every_select_has_a_name(self):
        """A screen reader names a list by its <label for>, aria-label(ledby) or title."""
        labels = set(re.findall(r'<label[^>]*\sfor="([^"]+)"', HTML))
        unnamed = []
        for m in re.finditer(r"<select\b[^>]*>", HTML):
            tag = m.group(0)
            sid = (re.search(r'id="([^"]+)"', tag) or [None, "?"])[1]
            if "title=" in tag or "aria-label" in tag or sid in labels:
                continue
            before = HTML[max(0, m.start() - 300):m.start()]
            if before.rfind("<label") <= before.rfind("</label>"):
                unnamed.append(sid)
        self.assertEqual(unnamed, [])

    def test_every_class_has_its_css(self):
        """static/vendor/tailwind.css is built ahead (npm run vendor) from the classes the page uses: a
        class added without a rebuild does nothing (a gap of 0, a button that does not grey out)."""
        css = (ROOT / "static" / "vendor" / "tailwind.css").read_text(encoding="utf-8") \
            + (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        defined = {c.replace("\\", "") for c in re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", css)}
        # Names the scripts look for, with no style of their own
        hooks = {"dark", "rec-app-video", "ask-body", "cap-o-tag", "cap-t-tag", "del-opt", "row-more", "tr-word", "tx-chunk"}
        used = {c for m in re.finditer(r'\sclass="([^"]*)"', HTML) for c in m.group(1).split() if "${" not in c}
        for f in sorted((ROOT / "static" / "js").glob("*.js")):
            for m in re.finditer(r"""class(?:Name)?\s*=\s*["']([^"'$]*)["']""", f.read_text(encoding="utf-8")):
                used.update(m.group(1).split())
        self.assertEqual(sorted(used - defined - hooks), [], "run npm run vendor")

    def test_vendor_files_match_package_json(self):
        """static/vendor is what `npm run vendor` built from the versions in package.json."""
        import json

        deps = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["devDependencies"]
        head = (ROOT / "static" / "vendor" / "purify.min.js").read_text(encoding="utf-8")[:120]
        self.assertIn(f"DOMPurify {deps['dompurify']} ", head)
        head = (ROOT / "static" / "vendor" / "marked.min.js").read_text(encoding="utf-8")[:200]
        self.assertIn(f"marked v{deps['marked']} ", head)

    def test_toasts_call_existing_functions(self):
        """showToastt('…') (a typo of showToast(t('…'))) threw after the action it was to confirm."""
        defined = set()
        calls = {}
        for f in sorted((ROOT / "static" / "js").glob("*.js")):
            text = f.read_text(encoding="utf-8")
            defined.update(re.findall(r"^(?:async )?function ([A-Za-z0-9_]+)\(", text, flags=re.M))
            for m in re.finditer(r"\b(showToast[A-Za-z0-9_]*)\(", text):
                calls.setdefault(m.group(1), f.name)
        self.assertEqual(sorted(set(calls) - defined), [])

    def test_no_inline_handlers(self):
        """The page's CSP runs no inline script: events are data-<event> (see PAGE_EVENTS in core.js)."""
        sources = {f.name: f.read_text(encoding="utf-8") for f in (ROOT / "static" / "js").glob("*.js")}
        bad = [m.group(0) for m in re.finditer(r'<[^>]*\son[a-z]+\s*=', _no_comments(HTML))]
        for name, text in sources.items():
            for n, line in enumerate(text.splitlines(), 1):
                if not line.lstrip().startswith("//") and re.search(r"""[\s`'"]on[a-z]+=["'`$]""", line):
                    bad.append(f"{name}:{n}: {line.strip()[:120]}")
        self.assertEqual(bad, [])

    def test_data_handlers_name_page_functions(self):
        """Each data-<event>="name", and each name runAll / stopThen / preventThen / onEnter call on,
        is a function of the page: a typo would leave a button that silently does nothing."""
        import html
        import json

        js = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "static" / "js").glob("*.js"))
        js = re.sub(r"^\s*//.*$", "", js, flags=re.M)  # examples in comments are not handlers
        defined = set(re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", js, re.M))
        text = _no_comments(HTML) + js
        events = "click|change|input|keydown|contextmenu|dragstart|dragover|dragleave|drop|focus|blur|error"
        names = set()
        def args_after(pos: int) -> str:
            """The text of a data-*-args='...' value from pos, ${...} (which may hold quotes) included."""
            out, depth, i = "", 0, pos
            while i < len(text) and (depth or text[i] != "'"):
                if text.startswith("${", i):
                    depth += 1
                    out += "$"
                    i += 1
                elif text[i] == "{" and depth:
                    depth += 1
                elif text[i] == "}" and depth:
                    depth -= 1
                out += text[i]
                i += 1
            return out

        def no_interpolations(value: str) -> str:
            out, depth, i = "", 0, 0
            while i < len(value):
                if value.startswith("${", i):
                    depth += 1
                    i += 2
                    continue
                if depth:
                    depth += {"{": 1, "}": -1}.get(value[i], 0)
                    if not depth:
                        out += "0"
                else:
                    out += value[i]
                i += 1
            return out

        for m in re.finditer(rf"""data-({events})="([^"]*)"(\s+data-\1-args=')?""", text):
            fn, raw = m.group(2), args_after(m.end()) if m.group(3) else None
            names |= set(re.findall(r"[?:]\s*'(\w+)'", fn)) if fn.startswith("${") else {fn}
            if not raw:
                continue
            try:  # interpolated values are numbers or jsArg() texts: any value will do here
                args = json.loads(html.unescape(no_interpolations(raw)))
            except ValueError:
                self.fail(f"data-{m.group(1)}-args is not JSON: {raw[:80]}")
            if fn == "runAll":
                names |= {call[0] for call in args}
            elif fn in ("stopThen", "preventThen", "onEnter"):
                names.add(args[1])
        names |= set(re.findall(r"pageCall\('[a-z]+',\s*'(\w+)'", js))
        # menus built in code: item([['hideRecordPresetMenu'], ['quickRecord', id]]) / item(['setTxViewChoice', 'tr', c])
        names |= set(re.findall(r"item\(\[\[?'(\w+)'", js)) | set(re.findall(r"\],\s*\['(\w+)'\]\]", js))
        self.assertEqual(sorted(n for n in names if n not in defined), [])

if __name__ == "__main__":
    unittest.main()
