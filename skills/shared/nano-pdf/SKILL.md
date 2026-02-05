---
name: nano_pdf
description: Edit PDFs with natural-language instructions using the nano-pdf CLI.
version: 1.0.0
author: nano-pdf
homepage: https://pypi.org/project/nano-pdf/
requires:
  bins:
    - nano-pdf
install:
  - kind: pip
    package: nano-pdf
---

# nano-pdf

Edit PDFs using natural-language instructions via the `nano-pdf` CLI.

## Install

Install the CLI with uv (recommended):

```bash
uv pip install nano-pdf
```

After install, verify the binary exists:

```bash
command -v nano-pdf && nano-pdf --help
```

## Edit a Page

```bash
nano-pdf edit <input.pdf> <page_number> "<instruction>" [-o output.pdf]
```

### Examples

```bash
# Edit page 1 of a presentation
nano-pdf edit deck.pdf 1 "Change the title to 'Q3 Results' and fix the typo in the subtitle"

# Edit with custom output file
nano-pdf edit report.pdf 3 "Update the chart legend to show 2024 data" -o report_updated.pdf

# Fix text on a specific page
nano-pdf edit contract.pdf 2 "Replace 'John Doe' with 'Jane Smith' in the signature block"
```

## Notes

- Page numbers are 1-based (page 1 is the first page)
- Output defaults to overwriting the input file; use `-o` to specify a different output
- Always verify the output PDF before sharing
- Works best with text-based PDFs (not scanned images)
