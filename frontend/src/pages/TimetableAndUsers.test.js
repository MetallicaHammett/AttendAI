import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import TimetablePage from './TimetablePage';
import UsersPage from './UsersPage';
import AttendancePage from './AttendancePage';
import { AuthContext } from '../context/AuthContext';
import { installMockApi } from '../testUtils/mockApi';

// These tests drive several MUI dialogs and selects; under a loaded full-suite run they can
// exceed Jest's 5 s default without anything being wrong
jest.setTimeout(15000);

let mock;
afterEach(() => mock?.restore());

const asRole = (role, ui, id = 1) => (
  <AuthContext.Provider value={{ user: { id, username: role, role } }}>
    <MemoryRouter>{ui}</MemoryRouter>
  </AuthContext.Provider>
);

const ROOMS = [{ id: 1, name: 'Room 101', is_active: true }];
const PERIODS = [
  { id: 4, classroom_id: 1, class_name: 'CSE-A', course_code: null, course_name: 'Maths', day_of_week: 0, start_time: '09:00:00', end_time: '10:00:00', grace_minutes: null, is_active: true, day_name: 'Monday' }
];
const TIMETABLE_ROUTES = {
  'GET /timetable/classrooms': { data: ROOMS },
  'GET /timetable/periods': { data: PERIODS },
  'GET /timetable/holidays': { data: [{ id: 2, date: '2026-11-01', name: 'Kannada Rajyotsava' }] },
  'GET /students/classes': { data: ['CSE-A'] }
};

