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
  const analyticsView = "classic";
  const analyticsAdapter = window.PdfSearchAnalytics;
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

  function analyticsCall(method, args = [], fallback = null) {
    try {
      const operation = analyticsAdapter?.[method];
      return typeof operation === "function" ? operation(...args) : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function track(name, properties) {
    return analyticsCall("track", [name, properties], false);
  }

  function trackCompleted({startedAt, wordCount, questionLanguage, outcome, answerKind, failureFamily, references = 0}) {
    if (!analyticsAdapter) return;
    track("search_completed", {
      view: analyticsView,
      question_language: questionLanguage,
      word_count_bucket: analyticsCall("wordCountBucket", [wordCount], "1-5"),
      latency_bucket: analyticsCall("durationBucket", [performance.now() - startedAt], ">=30s"),
      reference_count_bucket: analyticsCall("countBucket", [references], "0"),
      outcome,
      answer_kind: answerKind,
      failure_family: failureFamily,
    });
  }

  function classifiedFailure(status, payload) {
    if (status === 429 || payload?.error === "rate_limited") return ["rate_limited", "rate_limit"];
    if ([400, 401, 403].includes(status)) return ["invalid_request", "validation"];
    if (status === 503) return ["provider_unavailable", "provider"];
    return ["internal_error", "internal"];
  }

  function updateComposerState() {
    const count = wordsIn(userQuery.value);
    wordCounter.textContent = `${count}/30 ${wordCounter.dataset.label || "words"}`;
    wordCounter.classList.toggle("is-over-limit", count > 30);
    sendBtn.disabled = count === 0 || count > 30 || Boolean(activeController);
    document.querySelectorAll(".classic-retry").forEach(retry => {
      retry.disabled = Boolean(activeController);
    });
  }

  wordCounter.dataset.label = wordCounter.textContent.replace(/^0\/30\s*/u, "").trim();
  userQuery.addEventListener("input", updateComposerState);

  function appendMessage(kind, text) {
    const message = document.createElement("article");
    message.className = `classic-message classic-message--${kind}`;
    message.textContent = text;
    chatMain.appendChild(message);
    revealMessage(message);
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
    revealMessage(message);
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
    appendFormattedAnswer(answerBody, String(answer || ""));
    message.appendChild(answerBody);

    const safeReferences = kind === "evidence_answer" && Array.isArray(references)
      ? references
      : [];
    if (safeReferences.length) {
      const referencesRegion = document.createElement("section");
      referencesRegion.className = "classic-references";
      const heading = document.createElement("h2");
      heading.textContent = `📚 ${sourceDocumentsLabel}`;
      referencesRegion.appendChild(heading);
      safeReferences.forEach((reference, index) => {
        const card = createReferenceCard(reference, index + 1);
        if (card) referencesRegion.appendChild(card);
      });
      if (referencesRegion.childElementCount > 1) message.appendChild(referencesRegion);
    }

    chatMain.appendChild(message);
    revealMessage(message, true);
  }

  function revealMessage(message, alignStart = false) {
    if (!alignStart) {
      chatMain.scrollTop = chatMain.scrollHeight;
      return;
    }
    const transcriptRect = chatMain.getBoundingClientRect();
    const messageRect = message.getBoundingClientRect();
    chatMain.scrollTop += messageRect.top - transcriptRect.top;
  }

  function appendFormattedAnswer(parent, value) {
    const lines = value.replace(/\r\n?/gu, "\n").split("\n");
    let paragraphLines = [];
    let list = null;

    const flushParagraph = () => {
      if (!paragraphLines.length) return;
      const paragraph = document.createElement("p");
      paragraphLines.forEach((line, index) => {
        if (index) paragraph.appendChild(document.createElement("br"));
        appendInlineFormatting(paragraph, line);
      });
      parent.appendChild(paragraph);
      paragraphLines = [];
    };

    const closeList = () => {
      list = null;
    };

    lines.forEach(rawLine => {
      const line = rawLine.trim();
      if (!line) {
        flushParagraph();
        closeList();
        return;
      }

      const headingMatch = line.match(/^(#{1,3})\s+(.+)$/u);
      if (headingMatch) {
        flushParagraph();
        closeList();
        const heading = document.createElement("h3");
        appendInlineFormatting(heading, headingMatch[2]);
        parent.appendChild(heading);
        return;
      }

      const orderedMatch = line.match(/^\d+[.)]\s+(.+)$/u);
      const unorderedMatch = line.match(/^[-*]\s+(.+)$/u);
      const listMatch = orderedMatch || unorderedMatch;
      if (listMatch) {
        flushParagraph();
        const listTag = orderedMatch ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== listTag) {
          list = document.createElement(listTag);
          parent.appendChild(list);
        }
        const item = document.createElement("li");
        appendInlineFormatting(item, listMatch[1]);
        list.appendChild(item);
        return;
      }

      closeList();
      paragraphLines.push(line);
    });

    flushParagraph();
    if (!parent.childElementCount) parent.appendChild(document.createElement("p"));
  }

  function appendInlineFormatting(parent, value) {
    const tokens = value.split(/(\*\*[^*]+\*\*)/gu);
    tokens.forEach(token => {
      if (token.startsWith("**") && token.endsWith("**") && token.length > 4) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.appendChild(strong);
        return;
      }
      parent.appendChild(document.createTextNode(token));
    });
  }

  function createReferenceCard(reference, rank = 1) {
    const href = safeReferenceUrl(reference);
    if (!href) return null;
    const card = document.createElement("a");
    card.className = "classic-reference";
    card.href = href;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.addEventListener("click", () => {
      track("search_source_selected", {
        view: analyticsView,
        source_rank_bucket: analyticsCall("rankBucket", [rank], "4+"),
        answer_kind: "evidence_answer",
      });
    });

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
    const pdfId = Number(reference?.pdf_id);
    if (!candidate && Number.isSafeInteger(pdfId) && pdfId > 0) {
      candidate = (form.dataset.publicPdfUrl || "").replace(
        /\/pdf\/0\/public\/?$/u,
        `/pdf/${pdfId}/public/`,
      );
    }
    if (!candidate) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      const protectedPdf = /^\/pdf\/[1-9]\d*\/(?:public|view)\/$/u.test(parsed.pathname);
      return parsed.origin === window.location.origin && protectedPdf
        ? parsed.href
        : "";
    } catch (_error) {
      return "";
    }
  }

  function appendError(title, detail, retryQuery, failureFamily = "internal") {
    const message = document.createElement("article");
    message.className = "classic-message classic-message--assistant classic-message--error";
    message.setAttribute("role", "alert");
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
      retry.disabled = Boolean(activeController);
      retry.addEventListener("click", () => {
        if (activeController) return;
        track("search_retry_clicked", {
          view: analyticsView,
          failure_family: failureFamily,
        });
        userQuery.value = retryQuery;
        updateComposerState();
        form.requestSubmit();
      });
      message.appendChild(retry);
    }
    chatMain.appendChild(message);
    revealMessage(message);
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
    if (status === 401) {
      return [messages.search_unavailable || "Search unavailable", messages.sign_in || "Please sign in to search."];
    }
    if (status === 403) {
      return [messages.search_unavailable || "Search unavailable", messages.security || "Refresh the page and try again."];
    }
    return [messages.unexpected || "Something went wrong", messages.request_failed || messages.try_again || "Please try again."];
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
    const startedAt = performance.now();
    const pageLanguage = language === "mr" ? "mr" : "en";
    const questionLanguage = analyticsCall("questionLanguage", [query, pageLanguage], pageLanguage);
    track("search_submitted", {
      view: analyticsView,
      question_language: questionLanguage,
      word_count_bucket: analyticsCall("wordCountBucket", [count], "1-5"),
    });
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
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
      if (!response.ok || payload?.error) {
        const [title, detail] = errorCopy(response.status, payload);
        const [outcome, failureFamily] = classifiedFailure(response.status, payload);
        trackCompleted({
          startedAt,
          wordCount: count,
          questionLanguage,
          outcome,
          answerKind: "error",
          failureFamily,
        });
        appendError(
          title,
          detail,
          response.status >= 500 || response.status === 429 ? query : "",
          failureFamily,
        );
      } else if (!isValidSuccessPayload(payload)) {
        const [title, detail] = errorCopy(502, null);
        trackCompleted({
          startedAt,
          wordCount: count,
          questionLanguage,
          outcome: "internal_error",
          answerKind: "error",
          failureFamily: "contract",
        });
        appendError(title, detail, query, "contract");
      } else {
        const outcome = payload.kind === "evidence_answer"
          ? "evidence"
          : payload.kind === "no_evidence"
            ? "no_evidence"
            : "conversational";
        trackCompleted({
          startedAt,
          wordCount: count,
          questionLanguage: payload.language,
          outcome,
          answerKind: payload.kind,
          failureFamily: "none",
          references: payload.references?.length || 0,
        });
        appendAnswer(
          payload.answer,
          payload.kind === "evidence_answer" ? (payload.references || []) : [],
          payload.kind,
          payload.language || "",
        );
      }
    } catch (error) {
      const timedOut = error?.name === "AbortError";
      trackCompleted({
        startedAt,
        wordCount: count,
        questionLanguage,
        outcome: timedOut ? "provider_timeout" : "internal_error",
        answerKind: "error",
        failureFamily: timedOut ? "timeout" : "network",
      });
      appendError(
        timedOut ? (messages.timeout || "Search is taking longer than expected") : (messages.unexpected || "Something went wrong"),
        timedOut ? (messages.try_again || "Please try again.") : (messages.request_failed || "Please try again later."),
        query,
        timedOut ? "timeout" : "network",
      );
    } finally {
      window.clearTimeout(timeout);
      clearLoadingTimer();
      loading.remove();
      activeController = null;
      chatMain.setAttribute("aria-busy", "false");
      updateComposerState();
      try {
        userQuery.focus({preventScroll: true});
      } catch (_error) {
        userQuery.focus();
      }
    }
  }

  form.addEventListener("submit", submitSearch);
  updateComposerState();

  document.querySelector(".classic-message--welcome a")?.addEventListener("click", () => {
    track("search_help_opened", {view: analyticsView});
  });
  document.querySelector(".classic-icon-link--feedback")?.addEventListener("click", () => {
    track("search_feedback_opened", {view: analyticsView});
  });
})();
