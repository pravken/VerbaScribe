import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

try:
    import numpy as np

    from app import database, voice_profiles
except ImportError:  # numpy/app dependencies are not installed (e.g. in the release CI job)
    voice_profiles = None


def _voice(seed: int, noise: float = 0.0, base: int = None) -> list:
    rng = np.random.default_rng(seed)
    v = np.random.default_rng(base if base is not None else seed).normal(size=192)
    v = v + rng.normal(size=192) * noise
    return list(v / np.linalg.norm(v))


@unittest.skipIf(voice_profiles is None, "app dependencies not installed")
class VoiceProfilesTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        for p in voice_profiles.list_profiles():
            voice_profiles.delete_profile(p["id"])
        database.update_settings({"voice_profiles": "true"})

    def test_named_voice_is_recognised_in_a_new_recording(self):
        voice_profiles.learn("Максим", _voice(1))
        speakers = {
            "SPEAKER_00": {"name": "Участник 1", "embedding": _voice(7, noise=0.3, base=1)},  # same voice
            "SPEAKER_01": {"name": "Участник 2", "embedding": _voice(2)},  # someone else
        }
        self.assertEqual(voice_profiles.apply_to_speakers(speakers), 1)
        self.assertEqual(speakers["SPEAKER_00"]["name"], "Максим")
        self.assertEqual(speakers["SPEAKER_01"]["name"], "Участник 2")

    def test_close_voice_is_only_suggested(self):
        voice_profiles.learn("Ольга", _voice(5))
        speakers = {"SPEAKER_00": {"name": "Участник 1", "embedding": _voice(11, noise=1.2, base=5)}}
        self.assertEqual(voice_profiles.apply_to_speakers(speakers), 0)
        self.assertEqual(speakers["SPEAKER_00"]["name"], "Участник 1")
        self.assertEqual(speakers["SPEAKER_00"]["voice_suggest"]["name"], "Ольга")

    def test_one_profile_names_one_speaker(self):
        voice_profiles.learn("Анна", _voice(3))
        speakers = {
            "SPEAKER_00": {"name": "Участник 1", "embedding": _voice(8, noise=0.2, base=3)},
            "SPEAKER_01": {"name": "Участник 2", "embedding": _voice(9, noise=0.4, base=3)},
        }
        self.assertEqual(voice_profiles.apply_to_speakers(speakers), 1)
        self.assertEqual(speakers["SPEAKER_00"]["name"], "Анна")

    def test_default_names_and_disabled_setting_are_not_learned(self):
        voice_profiles.learn("Участник 3", _voice(4))
        self.assertEqual(voice_profiles.list_profiles(), [])
        database.update_settings({"voice_profiles": "false"})
        voice_profiles.learn("Борис", _voice(5))
        self.assertEqual(voice_profiles.list_profiles(), [])

    def test_learning_again_refines_the_same_profile(self):
        voice_profiles.learn("Ольга", _voice(6))
        voice_profiles.learn("ольга", _voice(10, noise=0.3, base=6))
        profiles = voice_profiles.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["samples"], 2)


if __name__ == "__main__":
    unittest.main()
