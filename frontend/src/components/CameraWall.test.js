import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CameraWall from './CameraWall';
import { installMockApi } from '../testUtils/mockApi';

let mock;
let urlCounter = 0;
beforeEach(() => {
  urlCounter = 0;
  global.URL.createObjectURL = jest.fn(() => `blob:frame-${++urlCounter}`);
  global.URL.revokeObjectURL = jest.fn();
});
afterEach(() => mock?.restore());

const jpeg = () => ({ data: new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: 'image/jpeg' }) });

const session = (id, name, classroomId, stats = {}) => ({
  camera_id: id, camera_name: name, camera_type: 'IN', classroom_id: classroomId, is_processing: true,
  service_stats: { state: 'running', stale: false, last_error: null, frame_age_seconds: 0.4, feed_kind: 'live', ...stats }
});
const ROOMS = { 11: 'B-308', 12: 'BW102' };

test('both classroom feeds are shown side by side with their classroom names and own frames', async () => {
  mock = installMockApi({ 'GET /video/1/frame': jpeg(), 'GET /video/2/frame': jpeg() });
  render(<CameraWall sessions={[session(1, 'CCTV 192.0.2.5', 11), session(2, 'CCTV 192.0.2.2', 12)]}
    classroomNames={ROOMS} frameInterval={60000} />);
  const b308 = screen.getByRole('region', { name: 'CCTV 192.0.2.5 live feed' });
  const bw102 = screen.getByRole('region', { name: 'CCTV 192.0.2.2 live feed' });
  expect(within(b308).getByText('B-308')).toBeInTheDocument();
  expect(within(bw102).getByText('BW102')).toBeInTheDocument();
  expect(await within(b308).findByRole('img', { name: 'Latest frame from CCTV 192.0.2.5' })).toBeInTheDocument();
  expect(await within(bw102).findByRole('img', { name: 'Latest frame from CCTV 192.0.2.2' })).toBeInTheDocument();
  expect(mock.callsTo('GET', '/video/1/frame')).toHaveLength(1);
  expect(mock.callsTo('GET', '/video/2/frame')).toHaveLength(1);
  expect(within(b308).getByLabelText('CCTV 192.0.2.5 state')).toHaveTextContent('Receiving frames');
});

test('a reconnecting feed is clearly not live and shows the connection error', async () => {
  mock = installMockApi({ 'GET /video/1/frame': jpeg() });
  render(<CameraWall classroomNames={ROOMS} frameInterval={60000} sessions={[session(1, 'CCTV 192.0.2.5', 11, {
    state: 'reconnecting', stale: true, reconnect_attempts: 3,
    last_error: "Camera source 'rtsp://***@192.0.2.5:554/Streaming/Channels/101' is disconnected; reconnect attempt 3 failed"
  })]} />);
  const tile = screen.getByRole('region', { name: 'CCTV 192.0.2.5 live feed' });
  expect(within(tile).getByLabelText('CCTV 192.0.2.5 state')).toHaveTextContent('Reconnecting');
  expect(within(tile).getByText(/reconnect attempt 3 failed/)).toBeInTheDocument();
  expect(await within(tile).findByText('Not live: last picture shown')).toBeInTheDocument();
  expect(tile).not.toHaveTextContent('Receiving frames');
});

test('a running camera whose frames stopped is labelled "No new frames"', () => {
  mock = installMockApi({ 'GET /video/1/frame': { status: 404, data: { detail: 'No frame available yet' } } });
  render(<CameraWall classroomNames={ROOMS} frameInterval={60000}
    sessions={[session(1, 'CCTV 192.0.2.5', 11, { stale: true, frame_age_seconds: 12 })]} />);
  expect(screen.getByText('No new frames')).toBeInTheDocument();
  expect(screen.getByText('Newest frame 12 s ago')).toBeInTheDocument();
});

test('a camera without a classroom says so, and Details opens it', async () => {
  mock = installMockApi({ 'GET /video/3/frame': jpeg() });
  const onOpen = jest.fn();
  render(<CameraWall classroomNames={ROOMS} frameInterval={60000} onOpen={onOpen}
    sessions={[session(3, 'Spare', null)]} />);
  expect(screen.getByText('No classroom linked')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  expect(onOpen).toHaveBeenCalledWith(3);
  await waitFor(() => expect(mock.callsTo('GET', '/video/3/frame')).toHaveLength(1));
});

test('nothing is shown when no camera is running', () => {
  mock = installMockApi({});
  const { container } = render(<CameraWall sessions={[]} classroomNames={ROOMS} />);
  expect(container).toBeEmptyDOMElement();
});
