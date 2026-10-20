import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def list_sheets(xlsx_path: Path) -> List[str]:
    xl = pd.ExcelFile(xlsx_path)
    return list(xl.sheet_names)


def load_sheet(xlsx_path: Path, sheet_name: Optional[str] = None) -> pd.DataFrame:
    return pd.read_excel(xlsx_path, sheet_name=sheet_name)


def infer_structure(df: pd.DataFrame, preview_rows: int = 5) -> Dict[str, Any]:
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}
    non_null = {col: int(df[col].notna().sum()) for col in df.columns}
    examples: Dict[str, List[Any]] = {}
    for col in df.columns:
        vals = df[col].dropna().unique().tolist()
        # Keep only the first few examples and convert un-JSON-able values to string
        slim: List[Any] = []
        for v in vals[:3]:
            try:
                json.dumps(v)
                slim.append(v)
            except TypeError:
                slim.append(str(v))
        examples[col] = slim

    preview = df.head(preview_rows).to_dict(orient="records")
    return {
        "num_rows": int(len(df)),
        "num_columns": int(len(df.columns)),
        "columns": list(df.columns),
        "dtypes": dtypes,
        "non_null_counts": non_null,
        "examples_per_column": examples,
        "preview_first_rows": preview,
    }


def convert_to_json_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    return df.to_dict(orient="records")


