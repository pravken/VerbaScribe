import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_distances

from app.database import MODELS_DIR
from app.hardware import clear_vram

SPEAKER_COLORS = [
    "#6366f1",  # Indigo
    "#10b981",  # Emerald
    "#f59e0b",  # Amber
    "#ec4899",  # Pink
    "#06b6d4",  # Cyan
    "#8b5cf6",  # Violet
    "#ef4444",  # Red
    "#14b8a6",  # Teal
]

# Auto mode: minimum anchor speech for a cluster to count as a separate speaker
MIN_SPEAKER_SECONDS = 4.0
MIN_SPEAKER_SHARE = 0.03
MAX_MIN_SPEAKER_SECONDS = 20.0

_ecapa_model = None
_ecapa_device = None


def _ensure_hf_compat() -> None:
    """
    Ensures compatibility between SpeechBrain and newer huggingface_hub / torchaudio versions.
    """
    import huggingface_hub

    orig_download = huggingface_hub.hf_hub_download
    sig = inspect.signature(orig_download)
    if "use_auth_token" not in sig.parameters:
        def _patched_hf_hub_download(*args, **kwargs):
            if "use_auth_token" in kwargs:
                val = kwargs.pop("use_auth_token")
                if val and "token" not in kwargs:
                    kwargs["token"] = val
            return orig_download(*args, **kwargs)

        huggingface_hub.hf_hub_download = _patched_hf_hub_download


def _get_ecapa_encoder(device: str = "cpu"):
    global _ecapa_model, _ecapa_device
    target_device = "cuda:0" if (device.startswith("cuda") and torch.cuda.is_available()) else "cpu"

    if _ecapa_model is not None and _ecapa_device == target_device:
        return _ecapa_model

    _ensure_hf_compat()
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    save_dir = MODELS_DIR / "spkrec-ecapa-voxceleb"
    save_dir.mkdir(parents=True, exist_ok=True)

    _ecapa_model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(save_dir),
        run_opts={"device": target_device},
        local_strategy=LocalStrategy.COPY,
    )
    _ecapa_device = target_device
    return _ecapa_model


def unload_diarizer() -> None:
    global _ecapa_model, _ecapa_device
    _ecapa_model = None
    _ecapa_device = None
    clear_vram()


def _split_segments_by_word_pauses(
    raw_segments: List[Dict[str, Any]],
    pause_threshold: float = 0.48,
    max_chunk_duration: float = 12.0,
) -> List[Dict[str, Any]]:
    """
    Splits long Whisper segments at natural pauses between words (> 0.48s) or sentence
    endings so speaker changes inside a segment are captured accurately.
    """
    refined: List[Dict[str, Any]] = []

    for seg in raw_segments:
        words = seg.get("words") or []
        if not words or len(words) <= 2:
            refined.append(seg)
            continue

        current_words = [words[0]]
        for prev_w, curr_w in zip(words[:-1], words[1:]):
            gap = float(curr_w["start"]) - float(prev_w["end"])
            chunk_dur = float(prev_w["end"]) - float(current_words[0]["start"])
            ends_sentence = str(prev_w.get("word", "")).strip().endswith((".", "?", "!"))

            if (gap >= pause_threshold and chunk_dur >= 1.0) or (
                ends_sentence and gap >= 0.22 and chunk_dur >= 2.2
            ) or (chunk_dur >= max_chunk_duration and ends_sentence):
                text = "".join(w["word"] for w in current_words).strip()
                if text:
                    refined.append(
                        {
                            "start": round(float(current_words[0]["start"]), 2),
                            "end": round(float(current_words[-1]["end"]), 2),
                            "text": text,
                            "words": current_words,
                        }
                    )
                current_words = [curr_w]
            else:
                current_words.append(curr_w)

        if current_words:
            text = "".join(w["word"] for w in current_words).strip()
            if text:
                refined.append(
                    {
                        "start": round(float(current_words[0]["start"]), 2),
                        "end": round(float(current_words[-1]["end"]), 2),
                        "text": text,
                        "words": current_words,
                    }
                )

    return refined


