import { defineConfig, loadEnv } from 'vite';

// The API's Content-Security-Policy header only covers API responses.  The SPA
// document needs its own policy so that injected markup cannot run script.
// Applied to production builds only: the dev server relies on inline scripts.
function contentSecurityPolicy(apiOrigin) {
  const policy = [
    "default-src 'self'",
    "script-src 'self' https://ip.hzcat.com",
    // style.css @imports Noto Sans SC / Space Grotesk from Google Fonts.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' https: http: data: blob:",
    "media-src 'self' https: http:",
    "font-src 'self' data: https://fonts.gstatic.com",
    `connect-src 'self' ${apiOrigin}`.trim(),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
  return {
    name: 'mmcat-csp',
    apply: 'build',
    transformIndexHtml: () => [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: policy }, injectTo: 'head-prepend' }],
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  let apiOrigin = '';
  try {
    apiOrigin = new URL(env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').origin;
  } catch {
    // A relative API base (same origin) is already covered by 'self'.
  }
  return { plugins: [contentSecurityPolicy(apiOrigin)] };
});
