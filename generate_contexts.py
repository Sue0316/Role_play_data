import argparse
import json
from pathlib import Path
from typing import List, Dict, Any


def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def format_turn(turn: Dict[str, Any]) -> str:
    speaker = str(turn.get("speaker", "")).strip() or "Unknown"
    text = normalize_text(str(turn.get("text", "")))
    return f"<{speaker}>: {text}"


def build_context(
    dialogue: List[Dict[str, Any]],
    idx: int,
    prev_turns: int,
    include_target: bool,
    next_turns: int,
) -> str:
    prev_parts = []
    for p in range(idx - prev_turns, idx):
        if 0 <= p < len(dialogue):
            prev_parts.append(format_turn(dialogue[p]))

    target_part = None
    if include_target and 0 <= idx < len(dialogue):
        target_part = format_turn(dialogue[idx])

    next_parts = []
    for n in range(idx + 1, idx + 1 + next_turns):
        if 0 <= n < len(dialogue):
            next_parts.append(format_turn(dialogue[n]))

    prev_str = " ".join(prev_parts).strip()
    next_str = " ".join(next_parts).strip()

    # Only place a single '# ' immediately before the target utterance
    out = "Context: "
    if prev_str:
        out += prev_str + " "
    if target_part:
        out += "# " + target_part
    if next_str:
        # Separate following turns by a single space, no additional '#'
        out += " " + next_str
    return out.strip()


def extract_missing_annotations(
    data: List[Dict[str, Any]], prev_turns: int = 1, include_target: bool = True, next_turns: int = 1
) -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for scene_idx, scene in enumerate(data):
        dialogue = scene.get("dialogue", []) or []
        for i, turn in enumerate(dialogue):
            ann = turn.get("annotation")
            missing = ann is None or (isinstance(ann, str) and not ann.strip())
            if not missing:
                continue
            ctx = build_context(dialogue, i, prev_turns, include_target, next_turns)
            outputs.append(
                {
                    "scene": scene.get("scene", scene_idx + 1),
                    "utterance_index": i,
                    "speaker": turn.get("speaker"),
                    "text": normalize_text(turn.get("text", "")),
                    "input": ctx,
                }
            )
    return outputs


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate 'Context: <Speaker>: … # …' lines for dialogue turns with missing annotations"
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("convert_excel_to_json") / "Group1_testing.json",
        help="Path to JSON file containing a list of scenes with 'dialogue' arrays",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("convert_excel_to_json") / "missing_annotation_contexts.txt",
        help="Path to write the contexts (one per line)",
    )
    parser.add_argument(
        "--prev-turns",
        type=int,
        default=1,
        help="How many previous turns to include before the target (default: 1)",
    )
    parser.add_argument(
        "--next-turns",
        type=int,
        default=1,
        help="How many following turns to include after the target (default: 1)",
    )
    parser.add_argument(
        "--no-include-target",
        action="store_true",
        help="If set, do not include the target missing-annotation utterance in the context",
    )

    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of scenes")

    contexts = extract_missing_annotations(
        data,
        prev_turns=args.prev_turns,
        include_target=not args.no_include_target,
        next_turns=args.next_turns,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for item in contexts:
            f.write(item["input"] + "\n")

    jsonl_path = args.output.with_suffix(".jsonl")
    with jsonl_path.open("w", encoding="utf-8") as jf:
        for item in contexts:
            jf.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Wrote {len(contexts)} contexts to {args.output}")
    print(f"Metadata JSONL written to {jsonl_path}")


if __name__ == "__main__":
    main()
