// Runs before the app bundle: apply the remembered theme to the page background
// and preload only that theme's hero image (dark is the default).
(function () {
  var light = false;
  try {
    var value = window.localStorage.getItem('mmcat-lights-on');
    light = value === '1' || value === 'true';
  } catch (error) {
    // Storage blocked (private mode): fall back to the default dark theme.
  }
  var root = document.documentElement;
  if (light) root.setAttribute('data-theme', 'light');
  var theme = light ? 'light' : 'dark';
  var hints = document.querySelectorAll('link[rel="mmcat-hero"][data-theme="' + theme + '"]');
  for (var i = 0; i < hints.length; i += 1) {
    var link = document.createElement('link');
    link.rel = 'preload';
    link.as = 'image';
    link.href = hints[i].href;
    if (hints[i].media) link.media = hints[i].media;
    link.setAttribute('fetchpriority', 'high');
    document.head.appendChild(link);
  }
  var themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor && light) themeColor.content = '#eef6fb';
})();
