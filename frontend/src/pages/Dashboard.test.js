import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Dashboard, { DASHBOARD_REFRESH_MS } from './Dashboard';
import { installMockApi, headerOf } from '../testUtils/mockApi';

// jsdom has no layout/ResizeObserver: give charts a fixed size instead of ResponsiveContainer
jest.mock('recharts', () => {
  const actual = jest.requireActual('recharts');
  const React = require('react');
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => React.cloneElement(children, { width: 600, height: 260 })
  };
});

// Shape of GET /dashboard/ (backend routers/dashboard.py DashboardData, schemas.DashboardStats)
const dashboardPayload = (overrides = {}) => ({
  stats: {
    total_students: 12,
    active_students: 11,
    total_cameras: 3,
    active_cameras: 2,
    in_cameras: 1,
    out_cameras: 1,
    attendance_today: 9, // events, NOT students
    today_entries: 6,
    today_exits: 3,
    estimated_present: 3,
    attendance_this_week: 40,
    present_today: 4,
    student_count: 10, // students only (staff excluded)
    staff_count: 1,
    enrolled_people: 9,
    known_inside_count: 3,
    students_inside: 2,
    staff_inside: 1,
    unknown_inside_count: 2,
    unknown_entries_today: 3,
    unknown_tracking_since: 1760860800,
    unknown_tracking_active: true,
    cameras_processing: 2,
    cameras_unhealthy: 0,
    classroom_occupancy: [{ classroom_id: 1, name: 'Room 101', known_inside: 2 }, { classroom_id: null, name: 'Unassigned', known_inside: 1 }],
    ...overrides.stats
  },
  daily_attendance: [
    { date: '2026-10-13', present: 5, absent: 5, late: 0, total: 10 },
    { date: '2026-10-14', present: 6, absent: 3, late: 1, total: 10 },
    { date: '2026-10-15', present: 0, absent: 10, late: 0, total: 10 },
    { date: '2026-10-16', present: 7, absent: 2, late: 1, total: 10 },
    { date: '2026-10-17', present: 8, absent: 2, late: 0, total: 10 },
    { date: '2026-10-18', present: 0, absent: 10, late: 0, total: 10 },
    { date: '2026-10-19', present: 3, absent: 6, late: 1, total: 10 }
  ],
  trend: { trend: 'up', change_percentage: 12.5, period_days: 14, ...overrides.trend }
});

const ALERTS = {
  data: {
    date: '2026-10-19',
    alerts: [
      { category: 'attendance', severity: 'warning', type: 'LATE_ARRIVAL', title: 'Asha (21CSE001)', detail: 'Maths 09:00: First entry at 09:25', basis: 'verified: recorded attendance events' },
      { category: 'camera', severity: 'error', type: 'CAMERA_DISCONNECTED', title: 'Exit gate', detail: 'Camera disconnected', basis: 'camera health' }
    ]
  }
};
const RECENT = { data: [{ id: 5, student_name: 'Asha', student_id: '21CSE001', event_type: 'ENTRY', timestamp: '2026-10-19T03:55:00Z', confidence: 81, camera_name: 'Main door' }] };

const LocationDisplay = () => <div data-testid="location">{useLocation().pathname}</div>;

const renderDashboard = (routes = {}) => {
  mock = installMockApi({ 'GET /dashboard/': { data: dashboardPayload() }, 'GET /dashboard/alerts': ALERTS,
    'GET /attendance/recent': RECENT, ...routes });
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="*" element={<p>Other page</p>} />
      </Routes>
      <LocationDisplay />
    </MemoryRouter>
  );
};

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
  jest.useRealTimers();
});

test('loads the dashboard, alerts and recent events through the shared client with the auth token', async () => {
  localStorage.setItem('token', 'test.token.value');
  renderDashboard();
  expect(await screen.findByLabelText('Present today')).toHaveTextContent('4');
  for (const path of ['/dashboard/', '/dashboard/alerts', '/attendance/recent']) {
    expect(headerOf(mock.callsTo('GET', path)[0], 'Authorization')).toBe('Bearer test.token.value');
  }
  expect(mock.callsTo('GET', '/attendance/recent')[0].params).toEqual({ limit: 8 });
  expect(mock.unmatched).toEqual([]);
});

test('present today counts students (present_today), against the student roster', async () => {
  renderDashboard();
  expect(await screen.findByLabelText('Present today')).toHaveTextContent('4');
  expect(screen.getByText('40% of 10 students')).toBeInTheDocument();
});

test('inside now splits students and staff; unregistered is labelled as an estimate with its tracking period', async () => {
  renderDashboard();
  expect(await screen.findByLabelText('Inside now')).toHaveTextContent('3');
  expect(screen.getByText('2 students · 1 staff')).toBeInTheDocument();
  expect(screen.getByLabelText('Unregistered inside')).toHaveTextContent('2');
  expect(screen.getByText(/^Estimate since .* · 3 entered today$/)).toBeInTheDocument();
});

