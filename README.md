**Convert Excel to JSON**
- Inspect Excel workbooks and convert sheets to JSON.

**Features**
- Lists sheet names.
- Shows structure: columns, dtypes, non-null counts, sample values, and a row preview.
- Exports a single sheet or all sheets to JSON.

**Usage**
- List sheets:
  - `python convert_excel_to_json/convert_excel_to_json.py --input data.xlsx --list-sheets`
- Show structure and export first sheet:
  - `python convert_excel_to_json/convert_excel_to_json.py --input data.xlsx --show-structure --output data.json`
- Export a specific sheet with summary saved:
  - `python convert_excel_to_json/convert_excel_to_json.py --input data.xlsx --sheet Sheet1 --output sheet1.json --show-structure --summary-out sheet1_summary.json`
- Export all sheets to a single JSON object:
  - `python convert_excel_to_json/convert_excel_to_json.py --input data.xlsx --all-sheets --output all_sheets.json --show-structure --summary-out all_sheets_summary.json`

**Output**
- JSON data uses `records` orientation (list of objects).
- Structure summary is separate (optional) JSON with inferred schema and preview.

**Requirements**
- Uses `pandas` and `openpyxl` (already listed in the root requirements.txt).