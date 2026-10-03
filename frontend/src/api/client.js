const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '');
export const rssFeedUrl = `${API_BASE_URL}/rss.xml`;
const publicLocale = () => window.localStorage.getItem('mmcat-locale') || 'zh-CN';

function requestId() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// The cookie is the durable source.  The value returned by login covers
// deployments where the API host's cookie is not readable from this origin
// until CSRF_COOKIE_DOMAIN is configured (it does not survive a reload).
let sessionCsrfToken = null;
function readCsrfToken() {
  const cookie = document.cookie
    .split('; ')
    .find((item) => item.startsWith('mmcat-csrf='))
    ?.split('=')[1];
  return cookie ? decodeURIComponent(cookie) : sessionCsrfToken;
}

// Every public screen mounts its own shell, so the same navigation, profile
// and home requests used to be repeated on each route change.  Share one
// in-flight/recent response instead.
const responseCache = new Map();
const CACHE_TTL_MS = 30_000;
function cached(key, load, { keepErrors = false } = {}) {
  const hit = responseCache.get(key);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.promise;
  const promise = load();
  const entry = { at: Date.now(), promise };
  responseCache.set(key, entry);
  if (!keepErrors) promise.catch(() => responseCache.get(key) === entry && responseCache.delete(key));
  return promise;
}
export function clearApiCache() {
  responseCache.clear();
}

function withQuery(path, params = {}) {
  const search = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ''));
  return `${path}${search.size ? `?${search}` : ''}`;
}

export class ApiError extends Error {
  constructor(code, message, status, fields = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.fields = fields;
  }
}

export async function api(path, { method = 'GET', body, headers = {}, signal } = {}) {
  const csrfToken = readCsrfToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: 'include',
    signal,
    headers: {
      Accept: 'application/json',
      'X-Request-ID': requestId(),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload?.error;
    throw new ApiError(error?.code || 'NETWORK_ERROR', error?.message || '请求失败，请稍后重试。', response.status, error?.fields);
  }
  return payload;
}

