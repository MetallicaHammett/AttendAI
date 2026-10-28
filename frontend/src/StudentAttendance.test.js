import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AppRoutes } from './App';
import { AuthContextProvider } from './context/AuthContext';
import { portalDate } from './components/StudentAttendanceReport';
import { installMockApi, makeToken, ME } from './testUtils/mockApi';

// jsdom has no layout/ResizeObserver: give charts a fixed size instead of ResponsiveContainer
jest.mock('recharts', () => {
  const actual = jest.requireActual('recharts');
  const React = require('react');
  return { ...actual, ResponsiveContainer: ({ children }) => React.cloneElement(children, { width: 600, height: 280 }) };
});
jest.mock('./pages/Dashboard',() => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Dashboard page') }));

const HEALTHY = { 'GET /health': { data: { status: 'healthy' } } };
const STUDENT_ME = { ...ME, id: 40, username: 'surya_s', role: 'student' };

const course = (code, name, max, att, absent, odml = 0) => {
  const p = (n) => Math.round((n / max) * 10000) / 100;
  return { course_code: code, course_name: name, max_hours: max, attended: att, late: 0, absent, od_ml: odml, unverified: 0,
    average_pct: p(att), od_ml_pct: p(odml), total_pct: p(att + odml) };
};

const REPORT = {
  student: { id: 7, student_id: 'RA2511030040008', name: 'S Surya', class_name: 'CSE-A', department: null },
  start_date: '2026-07-20', end_date: '2026-10-16',
  courses: [
    course('21CSC201J', 'Data Structures and Algorithms', 64, 60, 4),
    course('21CSC202J', 'Operating Systems', 67, 59, 8),
    course('21MAB206T', 'Probability and Queueing Theory', 40, 26, 12, 2)
  ],
  totals: { max_hours: 171, attended: 145, late: 3, absent: 24, od_ml: 2, unverified: 1, total_pct: 85.96 }
};

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const LocationDisplay = () => <div data-testid="location">{useLocation().pathname}</div>;

const renderAs = (me, path, routes = {}) => {
  localStorage.setItem('token', makeToken());
  mock = installMockApi({ ...HEALTHY, 'GET /auth/me': { data: me }, ...routes });
  render(
    <AuthContextProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
        <LocationDisplay />
      </MemoryRouter>
    </AuthContextProvider>
  );
};

test('dates are shown portal-style without time-zone shifts', () => {
  expect(portalDate('2026-07-20')).toBe('20/Jul/2026');
  expect(portalDate('2026-10-06')).toBe('06/Oct/2026');
  expect(portalDate(undefined)).toBe('—');
});

describe('student account', () => {
  test('lands on their own course-wise attendance', async () => {
    renderAs(STUDENT_ME, '/my-attendance', { 'GET /portal/me/attendance': { data: REPORT } });
    const table = await screen.findByRole('table', { name: 'Course-wise attendance' });
    expect(screen.getByText('RA2511030040008 · S Surya · CSE-A')).toBeInTheDocument();
    expect(screen.getByText('COURSE WISE ATTENDANCE - During the Period: 20/Jul/2026 To 16/Oct/2026')).toBeInTheDocument();
    const rows = within(table).getAllByRole('row');
    const row = (text) => rows.find((r) => within(r).queryByText(text));
    expect(within(row('21CSC201J')).getAllByRole('cell').map((c) => c.textContent)).toEqual(
      ['21CSC201J', 'Data Structures and Algorithms', '64', '60', '4', '93.75', '0.00', '93.75']);
    // Below 75% is highlighted and announced; OD/ML counts towards the total
    expect(within(row('21MAB206T')).getByLabelText('70.00, below 75%')).toBeInTheDocument();
    expect(within(row('21MAB206T')).getByText('5.00')).toBeInTheDocument();
    expect(row('Total')).toHaveTextContent('85.96');
    expect(screen.getByText(/21CSC201J: 93.75% · 21CSC202J: 88.06% · 21MAB206T: 70.00%/)).toBeInTheDocument();
    expect(screen.getByText('Hours present: 147 · Hours absent: 24')).toBeInTheDocument();
    expect(screen.getByText(/1 period\(s\) are not counted because no entry camera was running/)).toBeInTheDocument();
    // Students get only their own page in the menu
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Attendance Details']);
  });

  test('is sent back to their page from any staff page', async () => {
    renderAs(STUDENT_ME, '/students', { 'GET /portal/me/attendance': { data: REPORT } });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/my-attendance'));
    expect(screen.queryByText('Dashboard page')).not.toBeInTheDocument();
    expect(mock.callsTo('GET', '/students/')).toHaveLength(0);
  });

  test('choosing dates asks the server for that range', async () => {
    renderAs(STUDENT_ME, '/my-attendance', { 'GET /portal/me/attendance': { data: REPORT } });
    await screen.findByRole('table', { name: 'Course-wise attendance' });
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-01' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-09-30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    await waitFor(() => expect(mock.callsTo('GET', '/portal/me/attendance')).toHaveLength(2));
    expect(mock.callsTo('GET', '/portal/me/attendance')[1].params).toEqual({ start_date: '2026-09-01', end_date: '2026-09-30' });
  });

  test('an unlinked account sees the server explanation', async () => {
    renderAs(STUDENT_ME, '/my-attendance', {
      'GET /portal/me/attendance': { status: 404, data: { detail: 'This account is not linked to a student record. Ask an administrator to check your account.' } }
    });
    expect(await screen.findByText(/not linked to a student record/)).toBeInTheDocument();
  });
});

describe('staff', () => {
  test('open a student’s attendance page; the personal page is not theirs', async () => {
    renderAs(ME, '/students/7/attendance', { 'GET /portal/students/7/attendance': { data: REPORT } });
    expect(await screen.findByRole('heading', { name: 'Attendance of S Surya' })).toBeInTheDocument();
    // Sidebar header: institution seal and name, then the application name
    const nav = screen.getByRole('complementary');  // the sidebar (<aside>)
    expect(within(nav).getByRole('img', { name: 'SRM Institute of Science and Technology logo' }))
      .toHaveAttribute('src', expect.stringContaining('srm-seal.png'));
    expect(within(nav).getByText('SRM INSTITUTE OF SCIENCE AND TECHNOLOGY')).toBeInTheDocument();
    expect(within(nav).getByText('AttendAI')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'All students' })).toHaveAttribute('href', '/students');
    expect(mock.callsTo('GET', '/portal/me/attendance')).toHaveLength(0);
  });

  test('are sent to the dashboard from the student-only page', async () => {
    renderAs(ME, '/my-attendance');
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument();
    expect(mock.callsTo('GET', '/portal/me/attendance')).toHaveLength(0);
  });
});
