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
    paused: "Der Versand ist verzögert. Dein Antrag bleibt gespeichert.",
    send_unknown:
      "Der Versandstatus wird geprüft. Dein Antrag bleibt gespeichert; bitte reiche ihn nicht erneut ein.",
    delivery_failed:
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
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${window.location.pathname}?format=json`, {
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("status_unavailable");
      const result = await response.json();
      show(result.state);
      if (result.final) return;
    } catch {
      target.textContent =
        "Der Status ist gerade nicht erreichbar. Dein Antrag bleibt gespeichert; bitte reiche ihn nicht erneut ein.";
    } finally {
      clearTimeout(deadline);
    }
    if (++polls < 120) {
      const delay = document.hidden
        ? 30000
        : Math.min(30000, 5000 * 1.1 ** polls);
      setTimeout(refresh, delay + Math.random() * 1000);
    } else {
      target.textContent +=
        " Lade diese Seite später neu, um den aktuellen Status zu prüfen.";
    }
  }
  if (target.dataset.final !== "true") setTimeout(refresh, 3000);
})();
