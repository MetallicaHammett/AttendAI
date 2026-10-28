import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import StudentsPage, { STUDENT_LIST_LIMIT } from './StudentsPage';
import { installMockApi, headerOf } from '../testUtils/mockApi';

// Shapes follow backend schemas.StudentResponse
const ALICE = { id: 1, student_id: 'S001', name: 'Alice Smith', class_name: '10A', department: 'Science', is_active: true, created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const BOB = { id: 2, student_id: 'S002', name: 'Bob Jones', class_name: '10B', department: 'Arts', is_active: true, created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const CARA = { id: 3, student_id: 'S003', name: 'Cara Lee', class_name: null, department: null, is_active: false, created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const DAN_DELETED = { id: 4, student_id: 'S004', name: 'Dan Old', class_name: '9A', department: 'Arts', is_active: false, created_at: '2026-09-01T08:00:00Z', updated_at: '2026-10-10T08:00:00Z', deleted_at: '2026-10-10T08:00:00Z' };

const listRoute = (config) => ({
  data: config.params?.include_deleted ? [ALICE, BOB, CARA, DAN_DELETED] : [ALICE, BOB, CARA]
});

let mock;
beforeEach(() => {
  global.URL.createObjectURL = jest.fn(() => 'blob:preview-1');
  global.URL.revokeObjectURL = jest.fn();
});
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const renderPage = async (routes = {}) => {
  mock = installMockApi({ 'GET /students/': listRoute, ...routes });
  render(<MemoryRouter><StudentsPage /></MemoryRouter>);
  await screen.findByRole('table', { name: 'Students' });
};

// While a MUI dialog is open (or still running its close transition) the page behind it
// is aria-hidden, so role queries on the table must wait for the dialog to go away.
// Role queries on this page are slow (~0.8 s), hence the longer timeout.
const waitForDialogToClose = () =>
  waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });

const rowFor = (name, options = {}) =>
  screen.getAllByRole('row', options).find((row) => within(row).queryByRole('cell', { name, ...options }));
const studentNames = (options = {}) =>
  within(screen.getByRole('table', { name: 'Students', ...options })).getAllByRole('row', options).slice(1)
    .map((row) => within(row).getAllByRole('cell', options)[0].textContent);

describe('list', () => {
  test('loads through the shared client with auth, a high limit and no deleted students', async () => {
    localStorage.setItem('token', 'test.token.value');
    await renderPage();
    const [call] = mock.callsTo('GET', '/students/');
    expect(headerOf(call, 'Authorization')).toBe('Bearer test.token.value');
    expect(call.params).toEqual({ limit: STUDENT_LIST_LIMIT, include_deleted: false });
    expect(studentNames()).toEqual(['Alice Smith', 'Bob Jones', 'Cara Lee']);
    expect(within(rowFor('Cara Lee')).getByText('Inactive')).toBeInTheDocument();
    expect(within(rowFor('Alice Smith')).getByText('Active')).toBeInTheDocument();
  });

  test('search filters by name, student ID, class and department', async () => {
    await renderPage();
    const search = screen.getByLabelText('Search students');
    fireEvent.change(search, { target: { value: 'bob' } });
    expect(studentNames()).toEqual(['Bob Jones']);
    fireEvent.change(search, { target: { value: 'S001' } });
    expect(studentNames()).toEqual(['Alice Smith']);
    fireEvent.change(search, { target: { value: '10' } });
    expect(studentNames()).toEqual(['Alice Smith', 'Bob Jones']);
    fireEvent.change(search, { target: { value: 'science' } });
    expect(studentNames()).toEqual(['Alice Smith']);
    fireEvent.change(search, { target: { value: 'nobody' } });
    expect(screen.getByText('No students match “nobody”.')).toBeInTheDocument();
    expect(mock.callsTo('GET', '/students/')).toHaveLength(1); // client-side only
  });

  test('"Show deleted" requests include_deleted=true and shows deleted rows read-only', async () => {
    await renderPage();
    fireEvent.click(screen.getByLabelText('Show deleted'));
    await screen.findByRole('cell', { name: 'Dan Old' });
    expect(mock.callsTo('GET', '/students/').at(-1).params).toEqual({ limit: STUDENT_LIST_LIMIT, include_deleted: true });
    const row = rowFor('Dan Old');
    expect(within(row).getByText(/^Deleted /)).toBeInTheDocument();
    expect(within(row).getByText('Read only')).toBeInTheDocument();
    expect(within(row).queryByRole('button')).not.toBeInTheDocument();
  });

  test('a load error shows the backend reason and Retry reloads', async () => {
    let fail = true;
    mock = installMockApi({
      'GET /students/': (config) => (fail ? { status: 500, data: { detail: 'Database is locked' } } : listRoute(config))
    });
    render(<MemoryRouter><StudentsPage /></MemoryRouter>);
    expect(await screen.findByText('Database is locked')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('cell', { name: 'Alice Smith' })).toBeInTheDocument();
  });
});

describe('add and edit', () => {
  test('adds a student; blank optional fields are sent as null', async () => {
    const created = { ...BOB, id: 9, student_id: 'S009', name: 'Eve New', class_name: null, department: null };
    await renderPage({ 'POST /students/': { data: created } });
    fireEvent.click(screen.getByRole('button', { name: 'Add student' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/student id/i), { target: { value: ' S009 ' } });
    fireEvent.change(within(dialog).getByLabelText(/full name/i), { target: { value: 'Eve New' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add student' }));

    await waitForDialogToClose();
    expect(studentNames()).toEqual(['Alice Smith', 'Bob Jones', 'Cara Lee', 'Eve New']);
    expect(JSON.parse(mock.callsTo('POST', '/students/')[0].data)).toEqual({
      student_id: 'S009', name: 'Eve New', class_name: null, department: null, is_active: true, person_type: 'student'
    });
    expect(screen.getByText('Added Eve New.')).toBeInTheDocument();
  });

  test('requires student ID and name before calling the backend', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Add student' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add student' }));
    expect(await within(dialog).findByText('Student ID is required.')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/students/')).toHaveLength(0);
  });

  test('shows the backend reason when adding fails', async () => {
    await renderPage({
      'POST /students/': { status: 400, data: { detail: 'Student ID already exists (belongs to a deleted student)' } }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add student' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/student id/i), { target: { value: 'S004' } });
    fireEvent.change(within(dialog).getByLabelText(/full name/i), { target: { value: 'Dan Again' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add student' }));
    expect(await within(dialog).findByText('Student ID already exists (belongs to a deleted student)')).toBeInTheDocument();
  });

  test('edit sends the full object with PUT and keeps the student ID fixed', async () => {
    const updated = { ...ALICE, name: 'Alice Smith-Jones', is_active: false, updated_at: '2026-10-19T09:00:00Z' };
    await renderPage({ 'PUT /students/1': { data: updated } });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Alice Smith' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(/student id/i)).toBeDisabled();
    expect(within(dialog).getByLabelText(/student id/i)).toHaveValue('S001');
    fireEvent.change(within(dialog).getByLabelText(/full name/i), { target: { value: 'Alice Smith-Jones' } });
    fireEvent.click(within(dialog).getByLabelText(/^active/i));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitForDialogToClose();
    expect(studentNames()).toEqual(['Alice Smith-Jones', 'Bob Jones', 'Cara Lee']);
    expect(JSON.parse(mock.callsTo('PUT', '/students/1')[0].data)).toEqual({
      student_id: 'S001', name: 'Alice Smith-Jones', class_name: '10A', department: 'Science', is_active: false,
      person_type: 'student'
    });
    expect(within(rowFor('Alice Smith-Jones')).getByText('Inactive')).toBeInTheDocument();
  });

  test('shows the backend reason when editing fails', async () => {
    await renderPage({ 'PUT /students/2': { status: 404, data: { detail: 'Student not found' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Bob Jones' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    expect(await within(dialog).findByText('Student not found')).toBeInTheDocument();
  });
});

describe('delete', () => {
  test('asks for confirmation; cancel sends nothing', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Delete Bob Jones' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/attendance history is kept/i);
    expect(dialog).toHaveTextContent('Bob Jones (S002)');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mock.callsTo('DELETE', '/students/2')).toHaveLength(0);
  });

  test('confirming soft-deletes and reloads the list', async () => {
    let deleted = false;
    await renderPage({
      'GET /students/': (config) => ({
        data: deleted ? (config.params.include_deleted ? [ALICE, CARA, { ...BOB, deleted_at: '2026-10-19T09:00:00Z' }] : [ALICE, CARA]) : [ALICE, BOB, CARA]
      }),
      'DELETE /students/2': () => { deleted = true; return { data: { message: 'Student deleted successfully' } }; }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Bob Jones' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete student' }));
    expect(await screen.findByText('Deleted Bob Jones. Their attendance history is kept.')).toBeInTheDocument();
    await waitForDialogToClose();
    expect(studentNames()).toEqual(['Alice Smith', 'Cara Lee']);
    expect(mock.callsTo('DELETE', '/students/2')).toHaveLength(1);
    expect(mock.callsTo('GET', '/students/')).toHaveLength(2);
  });

  test('a failed delete keeps the dialog open with the backend reason', async () => {
    await renderPage({ 'DELETE /students/2': { status: 404, data: { detail: 'Student not found' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Bob Jones' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete student' }));
    expect(await within(dialog).findByText('Student not found')).toBeInTheDocument();
    // dialog stays open (page behind it is aria-hidden) and the row is still listed
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(studentNames({ hidden: true })).toContain('Bob Jones');
  });
});

describe('face enrollment', () => {
  const photo = (name = 'face.jpg', type = 'image/jpeg', size = 2048) =>
    new File([new Uint8Array(size)], name, { type });

  const openEnroll = async (routes = {}) => {
    await renderPage(routes);
    fireEvent.click(screen.getByRole('button', { name: 'Enroll face for Alice Smith' }));
    return screen.findByRole('dialog');
  };

  test('uploads the photo as multipart field "file" and confirms', async () => {
    const dialog = await openEnroll({
      'POST /face-enrollment/1': { data: { id: 11, student_id: 1, is_active: true, created_at: '2026-10-19T09:00:00Z', updated_at: null } }
    });
    fireEvent.change(screen.getByTestId('face-file-input'), { target: { files: [photo()] } });
    expect(within(dialog).getByAltText('Selected photo of Alice Smith')).toHaveAttribute('src', 'blob:preview-1');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Upload and enroll' }));

    expect(await within(dialog).findByText(/face enrolled for alice smith/i)).toBeInTheDocument();
    const [call] = mock.callsTo('POST', '/face-enrollment/1');
    expect(call.data).toBeInstanceOf(FormData);
    expect(call.data.get('file').name).toBe('face.jpg');
    expect(call.data.get('file').type).toBe('image/jpeg');
    // preview URL released once the upload replaced the selection
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-1');
  });

  test.each([
    [{ name: 'notes.txt', type: 'text/plain' }, 'Choose a JPEG or PNG image.'],
    [{ name: 'huge.jpg', type: 'image/jpeg', size: 5 * 1024 * 1024 + 1 }, 'The image is larger than 5 MB.']
  ])('rejects invalid files before uploading (%o)', async (spec, message) => {
    const dialog = await openEnroll();
    fireEvent.change(screen.getByTestId('face-file-input'), { target: { files: [photo(spec.name, spec.type, spec.size)] } });
    expect(within(dialog).getByText(message)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Upload and enroll' })).toBeDisabled();
    expect(mock.callsTo('POST', '/face-enrollment/1')).toHaveLength(0);
  });

  test.each([
    [400, 'No face detected in the image'],
    [400, 'Multiple faces detected. Please provide an image with exactly one face'],
    [413, 'File too large. Maximum size is 5 MB'],
    [503, 'Face recognition is not available on this server (face_recognition could not be imported)']
  ])('shows the backend reason for HTTP %i', async (status, detail) => {
    const dialog = await openEnroll({ 'POST /face-enrollment/1': { status, data: { detail } } });
    fireEvent.change(screen.getByTestId('face-file-input'), { target: { files: [photo('face.png', 'image/png')] } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Upload and enroll' }));
    expect(await within(dialog).findByText(detail)).toBeInTheDocument();
    expect(within(dialog).queryByText(/face enrolled/i)).not.toBeInTheDocument();
  });

  test('closing the dialog releases the preview URL', async () => {
    const dialog = await openEnroll();
    fireEvent.change(screen.getByTestId('face-file-input'), { target: { files: [photo()] } });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-1'));
  });

  test('is disabled for inactive students (the backend rejects them)', async () => {
    await renderPage();
    expect(screen.getByRole('button', { name: 'Enroll face for Cara Lee' })).toBeDisabled();
  });
});

describe('enrollment status, filters, import and details', () => {
  const ENROLLED = { ...ALICE, enrolled_faces: 2 };
  const NOT_ENROLLED = { ...BOB, enrolled_faces: 0 };
  const STAFF = { ...CARA, id: 5, student_id: 'T01', name: 'Tara Staff', is_active: true, person_type: 'staff', enrolled_faces: 1 };
  const people = { data: [ENROLLED, NOT_ENROLLED, STAFF] };

  test('shows enrollment status, warns about unenrolled people and filters by type and enrollment', async () => {
    await renderPage({ 'GET /students/': people });
    expect(within(rowFor('Alice Smith')).getByText('2 faces')).toBeInTheDocument();
    expect(within(rowFor('Bob Jones')).getByText('Not enrolled')).toBeInTheDocument();
    expect(screen.getByText(/1 active person is not enrolled/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show them' }));
    expect(studentNames()).toEqual(['Bob Jones']);
    fireEvent.mouseDown(screen.getByLabelText('Enrollment'));
    fireEvent.click(await screen.findByRole('option', { name: 'Any' }));
    fireEvent.mouseDown(screen.getByLabelText('Type'));
    fireEvent.click(await screen.findByRole('option', { name: 'Staff' }));
    await waitFor(() => expect(studentNames()).toEqual(['Tara StaffStaff']));
  });

  test('sorting by name', async () => {
    await renderPage({ 'GET /students/': { data: [NOT_ENROLLED, ENROLLED] } });
    fireEvent.click(screen.getByRole('button', { name: 'Name' }));
    expect(studentNames()).toEqual(['Alice Smith', 'Bob Jones']);
    fireEvent.click(screen.getByRole('button', { name: 'Name' }));
    expect(studentNames()).toEqual(['Bob Jones', 'Alice Smith']);
  });

  test('roster import validates first and imports only a clean file', async () => {
    let imported = false;
    await renderPage({
      'POST /students/import': (config) => (config.params.dry_run
        ? { data: { dry_run: true, total_rows: 2, valid_rows: 2, errors: [], created: 0 } }
        : (imported = true, { data: { dry_run: false, total_rows: 2, valid_rows: 2, errors: [], created: 2 } }))
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import CSV' }));
    const dialog = await screen.findByRole('dialog');
    const file = new File(['student_id,name\nA,B\nC,D\n'], 'roster.csv', { type: 'text/csv' });
    fireEvent.change(within(dialog).getByTestId('roster-file-input'), { target: { files: [file] } });
    expect(await within(dialog).findByText('All 2 rows are valid and ready to import.')).toBeInTheDocument();
    expect(imported).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import 2 people' }));
    expect(await screen.findByText('Imported 2 people from the roster file.')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/students/import').map((c) => c.params.dry_run)).toEqual([true, false]);
  });

  test('roster problems are listed per line and block the import', async () => {
    await renderPage({
      'POST /students/import': { data: { dry_run: true, total_rows: 2, valid_rows: 1, created: 0,
        errors: [{ row: 3, field: 'student_id', message: "'S001' already exists" }] } }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import CSV' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByTestId('roster-file-input'),
      { target: { files: [new File(['x'], 'roster.csv', { type: 'text/csv' })] } });
    const problems = await within(dialog).findByRole('table', { name: 'Import problems' });
    expect(within(problems).getByText("'S001' already exists")).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Import' })).toBeDisabled();
  });

  test('details drawer shows face records and history, and erases a face after confirmation', async () => {
    let faces = [{ id: 11, is_active: true, created_at: '2026-10-19T09:00:00Z' }];
    await renderPage({
      'GET /students/': people,
      'GET /face-enrollment/1': () => ({ data: faces }),
      'GET /attendance/sessions': { data: [{ student_db_id: 1, date: '2026-10-19', entry_at: '2026-10-19T08:55:00',
        exit_at: '2026-10-19T12:01:00', duration_minutes: 186, status: 'complete', manual: false }] },
      'DELETE /face-enrollment/1/11': () => { faces = []; return { data: { message: 'Face record deleted' } }; }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Details for Alice Smith' }));
    const region = await screen.findByRole('region', { name: 'Details for Alice Smith' });
    expect(await within(region).findByText('Face record 1')).toBeInTheDocument();
    expect(within(region).getByText('3 h 6 min')).toBeInTheDocument();
    fireEvent.click(within(region).getByRole('button', { name: 'Delete' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));
    expect(await screen.findByText(/Not enrolled: cameras cannot recognise Alice Smith/)).toBeInTheDocument();
  });

  test('viewers see no editing controls', async () => {
    const { AuthContext } = require('../context/AuthContext');
    mock = installMockApi({ 'GET /students/': people });
    render(
      <AuthContext.Provider value={{ user: { username: 'v', role: 'viewer' } }}>
        <MemoryRouter><StudentsPage /></MemoryRouter>
      </AuthContext.Provider>
    );
    await screen.findByRole('table', { name: 'Students' });
    // Viewing attendance changes nothing, so every staff role gets the link
    expect(screen.getByRole('link', { name: 'Attendance of Alice Smith' })).toHaveAttribute('href', '/students/1/attendance');
    expect(screen.queryByRole('button', { name: 'Add student' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import CSV' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit Alice Smith' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete Alice Smith' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Details for Alice Smith' })).toBeInTheDocument();
  });
});
