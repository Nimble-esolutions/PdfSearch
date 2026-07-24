const chatMain = document.getElementById('chatMain');
const sendBtn = document.getElementById('sendBtn');
const userQuery = document.getElementById('userQuery');
const wordCounter = document.getElementById("wordCounter");
const viewPdfUrlTemplate = window.PdfSearch.viewPdfUrlTemplate;
const welcomePromptLabel = JSON.parse(document.getElementById("welcome-prompt-label").textContent);
const welcomePrompts = JSON.parse(document.getElementById("welcome-prompts").textContent);
const searchLoadingStages = JSON.parse(document.getElementById("search-loading-stages").textContent);
const retryLabel = JSON.parse(document.getElementById("retry-label").textContent);
const sourceDocumentsLabel = JSON.parse(document.getElementById("source-documents-label").textContent);
const searchMessages = JSON.parse(document.getElementById("search-messages").textContent);
const searchComposer = document.getElementById("searchComposer");
const aboutDialog = document.getElementById("aboutDialog");
const aboutOpen = document.querySelector("[data-about-open]");
const aboutClose = document.querySelector("[data-about-close]");
let aboutReturnFocus = null;
let requestPending = false;
let activeController = null;
let requestTimeout = null;
let stageTimer = null;

window.addEventListener("DOMContentLoaded", function(){
    const welcome = JSON.parse(document.getElementById("welcome-message").textContent);
    const helpUrl = JSON.parse(document.getElementById("welcome-help-url").textContent);
    const helpLabel = JSON.parse(document.getElementById("welcome-help-label").textContent);
    if(welcome) appendWelcomeMessage(welcome, helpUrl, helpLabel);
});

searchComposer.addEventListener("submit", event => {
    event.preventDefault();
    sendMessage();
});

function closeAbout() {
    if (!aboutDialog) return;
    if (typeof aboutDialog.close === "function") {
        aboutDialog.close();
    } else {
        aboutDialog.removeAttribute("open");
    }
    if (aboutReturnFocus) aboutReturnFocus.focus();
}

aboutOpen?.addEventListener("click", () => {
    aboutReturnFocus = aboutOpen;
    if (typeof aboutDialog.showModal === "function") {
        aboutDialog.showModal();
    } else {
        aboutDialog.setAttribute("open", "");
    }
    aboutClose?.focus();
});
aboutClose?.addEventListener("click", closeAbout);
aboutDialog?.addEventListener("cancel", event => {
    event.preventDefault();
    closeAbout();
});
aboutDialog?.addEventListener("click", event => {
    if (event.target === aboutDialog) closeAbout();
});

async function sendMessage(){
    if(requestPending) return;
    const query = userQuery.value.trim();
    if(!query) return;

    const wordCount = query.split(/\s+/).length;
    if(wordCount > 30){
        appendErrorMessage(searchMessages.question_too_long, searchMessages.question_too_long_detail, query);
        return;
    }

    appendMessage(query, 'user');
    userQuery.value = '';
    sendBtn.disabled = true;
    requestPending = true;
    sendBtn.setAttribute("aria-busy", "true");
    chatMain.setAttribute("aria-busy", "true");

    const typingDiv = appendMessage('', 'gpt', true);
    typingDiv.classList.add('typing');
    typingDiv.classList.add('search-loading');
    typingDiv.setAttribute("role", "status");
    typingDiv.setAttribute("aria-live", "polite");

    const stages = searchLoadingStages;
    let stageIndex = 0;

    function showStage() {
        typingDiv.replaceChildren();
        const stage = document.createElement("span");
        stage.className = "search-loading__label";
        stage.textContent = stages[stageIndex];
        [0, 1, 2].forEach(index => {
            const dot = document.createElement("span");
            dot.className = "search-loading__dot";
            dot.style.animationDelay = `${index * 0.18}s`;
            typingDiv.appendChild(dot);
        });
        typingDiv.prepend(stage);
        stageIndex++;
        if(stageIndex < stages.length) {
            stageTimer = setTimeout(showStage, 2000);
        }
    }
    showStage();

    const formData = new FormData();
    formData.append('query', query);
    formData.append('language', document.documentElement.lang === 'mr' ? 'mr' : 'en');
    activeController = new AbortController();
    requestTimeout = setTimeout(() => activeController.abort(), 30000);

    try {
        const response = await fetch(window.PdfSearch.searchQueryUrl, {
            method: "POST",
            headers: {'X-CSRFToken': window.PdfSearch.csrfToken},
            body: formData,
            credentials: "same-origin",
            signal: activeController.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            typingDiv.remove();
            const errorMessages = {
                401: searchMessages.sign_in,
                403: searchMessages.security,
                400: searchMessages.limit,
                500: searchMessages.request_failed,
                503: searchMessages.unavailable,
                429: searchMessages.rate_limited,
            };
            appendErrorMessage(
                errorMessages[response.status] || searchMessages.request_failed,
                data.detail,
                query,
            );
            return;
        }
        typingDiv.remove();
        if(data.answer){
            typeEffect(data.answer, data.references || []);
        } else if(data.error){
            appendErrorMessage(searchMessages.search_unavailable, searchMessages.try_later, query);
        }
    } catch(err) {
        typingDiv.remove();
        if (err.name === "AbortError") {
            appendErrorMessage(searchMessages.timeout, searchMessages.try_again, query);
        } else {
            appendErrorMessage(searchMessages.unexpected, searchMessages.try_later, query);
            console.error(err);
        }
    } finally {
        clearTimeout(requestTimeout);
        clearTimeout(stageTimer);
        requestTimeout = null;
        stageTimer = null;
        activeController = null;
        requestPending = false;
        sendBtn.removeAttribute("aria-busy");
        chatMain.setAttribute("aria-busy", "false");
        updateSendButton();
    }
}

function appendMessage(text, sender, isLoading=false){
    const div = document.createElement('div');
    div.className = sender === 'user' ? 'user-msg' : 'gpt-msg';

    if (isLoading) {
        div.textContent = text;
    } else {
        text.split("\n").forEach((line, idx) => {
            if (idx > 0) div.appendChild(document.createElement("br"));
            div.appendChild(document.createTextNode(line));
        });
    }

    chatMain.appendChild(div);
    chatMain.scrollTop = chatMain.scrollHeight;
    if(isLoading) return div;
    return div;
}

function appendWelcomeMessage(text, href, label) {
    const div = appendMessage('', 'gpt');
    div.classList.add('gpt-msg--welcome');
    div.appendChild(document.createTextNode(text + ' '));
    const link = document.createElement('a');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = label;
    div.appendChild(link);

    const promptLabel = document.createElement('span');
    promptLabel.className = 'welcome-prompts__label';
    promptLabel.textContent = welcomePromptLabel;
    div.appendChild(promptLabel);

    const promptWrap = document.createElement('div');
    promptWrap.className = 'prompt-chips';
    welcomePrompts.forEach(prompt => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'prompt-chip';
        button.textContent = prompt;
        button.addEventListener('click', () => {
            userQuery.value = prompt;
            userQuery.dispatchEvent(new Event('input', {bubbles: true}));
            userQuery.focus();
        });
        promptWrap.appendChild(button);
    });
    div.appendChild(promptWrap);
}

