"""Give every dialogue turn a start/end time and attach the NOVA labels to it.

The dialogue JSON has the transcription but no timestamps. This script
force-aligns the existing text to each session's audio with the MMS forced
aligner shipped in torchaudio (CPU is fine, no transcription step), then adds
to every turn the NOVA labels whose segments overlap it:

  {"speaker": "Robot", "text": "...", "annotation": "Inform",
   "start": 47.9, "end": 52.3,
   "nova": {"Robot": ["Pausing"], "Both": ["Not Grounded"], "Off topic": ["On topic"]}}

Setup (once):
  pip install "torch==2.8.*" "torchaudio==2.8.*" num2words
  ffmpeg must be on PATH (Windows: winget install ffmpeg)

Run:
  python nova_export/align_turns.py --dialogues json_data --media nova_media \
      --nova-files nova_files --out release
  (or --mongo mongodb://USER:PASS@nova.hcai.eu:37317 instead of --nova-files)

--media holds the session videos, either as nova_media/Group1_1/roleplay.video.mp4
(NOVA's own layout) or as nova_media/Group1_1.mp4 / .wav. Use the same video
the annotations were made on, so the times match.

Outputs in --out:
  merged/Group<N>.json   dialogue JSON with start, end and nova on every turn
  timings/<session>.json cached turn times (re-runs skip the alignment)
  srt/<session>.srt      turns as subtitles: open next to the video in VLC to
                         check the timing by eye
"""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_nova_annotations import (  # noqa: E402
    build_standoff, load_from_files, load_from_mongo)

SAMPLE_RATE = 16000
MEDIA_EXT = (".mp4", ".wav", ".mp3", ".m4a", ".avi", ".mov", ".mkv")

try:
    from num2words import num2words
except ImportError:
    num2words = None


# ----------------------------------------------------------------- text prep

def normalize_word(word):
    """Map a transcript word to the aligner alphabet (a-z and ')."""
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode()
    word = word.lower().replace("’", "'")
    if num2words is not None:
        word = re.sub(r"\d+", lambda m: " " + num2words(int(m.group())) + " ", word)
    word = re.sub(r"[^a-z' ]", " ", word)
    return [w.strip("'") for w in word.split() if w.strip("'")]


def turn_tokens(dialogue):
    """Flatten turns into aligner words, remembering which turn each came from."""
    words, owner = [], []
    for i, turn in enumerate(dialogue):
        for raw in str(turn.get("text") or "").split():
            for w in normalize_word(raw):
                words.append(w)
                owner.append(i)
    return words, owner


# ----------------------------------------------------------------- alignment

def load_audio(path):
    """Decode any audio/video file to 16 kHz mono float32 with ffmpeg."""
    import numpy as np

    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


class Aligner:
    def __init__(self):
        import torch
        from torchaudio.pipelines import MMS_FA

        self.torch = torch
        self.bundle = MMS_FA
        self.model = MMS_FA.get_model()  # includes the <star> wildcard token
        self.model.eval()
        self.tokenizer = MMS_FA.get_tokenizer()
        self.aligner = MMS_FA.get_aligner()

    def word_times(self, audio, words):
        """Return (start, end, score) in seconds for every word."""
        torch = self.torch
        wave = torch.from_numpy(audio).unsqueeze(0)
        with torch.inference_mode():
            emission, _ = self.model(wave)
        # a "*" between words absorbs speech that is not in the transcript
        starred = ["*"]
        for w in words:
            starred += [w, "*"]
        spans = self.aligner(emission[0], self.tokenizer(starred))
        ratio = wave.size(1) / emission.size(1) / SAMPLE_RATE
        times = []
        for w, span in zip(starred, spans):
            if w == "*":
                continue
            score = sum(s.score * len(s) for s in span) / sum(len(s) for s in span)
            times.append((round(span[0].start * ratio, 3),
                          round(span[-1].end * ratio, 3), round(score, 3)))
        return times


def turn_times(dialogue, words, owner, times):
    """Collapse word times into one (start, end) per turn."""
    result = [None] * len(dialogue)
    for (start, end, score), i in zip(times, owner):
        if result[i] is None:
            result[i] = {"start": start, "end": end, "scores": [score]}
        else:
            result[i]["end"] = end
            result[i]["scores"].append(score)
    out = []
    for i, r in enumerate(result):
        if r is None:
            out.append({"start": None, "end": None, "align_score": None})
        else:
            out.append({"start": r["start"], "end": r["end"],
                        "align_score": round(sum(r["scores"]) / len(r["scores"]), 3)})
    # turns with no alignable words (e.g. only "...") get the gap around them
    for i, t in enumerate(out):
        if t["start"] is None:
            prev = next((o["end"] for o in reversed(out[:i]) if o["end"] is not None), 0.0)
            nxt = next((o["start"] for o in out[i + 1:] if o["start"] is not None), prev)
            t["start"], t["end"] = prev, max(prev, nxt)
    return out


# ---------------------------------------------------------------- NOVA merge

