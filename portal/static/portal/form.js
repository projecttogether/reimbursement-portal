"use strict";
(() => {
  const config = JSON.parse(
    document.getElementById("portal-configuration").textContent,
  );
  const form = document.getElementById("claim-form");
  const details = document.getElementById("details-panel");
  const review = document.getElementById("review-panel");
  const expensesContainer = document.getElementById("expenses");
  const errorBox = document.getElementById("form-error");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const canvas = document.getElementById("signature-canvas");
  const context = canvas.getContext("2d");
  const signatureStatus = document.getElementById("signature-status");
  const rows = [];
  const tripFields = document.getElementById("trip-fields");
  const isTrip = form.elements.namedItem("is_trip");
  let signature = null,
    signatureBusy = false,
    hasInk = false,
    drawing = false,
    submitting = false,
    accepted = false;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  // The named input, or the first radio button of a group.
  function fieldElement(name) {
    const element = form.elements.namedItem(name);
    return element instanceof RadioNodeList ? element[0] : element;
  }
  function showError(message, fields = {}) {
    const messages = Object.values(fields)
      .flat()
      .map((error) => error.message);
    errorBox.textContent = [message, ...messages].join(" ");
    errorBox.hidden = false;
    for (const [name, errors] of Object.entries(fields)) {
      const target = document.getElementById(`error-${name}`);
      if (target)
        target.textContent = errors.map((error) => error.message).join(" ");
      fieldElement(name)?.setAttribute("aria-invalid", "true");
    }
    const invalid = form.querySelector('[aria-invalid="true"]');
    if (invalid && invalid.getClientRects().length) invalid.focus();
    else errorBox.focus();
  }
  function clearErrors() {
    errorBox.hidden = true;
    document
      .querySelectorAll(".field-error")
      .forEach((element) => (element.textContent = ""));
    document
      .querySelectorAll("[aria-invalid]")
      .forEach((element) => element.removeAttribute("aria-invalid"));
  }
  async function api(url, options = {}) {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 15000);
    let response, result;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        signal: controller.signal,
        headers: { "X-CSRFToken": csrf, ...options.headers },
      });
      result = await response.json();
    } catch {
      if (response && !response.ok) {
        result = {
          error: [404, 410].includes(response.status)
            ? "Die Sitzung oder Datei ist nicht mehr verfügbar. Bitte öffne das Formular erneut."
            : "Die Anfrage konnte nicht verarbeitet werden.",
        };
      } else {
        const error = new Error(
          "Die Verbindung wurde unterbrochen. Bitte versuche es erneut.",
        );
        error.retryable = true;
        throw error;
      }
    } finally {
      clearTimeout(deadline);
    }
    if (!response.ok) {
      const error = new Error(
        result.error || "Die Anfrage ist fehlgeschlagen.",
      );
      error.fields = result.fields;
      error.status = response.status;
      error.retryable =
        response.status === 408 ||
        response.status === 429 ||
        response.status >= 500;
      throw error;
    }
    return result;
  }
  const amountFormat = new RegExp(`^(?:${config.amount_pattern})$`);
  function money(cents) {
    return new Intl.NumberFormat("de-DE", {
      style: "currency",
      currency: "EUR",
    }).format(Number(cents) / 100);
  }
  // An exact EUR amount in cents, or null when the server would reject it.
  function cents(value) {
    if (!amountFormat.test(value.trim())) return null;
    const [whole, fraction = ""] = value.trim().replace(",", ".").split(".");
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0") || "0");
  }
  const rateCents = config.mileage_rate ? cents(config.mileage_rate) : null;
  function rowData(row) {
    return Object.fromEntries(
      [...row.element.querySelectorAll("[data-field]")].map((element) => [
        element.dataset.field,
        element.type === "checkbox" ? element.checked : element.value,
      ]),
    );
  }
  // The row's amount in cents; mileage is distance × rate, as on the server.
  function rowAmount(row) {
    const data = rowData(row);
    if (row.kind !== "mileage") return cents(data.amount);
    const distance = data.distance_km.trim();
    if (!/^\d{1,5}$/.test(distance)) return null;
    const kilometres = BigInt(distance);
    if (kilometres < 1n || kilometres > BigInt(config.max_distance_km))
      return null;
    return kilometres * (data.round_trip ? 2n : 1n) * rateCents;
  }
  function updateTotals() {
    let total = 0n;
    const references = new Set();
    let duplicate = false;
    rows.forEach((row) => {
      const data = rowData(row),
        amount = rowAmount(row);
      if (amount !== null) total += amount;
      if (row.kind === "mileage") {
        row.element.querySelector(".mileage-amount").textContent =
          amount === null
            ? ""
            : `${data.round_trip ? Number(data.distance_km) * 2 : data.distance_km} km × ${money(rateCents)} = ${money(amount)}`;
        return;
      }
      const reference = data.receipt_reference
        .trim()
        .toLocaleLowerCase("de-DE");
      if (reference && references.has(reference)) duplicate = true;
      references.add(reference);
    });
    const target = document.getElementById("totals");
    target.replaceChildren();
    const line = node("div");
    line.append(node("span", "Summe", "total-caption"), node("span", money(total)));
    target.append(line);
    if (duplicate)
      target.append(
        node(
          "p",
          "Hinweis: Eine Belegreferenz wird mehrfach verwendet. Bitte prüfe die Zuordnung.",
          "muted",
        ),
      );
    return total;
  }
  function updateTravel() {
    if (!tripFields) return;
    const hasMileage = rows.some((row) => row.kind === "mileage");
    if (isTrip) {
      if (hasMileage) isTrip.checked = true;
      isTrip.disabled = hasMileage;
    }
    const needed = config.travel === "required" || hasMileage || isTrip?.checked;
    tripFields.hidden = !needed;
    for (const name of ["trip_destination", "trip_start", "trip_end"])
      form.elements.namedItem(name).required = needed;
  }
  isTrip?.addEventListener("change", updateTravel);
  const fullName = form.elements.namedItem("full_name");
  const accountHolder = form.elements.namedItem("account_holder");
  let accountHolderEdited = false;
  fullName.addEventListener("input", () => {
    if (!accountHolderEdited) accountHolder.value = fullName.value;
  });
  accountHolder.addEventListener("input", () => {
    accountHolderEdited = accountHolder.value !== "";
  });
  function arrangeRows() {
    rows.forEach((row, index) => {
      expensesContainer.append(row.element);
      row.element.querySelector(".row-number").textContent = index + 1;
      row.element.querySelector('[data-action="up"]').disabled = index === 0;
      row.element.querySelector('[data-action="down"]').disabled =
        index === rows.length - 1;
      row.element.querySelector('[data-action="remove"]').disabled =
        rows.length === 1;
    });
    for (const id of ["add-expense", "add-mileage"]) {
      const button = document.getElementById(id);
      if (button) button.disabled = rows.length >= config.limits.expenses;
    }
    updateTotals();
    updateTravel();
  }
  function renderFiles(row) {
    const list = row.element.querySelector(".file-list");
    list.replaceChildren();
    row.files.forEach((file, index) => {
      const item = node("li");
      item.append(
        node("span", file.name, "file-name"),
        node(
          "span",
          file.message ||
            {
              ready: "Geprüft",
              pending: "Wird geprüft …",
              uploading: "Wird hochgeladen …",
              failed: "Prüfung fehlgeschlagen",
            }[file.state],
          `file-status ${file.state}`,
        ),
      );
      for (const [label, delta] of [
        ["↑", -1],
        ["↓", 1],
      ]) {
        const button = node("button", label);
        button.type = "button";
        button.setAttribute(
          "aria-label",
          `Beleg ${delta === -1 ? "nach oben" : "nach unten"} verschieben`,
        );
        button.disabled =
          index + delta < 0 || index + delta >= row.files.length;
        button.addEventListener("click", () => {
          [row.files[index], row.files[index + delta]] = [
            row.files[index + delta],
            row.files[index],
          ];
          renderFiles(row);
        });
        item.append(button);
      }
      const remove = node("button", "Entfernen");
      remove.type = "button";
      remove.disabled = file.state === "uploading";
      remove.addEventListener("click", async () => {
        try {
          if (file.delete_url) await api(file.delete_url, { method: "DELETE" });
          row.files.splice(row.files.indexOf(file), 1);
          renderFiles(row);
        } catch (error) {
          showError(error.message);
        }
      });
      item.append(remove);
      if (file.state === "pending" && !file.polling) {
        const retry = node("button", "Status erneut prüfen");
        retry.type = "button";
        retry.addEventListener("click", async () => {
          file.polling = true;
          renderFiles(row);
          try {
            Object.assign(
              file,
              await pollUpload(file, (update) => {
                Object.assign(file, update);
                renderFiles(row);
              }),
            );
          } catch (error) {
            file.state = "failed";
            file.message = error.message;
          } finally {
            file.polling = false;
            renderFiles(row);
          }
        });
        item.append(retry);
      }
      list.append(item);
    });
  }
  async function uploadFile(blob, kind, expenseId, onUpdate) {
    if (config.preview)
      throw new Error("Vorschau: Hochladen ist nicht möglich.");
    const data = new FormData();
    for (const [key, value] of Object.entries({
      draft_id: config.draft_id,
      token: config.token,
      kind,
      expense_id: expenseId || "",
    }))
      data.append(key, value);
    data.append("file", blob, blob.name || "signature.png");
    const result = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", config.upload_url);
      xhr.setRequestHeader("X-CSRFToken", csrf);
      xhr.timeout = 120000;
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable)
          onUpdate?.({
            state: "uploading",
            message: `Upload ${Math.round((event.loaded / event.total) * 100)} %`,
          });
      });
      xhr.onload = () => {
        let value;
        try {
          value = JSON.parse(xhr.responseText);
        } catch {
          reject(
            new Error(
              "Der Upload ist fehlgeschlagen. Bitte versuche es erneut.",
            ),
          );
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(value);
        else reject(new Error(value.error || "Der Upload ist fehlgeschlagen."));
      };
      xhr.onerror = xhr.ontimeout = () =>
        reject(
          new Error("Der Upload wurde unterbrochen. Bitte versuche es erneut."),
        );
      xhr.send(data);
    });
    return pollUpload(result, onUpdate);
  }
  async function pollUpload(result, onUpdate) {
    onUpdate?.({ ...result, state: "pending", message: "Wird geprüft …" });
    for (let attempt = 0; attempt < 36; attempt++) {
      let status;
      try {
        status = await api(result.status_url);
      } catch (error) {
        if (!error.retryable) throw error;
        status = {
          state: "pending",
          message:
            "Die Verbindung ist verzögert. Die Prüfung bleibt gespeichert.",
        };
      }
      onUpdate?.(status);
      if (status.state === "ready") return { ...result, ...status };
      if (status.state === "failed") throw new Error(status.message);
      const delay = Math.min(10000, 2000 * 1.4 ** attempt);
      await new Promise((resolve) =>
        setTimeout(resolve, delay + Math.random() * delay * 0.2),
      );
    }
    const delayed = {
      ...result,
      state: "pending",
      message:
        "Die Prüfung dauert länger. Bitte prüfe den Status später erneut; die Datei bleibt gespeichert.",
    };
    onUpdate?.(delayed);
    return delayed;
  }
  function addRow(kind = "receipt") {
    if (rows.length >= config.limits.expenses) return;
    const element = document
      .getElementById(kind === "mileage" ? "mileage-template" : "expense-template")
      .content.firstElementChild.cloneNode(true);
    const row = { id: crypto.randomUUID(), kind, files: [], element };
    element.addEventListener("input", updateTotals);
    element.querySelectorAll("[data-action]").forEach((button) =>
      button.addEventListener("click", () => {
        const index = rows.indexOf(row);
        if (button.dataset.action === "remove") {
          if (rows.length === 1) return;
          rows.splice(index, 1);
          element.remove();
          for (const file of row.files)
            if (file.delete_url)
              api(file.delete_url, { method: "DELETE" }).catch(() => {});
        } else {
          const other = index + (button.dataset.action === "up" ? -1 : 1);
          if (other < 0 || other >= rows.length) return;
          [rows[index], rows[other]] = [rows[other], rows[index]];
        }
        arrangeRows();
      }),
    );
    element
      .querySelector(".receipt-input")
      .addEventListener("change", async (event) => {
        const selected = [...event.target.files];
        event.target.value = "";
        if (
          selected.length + row.files.length >
          config.limits.files_per_expense
        ) {
          showError(config.messages.files_per_expense_exceeded);
          return;
        }
        const existingBytes = rows
          .flatMap((row) => row.files)
          .reduce((sum, file) => sum + file.size, 0);
        if (
          existingBytes + selected.reduce((sum, file) => sum + file.size, 0) >
          config.limits.submission_bytes
        ) {
          showError(config.messages.submission_too_large);
          return;
        }
        for (const selectedFile of selected) {
          if (
            !selectedFile.size ||
            selectedFile.size > config.limits.receipt_bytes
          ) {
            showError(config.messages.receipt_size);
            continue;
          }
          const file = {
            name: selectedFile.name,
            size: selectedFile.size,
            state: "uploading",
            polling: true,
          };
          row.files.push(file);
          renderFiles(row);
          try {
            Object.assign(
              file,
              await uploadFile(selectedFile, "receipt", row.id, (update) => {
                Object.assign(file, update);
                renderFiles(row);
              }),
            );
          } catch (error) {
            file.state = "failed";
            file.message = error.message;
          }
          file.polling = false;
          renderFiles(row);
        }
      });
    rows.push(row);
    arrangeRows();
  }
  document
    .getElementById("add-expense")
    .addEventListener("click", () => addRow("receipt"));
  document
    .getElementById("add-mileage")
    ?.addEventListener("click", () => addRow("mileage"));
  addRow();

  async function discardSignature() {
    if (signature?.delete_url) {
      try {
        await api(signature.delete_url, { method: "DELETE" });
      } catch (error) {
        // A previous successful deletion may have lost its response.
        if (error.status !== 404) throw error;
      }
    }
    signature = null;
  }
  async function clearSignature() {
    await discardSignature();
    context.clearRect(0, 0, canvas.width, canvas.height);
    hasInk = false;
    document.getElementById("retry-signature").hidden = true;
    document.getElementById("signature-file").value = "";
    signatureStatus.textContent = config.messages.signature_hint;
  }
  async function uploadSignature(blob) {
    try {
      signature = await uploadFile(blob, "signature", null, (update) => {
        if (update.id) signature = { ...signature, ...update };
      });
      document.getElementById("retry-signature").hidden =
        signature.state !== "pending";
    } catch (error) {
      // Retain the deletion handle even if validation or polling fails.
      if (signature) signature.state = "failed";
      throw error;
    }
  }
  async function signatureAction(action) {
    if (signatureBusy || submitting) return false;
    clearErrors();
    signatureBusy = true;
    const controls = [
      "signature-file",
      "clear-signature",
      "edit-button",
      "submit-button",
      "retry-signature",
    ];
    controls.forEach((id) => (document.getElementById(id).disabled = true));
    try {
      await action();
      return true;
    } catch (error) {
      showError(error.message);
      signatureStatus.textContent =
        "Bitte lösche die Unterschrift oder versuche es erneut.";
      return false;
    } finally {
      signatureBusy = false;
      document.getElementById("retry-signature").hidden =
        signature?.state !== "pending";
      controls.forEach((id) => (document.getElementById(id).disabled = false));
    }
  }
  function position(event) {
    const bounds = canvas.getBoundingClientRect();
    return [
      ((event.clientX - bounds.left) * canvas.width) / bounds.width,
      ((event.clientY - bounds.top) * canvas.height) / bounds.height,
    ];
  }
  canvas.addEventListener("pointerdown", (event) => {
    if (submitting || signatureBusy) return;
    if (signature) {
      if (!hasInk) context.clearRect(0, 0, canvas.width, canvas.height);
      // Delete the previous server record before uploading the new drawing.
      signature.state = "obsolete";
      document.getElementById("retry-signature").hidden = true;
    }
    canvas.setPointerCapture(event.pointerId);
    const [x, y] = position(event);
    context.beginPath();
    context.moveTo(x, y);
    context.lineWidth = 3;
    context.lineCap = "round";
    context.strokeStyle = "#00115a";
    drawing = true;
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    const [x, y] = position(event);
    context.lineTo(x, y);
    context.stroke();
    hasInk = true;
    signatureStatus.textContent = "Unterschrift gezeichnet.";
  });
  canvas.addEventListener("pointerup", () => (drawing = false));
  canvas.addEventListener("pointercancel", () => (drawing = false));
  document.getElementById("retry-signature").addEventListener("click", () =>
    signatureAction(async () => {
      if (signature?.state !== "pending" || !signature.status_url) return;
      signature = await pollUpload(signature, (update) => {
        signature = { ...signature, ...update };
        signatureStatus.textContent =
          update.message || "Unterschrift wird geprüft …";
      });
      document.getElementById("retry-signature").hidden =
        signature.state !== "pending";
      signatureStatus.textContent =
        signature.state === "ready"
          ? "Unterschrift geprüft."
          : signature.message;
    }),
  );
  document
    .getElementById("clear-signature")
    .addEventListener("click", () => signatureAction(clearSignature));
  document
    .getElementById("signature-file")
    .addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > config.limits.signature_bytes || !file.size) {
        showError(config.messages.signature_too_large);
        return;
      }
      await signatureAction(async () => {
        await clearSignature();
        signatureStatus.textContent =
          "Unterschrift wird hochgeladen und geprüft …";
        await uploadSignature(file);
        signatureStatus.textContent =
          signature.state === "ready"
            ? `Unterschrift geprüft: ${file.name}`
            : signature.message;
        const image = await createImageBitmap(file);
        const scale = Math.min(
          canvas.width / image.width,
          canvas.height / image.height,
        );
        context.drawImage(
          image,
          (canvas.width - image.width * scale) / 2,
          (canvas.height - image.height * scale) / 2,
          image.width * scale,
          image.height * scale,
        );
        image.close();
      });
    });
  document.getElementById("review-button").addEventListener("click", () => {
    clearErrors();
    for (const element of details.querySelectorAll("input,textarea,select"))
      if (!element.checkValidity()) {
        element.reportValidity();
        return;
      }
    if (
      rows.some(
        (row) =>
          (row.kind !== "mileage" && !row.files.length) ||
          row.files.some((file) => file.state !== "ready"),
      )
    ) {
      showError(
        "Jede Auslage benötigt mindestens einen Beleg, und alle Dateien müssen geprüft sein.",
      );
      return;
    }
    if (
      rows.some((row) => {
        const amount = rowAmount(row);
        return amount === null || amount <= 0n;
      })
    ) {
      showError(
        "Bitte gib für jede Position einen positiven Betrag in Euro bzw. eine Entfernung in ganzen Kilometern ein.",
      );
      return;
    }
    const iban = form.elements.namedItem("iban").value.replace(/\s/g, "");
    if (
      iban.length > 2 &&
      !config.sepa_countries.includes(iban.slice(0, 2).toUpperCase()) &&
      !form.elements.namedItem("bic").value.trim()
    ) {
      showError("Bitte prüfe die markierten Angaben.", {
        bic: [
          {
            message:
              "Für Konten außerhalb des SEPA-Raums ist die BIC erforderlich.",
          },
        ],
      });
      return;
    }
    if (
      rows
        .flatMap((row) => row.files)
        .reduce((sum, file) => sum + (file.pages || 0), 0) >
      config.limits.claim_pages
    ) {
      showError(config.messages.claim_pages);
      return;
    }
    const content = document.getElementById("review-content");
    content.replaceChildren();
    const descriptions = node("dl");
    const travel = tripFields && !tripFields.hidden;
    for (const [name, label] of [
      ["full_name", "Name"],
      ["email", "E-Mail"],
      ["street", "Straße und Hausnummer"],
      ["postal_code", "PLZ"],
      ["city", "Ort"],
      ["country", "Land"],
      ["account_holder", "Kontoinhaber:in"],
      ["iban", "IBAN"],
      ["bic", "BIC"],
      ["purpose", "Erläuterung"],
      ["purpose_details", "Ergänzung"],
      ["trip_destination", "Zielort"],
      ["trip_start", "Reisebeginn"],
      ["trip_end", "Reiseende"],
    ]) {
      if (name.startsWith("trip_") && !travel) continue;
      const element = form.elements.namedItem(name);
      const value =
        element instanceof HTMLSelectElement
          ? element.selectedOptions[0]?.text
          : element?.type === "date"
            ? element.value.split("-").reverse().join(".")
            : element?.value;
      if (value) descriptions.append(node("dt", label), node("dd", value));
    }
    content.append(descriptions);
    rows.forEach((row, index) => {
      const data = rowData(row),
        item = node("div", undefined, "review-expense");
      const amount = money(rowAmount(row));
      if (row.kind === "mileage")
        item.append(
          node(
            "p",
            `${index + 1}. Fahrt ${data.route_from} – ${data.route_to}${data.round_trip ? " und zurück" : ""} · ${amount}`,
            "review-title",
          ),
          node(
            "p",
            `${data.travel_date.split("-").reverse().join(".")} · ${data.purpose}`,
          ),
        );
      else
        item.append(
          node("p", `${index + 1}. ${data.merchant} · ${amount}`, "review-title"),
          node(
            "p",
            data.receipt_reference.trim()
              ? `${data.purpose} · Referenz ${data.receipt_reference}`
              : data.purpose,
          ),
        );
      if (row.files.length)
        item.append(node("small", row.files.map((file) => file.name).join(" · ")));
      content.append(item);
    });
    content.append(node("p", `Summe: ${money(updateTotals())}`, "totals"));
    details.hidden = true;
    review.hidden = false;
    review.querySelector("h2").focus();
    document.getElementById("step-details").classList.remove("active");
    document.getElementById("step-details").removeAttribute("aria-current");
    document.getElementById("step-review").classList.add("active");
    document.getElementById("step-review").setAttribute("aria-current", "step");
    review.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("edit-button").addEventListener("click", async () => {
    clearErrors();
    if (!(await signatureAction(clearSignature))) return;
    form.elements.declaration.checked = false;
    form.elements.privacy_acknowledged.checked = false;
    details.hidden = false;
    review.hidden = true;
    document.getElementById("step-details").classList.add("active");
    document
      .getElementById("step-details")
      .setAttribute("aria-current", "step");
    document.getElementById("step-review").classList.remove("active");
    document.getElementById("step-review").removeAttribute("aria-current");
    details.scrollIntoView({ behavior: "smooth" });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || signatureBusy || review.hidden) return;
    clearErrors();
    if (
      !form.elements.declaration.checked ||
      !form.elements.privacy_acknowledged.checked
    ) {
      showError("Bitte bestätige die Erklärung und den Datenschutzhinweis.");
      return;
    }
    if (!hasInk && !["ready", "pending"].includes(signature?.state)) {
      showError("Bitte unterschreibe oder lade eine Unterschrift hoch.");
      return;
    }
    submitting = true;
    const button = document.getElementById("submit-button");
    button.textContent = "Wird eingereicht …";
    review
      .querySelectorAll("button,input")
      .forEach((element) => (element.disabled = true));
    try {
      if (signature?.state === "pending") {
        signature = await pollUpload(signature, (update) => {
          signature = { ...signature, ...update };
          signatureStatus.textContent =
            update.message || "Unterschrift wird geprüft …";
        });
        document.getElementById("retry-signature").hidden =
          signature.state !== "pending";
        if (signature.state !== "ready") throw new Error(signature.message);
      }
      if (!signature || signature.state !== "ready") {
        await discardSignature();
        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/png"),
        );
        await uploadSignature(blob);
        if (signature.state !== "ready") throw new Error(signature.message);
      }
      const data = {
        draft_id: config.draft_id,
        token: config.token,
        revision: config.revision,
        signature: signature.id,
        declaration: true,
        privacy_acknowledged: true,
        is_trip: Boolean(tripFields && !tripFields.hidden),
        website: form.elements.website.value,
      };
      for (const name of [
        "full_name",
        "email",
        "street",
        "postal_code",
        "city",
        "country",
        "account_holder",
        "iban",
        "bic",
        "purpose",
        "purpose_details",
        "trip_destination",
        "trip_start",
        "trip_end",
      ])
        data[name] = form.elements.namedItem(name)?.value || "";
      data.expenses = rows.map((row) => ({
        id: row.id,
        kind: row.kind,
        ...rowData(row),
        uploads: row.files.map((file) => file.id),
      }));
      const result = await api(config.submit_url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      accepted = true;
      window.location.assign(result.receipt_url);
    } catch (error) {
      if (error.fields && Object.keys(error.fields).length) {
        try {
          await clearSignature();
        } catch {
          if (signature) signature.state = "obsolete";
          context.clearRect(0, 0, canvas.width, canvas.height);
          hasInk = false;
        }
        form.elements.declaration.checked = false;
        form.elements.privacy_acknowledged.checked = false;
        details.hidden = false;
        review.hidden = true;
        document.getElementById("step-details").classList.add("active");
        document
          .getElementById("step-details")
          .setAttribute("aria-current", "step");
        document.getElementById("step-review").classList.remove("active");
        document.getElementById("step-review").removeAttribute("aria-current");
      }
      showError(error.message, error.fields);
      submitting = false;
      review
        .querySelectorAll("button,input")
        .forEach((element) => (element.disabled = false));
      button.textContent = "Antrag einreichen →";
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (
      !accepted &&
      (rows.some((row) => row.files.length) || form.elements.full_name.value)
    ) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
