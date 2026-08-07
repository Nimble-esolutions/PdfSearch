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
const workbenchCopy = JSON.parse(document.getElementById("workbench-copy").textContent);
const searchComposer = document.getElementById("searchComposer");
const aboutDialog = document.getElementById("aboutDialog");
const aboutOpens = document.querySelectorAll("[data-about-open]");
const aboutClose = document.querySelector("[data-about-close]");
const evidenceRail = document.getElementById("evidenceRail");
const evidenceAnswer = document.querySelector("[data-evidence-answer]");
const evidenceContent = document.querySelector("[data-evidence-content]");
const evidenceClose = document.querySelector("[data-evidence-close]");
const newQuestionButtons = document.querySelectorAll("[data-new-question]");
let aboutReturnFocus = null;
let evidenceReturnFocus = null;
let shareMenuReturnFocus = null;
let requestPending = false;
let activeController = null;
let requestGeneration = 0;
let answerSequence = 0;
const answerStore = new Map();
const responseKinds = new Set(["small_talk", "evidence_answer", "no_evidence", "validation"]);

function detectPerformanceProfile() {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const slowConnection = Boolean(connection && ["slow-2g", "2g"].includes(connection.effectiveType));
    const unknownConnection = Boolean(connection && !connection.effectiveType);
    const lowMemory = typeof navigator.deviceMemory === "number" && navigator.deviceMemory <= 2;
    const lowCpu = typeof navigator.hardwareConcurrency === "number" && navigator.hardwareConcurrency <= 4;
    const saveData = connection?.saveData === true;
    let mode = "full";
    if (reducedMotion) mode = "reduced";
    else if (saveData || slowConnection || unknownConnection || lowMemory || lowCpu) mode = "light";

    return {
        mode,
        reducedMotion,
        saveData,
        slowConnection,
        lowMemory,
        lowCpu,
        connectionType: connection?.effectiveType || "unknown",
    };
}

function applyPerformanceProfile() {
    const profile = detectPerformanceProfile();
    document.body.dataset.motionMode = profile.mode;
    window.PdfSearch.performanceProfile = profile;
    const optionalFont = document.getElementById("optional-deva-font");
    if (optionalFont) optionalFont.media = profile.mode === "full" ? "all" : "not all";
    return profile;
}

let performanceProfile = applyPerformanceProfile();
const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
connection?.addEventListener?.("change", () => {
    performanceProfile = applyPerformanceProfile();
});

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

function resetConversation() {
    requestGeneration += 1;
    activeController?.abort();
    activeController = null;
    requestPending = false;
    sendBtn.removeAttribute("aria-busy");
    chatMain.setAttribute("aria-busy", "false");
    chatMain.replaceChildren();
    answerStore.clear();
    answerSequence = 0;
    appendWelcomeMessage();
    userQuery.value = "";
    userQuery.dispatchEvent(new Event("input", {bubbles: true}));
    userQuery.focus();
    resetEvidenceRail();
}

newQuestionButtons.forEach(button => button.addEventListener("click", resetConversation));

function closeAbout() {
    if (!aboutDialog) return;
    if (typeof aboutDialog.close === "function") {
        aboutDialog.close();
    } else {
        aboutDialog.removeAttribute("open");
    }
    if (aboutReturnFocus) aboutReturnFocus.focus();
}

