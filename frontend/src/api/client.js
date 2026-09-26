const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '');

function requestId() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
  const csrfToken = document.cookie
    .split('; ')
    .find((item) => item.startsWith('mmcat-csrf='))
    ?.split('=')[1];
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: 'include',
    signal,
    headers: {
      Accept: 'application/json',
      'X-Request-ID': requestId(),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(csrfToken ? { 'X-CSRF-Token': decodeURIComponent(csrfToken) } : {}),
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

export const authApi = {
  me: () => api('/auth/me'),
  login: (body) => api('/auth/login', { method: 'POST', body }),
  register: (body) => api('/auth/register', { method: 'POST', body }),
  logout: () => api('/auth/logout', { method: 'POST' }),
};

export const contentApi = {
  home: (locale = 'zh-CN') => api(`/home?locale=${locale}`),
  posts: (params = {}) => {
    const search = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
    return api(`/posts${search.size ? `?${search}` : ''}`);
  },
  post: (slug, locale = 'zh-CN') => api(`/posts/${encodeURIComponent(slug)}?locale=${locale}`),
  site: (locale = 'zh-CN') => api(`/site?locale=${locale}`),
};

export const studioApi = {
  dashboard: () => api('/studio/dashboard'),
  posts: (params = {}) => {
    const search = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ''));
    return api(`/studio/posts${search.size ? `?${search}` : ''}`);
  },
  createPost: (body) => api('/studio/posts', { method: 'POST', body }),
  post: (id) => api(`/studio/posts/${id}`),
  updatePost: (id, body) => api(`/studio/posts/${id}`, { method: 'PATCH', body }),
  deletePost: (id) => api(`/studio/posts/${id}`, { method: 'DELETE' }),
  categories: () => api('/studio/categories'),
  createCategory: (body) => api('/studio/categories', { method: 'POST', body }),
  updateCategory: (id, body) => api(`/studio/categories/${id}`, { method: 'PATCH', body }),
  deleteCategory: (id) => api(`/studio/categories/${id}`, { method: 'DELETE' }),
  tags: () => api('/studio/tags'),
  createTag: (body) => api('/studio/tags', { method: 'POST', body }),
  updateTag: (id, body) => api(`/studio/tags/${id}`, { method: 'PATCH', body }),
  deleteTag: (id) => api(`/studio/tags/${id}`, { method: 'DELETE' }),
  pages: () => api('/studio/pages'),
  page: (id) => api(`/studio/pages/${id}`),
  createPage: (body) => api('/studio/pages', { method: 'POST', body }),
  updatePage: (id, body) => api(`/studio/pages/${id}`, { method: 'PUT', body }),
  deletePage: (id) => api(`/studio/pages/${id}`, { method: 'DELETE' }),
  tools: () => api('/studio/tools'),
  createTool: (body) => api('/studio/tools', { method: 'POST', body }),
  updateTool: (id, body) => api(`/studio/tools/${id}`, { method: 'PUT', body }),
  deleteTool: (id) => api(`/studio/tools/${id}`, { method: 'DELETE' }),
  comments: (status = '') => api(`/studio/comments${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  moderateComment: (id, body) => api(`/studio/comments/${id}`, { method: 'PATCH', body }),
  siteSettings: () => api('/studio/settings/site'),
  updateSiteSettings: (body) => api('/studio/settings/site', { method: 'PATCH', body }),
};
