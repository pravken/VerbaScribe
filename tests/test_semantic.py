import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from unittest import mock

from app import database as db

try:
    import numpy as np

    from app import library_qa, semantic
except ImportError:
    semantic = None
needs_deps = unittest.skipIf(semantic is None, "app dependencies not installed")

# A toy embedding: words of one topic share a dimension, so "deadline" and "Friday" are close
_TOPICS = [("срок", "пятниц", "перенес", "дедлайн"), ("бюджет", "деньг", "смет"), ("кот", "собак")]


def _fake_embed(texts, settings=None):
    out = []
    for t in texts:
        low = t.casefold()
        v = np.array([sum(low.count(w) for w in words) for words in _TOPICS] + [0.01], dtype=np.float32)
        out.append(v / np.linalg.norm(v))
    return np.stack(out)


def _seg(i, start, text, speaker="S1"):
    return {"id": i, "start": start, "end": start + 4, "speaker": speaker, "text": text}


@needs_deps
class SemanticIndexTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.update_settings({"semantic_search": "true", "semantic_model": "toy"})
        self.a = db.create_meeting("Планёрка", "a.wav", "/tmp/a.wav")["id"]
        self.b = db.create_meeting("Бюджет", "b.wav", "/tmp/b.wav")["id"]
        db.save_meeting_results(self.a, speakers={"S1": {"name": "Анна"}}, segments=[
            _seg(1, 0, "Привет всем."), _seg(2, 30, "Давайте перенесём релиз на пятницу."),
        ])
        db.save_meeting_results(self.b, segments=[_seg(1, 0, "Смета выросла, денег не хватает.")])
        for mid in (self.a, self.b):
            db.update_meeting_progress(mid, status="completed", progress=100)
        self.patches = [mock.patch.object(semantic, "embed", side_effect=_fake_embed),
                        mock.patch.object(semantic, "_ollama_up", return_value=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        db.delete_meeting(self.a)
        db.delete_meeting(self.b)
        db.update_settings({"semantic_search": "false", "semantic_model": "bge-m3"})

    def test_index_search_and_invalidation(self):
        self.assertIn(self.a, semantic.pending_ids())
        for mid in semantic.pending_ids():
            semantic.index_meeting(mid)
        self.assertEqual(semantic.pending_ids(), [])
        hits = semantic.search("Какой у нас дедлайн?")
        self.assertEqual(hits[0]["meeting_id"], self.a)
        self.assertIn("пятницу", hits[0]["text"])
        self.assertIn("Анна:", hits[0]["text"])  # chunks carry the speaker names
        self.assertEqual(semantic.search("дедлайн", meeting_ids=[self.b]), [])  # scoped and below MIN_SCORE
        # An edited transcript drops out of the index until it is indexed again
        db.save_meeting_results(self.b, segments=[_seg(1, 0, "Про котов и собак.")])
        self.assertEqual(semantic.pending_ids(), [self.b])

    def test_assistant_gets_fragments_without_shared_words(self):
        for mid in semantic.pending_ids():
            semantic.index_meeting(mid)
        frags = library_qa.retrieve("когда дедлайн", [self.a, self.b])
        self.assertTrue(frags)
        self.assertEqual(frags[0]["meeting_id"], self.a)
        self.assertEqual(frags[0]["start"], 0.0)  # the chunk starts at the first phrase

    def test_off_means_no_search(self):
        semantic.index_meeting(self.a)
        db.update_settings({"semantic_search": "false"})
        self.assertEqual(semantic.search("дедлайн"), [])

    def test_chunks_are_bounded(self):
        segs = [_seg(i, i * 5.0, "слово " * 30) for i in range(20)]
        chunks = semantic.meeting_chunks({"segments": segs, "speakers": {}})
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c[2]) <= semantic.CHUNK_CHARS + 200 for c in chunks))
        starts = [c[0] for c in chunks]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(st in {s["start"] for s in segs} for st in starts))  # each starts at a phrase


@needs_deps
class EmbedRequestTest(unittest.TestCase):
    def test_batches_and_normalizes(self):
        import httpx

        calls = []

        def handler(request):
            import json

            body = json.loads(request.content)
            calls.append(body)
            return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

        real = httpx.Client
        with mock.patch("httpx.Client", side_effect=lambda **kw: real(transport=httpx.MockTransport(handler), **kw)):
            m = semantic.embed([f"t{i}" for i in range(20)], {"semantic_model": "bge-m3"})
        self.assertEqual([len(c["input"]) for c in calls], [16, 4])
        self.assertEqual(calls[0]["model"], "bge-m3")
        self.assertTrue(np.allclose(m[0], [0.6, 0.8]))


if __name__ == "__main__":
    unittest.main()