aboutOpens.forEach(aboutOpen => aboutOpen.addEventListener("click", () => {
    aboutReturnFocus = aboutOpen;
    if (typeof aboutDialog.showModal === "function") aboutDialog.showModal();
    else aboutDialog.setAttribute("open", "");
    aboutClose?.focus();
}));
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
    let loadingTimer = null;

    function showStage() {
        typingDiv.replaceChildren();
        const stage = document.createElement("span");
        stage.className = "search-loading__label";
        stage.textContent = stages[stageIndex];
        [0, 1, 2].forEach(index => {
            const dot = document.createElement("span");
            dot.setAttribute("aria-hidden", "true");
            dot.className = "search-loading__dot";
            dot.style.animationDelay = `${index * 0.18}s`;
            typingDiv.appendChild(dot);
        });
        typingDiv.prepend(stage);
        stageIndex++;
        if(stageIndex < stages.length) {
            loadingTimer = setTimeout(showStage, 2000);
        }
    }
    showStage();

    const formData = new FormData();
    formData.append('query', query);
    formData.append('language', document.documentElement.lang === 'mr' ? 'mr' : 'en');
    const generation = ++requestGeneration;
    const controller = new AbortController();
    activeController = controller;
    const timeout = setTimeout(() => controller.abort(), 60000);

    try {
        const response = await fetch(window.PdfSearch.searchQueryUrl, {
            method: "POST",
            headers: {'X-CSRFToken': window.PdfSearch.csrfToken},
            body: formData,
            credentials: "same-origin",
            signal: controller.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (generation !== requestGeneration) return;
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
        if (!isValidSuccessPayload(data)) {
            typingDiv.remove();
            appendErrorMessage(
                searchMessages.unexpected,
                searchMessages.request_failed,
                query,
            );
            return;
        }
        typingDiv.classList.add("search-loading--complete");
        typingDiv.setAttribute("aria-label", stages[stages.length - 1]);
        typingDiv.remove();
        typeEffect(
            data.answer,
            data.references,
            query,
            data.kind,
            data.language || "",
        );
    } catch(err) {
        typingDiv.remove();
        if (generation !== requestGeneration) return;
        if (err.name === "AbortError") {
            appendErrorMessage(searchMessages.timeout, searchMessages.try_again, query);
        } else {
            appendErrorMessage(searchMessages.unexpected, searchMessages.try_later, query);
            console.error(err);
        }
    } finally {
        clearTimeout(timeout);
        clearTimeout(loadingTimer);
        if (generation === requestGeneration) {
            activeController = null;
            requestPending = false;
            sendBtn.removeAttribute("aria-busy");
            chatMain.setAttribute("aria-busy", "false");
            updateSendButton();
        }
    }
}

function isValidSuccessPayload(payload) {
    if (!Boolean(
        payload
        && typeof payload === "object"
        && responseKinds.has(payload.kind)
        && typeof payload.answer === "string"
        && payload.answer.trim()
        && Array.isArray(payload.references)
        && ["en", "mr"].includes(payload.language)
    )) return false;
    if (payload.kind === "evidence_answer") {
        return payload.references.length > 0 && payload.references.every(isValidReference);
    }
    return payload.references.length === 0;
}

function isValidReference(reference) {
    const pdfId = Number(reference?.pdf_id);
    return Boolean(
        reference
        && typeof reference === "object"
        && !Array.isArray(reference)
        && typeof reference.title === "string"
        && reference.title.trim()
        && ((Number.isSafeInteger(pdfId) && pdfId > 0) || safeReferenceUrl(reference))
    );
}

function appendMessage(text, sender, isLoading=false){
    const div = document.createElement('div');
    div.className = sender === 'user'
        ? 'conversation-entry conversation-entry--user user-msg'
        : 'conversation-entry conversation-entry--assistant gpt-msg';

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
    const div = document.createElement('section');
    div.className = 'empty-state gpt-msg--welcome';
    div.setAttribute('aria-labelledby', 'empty-state-heading');

    const mark = document.createElement('span');
    mark.className = 'empty-state__mark';
    mark.setAttribute('aria-hidden', 'true');
    mark.textContent = 'AI';
    div.appendChild(mark);

    const heading = document.createElement('h2');
    heading.id = 'empty-state-heading';
    heading.textContent = workbenchCopy.empty_heading;
    div.appendChild(heading);

    const support = document.createElement('p');
    support.className = 'empty-state__support';
    support.textContent = workbenchCopy.empty_support;
    div.appendChild(support);

    const trust = document.createElement('p');
    trust.className = 'empty-state__trust';
    trust.textContent = workbenchCopy.empty_trust;
    div.appendChild(trust);

    const promptWrap = document.createElement('div');
    promptWrap.className = 'suggested-questions';
    const promptHeading = document.createElement('strong');
    promptHeading.textContent = workbenchCopy.suggested_questions;
    promptWrap.appendChild(promptHeading);
    const promptGrid = document.createElement('div');
    promptGrid.className = 'suggested-questions__grid';
    welcomePrompts.forEach(prompt => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'suggested-question prompt-chip';
        button.textContent = prompt;
        button.dataset.prompt = prompt;
        promptGrid.appendChild(button);
    });
    promptWrap.appendChild(promptGrid);
    div.appendChild(promptWrap);
    chatMain.appendChild(div);
    chatMain.scrollTop = 0;
}

