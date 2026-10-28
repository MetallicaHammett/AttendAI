import { api, getErrorMessage, setUnauthorizedHandler, API_URL } from './data';
import { installMockApi, headerOf, networkError } from './testUtils/mockApi';

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

describe('shared API client', () => {
  test('sends the stored token as a Bearer header', async () => {
    localStorage.setItem('token', 'abc.def.ghi');
    mock = installMockApi({ 'GET /students/': { data: [] } });
    await api.get('/students/');
    expect(headerOf(mock.calls[0], 'Authorization')).toBe('Bearer abc.def.ghi');
  });

  test('sends no Authorization header without a token', async () => {
    mock = installMockApi({ 'GET /health': { data: { status: 'healthy' } } });
    await api.get('/health');
    expect(headerOf(mock.calls[0], 'Authorization')).toBeUndefined();
  });

  test('uses the configured backend URL (port 8000 by default)', () => {
    expect(api.defaults.baseURL).toBe(API_URL);
    expect(API_URL).toMatch(/:8000$/);
  });

  test('a 401 calls the unauthorized handler', async () => {
    localStorage.setItem('token', 'current.token.value');
    const handler = jest.fn();
    const unregister = setUnauthorizedHandler(handler);
    mock = installMockApi({ 'GET /students/': { status: 401, data: { detail: 'Could not validate credentials' } } });
    await expect(api.get('/students/')).rejects.toMatchObject({ response: { status: 401 } });
    expect(handler).toHaveBeenCalledTimes(1);
    unregister();
  });

  test('a 401 for a request sent with an older token does not end the new session', async () => {
    localStorage.setItem('token', 'old.token.value');
    const handler = jest.fn();
    const unregister = setUnauthorizedHandler(handler);
    let release;
    mock = installMockApi({
      'GET /students/': () => new Promise((resolve) => { release = resolve; })
    });
    // Slow request leaves with the old token...
    const pending = api.get('/students/').catch((e) => e);
    await new Promise((r) => setTimeout(r, 0));
    // ...the user signs in again before it returns 401
    localStorage.setItem('token', 'new.token.value');
    release({ status: 401, data: { detail: 'Could not validate credentials' } });
    const error = await pending;
    expect(error.response.status).toBe(401);
    expect(handler).not.toHaveBeenCalled();
    unregister();
  });

  test('a 401 after logout (no token) does not call the handler again', async () => {
    const handler = jest.fn();
    const unregister = setUnauthorizedHandler(handler);
    mock = installMockApi({ 'GET /students/': { status: 401, data: {} } });
    await expect(api.get('/students/')).rejects.toBeTruthy();
    expect(handler).not.toHaveBeenCalled();
    unregister();
  });

  test('skipAuthRedirect requests and non-401 errors do not trigger it', async () => {
    localStorage.setItem('token', 'current.token.value');
    const handler = jest.fn();
    const unregister = setUnauthorizedHandler(handler);
    mock = installMockApi({
      'POST /auth/login': { status: 401, data: { detail: 'Incorrect username or password' } },
      'GET /cameras/': { status: 403, data: { detail: 'Not authenticated' } },
      'GET /dashboard/': { status: 500, data: {} }
    });
    await expect(api.post('/auth/login', 'x', { skipAuthRedirect: true })).rejects.toBeTruthy();
    await expect(api.get('/cameras/')).rejects.toBeTruthy();
    await expect(api.get('/dashboard/')).rejects.toBeTruthy();
    expect(handler).not.toHaveBeenCalled();
    unregister();
  });

  test('unregistering removes the handler', async () => {
    localStorage.setItem('token', 'current.token.value');
    const handler = jest.fn();
    setUnauthorizedHandler(handler)();
    mock = installMockApi({ 'GET /students/': { status: 401, data: {} } });
    await expect(api.get('/students/')).rejects.toBeTruthy();
    expect(handler).not.toHaveBeenCalled();
  });
});

describe('getErrorMessage', () => {
  const httpError = (status, data) => ({ response: { status, data }, request: {} });

  test('uses a string detail from the backend', () => {
    expect(getErrorMessage(httpError(503, { detail: "Camera 'Door' could not be opened: ..." })))
      .toBe("Camera 'Door' could not be opened: ...");
  });

  test('formats FastAPI 422 validation lists with field names', () => {
    const detail = [
      { loc: ['body', 'camera_type'], msg: 'Field required', type: 'missing' },
      { loc: ['query', 'limit'], msg: 'Input should be greater than or equal to 1' }
    ];
    expect(getErrorMessage(httpError(422, { detail }))).toBe(
      'camera_type: Field required; query.limit: Input should be greater than or equal to 1'
    );
  });

  test('falls back to the status code when there is no detail', () => {
    expect(getErrorMessage(httpError(500, {}), 'Failed to load')).toBe('Failed to load (HTTP 500)');
  });

  test('reports an unreachable backend', async () => {
    mock = installMockApi({ 'GET /health': () => networkError() });
    const error = await api.get('/health').catch((e) => e);
    expect(getErrorMessage(error)).toBe(`Cannot reach the server at ${API_URL}. Is the backend running?`);
  });

  test('uses the plain error message otherwise', () => {
    expect(getErrorMessage(new Error('boom'))).toBe('boom');
  });
});