def _extract_embedding_for_slice(
    encoder,
    audio_data: np.ndarray,
    sr: int,
    start_sec: float,
    end_sec: float,
) -> Optional[np.ndarray]:
    """
    Slices 16kHz waveform [start_sec, end_sec] and computes L2-normalized 192-dim ECAPA embedding.
    Pads very short segments slightly with surrounding context.
    """
    total_samples = len(audio_data)
    dur = end_sec - start_sec
    if dur < 0.35:
        return None

    # Expand window if segment is short (< 2.2s) for a more stable voiceprint
    pad = max(0.0, (2.2 - dur) / 2.0)
    s_idx = max(0, int((start_sec - pad) * sr))
    e_idx = min(total_samples, int((end_sec + pad) * sr))
    # Cap window to 10s around center for speed
    if (e_idx - s_idx) > 10 * sr:
        mid = (s_idx + e_idx) // 2
        s_idx = mid - 5 * sr
        e_idx = mid + 5 * sr

    chunk = audio_data[s_idx:e_idx]
    if len(chunk) < int(0.35 * sr):
        return None

    # Check RMS energy to avoid embedding pure silence
    rms = float(np.sqrt(np.mean(chunk**2)))
    if rms < 1e-4:
        return None

    wav_tensor = torch.from_numpy(chunk).float().unsqueeze(0)
    with torch.no_grad():
        emb = encoder.encode_batch(wav_tensor)
        vec = emb.squeeze().cpu().numpy().astype(np.float32)

    norm = np.linalg.norm(vec)
    if norm > 1e-8:
        vec = vec / norm
    return vec


def _determine_clusters(
    embeddings: np.ndarray,
    durations: np.ndarray,
    num_speakers_hint: int = 0,
    max_speakers: int = 8,
) -> np.ndarray:
    """
    Clusters L2-normalized speaker embeddings using Cosine Agglomerative Clustering.
    Automatically detects if only 1 speaker is present or picks optimal K via Silhouette Score,
    duration-weighted centroids, and inter-centroid cosine distance verification.
    """
    n_samples = len(embeddings)
    if n_samples == 0:
        return np.array([], dtype=int)
    if n_samples == 1 or num_speakers_hint == 1:
        return np.zeros(n_samples, dtype=int)

    # Filter reliable anchor segments (duration >= 2.2s) to determine speaker clusters
    # Short emotional phrases (< 2.2s) have higher pitch/timbre variance in ECAPA space
    anchor_mask = durations >= 2.2
    if np.sum(anchor_mask) < 2:
        anchor_mask = durations >= 1.4
    if np.sum(anchor_mask) < 2:
        anchor_mask = np.ones(n_samples, dtype=bool)

    anchor_embs = embeddings[anchor_mask]
    anchor_durs = durations[anchor_mask]
    dist_matrix = cosine_distances(anchor_embs)

    # In Auto mode (num_speakers_hint <= 0), check if anchor segments belong to a single speaker
    if num_speakers_hint <= 0:
        upper_tri = dist_matrix[np.triu_indices_from(dist_matrix, k=1)]
        if len(upper_tri) > 0:
            p90_dist = float(np.percentile(upper_tri, 90))
            # Same speaker across varying intonation/emotion in ECAPA-TDNN typically stays < 0.48
            if p90_dist < 0.48:
                return np.zeros(n_samples, dtype=int)

    # Determine optimal number of speakers K
    if num_speakers_hint > 1:
        best_k = min(num_speakers_hint, len(anchor_embs))
    else:
        max_k = min(max_speakers, len(anchor_embs) - 1)
        if max_k < 2:
            return np.zeros(n_samples, dtype=int)

        best_k = 1
        best_score = 0.25  # Minimum silhouette threshold to split into >=2 speakers in Auto mode
        # A separate speaker must be backed by several anchor segments and enough speech: a single
        # emotional or noisy phrase can sit far from the rest of the same voice in ECAPA space.
        total_anchor_dur = float(np.sum(anchor_durs))
        min_cluster_dur = min(
            MAX_MIN_SPEAKER_SECONDS,
            max(MIN_SPEAKER_SECONDS, MIN_SPEAKER_SHARE * total_anchor_dur),
        )
        for k in range(2, max_k + 1):
            try:
                clustering = AgglomerativeClustering(
                    n_clusters=k,
                    metric="cosine",
                    linkage="average",
                )
                labels = clustering.fit_predict(anchor_embs)
                # Ensure tiny noise clusters don't create phantom speakers in Auto mode
                counts = np.bincount(labels)
                cluster_durs = [float(np.sum(anchor_durs[labels == c_idx])) for c_idx in range(k)]
                if np.min(counts) < 2 or min(cluster_durs) < min_cluster_dur:
                    continue
                score = float(silhouette_score(dist_matrix, labels, metric="precomputed"))
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception:
                continue

    if best_k <= 1:
        return np.zeros(n_samples, dtype=int)

    # Fit on anchor segments and compute duration-weighted speaker centroids
    clustering = AgglomerativeClustering(
        n_clusters=best_k,
        metric="cosine",
        linkage="average",
    )
    anchor_labels = clustering.fit_predict(anchor_embs)

    centroids_list: List[np.ndarray] = []
    cluster_weights: List[float] = []
    for k in range(best_k):
        mask_k = anchor_labels == k
        k_embs = anchor_embs[mask_k]
        k_weights = np.clip(anchor_durs[mask_k], 0.5, 10.0)[:, None]
        c = np.sum(k_embs * k_weights, axis=0)
        norm = np.linalg.norm(c)
        centroids_list.append((c / (norm if norm > 1e-8 else 1.0)).astype(np.float32))
        cluster_weights.append(float(np.sum(anchor_durs[mask_k])))

    # In Auto mode, merge any two centroids whose cosine distance is < 0.46 (same voice with pitch shift)
    if num_speakers_hint <= 0 and len(centroids_list) > 1:
        keep_indices = list(range(len(centroids_list)))
        keep_indices.sort(key=lambda idx: cluster_weights[idx], reverse=True)
        merged_centroids: List[np.ndarray] = []
        for idx in keep_indices:
            cand = centroids_list[idx]
            if not merged_centroids:
                merged_centroids.append(cand)
                continue
            dists_to_existing = [float(1.0 - np.dot(cand, existing)) for existing in merged_centroids]
            if min(dists_to_existing) >= 0.46:
                merged_centroids.append(cand)
        centroids = np.vstack(merged_centroids)
        if len(centroids) <= 1:
            return np.zeros(n_samples, dtype=int)
    else:
        centroids = np.vstack(centroids_list)

    # Assign all segments to nearest speaker centroid
    all_dists = cosine_distances(embeddings, centroids)
    raw_labels = np.argmin(all_dists, axis=1)

    # Re-index speakers in chronological order of first appearance (SPEAKER_00 = first person to speak)
    ordered_map: Dict[int, int] = {}
    final_labels = np.zeros(n_samples, dtype=int)
    for i, lbl in enumerate(raw_labels):
        lbl_int = int(lbl)
        if lbl_int not in ordered_map:
            ordered_map[lbl_int] = len(ordered_map)
        final_labels[i] = ordered_map[lbl_int]

    return final_labels


