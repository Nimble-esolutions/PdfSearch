(() => {
  "use strict";

  const notice = document.getElementById("cookieConsent");
  if (!notice) return;

  let accepted = false;
  try {
    accepted = window.localStorage.getItem("cookieConsent") === "accepted";
  } catch (_error) {
    accepted = false;
  }
  notice.hidden = accepted;

  const acceptButton = notice.querySelector("[data-cookie-accept]");
  if (!acceptButton) return;
  acceptButton.addEventListener("click", () => {
    notice.hidden = true;
    try {
      window.localStorage.setItem("cookieConsent", "accepted");
    } catch (_error) {
      // The notice can still be dismissed when storage is unavailable.
    }
  });
})();
