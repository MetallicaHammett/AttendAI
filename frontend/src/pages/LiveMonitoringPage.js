import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography
} from '@mui/material';
import { ConfirmDialog, KpiCard, PageHeader, SectionCard, formatTime } from '../components/ui';
import CameraWall from '../components/CameraWall';
import { usePermission } from '../context/AuthContext';
import { api, getErrorMessage } from '../data';

export const DEFAULT_POLL_INTERVALS = {
  status: 2000, // GET /video/{id}/status
  frame: 500, // GET /video/{id}/frame (skipped while a previous frame request is in flight)
  attendance: 5000, // GET /attendance/recent?camera_id=
  live: 3000, // GET /video/live (occupancy + recognitions from all cameras)
  wallFrame: 1000 // GET /video/{id}/frame for each tile of "All running cameras"
};

// What the frames are, stated plainly (never call recorded or synthetic video "live")
export const FEED_LABELS = {
  live: { label: 'Live feed', color: 'success' },
  recorded: { label: 'Recorded video', color: 'info' },
  synthetic: { label: 'Synthetic test frames', color: 'warning' }
};

const STATE_LABELS = {
  running: { label: 'Receiving frames', color: 'success' },
  reconnecting: { label: 'Reconnecting', color: 'warning' },
  disconnected: { label: 'Disconnected', color: 'error' },
  ended: { label: 'Video ended', color: 'info' },
  stopped: { label: 'Stopped', color: 'default' }
};

const OBSERVATION_LABELS = {
  known: (o) => o.student_name || `Student #${o.student_id}`,
  unknown: () => 'Unregistered person',
  ambiguous: () => 'Ambiguous match (not counted)'
};

const NO_FRAME_YET = /no frame available yet/i;
// Before the routers/video.py fix (re-raise HTTPException), the backend wrapped its own
// 404 "No frame available yet" in a 500 "Failed to get frame: {str(e)}", which with
// Starlette 0.27 had an empty message. The current backend returns a plain 404; this
// pattern is kept so an older backend still shows "waiting" instead of an error.
const WRAPPED_NO_FRAME = /^failed to get frame:\s*(404:?\s*)?$/i;
const NO_SESSION = /no active video processing session/i;

// Frame requests use responseType "blob", so an error body arrives as a Blob of JSON.
// Turn it back into { detail } so getErrorMessage can show the backend's reason.
const readBlobText = (blob) => new Promise((resolve) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => resolve('');
  reader.readAsText(blob);
});

export const normaliseBlobError = async (error) => {
  const data = error?.response?.data;
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const parsed = JSON.parse(await readBlobText(data));
      return { ...error, response: { ...error.response, data: parsed } };
    } catch (e) {
      return { ...error, response: { ...error.response, data: {} } };
    }
  }
  return error;
};

// "No frame yet" is a normal start-up state (e.g. while the detection model loads). The
// backend reports it as 404 (older backends: a 500 whose detail is
// "Failed to get frame: 404: No frame available yet" or just "Failed to get frame: ").
// Any 500 with a real error message is still treated as an error.
export const isNoFrameYet = (error) => {
  const status = error?.response?.status;
  const detail = String(error?.response?.data?.detail || '');
  if (status === 404) return NO_FRAME_YET.test(detail);
  if (status === 500) return NO_FRAME_YET.test(detail) || WRAPPED_NO_FRAME.test(detail);
  return false;
};