test('unregistered count says when nothing is being tracked', async () => {
  renderDashboard({ 'GET /dashboard/': { data: dashboardPayload({ stats: { unknown_tracking_since: null, unknown_inside_count: 0 } }) } });
  expect(await screen.findByText('Not tracking: no camera has run since the server started')).toBeInTheDocument();
});

test('every KPI has a definition', async () => {
  renderDashboard();
  await screen.findByLabelText('Present today');
  for (const label of ['Present today', 'Inside now', 'Unregistered inside', 'Cameras processing']) {
    expect(screen.getByLabelText(`How "${label}" is calculated`)).toBeInTheDocument();
  }
});

test('camera problems change the camera KPI', async () => {
  renderDashboard({ 'GET /dashboard/': { data: dashboardPayload({ stats: { cameras_unhealthy: 1 } }) } });
  expect(await screen.findByText('1 need attention')).toBeInTheDocument();
});

test('alerts show their reason and basis', async () => {
  renderDashboard();
  const list = await screen.findByRole('list', { name: 'Alerts' });
  expect(within(list).getByText('Asha (21CSE001)')).toBeInTheDocument();
  expect(within(list).getByText('Basis: verified: recorded attendance events')).toBeInTheDocument();
  expect(within(list).getByText('Camera')).toBeInTheDocument();
  expect(screen.getByLabelText('Alert count')).toHaveTextContent('2');
});

test('a failing alerts request does not hide the rest of the dashboard', async () => {
  renderDashboard({ 'GET /dashboard/alerts': { status: 500, data: { detail: 'boom' } } });
  expect(await screen.findByText('boom')).toBeInTheDocument();
  expect(screen.getByLabelText('Present today')).toHaveTextContent('4');
});

test('classroom occupancy lists rooms with their counts', async () => {
  renderDashboard();
  expect(await screen.findByLabelText('Room 101 inside')).toHaveTextContent('2');
  expect(screen.getByLabelText('Unassigned inside')).toHaveTextContent('1');
});

test('recent events and the 7-day chart are shown', async () => {
  renderDashboard();
  const events = await screen.findByRole('list', { name: 'Recent events' });
  expect(within(events).getByText('Asha')).toBeInTheDocument();
  expect(screen.getByText('6 entries · 3 exits today')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Daily attendance chart' })).toBeInTheDocument();
  expect(screen.getByLabelText('Attendance trend')).toHaveTextContent('▲ Up +12.5%');
});

test('empty states explain what to do', async () => {
  renderDashboard({
    'GET /dashboard/': { data: dashboardPayload({ stats: { classroom_occupancy: [] } }) },
    'GET /dashboard/alerts': { data: { alerts: [] } },
    'GET /attendance/recent': { data: [] }
  });
  expect(await screen.findByText('No alerts')).toBeInTheDocument();
  expect(screen.getByText('No events yet')).toBeInTheDocument();
  expect(screen.getByText('No classrooms yet')).toBeInTheDocument();
});

test('a failed first load shows the reason and Retry recovers', async () => {
  let fail = true;
  renderDashboard({ 'GET /dashboard/': () => (fail ? { status: 500, data: { detail: 'Database is locked' } } : { data: dashboardPayload() }) });
  expect(await screen.findByText('Database is locked')).toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByLabelText('Present today')).toHaveTextContent('4');
});

test('a failed refresh keeps the last data and warns', async () => {
  let fail = false;
  renderDashboard({ 'GET /dashboard/': () => (fail ? { status: 500, data: { detail: 'Database is locked' } } : { data: dashboardPayload() }) });
  await screen.findByLabelText('Present today');
  fail = true;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(await screen.findByText('Could not refresh: Database is locked')).toBeInTheDocument();
  expect(screen.getByLabelText('Present today')).toHaveTextContent('4');
});

test('refreshes on an interval and stops when unmounted', async () => {
  jest.useFakeTimers();
  const { unmount } = renderDashboard();
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(mock.callsTo('GET', '/dashboard/')).toHaveLength(1));
  await act(async () => { jest.advanceTimersByTime(DASHBOARD_REFRESH_MS); });
  await waitFor(() => expect(mock.callsTo('GET', '/dashboard/')).toHaveLength(2));
  unmount();
  await act(async () => { jest.advanceTimersByTime(DASHBOARD_REFRESH_MS * 2); });
  expect(mock.callsTo('GET', '/dashboard/')).toHaveLength(2);
});

test('quick actions navigate', async () => {
  renderDashboard();
  await screen.findByLabelText('Present today');
  fireEvent.click(screen.getByRole('link', { name: 'Reports & exceptions' }));
  expect(screen.getByTestId('location')).toHaveTextContent('/reports');
});
