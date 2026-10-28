import React from 'react';
import axios from 'axios';

// API Configuration
export const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
export const TOKEN_KEY = 'token';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

// Default headers for API requests
export const getAuthHeaders = () => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

// Shared client: every request carries the current token
export const api = axios.create({ baseURL: API_URL });

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Called when a request fails with 401 (expired/revoked token). AuthContext
// registers a handler that logs the user out; RequireAuth then shows /login.
// Requests made with { skipAuthRedirect: true } (login, session check) opt out.
let unauthorizedHandler = null;

export const setUnauthorizedHandler = (handler) => {
  unauthorizedHandler = handler;
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null;
  };
};

const sentAuthorization = (config) => {
  const headers = config?.headers;
  if (!headers) return undefined;
  return typeof headers.get === 'function' ? headers.get('Authorization') : headers.Authorization;
};

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const token = getToken();
    // Only end the session that actually failed: a slow request sent with an older
    // token (or before logout) must not sign out a session started since.
    const sentWithCurrentToken = Boolean(token) && sentAuthorization(error?.config) === `Bearer ${token}`;
    if (error?.response?.status === 401 && !error.config?.skipAuthRedirect && sentWithCurrentToken && unauthorizedHandler) {
      unauthorizedHandler(error);
    }
    return Promise.reject(error);
  }
);

// Human-readable message from an API error, preferring the backend's `detail`
// (a string, or FastAPI's list of validation errors for 422 responses).
export const getErrorMessage = (error, fallback = 'Something went wrong') => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
        const message = item?.msg || 'Invalid value';
        return field ? `${field}: ${message}` : message;
      })
      .join('; ');
  }
  if (error?.response) return `${fallback} (HTTP ${error.response.status})`;
  if (error?.request) return `Cannot reach the server at ${API_URL}. Is the backend running?`;
  return error?.message || fallback;
};

// Whether self-registration is open: undefined while the request is pending (show no
// sign-up controls yet), then true / false, or null if the request failed (older backends
// have no endpoint, so sign-up stays available and the backend still decides)
export const useRegistrationOpen = () => {
  const [open, setOpen] = React.useState(undefined);
  React.useEffect(() => {
    let cancelled = false;
    api.get('/auth/registration', { skipAuthRedirect: true })
      .then((r) => { if (!cancelled) setOpen(Boolean(r.data?.enabled)); })
      .catch(() => { if (!cancelled) setOpen(null); });
    return () => { cancelled = true; };
  }, []);
  return open;
};

// Download a report through the authenticated client (a plain link would not send the
// token). Resolves once the browser has been handed the file.
export const downloadFile = async (url, params, fallbackName) => {
  const response = await api.get(url, { params, responseType: 'blob' });
  const disposition = response.headers?.['content-disposition'] || '';
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const name = match ? match[1] : fallbackName;
  const href = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
  return name;
};

// Error from a blob request: the body is a Blob of JSON; read its `detail`
export const getBlobErrorMessage = async (error, fallback) => {
  const data = error?.response?.data;
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      return getErrorMessage({ ...error, response: { ...error.response, data: parsed } }, fallback);
    } catch (e) {
      return getErrorMessage({ ...error, response: { ...error.response, data: {} } }, fallback);
    }
  }
  return getErrorMessage(error, fallback);
};

const apiConfig = {
  API_URL,
  api,
  getAuthHeaders,
  getErrorMessage
};

export default apiConfig;
