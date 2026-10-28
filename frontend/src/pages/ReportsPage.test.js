import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ReportsPage from './ReportsPage';
import { installMockApi } from '../testUtils/mockApi';

// Shapes follow backend app/attendance_logic.py (compute_period / daily_summary / monthly_summary)
const PERIOD = { id: 4, classroom_id: 1, class_name: 'CSE-A', course_code: 'MA101', course_name: 'Maths', day_of_week: 0,
  start_time: '09:00:00', end_time: '10:00:00', grace_minutes: 10, is_active: true, day_name: 'Monday' };
const student = (overrides) => ({ student_db_id: 1, student_id: 'A1', name: 'Asha', status: 'PRESENT', computed_status: 'PRESENT',
  overridden: false, override_reason: null, arrived_at: '2026-10-19T08:58:00', inside_at_start: false, left_at: null,
  returned_during_period: false, minutes_inside: 60, exceptions: [], ...overrides });
const RESULT = {
  period_id: 4, date: '2026-10-19', class_name: 'CSE-A', course_code: 'MA101', course_name: 'Maths', classroom_id: 1,
  start_time: '09:00', end_time: '10:00', grace_minutes: 10, phase: 'completed', holiday: null, camera_coverage: true,
  roster_size: 3, counts: { PRESENT: 1, LATE: 1, ABSENT: 1, UNVERIFIED: 0, NOT_ARRIVED: 0, NOT_STARTED: 0, EXCUSED: 0, HOLIDAY: 0 },
  attendance_rate: 66.7,
  students: [
    student(),
    student({ student_db_id: 2, student_id: 'A2', name: 'Ravi', status: 'LATE', computed_status: 'LATE', arrived_at: '2026-10-19T09:25:00',
      minutes_inside: 35, exceptions: [{ type: 'LATE_ARRIVAL', reason: 'First entry at 09:25, after 09:10' }] }),
    student({ student_db_id: 3, student_id: 'A3', name: 'Meera', status: 'ABSENT', computed_status: 'ABSENT', arrived_at: null,
      minutes_inside: 0, exceptions: [{ type: 'ABSENT', reason: 'No entry recorded while an entry camera was running' }] })
  ]
};

let mock;
afterEach(() => mock?.restore());

const renderPage = (routes = {}, path = '/reports') => {
  mock = installMockApi({
    'GET /timetable/periods': { data: [PERIOD] },
    'GET /timetable/periods/4/attendance': { data: RESULT },
    'GET /students/classes': { data: ['CSE-A'] },
    ...routes
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/reports" element={<ReportsPage />} /></Routes>
    </MemoryRouter>
  );
};

test('period attendance shows statuses, exceptions and the rate for the chosen date', async () => {
  renderPage();
  const table = await screen.findByRole('table', { name: 'Period attendance' });
  expect(within(table).getByText('Asha')).toBeInTheDocument();
  expect(within(table).getByText('Late')).toBeInTheDocument();
  expect(within(table).getByText('Late arrival')).toBeInTheDocument();
  expect(within(table).getAllByText('Absent')).toHaveLength(2); // status and exception
  expect(screen.getByLabelText('Attendance rate')).toHaveTextContent('66.7%');
  const [periodsCall] = mock.callsTo('GET', '/timetable/periods');
  expect(Object.keys(periodsCall.params)).toEqual(['day_of_week']);
  expect(mock.callsTo('GET', '/timetable/periods/4/attendance')[0].params.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
});

test('no camera coverage is explained instead of marking people absent', async () => {
  renderPage({ 'GET /timetable/periods/4/attendance': { data: { ...RESULT, camera_coverage: false } } });
  expect(await screen.findByText(/No entry camera was running during this period/)).toBeInTheDocument();
});

test('an override needs a reason and is sent for the selected date', async () => {
  let saved = null;
  renderPage({ 'PUT /timetable/periods/4/attendance/3': (config) => { saved = config; return { data: { message: 'Override saved' } }; } });
  fireEvent.click(await screen.findByRole('button', { name: 'Override Meera' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save override' }));
  expect(await within(dialog).findByText(/A reason is required/)).toBeInTheDocument();
  expect(saved).toBeNull();
  fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'Medical certificate' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save override' }));
  await waitFor(() => expect(saved).not.toBeNull());
  expect(JSON.parse(saved.data)).toEqual({ status: 'EXCUSED', reason: 'Medical certificate' });
  expect(saved.params.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
});

test('a day without periods says so', async () => {
  renderPage({ 'GET /timetable/periods': { data: [] } });
  expect(await screen.findByText('No periods on this day')).toBeInTheDocument();
});

test('daily summary lists periods with coverage and the exceptions', async () => {
  renderPage({
    'GET /timetable/daily': {
      data: {
        date: '2026-10-19',
        periods: [{ ...RESULT, students: undefined }],
        exceptions: [{ date: '2026-10-19', period_id: 4, course_name: 'Maths', class_name: 'CSE-A', start_time: '09:00',
          student_db_id: 2, student_id: 'A2', name: 'Ravi', type: 'LATE_ARRIVAL', reason: 'First entry at 09:25', basis: 'recorded attendance events' }]
      }
    }
  }, '/reports?tab=daily');
  const table = await screen.findByRole('table', { name: 'Daily summary' });
  expect(within(table).getByText('Maths')).toBeInTheDocument();
  expect(within(table).getByText('Camera on')).toBeInTheDocument();
  const exceptions = screen.getByRole('table', { name: 'Exceptions' });
  expect(within(exceptions).getByText('Ravi (A2)')).toBeInTheDocument();
  expect(within(exceptions).getByText('First entry at 09:25')).toBeInTheDocument();
});

test('monthly summary highlights low attendance and filters by class', async () => {
  renderPage({
    'GET /timetable/monthly': (config) => ({
      data: {
        year: config.params.year, month: config.params.month, through: '2026-10-19', days_with_classes: 12,
        students: [{ student_db_id: 1, student_id: 'A1', name: 'Asha', class_name: 'CSE-A', scheduled: 20, present: 12, late: 2,
          absent: 6, excused: 0, unverified: 0, early_departures: 1, minutes_inside: 900, attendance_rate: 70 }]
      }
    })
  }, '/reports?tab=monthly');
  const table = await screen.findByRole('table', { name: 'Monthly summary' });
  expect(within(table).getByText('70%')).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText('Class'));
  fireEvent.click(await screen.findByRole('option', { name: 'CSE-A' }));
  await waitFor(() => expect(mock.callsTo('GET', '/timetable/monthly').at(-1).params.class_name).toBe('CSE-A'));
});
