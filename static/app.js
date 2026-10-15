const urlInput = document.getElementById('url-input');
const clearBtn = document.getElementById('clear-btn');
const depthSelect = document.getElementById('depth-select');
const crawlBtn = document.getElementById('crawl-btn');
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const statusDiv = document.getElementById('status');
const statusText = document.getElementById('status-text');
const loadingSpinner = document.getElementById('loading-spinner');
const outputPreview = document.getElementById('output-preview');
const outputStats = document.getElementById('output-stats');
const outputNotes = document.getElementById('output-notes');
const draftCount = document.getElementById('draft-count');
const copyBtn = document.getElementById('copy-btn');
const saveBtn = document.getElementById('save-btn');
const btnRaw = document.getElementById('btn-raw');
const btnCleaned = document.getElementById('btn-cleaned');
const btnContext = document.getElementById('btn-context');
const profileSelect = document.getElementById('profile-select');
const trimBody = document.getElementById('trim-body');
const questionInput = document.getElementById('question-input');
const maxTokens = document.getElementById('max-tokens');
const modeSelect = document.getElementById('mode-select');
const tokenizerSelect = document.getElementById('tokenizer-select');
const contextWindow = document.getElementById('context-window');
const reservedTokens = document.getElementById('reserved-tokens');
const answerTokens = document.getElementById('answer-tokens');
const compressionSelect = document.getElementById('compression-select');
const compressionRate = document.getElementById('compression-rate');
const useBrowser = document.getElementById('use-browser');
const allowRemote = document.getElementById('allow-remote');

const drafts = { raw: '', cleaned: '', context: '' };
let activeView = 'context';
let currentSourceName = '';
let busy = false;
let requestSequence = 0;
let countSequence = 0;
let progressTimer = null;
let progressRequestPending = false;
let countTimer = null;

const processingControls = [
    urlInput, clearBtn, depthSelect, crawlBtn, fileInput,
    profileSelect, trimBody, questionInput, maxTokens, modeSelect,
    tokenizerSelect, contextWindow, reservedTokens, answerTokens,
    compressionSelect, compressionRate, useBrowser, allowRemote
];

function showStatus(message, isError = false, isLoading = false, isWarning = false) {
    statusText.textContent = message;
    statusDiv.className = isError ? 'error' : isWarning ? 'warning' : 'success';
    loadingSpinner.style.display = isLoading ? 'block' : 'none';
}

function setBusy(value) {
    busy = value;
    processingControls.forEach((control) => { control.disabled = value; });
    dropZone.setAttribute('aria-disabled', value ? 'true' : 'false');
    dropZone.tabIndex = value ? -1 : 0;
    dropZone.classList.toggle('drag-over', false);
}

function syncCurrentDraft() {
    if (activeView) drafts[activeView] = outputPreview.value;
}

function setActiveView(view) {
    if (!Object.prototype.hasOwnProperty.call(drafts, view)) return;
    syncCurrentDraft();
    activeView = view;
    outputPreview.value = drafts[view];
    outputPreview.disabled = !currentSourceName;
    btnRaw.classList.toggle('active', view === 'raw');
    btnCleaned.classList.toggle('active', view === 'cleaned');
    btnContext.classList.toggle('active', view === 'context');
    queueDraftCount();
}

function setDrafts(raw, cleaned, context) {
    drafts.raw = raw;
    drafts.cleaned = cleaned;
    drafts.context = context;
    currentSourceName = currentSourceName || 'source';
    outputPreview.disabled = false;
    copyBtn.disabled = false;
    saveBtn.disabled = false;
    activeView = 'context';
    outputPreview.value = drafts.context;
    btnRaw.classList.toggle('active', false);
    btnCleaned.classList.toggle('active', false);
    btnContext.classList.toggle('active', true);
    queueDraftCount();
}

function niceWarning(value) {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') return String(value.message || value.reason || 'A processing note is available.');
    return 'A processing note is available.';
}

function isByteCount(countKind, tokenizer) {
    return tokenizer === 'utf8_bytes' || countKind === 'utf8_bytes' || countKind === 'utf8_bytes_bound';
}

function updateResultSummary(result) {
    const stats = result.stats || {};
    const selected = Array.isArray(result.selected) ? result.selected.length : 0;
    const omitted = Array.isArray(result.omitted) ? result.omitted.length : 0;
    const countLabel = isByteCount(stats.count_kind, stats.tokenizer) ? 'UTF-8 bytes' : 'tokens';
    const values = [];
    if (Number.isFinite(stats.output_tokens) && Number.isFinite(stats.budget_tokens)) {
        values.push(`Generated context: ${stats.output_tokens} / ${stats.budget_tokens} ${countLabel}`);
    }
    if (stats.tokenizer) values.push(String(stats.tokenizer));
    if (selected || omitted) values.push(`${selected} selected, ${omitted} omitted`);
    if (Number.isFinite(stats.raw_tokens) && Number.isFinite(stats.cleaned_tokens)) {
        values.push(`Raw ${stats.raw_tokens}; cleaned ${stats.cleaned_tokens}`);
    }
    outputStats.textContent = values.join(' · ');

    const warningList = Array.isArray(result.warnings) ? result.warnings : [];
    outputNotes.textContent = warningList.map(niceWarning).join('\n');
}

