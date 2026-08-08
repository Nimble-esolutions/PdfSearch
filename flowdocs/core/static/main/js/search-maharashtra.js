(() => {
  "use strict";

  const form = document.getElementById("searchComposer");
  const chatMain = document.getElementById("chatMain");
  const userQuery = document.getElementById("userQuery");
  const sendButton = document.getElementById("sendBtn");
  const wordCounter = document.getElementById("wordCounter");
  const liveStatus = document.getElementById("searchLiveStatus");
  if (!form || !chatMain || !userQuery || !sendButton || !wordCounter || !liveStatus) return;

  const parsedLoadingStages = readJson("maha-search-loading-stages", ["Searching…"]);
  const loadingStages = Array.isArray(parsedLoadingStages)
    ? parsedLoadingStages.filter((value) => typeof value === "string" && value.trim()).slice(0, 8)
    : ["Searching…"];
  const parsedMessages = readJson("maha-search-messages", {});
  const messages = parsedMessages && typeof parsedMessages === "object" && !Array.isArray(parsedMessages)
    ? parsedMessages
    : {};
  const parsedSourceDocumentsLabel = readJson("maha-source-documents-label", "Source documents");
  const sourceDocumentsLabel = typeof parsedSourceDocumentsLabel === "string"
    ? parsedSourceDocumentsLabel
    : "Source documents";
  const parsedRetryLabel = readJson("maha-retry-label", "Try again");
  const retryLabel = typeof parsedRetryLabel === "string" ? parsedRetryLabel : "Try again";
  const copyLabel = form.dataset.copyLabel || "Copy answer";
  const copiedLabel = form.dataset.copiedLabel || "Copied";
  const viewSourceLabel = form.dataset.viewSourceLabel || "View original PDF";
  const pageLabel = form.dataset.pageLabel || "Page";
  const configuredMaxWords = Number(form.dataset.maxWords);
  const maxWords = Number.isSafeInteger(configuredMaxWords) && configuredMaxWords > 0
    ? configuredMaxWords
    : 30;
  const wordLimitDetail = document.getElementById("maha-word-limit-detail")?.textContent?.trim()
    || `Please keep it within ${maxWords} words.`;
  const csrfToken = form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  const language = form.querySelector("input[name='language']")?.value === "mr" ? "mr" : "en";
  const initialConversation = [...chatMain.childNodes].map((node) => node.cloneNode(true));
  const analyticsView = "maharashtra";
  const analyticsAdapter = window.PdfSearchAnalytics;
  const responseKinds = new Set(["small_talk", "evidence_answer", "no_evidence", "validation"]);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let activeController = null;
  let requestGeneration = 0;
  let loadingTimer = null;

  function readJson(id, fallback) {
    const element = document.getElementById(id);
    if (!element) return fallback;
    try {
      return JSON.parse(element.textContent);
    } catch (_error) {
      return fallback;
    }
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

  function wordsIn(value) {
    const trimmed = value.trim();
    return trimmed ? trimmed.split(/\s+/u).length : 0;
  }

  function setLiveStatus(value) {
    liveStatus.textContent = "";
    window.requestAnimationFrame(() => {
      liveStatus.textContent = value;
    });
  }

  function resizeComposer() {
    userQuery.style.height = "auto";
    userQuery.style.height = `${Math.min(userQuery.scrollHeight, 160)}px`;
  }

  function updateComposerState() {
    const count = wordsIn(userQuery.value);
    const label = wordCounter.dataset.label || "words";
    wordCounter.textContent = `${count}/${maxWords} ${label}`;
    wordCounter.classList.toggle("is-over-limit", count > maxWords);
    sendButton.disabled = count === 0 || count > maxWords || Boolean(activeController);
    document.querySelectorAll(".maha-retry").forEach((button) => {
      button.disabled = Boolean(activeController);
    });
    resizeComposer();
  }

  userQuery.addEventListener("input", updateComposerState);
  userQuery.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!sendButton.disabled) form.requestSubmit();
    }
  });

  function reveal(element, block = "nearest") {
    element.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block,
    });
  }

  function appendUserMessage(text) {
    const message = document.createElement("article");
    message.className = "maha-message maha-message--user";
    message.textContent = text;
    chatMain.appendChild(message);
    reveal(message);
    return message;
  }

  function appendLoading() {
    const message = document.createElement("article");
    message.className = "maha-message maha-message--assistant maha-message--loading";
    message.setAttribute("role", "status");
    const label = document.createElement("strong");
    const dots = document.createElement("span");
    dots.className = "maha-loading-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) dots.appendChild(document.createElement("i"));
    message.append(label, dots);
    chatMain.appendChild(message);

    let stage = 0;
    const updateStage = () => {
      const nextLabel = loadingStages[stage] || loadingStages[0] || "Searching…";
      label.textContent = nextLabel;
      setLiveStatus(nextLabel);
      stage = (stage + 1) % Math.max(loadingStages.length, 1);
    };
    updateStage();
    loadingTimer = window.setInterval(updateStage, 2200);
    reveal(message);
    return message;
  }

  function clearLoading() {
    if (loadingTimer !== null) window.clearInterval(loadingTimer);
    loadingTimer = null;
  }

  function appendAnswer(answer, references, kind, answerLanguage) {
    const message = document.createElement("article");
    message.className = "maha-message maha-message--assistant";
    message.dataset.responseKind = responseKinds.has(kind) ? kind : "evidence_answer";
    if (["en", "mr"].includes(answerLanguage)) message.lang = answerLanguage;

    const answerBody = document.createElement("div");
    answerBody.className = "maha-answer-body";
    appendFormattedAnswer(answerBody, String(answer || ""));
    message.appendChild(answerBody);

    const safeReferences = kind === "evidence_answer" && Array.isArray(references)
      ? references
      : [];
    if (safeReferences.length) appendSources(message, safeReferences);
    appendAnswerActions(message, String(answer || ""));
    chatMain.appendChild(message);
    setLiveStatus(messages.answer_ready || "Answer ready");
    reveal(message, "start");
  }

  function appendFormattedAnswer(parent, value) {
    const lines = value.replace(/\r\n?/gu, "\n").split("\n");
    let index = 0;
    let paragraph = [];
    let list = null;
    let listType = "";

    const flushParagraph = () => {
      if (!paragraph.length) return;
      const element = document.createElement("p");
      paragraph.forEach((line, lineIndex) => {
        if (lineIndex) element.appendChild(document.createElement("br"));
        appendInlineFormatting(element, line);
      });
      parent.appendChild(element);
      paragraph = [];
    };

    const closeList = () => {
      list = null;
      listType = "";
    };

    while (index < lines.length) {
      const line = lines[index].trim();
      if (!line) {
        flushParagraph();
        closeList();
        index += 1;
        continue;
      }

      if (isTableStart(lines, index)) {
        flushParagraph();
        closeList();
        index = appendTable(parent, lines, index);
        continue;
      }

      const headingMatch = line.match(/^(#{1,3})\s+(.+)$/u);
      if (headingMatch) {
        flushParagraph();
        closeList();
        const level = Math.min(Math.max(headingMatch[1].length, 2), 4);
        const heading = document.createElement(`h${level}`);
        appendInlineFormatting(heading, headingMatch[2]);
        parent.appendChild(heading);
        index += 1;
        continue;
      }

      const orderedMatch = line.match(/^(\d+)[.)]\s+(.+)$/u);
      const unorderedMatch = line.match(/^[\-*+•·◦▪▫➤➢⦿‣]\s+(.+)$/u);
      if (orderedMatch || unorderedMatch) {
        flushParagraph();
        const nextType = orderedMatch ? "ol" : "ul";
        if (!list || listType !== nextType) {
          list = document.createElement(nextType);
          parent.appendChild(list);
          listType = nextType;
        }
        const item = document.createElement("li");
        if (orderedMatch) {
          const itemNumber = Number(orderedMatch[1]);
          if (Number.isSafeInteger(itemNumber) && itemNumber > 0) item.value = itemNumber;
          appendInlineFormatting(item, orderedMatch[2]);
        } else {
          appendInlineFormatting(item, unorderedMatch[1]);
        }
        list.appendChild(item);
        index += 1;
        continue;
      }

      const quoteMatch = line.match(/^>\s*(.+)$/u);
      if (quoteMatch) {
        flushParagraph();
        closeList();
        const quote = document.createElement("blockquote");
        appendInlineFormatting(quote, quoteMatch[1]);
        parent.appendChild(quote);
        index += 1;
        continue;
      }

      closeList();
      paragraph.push(line);
      index += 1;
    }

    flushParagraph();
    if (!parent.childElementCount) parent.appendChild(document.createElement("p"));
  }

  function appendInlineFormatting(parent, value) {
    const tokenPattern = /(\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/giu;
    let cursor = 0;
    let match = tokenPattern.exec(value);
    while (match) {
      if (match.index > cursor) parent.appendChild(document.createTextNode(value.slice(cursor, match.index)));
      const token = match[0];
      if (token.startsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.appendChild(strong);
      } else {
        const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/iu);
        if (linkMatch && safeAnswerLinkUrl(linkMatch[2])) {
          const link = document.createElement("a");
          link.href = safeAnswerLinkUrl(linkMatch[2]);
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = linkMatch[1];
          parent.appendChild(link);
        } else if (linkMatch) {
          parent.appendChild(document.createTextNode(`${linkMatch[1]} (${linkMatch[2]})`));
        }
      }
      cursor = match.index + token.length;
      match = tokenPattern.exec(value);
    }
    if (cursor < value.length) parent.appendChild(document.createTextNode(value.slice(cursor)));
  }

  function safeAnswerLinkUrl(candidate) {
    try {
      const parsed = new URL(candidate, window.location.origin);
      const protectedPdf = /^\/pdf\/[1-9]\d*\/(?:public|view)\/$/u.test(parsed.pathname);
      return parsed.origin === window.location.origin && protectedPdf ? parsed.href : "";
    } catch (_error) {
      return "";
    }
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\||\|$/gu, "").split("|").map((cell) => cell.trim());
  }

  function isTableStart(lines, index) {
    if (index + 1 >= lines.length || !lines[index].includes("|")) return false;
    const separator = splitTableRow(lines[index + 1]);
    return separator.length > 1 && separator.every((cell) => /^:?-{3,}:?$/u.test(cell));
  }

  function appendTable(parent, lines, startIndex) {
    const headings = splitTableRow(lines[startIndex]);
    const wrap = document.createElement("div");
    wrap.className = "maha-answer-table-wrap";
    wrap.tabIndex = 0;
    const table = document.createElement("table");
    table.className = "maha-answer-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headings.forEach((value) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      appendInlineFormatting(cell, value);
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    let index = startIndex + 2;
    while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
      const row = document.createElement("tr");
      splitTableRow(lines[index]).slice(0, headings.length).forEach((value) => {
        const cell = document.createElement("td");
        appendInlineFormatting(cell, value);
        row.appendChild(cell);
      });
      body.appendChild(row);
      index += 1;
    }
    table.appendChild(body);
    wrap.appendChild(table);
    parent.appendChild(wrap);
    return index;
  }

  function appendSources(parent, references) {
    const details = document.createElement("details");
    details.className = "maha-sources";
    const summary = document.createElement("summary");
    summary.textContent = `${sourceDocumentsLabel} (${references.length})`;
    details.appendChild(summary);
    const list = document.createElement("div");
    list.className = "maha-sources__list";
    references.forEach((reference, index) => {
      const card = createReferenceCard(reference, index + 1);
      if (card) list.appendChild(card);
    });
    if (list.childElementCount) {
      details.appendChild(list);
      parent.appendChild(details);
    }
  }

  function createReferenceCard(reference, rank) {
    const href = safeReferenceUrl(reference);
    if (!href) return null;
    const card = document.createElement("a");
    card.className = "maha-source";
    card.href = href;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.setAttribute("aria-label", `${viewSourceLabel}: ${reference.title || sourceDocumentsLabel}`);
    card.addEventListener("click", () => {
      track("search_source_selected", {
        view: analyticsView,
        source_rank_bucket: analyticsCall("rankBucket", [rank], "4+"),
        answer_kind: "evidence_answer",
      });
    });

    const number = document.createElement("span");
    number.className = "maha-source__number";
    number.textContent = `[${rank}]`;
    number.setAttribute("aria-hidden", "true");
    const body = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = reference.title || sourceDocumentsLabel;
    body.appendChild(title);
    const metadata = [
      reference.folder,
      reference.page ? `${pageLabel} ${reference.page}` : "",
      reference.uploaded_at,
    ].filter(Boolean);
    if (metadata.length) {
      const small = document.createElement("small");
      small.textContent = metadata.join(" • ");
      body.appendChild(small);
    }
    const external = document.createElement("span");
    external.className = "maha-source__external";
    external.textContent = "↗";
    external.setAttribute("aria-hidden", "true");
    card.append(number, body, external);
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
      return parsed.origin === window.location.origin && protectedPdf ? parsed.href : "";
    } catch (_error) {
      return "";
    }
  }

  function appendAnswerActions(parent, answer) {
    const actions = document.createElement("div");
    actions.className = "maha-answer-actions";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "maha-answer-action";
    const label = document.createElement("span");
    label.textContent = copyLabel;
    copy.appendChild(label);
    copy.addEventListener("click", async () => {
      try {
        await copyText(cleanAnswerText(answer));
        label.textContent = copiedLabel;
        window.setTimeout(() => { label.textContent = copyLabel; }, 1800);
      } catch (_error) {
        label.textContent = messages.copy_failed || copyLabel;
      }
    });
    actions.appendChild(copy);

    const newQuestion = document.createElement("button");
    newQuestion.type = "button";
    newQuestion.className = "maha-answer-action";
    newQuestion.textContent = document.querySelector("[data-new-question] span")?.textContent || "New question";
    newQuestion.addEventListener("click", resetConversation);
    actions.appendChild(newQuestion);
    parent.appendChild(actions);
  }

  function cleanAnswerText(answer) {
    return answer
      .replace(/\*\*(.*?)\*\*/gu, "$1")
      .replace(/^#{1,3}\s+/gmu, "")
      .replace(/^\s*[-*]\s+/gmu, "• ");
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const fallback = document.createElement("textarea");
    fallback.value = value;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.appendChild(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    fallback.remove();
    if (!copied) throw new Error("Clipboard unavailable");
  }

  function classifiedFailure(status, payload) {
    if (status === 429 || payload?.error === "rate_limited") return ["rate_limited", "rate_limit"];
    if ([400, 401, 403].includes(status)) return ["invalid_request", "validation"];
    if (status === 503) return ["provider_unavailable", "provider"];
    return ["internal_error", "internal"];
  }

  function errorCopy(status, payload) {
    if (status === 429 || payload?.error === "rate_limited") {
      return [messages.search_unavailable || "Search unavailable", messages.rate_limited || messages.try_later || "Please try again later."];
    }
    if (payload?.error === "query_too_long") {
      return [messages.question_too_long || "Question is too long", payload.detail || wordLimitDetail];
    }
    if (status === 503 || payload?.error === "search_unavailable") {
      return [messages.search_unavailable || "Search unavailable", messages.unavailable || messages.try_later || "Please try again later."];
    }
    if (status === 401) return [messages.search_unavailable || "Search unavailable", messages.sign_in || "Please sign in to search."];
    if (status === 403) return [messages.search_unavailable || "Search unavailable", messages.security || "Refresh the page and try again."];
    return [messages.unexpected || "Something went wrong", messages.request_failed || messages.try_again || "Please try again."];
  }

  function appendError(title, detail, retryQuery, failureFamily) {
    const message = document.createElement("article");
    message.className = "maha-message maha-message--assistant maha-message--error";
    message.setAttribute("role", "alert");
    const heading = document.createElement("strong");
    heading.textContent = title;
    const explanation = document.createElement("p");
    explanation.textContent = detail;
    message.append(heading, explanation);
    if (retryQuery) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "maha-retry";
      retry.textContent = retryLabel;
      retry.addEventListener("click", () => {
        if (activeController) return;
        track("search_retry_clicked", {view: analyticsView, failure_family: failureFamily});
        userQuery.value = retryQuery;
        updateComposerState();
        form.requestSubmit();
      });
      message.appendChild(retry);
    }
    chatMain.appendChild(message);
    setLiveStatus(`${title}. ${detail}`);
    reveal(message);
  }

  function isValidSuccessPayload(payload) {
    if (!Boolean(
      payload
      && typeof payload === "object"
      && responseKinds.has(payload.kind)
      && typeof payload.answer === "string"
      && payload.answer.trim()
      && payload.answer.length <= 60000
      && Array.isArray(payload.references)
      && payload.references.length <= 25
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

  function resetConversation() {
    requestGeneration += 1;
    activeController?.abort();
    activeController = null;
    clearLoading();
    chatMain.replaceChildren(...initialConversation.map((node) => node.cloneNode(true)));
    chatMain.setAttribute("aria-busy", "false");
    userQuery.value = "";
    setLiveStatus("");
    updateComposerState();
    try {
      userQuery.focus({preventScroll: true});
    } catch (_error) {
      userQuery.focus();
    }
  }

  document.querySelectorAll("[data-new-question]").forEach((button) => {
    button.addEventListener("click", resetConversation);
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-help-link]")) {
      track("search_help_opened", {view: analyticsView});
    } else if (event.target.closest("[data-feedback-link]")) {
      track("search_feedback_opened", {view: analyticsView});
    }
  });

  async function submitSearch(event) {
    event.preventDefault();
    if (activeController) return;
    const query = userQuery.value.trim();
    const wordCount = wordsIn(query);
    if (!query) {
      userQuery.focus();
      return;
    }
    if (wordCount > maxWords) {
      const [title, detail] = errorCopy(400, {error: "query_too_long"});
      appendError(title, detail, "", "validation");
      return;
    }

    appendUserMessage(query);
    const startedAt = performance.now();
    const questionLanguage = analyticsCall("questionLanguage", [query, language], language);
    track("search_submitted", {
      view: analyticsView,
      question_language: questionLanguage,
      word_count_bucket: analyticsCall("wordCountBucket", [wordCount], "1-5"),
    });
    userQuery.value = "";
    const generation = ++requestGeneration;
    activeController = new AbortController();
    const requestController = activeController;
    chatMain.setAttribute("aria-busy", "true");
    updateComposerState();
    const loading = appendLoading();
    const timeout = window.setTimeout(() => requestController.abort(), 60000);

    try {
      const data = new FormData();
      data.set("query", query);
      data.set("language", language);
      const response = await fetch(form.action, {
        method: "POST",
        headers: {"X-CSRFToken": csrfToken},
        body: data,
        credentials: "same-origin",
        signal: requestController.signal,
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
      if (generation !== requestGeneration) return;

      loading.remove();
      clearLoading();
      if (!response.ok || payload?.error) {
        const [title, detail] = errorCopy(response.status, payload);
        const [outcome, failureFamily] = classifiedFailure(response.status, payload);
        trackCompleted({
          startedAt,
          wordCount,
          questionLanguage,
          outcome,
          answerKind: "error",
          failureFamily,
        });
        appendError(title, detail, response.status >= 500 || response.status === 429 ? query : "", failureFamily);
      } else if (!isValidSuccessPayload(payload)) {
        const [title, detail] = errorCopy(502, null);
        trackCompleted({
          startedAt,
          wordCount,
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
          wordCount,
          questionLanguage: payload.language,
          outcome,
          answerKind: payload.kind,
          failureFamily: "none",
          references: payload.references.length,
        });
        appendAnswer(payload.answer, payload.references, payload.kind, payload.language);
      }
    } catch (error) {
      if (generation !== requestGeneration) return;
      loading.remove();
      clearLoading();
      const timedOut = error?.name === "AbortError";
      trackCompleted({
        startedAt,
        wordCount,
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
      if (generation === requestGeneration) {
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
  }

  form.addEventListener("submit", submitSearch);
  updateComposerState();
})();
