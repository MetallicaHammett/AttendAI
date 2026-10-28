import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import LiveMonitoringPage, { isNoFrameYet, normaliseBlobError } from './LiveMonitoringPage';
import { setUnauthorizedHandler } from '../data';
import { installMockApi, headerOf } from '../testUtils/mockApi';

const FAST = { status: 60, frame: 40, attendance: 80 };
const sleep = (ms) => act(() => new Promise((r) => setTimeout(r, ms)));

// Shapes follow backend routers/video.py and schemas
const CAMERAS = [
  { id: 1, name: 'Main door', camera_type: 'IN', source_type: 'webcam', source: '0', is_active: true, deleted_at: null },
  { id: 2, name: 'Exit gate', camera_type: 'OUT', source_type: 'rtsp', source: 'rtsp://x', is_active: true, deleted_at: null },
  { id: 3, name: 'Spare', camera_type: 'IN', source_type: 'video', source: 'x.mp4', is_active: false, deleted_at: null }
];
const notProcessing = (id) => ({ data: { camera_id: id, is_processing: false, message: 'Video processing not active' } });
const processing = (id, stats = {}, extra = {}) => ({
  data: {
    camera_id: id, is_processing: true, frames_processed: 120, duration_seconds: 65, error: null,
    service_stats: { known_faces_count: 4, current_inside_count: 2, frame_drop_count: 1, camera_healthy: true, last_error: null, synthetic_source: false, ...stats },
    ...extra
  }
});
const jpeg = () => ({ data: new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: 'image/jpeg' }) });
const blobError = (status, detail) => ({ status, data: new Blob([JSON.stringify({ detail })], { type: 'application/json' }) });
const sessions = (...ids) => ({ data: { active_sessions: ids.map((id) => ({ camera_id: id, is_processing: true })), count: ids.length } });

const LocationDisplay = () => {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
};

let mock;
let urlCounter;
beforeEach(() => {
  urlCounter = 0;
  global.URL.createObjectURL = jest.fn(() => `blob:frame-${++urlCounter}`);
  global.URL.revokeObjectURL = jest.fn();
});
afterEach(() => {
  mock?.restore();
  localStorage.clear();
});

const LIVE = {
  data: {
    sessions: [], observations: [], server_time: 1760860800,
    occupancy: { known_inside_count: null, unknown_inside_count: 0, unknown_entries_today: 0, unknown_tracking_since: null, unknown_tracking_active: false }
  }
};

// Start and Stop open a confirmation dialog first
const startAndConfirm = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Start' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Start processing' }));
};
const stopAndConfirm = async () => {
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Stop processing' }));
};

const renderPage = (routes, path = '/live-monitoring') => {
  mock = installMockApi({ 'GET /cameras/': { data: CAMERAS }, 'GET /video/sessions': sessions(), 'GET /video/live': LIVE, ...routes });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/live-monitoring" element={<LiveMonitoringPage pollIntervals={FAST} />} /></Routes>
      <LocationDisplay />
    </MemoryRouter>
  );
};