describe('timetable', () => {
  test('lists periods by weekday, classrooms and holidays', async () => {
    mock = installMockApi(TIMETABLE_ROUTES);
    render(asRole('viewer', <TimetablePage />));
    const monday = await screen.findByRole('table', { name: 'Monday periods' });
    expect(within(monday).getByText('09:00–10:00')).toBeInTheDocument();
    expect(within(monday).getByText('Maths')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Classrooms' })).toHaveTextContent('Room 101');
    expect(screen.getByRole('list', { name: 'Holidays' })).toHaveTextContent('Kannada Rajyotsava');
    expect(screen.queryByRole('button', { name: 'Add period' })).not.toBeInTheDocument(); // viewers read only
  });

  test('an admin adds a period; the backend overlap reason is shown', async () => {
    mock = installMockApi({
      ...TIMETABLE_ROUTES,
      'POST /timetable/periods': { status: 409, data: { detail: 'Overlaps Maths (09:00-10:00) for class CSE-A' } }
    });
    render(asRole('admin', <TimetablePage />));
    fireEvent.click(await screen.findByRole('button', { name: 'Add period' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Class group'), { target: { value: 'CSE-A' } });
    fireEvent.change(within(dialog).getByLabelText('Course name'), { target: { value: 'Physics' } });
    fireEvent.change(within(dialog).getByLabelText('Start'), { target: { value: '09:30' } });
    fireEvent.change(within(dialog).getByLabelText('End'), { target: { value: '10:30' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save period' }));
    expect(await within(dialog).findByText('Overlaps Maths (09:00-10:00) for class CSE-A')).toBeInTheDocument();
    expect(JSON.parse(mock.callsTo('POST', '/timetable/periods')[0].data)).toMatchObject({
      classroom_id: 1, class_name: 'CSE-A', course_name: 'Physics', day_of_week: 0, start_time: '09:30', end_time: '10:30', grace_minutes: null
    });
  });

  test('end before start is rejected without calling the backend', async () => {
    mock = installMockApi(TIMETABLE_ROUTES);
    render(asRole('admin', <TimetablePage />));
    fireEvent.click(await screen.findByRole('button', { name: 'Add period' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Class group'), { target: { value: 'CSE-A' } });
    fireEvent.change(within(dialog).getByLabelText('Course name'), { target: { value: 'Physics' } });
    fireEvent.change(within(dialog).getByLabelText('End'), { target: { value: '08:00' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save period' }));
    expect(await within(dialog).findByText('The end time must be after the start time.')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/timetable/periods')).toHaveLength(0);
  });
});

describe('users', () => {
  const USERS = [
    { id: 1, username: 'admin', email: 'a@x.org', role: 'admin', is_active: true, created_at: '2026-10-01T08:00:00Z' },
    { id: 2, username: 'guard', email: 'g@x.org', role: 'viewer', is_active: true, created_at: '2026-10-02T08:00:00Z' }
  ];

  test('non-admins are told they cannot manage accounts and nothing is requested', async () => {
    mock = installMockApi({});
    render(asRole('operator', <UsersPage />));
    expect(await screen.findByText('Only administrators can manage accounts.')).toBeInTheDocument();
    expect(mock.calls).toHaveLength(0);
  });

  test('an admin changes a role; their own account cannot be changed', async () => {
    mock = installMockApi({
      'GET /users/': { data: USERS },
      'GET /users/audit-log': { data: [{ id: 9, created_at: '2026-10-19T09:00:00Z', username: 'admin', action: 'user.update', entity_type: 'user', entity_id: '2', details: { after: { role: 'operator' } } }] },
      'PATCH /users/2': (config) => ({ data: { ...USERS[1], ...JSON.parse(config.data) } })
    });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    expect(screen.getByRole('combobox', { name: 'Role of admin' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Role of guard' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Operator' }));
    // Nothing is sent until the change is confirmed
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Change guard from Viewer to Operator\?/)).toBeInTheDocument();
    expect(mock.callsTo('PATCH', '/users/2')).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change role' }));
    await waitFor(() => expect(mock.callsTo('PATCH', '/users/2')).toHaveLength(1));
    expect(JSON.parse(mock.callsTo('PATCH', '/users/2')[0].data)).toEqual({ role: 'operator' });
    expect(screen.getByRole('table', { name: 'Audit log', hidden: true })).toHaveTextContent('user.update');
  });

  test('cancelling a role change or deactivation sends nothing', async () => {
    mock = installMockApi({ 'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] } });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Role of guard' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Administrator' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Deactivate guard' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Deactivate guard\?/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    expect(mock.callsTo('PATCH', '/users/2')).toHaveLength(0);
    expect(screen.getByRole('checkbox', { name: 'Deactivate guard' })).toBeChecked();
  });

  test('deactivation is sent only after confirming', async () => {
    mock = installMockApi({
      'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] },
      'PATCH /users/2': (config) => ({ data: { ...USERS[1], ...JSON.parse(config.data) } })
    });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Deactivate guard' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Deactivate' }));
    await waitFor(() => expect(mock.callsTo('PATCH', '/users/2')).toHaveLength(1));
    expect(JSON.parse(mock.callsTo('PATCH', '/users/2')[0].data)).toEqual({ is_active: false });
  });

  const fillAccount = (dialog, { username = 'teacher1', email = 'teacher1@college.edu', password = 'initial-pass-1', confirm = password } = {}) => {
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: username } });
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: email } });
    fireEvent.change(within(dialog).getByLabelText('Initial password'), { target: { value: password } });
    fireEvent.change(within(dialog).getByLabelText('Repeat password'), { target: { value: confirm } });
  };

  test('an admin creates an account with a chosen role; it is added to the list', async () => {
    mock = installMockApi({
      'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] },
      'POST /users/': (config) => {
        const { password, ...rest } = JSON.parse(config.data);
        return { status: 201, data: { ...rest, id: 3, is_active: true, created_at: '2026-10-20T08:00:00Z' } };
      }
    });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    const dialog = await screen.findByRole('dialog');
    fillAccount(dialog, { username: ' teacher1 ' });
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Role' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Operator' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(mock.callsTo('POST', '/users/')).toHaveLength(1));
    expect(JSON.parse(mock.callsTo('POST', '/users/')[0].data)).toEqual({
      username: 'teacher1', email: 'teacher1@college.edu', role: 'operator', password: 'initial-pass-1'
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    const table = screen.getByRole('table', { name: 'Accounts' });
    expect(await within(table).findByText('teacher1')).toBeInTheDocument();
    expect(table).not.toHaveTextContent('initial-pass-1');
  });

  test.each([
    [{ username: 'ab' }, /Username must be 3-50/],
    [{ email: '[a@b.com](mailto:a@b.com)' }, /plain email address/],
    [{ password: 'short' }, /8-72 characters\./],
    [{ password: 'x'.repeat(73) }, /8-72 characters\./],
    [{ confirm: 'something-else' }, /do not match/]
  ])('invalid details %p are explained and nothing is sent', async (changes, message) => {
    mock = installMockApi({ 'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] } });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    const dialog = await screen.findByRole('dialog');
    fillAccount(dialog, changes);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create account' }));
    expect(await within(dialog).findByText(message)).toBeInTheDocument();
    expect(mock.callsTo('POST', '/users/')).toHaveLength(0);
  });

  test('choosing administrator warns and the server reason is shown on a duplicate', async () => {
    mock = installMockApi({
      'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] },
      'POST /users/': { status: 400, data: { detail: 'Username already taken' } }
    });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    const dialog = await screen.findByRole('dialog');
    fillAccount(dialog, { username: 'GUARD' });
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Role' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Administrator' }));
    expect(within(dialog).getByText(/Grant this only to trusted staff/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create administrator' }));
    expect(await within(dialog).findByText('Username already taken')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Accounts', hidden: true })).not.toHaveTextContent('GUARD');
  });

  const STUDENTS = [
    { id: 7, student_id: 'RA001', name: 'Asha', person_type: 'student', deleted_at: null },
    { id: 8, student_id: 'RA002', name: 'Ravi', person_type: 'student', deleted_at: null },
    { id: 9, student_id: 'T01', name: 'Teacher', person_type: 'staff', deleted_at: null }
  ];
  const LINKED = { id: 5, username: 'ravi_s', email: 'r@x.org', role: 'student', student_db_id: 8, is_active: true, created_at: '2026-10-20T08:00:00Z' };

  test('an admin creates a student account linked to one student record', async () => {
    mock = installMockApi({
      'GET /users/': { data: [...USERS, LINKED] }, 'GET /users/audit-log': { data: [] }, 'GET /students/': { data: STUDENTS },
      'POST /users/': (config) => {
        const { password, ...rest } = JSON.parse(config.data);
        return { status: 201, data: { ...rest, id: 6, is_active: true, created_at: '2026-10-20T09:00:00Z' } };
      }
    });
    render(asRole('admin', <UsersPage />, 1));
    const accounts = await screen.findByRole('table', { name: 'Accounts' });
    // Student accounts show their student and have no role menu
    expect(await within(accounts).findByText('Student · Ravi (RA002)')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Role of ravi_s' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    const dialog = await screen.findByRole('dialog');
    fillAccount(dialog, { username: 'asha_s', email: 'asha@college.edu' });
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Role' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Student' }));
    // No record chosen yet: explained, nothing sent
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create account' }));
    expect(await within(dialog).findByText('Choose the student this account belongs to.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Student record'), { target: { value: 'R' } });
    expect(await screen.findByRole('option', { name: 'Ravi (RA002) · already has an account' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByRole('option', { name: /Teacher/ })).not.toBeInTheDocument(); // staff records are not offered
    fireEvent.click(screen.getByRole('option', { name: 'Asha (RA001)' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(mock.callsTo('POST', '/users/')).toHaveLength(1));
    expect(JSON.parse(mock.callsTo('POST', '/users/')[0].data)).toEqual({
      username: 'asha_s', email: 'asha@college.edu', role: 'student', student_db_id: 7, password: 'initial-pass-1'
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    expect(within(screen.getByRole('table', { name: 'Accounts' })).getByText('Student · Asha (RA001)')).toBeInTheDocument();
  });

  test('the dialog is cleared when reopened, so a typed password does not linger', async () => {
    mock = installMockApi({ 'GET /users/': { data: USERS }, 'GET /users/audit-log': { data: [] } });
    render(asRole('admin', <UsersPage />, 1));
    await screen.findByRole('table', { name: 'Accounts' });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    fillAccount(await screen.findByRole('dialog'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Initial password')).toHaveValue('');
    expect(within(dialog).getByLabelText('Username')).toHaveValue('');
    expect(mock.callsTo('POST', '/users/')).toHaveLength(0);
  });
});

describe('presence sessions', () => {
  const SESSIONS = [
    { student_db_id: 1, student_id: 'A1', name: 'Asha', class_name: 'CSE-A', person_type: 'student', date: '2026-10-19',
      entry_at: '2026-10-19T08:55:00', exit_at: '2026-10-19T12:01:00', entry_camera: 'Door IN', exit_camera: 'Door OUT',
      duration_minutes: 186, status: 'complete', manual: false },
    { student_db_id: 2, student_id: 'A2', name: 'Ravi', class_name: 'CSE-A', person_type: 'student', date: '2026-10-19',
      entry_at: '2026-10-19T09:05:00', exit_at: null, entry_camera: 'Door IN', exit_camera: null,
      duration_minutes: null, status: 'no_exit', manual: false }
  ];

  test('pairs are listed with time inside, unpaired ones are flagged, and the range is inclusive dates', async () => {
    mock = installMockApi({ 'GET /attendance/sessions': { data: SESSIONS } });
    render(<MemoryRouter initialEntries={['/attendance?view=sessions']}><AttendancePage /></MemoryRouter>);
    const table = await screen.findByRole('table', { name: 'Presence sessions' });
    expect(within(table).getByText('3 h 6 min')).toBeInTheDocument();
    expect(within(table).getByText('No exit recorded')).toBeInTheDocument();
    expect(screen.getByText(/1 session has a missing entry or exit/)).toBeInTheDocument();
    const { params } = mock.callsTo('GET', '/attendance/sessions')[0];
    expect(params.start_date <= params.end_date).toBe(true);
  });

  test('a manual event requires a reason and is posted as local time', async () => {
    mock = installMockApi({
      'GET /attendance/sessions': { data: SESSIONS },
      'GET /students/': { data: [{ id: 2, student_id: 'A2', name: 'Ravi', is_active: true }] },
      'POST /attendance/manual': { data: { id: 50 } }
    });
    render(<MemoryRouter initialEntries={['/attendance?view=sessions']}><AttendancePage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Record missed event' }));
    const dialog = await screen.findByRole('dialog');
    const person = within(dialog).getByLabelText('Person');
    fireEvent.change(person, { target: { value: 'Ravi' } });
    fireEvent.click(await screen.findByRole('option', { name: 'Ravi (A2)' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record event' }));
    expect(await within(dialog).findByText(/Give a reason/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'Camera blocked by a group' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record event' }));
    await waitFor(() => expect(mock.callsTo('POST', '/attendance/manual')).toHaveLength(1));
    const body = JSON.parse(mock.callsTo('POST', '/attendance/manual')[0].data);
    expect(body).toMatchObject({ student_id: 2, event_type: 'ENTRY', reason: 'Camera blocked by a group' });
    expect(body.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00$/);
  });
});