def diarize_and_merge_segments(
    wav_path: str | Path,
    raw_segments: List[Dict[str, Any]],
    device: str = "cuda",
    num_speakers_hint: int = 0,
    diarization_enabled: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Assigns speaker IDs (SPEAKER_00, SPEAKER_01, ...) to transcript segments without requiring
    any external tokens or API keys, smooths short interjections, and merges consecutive
    turns from the same speaker into readable dialogue paragraphs.
    """
    if not raw_segments:
        return {}, []

    if not diarization_enabled:
        speakers = {
            "SPEAKER_00": {
                "id": "SPEAKER_00",
                "name": "Участник 1",
                "color": SPEAKER_COLORS[0],
                "sample_start": raw_segments[0]["start"],
                "sample_end": min(raw_segments[0]["end"], raw_segments[0]["start"] + 6.0),
            }
        }
        merged = _merge_consecutive_speaker_segments(
            [{**seg, "speaker": "SPEAKER_00"} for seg in raw_segments]
        )
        return speakers, merged

    if progress_callback:
        progress_callback(68, "Подготовка сегментов речи для разделения спикеров...")

    fine_segments = _split_segments_by_word_pauses(raw_segments)

    if progress_callback:
        progress_callback(72, "Загрузка голосовой модели ECAPA-TDNN (без токенов)...")

    audio_data, sr = sf.read(str(wav_path), dtype="float32")
    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)

    encoder = _get_ecapa_encoder(device=device)

    embeddings_list: List[np.ndarray] = []
    valid_indices: List[int] = []
    durations_list: List[float] = []

    total_segs = len(fine_segments)
    for idx, seg in enumerate(fine_segments):
        if progress_callback and idx % max(1, total_segs // 6) == 0:
            pct = 74 + int(10 * (idx / max(1, total_segs)))
            progress_callback(pct, f"Анализ тембра голосов ({idx + 1}/{total_segs})...")

        emb = _extract_embedding_for_slice(
            encoder, audio_data, sr, float(seg["start"]), float(seg["end"])
        )
        if emb is not None:
            embeddings_list.append(emb)
            valid_indices.append(idx)
            durations_list.append(float(seg["end"]) - float(seg["start"]))

    # Free diarizer VRAM immediately after embedding extraction
    unload_diarizer()

    if progress_callback:
        progress_callback(86, "Кластеризация спикеров по голосам...")

    speaker_assignments = [0] * len(fine_segments)

    if len(embeddings_list) >= 2:
        embs_arr = np.vstack(embeddings_list)
        durs_arr = np.array(durations_list, dtype=np.float32)
        labels = _determine_clusters(embs_arr, durs_arr, num_speakers_hint=num_speakers_hint)

        for valid_pos, seg_idx in enumerate(valid_indices):
            speaker_assignments[seg_idx] = int(labels[valid_pos])

        # Fill in very short segments that had no embedding using nearest valid neighbor
        valid_set = set(valid_indices)
        for i in range(len(fine_segments)):
            if i not in valid_set:
                # Find closest valid index
                closest = min(valid_indices, key=lambda v: abs(v - i))
                speaker_assignments[i] = speaker_assignments[closest]

        # Temporal collar smoothing: if a short segment (< 2.2s) is surrounded by the same speaker, smooth it
        for i in range(1, len(fine_segments) - 1):
            dur = float(fine_segments[i]["end"]) - float(fine_segments[i]["start"])
            if dur < 2.2 and speaker_assignments[i - 1] == speaker_assignments[i + 1]:
                speaker_assignments[i] = speaker_assignments[i - 1]

    labeled_segments = []
    for seg, spk_num in zip(fine_segments, speaker_assignments):
        spk_id = f"SPEAKER_{spk_num:02d}"
        labeled_segments.append({**seg, "speaker": spk_id})

    merged_segments = _merge_consecutive_speaker_segments(labeled_segments)

    # Build speaker metadata with best voice sample for audio preview in UI
    speakers_meta: Dict[str, Dict[str, Any]] = {}
    unique_speakers = sorted({seg["speaker"] for seg in merged_segments})

    for idx, spk_id in enumerate(unique_speakers):
        spk_segs = [s for s in merged_segments if s["speaker"] == spk_id]
        # Pick the clearest segment between 2.5s and 8s for voice preview
        best_seg = max(
            spk_segs,
            key=lambda s: min(6.0, float(s["end"]) - float(s["start"])),
        )
        s_start = round(float(best_seg["start"]), 2)
        s_end = round(min(float(best_seg["end"]), s_start + 6.0), 2)

        speakers_meta[spk_id] = {
            "id": spk_id,
            "name": f"Участник {idx + 1}",
            "color": SPEAKER_COLORS[idx % len(SPEAKER_COLORS)],
            "sample_start": s_start,
            "sample_end": s_end,
        }

    return speakers_meta, merged_segments


def _merge_consecutive_speaker_segments(
    segments: List[Dict[str, Any]],
    max_pause_gap: float = 1.6,
    max_merged_duration: float = 38.0,
) -> List[Dict[str, Any]]:
    """
    Merges consecutive segments belonging to the same speaker into readable dialogue turns
    while preserving word-level timestamps for interactive player highlighting.
    """
    if not segments:
        return []

    merged: List[Dict[str, Any]] = []
    current = dict(segments[0])
    current["words"] = list(current.get("words") or [])

    for nxt in segments[1:]:
        same_speaker = nxt.get("speaker") == current.get("speaker")
        gap = float(nxt["start"]) - float(current["end"])
        combined_dur = float(nxt["end"]) - float(current["start"])

        if same_speaker and gap <= max_pause_gap and combined_dur <= max_merged_duration:
            current["end"] = round(float(nxt["end"]), 2)
            current["text"] = (current["text"].rstrip() + " " + nxt["text"].lstrip()).strip()
            current["words"].extend(nxt.get("words") or [])
        else:
            current["id"] = len(merged) + 1
            merged.append(current)
            current = dict(nxt)
            current["words"] = list(current.get("words") or [])

    current["id"] = len(merged) + 1
    merged.append(current)
    return merged
