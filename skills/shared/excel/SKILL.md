---
name: excel
description: Read, write, edit, and format Excel files (.xlsx). Create spreadsheets, manipulate data, apply formatting, manage sheets, merge cells, find/replace, and export to CSV/JSON/Markdown.
version: 1.0.0
author: system
requires:
  bins:
    - python3
  pip:
    - openpyxl
---

# Excel

Comprehensive Excel file manipulation - read, write, edit, format, and export.

## Quick Reference

```bash
# Get file info
python3 {baseDir}/scripts/excel.py info report.xlsx

# Read sheet data
python3 {baseDir}/scripts/excel.py read report.xlsx
python3 {baseDir}/scripts/excel.py read report.xlsx --format markdown
python3 {baseDir}/scripts/excel.py read report.xlsx --sheet "Sales" --range A1:D10

# Read specific cell
python3 {baseDir}/scripts/excel.py cell report.xlsx B5

# Create new workbook
python3 {baseDir}/scripts/excel.py create output.xlsx
python3 {baseDir}/scripts/excel.py create output.xlsx --sheets "Data,Summary,Charts"

# Write data
python3 {baseDir}/scripts/excel.py write output.xlsx --data '[[1,2,3],[4,5,6]]'
python3 {baseDir}/scripts/excel.py write output.xlsx --data '{"headers":["Name","Age"],"rows":[["Alice",30],["Bob",25]]}'

# Edit a cell
python3 {baseDir}/scripts/excel.py edit report.xlsx A1 "New Value"
python3 {baseDir}/scripts/excel.py edit report.xlsx B2 "SUM(A1:A10)" --formula

# Export
python3 {baseDir}/scripts/excel.py to-csv report.xlsx output.csv
python3 {baseDir}/scripts/excel.py to-json report.xlsx output.json
python3 {baseDir}/scripts/excel.py to-markdown report.xlsx
```

## Commands

### Reading Data

```bash
# Get workbook metadata (sheets, dimensions, row/column counts)
python3 {baseDir}/scripts/excel.py info file.xlsx

# Read sheet data in various formats
python3 {baseDir}/scripts/excel.py read file.xlsx                     # JSON output
python3 {baseDir}/scripts/excel.py read file.xlsx --format csv        # CSV output
python3 {baseDir}/scripts/excel.py read file.xlsx --format markdown   # Markdown table
python3 {baseDir}/scripts/excel.py read file.xlsx --sheet "Sheet2"    # Specific sheet
python3 {baseDir}/scripts/excel.py read file.xlsx --range A1:D10      # Specific range

# Read a specific cell (value, formula, data type, merge status)
python3 {baseDir}/scripts/excel.py cell file.xlsx A1
python3 {baseDir}/scripts/excel.py cell file.xlsx B5 --sheet "Data"
```

### Creating & Writing

```bash
# Create new workbook
python3 {baseDir}/scripts/excel.py create new.xlsx
python3 {baseDir}/scripts/excel.py create new.xlsx --sheets "Sheet1,Sheet2,Summary"

# Write data (2D array, headers+rows, or key-value pairs)
python3 {baseDir}/scripts/excel.py write file.xlsx --data '[[1,2,3],[4,5,6]]'
python3 {baseDir}/scripts/excel.py write file.xlsx --data '{"headers":["A","B"],"rows":[[1,2],[3,4]]}'
python3 {baseDir}/scripts/excel.py write file.xlsx --data '[[1,2]]' --start C5

# Import from CSV or JSON
python3 {baseDir}/scripts/excel.py from-csv data.csv output.xlsx
python3 {baseDir}/scripts/excel.py from-json data.json output.xlsx
```

### Editing

```bash
# Edit cell value or formula
python3 {baseDir}/scripts/excel.py edit file.xlsx A1 "New Value"
python3 {baseDir}/scripts/excel.py edit file.xlsx C3 "SUM(A1:B2)" --formula

# Find and replace
python3 {baseDir}/scripts/excel.py find file.xlsx "search term"
python3 {baseDir}/scripts/excel.py replace file.xlsx "old" "new"
```

### Sheet Management

```bash
python3 {baseDir}/scripts/excel.py add-sheet file.xlsx "NewSheet"
python3 {baseDir}/scripts/excel.py rename-sheet file.xlsx "Sheet1" "Data"
python3 {baseDir}/scripts/excel.py delete-sheet file.xlsx "OldSheet"
python3 {baseDir}/scripts/excel.py copy-sheet file.xlsx "Template" "January"
```

### Row & Column Operations

```bash
python3 {baseDir}/scripts/excel.py insert-rows file.xlsx 5 --count 3
python3 {baseDir}/scripts/excel.py insert-cols file.xlsx C --count 2
python3 {baseDir}/scripts/excel.py delete-rows file.xlsx 5 --count 3
python3 {baseDir}/scripts/excel.py delete-cols file.xlsx B --count 2
```

### Cell Operations

```bash
python3 {baseDir}/scripts/excel.py merge file.xlsx A1:C1
python3 {baseDir}/scripts/excel.py unmerge file.xlsx A1:C1
```

### Formatting

```bash
# Font styling
python3 {baseDir}/scripts/excel.py format file.xlsx A1:D1 --bold --italic
python3 {baseDir}/scripts/excel.py format file.xlsx A1:D1 --font-size 14 --font-color RED

# Background and alignment
python3 {baseDir}/scripts/excel.py format file.xlsx A1:D1 --bg-color YELLOW
python3 {baseDir}/scripts/excel.py format file.xlsx A:A --align center --valign top

# Borders and text wrapping
python3 {baseDir}/scripts/excel.py format file.xlsx A1:D10 --border thin
python3 {baseDir}/scripts/excel.py format file.xlsx B2:B100 --wrap

# Resize rows/columns
python3 {baseDir}/scripts/excel.py resize file.xlsx --row 1:30 --col A:20

# Freeze panes
python3 {baseDir}/scripts/excel.py freeze file.xlsx A2    # Freeze row 1
```

### Export

```bash
python3 {baseDir}/scripts/excel.py to-csv file.xlsx output.csv
python3 {baseDir}/scripts/excel.py to-json file.xlsx output.json
python3 {baseDir}/scripts/excel.py to-markdown file.xlsx
```

## Colors

Named: `RED`, `GREEN`, `BLUE`, `YELLOW`, `WHITE`, `BLACK`, `GRAY`, `ORANGE`, `PURPLE`, `PINK`, `CYAN`

Hex: `#FF0000`, `#4472C4`, `00FF00` (with or without #)

## Output Format

All commands output JSON with `success: true/false`. Use `--format markdown` or `--format csv` with `read` command for alternative output.