function appendErrorMessage(title, detail, retryQuery) {
    const box = document.createElement('div');
    box.className = 'search-error conversation-entry conversation-entry--assistant';
    box.setAttribute('role', 'alert');

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
    retry.dataset.retryQuery = retryQuery || '';
    content.appendChild(retry);
    box.appendChild(content);
    chatMain.appendChild(box);
    chatMain.scrollTop = chatMain.scrollHeight;
    return box;
}


function typeEffect(text, references = [], query = "", responseKind = "evidence_answer", language = "") {
    const div = document.createElement('div');
    div.className = 'conversation-entry conversation-entry--assistant gpt-msg';
    const allowedKinds = new Set(["small_talk", "evidence_answer", "no_evidence", "validation"]);
    div.dataset.responseKind = allowedKinds.has(responseKind) ? responseKind : "evidence_answer";
    if (["en", "mr"].includes(language)) div.lang = language;
    div.setAttribute("aria-live", "off");
    chatMain.appendChild(div);
    const finish = () => {
        div.classList.add("answer-complete");
        if (responseKind === "evidence_answer") {
            appendReferences(div, references);
            appendAnswerActions(div, {answer: text, query, references});
            updateEvidenceRail(references);
        } else {
            resetEvidenceRail();
        }
        const announcement = document.createElement("span");
        announcement.className = "visually-hidden";
        announcement.setAttribute("role", "status");
        announcement.setAttribute("aria-live", "polite");
        announcement.textContent = searchMessages.answer_ready || "Answer ready";
        div.appendChild(announcement);
    };

    // The response is already complete when this JSON endpoint resolves.
    // Rendering it synchronously avoids adding up to several seconds of
    // artificial character-by-character latency after the network wait.
    appendFormattedAnswer(div, text);
    finish();
    return div;
}