function appendErrorMessage(title, detail, retryQuery) {
    const box = document.createElement('div');
    box.className = 'search-error';

    const content = document.createElement('div');
    const heading = document.createElement('strong');
    heading.textContent = title;
    const message = document.createElement('p');
    message.textContent = detail || '';
    content.append(heading, message);

    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'search-error__retry';
    retry.textContent = retryLabel;
    retry.addEventListener('click', () => {
        userQuery.value = retryQuery || '';
        userQuery.dispatchEvent(new Event('input', {bubbles: true}));
        sendMessage();
    });
    content.appendChild(retry);
    box.appendChild(content);
    chatMain.appendChild(box);
    chatMain.scrollTop = chatMain.scrollHeight;
    return box;
}


function typeEffect(text, references = []) {
    const div = document.createElement('div');
    div.className = 'gpt-msg';
    chatMain.appendChild(div);

    let cursor = document.createElement("span");
    cursor.className = "cursor";
    div.appendChild(cursor);

    const segmenter = Intl.Segmenter
        ? new Intl.Segmenter('mr', { granularity: 'grapheme' })
        : null;
    const graphemes = segmenter
        ? [...segmenter.segment(text)].map(seg => seg.segment)
        : [...text];

    let i = 0;
    function typing() {
        if (i < graphemes.length) {
            if (graphemes[i] === "\n") {
                cursor.before(document.createElement("br"));
            } else {
                cursor.before(document.createTextNode(graphemes[i]));
            }
            chatMain.scrollTop = chatMain.scrollHeight;
            i++;
            const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            setTimeout(typing, reduceMotion ? 0 : 8);
        } else {
            cursor.remove();

            if (references.length > 0) {
                const refDiv = document.createElement('div');
                refDiv.className = "references";
                const heading = document.createElement("strong");
                heading.textContent = sourceDocumentsLabel;
                refDiv.appendChild(heading);
                references.forEach(ref => {
                    const card = document.createElement('div');
                    card.className = "ref-card";
                    const link = document.createElement("a");
                    link.href = safeReferenceUrl(ref);
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    link.textContent = ref.title || "Document";
                    card.appendChild(link);
                    const meta = document.createElement("small");
                    meta.textContent = [ref.folder, ref.uploaded_at]
                        .filter(Boolean)
                        .join(" • ");
                    if (meta.textContent) card.appendChild(meta);
                    refDiv.appendChild(card);
                });
                div.appendChild(refDiv);
            }
        }
    }
    typing();
}


function protectedPdfUrl(pdfId) {
    if (!pdfId) return "#";
    return viewPdfUrlTemplate.replace("/0/", `/${encodeURIComponent(pdfId)}/`);
}

function safeReferenceUrl(ref) {
    if (typeof ref.url === "string") {
        try {
            const url = new URL(ref.url, window.location.origin);
            if (url.origin === window.location.origin && url.pathname.startsWith("/pdf/")) {
                return `${url.pathname}${url.search}`;
            }
        } catch (_) {
        }
    }
    return protectedPdfUrl(ref.pdf_id);
}

function updateSendButton() {
    const count = userQuery.value.trim().split(/\s+/).filter(w => w.length > 0).length;
    sendBtn.disabled = requestPending || count === 0 || count > 30;
}

userQuery.addEventListener("input", function () {
    const words = this.value.trim().split(/\s+/).filter(w => w.length > 0);
    const count = words.length;

    wordCounter.textContent = `${count}/30`;

    if (count > 30) {
        wordCounter.className = "word-counter word-counter--over";
        updateSendButton();
    } else if (count >= 25) {
        wordCounter.className = "word-counter word-counter--warn";
        updateSendButton();
    } else {
        wordCounter.className = "word-counter word-counter--ok";
        updateSendButton();
    }
});

document.querySelectorAll('form[action="' + window.PdfSearch.setLanguageUrl + '"]').forEach(form => {
    form.addEventListener('submit', () => {
        const langInput = form.querySelector('input[name="language"]');
        if(langInput) document.documentElement.lang = langInput.value;
    });
});
