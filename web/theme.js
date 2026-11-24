(() => {
  const choices = new Map([
    ["charcoal", "Charcoal"],
    ["forest", "Forest"],
    ["navy", "Navy"],
    ["oxblood", "Oxblood"],
    ["petrol", "Petrol"],
    ["ultraviolet", "Ultraviolet"],
    ["ember", "Ember"],
    ["porcelain", "Porcelain"],
    ["matcha", "Matcha"],
    ["rose", "Rosé"],
  ]);
  window.clipFarmTheme = (value, save = false) => {
    const theme = choices.has(value) ? value : "charcoal";
    document.documentElement.dataset.theme = theme;
    const radio = document.querySelector(`input[name="theme"][value="${theme}"]`);
    if (radio) radio.checked = true;
    document.querySelector("#theme-button")?.setAttribute("aria-label", `Theme, ${choices.get(theme)} selected`);
    if (save) {
      try { localStorage.setItem("clipfarm-theme", theme); } catch {}
    }
  };

  try {
    const saved = localStorage.getItem("clipfarm-theme") || localStorage.getItem("framefold-theme");
    window.clipFarmTheme(saved, Boolean(saved));
  } catch {}
  addEventListener("storage", event => {
    if (event.key === "clipfarm-theme" || event.key === null) window.clipFarmTheme(event.newValue);
  });

  addEventListener("DOMContentLoaded", () => {
    const themeButton = document.querySelector("#theme-button");
    const picker = document.querySelector("#theme-picker");
    const options = document.querySelector("#theme-options");
    for (const [id, name] of choices) {
      const label = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "theme";
      radio.value = id;
      const preview = document.createElement("span");
      preview.className = "theme-swatch";
      preview.dataset.theme = id;
      preview.setAttribute("aria-hidden", "true");
      const title = document.createElement("span");
      title.textContent = name;
      label.append(preview, title, radio);
      options.append(label);
    }

    function showTheme(open, restoreFocus = false) {
      picker.hidden = !open;
      themeButton.setAttribute("aria-expanded", String(open));
      if (open) picker.querySelector(":checked").focus();
      else if (restoreFocus) themeButton.focus();
    }
    window.clipFarmTheme(document.documentElement.dataset.theme);
    themeButton.addEventListener("click", () => showTheme(picker.hidden, true));
    picker.addEventListener("change", event => window.clipFarmTheme(event.target.value, true));
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !picker.hidden) showTheme(false, true);
    });
    function dismissOutside(event) {
      if (!picker.hidden && !picker.contains(event.target) && !themeButton.contains(event.target)) showTheme(false);
    }
    document.addEventListener("click", dismissOutside);
    document.addEventListener("focusin", dismissOutside);
  });
})();
