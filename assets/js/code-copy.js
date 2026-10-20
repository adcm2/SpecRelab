(() => {
  document.documentElement.classList.add("has-code-copy");
  const status = document.createElement("div");
  status.className = "copy-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  document.body.append(status);

  const fallbackCopy = (code) => {
    const selection = window.getSelection();
    const previous = [];
    if (selection) {
      for (let i = 0; i < selection.rangeCount; i += 1) previous.push(selection.getRangeAt(i).cloneRange());
      const range = document.createRange();
      range.selectNodeContents(code);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
    if (selection && copied) {
      selection.removeAllRanges();
      previous.forEach((range) => selection.addRange(range));
    }
    return copied;
  };

  document.querySelectorAll("pre > code").forEach((code) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "code-copy-button";
    button.setAttribute("aria-label", "Copy code");
    button.title = "Copy code";
    button.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="6" width="10" height="12" rx="1.5"></rect><path d="M13 6V3.5A1.5 1.5 0 0 0 11.5 2h-8A1.5 1.5 0 0 0 2 3.5v10A1.5 1.5 0 0 0 3.5 15H7"></path></svg>';
    button.addEventListener("click", async () => {
      let copied = false;
      if (navigator.clipboard && window.isSecureContext) {
        try { await navigator.clipboard.writeText(code.textContent); copied = true; } catch (_) { copied = false; }
      }
      if (!copied) copied = fallbackCopy(code);
      if (copied) {
        button.dataset.state = "copied";
        button.setAttribute("aria-label", "Copied");
        button.title = "Copied";
        status.textContent = "Code copied to clipboard.";
        window.setTimeout(() => {
          delete button.dataset.state;
          button.setAttribute("aria-label", "Copy code");
          button.title = "Copy code";
        }, 1800);
      } else {
        button.dataset.state = "selected";
        button.setAttribute("aria-label", "Code selected; press Control+C or Command+C to copy");
        button.title = "Code selected; press Control+C or Command+C to copy";
        status.textContent = "Clipboard access is unavailable. The code is selected; press Control+C or Command+C to copy it.";
      }
    });
    code.parentElement.append(button);
  });
})();
