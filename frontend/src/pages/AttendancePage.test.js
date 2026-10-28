import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import AttendancePage, { DEFAULT_LIMIT, formatTimestamp } from './AttendancePage';
import { setUnauthorizedHandler } from '../data';
import { installMockApi, headerOf } from '../testUtils/mockApi';

// Shapes follow backend schemas.AttendanceRecord (GET /attendance/recent) and CameraResponse
const RECORDS = [
  { id: 3, student_name: 'Bob Jones', student_id: 'S002', event_type: 'EXIT', timestamp: '2026-10-19T15:30:00Z', confidence: 88, camera_name: 'Exit gate', student_db_id: 2, camera_id: 2 },
  { id: 2, student_name: 'Alice Smith', student_id: 'S001', event_type: 'ENTRY', timestamp: '2026-10-19T08:05:00Z', confidence: 93, camera_name: 'Main door', student_db_id: 1, camera_id: 1 },
  { id: 1, student_name: 'Cara Lee', student_id: 'S003', event_type: 'ENTRY', timestamp: '2026-10-18T23:59:59Z', confidence: 71, camera_name: null, student_db_id: 3, camera_id: null }
];
const CAMERAS = [
  { id: 1, name: 'Main door', camera_type: 'IN', deleted_at: null },
  { id: 2, name: 'Exit gate', camera_type: 'OUT', deleted_at: null },
  { id: 5, name: 'Old lab cam', camera_type: 'IN', deleted_at: '2026-10-05T08:00:00Z' }
];

const recentRoute = (config) => ({
  data: RECORDS.filter((r) => !config.params?.camera_id || r.camera_id === config.params.camera_id).slice(0, config.params?.limit)
});

const LocationDisplay = () => {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
};

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const renderPage = async (path = '/attendance', routes = {}) => {
  mock = installMockApi({ 'GET /attendance/recent': recentRoute, 'GET /cameras/': { data: CAMERAS }, ...routes });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/attendance" element={<AttendancePage />} /></Routes>
      <LocationDisplay />
    </MemoryRouter>
  );
};

const tableRows = () => within(screen.getByRole('table', { name: 'Attendance records' })).getAllByRole('row').slice(1);

// MUI select: open the listbox and pick an option
const choose = async (label, optionText) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: optionText }));
};

test('loads GET /attendance/recent through the shared client with the default limit', async () => {
  localStorage.setItem('token', 'test.token.value');
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  const [call] = mock.callsTo('GET', '/attendance/recent');
  expect(headerOf(call, 'Authorization')).toBe('Bearer test.token.value');
  expect(call.params).toEqual({ limit: DEFAULT_LIMIT });
  expect(mock.callsTo('GET', '/cameras/')[0].params).toEqual({ limit: 1000, include_deleted: true });
  expect(mock.unmatched).toEqual([]);
});

test('renders records newest first with student, event, confidence and camera', async () => {
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  const rows = tableRows();
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('Bob Jones');
  expect(rows[0]).toHaveTextContent('S002');
  expect(within(rows[0]).getByText('EXIT')).toBeInTheDocument();
  expect(rows[0]).toHaveTextContent('88%');
  expect(rows[0]).toHaveTextContent('Exit gate');
  expect(rows[2]).toHaveTextContent('No camera (manual entry)');
});

test('UTC timestamps are shown in local time, not as the raw UTC string', async () => {
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  const time = within(tableRows()[1]).getByText(formatTimestamp('2026-10-19T08:05:00Z'));
  expect(time).toHaveAttribute('datetime', '2026-10-19T08:05:00Z');
  expect(time.textContent).not.toContain('T08:05:00Z');
  // formatTimestamp interprets the "Z" suffix: the same instant as Date.parse
  expect(formatTimestamp('2026-10-19T08:05:00Z')).toBe(new Date(Date.UTC(2026, 9, 19, 8, 5, 0)).toLocaleString());
});

test('camera filter sends camera_id, updates the URL and lists deleted cameras', async () => {
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  fireEvent.mouseDown(screen.getByLabelText('Camera'));
  expect(await screen.findByRole('option', { name: 'Old lab cam (deleted)' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('option', { name: 'Exit gate (OUT)' }));

  await waitFor(() => expect(mock.callsTo('GET', '/attendance/recent').at(-1).params).toEqual({ limit: DEFAULT_LIMIT, camera_id: 2 }));
  await waitFor(() => expect(tableRows()).toHaveLength(1));
  expect(screen.getByTestId('location')).toHaveTextContent('/attendance?camera_id=2');
});

test('camera_id and limit from the URL are applied on load (deep link)', async () => {
  await renderPage('/attendance?camera_id=1&limit=25');
  await screen.findByRole('table', { name: 'Attendance records' });
  expect(mock.callsTo('GET', '/attendance/recent')[0].params).toEqual({ limit: 25, camera_id: 1 });
  expect(tableRows()).toHaveLength(1);
});

test('limit selector sends the chosen limit (max 500) and notes when the list is truncated', async () => {
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  await choose('Show', 'Latest 500');
  await waitFor(() => expect(mock.callsTo('GET', '/attendance/recent').at(-1).params).toEqual({ limit: 500 }));
  expect(screen.getByTestId('location')).toHaveTextContent('/attendance?limit=500');
  await choose('Show', 'Latest 25');
  await waitFor(() => expect(mock.callsTo('GET', '/attendance/recent').at(-1).params).toEqual({ limit: 25 }));
});

test('an invalid limit in the URL falls back to the default', async () => {
  await renderPage('/attendance?limit=9999');
  await screen.findByRole('table', { name: 'Attendance records' });
  expect(mock.callsTo('GET', '/attendance/recent')[0].params).toEqual({ limit: DEFAULT_LIMIT });
});

test('Refresh reloads the records', async () => {
  await renderPage();
  await screen.findByRole('table', { name: 'Attendance records' });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() => expect(mock.callsTo('GET', '/attendance/recent')).toHaveLength(2));
});

test.each([
  ['/attendance', 'No attendance records yet.'],
  ['/attendance?camera_id=1', 'No attendance records for Main door.']
])('empty state for %s', async (path, text) => {
  await renderPage(path, { 'GET /attendance/recent': { data: [] } });
  expect(await screen.findByText(new RegExp(text.replace('.', '\\.')))).toBeInTheDocument();
});

test('a backend failure shows the reason and Retry recovers', async () => {
  let fail = true;
  await renderPage('/attendance', {
    'GET /attendance/recent': (config) => (fail ? { status: 500, data: { detail: 'Database is locked' } } : recentRoute(config))
  });
  expect(await screen.findByText('Database is locked')).toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('table', { name: 'Attendance records' })).toBeInTheDocument();
});

test('a failed camera list still shows records, with a warning', async () => {
  await renderPage('/attendance', { 'GET /cameras/': { status: 500, data: { detail: 'boom' } } });
  expect(await screen.findByRole('table', { name: 'Attendance records' })).toBeInTheDocument();
  expect(screen.getByText('Camera filter unavailable: boom')).toBeInTheDocument();
});

test('an expired session (401) triggers the shared sign-out handler', async () => {
  localStorage.setItem('token', 'expired.token.value');
  const signOut = jest.fn();
  const unregister = setUnauthorizedHandler(signOut);
  await renderPage('/attendance', {
    'GET /attendance/recent': { status: 401, data: { detail: 'Could not validate credentials' } },
    'GET /cameras/': { status: 401, data: { detail: 'Could not validate credentials' } }
  });
  expect(await screen.findByText('Could not validate credentials')).toBeInTheDocument();
  expect(signOut).toHaveBeenCalled();
  unregister();
});
