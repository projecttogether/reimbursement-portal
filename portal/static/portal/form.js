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
  function showError(message, fields = {}) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    for (const [name, errors] of Object.entries(fields)) {
      const target = document.getElementById(`error-${name}`);
      if (target)
        target.textContent = errors.map((error) => error.message).join(" ");
      form.elements.namedItem(name)?.setAttribute("aria-invalid", "true");
    }
    errorBox.focus();
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
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { "X-CSRFToken": csrf, ...options.headers },
    });
    const result = await response
      .json()
      .catch(() => ({ error: "Die Anfrage konnte nicht verarbeitet werden." }));
    if (!response.ok) {
      const error = new Error(
        result.error || "Die Anfrage ist fehlgeschlagen.",
      );
      error.fields = result.fields;
      error.status = response.status;
      throw error;
    }
    return result;
  }
  function money(amount, currency) {
    return new Intl.NumberFormat("de-DE", {
      style: "currency",
      currency,
    }).format(Number(amount) / 100);
  }
  function cents(value) {
    if (!/^\d{1,9}(?:[,.]\d{1,2})?$/.test(value.trim())) return null;
    const [whole, fraction = ""] = value.trim().replace(",", ".").split(".");
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  }
  function rowData(row) {
    return Object.fromEntries(
      [...row.element.querySelectorAll("[data-field]")].map((element) => [
        element.dataset.field,
        element.value,
      ]),
    );
  }
  function updateTotals() {
    const totals = {};
    const references = new Set();
    let duplicate = false;
    rows.forEach((row) => {
      const data = rowData(row),
        amount = cents(data.amount);
      if (amount !== null)
        totals[data.currency] = (totals[data.currency] || 0n) + amount;
      const reference = data.receipt_reference
        .trim()
        .toLocaleLowerCase("de-DE");
      if (reference && references.has(reference)) duplicate = true;
      references.add(reference);
    });
    const target = document.getElementById("totals");
    target.replaceChildren();
    for (const [currency, total] of Object.entries(totals)) {
      const line = node("div");
      line.append(
        node("span", "Summe", "total-caption"),
        node("span", money(total, currency)),
      );
      target.append(line);
    }
    if (!Object.keys(totals).length)
      target.append(node("span", money(0n, config.currencies[0])));
    if (duplicate)
      target.append(
        node(
          "p",
          "Hinweis: Eine Belegreferenz wird mehrfach verwendet. Bitte prüfe die Zuordnung.",
          "muted",
        ),
      );
    return totals;
  }
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
    document.getElementById("add-expense").disabled =
      rows.length >= config.max_expenses;
    updateTotals();
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
      list.append(item);
    });
  }
  async function uploadFile(blob, kind, expenseId, onUpdate) {
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
    onUpdate?.({ ...result, message: "Wird geprüft …" });
    for (let attempt = 0; attempt < 180; attempt++) {
      const status = await api(result.status_url);
      onUpdate?.(status);
      if (status.state === "ready") return { ...result, ...status };
      if (status.state === "failed") throw new Error(status.message);
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error(
      "Die Prüfung dauert länger. Bitte versuche es später erneut und behalte dieses Formular geöffnet.",
    );
  }
  function addRow() {
    if (rows.length >= config.max_expenses) return;
    const element = document
      .getElementById("expense-template")
      .content.firstElementChild.cloneNode(true);
    const row = { id: crypto.randomUUID(), files: [], element };
    element.querySelector('[data-field="receipt_reference"]').value = String(
      rows.length + 1,
    );
    const currency = element.querySelector('[data-field="currency"]');
    config.currencies.forEach((code) => {
      const option = node("option", code);
      option.value = code;
      currency.append(option);
    });
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
        if (selected.length + row.files.length > 3) {
          showError("Pro Position sind höchstens 3 Belegdateien erlaubt.");
          return;
        }
        const existingBytes = rows
          .flatMap((row) => row.files)
          .reduce((sum, file) => sum + file.size, 0);
        if (
          existingBytes + selected.reduce((sum, file) => sum + file.size, 0) >
          config.max_submission_bytes
        ) {
          showError("Alle Belege zusammen dürfen höchstens 50 MB groß sein.");
          return;
        }
        for (const selectedFile of selected) {
          if (!selectedFile.size || selectedFile.size > config.max_file_bytes) {
            showError(
              "Jede Belegdatei muss zwischen 1 Byte und 10 MB groß sein.",
            );
            continue;
          }
          const file = {
            name: selectedFile.name,
            size: selectedFile.size,
            state: "uploading",
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
          renderFiles(row);
        }
      });
    rows.push(row);
    arrangeRows();
  }
  document.getElementById("add-expense").addEventListener("click", addRow);
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
    document.getElementById("signature-file").value = "";
    signatureStatus.textContent =
      "Mit Finger, Stift oder Maus unterschreiben oder ein Bild hochladen. JPG/PNG, höchstens 1 MB.";
  }
  async function uploadSignature(blob) {
    try {
      signature = await uploadFile(blob, "signature", null, (update) => {
        if (update.id) signature = { ...signature, ...update };
      });
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
  document
    .getElementById("clear-signature")
    .addEventListener("click", () => signatureAction(clearSignature));
  document
    .getElementById("signature-file")
    .addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 1000000 || !file.size) {
        showError("Die Unterschrift darf höchstens 1 MB groß sein.");
        return;
      }
      await signatureAction(async () => {
        await clearSignature();
        signatureStatus.textContent =
          "Unterschrift wird hochgeladen und geprüft …";
        await uploadSignature(file);
        signatureStatus.textContent = `Unterschrift geprüft: ${file.name}`;
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
          !row.files.length || row.files.some((file) => file.state !== "ready"),
      )
    ) {
      showError(
        "Jede Position benötigt mindestens einen vollständig geprüften Beleg.",
      );
      return;
    }
    if (
      rows.some((row) => {
        const amount = cents(rowData(row).amount);
        return amount === null || amount <= 0n;
      })
    ) {
      showError("Bitte gib für jede Position einen positiven Betrag ein.");
      return;
    }
    if (
      rows
        .flatMap((row) => row.files)
        .reduce((sum, file) => sum + (file.pages || 0), 0) > 100
    ) {
      showError("Alle Belege zusammen dürfen höchstens 100 Seiten enthalten.");
      return;
    }
    const content = document.getElementById("review-content");
    content.replaceChildren();
    const descriptions = node("dl");
    for (const [name, label] of [
      ["full_name", "Name"],
      ["email", "E-Mail"],
      ["postal_address", "Anschrift"],
      ["iban", "IBAN"],
      ["purpose", "Erläuterung"],
      ["trip_location", "Reiseort"],
      ["trip_start", "Reisebeginn"],
      ["trip_end", "Reiseende"],
    ]) {
      const value = form.elements.namedItem(name)?.value;
      if (value) descriptions.append(node("dt", label), node("dd", value));
    }
    content.append(descriptions);
    rows.forEach((row, index) => {
      const data = rowData(row),
        item = node("div", undefined, "review-expense");
      item.append(
        node(
          "strong",
          `${index + 1}. ${data.merchant} · ${money(cents(data.amount), data.currency)}`,
        ),
        node("p", `${data.purpose} · Referenz ${data.receipt_reference}`),
        node("small", row.files.map((file) => file.name).join(" · ")),
      );
      content.append(item);
    });
    for (const [currency, total] of Object.entries(updateTotals()))
      content.append(node("p", `Summe: ${money(total, currency)}`, "totals"));
    details.hidden = true;
    review.hidden = false;
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
    if (!hasInk && signature?.state !== "ready") {
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
      if (!signature || signature.state !== "ready") {
        await discardSignature();
        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/png"),
        );
        await uploadSignature(blob);
      }
      const data = {
        draft_id: config.draft_id,
        token: config.token,
        config_version: config.config_version,
        signature: signature.id,
        declaration: true,
        privacy_acknowledged: true,
        website: form.elements.website.value,
      };
      for (const name of [
        "full_name",
        "email",
        "postal_address",
        "iban",
        "purpose",
        "trip_location",
        "trip_start",
        "trip_end",
      ])
        data[name] = form.elements.namedItem(name)?.value || "";
      data.expenses = rows.map((row) => ({
        id: row.id,
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
      showError(error.message, error.fields);
      submitting = false;
      review
        .querySelectorAll("button,input")
        .forEach((element) => (element.disabled = false));
      button.textContent = "Erstattung einreichen →";
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
