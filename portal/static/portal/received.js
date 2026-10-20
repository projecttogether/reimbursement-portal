"use strict";
(() => {
  const target = document.getElementById("processing-status");
  const messages = {
    received: "Dein Antrag wartet auf die PDF-Erstellung.",
    rendering: "Deine PDF wird erstellt.",
    ready: "Deine PDF ist fertig und wartet auf den Versand.",
    pending: "Deine PDF wartet auf den Versand.",
    sending: "Deine PDF wird versendet.",
    provider_accepted: "Die E-Mail wurde vom Versanddienst angenommen.",
    delivered:
      "Deine PDF wurde an den Mailserver des Finance-Postfachs zugestellt.",
    purged: "Die vorübergehend gespeicherten Antragsdaten wurden gelöscht.",
    paused: "Der Versand ist verzögert. Dein Antrag bleibt gespeichert.",
    send_unknown:
      "Der Versandstatus wird geprüft. Dein Antrag bleibt gespeichert; bitte reiche ihn nicht erneut ein.",
    failed:
      "Beim Versand ist ein Problem aufgetreten. Bitte kontaktiere uns mit deiner Referenz.",
    processing_failed:
      "Bei der PDF-Erstellung ist ein Problem aufgetreten. Bitte kontaktiere uns mit deiner Referenz.",
  };
  function show(state) {
    target.textContent = messages[state] || "Dein Antrag wird verarbeitet.";
  }
  show(target.dataset.status);
  let polls = 0;
  async function refresh() {
    try {
      const response = await fetch(`${window.location.pathname}?format=json`, {
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const result = await response.json();
      show(result.state);
      if (
        ["delivered", "purged", "failed", "processing_failed"].includes(
          result.state,
        )
      )
        return;
    } catch {
      /* The saved reference remains visible through connection failures. */
    }
    if (++polls < 120) setTimeout(refresh, 5000);
  }
  setTimeout(refresh, 3000);
})();
