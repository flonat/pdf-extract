# Test Fixtures

PDFs in this directory are excluded from version control (see `.gitignore`).

To run the full test suite, drop a sample PDF here:

```bash
cp /path/to/some/academic-paper.pdf tests/fixtures/sample_paper.pdf
```

Tests that depend on `sample_paper.pdf` are skipped if the file is missing.