function isPaywalled(text) {
    const clean = text.replace(/\s+/g, ' ').trim();
    if (clean.length < 200) return true;
    const hardGate = /sign in to read|sign up to read|members only|subscription required|subscribe to continue|unlock this article/i.test(clean);
    return hardGate && clean.length < 1000;
}

function queueDraftCount() {
    const sequence = ++countSequence;
    if (countTimer !== null) clearTimeout(countTimer);
    draftCount.textContent = 'Counting current draft…';
    countTimer = setTimeout(() => { refreshDraftCount(sequence); }, 250);
}

async function refreshDraftCount(sequence) {
    const text = outputPreview.value;
    const tokenizer = tokenizerSelect.value;
    try {
        const response = await fetch('/count', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, tokenizer })
        });
        const result = await response.json();
        if (sequence !== countSequence || text !== outputPreview.value || tokenizer !== tokenizerSelect.value) return;
        if (!response.ok || result.status !== 'success') {
            draftCount.textContent = `Draft count unavailable: ${result.message || 'counting failed.'}`;
            return;
        }
        if (isByteCount(result.count_kind, result.tokenizer)) {
            draftCount.textContent = `Current draft: ${result.count} UTF-8 bytes (byte bound; not model tokens)`;
        } else {
            draftCount.textContent = `Current draft: ${result.count} tokens (${result.tokenizer})`;
        }
    } catch (error) {
        if (sequence === countSequence) draftCount.textContent = `Draft count unavailable: ${error.message}`;
    }
}

function readInteger(control, label, allowBlank = false) {
    if (allowBlank && control.value.trim() === '') return null;
    const value = Number(control.value);
    if (!Number.isSafeInteger(value)) throw new Error(`${label} must be a whole number.`);
    return value;
}

function buildOptions(isUrl) {
    const options = {
        depth: readInteger(depthSelect, 'Depth'),
        profile: profileSelect.value,
        trim_body: trimBody.checked,
        allow_remote: isUrl && allowRemote.checked,
        use_browser: isUrl && useBrowser.checked,
        max_tokens: readInteger(maxTokens, 'Context budget'),
        tokenizer: tokenizerSelect.value,
        mode: modeSelect.value,
        query: questionInput.value,
        context_window: readInteger(contextWindow, 'Model context window', true),
        reserved_tokens: readInteger(reservedTokens, 'Prompt reserve'),
        answer_tokens: readInteger(answerTokens, 'Answer reserve'),
        compression: compressionSelect.value,
        compression_rate: Number(compressionRate.value)
    };
    if (options.depth < 1 || options.depth > 2) throw new Error('Depth must be 1 or 2.');
    if (options.max_tokens < 1) throw new Error('Context budget must be greater than zero.');
    if (options.context_window !== null && options.context_window < 1) throw new Error('Model context window must be greater than zero.');
    if (options.reserved_tokens < 0 || options.answer_tokens < 0) throw new Error('Token reserves cannot be negative.');
    if (!Number.isFinite(options.compression_rate) || options.compression_rate <= 0 || options.compression_rate > 1) {
        throw new Error('Compression rate must be greater than 0 and at most 1.');
    }
    return options;
}

function startProgressPolling(sequence) {
    stopProgressPolling();
    progressRequestPending = false;
    progressTimer = setInterval(async () => {
        if (sequence !== requestSequence || progressRequestPending) return;
        progressRequestPending = true;
        try {
            const response = await fetch('/progress');
            const progress = await response.json();
            if (sequence !== requestSequence || !busy) return;
            if (progress.status === 'processing' && progress.pages_queued > 0) {
                const queued = Math.min(progress.pages_queued, 25);
                statusText.textContent = `Processing website… ${progress.pages_done} / ${queued} page${queued === 1 ? '' : 's'}`;
            }
        } catch {
            // The processing request remains authoritative if progress polling fails.
        } finally {
            progressRequestPending = false;
        }
    }, 900);
}

function stopProgressPolling() {
    if (progressTimer !== null) {
        clearInterval(progressTimer);
        progressTimer = null;
    }
    progressRequestPending = false;
}

function updateUIOnSuccess(result, sourceName) {
    currentSourceName = sourceName;
    setDrafts(result.raw, result.cleaned, result.content);
    updateResultSummary(result);
    copyBtn.disabled = false;
    saveBtn.disabled = false;
}

