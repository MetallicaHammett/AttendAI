import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AppRoutes } from '../App';
import { AuthContext, AuthContextProvider } from '../context/AuthContext';
import { ClassRegisterPage, WeeklyTimetablePage, reasonRequired } from './StaffPortalPages';
import { installMockApi, makeToken, ME } from '../testUtils/mockApi';

jest.mock('./Dashboard', () => ({ __esModule: true, default: () => require('react').createElement('h1', null, 'Dashboard page') }));

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const PROFESSOR = { ...ME, id: 7, username: 'prof_rao', role: 'professor' };

const WEEK = {
  week_start: '2026-10-19', week_end: '2026-10-25', today: '2026-10-21', assigned_periods: 2,
  days: [
    { date: '2026-10-19', day_name: 'Monday', holiday: null, periods: [
      { period_id: 4, date: '2026-10-19', start_time: '09:00', end_time: '10:00', course_code: 'MA101', course_name: 'Maths',
        class_name: 'CSE-A', classroom: 'Room 101', professor: 'prof_rao', state: 'submitted', submitted_by: 'prof_rao',
        submitted_at: '2026-10-19T04:40:00Z' },
      { period_id: 5, date: '2026-10-19', start_time: '10:15', end_time: '11:15', course_code: null, course_name: 'Physics',
        class_name: 'CSE-A', classroom: 'Room 101', professor: 'prof_rao', state: 'awaiting_review', submitted_by: null, submitted_at: null }
    ] },
    { date: '2026-10-20', day_name: 'Tuesday', holiday: 'Founders Day', periods: [] },
    ...['Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day_name, i) => (
      { date: `2026-10-2${i + 1}`, day_name, holiday: null, periods: [] }))
  ]
};

const LocationDisplay = () => {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
};

describe('professor routing and navigation', () => {
  test('a professor lands on the staff portal and only sees staff navigation', async () => {
    localStorage.setItem('token', makeToken());
    mock = installMockApi({
      'GET /health': { data: { status: 'healthy' } }, 'GET /auth/me': { data: PROFESSOR },
      'GET /staff/timetable': { data: WEEK }
    });
    render(
      <AuthContextProvider>
        <MemoryRouter initialEntries={['/dashboard']}>
          <AppRoutes />
          <LocationDisplay />
        </MemoryRouter>
      </AuthContextProvider>
    );
    expect(await screen.findByRole('heading', { name: 'Weekly timetable' })).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/staff/timetable');
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(within(nav).getByText('Weekly Timetable')).toBeInTheDocument();
    expect(within(nav).getByText('Attendance History')).toBeInTheDocument();
    for (const hidden of ['Dashboard', 'Cameras', 'Users', 'Students', 'Live Monitoring']) {
      expect(within(nav).queryByText(hidden)).not.toBeInTheDocument();
    }
    expect(screen.queryByText('Dashboard page')).not.toBeInTheDocument();
  });

  test.each(['/cameras', '/users', '/students', '/timetable'])('a professor opening %s is sent to the staff portal', async (path) => {
    localStorage.setItem('token', makeToken());
    mock = installMockApi({
      'GET /health': { data: { status: 'healthy' } }, 'GET /auth/me': { data: PROFESSOR },
      'GET /staff/timetable': { data: WEEK }
    });
    render(
      <AuthContextProvider>
        <MemoryRouter initialEntries={[path]}><AppRoutes /><LocationDisplay /></MemoryRouter>
      </AuthContextProvider>
    );
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/staff/timetable'));
    expect(mock.callsTo('GET', '/cameras/')).toHaveLength(0);
    expect(mock.callsTo('GET', '/users/')).toHaveLength(0);
  });
});

const asUser = (user, ui, path = '/') => (
  <AuthContext.Provider value={{ user }}>
    <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
  </AuthContext.Provider>
);

