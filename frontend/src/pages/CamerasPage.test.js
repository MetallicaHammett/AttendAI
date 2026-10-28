import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CamerasPage, { CAMERA_LIST_LIMIT } from './CamerasPage';
import { installMockApi, headerOf } from '../testUtils/mockApi';

// Shapes follow backend schemas.CameraResponse and GET /video/sessions
const DOOR_IN = { id: 1, name: 'Main door', source: '0', source_type: 'webcam', camera_type: 'IN', is_active: true, width: 640, height: 480, fps: 30, description: 'Front entrance', created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const DOOR_OUT = { id: 2, name: 'Exit gate', source: 'rtsp://cam.local/exit', source_type: 'rtsp', camera_type: 'OUT', is_active: true, width: 1280, height: 720, fps: 15, description: null, created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const SPARE = { id: 3, name: 'Spare', source: 'C:/videos/test.mp4', source_type: 'video', camera_type: 'IN', is_active: false, width: 640, height: 480, fps: 30, description: null, created_at: '2026-10-01T08:00:00Z', updated_at: null, deleted_at: null };
const OLD = { ...SPARE, id: 4, name: 'Old lab cam', deleted_at: '2026-10-05T08:00:00Z' };
const SESSIONS = { data: { active_sessions: [{ camera_id: 2, camera_name: 'Exit gate', is_processing: true, frames_processed: 10, duration_seconds: 4, service_stats: {} }], count: 1 } };

const listRoute = (config) => ({ data: config.params?.include_deleted ? [DOOR_IN, DOOR_OUT, SPARE, OLD] : [DOOR_IN, DOOR_OUT, SPARE] });

const waitForDialogToClose = () =>
  waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
const cameraNames = (options = {}) =>
  within(screen.getByRole('table', { name: 'Cameras', ...options })).getAllByRole('rowheader', options)
    // first text element in the name cell is the camera name (the description follows it)
    .map((cell) => within(cell).getAllByText(/\S/, options)[0].textContent);
const rowFor = (name, options = {}) =>
  screen.getAllByRole('row', options).find((row) => within(row).queryByRole('rowheader', { name: new RegExp(`^${name}`), ...options }));

let mock;
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const renderPage = async (routes = {}) => {
  mock = installMockApi({ 'GET /cameras/': listRoute, 'GET /video/sessions': SESSIONS,
    'GET /timetable/classrooms': { data: [] }, ...routes });
  render(<CamerasPage />);
  await screen.findByRole('table', { name: 'Cameras' });
};

const fillForm = (dialog, values) => {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(within(dialog).getByLabelText(new RegExp(label, 'i')), { target: { value } });
  }
};

// MUI select: open the listbox and pick an option
const choose = async (dialog, label, optionText) => {
  fireEvent.mouseDown(within(dialog).getByLabelText(new RegExp(label, 'i')));
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(optionText) }));
};

