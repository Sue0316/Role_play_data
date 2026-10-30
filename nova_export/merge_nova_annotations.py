"""Collect NOVA annotations and attach them to the role-play dialogue JSON.

NOVA sessions are named "Group<N>_<scene>" (e.g. Group1_1 = Group 1, scene 1).
Each NOVA annotation is one tier (scheme x role x annotator) holding
time-aligned segments: from, to (seconds), label, confidence.

Two input sources are supported:

  1. Files exported from NOVA (no database access needed)
       --files DIR
     DIR holds one sub-folder per session (Group1_1/, Group1_2/, ...), each with
     the NOVA annotation files:
       <role>.<scheme>.<annotator>.annotation   (XML header with label names)
       <role>.<scheme>.<annotator>.annotation~  (data: from;to;label_id;conf)
     A CSV per tier (from;to;label;confidence, with or without header) is
     also accepted in place of the .annotation pair.

  2. The NOVA MongoDB database directly (needs `pip install pymongo`)
       --mongo mongodb://USER:PASS@nova.hcai.eu:37317 --db uncertainty-roleplay

Outputs (in --out):
  nova_annotations.json   standoff file: one entry per session with all tiers
  nova_annotations.csv    the same in long format (one row per segment)
  merged/Group<N>.json    copies of the dialogue JSON with a "nova_annotations"
                          field added to each scene (only with --dialogues)

Example:
  python nova_export/merge_nova_annotations.py --files nova_files \
      --dialogues json_data --out release
"""

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

SESSION_RE = re.compile(r"Group\s*(\d+)[_\- ]+(\d+)", re.IGNORECASE)


def parse_session(name):
    m = SESSION_RE.search(name)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------- file input

def read_annotation_pair(path):
    """Read a NOVA .annotation (XML) + .annotation~ (data) pair."""
    root = ET.parse(path).getroot()
    info = root.find("info")
    meta = root.find("meta")
    scheme = root.find("scheme")
    labels = {}
    if scheme is not None:
        for item in scheme.findall("item"):
            labels[item.get("id")] = item.get("name")
    tier = {
        "scheme": scheme.get("name") if scheme is not None else None,
        "type": scheme.get("type") if scheme is not None else None,
        "role": meta.get("role") if meta is not None else None,
        "annotator": meta.get("annotator") if meta is not None else None,
        "segments": [],
    }
    # fall back to the file name <role>.<scheme>.<annotator>.annotation
    parts = path.name[: -len(".annotation")].split(".")
    if len(parts) == 3:
        tier["role"] = tier["role"] or parts[0]
        tier["scheme"] = tier["scheme"] or parts[1]
        tier["annotator"] = tier["annotator"] or parts[2]

    data_path = Path(str(path) + "~")
    if info is not None and info.get("ftype", "ASCII").upper() != "ASCII":
        raise SystemExit(f"{path}: binary annotation, re-save it as ASCII in NOVA")
    if data_path.exists():
        for row in read_rows(data_path):
            seg = make_segment(row, labels)
            if seg:
                tier["segments"].append(seg)
    return tier


def read_rows(path):
    text = path.read_text(encoding="utf-8-sig")
    delim = ";" if text.count(";") >= text.count(",") else ","
    return [r for r in csv.reader(text.splitlines(), delimiter=delim) if r]


def make_segment(row, labels=None):
    try:
        start, end = float(row[0]), float(row[1])
    except (ValueError, IndexError):
        return None  # header line
    label = row[2].strip() if len(row) > 2 else ""
    if labels:
        label = labels.get(label, label)
    if label in ("", "-1"):
        label = None  # NOVA "garbage"/unlabelled
    conf = float(row[3]) if len(row) > 3 and row[3].strip() else None
    return {"from": round(start, 3), "to": round(end, 3),
            "label": label, "confidence": conf}


def read_csv_tier(path):
    parts = path.stem.split(".")
    tier = {"scheme": parts[1] if len(parts) == 3 else path.stem,
            "type": "DISCRETE",
            "role": parts[0] if len(parts) == 3 else None,
            "annotator": parts[2] if len(parts) == 3 else None,
            "segments": []}
    for row in read_rows(path):
        seg = make_segment(row)
        if seg:
            tier["segments"].append(seg)
    return tier


def load_from_files(root):
    sessions = defaultdict(list)
    for sess_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        for f in sorted(sess_dir.iterdir()):
            if f.suffix == ".annotation":
                sessions[sess_dir.name].append(read_annotation_pair(f))
            elif f.suffix.lower() == ".csv":
                sessions[sess_dir.name].append(read_csv_tier(f))
    return sessions


# --------------------------------------------------------------- mongo input