async function handleProcessing(inputData, isUrl) {
    if (busy) return;
    let options;
    try {
        options = buildOptions(isUrl);
    } catch (error) {
        showStatus(error.message, true);
        return;
    }

    const sequence = ++requestSequence;
    const sourceName = isUrl ? inputData : inputData.name;
    setBusy(true);
    showStatus('Processing… please wait.', false, true);

    const fetchOptions = { method: 'POST' };
    if (isUrl) {
        fetchOptions.headers = { 'Content-Type': 'application/json' };
        fetchOptions.body = JSON.stringify({ source: inputData, is_url: true, options });
        startProgressPolling(sequence);
    } else {
        const formData = new FormData();
        formData.append('file', inputData);
        formData.append('options', JSON.stringify(options));
        fetchOptions.body = formData;
    }

    try {
        const response = await fetch('/process', fetchOptions);
        const result = await response.json();
        if (sequence !== requestSequence) return;
        if (!response.ok || result.status !== 'success') {
            showStatus(`Error: ${result.message || 'The source could not be processed.'}`, true);
            return;
        }
        if (typeof result.raw !== 'string' || typeof result.cleaned !== 'string' || typeof result.content !== 'string' || !result.content.trim()) {
            showStatus('Error: The source did not produce readable context.', true);
            return;
        }
        updateUIOnSuccess(result, sourceName);
        const warningList = Array.isArray(result.warnings) ? result.warnings : [];
        if (isUrl && isPaywalled(result.cleaned)) {
            showStatus('This page may be paywalled. Try saving the page as HTML and dropping the saved file into Gobbler.', false, false, true);
        } else if (warningList.length) {
            showStatus(`Processing complete with ${warningList.length} note${warningList.length === 1 ? '' : 's'}.`, false, false, true);
        } else {
            showStatus('Extraction and context preparation complete.');
        }
    } catch (error) {
        if (sequence === requestSequence) showStatus(`Connection error: ${error.message}`, true);
    } finally {
        if (sequence === requestSequence) {
            stopProgressPolling();
            setBusy(false);
        }
    }
}

function safeFileBase(value) {
    return String(value || 'context')
        .replace(/\.md$/i, '')
        .replace(/[^a-z0-9._-]+/gi, '-')
        .replace(/^[-.]+|[-.]+$/g, '')
        .slice(0, 100) || 'context';
}

function buildFileName(source) {
    try {
        const url = new URL(source);
        const domain = url.hostname.replace(/^www\./, '').split('.')[0];
        const subject = url.pathname.split('/').filter(Boolean).pop()?.replace(/\.[^.]+$/, '');
        return `${safeFileBase(`${domain}-${subject || 'page'}`)}.md`;
    } catch {
        return `${safeFileBase(source)}.md`;
    }
}

async function triggerSave() {
    if (!currentSourceName || outputPreview.disabled) return;
    try {
        const response = await fetch('/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: buildFileName(currentSourceName), content: outputPreview.value })
        });
        const result = await response.json();
        if (!response.ok || result.status !== 'success') {
            showStatus(`Save error: ${result.message || 'The file could not be saved.'}`, true);
            return;
        }
        showStatus(`Saved to: ${result.path || result.filename}`);
    } catch (error) {
        showStatus(`Save error: ${error.message}`, true);
    }
}

async function copyCurrentDraft() {
    if (outputPreview.disabled) return;
    try {
        await navigator.clipboard.writeText(outputPreview.value);
        showStatus('Current draft copied to the clipboard.');
    } catch (error) {
        showStatus(`Clipboard error: ${error.message || 'the browser did not allow copying.'}`, true);
    }
}

btnRaw.addEventListener('click', () => setActiveView('raw'));
btnCleaned.addEventListener('click', () => setActiveView('cleaned'));
btnContext.addEventListener('click', () => setActiveView('context'));
outputPreview.addEventListener('input', () => {
    syncCurrentDraft();
    queueDraftCount();
});
tokenizerSelect.addEventListener('change', queueDraftCount);
copyBtn.addEventListener('click', copyCurrentDraft);
saveBtn.addEventListener('click', triggerSave);
clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    urlInput.focus();
});
crawlBtn.addEventListener('click', () => {
    const url = urlInput.value.trim();
    if (url) handleProcessing(url, true);
    else showStatus('Enter a website URL first.', true);
});
urlInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !busy) {
        event.preventDefault();
        crawlBtn.click();
    }
});

dropZone.addEventListener('click', () => {
    if (!busy) fileInput.click();
});
dropZone.addEventListener('keydown', (event) => {
    if (!busy && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        fileInput.click();
    }
});
fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) handleProcessing(file, false);
    fileInput.value = '';
});
dropZone.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (!busy) dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropZone.classList.remove('drag-over');
    if (busy) return;
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleProcessing(file, false);
});

setBusy(false);