describe('list', () => {
  test('loads cameras and processing sessions through the shared client', async () => {
    localStorage.setItem('token', 'test.token.value');
    await renderPage();
    const [call] = mock.callsTo('GET', '/cameras/');
    expect(headerOf(call, 'Authorization')).toBe('Bearer test.token.value');
    expect(call.params).toEqual({ limit: CAMERA_LIST_LIMIT, include_deleted: false });
    expect(mock.callsTo('GET', '/video/sessions')).toHaveLength(1);
    expect(cameraNames()).toEqual(['Main door', 'Exit gate', 'Spare']);
    const exit = rowFor('Exit gate');
    expect(within(exit).getByText('OUT')).toBeInTheDocument();
    expect(within(exit).getByText('Processing')).toBeInTheDocument();
    expect(within(exit).getByText(/RTSP \/ IP stream/)).toBeInTheDocument();
    expect(within(exit).getByText(/· live/)).toBeInTheDocument();
    expect(within(rowFor('Spare')).getByText(/· recorded/)).toBeInTheDocument();
    expect(within(exit).getByText('1280×720 @ 15 fps')).toBeInTheDocument();
    expect(within(rowFor('Spare')).getByText('Inactive')).toBeInTheDocument();
    expect(within(rowFor('Main door')).queryByText('Processing')).not.toBeInTheDocument();
  });

  test('search filters by name, source, type and description', async () => {
    await renderPage();
    const search = screen.getByLabelText('Search cameras');
    fireEvent.change(search, { target: { value: 'rtsp' } });
    expect(cameraNames()).toEqual(['Exit gate']);
    fireEvent.change(search, { target: { value: 'entrance' } });
    expect(cameraNames()).toEqual(['Main door']);
    fireEvent.change(search, { target: { value: 'out' } });
    expect(cameraNames()).toEqual(['Exit gate']);
    fireEvent.change(search, { target: { value: 'nothing-like-this' } });
    expect(screen.getByText('No cameras match “nothing-like-this”.')).toBeInTheDocument();
  });

  test('"Show deleted" requests include_deleted=true; deleted cameras are read-only', async () => {
    await renderPage();
    fireEvent.click(screen.getByLabelText('Show deleted'));
    await screen.findByText('Old lab cam');
    expect(mock.callsTo('GET', '/cameras/').at(-1).params).toEqual({ limit: CAMERA_LIST_LIMIT, include_deleted: true });
    const row = rowFor('Old lab cam');
    expect(within(row).getByText(/^Deleted /)).toBeInTheDocument();
    expect(within(row).queryByRole('button')).not.toBeInTheDocument();
  });

  test('a failed sessions call still shows cameras with a warning', async () => {
    await renderPage({ 'GET /video/sessions': { status: 500, data: { detail: 'boom' } } });
    expect(cameraNames()).toEqual(['Main door', 'Exit gate', 'Spare']);
    expect(screen.getByText('Processing status unavailable: boom')).toBeInTheDocument();
  });

  test('a failed camera load shows the backend reason with Retry', async () => {
    let fail = true;
    mock = installMockApi({
      'GET /cameras/': (config) => (fail ? { status: 500, data: { detail: 'Database is locked' } } : listRoute(config)),
      'GET /video/sessions': SESSIONS
    });
    render(<CamerasPage />);
    expect(await screen.findByText('Database is locked')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('table', { name: 'Cameras' })).toBeInTheDocument();
  });

  test('empty state', async () => {
    mock = installMockApi({ 'GET /cameras/': { data: [] }, 'GET /video/sessions': { data: { active_sessions: [], count: 0 } } });
    render(<CamerasPage />);
    expect(await screen.findByText(/no cameras yet/i)).toBeInTheDocument();
  });
});

