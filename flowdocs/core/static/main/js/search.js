const chatMain = document.getElementById('chatMain');
const sendBtn = document.getElementById('sendBtn');
const userQuery = document.getElementById('userQuery');
const wordCounter = document.getElementById("wordCounter");
const viewPdfUrlTemplate = window.PdfSearch.viewPdfUrlTemplate;
let requestPending = false;

window.addEventListener("DOMContentLoaded", function(){
    const welcome = JSON.parse(document.getElementById("welcome-message").textContent);
    const helpUrl = JSON.parse(document.getElementById("welcome-help-url").textContent);
    const helpLabel = JSON.parse(document.getElementById("welcome-help-label").textContent);
    if(welcome) appendWelcomeMessage(welcome, helpUrl, helpLabel);
});

sendBtn.addEventListener('click', sendMessage);
userQuery.addEventListener('keypress', e => { if(e.key === 'Enter') sendMessage(); });

async function sendMessage(){
    if(requestPending) return;
    const query = userQuery.value.trim();
    if(!query) return;

    const wordCount = query.split(/\s+/).length;
    if(wordCount > 30){
        appendMessage("⚠️ Your query is too long (max 30 words). Please shorten it.", "gpt");
        return;
    }

    appendMessage(query, 'user');
    userQuery.value = '';
    sendBtn.disabled = true;
    requestPending = true;
    sendBtn.setAttribute("aria-busy", "true");

    const typingDiv = appendMessage('', 'gpt', true);
    typingDiv.classList.add('typing');

    const stages = [" Searching", " Analyzing", " Composing"];
    let stageIndex = 0;

    function showStage() {
        typingDiv.replaceChildren();
        const stage = document.createElement("strong");
        stage.textContent = stages[stageIndex];
        typingDiv.appendChild(stage);
        ["•", "•", "•"].forEach(symbol => {
            const dot = document.createElement("span");
            dot.textContent = symbol;
            typingDiv.appendChild(dot);
        });
        stageIndex++;
        if(stageIndex < stages.length) {
            setTimeout(showStage, 2000);
        }
    }
    showStage();

    const formData = new FormData();
    formData.append('query', query);
    formData.append('language', document.documentElement.lang === 'mr' ? 'mr' : 'en');

    try {
        const response = await fetch(window.PdfSearch.searchQueryUrl, {
            method: "POST",
            headers: {'X-CSRFToken': window.PdfSearch.csrfToken},
            body: formData,
            credentials: "same-origin"
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            typingDiv.remove();
            const errorMessages = {
                401: "🔐 कृपया शोधण्यासाठी आधी Admin Login करा.",
                403: "⚠️ विनंती सुरक्षित करता आली नाही. कृपया पृष्ठ रिफ्रेश करून पुन्हा प्रयत्न करा.",
                400: "⚠️ प्रश्न 30 शब्दांपेक्षा मोठा असू शकत नाही.",
                500: "⚠️ शोध विनंती पूर्ण करता आली नाही. कृपया नंतर पुन्हा प्रयत्न करा.",
                503: "⚠️ शोध सेवा तात्पुरती अनुपलब्ध आहे. कृपया नंतर पुन्हा प्रयत्न करा.",
                429: "⚠️ कृपया पुढील शोधासाठी थोडा वेळ थांबा.",
            };
            appendMessage(
                errorMessages[response.status] || data.detail || "⚠️ शोध विनंती पूर्ण करता आली नाही.",
                "gpt",
            );
            return;
        }
        typingDiv.remove();
        if(data.answer){
            typeEffect(data.answer, data.references || []);
        } else if(data.error){
            appendMessage('⚠️ Error: ' + data.error, 'gpt');
        }
    } catch(err) {
        typingDiv.remove();
        appendMessage('⚠️ Something went wrong. Please try again later.', 'gpt');
        console.error(err);
    } finally {
        requestPending = false;
        sendBtn.removeAttribute("aria-busy");
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
    div.appendChild(document.createTextNode(text + ' '));
    const link = document.createElement('a');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = label;
    div.appendChild(link);
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
            setTimeout(typing, 20);
        } else {
            cursor.remove();

            if (references.length > 0) {
                const refDiv = document.createElement('div');
                refDiv.className = "references";
                const heading = document.createElement("strong");
                heading.textContent = "📚 Reference Docs:";
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

    wordCounter.textContent = `${count}/30 words`;

    if (count > 30) {
        wordCounter.style.color = "red";
        updateSendButton();
    } else {
        wordCounter.style.color = "gray";
        updateSendButton();
    }
});

document.querySelectorAll('form[action="' + window.PdfSearch.setLanguageUrl + '"]').forEach(form => {
    form.addEventListener('submit', () => {
        const langInput = form.querySelector('input[name="language"]');
        if(langInput) document.documentElement.lang = langInput.value;
    });
});