function appendFormattedAnswer(parent, text) {
    const body = document.createElement("div");
    body.className = "answer-body";
    const lines = text.replace(/\r\n?/g, "\n").split("\n");
    let paragraph = null;
    let list = null;
    let listType = null;

    const flushParagraph = () => {
        if (paragraph) {
            body.appendChild(paragraph);
            paragraph = null;
        }
    };
    const closeList = () => {
        flushParagraph();
        list = null;
        listType = null;
    };

    lines.forEach(line => {
        const trimmed = line.trim();
        if (!trimmed) {
            closeList();
            return;
        }
        const heading = trimmed.match(/^#{1,3}\s+(.+)$/);
        const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
        const unordered = trimmed.match(/^[-*]\s+(.+)$/);
        if (heading) {
            closeList();
            const element = document.createElement("h3");
            appendInlineFormatting(element, heading[1]);
            body.appendChild(element);
            return;
        }
        if (ordered || unordered) {
            const nextType = ordered ? "ol" : "ul";
            if (!list || listType !== nextType) {
                closeList();
                listType = nextType;
                list = document.createElement(nextType);
                body.appendChild(list);
            }
            const item = document.createElement("li");
            appendInlineFormatting(item, (ordered || unordered)[1]);
            list.appendChild(item);
            return;
        }
        closeList();
        if (!paragraph) paragraph = document.createElement("p");
        else paragraph.appendChild(document.createElement("br"));
        appendInlineFormatting(paragraph, trimmed);
    });
    closeList();
    parent.appendChild(body);
}

function appendInlineFormatting(parent, value) {
    value.split(/(\*\*[^*]+\*\*)/g).forEach(token => {
        if (!token) return;
        if (token.startsWith("**") && token.endsWith("**")) {
            const strong = document.createElement("strong");
            strong.textContent = token.slice(2, -2);
            parent.appendChild(strong);
        } else {
            parent.appendChild(document.createTextNode(token));
        }
    });
}

function appendPlainText(parent, text) {
    text.split("\n").forEach((line, idx) => {
        if (idx > 0) parent.appendChild(document.createElement("br"));
        parent.appendChild(document.createTextNode(line));
    });
}

function appendReferences(parent, references) {
    const section = document.createElement("section");
    section.className = "answer-sources references";
    section.setAttribute("aria-label", sourceDocumentsLabel);
    const header = document.createElement("div");
    header.className = "answer-sources__header";
    const refDiv = document.createElement("div");
    refDiv.className = "answer-sources__list";
    const heading = document.createElement("strong");
    heading.textContent = sourceDocumentsLabel;
    header.appendChild(heading);
    const open = document.createElement("button");
    open.type = "button";
    open.className = "source-drawer-trigger";
    open.dataset.evidenceOpen = "true";
    open.textContent = references.length
        ? `${workbenchCopy.view_sources} (${references.length})`
        : workbenchCopy.view_sources;
    header.appendChild(open);
    section.appendChild(header);
    if (!references.length) {
        const empty = document.createElement("p");
        empty.className = "answer-sources__empty";
        empty.textContent = workbenchCopy.empty_sources;
        section.appendChild(empty);
        parent.appendChild(section);
        return;
    }
    references.forEach((ref, index) => {
        refDiv.appendChild(createReferenceCard(ref, index + 1));
    });
    section.appendChild(refDiv);
    parent.appendChild(section);
}

function createReferenceCard(ref, index = 1) {
    const card = document.createElement("article");
    card.className = "ref-card";
    const number = document.createElement("span");
    number.className = "ref-card__number";
    number.setAttribute("aria-hidden", "true");
    number.textContent = `[${index}]`;
    card.appendChild(number);
    const body = document.createElement("div");
    const referenceUrl = safeReferenceUrl(ref);
    const link = document.createElement(referenceUrl ? "a" : "span");
    if (referenceUrl) {
        link.href = referenceUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
    } else {
        link.className = "ref-card__unavailable";
    }
    link.textContent = ref.title || sourceDocumentsLabel;
    if (referenceUrl) link.setAttribute("aria-label", `${workbenchCopy.view_original}: ${link.textContent}`);
    body.appendChild(link);
    const meta = document.createElement("small");
    meta.textContent = [ref.folder, ref.page ? `Page ${ref.page}` : "", ref.uploaded_at]
        .filter(Boolean)
        .join(" • ");
    if (meta.textContent) body.appendChild(meta);
    if (ref.excerpt) {
        const excerpt = document.createElement("p");
        excerpt.textContent = ref.excerpt;
        body.appendChild(excerpt);
    }
    card.appendChild(body);
    return card;
}

function buildShareText({answer, query, references}) {
    const cleanAnswer = cleanAnswerText(answer);
    const sourceLines = references.map((ref, index) => {
        const referenceUrl = safeReferenceUrl(ref);
        if (!referenceUrl) return null;
        const url = new URL(referenceUrl, window.location.origin).href;
        return `[${index + 1}] ${ref.title || sourceDocumentsLabel} — ${url}`;
    }).filter(Boolean).join("\n");
    return `${workbenchCopy.share_title}\n\n${workbenchCopy.share_question}:\n${query}\n\n${workbenchCopy.share_answer_label}:\n${cleanAnswer}\n\n${workbenchCopy.share_sources}:\n${sourceLines || workbenchCopy.empty_sources}`;
}

function cleanAnswerText(answer) {
    return answer
        .replace(/\*\*(.*?)\*\*/g, "$1")
        .replace(/^#{1,3}\s+/gm, "")
        .replace(/^\s*[-*]\s+/gm, "• ");
}

function appendAnswerActions(parent, payload) {
    const actions = document.createElement("div");
    actions.className = "answer-actions";
    const answerId = `answer-${++answerSequence}`;
    answerStore.set(answerId, payload);
    while (answerStore.size > 20) answerStore.delete(answerStore.keys().next().value);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.dataset.answerId = answerId;
    copy.dataset.copyAnswer = "true";
    copy.textContent = workbenchCopy.copy_answer;
    actions.appendChild(copy);
    const feedback = document.createElement("a");
    feedback.href = window.PdfSearch.feedbackUrl;
    feedback.target = "_blank";
    feedback.rel = "noopener noreferrer";
    feedback.textContent = workbenchCopy.feedback;
    actions.appendChild(feedback);
    const share = document.createElement("button");
    share.type = "button";
    share.dataset.answerId = answerId;
    share.dataset.shareAnswer = "true";
    share.textContent = workbenchCopy.share_answer;
    actions.appendChild(share);
    parent.appendChild(actions);
}

async function copyAnswerText(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const fallback = document.createElement("textarea");
    fallback.value = text;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.appendChild(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    fallback.remove();
    if (!copied) throw new Error("Clipboard unavailable");
}

function createShareMenu(button, payload) {
    closeShareMenu();
    shareMenuReturnFocus = button;
    const menu = document.createElement("div");
    menu.className = "share-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", workbenchCopy.share_menu_label);
    const text = buildShareText(payload);
    const encodedText = encodeURIComponent(text);
    const encodedUrl = encodeURIComponent(window.location.href);
    const options = [
        [workbenchCopy.share_whatsapp, `https://api.whatsapp.com/send?text=${encodedText}`],
        [workbenchCopy.share_telegram, `https://t.me/share/url?url=${encodedUrl}&text=${encodedText}`],
        [workbenchCopy.share_teams, `https://teams.microsoft.com/share?href=${encodedUrl}&msgText=${encodedText}`],
    ];
    options.forEach(([label, href]) => {
        const link = document.createElement("a");
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("role", "menuitem");
        link.textContent = label;
        menu.appendChild(link);
    });
    const copy = document.createElement("button");
    copy.type = "button";
    copy.setAttribute("role", "menuitem");
    copy.textContent = workbenchCopy.copy_share_text;
    copy.addEventListener("click", async () => {
        try {
            await copyAnswerText(text);
            copy.textContent = workbenchCopy.copied;
        } catch (_) {
            copy.textContent = workbenchCopy.copy_failed;
        }
    });
    menu.appendChild(copy);
    button.parentElement.appendChild(menu);
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-haspopup", "menu");
    const menuItems = [...menu.querySelectorAll("a, button")];
    menu.addEventListener("keydown", event => {
        const current = menuItems.indexOf(document.activeElement);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const next = event.key === "ArrowDown"
                ? (current + 1) % menuItems.length
                : (current - 1 + menuItems.length) % menuItems.length;
            menuItems[next].focus();
        } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            menuItems[event.key === "Home" ? 0 : menuItems.length - 1].focus();
        }
    });
    menu.querySelector("a, button")?.focus();
}

