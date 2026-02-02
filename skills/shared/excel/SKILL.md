---
name: excel
description: Read, write, edit, and format Excel files (.xlsx). Create spreadsheets, manipulate data, apply formatting, manage sheets, merge cells, find/replace, and export to CSV/JSON/Markdown.
version: 1.0.0
author: system
requires:
  bins:
    - uv
    - python3
  pip:
    - openpyxl
    - xlrd
---

# Excel

Comprehensive Excel file manipulation - read, write, edit, format, and export.

## Dependencies

Install required Python packages with uv:

```bash
uv pip install openpyxl xlrd
```

## Quick Reference

```bash
# Get file info
uv run python {baseDir}/scripts/excel.py info report.xlsx

# Read sheet data
uv run python {baseDir}/scripts/excel.py read report.xlsx
uv run python {baseDir}/scripts/excel.py read report.xlsx --format markdown
uv run python {baseDir}/scripts/excel.py read report.xlsx --sheet "Sales" --range A1:D10

# Read specific cell
uv run python {baseDir}/scripts/excel.py cell report.xlsx B5

# Create new workbook
uv run python {baseDir}/scripts/excel.py create output.xlsx
uv run python {baseDir}/scripts/excel.py create output.xlsx --sheets "Data,Summary,Charts"

# Write data
uv run python {baseDir}/scripts/excel.py write output.xlsx --data '[[1,2,3],[4,5,6]]'
uv run python {baseDir}/scripts/excel.py write output.xlsx --data '{"headers":["Name","Age"],"rows":[["Alice",30],["Bob",25]]}'

# Edit a cell
uv run python {baseDir}/scripts/excel.py edit report.xlsx A1 "New Value"
uv run python {baseDir}/scripts/excel.py edit report.xlsx B2 "SUM(A1:A10)" --formula

# Export
uv run python {baseDir}/scripts/excel.py to-csv report.xlsx output.csv
uv run python {baseDir}/scripts/excel.py to-json report.xlsx output.json
uv run python {baseDir}/scripts/excel.py to-markdown report.xlsx
```

## Commands

### Reading Data

```bash
# Get workbook metadata (sheets, dimensions, row/column counts)
uv run python {baseDir}/scripts/excel.py info file.xlsx

# Read sheet data in various formats
uv run python {baseDir}/scripts/excel.py read file.xlsx                     # JSON output
uv run python {baseDir}/scripts/excel.py read file.xlsx --format csv        # CSV output
uv run python {baseDir}/scripts/excel.py read file.xlsx --format markdown   # Markdown table
uv run python {baseDir}/scripts/excel.py read file.xlsx --sheet "Sheet2"    # Specific sheet
uv run python {baseDir}/scripts/excel.py read file.xlsx --range A1:D10      # Specific range

# Read a specific cell (value, formula, data type, merge status)
uv run python {baseDir}/scripts/excel.py cell file.xlsx A1
uv run python {baseDir}/scripts/excel.py cell file.xlsx B5 --sheet "Data"
```

### Creating & Writing

```bash
# Create new workbook
uv run python {baseDir}/scripts/excel.py create new.xlsx
uv run python {baseDir}/scripts/excel.py create new.xlsx --sheets "Sheet1,Sheet2,Summary"

# Write data (2D array, headers+rows, or key-value pairs)
uv run python {baseDir}/scripts/excel.py write file.xlsx --data '[[1,2,3],[4,5,6]]'
uv run python {baseDir}/scripts/excel.py write file.xlsx --data '{"headers":["A","B"],"rows":[[1,2],[3,4]]}'
uv run python {baseDir}/scripts/excel.py write file.xlsx --data '[[1,2]]' --start C5

# Import from CSV or JSON
uv run python {baseDir}/scripts/excel.py from-csv data.csv output.xlsx
uv run python {baseDir}/scripts/excel.py from-json data.json output.xlsx
```

### Editing

```bash
# Edit cell value or formula
uv run python {baseDir}/scripts/excel.py edit file.xlsx A1 "New Value"
uv run python {baseDir}/scripts/excel.py edit file.xlsx C3 "SUM(A1:B2)" --formula

# Find and replace
uv run python {baseDir}/scripts/excel.py find file.xlsx "search term"
uv run python {baseDir}/scripts/excel.py replace file.xlsx "old" "new"
```

### Sheet Management

```bash
uv run python {baseDir}/scripts/excel.py add-sheet file.xlsx "NewSheet"
uv run python {baseDir}/scripts/excel.py rename-sheet file.xlsx "Sheet1" "Data"
uv run python {baseDir}/scripts/excel.py delete-sheet file.xlsx "OldSheet"
uv run python {baseDir}/scripts/excel.py copy-sheet file.xlsx "Template" "January"
```

### Row & Column Operations

```bash
uv run python {baseDir}/scripts/excel.py insert-rows file.xlsx 5 --count 3
uv run python {baseDir}/scripts/excel.py insert-cols file.xlsx C --count 2
uv run python {baseDir}/scripts/excel.py delete-rows file.xlsx 5 --count 3
uv run python {baseDir}/scripts/excel.py delete-cols file.xlsx B --count 2
```

### Cell Operations

```bash
uv run python {baseDir}/scripts/excel.py merge file.xlsx A1:C1
uv run python {baseDir}/scripts/excel.py unmerge file.xlsx A1:C1
```

### Formatting

```bash
# Font styling
uv run python {baseDir}/scripts/excel.py format file.xlsx A1:D1 --bold --italic
uv run python {baseDir}/scripts/excel.py format file.xlsx A1:D1 --font-size 14 --font-color RED

# Background and alignment
uv run python {baseDir}/scripts/excel.py format file.xlsx A1:D1 --bg-color YELLOW
uv run python {baseDir}/scripts/excel.py format file.xlsx A:A --align center --valign top

# Borders and text wrapping
uv run python {baseDir}/scripts/excel.py format file.xlsx A1:D10 --border thin
uv run python {baseDir}/scripts/excel.py format file.xlsx B2:B100 --wrap

# Resize rows/columns
uv run python {baseDir}/scripts/excel.py resize file.xlsx --row 1:30 --col A:20

# Freeze panes
uv run python {baseDir}/scripts/excel.py freeze file.xlsx A2    # Freeze row 1
```

### Export

```bash
uv run python {baseDir}/scripts/excel.py to-csv file.xlsx output.csv
uv run python {baseDir}/scripts/excel.py to-json file.xlsx output.json
uv run python {baseDir}/scripts/excel.py to-markdown file.xlsx
```

## Colors

Named: `RED`, `GREEN`, `BLUE`, `YELLOW`, `WHITE`, `BLACK`, `GRAY`, `ORANGE`, `PURPLE`, `PINK`, `CYAN`

Hex: `#FF0000`, `#4472C4`, `00FF00` (with or without #)

## Output Format

All commands output JSON with `success: true/false`. Use `--format markdown` or `--format csv` with `read` command for alternative output.
