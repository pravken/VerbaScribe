import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import tempfile
import unittest
from pathlib import Path

from app.settings_schema import as_int, clean, clean_all


class SettingsSchemaTest(unittest.TestCase):
    def test_switches_and_numbers(self):
        self.assertEqual(clean("auto_summary", True), "true")
        self.assertEqual(clean("auto_summary", "False"), "false")
        self.assertEqual(clean("num_speakers", "3"), "3")
        self.assertEqual(clean("num_speakers", 99), "20")
        self.assertEqual(clean("idle_unload_minutes", ""), "0")
        for key, bad in (("auto_summary", "yes"), ("num_speakers", "x"), ("llm_provider", "evil"), ("date_folders", "x")):
            with self.assertRaises(ValueError):
                clean(key, bad)
        self.assertEqual(as_int("x", 0), 0)
        self.assertEqual(as_int("2.0", 0), 2)

    def test_addresses(self):
        self.assertEqual(clean("ollama_url", "http://192.168.1.5:11434"), "http://192.168.1.5:11434")
        self.assertEqual(clean("llm_base_url_openai_compat", "http://localhost:1234/v1"), "http://localhost:1234/v1")
        self.assertEqual(clean("llm_base_url_openai_compat", "https://api.example.com/v1"), "https://api.example.com/v1")
        for key, bad in (("ollama_url", "file:///etc/passwd"), ("ollama_url", "javascript:alert(1)"),
                         ("llm_base_url_openai_compat", "http://api.example.com/v1"),  # the key in clear text
                         ("ollama_url", "http://user:pw@host")):
            with self.assertRaises(ValueError):
                clean(key, bad)

    def test_calendar_and_folders(self):
        ics = Path(tempfile.mkdtemp()) / "c.ics"
        ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        self.assertEqual(clean("calendar_ics", str(ics)), str(ics))
        self.assertEqual(clean("calendar_ics", "webcal://example.com/c.ics"), "webcal://example.com/c.ics")
        self.assertEqual(clean("calendar_ics", "outlook"), "outlook")
        self.assertEqual(clean("calendar_ics", ""), "")
        with self.assertRaises(ValueError):
            clean("calendar_ics", "/etc/hostname")  # not a calendar: never read
        self.assertEqual(clean("recordings_dir", "C:\\Users\\Анна\\Записи"), "C:\\Users\\Анна\\Записи")
        with self.assertRaises(ValueError):
            clean("recordings_dir", "relative\\folder")

    def test_other_keys(self):
        self.assertEqual(clean_all({"record_presets": '[{"id":"a"}]'}), {"record_presets": '[{"id":"a"}]'})
        with self.assertRaises(ValueError):
            clean("Bad Key", "x")
        with self.assertRaises(ValueError):
            clean("summary_templates", "x" * (300 * 1024))

    def test_cloud_needs_consent(self):
        from app.llm import uses_cloud

        self.assertFalse(uses_cloud({"llm_provider": "cloud", "llm_cloud_consent": "false"}))
        self.assertTrue(uses_cloud({"llm_provider": "cloud", "llm_cloud_consent": "true"}))


if __name__ == "__main__":
    unittest.main()
