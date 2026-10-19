import unittest

try:
    import numpy as np

    from app.dubbing import MIX_SR, _room_tone
except ImportError:  # numpy/httpx are not installed (e.g. in the release CI job)
    np = None


@unittest.skipIf(np is None, "numpy / app dependencies are not installed")
class RoomToneTest(unittest.TestCase):
    def _vocals(self, noise_rms=0.01, seconds=12.0):
        rng = np.random.default_rng(1)
        x = (rng.standard_normal(int(seconds * MIX_SR)) * noise_rms).astype(np.float32)
        x[int(2 * MIX_SR): int(5 * MIX_SR)] += 0.3  # loud "speech" inside a phrase
        return x

    def test_bed_matches_the_noise_floor_between_phrases(self):
        vocals = self._vocals()
        units = [{"start": 2.0, "end": 5.0}]
        n = 30 * MIX_SR  # longer than the source: the bed is tiled
        bed = _room_tone(vocals, units, 12.0, n)
        self.assertEqual(bed.size, n)
        rms = float(np.sqrt((bed ** 2).mean()))
        self.assertAlmostEqual(rms, 0.01, delta=0.0015)
        # No dips or bumps where the pieces are cross-faded (power-complementary windows)
        win = MIX_SR // 4
        levels = np.sqrt((bed[: n // win * win].reshape(-1, win) ** 2).mean(axis=1))
        self.assertLess(levels.max() / levels.min(), 1.35)

    def test_no_bed_without_speech_free_audio_or_for_silence(self):
        vocals = self._vocals()
        self.assertIsNone(_room_tone(vocals, [{"start": 0.0, "end": 12.0}], 12.0, MIX_SR))
        silent = np.zeros(10 * MIX_SR, dtype=np.float32)
        self.assertIsNone(_room_tone(silent, [{"start": 4.0, "end": 5.0}], 10.0, MIX_SR))


if __name__ == "__main__":
    unittest.main()
