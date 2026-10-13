# PDF2ZH Link Repair

Restore and repair hyperlinks in PDF documents translated with **pdf2zh / pdf2zh_next**.

PDF translation can remove hyperlink annotations, preserve outdated clickable regions, or break internal document navigation.

This project provides a lightweight Python utility that repairs hyperlinks using **text features, PDF geometry, and existing annotation metadata** — without OCR, character-count-based positioning, or retranslation.

## Features

- Automatically detects bilingual (dual) and monolingual (mono) PDF layouts
- Restores missing external hyperlinks in bilingual PDFs
- Restores internal document navigation
- Preserves existing hyperlinks in monolingual PDFs
- Repairs certain invalid internal PDF destinations
- Preserves PDF page content and bookmarks
- Generates reports for detected link-position issues
- Does not modify the original PDF files

## Requirements

- Python 3.10+
- PyMuPDF

Install the dependency:

```bash
pip install pymupdf
```

## Usage

Place `restore_pdf_links.py` in your working directory.

### Automatic detection (recommended)

```bash
python restore_pdf_links.py original.pdf translated.pdf
```

The script automatically identifies the PDF layout and selects the appropriate repair method.

### Bilingual PDF

```bash
python restore_pdf_links.py original.pdf translated.dual.pdf --mode dual
```

The original-language side is detected automatically.

You can also specify it manually:

```bash
python restore_pdf_links.py original.pdf translated.dual.pdf --mode dual --side left
```

### Monolingual PDF

```bash
python restore_pdf_links.py original.pdf translated.mono.pdf --mode mono
```

### Custom output filename

```bash
python restore_pdf_links.py original.pdf translated.pdf -o repaired.pdf
```

By default, the output is:

`translated.with_links.pdf`

The original and translated input files are never overwritten.

## How it works

### Bilingual mode

For side-by-side bilingual PDFs, the utility identifies the original-language half and matches its content against the source PDF.

The matching process uses:

1. Unique three-word text sequences as content features
2. Two-dimensional word coordinates
3. Geometric transformation estimation
4. Position consistency validation

Once the original-language region is verified, the script restores hyperlink annotations and their destinations.

**Important:** Links are restored to the original-language side only. The translated side remains unchanged.

If the original text cannot be matched reliably, the process stops rather than guessing hyperlink positions.

### Monolingual mode

Monolingual translation can change word lengths, line breaks, and paragraph positions.

Therefore, the bilingual matching strategy cannot be applied directly.

Instead, the utility:

1. Validates existing PDF hyperlink annotations
2. Checks page geometry and structural compatibility
3. Preserves existing hyperlink targets and clickable regions
4. Repairs supported internal document destinations
5. Reports clickable regions that no longer overlap translated text

**Important:** This mode does not automatically relocate hyperlinks to their translated text labels. Some links may remain clickable only at their original positions.

## Validation

Tested using the Red Hat OpenShift Container Platform 4.21 *Installing on Google Cloud* documentation.

| Metric | Result |
|---|---:|
| PDF pages | 483 |
| Original hyperlinks | 934 |
| External URLs | 322 |
| Internal document links | 612 |
| Links after repair | 934 |
| PDF bookmarks preserved | 402 |

For this test document:

- All external URLs were retained or restored.
- All internal links were converted to valid navigation targets.
- PDF page content streams remained unchanged.
- Sample rendered pages were pixel-identical before and after repair.

The source PDF contained 68 malformed internal destinations. The utility recovered 61 using document outline features and used page-level fallback destinations for 7 numbered callouts.

In the monolingual document, 112 clickable rectangles were found not to overlap translated words, primarily in the table of contents. These were reported rather than repositioned without reliable evidence.

## Limitations

- Both input PDFs must have the same number of pages.
- Bilingual mode requires a side-by-side layout containing the original text.
- Monolingual mode requires existing compatible link annotations.
- Internal destination recovery for malformed source PDFs may depend on document-specific bookmark structures.
- Scanned or image-only PDFs are not supported for text-feature matching.
- Significant page reordering, cropping, or layout restructuring may prevent reliable restoration.
- Restored link counts do not necessarily guarantee correct clickable-text alignment.

The utility prioritizes verifiable repairs over speculative positioning.

## Generated reports

Depending on the selected mode, the utility may generate:

- `.link-repairs.txt` — repaired or approximate internal destinations
- `.link-report.txt` — monolingual hyperlink coverage and repair diagnostics

Review these files when the source PDF contains malformed links or when the translation changes document layout.

## Project structure

```text
pdf2zh-link-repair/
├── README.md
└── restore_pdf_links.py
```

## License

No license has been specified yet. Add a `LICENSE` file before distributing the project under an open-source license.

## Disclaimer

This is an independent utility intended for repairing hyperlinks in translated PDF documents. It is not an official component of pdf2zh, pdf2zh_next, or Red Hat.
