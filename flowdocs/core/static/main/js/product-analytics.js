(() => {
  "use strict";

  const configElement = document.getElementById("product-analytics-config");
  if (!configElement) return;

  let config;
  try {
    config = JSON.parse(configElement.textContent);
  } catch (_error) {
    return;
  }

  const enumOf = values => value => values.includes(value);
  const boundedInteger = value => Number.isInteger(value) && value >= 1 && value <= 10;
  const schemas = Object.freeze({
    search_viewed: {
      view: enumOf(["classic", "workbench"]),
    },
    search_view_override_used: {
      from_view: enumOf(["classic", "workbench"]),
      to_view: enumOf(["classic", "workbench"]),
    },
    search_submitted: {
      view: enumOf(["classic", "workbench"]),
      question_language: enumOf(["en", "mr"]),
      word_count_bucket: enumOf(["1-5", "6-10", "11-20", "21-30"]),
    },
    search_completed: {
      view: enumOf(["classic", "workbench"]),
      question_language: enumOf(["en", "mr"]),
      word_count_bucket: enumOf(["1-5", "6-10", "11-20", "21-30"]),
      latency_bucket: enumOf(["<1s", "1-3s", "3-10s", "10-30s", ">=30s"]),
      reference_count_bucket: enumOf(["0", "1", "2-3", "4-5", "6+"]),
      outcome: enumOf([
        "evidence", "no_evidence", "conversational", "provider_timeout",
        "provider_unavailable", "rate_limited", "invalid_request", "internal_error",
      ]),
      answer_kind: enumOf(["evidence_answer", "no_evidence", "small_talk", "validation", "error"]),
      failure_family: enumOf(["none", "timeout", "network", "rate_limit", "validation", "provider", "contract", "internal"]),
    },
    search_retry_clicked: {
      view: enumOf(["classic", "workbench"]),
      failure_family: enumOf(["timeout", "network", "rate_limit", "provider", "contract", "internal"]),
    },
    search_sources_opened: {
      view: enumOf(["classic", "workbench"]),
      reference_count_bucket: enumOf(["1", "2-3", "4-5", "6+"]),
      answer_kind: enumOf(["evidence_answer"]),
    },
    search_source_selected: {
      view: enumOf(["classic", "workbench"]),
      source_rank_bucket: enumOf(["1", "2-3", "4+"]),
      answer_kind: enumOf(["evidence_answer"]),
    },
    search_share_started: {
      view: enumOf(["workbench"]),
      channel: enumOf(["native", "menu"]),
      answer_kind: enumOf(["evidence_answer"]),
    },
    search_feedback_opened: {
      view: enumOf(["classic", "workbench"]),
    },
    search_help_opened: {
      view: enumOf(["classic", "workbench"]),
    },
  });

  const sharedSchema = Object.freeze({
    schema_version: boundedInteger,
    deployment_tier: enumOf(["stage", "production"]),
    surface: enumOf(["classic", "workbench", "admin", "document_intake", "data_maintenance"]),
    ui_language: enumOf(["en", "mr"]),
    viewport_class: enumOf(["mobile", "tablet", "desktop"]),
    release_version: value => typeof value === "string" && /^[A-Za-z0-9._-]{0,64}$/u.test(value),
  });

  function viewportClass() {
    if (window.innerWidth < 768) return "mobile";
    if (window.innerWidth < 1200) return "tablet";
    return "desktop";
  }

  function validateProperties(schema, properties) {
    if (!properties || typeof properties !== "object" || Array.isArray(properties)) return null;
    const keys = Object.keys(properties);
    if (keys.length !== Object.keys(schema).length) return null;
    const clean = {};
    for (const [key, validator] of Object.entries(schema)) {
      if (!Object.prototype.hasOwnProperty.call(properties, key) || !validator(properties[key])) return null;
      clean[key] = properties[key];
    }
    return clean;
  }

  function sanitizeEvent(name, properties) {
    const schema = schemas[name];
    if (!schema || typeof name !== "string" || name.length > 50) return null;
    const eventData = validateProperties(schema, properties);
    if (!eventData) return null;
    const shared = validateProperties(sharedSchema, {
      schema_version: 1,
      deployment_tier: config.deployment_tier,
      surface: config.surface,
      ui_language: config.ui_language,
      viewport_class: viewportClass(),
      release_version: config.release_version || "",
    });
    return shared ? {...shared, ...eventData} : null;
  }

  function sanitizeTransportEvent(name, properties) {
    const eventSchema = schemas[name];
    if (!eventSchema || typeof name !== "string" || name.length > 50) return null;
    return validateProperties({...sharedSchema, ...eventSchema}, properties);
  }

  const queue = [];
  const MAX_QUEUE = 32;
  const dnt = String(navigator.doNotTrack || window.doNotTrack || "").toLowerCase();
  const privacyControlEnabled = ["1", "yes"].includes(dnt) || navigator.globalPrivacyControl === true;
  const domainAllowed = Array.isArray(config.allowed_domains)
    && config.allowed_domains.includes(window.location.hostname);
  const collectionAllowed = !privacyControlEnabled && domainAllowed;

  function send(name, data) {
    try {
      if (typeof window.umami?.track !== "function") return false;
      window.umami.track(name, data);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function track(name, properties) {
    if (!collectionAllowed) return false;
    const clean = sanitizeEvent(name, properties);
    if (!clean) return false;
    if (send(name, clean)) return true;
    if (queue.length >= MAX_QUEUE) queue.shift();
    queue.push([name, clean]);
    return true;
  }

  function flush() {
    while (queue.length) {
      const [name, data] = queue.shift();
      send(name, data);
    }
  }

  function beforeSend(type, payload) {
    if (!collectionAllowed || type !== "event" || !payload || payload.website !== config.website_id) return false;
    const clean = sanitizeTransportEvent(payload.name, payload.data);
    if (!clean) return false;
    return {
      website: config.website_id,
      hostname: window.location.hostname,
      language: config.ui_language,
      name: payload.name,
      url: `/product-events/${config.surface}`,
      data: clean,
    };
  }

  function wordCountBucket(count) {
    if (count <= 5) return "1-5";
    if (count <= 10) return "6-10";
    if (count <= 20) return "11-20";
    return "21-30";
  }

  function durationBucket(milliseconds) {
    if (milliseconds < 1000) return "<1s";
    if (milliseconds < 3000) return "1-3s";
    if (milliseconds < 10000) return "3-10s";
    if (milliseconds < 30000) return "10-30s";
    return ">=30s";
  }

  function countBucket(count) {
    if (count <= 0) return "0";
    if (count === 1) return "1";
    if (count <= 3) return "2-3";
    if (count <= 5) return "4-5";
    return "6+";
  }

  function rankBucket(rank) {
    if (rank <= 1) return "1";
    if (rank <= 3) return "2-3";
    return "4+";
  }

  function questionLanguage(text, fallback = "en") {
    const safeFallback = fallback === "mr" ? "mr" : "en";
    const value = String(text || "");
    const letters = value.match(/\p{L}/gu) || [];
    if (!letters.length) return safeFallback;
    const devanagari = value.match(/[\u0900-\u097f]/gu) || [];
    return devanagari.length / Math.max(value.length, 1) > 0.2 ? "mr" : "en";
  }

  window.aiSahakarAnalyticsBeforeSend = beforeSend;
  window.PdfSearchAnalytics = Object.freeze({
    track,
    beforeSend,
    wordCountBucket,
    durationBucket,
    countBucket,
    rankBucket,
    questionLanguage,
  });

  track("search_viewed", {view: config.surface});
  if (config.view_override_used) {
    track("search_view_override_used", {
      from_view: config.primary_surface,
      to_view: config.surface,
    });
  }

  if (!collectionAllowed) return;

  function loadTracker() {
    const script = document.createElement("script");
    script.async = true;
    script.src = config.script_url;
    script.dataset.websiteId = config.website_id;
    script.dataset.autoTrack = "false";
    script.dataset.doNotTrack = "true";
    script.dataset.domains = config.allowed_domains.join(",");
    script.dataset.beforeSend = "aiSahakarAnalyticsBeforeSend";
    script.dataset.excludeSearch = "true";
    script.dataset.excludeHash = "true";
    script.dataset.performance = "false";
    script.addEventListener("load", flush, {once: true});
    script.addEventListener("error", () => { queue.length = 0; }, {once: true});
    document.head.appendChild(script);
  }

  function scheduleTracker() {
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(loadTracker, {timeout: 1500});
    } else {
      window.setTimeout(loadTracker, 0);
    }
  }

  if (document.readyState === "complete") scheduleTracker();
  else window.addEventListener("load", scheduleTracker, {once: true});
})();