def load_from_mongo(uri, db_name, annotators=None):
    from pymongo import MongoClient

    db = MongoClient(uri)[db_name]
    names = {
        coll: {d["_id"]: d for d in db[coll].find()}
        for coll in ("Sessions", "Schemes", "Roles", "Annotators")
    }
    sessions = defaultdict(list)
    for anno in db["Annotations"].find():
        annotator = names["Annotators"][anno["annotator_id"]]["name"]
        if annotators and annotator not in annotators:
            continue
        scheme = names["Schemes"][anno["scheme_id"]]
        labels = {str(l["id"]): l["name"] for l in scheme.get("labels", [])}
        data = db["AnnotationData"].find_one({"_id": anno["data_id"]}) or {}
        tier = {
            "scheme": scheme["name"],
            "type": scheme.get("type"),
            "role": names["Roles"][anno["role_id"]]["name"],
            "annotator": annotator,
            "finished": anno.get("isFinished"),
            "segments": [],
        }
        for l in data.get("labels", []):
            label = labels.get(str(l.get("id")))
            tier["segments"].append({
                "from": round(float(l["from"]), 3),
                "to": round(float(l["to"]), 3),
                "label": label,
                "confidence": l.get("conf"),
            })
        sessions[names["Sessions"][anno["session_id"]]["name"]].append(tier)
    return sessions


# -------------------------------------------------------------------- output

def build_standoff(sessions):
    out = []
    for name in sorted(sessions, key=lambda n: (parse_session(n)[0] or 0,
                                                 parse_session(n)[1] or 0, n)):
        group, scene = parse_session(name)
        tiers = sorted(sessions[name], key=lambda t: (t["scheme"] or "",
                                                      t["role"] or "",
                                                      t["annotator"] or ""))
        for t in tiers:
            t["segments"].sort(key=lambda s: s["from"])
        out.append({"session": name, "group": group, "scene": scene,
                    "tiers": tiers})
    return out


def write_csv(standoff, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["session", "group", "scene", "scheme", "role", "annotator",
                    "from", "to", "duration", "label", "confidence"])
        for s in standoff:
            for t in s["tiers"]:
                for seg in t["segments"]:
                    w.writerow([s["session"], s["group"], s["scene"],
                                t["scheme"], t["role"], t["annotator"],
                                seg["from"], seg["to"],
                                round(seg["to"] - seg["from"], 3),
                                seg["label"], seg["confidence"]])


def merge_into_dialogues(standoff, dialogue_dir, out_dir):
    by_key = {(s["group"], s["scene"]): s for s in standoff}
    used, missing = set(), []
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(dialogue_dir).glob("Group*.json")):
        group = int(re.search(r"\d+", path.stem).group())
        scenes = json.loads(path.read_text(encoding="utf-8"))
        for scene in scenes:
            if not isinstance(scene.get("scene"), (int, float)) \
                    or scene["scene"] != scene["scene"]:
                continue  # empty padding row (scene is null/NaN)
            key = (group, int(scene["scene"]))
            match = by_key.get(key)
            scene["nova_session"] = match["session"] if match else None
            scene["nova_annotations"] = match["tiers"] if match else []
            if match:
                used.add(key)
            else:
                missing.append(f"Group{group}_{key[1]}")
        (out_dir / path.name).write_text(
            json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    if missing:
        print(f"{len(missing)} scenes without NOVA annotations: "
              + ", ".join(missing))
    for key in sorted(set(by_key) - used):
        print(f"NOVA session {by_key[key]['session']} has no dialogue scene")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", help="folder with one sub-folder per NOVA session")
    src.add_argument("--mongo", help="MongoDB URI of the NOVA server")
    ap.add_argument("--db", default="uncertainty-roleplay")
    ap.add_argument("--annotators", nargs="*", help="keep only these annotators")
    ap.add_argument("--dialogues", help="dialogue JSON folder to merge into")
    ap.add_argument("--out", default="nova_release")
    args = ap.parse_args()

    if args.files:
        sessions = load_from_files(args.files)
        if args.annotators:
            for k in sessions:
                sessions[k] = [t for t in sessions[k]
                               if t["annotator"] in args.annotators]
    else:
        sessions = load_from_mongo(args.mongo, args.db, args.annotators)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    standoff = build_standoff(sessions)
    (out / "nova_annotations.json").write_text(
        json.dumps(standoff, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(standoff, out / "nova_annotations.csv")
    n_seg = sum(len(t["segments"]) for s in standoff for t in s["tiers"])
    print(f"{len(standoff)} sessions, {n_seg} segments -> {out}")
    if args.dialogues:
        merge_into_dialogues(standoff, args.dialogues, out / "merged")


if __name__ == "__main__":
    main()