function closeShareMenu(restoreFocus = false) {
    const menu = document.querySelector(".share-menu");
    if (!menu) return;
    menu.remove();
    shareMenuReturnFocus?.setAttribute("aria-expanded", "false");
    if (restoreFocus) shareMenuReturnFocus?.focus();
    shareMenuReturnFocus = null;
}

async function shareAnswer(button, payload) {
    const shareData = {
        title: workbenchCopy.share_title,
        text: buildShareText(payload),
        url: window.location.href,
    };
    if (navigator.share && (!navigator.canShare || navigator.canShare(shareData))) {
        try {
            await navigator.share(shareData);
            return;
        } catch (error) {
            if (error?.name === "AbortError") return;
        }
    }
    createShareMenu(button, payload);
}

function updateEvidenceRail(references) {
    if (!evidenceRail || !evidenceAnswer || !evidenceContent) return;
    evidenceContent.replaceChildren();
    const intro = document.createElement("p");
    intro.className = "evidence-summary";
    intro.textContent = references.length
        ? `${references.length} ${sourceDocumentsLabel.toLowerCase()}`
        : workbenchCopy.empty_sources;
    evidenceContent.appendChild(intro);
    references.forEach((ref, index) => evidenceContent.appendChild(createReferenceCard(ref, index + 1)));
    evidenceAnswer.hidden = false;
    document.querySelector("[data-evidence-empty]")?.setAttribute("hidden", "");
}

