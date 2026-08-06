(() => {
  "use strict";

  const form = document.getElementById("searchComposer");
  const chatMain = document.getElementById("chatMain");
  const userQuery = document.getElementById("userQuery");
  const sendBtn = document.getElementById("sendBtn");
  const wordCounter = document.getElementById("wordCounter");
  if (!form || !chatMain || !userQuery || !sendBtn || !wordCounter) return;

  const loadingStages = readJson("classic-search-loading-stages", ["Searching…"]);
  const messages = readJson("classic-search-messages", {});
  const sourceDocumentsLabel = readJson(
    "classic-source-documents-label",
    "Source documents",
  );
  const retryLabel = readJson("classic-retry-label", "Try again");
  const csrfToken = form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  const language = form.querySelector("input[name='language']")?.value || "en";
  let activeController = null;
  let stageTimer = null;
  const responseKinds = new Set(["small_talk", "evidence_answer", "no_evidence", "validation"]);

  function readJson(id, fallback) {
    const element = document.getElementById(id);
    if (!element) return fallback;
    try {
      return JSON.parse(element.textContent);
    } catch (_error) {
      return fallback;
    }
  }

  function wordsIn(value) {
    const valueTrimmed = value.trim();
    return valueTrimmed ? valueTrimmed.split(/\s+/u).length : 0;
  }

  function updateComposerState() {
    const count = wordsIn(userQuery.value);
    wordCounter.textContent = `${count}/30 ${wordCounter.dataset.label || "words"}`;
    wordCounter.classList.toggle("is-over-limit", count > 30);
    sendBtn.disabled = count === 0 || count > 30 || Boolean(activeController);
  }

  wordCounter.dataset.label = wordCounter.textContent.replace(/^0\/30\s*/u, "").trim();
  userQuery.addEventListener("input", updateComposerState);

  function appendMessage(kind, text) {
    const message = document.createElement("article");
    message.className = `classic-message classic-message--${kind}`;
    message.textContent = text;
    chatMain.appendChild(message);
    chatMain.scrollTop = chatMain.scrollHeight;
    return message;
  }

  function appendLoading() {
    const message = document.createElement("article");
    message.className = "classic-message classic-message--assistant classic-message--loading";
    message.setAttribute("role", "status");
    const label = document.createElement("strong");
    const dots = document.createElement("span");
    dots.className = "classic-loading-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) dots.appendChild(document.createElement("i"));
    message.append(label, dots);
    chatMain.appendChild(message);

    let stage = 0;
    const updateStage = () => {
      label.textContent = loadingStages[stage] || loadingStages[0] || "Searching…";
      stage = (stage + 1) % Math.max(loadingStages.length, 1);
    };
    updateStage();
    stageTimer = window.setInterval(updateStage, 2200);
    chatMain.scrollTop = chatMain.scrollHeight;
    return message;
  }

  function clearLoadingTimer() {
    if (stageTimer !== null) window.clearInterval(stageTimer);
    stageTimer = null;
  }

  function appendAnswer(answer, references, kind = "evidence_answer", language = "") {
    const message = document.createElement("article");
    message.className = "classic-message classic-message--assistant";
    message.dataset.responseKind = responseKinds.has(kind) ? kind : "evidence_answer";
    if (["en", "mr"].includes(language)) message.lang = language;

    const answerBody = document.createElement("div");
    answerBody.className = "classic-message__answer";
    const paragraphs = String(answer || "")
      .split(/\n{2,}/u)
      .map(value => value.trim())
      .filter(Boolean);
    for (const paragraphText of paragraphs.length ? paragraphs : [String(answer || "")]) {
      const paragraph = document.createElement("p");
      paragraph.textContent = paragraphText;
      answerBody.appendChild(paragraph);
    }
    message.appendChild(answerBody);

    const safeReferences = Array.isArray(references) ? references : [];
    if (safeReferences.length) {
      const referencesRegion = document.createElement("section");
      referencesRegion.className = "classic-references";
      const heading = document.createElement("h2");
      heading.textContent = `📚 ${sourceDocumentsLabel}`;
      referencesRegion.appendChild(heading);
      safeReferences.forEach(reference => {
        const card = createReferenceCard(reference);
        if (card) referencesRegion.appendChild(card);
      });
      if (referencesRegion.childElementCount > 1) message.appendChild(referencesRegion);
    }

    chatMain.appendChild(message);
    chatMain.scrollTop = chatMain.scrollHeight;
  }

  function createReferenceCard(reference) {
    const href = safeReferenceUrl(reference);
    if (!href) return null;
    const card = document.createElement("a");
    card.className = "classic-reference";
    card.href = href;
    card.target = "_blank";
    card.rel = "noopener noreferrer";

    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = reference.title || sourceDocumentsLabel;
    copy.appendChild(title);
    const metadataValues = [
      reference.folder,
      reference.page ? `Page ${reference.page}` : "",
      reference.uploaded_at,
    ].filter(Boolean);
    if (metadataValues.length) {
      const metadata = document.createElement("small");
      metadata.textContent = metadataValues.join(" • ");
      copy.appendChild(metadata);
    }
    const arrow = document.createElement("span");
    arrow.className = "classic-reference__arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "↗";
    card.append(copy, arrow);
    return card;
  }

  function safeReferenceUrl(reference) {
    let candidate = reference?.url || "";
    if (!candidate && reference?.pdf_id) {
      candidate = form.dataset.viewPdfUrl.replace(/\/0\/?$/u, `/${reference.pdf_id}/`);
    }
    if (!candidate) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      return parsed.origin === window.location.origin ? parsed.href : "";
    } catch (_error) {
      return "";
    }
  }

  function appendError(title, detail, retryQuery) {
    const message = document.createElement("article");
    message.className = "classic-message classic-message--assistant classic-message--error";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const explanation = document.createElement("p");
    explanation.textContent = detail;
    message.append(heading, explanation);
    if (retryQuery) {
      const retry = document.createElement("button");
      retry.className = "classic-retry";
      retry.type = "button";
      retry.textContent = retryLabel;
      retry.addEventListener("click", () => {
        userQuery.value = retryQuery;
        updateComposerState();
        form.requestSubmit();
      });
      message.appendChild(retry);
    }
    chatMain.appendChild(message);
    chatMain.scrollTop = chatMain.scrollHeight;
  }

  function errorCopy(status, payload) {
    if (status === 429 || payload?.error === "rate_limited") {
      return [messages.search_unavailable || "Search unavailable", messages.rate_limited || messages.try_later || "Please try again later."];
    }
    if (status === 400 || payload?.error === "query_too_long") {
      return [messages.question_too_long || "Question is too long", messages.question_too_long_detail || messages.limit || "Please keep it within 30 words."];
    }
    if (status === 503 || payload?.error === "search_unavailable") {
      return [messages.search_unavailable || "Search unavailable", messages.unavailable || messages.try_later || "Please try again later."];
    }
    return [messages.unexpected || "Something went wrong", messages.request_failed || messages.try_again || "Please try again."];
  }

  async function submitSearch(event) {
    event.preventDefault();
    if (activeController) return;
    const query = userQuery.value.trim();
    const count = wordsIn(query);
    if (!query) {
      userQuery.focus();
      return;
    }
    if (count > 30) {
      const [title, detail] = errorCopy(400, {error: "query_too_long"});
      appendError(title, detail, "");
      return;
    }

    appendMessage("user", query);
    userQuery.value = "";
    activeController = new AbortController();
    chatMain.setAttribute("aria-busy", "true");
    updateComposerState();
    const loading = appendLoading();
    const timeout = window.setTimeout(() => activeController?.abort(), 60000);

    try {
      const data = new FormData();
      data.set("query", query);
      data.set("language", language);
      const response = await fetch(form.action, {
        method: "POST",
        headers: {"X-CSRFToken": csrfToken},
        body: data,
        credentials: "same-origin",
        signal: activeController.signal,
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok || payload.error) {
        const [title, detail] = errorCopy(response.status, payload);
        appendError(
          title,
          payload.detail || detail,
          response.status >= 500 || response.status === 429 ? query : "",
        );
      } else {
        appendAnswer(
          payload.answer || "",
          payload.references || [],
          payload.kind || "evidence_answer",
          payload.language || "",
        );
      }
    } catch (error) {
      const timedOut = error?.name === "AbortError";
      appendError(
        timedOut ? (messages.timeout || "Search is taking longer than expected") : (messages.unexpected || "Something went wrong"),
        timedOut ? (messages.try_again || "Please try again.") : (messages.request_failed || "Please try again later."),
        query,
      );
    } finally {
      window.clearTimeout(timeout);
      clearLoadingTimer();
      loading.remove();
      activeController = null;
      chatMain.setAttribute("aria-busy", "false");
      updateComposerState();
      userQuery.focus();
    }
  }

  form.addEventListener("submit", submitSearch);
  updateComposerState();

  const cookieNotice = document.getElementById("cookieConsent");
  const cookieAccept = document.querySelector("[data-cookie-accept]");
  try {
    if (cookieNotice && !window.localStorage.getItem("cookieConsent")) cookieNotice.hidden = false;
  } catch (_error) {
    if (cookieNotice) cookieNotice.hidden = false;
  }
  cookieAccept?.addEventListener("click", () => {
    if (cookieNotice) cookieNotice.hidden = true;
    try {
      window.localStorage.setItem("cookieConsent", "accepted");
    } catch (_error) {
      // The notice can still be dismissed for this page when storage is unavailable.
    }
  });
})();
