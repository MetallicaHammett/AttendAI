// Test helper: replaces the shared axios client's adapter so tests exercise the
// real interceptors (auth header, 401 handling) without any network access.
import { AxiosError } from 'axios';
import { api } from '../data';

const NETWORK = Symbol('network');
export const networkError = () => NETWORK;

// routes: { 'GET /auth/me': { status: 200, data: {...} } | (config) => result }
// A result may be { status, data } or networkError(). Unmatched requests fail like a
// network error and are recorded in `unmatched`.
export const installMockApi = (routes = {}) => {
  const calls = [];
  const unmatched = [];
  const original = api.defaults.adapter;

  api.defaults.adapter = async (config) => {
    const method = (config.method || 'get').toUpperCase();
    const path = (config.url || '').split('?')[0];
    calls.push({ method, path, url: config.url, headers: config.headers, data: config.data, params: config.params });

    const handler = routes[`${method} ${path}`];
    if (!handler) {
      unmatched.push(`${method} ${path}`);
      throw new AxiosError(`No mock for ${method} ${path}`, AxiosError.ERR_NETWORK, config, {});
    }
    // A handler may return a result or a promise of one (to hold a request open)
    const result = await (typeof handler === 'function' ? handler(config) : handler);
    if (result === NETWORK) {
      throw new AxiosError('Network Error', AxiosError.ERR_NETWORK, config, {});
    }
    const response = { data: result.data, status: result.status ?? 200, statusText: '', headers: {}, config, request: {} };
    if (response.status >= 400) {
      throw new AxiosError(`Request failed with status code ${response.status}`, AxiosError.ERR_BAD_REQUEST,
        config, {}, response);
    }
    return response;
  };

  return {
    calls,
    unmatched,
    restore: () => { api.defaults.adapter = original; },
    callsTo: (method, path) => calls.filter((c) => c.method === method && c.path === path)
  };
};

export const headerOf = (call, name) =>
  (call.headers && typeof call.headers.get === 'function' ? call.headers.get(name) : call.headers?.[name]) || undefined;

const base64url = (obj) =>
  btoa(JSON.stringify(obj)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');

// Unsigned JWT-shaped token; the frontend only decodes it to read `exp`
export const makeToken = ({ sub = 'admin', expiresInSeconds = 3600 } = {}) =>
  `${base64url({ alg: 'HS256', typ: 'JWT' })}.${base64url({ sub, exp: Math.floor(Date.now() / 1000) + expiresInSeconds })}.signature`;

export const ME = { id: 1, email: 'admin@example.com', username: 'admin', is_active: true, created_at: '2026-10-19T08:00:00Z' };