def nova_labels(start, end, tiers, min_overlap):
    """Labels per scheme whose segments overlap [start, end]."""
    labels = {}
    annotators = {}
    for t in tiers:
        annotators.setdefault(t["scheme"], set()).add(t["annotator"])
    for t in tiers:
        key = t["scheme"]
        if len(annotators[key]) > 1:
            key = f"{t['scheme']}/{t['annotator']}"
        hits = labels.setdefault(key, [])
        for seg in t["segments"]:
            if seg["label"] is None:
                continue
            overlap = min(end, seg["to"]) - max(start, seg["from"])
            # a very short turn counts if it lies inside the segment
            if overlap >= min(min_overlap, end - start) and overlap > 0 \
                    and seg["label"] not in hits:
                hits.append(seg["label"])
    return labels


# -------------------------------------------------------------------- driver

def find_media(media_dir, session):
    d = Path(media_dir)
    for ext in MEDIA_EXT:
        if (d / f"{session}{ext}").exists():
            return d / f"{session}{ext}"
    sess_dir = d / session
    if sess_dir.is_dir():
        files = sorted(f for f in sess_dir.iterdir() if f.suffix.lower() in MEDIA_EXT)
        preferred = [f for f in files if "video" in f.name.lower()]
        if preferred or files:
            return (preferred or files)[0]
    return None


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def write_srt(path, dialogue):
    with open(path, "w", encoding="utf-8") as f:
        for n, turn in enumerate(dialogue, 1):
            f.write(f"{n}\n{srt_time(turn['start'])} --> {srt_time(turn['end'])}\n"
                    f"{turn['speaker']}: {str(turn.get('text') or '').strip()}\n\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dialogues", default="json_data")
    ap.add_argument("--media", required=True, help="folder with the session videos")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--nova-files", help="folder of exported NOVA annotations")
    src.add_argument("--mongo", help="MongoDB URI of the NOVA server")
    ap.add_argument("--db", default="uncertainty-roleplay")
    ap.add_argument("--annotators", nargs="*", help="keep only these annotators")
    ap.add_argument("--min-overlap", type=float, default=0.2,
                    help="seconds a NOVA segment must overlap a turn (default 0.2)")
    ap.add_argument("--out", default="nova_release")
    args = ap.parse_args()

    if num2words is None:
        print("note: num2words not installed, numbers like 2003 are skipped "
              "during alignment (pip install num2words)")

    tiers_by_scene = {}
    if args.nova_files or args.mongo:
        sessions = (load_from_files(args.nova_files) if args.nova_files
                    else load_from_mongo(args.mongo, args.db, args.annotators))
        for s in build_standoff(sessions):
            tiers = [t for t in s["tiers"]
                     if not args.annotators or t["annotator"] in args.annotators]
            tiers_by_scene[(s["group"], s["scene"])] = tiers

    out = Path(args.out)
    for sub in ("merged", "timings", "srt"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    aligner = None
    problems = []
    for path in sorted(Path(args.dialogues).glob("Group*.json")):
        group = int(re.search(r"\d+", path.stem).group())
        scenes = json.loads(path.read_text(encoding="utf-8"))
        for scene in scenes:
            if not isinstance(scene.get("scene"), (int, float)) \
                    or scene["scene"] != scene["scene"]:
                continue  # empty padding row (scene is null/NaN)
            session = f"Group{group}_{int(scene['scene'])}"
            dialogue = scene.get("dialogue") or []
            cache = out / "timings" / f"{session}.json"

            if cache.exists():
                timing = json.loads(cache.read_text(encoding="utf-8"))
            else:
                media = find_media(args.media, session)
                words, owner = turn_tokens(dialogue)
                if media is None or not words:
                    problems.append(f"{session}: " +
                                    ("no video/audio found" if media is None else "no text"))
                    continue
                if aligner is None:
                    print("loading MMS aligner (downloads ~1 GB on first run)...")
                    aligner = Aligner()
                print(f"aligning {session} ({media.name}, {len(dialogue)} turns)")
                times = aligner.word_times(load_audio(media), words)
                timing = turn_times(dialogue, words, owner, times)
                cache.write_text(json.dumps(timing, indent=1), encoding="utf-8")

            tiers = tiers_by_scene.get((group, int(scene["scene"])))
            if (args.nova_files or args.mongo) and tiers is None:
                problems.append(f"{session}: no NOVA annotations")
            for turn, t in zip(dialogue, timing):
                turn.update(t)
                if tiers is not None:
                    turn["nova"] = nova_labels(t["start"], t["end"], tiers,
                                               args.min_overlap)
            low = [i for i, t in enumerate(timing)
                   if t["align_score"] is not None and t["align_score"] < 0.3]
            if low:
                problems.append(f"{session}: check turns {low} (low alignment score)")
            write_srt(out / "srt" / f"{session}.srt", dialogue)

        (out / "merged" / path.name).write_text(
            json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done -> {out / 'merged'}")
    if problems:
        print("\nto check:\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    main()
