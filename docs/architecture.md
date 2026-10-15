# Architecture

Greedy Gobbler has four local stages: extraction, conservative clean-up, evidence selection and export. The Flask application connects those stages to the existing vanilla JavaScript page.

## Extraction

`converter.extract_input()` is the public extraction entry point. It returns faithful Markdown, cleaned text, source records, ordered units, warnings, omissions and basic metrics. The legacy `process_input()` wrapper still returns the former `(raw, cleaned)` tuple.

`pipeline.extraction` handles local files. Text-like inputs keep their source wording; HTML and EPUB are converted with the local parser; PDFs are read page by page with `pdfplumber`, with OCR attempted when native text is missing or unusable. An OCR page is a text transcription, not an interpretation of its image. A failed or unreadable page is reported as an omission. Source names and local URIs use basenames rather than local absolute paths.

`pipeline.web_fetch` handles explicit HTTP and HTTPS URLs. Before each request and redirect it resolves the host and rejects loopback, private, reserved and link-local addresses. The regular request path caps response size and page count, and depth-two crawling stays on the requested hostname. Browser rendering is an optional path with its own address checks. Microlink use is a separate opt-in setting. Tests inject a trusted fixture transport and public-address resolver; they do not weaken production address checks.

## Clean-up and context preparation

`pipeline.normaliser.clean(markdown, profile=...)` is deliberately conservative. The document profile normalises repeated blank lines and outer whitespace while protecting code blocks. Web and Reddit profiles may remove exact interface labels in marked blocks at a page edge. It does not paraphrase, summarise, or decide which claims matter.

`pipeline.context.prepare_context(extraction, options)` builds a complete Markdown export within the configured budget. It counts the full wrapper, source references and omission notices. Faithful mode follows source order; balanced mode spreads evidence across sections; focused mode ranks evidence against the supplied query. It keeps code, tables and evidence groups intact, and reports selected and omitted block references in structured metadata. If the minimal export cannot fit, it returns a clear error rather than cutting text mid-sentence.

`pipeline.token_budget` supports exact `cl100k_base` and `o200k_base` counts only when verified tokenizer data is already local. `utf8_bytes` is explicitly a byte-bound count; it is not presented as a model token count. The application does not fetch missing token data automatically.

LLMLingua-2 is optional and experimental. The module is loaded only when requested and accepts a server-configured local model path. Model downloads and arbitrary client-supplied paths are not part of request handling.

## Application boundary

The existing routes remain `/`, `/process`, `/progress`, `/count` and `/download`. `/process` accepts a URL JSON request or one multipart upload and runs extraction and packing synchronously. A lock rejects overlapping processing requests. Uploads receive unique names; exports use exclusive creation and never overwrite an existing file.

The browser keeps raw, cleaned and context drafts separately. Editing one draft does not replace the other two. `/count` counts the currently edited draft using the selected exact tokenizer or the explicit byte bound. `/download` saves the active text as a new Markdown file under the configured save folder.

## Source trust

Documents and pages are untrusted data. Their text is never run as code or treated as application instructions. A Markdown boundary label helps a downstream reader identify source material; it is not a prompt-injection defence. Local files are processed locally and are not sent to a remote service.