def write_json(data: Any, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find_col(df: pd.DataFrame, candidates: List[str], required: bool = True) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    if required:
        raise ValueError(f"Missing required column. Tried: {candidates}")
    return None


def build_ratings_by_scene(df_ratings: pd.DataFrame) -> Dict[Any, Dict[str, Any]]:
    scene_col = _find_col(df_ratings, ["Scene", "scene", "SCENE"])  # required
    # Identify user_rate_* and robot_rate_* columns
    user_rate_cols = [c for c in df_ratings.columns if c.lower().startswith("user_rate_")]
    robot_rate_cols = [c for c in df_ratings.columns if c.lower().startswith("robot_rate_")]

    by_scene: Dict[Any, Dict[str, Any]] = {}
    for _, row in df_ratings.iterrows():
        scene_id = row[scene_col]
        record: Dict[str, Any] = {}
        # Top-level fields except the nested rate columns
        for c in df_ratings.columns:
            cl = c.lower()
            if cl == scene_col.lower():
                continue
            if cl.startswith("user_rate_") or cl.startswith("robot_rate_"):
                continue
            val = row[c]
            if pd.isna(val):
                val = None
            record[c] = val

        # Nested dicts for rates
        user_rate: Dict[str, Any] = {}
        for c in user_rate_cols:
            key = c[len("user_rate_") :]
            val = row[c]
            if pd.isna(val):
                val = None
            user_rate[key] = val

        robot_rate: Dict[str, Any] = {}
        for c in robot_rate_cols:
            key = c[len("robot_rate_") :]
            val = row[c]
            if pd.isna(val):
                val = None
            robot_rate[key] = val

        record["user_rate"] = user_rate
        record["robot_rate"] = robot_rate

        by_scene[scene_id] = {"scene": scene_id, **record}

    return by_scene


def build_dialogue_by_scene(df_dialogue: pd.DataFrame) -> Dict[Any, List[Dict[str, Any]]]:
    scene_col = _find_col(df_dialogue, ["Scene", "scene", "SCENE"])  # required
    speaker_col = _find_col(df_dialogue, ["Player", "player"])  # required
    text_col = _find_col(df_dialogue, ["text", "utterance", "Text", "Utterance"])  # required
    annotation_col = _find_col(
        df_dialogue,
        ["annotation", "annotations", "label", "Annotation", "Label"],
        required=False,
    )

    by_scene: Dict[Any, List[Dict[str, Any]]] = {}
    for _, row in df_dialogue.iterrows():
        scene_id = row[scene_col]
        speaker = row[speaker_col]
        text = row[text_col]
        if pd.isna(speaker):
            speaker = None
        if pd.isna(text):
            text = None
        item: Dict[str, Any] = {"speaker": speaker, "text": text}
        if annotation_col is not None:
            ann = row[annotation_col]
            if pd.isna(ann):
                ann = None
            item["annotation"] = ann
        by_scene.setdefault(scene_id, []).append(item)
    return by_scene


def build_interaction_json(
    xlsx_path: Path,
    ratings_sheet: Optional[str] = None,
    dialogue_sheet: Optional[str] = None,
) -> Dict[str, Any]:
    # Load workbook and default sheet names if not provided
    xl = pd.ExcelFile(xlsx_path)
    sheets = list(xl.sheet_names)
    if ratings_sheet is None:
        ratings_sheet = sheets[0] if sheets else None
    if dialogue_sheet is None:
        dialogue_sheet = sheets[1] if len(sheets) > 1 else sheets[0] if sheets else None
    if ratings_sheet is None or dialogue_sheet is None:
        raise ValueError("Workbook must contain at least two sheets for ratings and dialogue.")

    df_ratings = pd.read_excel(xlsx_path, sheet_name=ratings_sheet)
    print(df_ratings)
    df_dialogue = pd.read_excel(xlsx_path, sheet_name=dialogue_sheet)

    ratings_by_scene = build_ratings_by_scene(df_ratings)
    dialogue_by_scene = build_dialogue_by_scene(df_dialogue)

    # Merge into a list of scenes, preserving all scene ids from ratings; include any extra from dialogue
    all_scene_ids = set(ratings_by_scene.keys()) | set(dialogue_by_scene.keys())
    scenes: List[Dict[str, Any]] = []
    for scene_id in sorted(all_scene_ids, key=lambda x: (str(type(x)), x)):
        base = ratings_by_scene.get(scene_id, {"scene": scene_id, "user_rate": {}, "robot_rate": {}})
        dlg = dialogue_by_scene.get(scene_id, [])
        scenes.append({**base, "dialogue": dlg})

    return scenes


def main():
    parser = argparse.ArgumentParser(description="Convert Excel (.xlsx/.xlsm) to JSON and inspect structure.")
    parser.add_argument("--input", required=True, help="Path to Excel file (.xlsx/.xlsm)")
    parser.add_argument("--sheet", default=None, help="Sheet name to load (default: first sheet)")
    parser.add_argument("--output", default=None, help="Output JSON path (default: <input-stem>.json)")
    parser.add_argument("--list-sheets", action="store_true", help="List sheet names and exit")
    parser.add_argument("--show-structure", action="store_true", help="Print structure summary to stdout")
    parser.add_argument("--summary-out", default=None, help="Optional path to write structure summary JSON")
    parser.add_argument("--all-sheets", action="store_true", help="Export all sheets to a single JSON object")
    parser.add_argument("--preview-rows", type=int, default=5, help="Rows to include in preview (default: 5)")
    # Interaction-specific export
    parser.add_argument("--interaction", action="store_true", help="Treat workbook as interaction dataset (ratings + dialogue)")
    parser.add_argument("--ratings-sheet", default=None, help="Sheet name for ratings (default: first sheet)")
    parser.add_argument("--dialogue-sheet", default=None, help="Sheet name for dialogue (default: second sheet)")
    args = parser.parse_args()

    xlsx_path = Path(args.input)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Input file not found: {xlsx_path}")

    sheets = list_sheets(xlsx_path)
    if args.list_sheets:
        print("Sheets:")
        for s in sheets:
            print(f"- {s}")
        return

    # Determine output path
    out_path = Path(args.output) if args.output else xlsx_path.with_suffix(".json")

    # Interaction mode: ratings + dialogue merged by Scene
    if args.interaction:
        payload = build_interaction_json(
            xlsx_path=xlsx_path,
            ratings_sheet=args.ratings_sheet,
            dialogue_sheet=args.dialogue_sheet,
        )
        write_json(payload, out_path)
        print(f"Wrote interaction JSON to {out_path}")
        return

    if args.all_sheets:
        data: Dict[str, Any] = {}
        summary: Dict[str, Any] = {"sheets": sheets, "structures": {}}
        for s in sheets:
            df = load_sheet(xlsx_path, sheet_name=s)
            data[s] = convert_to_json_records(df)
            summary["structures"][s] = infer_structure(df, preview_rows=args.preview_rows)
        write_json(data, out_path)
        if args.show_structure:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.summary_out:
            write_json(summary, Path(args.summary_out))
        print(f"Wrote JSON for all sheets to {out_path}")
        return

    # Single sheet path
    sheet_name = args.sheet or (sheets[0] if sheets else None)
    if sheet_name is None:
        raise ValueError("No sheets found in workbook.")

    df = load_sheet(xlsx_path, sheet_name=sheet_name)
    structure = infer_structure(df, preview_rows=args.preview_rows)
    data = convert_to_json_records(df)
    write_json(data, out_path)

    if args.show_structure:
        print(json.dumps({"sheet": sheet_name, **structure}, ensure_ascii=False, indent=2))
    if args.summary_out:
        write_json({"sheet": sheet_name, **structure}, Path(args.summary_out))

    print(f"Wrote JSON for sheet '{sheet_name}' to {out_path}")


if __name__ == "__main__":
    main()
