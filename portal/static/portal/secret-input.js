// The eye button of a secret field (admin/portal/secret_input.html): shows the value as
// text, and hides it again. One listener serves every secret field on the page.
(() => {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-secret-toggle]");
    const input = button?.closest("[data-secret-input]")?.querySelector("input");
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    const label = show ? "Hide" : "Show";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.querySelector("[data-secret-show]").hidden = show;
    button.querySelector("[data-secret-hide]").hidden = !show;
  });
})();