function openEvidence(trigger) {
    if (!evidenceRail) return;
    evidenceReturnFocus = trigger || null;
    evidenceRail.classList.add("is-open");
    evidenceClose?.focus();
}

function closeEvidence() {
    if (!evidenceRail) return;
    evidenceRail.classList.remove("is-open");
    evidenceReturnFocus?.focus();
    evidenceReturnFocus = null;
}

function resetEvidenceRail() {
    if (!evidenceRail || !evidenceAnswer || !evidenceContent) return;
    evidenceAnswer.hidden = true;
    evidenceContent.replaceChildren();
    document.querySelector("[data-evidence-empty]")?.removeAttribute("hidden");
    closeEvidence();
}

evidenceClose?.addEventListener("click", closeEvidence);
document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
        closeEvidence();
        closeShareMenu(true);
    }
});

document.addEventListener("click", event => {
    const menu = document.querySelector(".share-menu");
    if (menu && !menu.contains(event.target) && event.target !== shareMenuReturnFocus) closeShareMenu();
});

function appendPromptOrRetry(event) {
    const prompt = event.target.closest("[data-prompt]");
    if (prompt) {
        userQuery.value = prompt.dataset.prompt;
        userQuery.dispatchEvent(new Event("input", {bubbles: true}));
        userQuery.focus();
        return;
    }
    const retry = event.target.closest("[data-retry-query]");
    if (retry) {
        userQuery.value = retry.dataset.retryQuery;
        userQuery.dispatchEvent(new Event("input", {bubbles: true}));
        sendMessage();
        return;
    }
    const sourceTrigger = event.target.closest("[data-evidence-open]");
    if (sourceTrigger) {
        openEvidence(sourceTrigger);
        return;
    }
    const copy = event.target.closest("[data-copy-answer]");
    if (copy) {
        const payload = answerStore.get(copy.dataset.answerId);
        if (!payload) return;
        copyAnswerText(cleanAnswerText(payload.answer)).then(() => {
            copy.textContent = workbenchCopy.copied;
            setTimeout(() => { copy.textContent = workbenchCopy.copy_answer; }, 1600);
        }).catch(() => {
            copy.textContent = workbenchCopy.copy_failed;
            setTimeout(() => { copy.textContent = workbenchCopy.copy_answer; }, 1600);
        });
        return;
    }
    const share = event.target.closest("[data-share-answer]");
    if (share) {
        const payload = answerStore.get(share.dataset.answerId);
        if (payload) shareAnswer(share, payload);
    }
}

chatMain.addEventListener("click", appendPromptOrRetry);


function protectedPdfUrl(pdfId) {
    if (!pdfId) return null;
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

    wordCounter.textContent = `${count} ${workbenchCopy.word_count}`;

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

updateSendButton();

userQuery.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        searchComposer.requestSubmit();
    }
});

userQuery.addEventListener("input", () => {
    userQuery.style.height = "auto";
    userQuery.style.height = `${Math.min(userQuery.scrollHeight, 140)}px`;
});

document.querySelectorAll('form[action="' + window.PdfSearch.setLanguageUrl + '"]').forEach(form => {
    form.addEventListener('submit', () => {
        const langInput = form.querySelector('input[name="language"]');
        if(langInput) document.documentElement.lang = langInput.value;
    });
});
