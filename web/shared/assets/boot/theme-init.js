// Applies the saved light/dark preference before first paint, so light-mode users never see a dark
// flash while Angular boots. Mirrors ThemeService (key 'esb.theme', default dark). A file rather than
// an inline script: the Content-Security-Policy only allows scripts from 'self'.
(function () {
  try {
    var mode = localStorage.getItem('esb.theme') || 'dark';
    var dark = mode === 'system' ? matchMedia('(prefers-color-scheme: dark)').matches : mode !== 'light';
    var root = document.documentElement;
    root.classList.toggle('theme-dark', dark);
    root.classList.toggle('theme-light', !dark);
  } catch (e) {
    /* storage unavailable: keep the default dark class */
  }
})();