const formatDuration = (seconds) => {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m ${s % 60}s` : m ? `${m}m ${s % 60}s` : `${s}s`;
};

const Stat = ({ label, value }) => (
  <Box>
    <Typography variant="caption" color="text.secondary">{label}</Typography>
    <Typography variant="body1" aria-label={label}>{value}</Typography>
  </Box>
);

// Live view of one camera's processing. Camera workers run on the server; leaving this
// page never stops them (only the Stop button does).
const LiveMonitoringPage = ({ pollIntervals: pollOverrides = DEFAULT_POLL_INTERVALS }) => {
  const pollIntervals = useMemo(() => ({ ...DEFAULT_POLL_INTERVALS, ...pollOverrides }), [pollOverrides]);
  const canOperate = usePermission('operator');
  const [searchParams, setSearchParams] = useSearchParams();
  const [confirming, setConfirming] = useState(null); // 'start' | 'stop'
  const [live, setLive] = useState(null);
  const [liveError, setLiveError] = useState('');
  const [cameras, setCameras] = useState([]);
  const [camerasLoading, setCamerasLoading] = useState(true);
  const [camerasError, setCamerasError] = useState('');
  const [runningIds, setRunningIds] = useState([]);
  const [classroomNames, setClassroomNames] = useState({}); // classroom id -> name

  const selectedId = searchParams.get('camera_id') || '';
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState(null); // { severity, text }

  const [frameUrl, setFrameUrl] = useState(null);
  const [frameState, setFrameState] = useState('idle'); // idle | waiting | live | error
  const [frameError, setFrameError] = useState('');
  const [rate, setRate] = useState(null);
  const [recent, setRecent] = useState([]);
  const [recentError, setRecentError] = useState('');

  const isProcessing = Boolean(status?.is_processing);
  const lastSample = useRef(null);
  const statusRequestRef = useRef(null);

  const selectCamera = useCallback((id) => {
    const next = new URLSearchParams(searchParams);
    if (id) next.set('camera_id', String(id)); else next.delete('camera_id');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const loadSessions = useCallback(async (signal) => {
    try {
      const response = await api.get('/video/sessions', { signal });
      const ids = response.data.active_sessions.map((s) => s.camera_id);
      setRunningIds(ids);
      return ids;
    } catch (err) {
      return null;
    }
  }, []);

  // Cameras + running sessions once on open; pick a camera if none is in the URL
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const [camerasResponse, running, rooms] = await Promise.all([
          api.get('/cameras/', { params: { limit: 1000 }, signal: controller.signal }),
          loadSessions(controller.signal),
          // Classroom names label each feed; the page works without them
          api.get('/timetable/classrooms', { signal: controller.signal }).then((r) => r.data, () => [])
        ]);
        const list = camerasResponse.data;
        setCameras(list);
        setClassroomNames(Object.fromEntries((rooms || []).map((room) => [room.id, room.name])));
        if (!searchParams.get('camera_id') && list.length > 0) {
          const preferred = list.find((c) => (running || []).includes(c.id)) || list.find((c) => c.is_active) || list[0];
          selectCamera(preferred.id);
        }
      } catch (err) {
        if (!controller.signal.aborted) setCamerasError(getErrorMessage(err, 'Failed to load cameras'));
      } finally {
        if (!controller.signal.aborted) setCamerasLoading(false);
      }
    })();
    return () => controller.abort();
    // Runs once on open; later camera changes go through selectCamera
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedCamera = useMemo(() => cameras.find((c) => String(c.id) === selectedId), [cameras, selectedId]);

  // GET /video/{id}/status: checked immediately when a camera is selected, then polled
  const refreshStatus = useCallback(async () => {
    if (!selectedId) return;
    statusRequestRef.current?.abort();
    const controller = new AbortController();
    statusRequestRef.current = controller;
    try {
      const response = await api.get(`/video/${selectedId}/status`, { signal: controller.signal });
      setStatus(response.data);
      setStatusError('');
      if (response.data.is_processing) {
        const now = Date.now();
        const prev = lastSample.current;
        if (prev && prev.cameraId === selectedId && now > prev.time && response.data.frames_processed >= prev.frames) {
          setRate((response.data.frames_processed - prev.frames) / ((now - prev.time) / 1000));
        }
        lastSample.current = { cameraId: selectedId, frames: response.data.frames_processed, time: now };
      } else {
        lastSample.current = null;
        setRate(null);
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatusError(getErrorMessage(err, 'Could not get processing status'));
    }
  }, [selectedId]);

  useEffect(() => {
    setStatus(null);
    setStatusError('');
    setRate(null);
    lastSample.current = null;
    if (!selectedId) return undefined;
    refreshStatus();
    const interval = setInterval(refreshStatus, pollIntervals.status);
    return () => {
      clearInterval(interval);
      statusRequestRef.current?.abort();
    };
  }, [selectedId, refreshStatus, pollIntervals.status]);

  // Release each frame's object URL when it is replaced or the page unmounts
  useEffect(() => () => { if (frameUrl) URL.revokeObjectURL(frameUrl); }, [frameUrl]);

  // GET /video/{id}/frame while processing; never more than one request in flight
  useEffect(() => {
    if (!isProcessing || !selectedId) {
      setFrameUrl(null);
      setFrameState('idle');
      setFrameError('');
      return undefined;
    }
    let cancelled = false;
    let inFlight = false;
    const controller = new AbortController();
    setFrameState((current) => (current === 'live' ? current : 'waiting'));

    const fetchFrame = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await api.get(`/video/${selectedId}/frame`, { responseType: 'blob', signal: controller.signal });
        if (cancelled) return;
        setFrameUrl(URL.createObjectURL(response.data));
        setFrameState('live');
        setFrameError('');
      } catch (rawError) {
        if (cancelled || controller.signal.aborted) return;
        const err = await normaliseBlobError(rawError);
        if (cancelled) return;
        if (isNoFrameYet(err)) {
          setFrameState('waiting');
        } else if (err?.response?.status === 404 && NO_SESSION.test(String(err.response.data?.detail || ''))) {
          refreshStatus(); // processing ended (stopped elsewhere or crashed)
        } else {
          setFrameState('error');
          setFrameError(getErrorMessage(err, 'Could not load the latest frame'));
        }
      } finally {
        inFlight = false;
      }
    };

    fetchFrame();
    const interval = setInterval(fetchFrame, pollIntervals.frame);
    return () => {
      cancelled = true;
      clearInterval(interval);
      controller.abort();
    };
  }, [isProcessing, selectedId, refreshStatus, pollIntervals.frame]);

  // Recent attendance for this camera while it is processing
  useEffect(() => {
    if (!isProcessing || !selectedId) return undefined;
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await api.get('/attendance/recent', {
          params: { camera_id: Number(selectedId), limit: 10 }, signal: controller.signal
        });
        setRecent(response.data);
        setRecentError('');
      } catch (err) {
        if (!controller.signal.aborted) setRecentError(getErrorMessage(err, 'Could not load recent attendance'));
      }
    };
    load();
    const interval = setInterval(load, pollIntervals.attendance);
    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [isProcessing, selectedId, pollIntervals.attendance]);

  useEffect(() => { setRecent([]); setRecentError(''); setActionMessage(null); }, [selectedId]);

  // Occupancy and recognitions from every running camera (one request per poll)
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await api.get('/video/live', { params: { limit: 30 }, signal: controller.signal });
        setLive(response.data);
        setLiveError('');
      } catch (err) {
        if (!controller.signal.aborted) setLiveError(getErrorMessage(err, 'Live overview unavailable'));
      } finally {
        inFlight = false;
      }
    };
    load();
    const interval = setInterval(load, pollIntervals.live);
    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [pollIntervals.live]);

  const startProcessing = async () => {
    setConfirming(null);
    setActionBusy(true);
    setActionMessage(null);
    try {
      const response = await api.post(`/video/${selectedId}/start`);
      setActionMessage({ severity: 'success', text: response.data.message });
    } catch (err) {
      // e.g. 503 "Camera 'Door' could not be opened: ...", 400 "Camera is not active"
      setActionMessage({ severity: 'error', text: getErrorMessage(err, 'Failed to start processing') });
    } finally {
      setActionBusy(false);
      await Promise.all([refreshStatus(), loadSessions()]);
    }
  };

  const stopProcessing = async () => {
    setConfirming(null);
    setActionBusy(true);
    setActionMessage(null);
    try {
      const response = await api.post(`/video/${selectedId}/stop`);
      const { frames_processed: frames, duration_seconds: seconds } = response.data;
      setActionMessage({
        severity: 'success',
        text: `Processing stopped after ${frames} frame${frames === 1 ? '' : 's'} (${formatDuration(seconds)}).`
      });
    } catch (err) {
      setActionMessage({ severity: 'error', text: getErrorMessage(err, 'Failed to stop processing') });
    } finally {
      setActionBusy(false);
      await Promise.all([refreshStatus(), loadSessions()]);
    }
  };

  const stats = status?.service_stats || {};
  const cameraProblem = isProcessing && (status?.error || stats.last_error || stats.camera_healthy === false);
  const otherRunning = cameras.filter((c) => runningIds.includes(c.id) && String(c.id) !== selectedId);
  const occupancy = live?.occupancy;
  const observations = live?.observations || [];
  const feed = FEED_LABELS[stats.feed_kind] || (stats.synthetic_source ? FEED_LABELS.synthetic : null);
  const cameraState = STATE_LABELS[stats.state];

  if (camerasLoading) {
    return (
      <Box sx={{ textAlign: 'center', pt: 4 }}>
        <CircularProgress />
        <Typography sx={{ mt: 2 }} variant="body2">Loading cameras...</Typography>
      </Box>
    );
  }

  return (
    <Box>
      <PageHeader
        title="Live Monitoring"
        subtitle="Camera processing runs on the server and continues when you leave this page."
      />

      <Grid container spacing={2} sx={{ mb: 2 }} aria-label="Live occupancy">
        <Grid item xs={6} md={3}>
          <KpiCard label="Registered inside (cameras)" loading={!live && !liveError}
            value={occupancy?.known_inside_count ?? '—'}
            definition="Students and staff with an active entry today in the shared attendance engine of the running cameras."
            detail={occupancy?.known_inside_count === null ? 'No camera has run since the server started' : 'From recognised entries and exits'} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="Unregistered inside" loading={!live && !liveError}
            value={occupancy?.unknown_inside_count ?? '—'}
            tone={occupancy?.unknown_inside_count ? 'warn' : 'default'}
            definition="Estimate from faces that matched nobody enrolled: counted once at an entry camera and removed when the same face is seen at an exit camera. Ambiguous matches are never counted."
            detail={occupancy?.unknown_tracking_since
              ? `Since ${new Date(occupancy.unknown_tracking_since * 1000).toLocaleTimeString()} · ${occupancy.unknown_entries_today} entered today`
              : 'Starts when the first camera runs'} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="Cameras running" loading={!live && !liveError} value={live ? live.sessions.length : '—'}
            detail={`${cameras.filter((c) => c.is_active).length} active of ${cameras.length} configured`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="Recognitions logged" loading={!live && !liveError} value={observations.length}
            detail="Latest sightings and events, all cameras" />
        </Grid>
      </Grid>
      {liveError && <Alert severity="warning" sx={{ mb: 2 }}>Live overview unavailable: {liveError}</Alert>}

      <CameraWall sessions={live?.sessions} classroomNames={classroomNames} onOpen={selectCamera}
        frameInterval={pollIntervals.wallFrame} />

      {camerasError && <Alert severity="error" sx={{ mb: 2 }}>{camerasError}</Alert>}
      {!camerasError && cameras.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>No cameras are configured. Add one on the Cameras page first.</Alert>
      )}

      {cameras.length > 0 && (
        <Stack direction="row" gap={2} alignItems="center" flexWrap="wrap" sx={{ mb: 2 }}>
          <TextField
            select
            size="small"
            label="Camera"
            value={selectedCamera ? selectedId : ''}
            onChange={(e) => selectCamera(e.target.value)}
            sx={{ minWidth: 280 }}
          >
            {cameras.map((c) => (
              <MenuItem key={c.id} value={String(c.id)}>
                {`${c.name}${classroomNames[c.classroom_id] ? ` · ${classroomNames[c.classroom_id]}` : ''} (${c.camera_type})`
                  + `${c.is_active ? '' : ' - inactive'}${runningIds.includes(c.id) ? ' - running' : ''}`}
              </MenuItem>
            ))}
          </TextField>
          {status && (
            <Chip
              label={isProcessing ? 'Processing' : 'Stopped'}
              color={isProcessing ? 'success' : 'default'}
              aria-label="Processing state"
            />
          )}
          {isProcessing && feed && <Chip variant="outlined" color={feed.color} label={feed.label} aria-label="Feed type" />}
          {isProcessing && cameraState && stats.state !== 'running' && (
            <Chip color={cameraState.color} label={cameraState.label} aria-label="Camera state" />
          )}
          {canOperate && (
            <>
              <Button
                variant="contained"
                color="success"
                onClick={() => setConfirming('start')}
                disabled={!selectedCamera || actionBusy || isProcessing || !status || !selectedCamera.is_active}
              >
                Start
              </Button>
              <Button variant="contained" color="error" onClick={() => setConfirming('stop')} disabled={!selectedCamera || actionBusy || !isProcessing}>
                Stop
              </Button>
            </>
          )}
        </Stack>
      )}

      <ConfirmDialog
        open={confirming === 'start'}
        title={`Start ${selectedCamera?.name || 'camera'}?`}
        confirmLabel="Start processing"
        onCancel={() => setConfirming(null)}
        onConfirm={startProcessing}
      >
        {selectedCamera?.source_type === 'webcam'
          ? `This switches on webcam device ${selectedCamera?.source} on the server and starts recording attendance from it.`
          : selectedCamera?.source_type === 'video'
            ? 'This processes the recorded video file. Attendance events it produces are stored like live ones.'
            : `This connects to the stream ${selectedCamera?.source || ''} and starts recording attendance from it.`}
        {' '}Direction: {selectedCamera?.camera_type === 'OUT' ? 'OUT (records exits)' : 'IN (records entries)'}.
      </ConfirmDialog>
      <ConfirmDialog
        open={confirming === 'stop'}
        title={`Stop ${selectedCamera?.name || 'camera'}?`}
        confirmLabel="Stop processing"
        danger
        onCancel={() => setConfirming(null)}
        onConfirm={stopProcessing}
      >
        Entries and exits are not recorded from this camera while it is stopped. People already counted as inside stay
        counted.
      </ConfirmDialog>

      {selectedCamera && !selectedCamera.is_active && !isProcessing && (
        <Alert severity="info" sx={{ mb: 2 }}>This camera is inactive. Activate it on the Cameras page before starting.</Alert>
      )}
      {actionMessage && (
        <Alert severity={actionMessage.severity} onClose={() => setActionMessage(null)} sx={{ mb: 2 }}>{actionMessage.text}</Alert>
      )}
      {statusError && <Alert severity="error" sx={{ mb: 2 }}>Status unavailable: {statusError}</Alert>}
      {isProcessing && stats.synthetic_source && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Synthetic test frames: the camera source could not be opened and the server is in development mode with
          ENABLE_SYNTHETIC_CAMERA on. This is not real camera footage.
        </Alert>
      )}
      {cameraProblem && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Camera problem: {status.error || stats.last_error || 'the camera is not delivering frames reliably'}
          {stats.state === 'reconnecting' && ` Reconnect attempts so far: ${stats.reconnect_attempts}.`}
        </Alert>
      )}
      {isProcessing && stats.stale && !cameraProblem && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          No new frame for {stats.frame_age_seconds ?? '?'} seconds. The picture below may be out of date.
        </Alert>
      )}
      {isProcessing && stats.pipeline_error && (
        <Alert severity="error" sx={{ mb: 2 }}>Recognition is not working: {stats.pipeline_error}</Alert>
      )}
      {isProcessing && stats.state === 'ended' && (
        <Alert severity="info" sx={{ mb: 2 }}>The recorded video has ended. Stop the camera to release it.</Alert>
      )}

      {selectedCamera && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={8}>
            <Paper variant="outlined" sx={{ height: 400, display: 'flex', alignItems: 'center', justifyContent: 'center', bgcolor: 'grey.100', overflow: 'hidden' }}>
              {frameUrl && isProcessing ? (
                <Box component="img" src={frameUrl} alt={`Live frame from ${selectedCamera.name}`}
                  sx={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
              ) : (
                <Stack alignItems="center" gap={1} sx={{ color: 'text.secondary', p: 2, textAlign: 'center' }}>
                  {isProcessing && frameState === 'waiting' && <CircularProgress size={24} />}
                  <Typography role="status">
                    {!status ? 'Checking status…'
                      : !isProcessing ? 'Not processing. Press Start to begin.'
                        : frameState === 'error' ? `Frame unavailable: ${frameError}`
                          : 'Waiting for the first frame…'}
                  </Typography>
                </Stack>
              )}
            </Paper>
            <Typography variant="caption" color="text.secondary">
              Green: recognised student · Yellow: low match score (not used) · Orange: ambiguous · Red: unregistered.
              Processing continues when you leave this page; use Stop to end it.
            </Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
              <Typography variant="h6" component="h2" gutterBottom>Status</Typography>
              {isProcessing ? (
                <Stack gap={1}>
                  <Stat label="Frames processed" value={status.frames_processed} />
                  <Stat label="Processing rate" value={rate === null ? '—' : `${rate.toFixed(1)} frames/s`} />
                  <Stat label="Running for" value={formatDuration(status.duration_seconds)} />
                  <Stat label="Enrolled faces loaded" value={stats.known_faces_count ?? '—'} />
                  <Stat label="Students inside now" value={stats.current_inside_count ?? '—'} />
                  <Stat label="Unregistered inside" value={stats.unknown_inside_count ?? '—'} />
                  <Stat label="Average processing time"
                    value={stats.avg_processing_ms == null ? '—' : `${Math.round(stats.avg_processing_ms)} ms per frame`} />
                  <Stat label="Last frame"
                    value={stats.frame_age_seconds == null ? '—' : `${stats.frame_age_seconds < 1 ? '<1' : Math.round(stats.frame_age_seconds)} s ago`} />
                  <Stat label="Frames slower than source FPS" value={stats.frame_drop_count ?? '—'} />
                  {stats.total_reconnects > 0 && <Stat label="Reconnects" value={stats.total_reconnects} />}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">{status ? 'Not processing.' : 'Checking…'}</Typography>
              )}
            </Paper>
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="h6" component="h2" gutterBottom>Recent attendance</Typography>
              {recentError && <Alert severity="warning" sx={{ mb: 1 }}>{recentError}</Alert>}
              {!isProcessing ? (
                <Typography variant="body2" color="text.secondary">Shown while this camera is processing.</Typography>
              ) : recent.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No attendance recorded by this camera yet.</Typography>
              ) : (
                <List dense aria-label="Recent attendance">
                  {recent.map((record) => (
                    <ListItem key={record.id} disableGutters
                      secondaryAction={<Chip size="small" label={record.event_type} color={record.event_type === 'ENTRY' ? 'success' : 'default'} />}>
                      <ListItemText
                        primary={`${record.student_name} (${record.student_id})`}
                        secondary={`${new Date(record.timestamp).toLocaleTimeString()} · ${record.confidence}%`}
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </Paper>
          </Grid>
        </Grid>
      )}

      <SectionCard
        sx={{ mt: 2, height: 'auto' }}
        title="Recognition log"
        subtitle="Latest identifications and entry/exit decisions from all running cameras (repeated sightings are throttled)"
      >
        {observations.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            {live && live.sessions.length === 0 ? 'No camera is running.' : 'No faces recognised yet.'}
          </Typography>
        ) : (
          <List dense disablePadding aria-label="Recognition log">
            {observations.map((o, index) => {
              const name = (OBSERVATION_LABELS[o.kind] || (() => o.kind))(o);
              const recorded = o.recorded && (o.decision === 'ENTRY' || o.decision === 'EXIT');
              return (
                <ListItem key={`${o.timestamp}-${index}`} disableGutters divider
                  secondaryAction={(
                    <Tooltip title={o.reasoning || ''}>
                      <Chip size="small"
                        color={recorded ? (o.decision === 'ENTRY' ? 'success' : 'default') : 'default'}
                        variant={recorded ? 'filled' : 'outlined'}
                        label={recorded ? `${o.kind === 'known' ? '' : 'Visitor '}${o.decision}` : 'Seen'} />
                    </Tooltip>
                  )}>
                  <ListItemText
                    sx={{ pr: 14 }}
                    primary={name}
                    secondary={`${formatTime(new Date(o.timestamp * 1000).toISOString())} · ${o.camera_name || `Camera ${o.camera_id}`} (${o.camera_type})`
                      + (o.confidence != null ? ` · match ${Math.round(o.confidence)}` : '')}
                  />
                </ListItem>
              );
            })}
          </List>
        )}
      </SectionCard>

      {otherRunning.length > 0 && (
        <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
          <Typography variant="subtitle1" component="h2">Other cameras processing</Typography>
          <Stack direction="row" gap={1} flexWrap="wrap" sx={{ mt: 1 }}>
            {otherRunning.map((c) => (
              <Button key={c.id} size="small" variant="outlined" onClick={() => selectCamera(c.id)}>{c.name}</Button>
            ))}
          </Stack>
        </Paper>
      )}
    </Box>
  );
};

export default LiveMonitoringPage;