describe('opening the page', () => {
  test('lists cameras with auth, checks running sessions and the selected camera status', async () => {
    localStorage.setItem('token', 'test.token.value');
    renderPage({ 'GET /video/1/status': notProcessing(1) });
    expect(await screen.findByText('Not processing. Press Start to begin.')).toBeInTheDocument();
    expect(headerOf(mock.callsTo('GET', '/cameras/')[0], 'Authorization')).toBe('Bearer test.token.value');
    expect(mock.callsTo('GET', '/video/sessions').length).toBeGreaterThan(0);
    expect(mock.callsTo('GET', '/video/1/status').length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Processing state')).toHaveTextContent('Stopped');
    expect(screen.getByTestId('location')).toHaveTextContent('/live-monitoring?camera_id=1');
    expect(mock.callsTo('GET', '/video/1/frame')).toHaveLength(0); // no frames unless processing
  });

  test('prefers a camera that is already running (is_processing from the backend)', async () => {
    renderPage({
      'GET /video/sessions': sessions(2),
      'GET /video/2/status': processing(2),
      'GET /video/2/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByAltText('Live frame from Exit gate')).toHaveAttribute('src', expect.stringMatching(/^blob:frame-/));
    expect(screen.getByLabelText('Processing state')).toHaveTextContent('Processing');
    expect(screen.getByLabelText('Frames processed')).toHaveTextContent('120');
    expect(screen.getByLabelText('Enrolled faces loaded')).toHaveTextContent('4');
    expect(screen.getByLabelText('Students inside now')).toHaveTextContent('2');
  });

  test('a camera_id in the URL is used (deep link)', async () => {
    renderPage({ 'GET /video/3/status': notProcessing(3) }, '/live-monitoring?camera_id=3');
    expect(await screen.findByText(/this camera is inactive/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
  });

  test('no cameras configured', async () => {
    renderPage({ 'GET /cameras/': { data: [] } });
    expect(await screen.findByText(/no cameras are configured/i)).toBeInTheDocument();
  });
});

describe('start and stop', () => {
  test('Start posts to /video/{id}/start, then frames are fetched as authenticated blobs', async () => {
    localStorage.setItem('token', 'test.token.value');
    let running = false;
    renderPage({
      'GET /video/1/status': () => (running ? processing(1) : notProcessing(1)),
      'POST /video/1/start': () => { running = true; return { data: { message: 'Video processing started for camera Main door', camera_id: 1, source: '0' } }; },
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Start' }));
    // Nothing is started until the operator confirms (the dialog names the physical device)
    expect(await screen.findByText(/switches on webcam device 0/)).toBeInTheDocument();
    expect(mock.callsTo('POST', '/video/1/start')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Start processing' }));
    expect(await screen.findByText('Video processing started for camera Main door')).toBeInTheDocument();
    const img = await screen.findByAltText('Live frame from Main door');
    expect(img.getAttribute('src')).toMatch(/^blob:frame-/);
    const frameCall = mock.callsTo('GET', '/video/1/frame')[0];
    expect(headerOf(frameCall, 'Authorization')).toBe('Bearer test.token.value');
    // the page is aria-hidden until the confirmation dialog has finished closing
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 4000 });
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
  });

  test.each([
    [503, "Camera 'Main door' could not be opened: Could not open camera source '0'."],
    [400, 'Camera is not active'],
    [400, 'Video processing already running for this camera']
  ])('Start failure %i shows the backend reason and stays stopped', async (status, detail) => {
    renderPage({ 'GET /video/1/status': notProcessing(1), 'POST /video/1/start': { status, data: { detail } } });
    await startAndConfirm();
    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(screen.getByLabelText('Processing state')).toHaveTextContent('Stopped');
    expect(mock.callsTo('GET', '/video/1/frame')).toHaveLength(0);
  });

  test('Stop posts to /video/{id}/stop, clears the frame and releases its URL', async () => {
    let running = true;
    renderPage({
      'GET /video/1/status': () => (running ? processing(1) : notProcessing(1)),
      'GET /video/sessions': sessions(1),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] },
      'POST /video/1/stop': () => { running = false; return { data: { message: 'stopped', frames_processed: 42, duration_seconds: 75 } }; }
    });
    await screen.findByAltText('Live frame from Main door');
    await stopAndConfirm();
    expect(await screen.findByText('Processing stopped after 42 frames (1m 15s).')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByAltText('Live frame from Main door')).not.toBeInTheDocument());
    expect(screen.getByText('Not processing. Press Start to begin.')).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  test('Stop failure shows the backend reason', async () => {
    renderPage({
      'GET /video/1/status': processing(1),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] },
      'POST /video/1/stop': { status: 404, data: { detail: 'No active video processing session for this camera' } }
    });
    await screen.findByAltText('Live frame from Main door');
    await stopAndConfirm();
    expect(await screen.findByText('No active video processing session for this camera')).toBeInTheDocument();
  });
});

describe('frames', () => {
  test.each([
    ['404', blobError(404, 'No frame available yet')],
    ['500 (current backend quirk)', blobError(500, 'Failed to get frame: 404: No frame available yet')],
    // Observed against the real backend (Starlette 0.27: str(HTTPException) is empty)
    ['500 with an empty wrapped message', blobError(500, 'Failed to get frame: ')]
  ])('"no frame yet" as %s keeps waiting and keeps polling', async (_, response) => {
    renderPage({ 'GET /video/1/status': processing(1), 'GET /video/1/frame': response, 'GET /attendance/recent': { data: [] } });
    expect(await screen.findByText('Waiting for the first frame…')).toBeInTheDocument();
    const before = mock.callsTo('GET', '/video/1/frame').length;
    await sleep(200);
    expect(mock.callsTo('GET', '/video/1/frame').length).toBeGreaterThan(before);
    expect(screen.queryByText(/frame unavailable/i)).not.toBeInTheDocument();
  });

  test('frame 404 "no active session" re-checks status and shows stopped', async () => {
    let running = true;
    renderPage({
      'GET /video/1/status': () => (running ? processing(1) : notProcessing(1)),
      'GET /video/1/frame': () => { running = false; return blobError(404, 'No active video processing session for this camera'); },
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByText('Not processing. Press Start to begin.')).toBeInTheDocument();
  });

  test('other frame errors are shown, not hidden', async () => {
    renderPage({
      'GET /video/1/status': processing(1),
      'GET /video/1/frame': blobError(500, 'Failed to get frame: encoder crashed'),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByText('Frame unavailable: Failed to get frame: encoder crashed')).toBeInTheDocument();
  });

  test('never more than one frame request in flight', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    renderPage({
      'GET /video/1/status': processing(1),
      'GET /attendance/recent': { data: [] },
      'GET /video/1/frame': () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        return new Promise((resolve) => setTimeout(() => { inFlight -= 1; resolve(jpeg()); }, 150));
      }
    });
    await screen.findByAltText('Live frame from Main door');
    await sleep(400);
    expect(maxInFlight).toBe(1);
  });

  test('each new frame releases the previous object URL', async () => {
    renderPage({ 'GET /video/1/status': processing(1), 'GET /video/1/frame': jpeg(), 'GET /attendance/recent': { data: [] } });
    await screen.findByAltText('Live frame from Main door');
    await waitFor(() => expect(URL.createObjectURL.mock.calls.length).toBeGreaterThanOrEqual(3));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:frame-1');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:frame-2');
  });
});

describe('status details', () => {
  test('camera errors from the status are shown clearly', async () => {
    renderPage({
      'GET /video/1/status': processing(1, { camera_healthy: false, last_error: "Camera source '0' stopped delivering frames (10 consecutive read failures)" },
        { error: "Camera source '0' stopped delivering frames (10 consecutive read failures)" }),
      'GET /video/1/frame': blobError(404, 'No frame available yet'),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByText(/camera problem: camera source '0' stopped delivering frames/i)).toBeInTheDocument();
  });

  test('synthetic test frames are labelled honestly', async () => {
    renderPage({
      'GET /video/1/status': processing(1, { synthetic_source: true }),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByText(/this is not real camera footage/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Feed type')).toHaveTextContent('Synthetic test frames');
  });

  test('recorded video is labelled as recorded, never as a live feed', async () => {
    renderPage({
      'GET /video/1/status': processing(1, { feed_kind: 'recorded', state: 'running' }),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByLabelText('Feed type')).toHaveTextContent('Recorded video');
  });

  test('reconnecting, stale and pipeline problems are explained', async () => {
    renderPage({
      'GET /video/1/status': processing(1, { state: 'reconnecting', camera_healthy: false, reconnect_attempts: 3,
        last_error: "Camera source 'rtsp://***@cam/1' is disconnected; reconnect attempt 3 failed",
        pipeline_error: 'Person-detection model not found at C:/x/yolov8n.pt' }),
      'GET /video/1/frame': blobError(404, 'No frame available yet'),
      'GET /attendance/recent': { data: [] }
    });
    expect(await screen.findByText(/Reconnect attempts so far: 3/)).toBeInTheDocument();
    expect(screen.getByLabelText('Camera state')).toHaveTextContent('Reconnecting');
    expect(screen.getByText(/Recognition is not working: Person-detection model not found/)).toBeInTheDocument();
  });

  test('occupancy and the recognition log come from /video/live', async () => {
    renderPage({
      'GET /video/1/status': notProcessing(1),
      'GET /video/live': {
        data: {
          sessions: [{ camera_id: 2 }], server_time: 1760860800,
          occupancy: { known_inside_count: 12, unknown_inside_count: 2, unknown_entries_today: 3, unknown_tracking_since: 1760850000, unknown_tracking_active: true },
          observations: [
            { timestamp: 1760860790, camera_id: 2, camera_name: 'Exit gate', camera_type: 'IN', kind: 'unknown', decision: 'ENTRY', recorded: true, reasoning: 'Unregistered person entered' },
            { timestamp: 1760860780, camera_id: 2, camera_name: 'Exit gate', camera_type: 'IN', kind: 'known', student_id: 4, student_name: 'Alice Smith', confidence: 71.4, decision: 'ENTRY', recorded: true, reasoning: 'Entry recorded via IN camera' },
            { timestamp: 1760860770, camera_id: 2, camera_name: 'Exit gate', camera_type: 'IN', kind: 'ambiguous', decision: 'IGNORED', recorded: false, reasoning: 'ambiguous face' }
          ]
        }
      }
    });
    expect(await screen.findByLabelText('Unregistered inside')).toHaveTextContent('2');
    expect(screen.getByLabelText('Registered inside (cameras)')).toHaveTextContent('12');
    const log = screen.getByRole('list', { name: 'Recognition log' });
    expect(within(log).getByText('Unregistered person')).toBeInTheDocument();
    expect(within(log).getByText('Visitor ENTRY')).toBeInTheDocument();
    expect(within(log).getByText('Alice Smith')).toBeInTheDocument();
    expect(within(log).getByText(/match 71/)).toBeInTheDocument();
    expect(within(log).getByText('Ambiguous match (not counted)')).toBeInTheDocument();
  });

  test('viewers cannot start or stop cameras', async () => {
    const { AuthContext } = require('../context/AuthContext');
    mock = installMockApi({ 'GET /cameras/': { data: CAMERAS }, 'GET /video/sessions': sessions(), 'GET /video/live': LIVE,
      'GET /video/1/status': notProcessing(1) });
    render(
      <AuthContext.Provider value={{ user: { username: 'v', role: 'viewer' } }}>
        <MemoryRouter initialEntries={['/live-monitoring']}>
          <Routes><Route path="/live-monitoring" element={<LiveMonitoringPage pollIntervals={FAST} />} /></Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    );
    expect(await screen.findByText('Not processing. Press Start to begin.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
  });

  test('a status failure is reported', async () => {
    renderPage({ 'GET /video/1/status': { status: 500, data: { detail: 'Database is locked' } } });
    expect(await screen.findByText('Status unavailable: Database is locked')).toBeInTheDocument();
  });

  test('recent attendance uses /attendance/recent?camera_id with backend field names', async () => {
    renderPage({
      'GET /video/1/status': processing(1),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': {
        data: [{ id: 7, student_name: 'Alice Smith', student_id: 'S001', event_type: 'ENTRY', timestamp: '2026-10-19T08:05:00Z', confidence: 93, camera_name: 'Main door', student_db_id: 1, camera_id: 1 }]
      }
    });
    expect(await screen.findByText('Alice Smith (S001)')).toBeInTheDocument();
    expect(screen.getByText('ENTRY')).toBeInTheDocument();
    expect(mock.callsTo('GET', '/attendance/recent')[0].params).toEqual({ camera_id: 1, limit: 10 });
  });

  test('switching camera re-checks status for the new camera', async () => {
    renderPage({
      'GET /video/1/status': notProcessing(1),
      'GET /video/2/status': processing(2),
      'GET /video/2/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    await screen.findByText('Not processing. Press Start to begin.');
    fireEvent.mouseDown(screen.getByLabelText('Camera'));
    fireEvent.click(await screen.findByRole('option', { name: /Exit gate \(OUT\)/ }));
    expect(await screen.findByAltText('Live frame from Exit gate')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('camera_id=2');
  });
});

describe('leaving the page', () => {
  test('unmounting stops all polling, releases the frame URL and does NOT stop processing', async () => {
    const { unmount } = renderPage({
      'GET /video/1/status': processing(1),
      'GET /video/1/frame': jpeg(),
      'GET /attendance/recent': { data: [] }
    });
    await screen.findByAltText('Live frame from Main door');
    unmount();
    const counts = () => mock.calls.length;
    const afterUnmount = counts();
    await sleep(300);
    expect(counts()).toBe(afterUnmount);
    expect(mock.callsTo('POST', '/video/1/stop')).toHaveLength(0);
    const lastUrl = URL.createObjectURL.mock.results.at(-1).value;
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(lastUrl);
  });

  test('a 401 from polling triggers the shared sign-out handler', async () => {
    localStorage.setItem('token', 'expired.token.value');
    const signOut = jest.fn();
    const unregister = setUnauthorizedHandler(signOut);
    renderPage({
      'GET /cameras/': { data: CAMERAS },
      'GET /video/1/status': { status: 401, data: { detail: 'Could not validate credentials' } }
    });
    await waitFor(() => expect(signOut).toHaveBeenCalled());
    unregister();
  });
});

describe('helpers', () => {
  test('normaliseBlobError reads JSON detail out of a Blob body', async () => {
    const err = await normaliseBlobError({ response: { status: 503, data: new Blob([JSON.stringify({ detail: 'x' })]) } });
    expect(err.response.data).toEqual({ detail: 'x' });
  });

  test('isNoFrameYet only matches the no-frame message', () => {
    expect(isNoFrameYet({ response: { status: 404, data: { detail: 'No frame available yet' } } })).toBe(true);
    expect(isNoFrameYet({ response: { status: 500, data: { detail: 'Failed to get frame: 404: No frame available yet' } } })).toBe(true);
    expect(isNoFrameYet({ response: { status: 500, data: { detail: 'Failed to get frame: ' } } })).toBe(true);
    expect(isNoFrameYet({ response: { status: 500, data: { detail: 'Failed to get frame: 404' } } })).toBe(true);
    expect(isNoFrameYet({ response: { status: 404, data: { detail: 'Failed to get frame: ' } } })).toBe(false);
    expect(isNoFrameYet({ response: { status: 503, data: { detail: 'No frame available yet' } } })).toBe(false);
    expect(isNoFrameYet({ response: { status: 404, data: { detail: 'No active video processing session for this camera' } } })).toBe(false);
    expect(isNoFrameYet({ response: { status: 500, data: { detail: 'Failed to get frame: boom' } } })).toBe(false);
  });
});
