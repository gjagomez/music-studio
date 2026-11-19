// Apply the saved preference before painting, then connect the day/night controls.
(() => {
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let saved = null;
  try { saved = localStorage.getItem('alma-theme'); } catch {}
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      const selected = button.dataset.themeChoice === theme;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  }
  apply(saved === 'light' || saved === 'dark' ? saved : system.matches ? 'dark' : 'light');
  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => {
        saved = button.dataset.themeChoice;
        try { localStorage.setItem('alma-theme', saved); } catch {}
        apply(saved);
      });
    });
  });
  system.addEventListener('change', event => {
    if (!saved) apply(event.matches ? 'dark' : 'light');
  });
})();