describe('weekly timetable', () => {
  test('shows each class with its attendance state and a link to its register', async () => {
    mock = installMockApi({ 'GET /staff/timetable': { data: WEEK } });
    render(asUser(PROFESSOR, <WeeklyTimetablePage />));
    const monday = await screen.findByRole('table', { name: 'Monday classes' });
    expect(within(monday).getByText('Maths')).toBeInTheDocument();
    expect(within(monday).getByText('MA101')).toBeInTheDocument();
    expect(within(monday).getByText('Submitted')).toBeInTheDocument();
    expect(within(monday).getByText('Needs review')).toBeInTheDocument();
    expect(within(monday).getByRole('link', { name: 'Open Physics register for Monday' }))
      .toHaveAttribute('href', '/staff/register/5/2026-10-19');
    expect(screen.getByText('Holiday: Founders Day')).toBeInTheDocument();
    expect(screen.queryByText(/Sunday/)).not.toBeInTheDocument(); // empty Sundays are hidden
    // Next week is requested with a date in that week
    fireEvent.click(screen.getByRole('button', { name: /Next week/ }));
    await waitFor(() => expect(mock.callsTo('GET', '/staff/timetable')).toHaveLength(2));
    expect(mock.callsTo('GET', '/staff/timetable')[1].params.week_of > mock.callsTo('GET', '/staff/timetable')[0].params.week_of).toBe(true);
  });

  test('a professor with no assigned classes is told what to do', async () => {
    mock = installMockApi({ 'GET /staff/timetable': { data: { ...WEEK, assigned_periods: 0, days: WEEK.days.map((d) => ({ ...d, periods: [] })) } } });
    render(asUser(PROFESSOR, <WeeklyTimetablePage />));
    expect(await screen.findByText('No classes are assigned to you')).toBeInTheDocument();
  });
});

const student = (overrides) => ({
  arrived_at: null, left_at: null, minutes_inside: 0, review_flags: [], final: null, evidence: null, ...overrides
});

const REGISTER = {
  period: { id: 5, course_code: null, course_name: 'Physics', class_name: 'CSE-A', classroom: 'Room 101', day_name: 'Monday',
    start_time: '10:15', end_time: '11:15', grace_minutes: 10, is_active: true, professor: 'prof_rao' },
  date: '2026-10-19', phase: 'completed', holiday: null,
  coverage: { state: 'full', covered_percent: 100, outage: false, classroom_cameras: ['Room 101 IN'] },
  can_save: true, save_blocked_reason: null,
  register: { submitted: false, version: 0, submitted_by: null, submitted_at: null, first_submitted_by: null, first_submitted_at: null },
  counts: { PRESENT: 1, LATE: 0, ABSENT: 0, EXCUSED: 0, UNVERIFIED: 2 }, confirmed_count: 0, roster_size: 3,
  students: [
    student({ student_db_id: 1, student_id: 'RA001', name: 'Asha', ai_status: 'PRESENT', suggested_status: 'PRESENT', status: 'PRESENT',
      evidence: { kind: 'classroom_camera', at: '2026-10-19T10:12:00', camera: 'Room 101 IN' }, minutes_inside: 63 }),
    student({ student_db_id: 2, student_id: 'RA002', name: 'Ravi', ai_status: 'SEEN_ELSEWHERE', suggested_status: 'UNVERIFIED', status: 'UNVERIFIED',
      evidence: { kind: 'other_camera', at: '2026-10-19T08:50:00', camera: 'Main gate' },
      review_flags: [{ code: 'BUILDING_ONLY', message: 'Seen by Main gate at 08:50, not by this classroom\'s camera' }] }),
    student({ student_db_id: 3, student_id: 'RA003', name: 'Meena', ai_status: 'NOT_DETECTED', suggested_status: 'UNVERIFIED', status: 'UNVERIFIED',
      review_flags: [{ code: 'NOT_DETECTED', message: 'Not recognised by the classroom camera while it was running' }] })
  ]
};

const renderRegister = () => render(asUser(PROFESSOR, (
  <Routes><Route path="/staff/register/:periodId/:date" element={<ClassRegisterPage />} /></Routes>
), '/staff/register/5/2026-10-19'));

const saved = (decisions, version = 1) => ({
  ...REGISTER,
  register: { submitted: true, version, submitted_by: 'prof_rao', submitted_at: '2026-10-19T06:00:00Z', first_submitted_by: 'prof_rao', first_submitted_at: '2026-10-19T06:00:00Z' },
  changed_count: decisions.length,
  students: REGISTER.students.map((s) => {
    const d = decisions.find((x) => x.student_db_id === s.student_db_id);
    return { ...s, status: d.status, final: { status: d.status, reason: d.reason || 'Confirmed by professor', by: 'prof_rao', at: '2026-10-19T06:00:00Z' } };
  })
});

