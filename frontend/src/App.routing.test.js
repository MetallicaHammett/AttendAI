import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AppRoutes } from './App';
import { AuthContextProvider } from './context/AuthContext';
import { api } from './data';
import { installMockApi, headerOf, makeToken, networkError, ME } from './testUtils/mockApi';

// Pages are replaced by markers: these tests cover routing and auth, not page content.
// (jest.mock factories are hoisted, so each one builds its marker inline.)
jest.mock('./pages/Dashboard', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Dashboard page') }));
jest.mock('./pages/StudentsPage', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Students page') }));
jest.mock('./pages/CamerasPage', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Cameras page') }));
jest.mock('./pages/AttendancePage', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Attendance page') }));
jest.mock('./pages/LiveMonitoringPage', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Live Monitoring page') }));

const HEALTHY = { 'GET /health': { data: { status: 'healthy' } } };

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const LocationDisplay = () => {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</div>;
};

const renderAt = (path) =>
  render(
    <AuthContextProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
        <LocationDisplay />
      </MemoryRouter>
    </AuthContextProvider>
  );

const signedIn = (extraRoutes = {}) => {
  const token = makeToken();
  localStorage.setItem('token', token);
  mock = installMockApi({ ...HEALTHY, 'GET /auth/me': { data: ME }, ...extraRoutes });
  return token;
};

describe('protected routes', () => {
  test('signed-out users are sent to login instead of a blank page', async () => {
    mock = installMockApi(HEALTHY);
    renderAt('/dashboard');
    expect(await screen.findByText('Sign In to AttendAI')).toBeInTheDocument();
    expect(screen.queryByText('Dashboard page')).not.toBeInTheDocument();
  });

  test.each([
    ['/dashboard', 'Dashboard page'],
    ['/students', 'Students page'],
    ['/cameras', 'Cameras page'],
    ['/attendance', 'Attendance page'],
    ['/live-monitoring', 'Live Monitoring page']
  ])('regression: %s renders its page for a signed-in user', async (path, marker) => {
    signedIn();
    renderAt(path);
    // RequireAuth used to return `children` (undefined) as a layout route -> blank page
    expect(await screen.findByText(marker)).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  test('the session check sends the stored token', async () => {
    const token = signedIn();
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    expect(headerOf(mock.callsTo('GET', '/auth/me')[0], 'Authorization')).toBe(`Bearer ${token}`);
  });

  test.each(['/', '/no-such-page'])('%s goes to the dashboard when signed in', async (path) => {
    signedIn();
    renderAt(path);
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument();
  });

  test('navigation links route between pages', async () => {
    signedIn();
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    // Setup pages are folded under "Setup" until opened
    expect(screen.queryByRole('link', { name: 'Cameras' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Setup/ }));
    fireEvent.click(await screen.findByRole('link', { name: 'Cameras' }));
    expect(await screen.findByText('Cameras page')).toBeInTheDocument();
    // The current page keeps its section open
    expect(screen.getByRole('link', { name: 'Cameras' })).toHaveAttribute('aria-current', 'page');
  });

  test('everyday pages are always in the menu; setup pages open on demand and stay open on their page', async () => {
    signedIn({ 'GET /auth/me': { data: { ...ME, role: 'admin' } } });
    renderAt('/cameras');
    expect(await screen.findByText('Cameras page')).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    for (const name of ['Dashboard', 'Live Monitoring', 'Attendance', 'Reports', 'Students', 'Cameras', 'Timetable', 'Users']) {
      expect(within(nav).getByRole('link', { name })).toBeInTheDocument();
    }
  });

  test('a viewer does not see setup pages they cannot use', async () => {
    signedIn({ 'GET /auth/me': { data: { ...ME, role: 'viewer', username: 'guard' } } });
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    fireEvent.click(screen.getByRole('button', { name: /^Setup/ }));
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(await within(nav).findByRole('link', { name: 'Students' })).toBeInTheDocument();
    for (const name of ['Cameras', 'Users', 'Class Registers']) {
      expect(within(nav).queryByRole('link', { name })).not.toBeInTheDocument();
    }
  });
});

describe('top-bar search', () => {
  test('finds pages by everyday words and students by name, and opens them', async () => {
    const ASHA = { id: 12, student_id: 'RA001', name: 'Asha Kumar', class_name: 'CSE-A', person_type: 'student' };
    signedIn({ 'GET /students/': (config) => ({ data: config.params?.q === 'Asha' ? [ASHA] : [] }) });
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    const search = screen.getByRole('combobox', { name: 'Search' });
    fireEvent.change(search, { target: { value: 'camera' } });
    expect(await screen.findByRole('option', { name: 'Live Monitoring' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Cameras' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Asha/ })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'Asha' } });
    const person = await screen.findByRole('option', { name: /Asha Kumar \(RA001\)/ }, { timeout: 3000 });
    expect(mock.callsTo('GET', '/students/').some((c) => c.params?.q === 'Asha' && c.params?.limit === 8)).toBe(true);
    fireEvent.click(person);
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/students/12/attendance'));
  });

  test('pressing Enter on free text opens the Students list filtered by it', async () => {
    signedIn({ 'GET /students/': { data: [] } });
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    const search = screen.getByRole('combobox', { name: 'Search' });
    fireEvent.change(search, { target: { value: 'RA2511' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/students'));
  });
});

describe('session handling', () => {
  test('an expired token is discarded without calling the backend', async () => {
    localStorage.setItem('token', makeToken({ expiresInSeconds: -60 }));
    mock = installMockApi(HEALTHY);
    renderAt('/students');
    expect(await screen.findByText(/session has expired/i)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
    expect(mock.callsTo('GET', '/auth/me')).toHaveLength(0);
  });

  test('a token rejected by /auth/me is cleared', async () => {
    localStorage.setItem('token', makeToken());
    mock = installMockApi({ ...HEALTHY, 'GET /auth/me': { status: 401, data: { detail: 'Could not validate credentials' } } });
    renderAt('/dashboard');
    expect(await screen.findByText(/session has expired/i)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
  });

  test('an unreachable backend is reported and the token kept', async () => {
    const token = makeToken();
    localStorage.setItem('token', token);
    mock = installMockApi({ 'GET /auth/me': () => networkError(), 'GET /health': () => networkError() });
    renderAt('/dashboard');
    expect(await screen.findByText(/cannot reach the server/i)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBe(token);
  });

  test('a 401 from any API call mid-session signs the user out', async () => {
    signedIn({ 'GET /students/': { status: 401, data: { detail: 'Could not validate credentials' } } });
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    await act(async () => {
      await api.get('/students/').catch(() => {});
    });
    expect(await screen.findByText('Sign In to AttendAI')).toBeInTheDocument();
    expect(screen.getByText(/session has expired/i)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
  });

  test('logout clears the token and returns to login', async () => {
    signedIn();
    renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    fireEvent.click(screen.getByRole('button', { name: 'Logout' }));
    expect(await screen.findByText('Sign In to AttendAI')).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
    // A deliberate logout is not an expired session
    expect(screen.queryByText(/session has expired/i)).not.toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/login$/);
  });

  test('a signed-in user opening /login is sent on without a redirect loop', async () => {
    signedIn();
    renderAt('/login');
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/dashboard$/);
    expect(mock.callsTo('GET', '/auth/me')).toHaveLength(1);
  });

  test('the session-expired notice is cleared by the next successful login', async () => {
    localStorage.setItem('token', makeToken({ expiresInSeconds: -60 }));
    mock = installMockApi({
      ...HEALTHY,
      'POST /auth/login': { data: { access_token: makeToken(), token_type: 'bearer' } },
      'GET /auth/me': { data: ME }
    });
    renderAt('/dashboard');
    await screen.findByText(/session has expired/i);
    fireEvent.change(screen.getByLabelText(/username or email/i), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'pw-12345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
    await screen.findByText('Dashboard page');
    fireEvent.click(screen.getByRole('button', { name: 'Logout' }));
    await screen.findByText('Sign In to AttendAI');
    expect(screen.queryByText(/session has expired/i)).not.toBeInTheDocument();
  });

  test('the /health poll is cleared when the layout unmounts', async () => {
    const setIntervalSpy = jest.spyOn(global, 'setInterval');
    const clearIntervalSpy = jest.spyOn(global, 'clearInterval');
    signedIn();
    const { unmount } = renderAt('/dashboard');
    await screen.findByText('Dashboard page');
    const healthPoll = setIntervalSpy.mock.calls.findIndex(([, delay]) => delay === 30000);
    expect(healthPoll).toBeGreaterThanOrEqual(0);
    const intervalId = setIntervalSpy.mock.results[healthPoll].value;
    unmount();
    expect(clearIntervalSpy).toHaveBeenCalledWith(intervalId);
    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });
});

describe('login', () => {
  const fillAndSubmit = (username, password) => {
    fireEvent.change(screen.getByLabelText(/username or email/i), { target: { value: username } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
  };

  test('posts the OAuth2 form and returns to the page originally requested', async () => {
    const token = makeToken();
    mock = installMockApi({
      ...HEALTHY,
      'POST /auth/login': { data: { access_token: token, token_type: 'bearer' } },
      'GET /auth/me': { data: ME }
    });
    renderAt('/students');
    await screen.findByText('Sign In to AttendAI');
    fillAndSubmit('admin', 'correct-horse');

    expect(await screen.findByText('Students page')).toBeInTheDocument();
    const [loginCall] = mock.callsTo('POST', '/auth/login');
    const body = new URLSearchParams(loginCall.data);
    expect(body.get('username')).toBe('admin');
    expect(body.get('password')).toBe('correct-horse');
    expect(String(headerOf(loginCall, 'Content-Type'))).toMatch(/application\/x-www-form-urlencoded/);
    expect(localStorage.getItem('token')).toBe(token);
    expect(headerOf(mock.callsTo('GET', '/auth/me')[0], 'Authorization')).toBe(`Bearer ${token}`);
  });

  test('returns to the requested page including its query string', async () => {
    mock = installMockApi({
      ...HEALTHY,
      'POST /auth/login': { data: { access_token: makeToken(), token_type: 'bearer' } },
      'GET /auth/me': { data: ME }
    });
    renderAt('/attendance?camera_id=2#latest');
    await screen.findByText('Sign In to AttendAI');
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/login$/);
    fillAndSubmit('admin', 'correct-horse');
    expect(await screen.findByText('Attendance page')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/attendance?camera_id=2#latest');
  });

  test.each([
    [401, 'Incorrect username or password'],
    [400, 'Inactive user']
  ])('shows the backend reason for HTTP %i', async (status, detail) => {
    mock = installMockApi({ ...HEALTHY, 'POST /auth/login': { status, data: { detail } } });
    renderAt('/login');
    fillAndSubmit('alice', 'whatever-password');
    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
  });

  test('validates empty fields without calling the backend', async () => {
    mock = installMockApi(HEALTHY);
    renderAt('/login');
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
    expect(await screen.findByText(/enter your username/i)).toBeInTheDocument();
    expect(mock.callsTo('POST', '/auth/login')).toHaveLength(0);
  });
});

describe('self-registration availability', () => {
  test('when the server has registration turned off, sign-in offers no sign-up link', async () => {
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': { data: { enabled: false } } });
    renderAt('/login');
    expect(await screen.findByText('Need an account? Ask an AttendAI administrator.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign up' })).not.toBeInTheDocument();
  });

  test('when it is on, the sign-up link is shown', async () => {
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': { data: { enabled: true } } });
    renderAt('/login');
    expect(await screen.findByRole('button', { name: 'Sign up' })).toBeInTheDocument();
  });

  test('the register page explains that registration is off and shows no form', async () => {
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': { data: { enabled: false } } });
    renderAt('/register');
    expect(await screen.findByText(/Self-registration is turned off on this server/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Register' })).not.toBeInTheDocument();
  });

  test('while the setting is still loading, neither the sign-up link nor the form is shown', async () => {
    let answer;
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': () => new Promise((resolve) => { answer = resolve; }) });
    renderAt('/login');
    await screen.findByText('Sign In to AttendAI');
    await waitFor(() => expect(mock.callsTo('GET', '/auth/registration')).toHaveLength(1));
    expect(screen.queryByRole('button', { name: 'Sign up' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Ask an AttendAI administrator/)).not.toBeInTheDocument();
    await act(async () => { answer({ data: { enabled: false } }); });
    expect(await screen.findByText('Need an account? Ask an AttendAI administrator.')).toBeInTheDocument();
  });

  test('the register page shows no form until the setting is known', async () => {
    let answer;
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': () => new Promise((resolve) => { answer = resolve; }) });
    renderAt('/register');
    expect(await screen.findByText('Checking sign-up availability…')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Register' })).not.toBeInTheDocument();
    await act(async () => { answer({ data: { enabled: false } }); });
    expect(await screen.findByText(/Self-registration is turned off on this server/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Register' })).not.toBeInTheDocument();
  });

  test('if the setting cannot be read (older backend), sign-up stays available and the server decides', async () => {
    mock = installMockApi({ ...HEALTHY, 'GET /auth/registration': { status: 404, data: { detail: 'Not Found' } } });
    renderAt('/login');
    expect(await screen.findByRole('button', { name: 'Sign up' })).toBeInTheDocument();
  });
});

describe('register', () => {
  // These tests cover a successful sign-up flow, so the server reports registration as open
  const OPEN = { 'GET /auth/registration': { data: { enabled: true } } };
  const fill = async ({ email = 'new@example.com', username = 'newuser', password = 'long-enough' } = {}) => {
    fireEvent.change(await screen.findByLabelText(/email address/i), { target: { value: email } });
    fireEvent.change(screen.getByLabelText(/^username/i), { target: { value: username } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Register' }));
  };

  test('rejects a short password before calling the backend', async () => {
    mock = installMockApi({ ...HEALTHY, ...OPEN });
    renderAt('/register');
    await fill({ password: 'short' });
    expect(await screen.findByText(/at least 8 characters\./i)).toBeInTheDocument();
    expect(mock.callsTo('POST', '/auth/register')).toHaveLength(0);
  });

  test('creates the account and goes to login with a notice', async () => {
    mock = installMockApi({ ...HEALTHY, ...OPEN, 'POST /auth/register': { data: { ...ME, id: 2 } } });
    renderAt('/register');
    await fill();
    expect(await screen.findByText(/account created/i)).toBeInTheDocument();
    expect(JSON.parse(mock.callsTo('POST', '/auth/register')[0].data)).toEqual({
      email: 'new@example.com', username: 'newuser', password: 'long-enough'
    });
  });

  test('shows the backend reason when registration fails', async () => {
    mock = installMockApi({ ...HEALTHY, ...OPEN, 'POST /auth/register': { status: 400, data: { detail: 'Email already registered' } } });
    renderAt('/register');
    await fill();
    expect(await screen.findByText('Email already registered')).toBeInTheDocument();
  });

  test('a 403 because registration was turned off meanwhile is shown, not hidden', async () => {
    mock = installMockApi({ ...HEALTHY, ...OPEN, 'POST /auth/register': { status: 403,
      data: { detail: 'Self-registration is disabled. Ask an administrator for an account.' } } });
    renderAt('/register');
    await fill();
    expect(await screen.findByText('Self-registration is disabled. Ask an administrator for an account.')).toBeInTheDocument();
  });
});

describe('backend status chip', () => {
  test.each([
    [{ data: { status: 'healthy' } }, 'API online'],
    [{ status: 503, data: { status: 'unhealthy' } }, 'Database unavailable'],
    [() => networkError(), 'API offline']
  ])('reflects GET /health', async (health, label) => {
    const token = makeToken();
    localStorage.setItem('token', token);
    mock = installMockApi({ 'GET /auth/me': { data: ME }, 'GET /health': health });
    renderAt('/dashboard');
    expect(await screen.findByText(label)).toBeInTheDocument();
  });
});

test('no request in these tests went unmatched', async () => {
  signedIn();
  renderAt('/dashboard');
  await screen.findByText('Dashboard page');
  await waitFor(() => expect(mock.callsTo('GET', '/health').length).toBeGreaterThan(0));
  expect(mock.unmatched).toEqual([]);
});
