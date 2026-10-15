const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const appScript = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

class FakeElement {
    constructor(id) {
        this.id = id;
        this.value = '';
        this.textContent = '';
        this.innerText = '';
        this.disabled = false;
        this.checked = false;
        this.style = {};
        this.listeners = new Map();
        this.attributes = {};
        this.tabIndex = 0;
        this.files = [];
        this.className = '';
        this.classes = new Set();
        this.classList = {
            toggle: (name, enabled) => {
                if (enabled) this.classes.add(name);
                else this.classes.delete(name);
                return enabled;
            },
            add: (name) => this.classes.add(name),
            remove: (name) => this.classes.delete(name),
            contains: (name) => this.classes.has(name)
        };
    }

    addEventListener(name, handler) {
        if (!this.listeners.has(name)) this.listeners.set(name, []);
        this.listeners.get(name).push(handler);
    }

    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}

    async fire(name, extra = {}) {
        const event = {
            key: '',
            preventDefault() {},
            ...extra
        };
        const handlers = this.listeners.get(name) || [];
        await Promise.all(handlers.map((handler) => handler(event)));
    }

    async click() { await this.fire('click'); }
}

function jsonResponse(data, ok = true) {
    return { ok, json: async () => data };
}

function createUI(fetchHandler) {
    const ids = [
        'url-input', 'clear-btn', 'depth-select', 'crawl-btn', 'drop-zone', 'file-input',
        'status', 'status-text', 'loading-spinner', 'output-preview', 'output-stats',
        'output-notes', 'draft-count', 'copy-btn', 'save-btn', 'btn-raw', 'btn-cleaned',
        'btn-context', 'profile-select', 'trim-body', 'question-input', 'max-tokens',
        'mode-select', 'tokenizer-select', 'context-window', 'reserved-tokens',
        'answer-tokens', 'compression-select', 'compression-rate', 'use-browser', 'allow-remote'
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
    Object.assign(elements['url-input'], { value: '', disabled: false });
    Object.assign(elements['depth-select'], { value: '1' });
    Object.assign(elements['max-tokens'], { value: '8000' });
    Object.assign(elements['mode-select'], { value: 'faithful' });
    Object.assign(elements['tokenizer-select'], { value: 'cl100k_base' });
    Object.assign(elements['context-window'], { value: '' });
    Object.assign(elements['reserved-tokens'], { value: '0' });
    Object.assign(elements['answer-tokens'], { value: '0' });
    Object.assign(elements['compression-select'], { value: 'none' });
    Object.assign(elements['compression-rate'], { value: '0.5' });
    Object.assign(elements['profile-select'], { value: 'document' });
    elements['output-preview'].value = 'No content loaded yet...';
    elements['output-preview'].disabled = true;

    const sandbox = {
        document: { getElementById: (id) => elements[id] },
        fetch: fetchHandler,
        navigator: { clipboard: { writeText: async () => {} } },
        URL,
        FormData: class FormData {} ,
        setInterval: () => 1,
        clearInterval: () => {},
        setTimeout: (callback) => { queueMicrotask(callback); return 1; },
        clearTimeout: () => {},
        console
    };
    vm.runInNewContext(appScript, sandbox, { filename: 'static/app.js' });
    return { elements, sandbox };
}

const successfulProcess = {
    status: 'success',
    raw: 'Raw draft',
    cleaned: 'Cleaned draft',
    content: 'Context draft',
    stats: { raw_tokens: 10, cleaned_tokens: 8, output_tokens: 5, budget_tokens: 100, tokenizer: 'cl100k_base', count_kind: 'tokens' },
    selected: [{ id: 'block-1' }],
    omitted: [],
    warnings: []
};

async function settle() {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
}

async function processUrl(ui) {
    ui.elements['url-input'].value = 'https://example.test/article';
    await ui.elements['crawl-btn'].click();
    await settle();
}

test('raw, cleaned, and context drafts keep separate edits across tab switches', async () => {
    const ui = createUI(async (url) => {
        if (url === '/process') return jsonResponse(successfulProcess);
        if (url === '/count') return jsonResponse({ status: 'success', count: 4, count_kind: 'tokens', tokenizer: 'cl100k_base' });
        throw new Error(`Unexpected request: ${url}`);
    });
    await processUrl(ui);
    assert.equal(ui.elements['output-preview'].value, 'Context draft');

    ui.elements['output-preview'].value = 'Context edit';
    await ui.elements['output-preview'].fire('input');
    await ui.elements['btn-raw'].click();
    assert.equal(ui.elements['output-preview'].value, 'Raw draft');
    ui.elements['output-preview'].value = 'Raw edit';
    await ui.elements['output-preview'].fire('input');
    await ui.elements['btn-cleaned'].click();
    assert.equal(ui.elements['output-preview'].value, 'Cleaned draft');
    await ui.elements['btn-context'].click();
    assert.equal(ui.elements['output-preview'].value, 'Context edit');
    await ui.elements['btn-raw'].click();
    assert.equal(ui.elements['output-preview'].value, 'Raw edit');
});

test('processing disables source and option controls until the response returns', async () => {
    let finishRequest;
    const ui = createUI((url) => {
        if (url === '/process') return new Promise((resolve) => { finishRequest = resolve; });
        return Promise.resolve(jsonResponse({ status: 'success', count: 0, count_kind: 'tokens', tokenizer: 'cl100k_base' }));
    });
    ui.elements['url-input'].value = 'https://example.test/article';
    await ui.elements['crawl-btn'].click();
    assert.equal(ui.elements['crawl-btn'].disabled, true);
    assert.equal(ui.elements['profile-select'].disabled, true);
    assert.equal(ui.elements['file-input'].disabled, true);

    finishRequest(jsonResponse(successfulProcess));
    await settle();
    assert.equal(ui.elements['crawl-btn'].disabled, false);
    assert.equal(ui.elements['profile-select'].disabled, false);
});

test('byte-bound context stats and edited drafts never claim token counts', async () => {
    const byteBoundResult = {
        ...successfulProcess,
        stats: {
            raw_tokens: 20,
            cleaned_tokens: 12,
            output_tokens: 5,
            budget_tokens: 100,
            tokenizer: 'utf8_bytes',
            count_kind: 'utf8_bytes_bound'
        }
    };
    const ui = createUI(async (url) => {
        if (url === '/process') return jsonResponse(byteBoundResult);
        if (url === '/count') return jsonResponse({
            status: 'success', count: 12, count_kind: 'utf8_bytes_bound', tokenizer: 'utf8_bytes'
        });
        throw new Error(`Unexpected request: ${url}`);
    });
    await processUrl(ui);
    await settle();

    assert.match(ui.elements['output-stats'].textContent, /5 \/ 100 UTF-8 bytes/);
    assert.doesNotMatch(ui.elements['output-stats'].textContent, /\btokens?\b/);
    assert.match(ui.elements['draft-count'].textContent, /12 UTF-8 bytes \(byte bound; not model tokens\)/);
});

test('backend save failures are shown to the user', async () => {
    const ui = createUI(async (url) => {
        if (url === '/process') return jsonResponse(successfulProcess);
        if (url === '/count') return jsonResponse({ status: 'success', count: 4, count_kind: 'tokens', tokenizer: 'cl100k_base' });
        if (url === '/download') return jsonResponse({ status: 'error', message: 'The save folder is unavailable.' }, false);
        throw new Error(`Unexpected request: ${url}`);
    });
    await processUrl(ui);
    await ui.elements['save-btn'].click();
    assert.match(ui.elements['status-text'].textContent, /Save error: The save folder is unavailable/);
    assert.equal(ui.elements['status'].className, 'error');
});

test('clipboard rejection is reported and never claims a copy succeeded', async () => {
    const ui = createUI(async (url) => {
        if (url === '/process') return jsonResponse(successfulProcess);
        if (url === '/count') return jsonResponse({ status: 'success', count: 4, count_kind: 'tokens', tokenizer: 'cl100k_base' });
        throw new Error(`Unexpected request: ${url}`);
    });
    ui.sandbox.navigator.clipboard.writeText = async () => { throw new Error('permission denied'); };
    await processUrl(ui);
    await ui.elements['copy-btn'].click();
    assert.match(ui.elements['status-text'].textContent, /Clipboard error: permission denied/);
    assert.doesNotMatch(ui.elements['status-text'].textContent, /Copied/);
});

test('failed processing preserves the previous useful draft', async () => {
    let processCount = 0;
    const ui = createUI(async (url) => {
        if (url === '/process') {
            processCount += 1;
            return processCount === 1
                ? jsonResponse(successfulProcess)
                : jsonResponse({ status: 'error', message: 'Unsupported file type' }, false);
        }
        if (url === '/count') return jsonResponse({ status: 'success', count: 4, count_kind: 'tokens', tokenizer: 'cl100k_base' });
        throw new Error(`Unexpected request: ${url}`);
    });
    await processUrl(ui);
    ui.elements['url-input'].value = 'https://example.test/second';
    await ui.elements['crawl-btn'].click();
    await settle();
    assert.equal(ui.elements['output-preview'].value, 'Context draft');
    assert.match(ui.elements['status-text'].textContent, /Unsupported file type/);
});
