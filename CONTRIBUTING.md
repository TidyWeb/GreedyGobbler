# Contributing to Greedy Gobbler

Thanks for your interest. Greedy Gobbler is a small personal tool — contributions are welcome but kept simple.

## Reporting bugs

Open a GitHub issue and include:
- What you did (URL submitted, file type dropped, etc.)
- What you expected to happen
- What actually happened (paste the status bar message if there is one)

## Submitting a PR

1. Fork the repo and create a branch from `main`
2. Keep changes focused — one fix or feature per PR
3. Test your change against at least one real file or URL before submitting
4. Describe what you changed and why in the PR description

No formal style guide. Just match the existing code's tone and simplicity.

## Known limitations

**ODT files are not supported.** MarkItDown has no ODT converter, and there is no plan to add a custom one. Please don't open issues or PRs for ODT support.

Some ebook formats require external tools: MOBI/AZW/FB2/LRF/HTMLZ need Calibre's `ebook-convert`, and DjVu files need `djvutxt`. If reporting one of those paths, include whether the relevant tool is installed.