async function upload(path, file, { galleryId } = {}) {
  const csrfToken = readCsrfToken();
  const query = new URLSearchParams({ filename: file.name, mime_type: file.type || 'application/octet-stream' });
  if (galleryId) query.set('gallery_id', galleryId);
  const response = await fetch(`${API_BASE_URL}${path}?${query}`, {
    method: 'POST',
    credentials: 'include',
    body: file,
    headers: {
      Accept: 'application/json',
      'Content-Type': file.type || 'application/octet-stream',
      'X-Request-ID': requestId(),
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload?.error;
    throw new ApiError(error?.code || 'UPLOAD_FAILED', error?.message || '附件上传失败，请稍后重试。', response.status, error?.fields);
  }
  return payload;
}

export const authApi = {
  // "Not signed in" is cached as well, so anonymous visitors do not re-ask on every screen.
  me: () => cached('me', () => api('/auth/me'), { keepErrors: true }),
  login: async (body) => {
    const result = await api('/auth/login', { method: 'POST', body });
    sessionCsrfToken = result?.data?.csrfToken || null;
    clearApiCache();
    return result;
  },
  register: (body) => api('/auth/register', { method: 'POST', body }),
  verifyEmail: (token) => api('/auth/verify-email', { method: 'POST', body: { token } }),
  logout: async () => {
    await api('/auth/logout', { method: 'POST' });
    sessionCsrfToken = null;
    clearApiCache();
  },
};

export const contentApi = {
  home: (locale = publicLocale()) => cached(`home:${locale}`, () => api(`/home?locale=${locale}`)),
  tools: (locale = publicLocale()) => api(`/tools?locale=${locale}`),
  tags: (locale = publicLocale()) => api(`/tags?locale=${locale}`),
  navigation: (locale = publicLocale()) => cached(`navigation:${locale}`, () => api(`/navigation?locale=${locale}`)),
  posts: (params = {}, options) => api(withQuery(`/posts`, { locale: publicLocale(), ...params }), options),
  post: (slug, locale = publicLocale()) => api(`/posts/${encodeURIComponent(slug)}?locale=${locale}`),
  comments: (params = {}) => api(withQuery(`/comments`, params)),
  createComment: (body) => api('/comments', { method: 'POST', body }),
  site: (locale = publicLocale()) => cached(`site:${locale}`, () => api(`/site?locale=${locale}`)),
  resolveRedirect: (path) => api(withQuery('/redirects/resolve', { path })),
  page: (slug, locale = publicLocale()) => api(`/pages/${encodeURIComponent(slug)}?locale=${locale}`),
  friendLinks: (locale = publicLocale()) => api(`/friend-links?locale=${locale}`),
  galleries: (locale = publicLocale()) => api(`/galleries?locale=${locale}`),
  gallery: (slug, params = {}) => api(withQuery(`/galleries/${encodeURIComponent(slug)}`, { locale: publicLocale(), ...params })),
};

export const studioApi = {
  dashboard: () => api('/studio/dashboard'),
  posts: (params = {}) => api(withQuery('/studio/posts', params)),
  createPost: (body) => api('/studio/posts', { method: 'POST', body }),
  post: (id) => api(`/studio/posts/${id}`),
  updatePost: (id, body) => api(`/studio/posts/${id}`, { method: 'PATCH', body }),
  deletePost: (id) => api(`/studio/posts/${id}`, { method: 'DELETE' }),
  restorePost: (id) => api(`/studio/posts/${id}/restore`, { method: 'POST' }),
  categories: () => api('/studio/categories'),
  createCategory: (body) => api('/studio/categories', { method: 'POST', body }),
  updateCategory: (id, body) => api(`/studio/categories/${id}`, { method: 'PATCH', body }),
  deleteCategory: (id) => api(`/studio/categories/${id}`, { method: 'DELETE' }),
  tags: () => api('/studio/tags'),
  createTag: (body) => api('/studio/tags', { method: 'POST', body }),
  updateTag: (id, body) => api(`/studio/tags/${id}`, { method: 'PATCH', body }),
  deleteTag: (id) => api(`/studio/tags/${id}`, { method: 'DELETE' }),
  pages: (params = {}) => api(withQuery(`/studio/pages`, params)),
  page: (id) => api(`/studio/pages/${id}`),
  createPage: (body) => api('/studio/pages', { method: 'POST', body }),
  updatePage: (id, body) => api(`/studio/pages/${id}`, { method: 'PUT', body }),
  deletePage: (id) => api(`/studio/pages/${id}`, { method: 'DELETE' }),
  restorePage: (id) => api(`/studio/pages/${id}/restore`, { method: 'POST' }),
  tools: () => api('/studio/tools'),
  createTool: (body) => api('/studio/tools', { method: 'POST', body }),
  updateTool: (id, body) => api(`/studio/tools/${id}`, { method: 'PUT', body }),
  deleteTool: (id) => api(`/studio/tools/${id}`, { method: 'DELETE' }),
  navigation: () => api('/studio/navigation'),
  navigationTargets: () => api('/studio/navigation/targets'),
  navigationMenus: () => api('/studio/navigation/menus'),
  createNavigationMenu: (body) => api('/studio/navigation/menus', { method: 'POST', body }),
  deleteNavigationMenu: (id) => api(`/studio/navigation/menus/${id}`, { method: 'DELETE' }),
  createNavigation: (body) => api('/studio/navigation', { method: 'POST', body }),
  updateNavigation: (id, body) => api(`/studio/navigation/${id}`, { method: 'PUT', body }),
  deleteNavigation: (id) => api(`/studio/navigation/${id}`, { method: 'DELETE' }),
  statusNodes: () => api('/studio/status-nodes'),
  createStatusNode: (body) => api('/studio/status-nodes', { method: 'POST', body }),
  updateStatusNode: (id, body) => api(`/studio/status-nodes/${id}`, { method: 'PUT', body }),
  deleteStatusNode: (id) => api(`/studio/status-nodes/${id}`, { method: 'DELETE' }),
  comments: (params = {}) =>
    api(withQuery(`/studio/comments`, Object.fromEntries(Object.entries(params).filter(([, value]) => value !== 'all')))),
  moderateComment: (id, body) => api(`/studio/comments/${id}`, { method: 'PATCH', body }),
  replyComment: (id, body) => api(`/studio/comments/${id}/reply`, { method: 'POST', body }),
  friendLinks: () => api('/studio/friend-links'),
  createFriendLink: (body) => api('/studio/friend-links', { method: 'POST', body }),
  updateFriendLink: (id, body) => api(`/studio/friend-links/${id}`, { method: 'PUT', body }),
  deleteFriendLink: (id) => api(`/studio/friend-links/${id}`, { method: 'DELETE' }),
  media: (params = {}) => api(withQuery(`/studio/media`, params)),
  updateMedia: (id, body) => api(`/studio/media/${id}`, { method: 'PATCH', body }),
  deleteMedia: (id) => api(`/studio/media/${id}`, { method: 'DELETE' }),
  uploadMedia: (file, options = {}) => upload('/studio/media/upload', file, options),
  galleries: () => api('/studio/galleries'),
  mediaGroups: () => api('/studio/media-groups'),
  gallery: (id) => api(`/studio/galleries/${id}`),
  createGallery: (body) => api('/studio/galleries', { method: 'POST', body }),
  updateGallery: (id, body) => api(`/studio/galleries/${id}`, { method: 'PUT', body }),
  deleteGallery: (id) => api(`/studio/galleries/${id}`, { method: 'DELETE' }),
  users: () => api('/studio/users'),
  updateUser: (id, body) => api(`/studio/users/${id}`, { method: 'PATCH', body }),
  auditLogs: (params = {}) => api(withQuery(`/studio/audit-logs`, params)),
  siteSettings: () => api('/studio/settings/site'),
  updateSiteSettings: (body) => api('/studio/settings/site', { method: 'PATCH', body }),
};
