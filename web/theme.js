(() => {
  const choices = ["charcoal", "forest", "navy"];
  window.clipFarmTheme = (value, save = false) => {
    const theme = choices.includes(value) ? value : "charcoal";
    document.documentElement.dataset.theme = theme;
    const radio = document.querySelector(`input[name="theme"][value="${theme}"]`);
    if (radio) radio.checked = true;
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
})();

addEventListener("DOMContentLoaded", () => {
  const themeButton = document.querySelector("#theme-button");
  const picker = document.querySelector("#theme-picker");
  function showTheme(open, focus = false) {
    picker.hidden = !open;
    themeButton.setAttribute("aria-expanded", String(open));
    if (open) picker.querySelector(":checked")?.focus();
    else if (focus) themeButton.focus();
  }
  window.clipFarmTheme(document.documentElement.dataset.theme);
  themeButton.addEventListener("click", () => showTheme(picker.hidden, true));
  picker.addEventListener("change", event => window.clipFarmTheme(event.target.value, true));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !picker.hidden) showTheme(false, true);
  });
  document.addEventListener("click", event => {
    if (!picker.hidden && !picker.contains(event.target) && !themeButton.contains(event.target)) showTheme(false);
  });
});