describe('add and edit', () => {
  test('requires camera_type (regression: it used to be missing -> 422) and sends numbers', async () => {
    const created = { ...DOOR_IN, id: 9, name: 'Library', camera_type: 'OUT', width: 800, height: 600, fps: 10, description: null };
    await renderPage({ 'POST /cameras/': { data: created } });
    fireEvent.click(screen.getByRole('button', { name: 'Add camera' }));
    const dialog = await screen.findByRole('dialog');
    fillForm(dialog, { 'camera name': 'Library', '^source( \\*)?$': '1', 'width': '800', 'height': '600', '^fps': '10' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add camera' }));
    expect(await within(dialog).findByText('Choose whether this is an IN or OUT camera.')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/cameras/')).toHaveLength(0);

    await choose(dialog, 'camera type', 'OUT');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add camera' }));
    await waitForDialogToClose();
    expect(JSON.parse(mock.callsTo('POST', '/cameras/')[0].data)).toEqual({
      name: 'Library', source: '1', source_type: 'webcam', camera_type: 'OUT', is_active: true,
      width: 800, height: 600, fps: 10, description: null, classroom_id: null
    });
    expect(cameraNames()).toContain('Library');
    expect(screen.getByText('Added Library.')).toBeInTheDocument();
  });

  test.each([
    [{ width: '0' }, 'Width must be a whole number greater than 0.'],
    [{ height: '-5' }, 'Height must be a whole number greater than 0.'],
    [{ '^fps': '2.5' }, 'FPS must be a whole number greater than 0.'],
    [{ 'camera name': '' }, 'Camera name is required.'],
    [{ '^source( \\*)?$': '' }, 'Source is required.']
  ])('validates %o before calling the backend', async (values, message) => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Main door' }));
    const dialog = await screen.findByRole('dialog');
    fillForm(dialog, values);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    expect(await within(dialog).findByText(message)).toBeInTheDocument();
    expect(mock.callsTo('PUT', '/cameras/1')).toHaveLength(0);
  });

  test('edit sends the full object with PUT', async () => {
    await renderPage({ 'PUT /cameras/1': (config) => ({ data: { ...DOOR_IN, ...JSON.parse(config.data) } }) });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Main door' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(/^source( \*)?$/i)).toHaveValue('0');
    fillForm(dialog, { 'camera name': 'Main door (north)', '^fps': '25' });
    await choose(dialog, 'source type', 'Video file');
    fireEvent.change(within(dialog).getByLabelText(/^source( \*)?$/i), { target: { value: 'D:/clips/door.mp4' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    await waitForDialogToClose();
    expect(JSON.parse(mock.callsTo('PUT', '/cameras/1')[0].data)).toEqual({
      name: 'Main door (north)', source: 'D:/clips/door.mp4', source_type: 'video', camera_type: 'IN',
      is_active: true, width: 640, height: 480, fps: 25, description: 'Front entrance', classroom_id: null
    });
    expect(cameraNames()).toContain('Main door (north)');
  });

  test('editing a processing camera says a restart is needed', async () => {
    await renderPage({ 'PUT /cameras/2': (config) => ({ data: { ...DOOR_OUT, ...JSON.parse(config.data) } }) });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Exit gate' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText(/it is processing now: restart it in live monitoring/i)).toBeInTheDocument();
  });

  test('shows the backend reason when saving fails', async () => {
    await renderPage({
      'POST /cameras/': { status: 422, data: { detail: [{ loc: ['body', 'source'], msg: 'String should have at most 255 characters' }] } }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add camera' }));
    const dialog = await screen.findByRole('dialog');
    fillForm(dialog, { 'camera name': 'X', '^source( \\*)?$': 'y' });
    await choose(dialog, 'camera type', 'IN');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add camera' }));
    expect(await within(dialog).findByText('source: String should have at most 255 characters')).toBeInTheDocument();
  });
});

describe('activate / deactivate', () => {
  test('POST /cameras/{id}/toggle updates the row with the backend message', async () => {
    await renderPage({ 'POST /cameras/3/toggle': { data: { message: 'Camera activated successfully' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Activate Spare' }));
    expect(await screen.findByText('Spare: Camera activated successfully')).toBeInTheDocument();
    expect(within(rowFor('Spare')).getByText('Active')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Deactivate Spare' })).toBeInTheDocument();
  });

  test('deactivating a processing camera warns that processing keeps running', async () => {
    await renderPage({ 'POST /cameras/2/toggle': { data: { message: 'Camera deactivated successfully' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate Exit gate' }));
    expect(await screen.findByText(/processing is still running; stop it in live monitoring/i)).toBeInTheDocument();
  });

  test('a failed toggle shows the reason and leaves the row unchanged', async () => {
    await renderPage({ 'POST /cameras/1/toggle': { status: 404, data: { detail: 'Camera not found' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate Main door' }));
    expect(await screen.findByText('Camera not found')).toBeInTheDocument();
    expect(within(rowFor('Main door')).getByText('Active')).toBeInTheDocument();
  });
});

describe('delete', () => {
  test('cancel sends nothing', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Delete Spare' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/attendance recorded by this camera is kept/i);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitForDialogToClose();
    expect(mock.callsTo('DELETE', '/cameras/3')).toHaveLength(0);
  });

  test('confirm soft-deletes and reloads', async () => {
    let deleted = false;
    await renderPage({
      'GET /cameras/': () => ({ data: deleted ? [DOOR_IN, DOOR_OUT] : [DOOR_IN, DOOR_OUT, SPARE] }),
      'DELETE /cameras/3': () => { deleted = true; return { data: { message: 'Camera deleted successfully' } }; }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Spare' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete camera' }));
    expect(await screen.findByText('Deleted Spare. Its attendance history is kept.')).toBeInTheDocument();
    await waitForDialogToClose();
    expect(cameraNames()).toEqual(['Main door', 'Exit gate']);
  });

  test('a 409 (camera processing) keeps the dialog open with the backend reason', async () => {
    await renderPage({
      'DELETE /cameras/2': { status: 409, data: { detail: 'Stop video processing for this camera before deleting it' } }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Exit gate' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete camera' }));
    expect(await within(dialog).findByText('Stop video processing for this camera before deleting it')).toBeInTheDocument();
    expect(cameraNames({ hidden: true })).toContain('Exit gate');
  });
});

describe('connection test, classrooms and roles', () => {
  test('testing a webcam asks for explicit device confirmation before calling the server', async () => {
    await renderPage({
      'POST /cameras/1/test': { data: { camera_id: 1, ok: true, tested: true, detail: 'Opened and read a frame' } }
    });
    fireEvent.click(screen.getByRole('button', { name: 'Test Main door' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/switches on webcam device 0/)).toBeInTheDocument();
    expect(mock.callsTo('POST', '/cameras/1/test')).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, open device 0' }));
    expect(await screen.findByText('Test: OK - Opened and read a frame')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/cameras/1/test')[0].params).toEqual({ confirm_device: true });
  });

  test('a failed stream test shows the reason', async () => {
    await renderPage({ 'POST /cameras/2/test': { data: { camera_id: 2, ok: false, tested: true, detail: 'Could not open the source' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Test Exit gate' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Run test' }));
    expect(await screen.findByText('Test: failed - Could not open the source')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/cameras/2/test')[0].params).toBeUndefined();
  });

  test('classrooms appear as a column and can be assigned', async () => {
    const rooms = [{ id: 7, name: 'Room 101' }];
    await renderPage({
      'GET /cameras/': () => ({ data: [{ ...DOOR_IN, classroom_id: 7 }, DOOR_OUT] }),
      'GET /timetable/classrooms': { data: rooms },
      'PUT /cameras/2': (config) => ({ data: { ...DOOR_OUT, ...JSON.parse(config.data) } })
    });
    expect(within(rowFor('Main door')).getByText('Room 101')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Exit gate' }));
    const dialog = await screen.findByRole('dialog');
    await choose(dialog, 'classroom', 'Room 101');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    await waitForDialogToClose();
    expect(JSON.parse(mock.callsTo('PUT', '/cameras/2')[0].data).classroom_id).toBe(7);
  });

  test('operators can test cameras but not add, edit or delete them', async () => {
    const { AuthContext } = require('../context/AuthContext');
    mock = installMockApi({ 'GET /cameras/': listRoute, 'GET /video/sessions': SESSIONS, 'GET /timetable/classrooms': { data: [] } });
    render(
      <AuthContext.Provider value={{ user: { username: 'op', role: 'operator' } }}>
        <CamerasPage />
      </AuthContext.Provider>
    );
    await screen.findByRole('table', { name: 'Cameras' });
    expect(screen.getByRole('button', { name: 'Test Main door' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add camera' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit Main door' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete Main door' })).not.toBeInTheDocument();
  });
});