describe('class register', () => {
  test('AI evidence is pre-filled, undetected students stay unverified, and nothing saves until all are decided', async () => {
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: REGISTER } });
    renderRegister();
    const table = await screen.findByRole('table', { name: 'Class register' });
    expect(within(table).getByText('Detected in class')).toBeInTheDocument();
    expect(within(table).getByText('Room 101 IN at 10:12')).toBeInTheDocument();
    expect(within(table).getByText('Seen elsewhere only')).toBeInTheDocument();
    expect(within(table).getByText(/not by this classroom's camera/)).toBeInTheDocument();
    expect(within(table).getByText('Not detected')).toBeInTheDocument();
    expect(screen.getByLabelText('Unverified')).toHaveTextContent('2');
    expect(screen.getByLabelText('Absent')).toHaveTextContent('0'); // never absent without a decision
    expect(screen.getByRole('button', { name: 'Save Attendance' })).toBeDisabled();
    expect(screen.getByText('Decide the 2 unverified students before saving.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Present for Asha' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('the professor decides each student and saves; the confirmed register is shown', async () => {
    mock = installMockApi({
      'GET /staff/periods/5/register': { data: REGISTER },
      'PUT /staff/periods/5/register': (config) => ({ data: saved(JSON.parse(config.data).decisions) })
    });
    renderRegister();
    await screen.findByRole('table', { name: 'Class register' });
    fireEvent.click(screen.getByRole('button', { name: 'Present for Ravi' }));
    fireEvent.click(screen.getByRole('button', { name: 'Absent for Meena' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Attendance' }));
    await waitFor(() => expect(mock.callsTo('PUT', '/staff/periods/5/register')).toHaveLength(1));
    const call = mock.callsTo('PUT', '/staff/periods/5/register')[0];
    expect(call.params).toEqual({ date: '2026-10-19' });
    expect(JSON.parse(call.data)).toEqual({
      expected_version: 0,
      decisions: [
        { student_db_id: 1, status: 'PRESENT', reason: null, base_status: null },
        { student_db_id: 2, status: 'PRESENT', reason: null, base_status: null },
        { student_db_id: 3, status: 'ABSENT', reason: null, base_status: null }
      ]
    });
    expect(await screen.findByText(/Submitted by prof_rao/)).toBeInTheDocument();
    expect(screen.getByLabelText('Absent')).toHaveTextContent('1');
  });

  test('marking a camera-detected student absent needs a reason before saving', async () => {
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: REGISTER } });
    renderRegister();
    await screen.findByRole('table', { name: 'Class register' });
    fireEvent.click(screen.getByRole('button', { name: 'Mark unverified present' }));
    fireEvent.click(screen.getByRole('button', { name: 'Absent for Asha' }));
    const reason = screen.getByLabelText('Reason for Asha');
    expect(screen.getByRole('button', { name: 'Save Attendance' })).toBeDisabled();
    expect(screen.getByText('Give a reason for Asha.')).toBeInTheDocument();
    fireEvent.change(reason, { target: { value: 'Left before roll call' } });
    expect(screen.getByRole('button', { name: 'Save Attendance' })).toBeEnabled();
  });

  test('marking all unverified students absent asks for confirmation first', async () => {
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: REGISTER } });
    renderRegister();
    await screen.findByRole('table', { name: 'Class register' });
    fireEvent.click(screen.getByRole('button', { name: 'Mark unverified absent…' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/not confirmed by the classroom camera/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    expect(screen.getByLabelText('Unverified')).toHaveTextContent('2');
    fireEvent.click(screen.getByRole('button', { name: 'Mark unverified absent…' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Mark absent' }));
    await waitFor(() => expect(screen.getByLabelText('Absent')).toHaveTextContent('2'));
    expect(mock.callsTo('PUT', '/staff/periods/5/register')).toHaveLength(0); // nothing saved yet
  });

  test('a conflicting save keeps every unsaved decision on screen and offers a reload', async () => {
    mock = installMockApi({
      'GET /staff/periods/5/register': { data: REGISTER },
      'PUT /staff/periods/5/register': { status: 409, data: { detail: 'This register was saved by prof_iyer after you opened it. Reload to see the latest version; your changes are still on screen.' } }
    });
    renderRegister();
    await screen.findByRole('table', { name: 'Class register' });
    fireEvent.click(screen.getByRole('button', { name: 'Present for Ravi' }));
    fireEvent.click(screen.getByRole('button', { name: 'Absent for Meena' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Attendance' }));
    expect(await screen.findByText(/saved by prof_iyer after you opened it/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Absent for Meena' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Present for Ravi' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  test('a submitted register says who saved it and changing a decision needs a reason', async () => {
    const decided = saved([{ student_db_id: 1, status: 'PRESENT' }, { student_db_id: 2, status: 'PRESENT' }, { student_db_id: 3, status: 'ABSENT' }]);
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: decided } });
    renderRegister();
    expect(await screen.findByText(/Submitted by prof_rao/)).toBeInTheDocument();
    expect(screen.getByText(/changing a saved decision needs a reason/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Present for Meena' }));
    expect(screen.getByLabelText('Reason for Meena')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Attendance' })).toBeDisabled();
  });

  test('a manual entry is never presented as a camera detection', async () => {
    const manual = { ...REGISTER, students: [student({ student_db_id: 9, student_id: 'RA009', name: 'Kiran', ai_status: 'PRESENT',
      suggested_status: 'PRESENT', status: 'PRESENT', evidence: { kind: 'manual_event', at: '2026-10-19T10:10:00', camera: null },
      review_flags: [{ code: 'MANUAL_EVENT', message: 'Entry was recorded manually by an operator, not by a camera' }] })] };
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: manual } });
    renderRegister();
    const table = await screen.findByRole('table', { name: 'Class register' });
    expect(within(table).getByText('Recorded manually')).toBeInTheDocument();
    expect(within(table).queryByText('Detected in class')).not.toBeInTheDocument();
    expect(within(table).getByText('Manual entry at 10:10')).toBeInTheDocument();
  });

  test('a holiday or future class cannot be edited', async () => {
    mock = installMockApi({ 'GET /staff/periods/5/register': { data: { ...REGISTER, can_save: false, save_blocked_reason: 'The class has not started yet', phase: 'upcoming' } } });
    renderRegister();
    expect(await screen.findByText(/The class has not started yet/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save Attendance' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Absent for Meena' })).not.toBeInTheDocument();
  });

  test('a class that is not the professor\'s shows the server refusal', async () => {
    mock = installMockApi({ 'GET /staff/periods/5/register': { status: 403, data: { detail: 'This class is not assigned to you' } } });
    renderRegister();
    expect(await screen.findByText('This class is not assigned to you')).toBeInTheDocument();
  });
});

describe('administrator assigns a professor', () => {
  test('the period dialog lists professor accounts and sends the assignment', async () => {
    const TimetablePage = require('./TimetablePage').default;
    mock = installMockApi({
      'GET /timetable/classrooms': { data: [{ id: 1, name: 'Room 101', is_active: true }] },
      'GET /timetable/periods': { data: [{ id: 4, classroom_id: 1, class_name: 'CSE-A', course_code: null, course_name: 'Maths',
        day_of_week: 0, start_time: '09:00:00', end_time: '10:00:00', grace_minutes: null, is_active: true, day_name: 'Monday',
        professor_user_id: null, professor_username: null }] },
      'GET /timetable/holidays': { data: [] },
      'GET /students/classes': { data: ['CSE-A'] },
      'GET /users/': { data: [
        { id: 7, username: 'prof_rao', role: 'professor', is_active: true },
        { id: 8, username: 'op1', role: 'operator', is_active: true }
      ] },
      'PUT /timetable/periods/4': (config) => ({ data: { ...JSON.parse(config.data), id: 4, professor_username: 'prof_rao' } })
    });
    render(asUser({ ...ME, role: 'admin' }, <TimetablePage />));
    const monday = await screen.findByRole('table', { name: 'Monday periods' });
    expect(within(monday).getByText('Not assigned')).toBeInTheDocument();
    fireEvent.click(within(monday).getByRole('button', { name: 'Edit Maths on Monday' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Professor' }));
    const options = await screen.findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['Not assigned', 'prof_rao']); // operators are not offered
    fireEvent.click(screen.getByRole('option', { name: 'prof_rao' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save period' }));
    await waitFor(() => expect(mock.callsTo('PUT', '/timetable/periods/4')).toHaveLength(1));
    expect(JSON.parse(mock.callsTo('PUT', '/timetable/periods/4')[0].data).professor_user_id).toBe(7);
  });
});

test('reason rules match the backend', () => {
  const camera = { evidence: { kind: 'classroom_camera' }, final: null };
  const building = { evidence: { kind: 'other_camera' }, final: null };
  expect(reasonRequired(camera, 'ABSENT')).toBe(true);
  expect(reasonRequired(building, 'ABSENT')).toBe(false);
  expect(reasonRequired(building, 'EXCUSED')).toBe(true);
  expect(reasonRequired({ evidence: null, final: { status: 'ABSENT' } }, 'PRESENT')).toBe(true);
  expect(reasonRequired({ evidence: null, final: { status: 'ABSENT' } }, 'ABSENT')).toBe(false);
  // Regression: a saved excusal must not demand a new reason when it is left unchanged
  expect(reasonRequired({ evidence: null, final: { status: 'EXCUSED' } }, 'EXCUSED')).toBe(false);
  expect(reasonRequired({ evidence: { kind: 'classroom_camera' }, final: { status: 'ABSENT' } }, 'ABSENT')).toBe(false);
});
