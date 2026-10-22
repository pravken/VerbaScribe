import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from app import transcriber
    from app.database import create_meeting, get_meeting, init_db, save_meeting_results, update_settings
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    transcriber = None

OLD = [{"id": 0, "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "прежняя правка"}]
NEW = [{"start": 0.0, "end": 2.0, "text": "новый текст", "words": []}]


@unittest.skipIf(transcriber is None, "app dependencies not installed")
class ProcessMeetingJobTest(unittest.TestCase):
    """The recognition job end to end with GigaAM on ONNX, the model and FFmpeg replaced by stand-ins."""

    def setUp(self):
        init_db()
        update_settings({"auto_summary": "false", "clean_fillers": "false"})
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        media = Path(self.tmp.name) / "meeting.wav"
        media.write_bytes(b"RIFF")
        m = create_meeting(title="Задание", filename="meeting.wav", media_path=str(media))
        self.mid = m["id"]
        from app.database import update_meeting_fields

        update_meeting_fields(self.mid, model_name="gigaam-v3", language="ru")
        save_meeting_results(self.mid, segments=OLD, speakers={"SPEAKER_00": {"name": "Анна", "color": "#0A84FF"}})

        from app import asr_onnx, onnx_gpu, semantic

        def diarize(wav_path, raw_segments, **kw):
            return ({"SPEAKER_00": {"name": "Участник 1", "color": "#0A84FF"}},
                    [{**s, "id": i, "speaker": "SPEAKER_00"} for i, s in enumerate(raw_segments)])

        patches = [
            mock.patch.object(transcriber, "get_hardware_info",
                              return_value={"recommended_device": "cpu", "recommended_compute_type": "int8"}),
            mock.patch.object(transcriber, "extract_audio_16k_mono", side_effect=lambda src, dst, **kw: Path(dst).write_bytes(b"x")),
            mock.patch.object(transcriber, "get_media_duration", return_value=2.0),
            mock.patch.object(transcriber, "_after_transcription", return_value=[]),
            mock.patch.object(asr_onnx, "supports_language", return_value=True),
            mock.patch.object(semantic, "kick"),
            mock.patch("app.diarizer.diarize_and_merge_segments", side_effect=diarize),
            mock.patch("app.voice_profiles.apply_to_speakers", return_value=0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.release = mock.patch.object(asr_onnx, "release").start()
        self.addCleanup(mock.patch.stopall)
        self.gpu = mock.patch.object(onnx_gpu, "gpu_for_job", return_value=False).start()
        self.asr = asr_onnx

    def _run(self, transcribe):
        with mock.patch.object(self.asr, "transcribe", side_effect=transcribe):
            transcriber._process_meeting_job(self.mid)
        return get_meeting(self.mid)

    def test_gpu_failure_is_done_again_on_the_cpu(self):
        self.gpu.return_value = True
        calls = []

        def transcribe(model, wav, dur, progress, cancel, out, gpu=False):
            calls.append(gpu)
            if gpu:
                raise RuntimeError("CUDA out of memory")
            return NEW

        m = self._run(transcribe)
        self.assertEqual(calls, [True, False])
        self.release.assert_any_call("gigaam-v3", gpu=True)
        self.assertEqual(m["status"], "completed")
        self.assertEqual([s["text"] for s in m["segments"]], ["новый текст"])

    def test_cancel_keeps_the_previous_transcript(self):
        def transcribe(model, wav, dur, progress, cancel, out, gpu=False):
            out.append(NEW[0])
            progress(0.5)  # a partial save would replace the old transcript: not for a recording that has one
            raise transcriber.JobCancelled()

        m = self._run(transcribe)
        self.assertEqual(m["status"], "cancelled")
        self.assertEqual([s["text"] for s in m["segments"]], ["прежняя правка"])

    def test_failure_keeps_the_previous_transcript_and_says_why(self):
        def transcribe(model, wav, dur, progress, cancel, out, gpu=False):
            raise RuntimeError("model file damaged")

        m = self._run(transcribe)
        self.assertEqual(m["status"], "failed")
        self.assertIn("model file damaged", m["error_message"])
        self.assertEqual([s["text"] for s in m["segments"]], ["прежняя правка"])

    def test_missing_media_fails_at_once(self):
        Path(get_meeting(self.mid)["media_path"]).unlink()
        m = self._run(lambda *a, **k: self.fail("recognition must not start"))
        self.assertEqual(m["status"], "failed")
        self.assertIn("не найден", m["stage_text"])


if __name__ == "__main__":
    unittest.main()
