import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography
} from '@mui/material';
import SessionsView from '../components/SessionsView';
import { PageHeader } from '../components/ui';
import { api, getErrorMessage } from '../data';

// GET /attendance/recent accepts limit 1..500
export const LIMIT_OPTIONS = [25, 50, 100, 200, 500];
export const DEFAULT_LIMIT = 100;

const parseLimit = (value) => {
  const n = Number(value);
  return LIMIT_OPTIONS.includes(n) ? n : DEFAULT_LIMIT;
};

// Backend timestamps are UTC ("...Z"); show them in the browser's local time
export const formatTimestamp = (iso) => new Date(iso).toLocaleString();

// Two views, chosen in the URL: the event log (default) and presence sessions (?view=sessions)
const AttendancePage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get('view') === 'sessions' ? 'sessions' : 'events';
  const switchView = (_, next) => {
    const params = new URLSearchParams();
    if (next === 'sessions') params.set('view', 'sessions');
    setSearchParams(params, { replace: true });
  };
  return (
    <Box>
      <PageHeader title="Attendance" subtitle="Every recorded entry and exit, and the time each person spent inside." />
      <Tabs value={view} onChange={switchView} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }} aria-label="Attendance views">
        <Tab value="events" label="Event log" />
        <Tab value="sessions" label="Presence sessions" />
      </Tabs>
      {view === 'sessions' ? <SessionsView /> : <EventLog />}
    </Box>
  );
};

// Recent attendance from GET /attendance/recent?limit=&camera_id= (newest first).
// The camera filter and limit live in the URL (?camera_id=2&limit=50).
const EventLog = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const cameraId = searchParams.get('camera_id') || '';
  const limit = parseLimit(searchParams.get('limit'));

  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [cameras, setCameras] = useState([]);
  const [camerasError, setCamerasError] = useState('');

  // Cameras for the filter, including deleted ones (their history is still listed)
  useEffect(() => {
    const controller = new AbortController();
    api.get('/cameras/', { params: { limit: 1000, include_deleted: true }, signal: controller.signal })
      .then((response) => setCameras(response.data))
      .catch((err) => {
        if (!controller.signal.aborted) setCamerasError(getErrorMessage(err, 'Could not load cameras'));
      });
    return () => controller.abort();
  }, []);

  const loadRecords = useCallback(async (signal) => {
    setLoading(true);
    setError('');
    try {
      const params = { limit };
      if (cameraId) params.camera_id = Number(cameraId);
      const response = await api.get('/attendance/recent', { params, signal });
      setRecords(response.data);
      setLastUpdated(new Date());
    } catch (err) {
      if (signal?.aborted) return;
      setError(getErrorMessage(err, 'Failed to load attendance records'));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [cameraId, limit]);

  useEffect(() => {
    const controller = new AbortController();
    loadRecords(controller.signal);
    return () => controller.abort();
  }, [loadRecords]);

  const updateParam = (key, value, defaultValue) => {
    const next = new URLSearchParams(searchParams);
    if (value === '' || value === defaultValue) next.delete(key);
    else next.set(key, String(value));
    setSearchParams(next, { replace: true });
  };

  const selectedCamera = useMemo(() => cameras.find((c) => String(c.id) === cameraId), [cameras, cameraId]);
  // A camera_id from the URL that is not in the list (yet) still filters; show it as an option
  const cameraOptions = useMemo(() => {
    const options = cameras.map((c) => ({ value: String(c.id), label: c.deleted_at ? `${c.name} (deleted)` : `${c.name} (${c.camera_type})` }));
    if (cameraId && !options.some((o) => o.value === cameraId)) options.push({ value: cameraId, label: `Camera #${cameraId}` });
    return options;
  }, [cameras, cameraId]);

  return (
    <Box>
      <Stack direction="row" gap={2} flexWrap="wrap" sx={{ mb: 2 }} alignItems="center">
        <TextField
          select
          size="small"
          label="Camera"
          value={cameraId}
          onChange={(e) => updateParam('camera_id', e.target.value, '')}
          sx={{ minWidth: 240 }}
        >
          <MenuItem value="">All cameras</MenuItem>
          {cameraOptions.map((o) => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
        </TextField>
        <TextField
          select
          size="small"
          label="Show"
          value={limit}
          onChange={(e) => updateParam('limit', Number(e.target.value), DEFAULT_LIMIT)}
          sx={{ minWidth: 160 }}
        >
          {LIMIT_OPTIONS.map((n) => <MenuItem key={n} value={n}>{`Latest ${n}`}</MenuItem>)}
        </TextField>
        <Box sx={{ flexGrow: 1 }} />
        {lastUpdated && (
          <Typography variant="caption" color="text.secondary">Updated {lastUpdated.toLocaleTimeString()}</Typography>
        )}
        <Button variant="outlined" size="small" onClick={() => loadRecords()} disabled={loading}>Refresh</Button>
      </Stack>

      {camerasError && (
        <Alert severity="warning" sx={{ mb: 2 }}>Camera filter unavailable: {camerasError}</Alert>
      )}

      {loading && records.length === 0 && !error ? (
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <CircularProgress />
          <Typography sx={{ mt: 2 }} variant="body2">Loading attendance records...</Typography>
        </Box>
      ) : error ? (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => loadRecords()}>Retry</Button>}>
          {error}
        </Alert>
      ) : records.length === 0 ? (
        <Typography variant="body2" sx={{ textAlign: 'center', py: 4 }}>
          {cameraId
            ? `No attendance records for ${selectedCamera ? selectedCamera.name : `camera #${cameraId}`}.`
            : 'No attendance records yet. Events appear here when cameras record entries and exits.'}
        </Typography>
      ) : (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Showing the latest {records.length} event{records.length === 1 ? '' : 's'}
            {records.length === limit ? ` (limit ${limit}; older events are not shown)` : ''}. Times are in your local time zone.
          </Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small" aria-label="Attendance records">
              <TableHead>
                <TableRow>
                  <TableCell>Time</TableCell>
                  <TableCell>Student</TableCell>
                  <TableCell>Event</TableCell>
                  <TableCell align="right">Match score</TableCell>
                  <TableCell>Camera</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {records.map((record) => (
                  <TableRow key={record.id} hover>
                    <TableCell>
                      <time dateTime={record.timestamp}>{formatTimestamp(record.timestamp)}</time>
                    </TableCell>
                    <TableCell>
                      {record.student_name}
                      <Typography variant="caption" color="text.secondary" component="div">{record.student_id}</Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={record.event_type}
                        color={record.event_type === 'ENTRY' ? 'success' : 'default'}
                        variant={record.event_type === 'ENTRY' ? 'filled' : 'outlined'}
                      />
                    </TableCell>
                    <TableCell align="right">{record.source === 'manual' ? '—' : `${record.confidence}%`}</TableCell>
                    <TableCell>
                      {record.camera_name || 'No camera (manual entry)'}
                      {record.source === 'manual' && <Chip size="small" variant="outlined" label="manual" sx={{ ml: 1 }} />}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}
    </Box>
  );
};

export default AttendancePage;
