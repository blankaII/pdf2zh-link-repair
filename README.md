# PDF2ZH Link Repair

Restore and repair hyperlinks in PDFs translated with `pdf2zh` / `pdf2zh_next`, without translating the document again.

PDF translation may remove link annotations, preserve clickable rectangles at outdated positions, or break internal navigation. This utility uses **text features, PDF geometry, and existing link annotations**—not character counts or OCR—to make repairs where alignment can be verified.

## Features

- Automatically select **dual** (side-by-side bilingual) or **mono** (translated-only) mode.
- **Dual:** identify the original-language half and rebuild external hyperlinks and internal jumps there.
- **Mono:** validate and retain existing hyperlinks, and repair supported internal destinations.
- Correct certain invalid internal link destinations found in the source PDF using outline and heading features.
- Preserve original page content, document appearance, and bookmarks.
- Produce a repair audit or link-coverage report.
- Refuse uncertain matches instead of guessing where links belong.

## Requirements

- Python 3.10+
- [PyMuPDF](https://pymupdf.readthedocs.io/) (tested with 1.26.7)

```bash
python -m pip install -r requirements.txt
```

Alternatively:

```bash
python -m pip install pymupdf
```

## Quick start

```bash
git clone https://github.com/blankaII/pdf2zh-link-repair.git
cd pdf2zh-link-repair

python pdf2zh-link-repair.py original.pdf translated.pdf
```

The utility detects the layout automatically. The default output is `translated.with_links.pdf`. It **does not overwrite input files**, and it stops if the proposed output already exists.

### Specify a mode

```bash
# Side-by-side bilingual PDF (automatically detect the source side)
python pdf2zh-link-repair.py original.pdf translated.dual.pdf --mode dual

# Explicitly specify which half contains the original text
python pdf2zh-link-repair.py original.pdf translated.dual.pdf --mode dual --side left

# Monolingual translated PDF
python pdf2zh-link-repair.py original.pdf translated.mono.pdf --mode mono

# Specify the output path
python pdf2zh-link-repair.py original.pdf translated.pdf -o repaired.pdf

# Show all options
python pdf2zh-link-repair.py --help
```

## How it works

### Dual mode: reconstruct missing links

For a side-by-side bilingual page, the original text is still present. The program:

1. Extracts unique **three-word sequences** as content features.
2. Compares their **two-dimensional coordinates** on the source and translated pages.
3. Estimates and validates a geometric transformation for each page.
4. Rebuilds each link's clickable rectangle on the **original-language half**.
5. Reconstructs URL actions and internal PDF jumps.

The translated-language half is not given new clickable rectangles.

If the original-language text cannot be matched reliably, the program stops rather than estimating link positions from word or character counts.

### Mono mode: retain existing links and fix destinations

A monolingual translation may change word lengths, line breaks, and paragraph positions. Exact positioning cannot be inferred from the source text alone.

The program checks **existing link identities, their rectangles, page dimensions, rotation, and non-text drawing counts** against the source. It then preserves link rectangles and external URLs, normalizes internal destinations, and reports source link rectangles that no longer intersect translated text.

**Important:** Mono mode does **not** move clickable rectangles to translated words. Even rectangles that intersect text may now overlap the wrong text. If the translated PDF has removed or changed the original link annotations, this mode stops rather than guessing.

## Validation

Tested against a 483-page Red Hat OpenShift Container Platform 4.21 *Installing on Google Cloud* PDF and its translated outputs:

| Check | Dual | Mono |
| --- | ---: | ---: |
| Pages | 483 | 483 |
| External URLs | 322 / 322 | 322 / 322 |
| Internal GoTo links | 612 / 612 | 612 / 612 |
| Total output links | 934 / 934 | 934 / 934 |
| PDF bookmarks | 402 | 402 |
| Pages with unchanged content streams | 483 / 483 | 483 / 483 |
| Identical rendered sample pages | 11 / 11 | 11 / 11 |

In this particular source PDF, **61** invalid named destinations were repaired using outline features and **7** numbered callout destinations could only be approximated to their declared pages. The mono output had **112** link rectangles with no translated text underneath (92 in the table of contents and 20 in the body).

These are results for this test document, **not a general success-rate guarantee**.

## Limitations

- Source and translated files must have the **same number of pages**.
- Dual mode currently handles **side-by-side** pages with a recognisable original-language half. It does not handle arbitrary reflow or interleaved original/translated pages.
- Mono mode requires the translation to retain source-compatible link annotations.
- Mono link targets may be repaired while the visible clickable region remains inaccurate.
- Complex rotation, nonuniform re-layout, missing source text, or image-only PDFs may be rejected.
- Some malformed source destinations may only be recoverable approximately.
- Restored link counts alone do not prove that every clickable region matches its intended visible label.

Check a few external links, the table of contents, and cross-chapter jumps in a PDF viewer after repair.

## Reports

- **Dual:** `<output-stem>.link-repairs.txt` if malformed source destinations required recovery.
- **Mono:** `<output-stem>.link-report.txt` containing validation counts and link-coverage warnings.

## Repository contents

```text
pdf2zh-link-repair/
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
└── pdf2zh-link-repair.py
```

## License

The code in this repository is licensed under the [MIT License](LICENSE).

PyMuPDF is a separate dependency with its own licensing terms; consult the [PyMuPDF licensing documentation](https://pymupdf.readthedocs.io/en/latest/about.html#license) for details.

## Disclaimer

Independent utility; not an official component of pdf2zh, pdf2zh_next, or Red Hat. Do not commit private PDFs, API tokens, or credentials to a public repository.