const urlInput = document.getElementById('url-input');
const clearBtn = document.getElementById('clear-btn');
const depthSelect = document.getElementById('depth-select');
const crawlBtn = document.getElementById('crawl-btn');
const dropZone = document.getElementById('drop-zone');
const statusDiv = document.getElementById('status');
const statusText = document.getElementById('status-text');
const loadingSpinner = document.getElementById('loading-spinner');
const outputPreview = document.getElementById('output-preview');
const copyBtn = document.getElementById('copy-btn');
const saveBtn = document.getElementById('save-btn');

let currentContent = "";
let currentRaw = "";
let currentCleaned = "";
let currentSourceName = "";

const btnCleaned = document.getElementById('btn-cleaned');
const btnRaw = document.getElementById('btn-raw');

function showView(view) {
    currentContent = view === 'raw' ? currentRaw : currentCleaned;
    outputPreview.textContent = currentContent;
    btnCleaned.classList.toggle('active', view === 'cleaned');
    btnRaw.classList.toggle('active', view === 'raw');
}

btnCleaned.addEventListener('click', () => showView('cleaned'));
btnRaw.addEventListener('click', () => showView('raw'));
let progressPoller = null;

function startProgressPolling() {
    progressPoller = setInterval(async () => {
        try {
            const r = await fetch('/progress');
            const p = await r.json();
            if (p.pages_done > 0) {
                const queued = Math.min(p.pages_queued, 25);
                statusText.textContent = `Crawling... ${p.pages_done} / ${queued} page${queued !== 1 ? 's' : ''}`;
            }
        } catch {}
    }, 1000);
}

function stopProgressPolling() {
    if (progressPoller) {
        clearInterval(progressPoller);
        progressPoller = null;
    }
}

function showStatus(message, isError = false, isLoading = false, isWarning = false) {
    statusText.textContent = message;
    statusDiv.className = isError ? 'error' : isWarning ? 'warning' : 'success';
    loadingSpinner.style.display = isLoading ? 'block' : 'none';
}

function isPaywalled(text) {
    const clean = text.replace(/\s+/g, ' ').trim();
    if (clean.length < 200) return true;
    // Only warn if a hard gate phrase appears AND content is short (under 1000 chars)
    const hardGate = /sign in to read|sign up to read|members only|subscription required|subscribe to continue|unlock this article/i.test(clean);
    return hardGate && clean.length < 1000;
}

function updateUIOnSuccess(raw, cleaned, sourceName) {
    currentRaw = raw;
    currentCleaned = cleaned;
    currentSourceName = sourceName;
    outputPreview.style.color = "#1e3932";
    copyBtn.disabled = false;
    saveBtn.disabled = false;
    showView('cleaned');
}

async function handleProcessing(inputData, isUrl) {
    showStatus("Processing... please wait.", false, true);
    copyBtn.disabled = true;
    saveBtn.disabled = true;

    let fetchOptions = { method: 'POST' };
    let sourceName = "";

    if (isUrl) {
        sourceName = inputData;
        fetchOptions.headers = { 'Content-Type': 'application/json' };
        const depth = parseInt(depthSelect.value, 10);
        fetchOptions.body = JSON.stringify({ source: inputData, is_url: true, depth: depth });
        startProgressPolling();
    } else {
        sourceName = inputData.name;
        const formData = new FormData();
        formData.append('file', inputData);
        fetchOptions.body = formData;
    }

    try {
        const response = await fetch('/process', { ...fetchOptions });
        stopProgressPolling();
        const result = await response.json();

        if (result.status === 'success') {
            updateUIOnSuccess(result.raw, result.content, sourceName);
            if (isUrl && isPaywalled(result.content)) {
                showStatus("This page may be paywalled. Try saving the page as HTML (Ctrl+S in your browser) and dragging the saved file into Gobbler instead.", false, false, true);
            } else {
                showStatus("Extraction complete.");
            }
        } else {
            showStatus("Error: " + result.message, true);
        }
    } catch (err) {
        stopProgressPolling();
        showStatus("Connection error: " + err.message, true);
    }
}

const domainCounters = {};

function buildFileName(urlStr) {
    try {
        const url = new URL(urlStr);
        const domain = url.hostname.replace(/^www\./, '').split('.')[0];
        const segments = url.pathname.split('/').filter(s => s);
        let subject = segments.length ? segments[segments.length - 1].replace(/\.[^.]+$/, '') : '';
        if (subject) return `${domain}-${subject}.md`;
        domainCounters[domain] = (domainCounters[domain] || 0) + 1;
        return `${domain}-${domainCounters[domain]}.md`;
    } catch {
        return currentSourceName.replace(/[^a-z0-9.]/gi, '_').replace(/\.md$/, '') + '.md';
    }
}

async function triggerSave() {
    let fileName = buildFileName(currentSourceName);

    try {
        const response = await fetch('/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: fileName, content: outputPreview.innerText })
        });
        const result = await response.json();
        if (result.status === 'success') showStatus("Saved to: " + result.path);
    } catch (err) {
        showStatus("Save error: " + err.message, true);
    }
}

copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(outputPreview.innerText);
    copyBtn.textContent = "Copied!";
    setTimeout(() => copyBtn.textContent = "Copy", 2000);
});

saveBtn.addEventListener('click', triggerSave);
clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    urlInput.focus();
});

crawlBtn.addEventListener('click', () => {
    const url = urlInput.value.trim();
    if (url) handleProcessing(url, true);
});

dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.background = '#e9ecef'; });
dropZone.addEventListener('dragleave', () => { dropZone.style.background = 'transparent'; });
dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.style.background = 'transparent';
    const file = e.dataTransfer.files[0];
    if (file) handleProcessing(file, false);
});
