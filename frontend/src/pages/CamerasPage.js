import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography
} from '@mui/material';
import CameraFormDialog, { SOURCE_TYPES } from '../components/CameraFormDialog';
import { ConfirmDialog, PageHeader } from '../components/ui';
import { usePermission } from '../context/AuthContext';
import { api, getErrorMessage } from '../data';

export const CAMERA_LIST_LIMIT = 1000;

const matches = (camera, query) => {
  if (!query) return true;
  const q = query.toLowerCase();
  return [camera.name, camera.source, camera.source_type, camera.camera_type, camera.description]
    .some((value) => (value || '').toLowerCase().includes(q));
};

const sourceLabel = (type) => SOURCE_TYPES.find((t) => t.value === type)?.label || type;

const StatusChips = ({ camera, processing }) => {
  if (camera.deleted_at) {
    return <Chip size="small" label={`Deleted ${new Date(camera.deleted_at).toLocaleDateString()}`} />;
  }
  return (
    <Stack direction="row" gap={0.5} flexWrap="wrap">
      {camera.is_active
        ? <Chip size="small" color="success" label="Active" />
        : <Chip size="small" color="warning" label="Inactive" />}
      {processing && <Chip size="small" color="info" label="Processing" />}
    </Stack>
  );
};

const CamerasPage = () => {
  const [cameras, setCameras] = useState([]);
  const [processingIds, setProcessingIds] = useState(new Set());
  const [sessionsError, setSessionsError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [search, setSearch] = useState('');
  const [showDeleted, setShowDeleted] = useState(false);
  const [notice, setNotice] = useState(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [togglingId, setTogglingId] = useState(null);
  const [classrooms, setClassrooms] = useState([]);
  const [testing, setTesting] = useState(null); // camera awaiting test confirmation
  const [testBusy, setTestBusy] = useState(false);
  const [testResults, setTestResults] = useState({}); // camera id -> { ok, detail }
  const canConfigure = usePermission('admin');
  const canOperate = usePermission('operator');

  // GET /cameras/ plus GET /video/sessions (which cameras are processing right now)
  // and GET /timetable/classrooms (optional: only used for the classroom column/field)
  const loadCameras = useCallback(async (signal) => {
    setLoading(true);
    setLoadError('');
    try {
      const [camerasResponse, sessionsResult, roomsResult] = await Promise.all([
        api.get('/cameras/', { params: { limit: CAMERA_LIST_LIMIT, include_deleted: showDeleted }, signal }),
        api.get('/video/sessions', { signal }).then((r) => ({ ok: r.data }), (e) => ({ error: e })),
        api.get('/timetable/classrooms', { signal }).then((r) => ({ ok: r.data }), () => ({ ok: null }))
      ]);
      setCameras(camerasResponse.data);
      if (roomsResult.ok) setClassrooms(roomsResult.ok);
      if (sessionsResult.ok) {
        setProcessingIds(new Set(sessionsResult.ok.active_sessions.map((s) => s.camera_id)));
        setSessionsError('');
      } else if (!signal?.aborted) {
        setSessionsError(getErrorMessage(sessionsResult.error, 'Could not load processing status'));
      }
    } catch (err) {
      if (signal?.aborted) return;
      setLoadError(getErrorMessage(err, 'Failed to load cameras'));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [showDeleted]);

  useEffect(() => {
    const controller = new AbortController();
    loadCameras(controller.signal);
    return () => controller.abort();
  }, [loadCameras]);

  const visible = useMemo(() => cameras.filter((c) => matches(c, search.trim())), [cameras, search]);

  const openAdd = () => { setEditing(null); setFormOpen(true); };
  const openEdit = (camera) => { setEditing(camera); setFormOpen(true); };

  const handleSaved = (saved) => {
    setCameras((current) => {
      const exists = current.some((c) => c.id === saved.id);
      return exists ? current.map((c) => (c.id === saved.id ? saved : c)) : [...current, saved];
    });
    const runningHint = editing && processingIds.has(saved.id)
      ? ' It is processing now: restart it in Live Monitoring for source, resolution or type changes to apply.'
      : '';
    setNotice({
      severity: 'success',
      text: (editing ? `Saved changes to ${saved.name}.` : `Added ${saved.name}.`) + runningHint
    });
    setFormOpen(false);
    setEditing(null);
  };

  // POST /cameras/{id}/toggle flips is_active and returns { message }
  const toggleActive = async (camera) => {
    setTogglingId(camera.id);
    try {
      const response = await api.post(`/cameras/${camera.id}/toggle`);
      const nowActive = !camera.is_active;
      setCameras((current) => current.map((c) => (c.id === camera.id ? { ...c, is_active: nowActive } : c)));
      const stillRunning = !nowActive && processingIds.has(camera.id);
      setNotice({
        severity: stillRunning ? 'warning' : 'success',
        text: `${camera.name}: ${response.data.message}`
          + (stillRunning ? ' Processing is still running; stop it in Live Monitoring.' : '')
      });
    } catch (err) {
      setNotice({ severity: 'error', text: getErrorMessage(err, `Failed to update ${camera.name}`) });
    } finally {
      setTogglingId(null);
    }
  };

  // POST /cameras/{id}/test: opens the source once, reads one frame and releases it
  const runTest = async () => {
    const camera = testing;
    setTestBusy(true);
    try {
      const response = await api.post(`/cameras/${camera.id}/test`, null,
        camera.source_type === 'webcam' ? { params: { confirm_device: true } } : undefined);
      setTestResults((current) => ({ ...current, [camera.id]: { ok: response.data.ok, detail: response.data.detail } }));
    } catch (err) {
      setTestResults((current) => ({ ...current, [camera.id]: { ok: false, detail: getErrorMessage(err, 'Test failed') } }));
    } finally {
      setTestBusy(false);
      setTesting(null);
    }
  };

  const roomName = (id) => classrooms.find((room) => room.id === id)?.name;

  const openDelete = (camera) => { setDeleteError(''); setDeleting(camera); };

  const confirmDelete = async () => {
    setDeleteBusy(true);
    setDeleteError('');
    try {
      await api.delete(`/cameras/${deleting.id}`);
      setNotice({ severity: 'success', text: `Deleted ${deleting.name}. Its attendance history is kept.` });
      setDeleting(null);
      await loadCameras();
    } catch (err) {
      // 409 while the camera is processing: "Stop video processing for this camera before deleting it"
      setDeleteError(getErrorMessage(err, 'Failed to delete camera'));
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <Box>
      <PageHeader
        title="Cameras"
        subtitle="Entry (IN) and exit (OUT) cameras. Start and stop processing on the Live Monitoring page."
        actions={canConfigure && <Button variant="contained" onClick={openAdd}>Add camera</Button>}
      />

      <Stack direction="row" alignItems="center" flexWrap="wrap" gap={2} sx={{ mb: 2 }}>
        <TextField
          label="Search cameras"
          placeholder="Name, source, type or description"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          size="small"
          sx={{ width: 320, maxWidth: '100%' }}
        />
        <FormControlLabel
          control={<Switch checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />}
          label="Show deleted"
        />
      </Stack>

      {notice && (
        <Alert severity={notice.severity} onClose={() => setNotice(null)} sx={{ mb: 2 }}>{notice.text}</Alert>
      )}
      {sessionsError && !loadError && (
        <Alert severity="warning" sx={{ mb: 2 }}>Processing status unavailable: {sessionsError}</Alert>
      )}

      {loading && cameras.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <CircularProgress />
          <Typography sx={{ mt: 2 }} variant="body2">Loading cameras...</Typography>
        </Box>
      ) : loadError ? (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => loadCameras()}>Retry</Button>}>
          {loadError}
        </Alert>
      ) : cameras.length === 0 ? (
        <Typography variant="body2" sx={{ textAlign: 'center', py: 4 }}>
          No cameras yet. Use “Add camera” to create one.
        </Typography>
      ) : visible.length === 0 ? (
        <Typography variant="body2" sx={{ textAlign: 'center', py: 4 }}>
          No cameras match “{search.trim()}”.
        </Typography>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="Cameras">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Direction</TableCell>
                {classrooms.length > 0 && <TableCell>Classroom</TableCell>}
                <TableCell>Source</TableCell>
                <TableCell>Resolution</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((camera) => {
                const deleted = Boolean(camera.deleted_at);
                const processing = processingIds.has(camera.id);
                return (
                  <TableRow key={camera.id} hover sx={deleted ? { opacity: 0.6 } : undefined}>
                    <TableCell component="th" scope="row">
                      <span>{camera.name}</span>
                      {camera.description && (
                        <Typography variant="caption" color="text.secondary" component="div">{camera.description}</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip size="small" variant="outlined" label={camera.camera_type}
                        color={camera.camera_type === 'IN' ? 'primary' : 'secondary'} />
                    </TableCell>
                    {classrooms.length > 0 && <TableCell>{roomName(camera.classroom_id) || '—'}</TableCell>}
                    <TableCell>
                      <Typography variant="body2">
                        {sourceLabel(camera.source_type)}
                        <Typography component="span" variant="caption" color="text.secondary">
                          {camera.source_type === 'video' ? ' · recorded' : ' · live'}
                        </Typography>
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ wordBreak: 'break-all' }}>{camera.source}</Typography>
                      {testResults[camera.id] && (
                        <Typography variant="caption" component="div" role="status"
                          color={testResults[camera.id].ok ? 'success.main' : 'error.main'}>
                          Test: {testResults[camera.id].ok ? 'OK' : 'failed'} - {testResults[camera.id].detail}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>{camera.width}×{camera.height} @ {camera.fps} fps</TableCell>
                    <TableCell><StatusChips camera={camera} processing={processing} /></TableCell>
                    <TableCell align="right">
                      {deleted ? (
                        <Typography variant="caption" color="text.secondary">Read only</Typography>
                      ) : (
                        <Stack direction="row" gap={1} justifyContent="flex-end" flexWrap="wrap">
                          {canOperate && (
                            <Button size="small" onClick={() => setTesting(camera)} aria-label={`Test ${camera.name}`}
                              disabled={testBusy}>Test</Button>
                          )}
                          {canConfigure && (
                            <>
                              <Button size="small" onClick={() => openEdit(camera)} aria-label={`Edit ${camera.name}`}>Edit</Button>
                              <Button
                                size="small"
                                onClick={() => toggleActive(camera)}
                                disabled={togglingId === camera.id}
                                aria-label={`${camera.is_active ? 'Deactivate' : 'Activate'} ${camera.name}`}
                              >
                                {camera.is_active ? 'Deactivate' : 'Activate'}
                              </Button>
                              <Button size="small" color="error" onClick={() => openDelete(camera)} aria-label={`Delete ${camera.name}`}>
                                Delete
                              </Button>
                            </>
                          )}
                        </Stack>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <CameraFormDialog
        open={formOpen}
        camera={editing}
        onClose={() => { setFormOpen(false); setEditing(null); }}
        onSaved={handleSaved}
        classrooms={classrooms}
      />

      <ConfirmDialog
        open={Boolean(testing)}
        title={`Test ${testing?.name || 'camera'}?`}
        confirmLabel={testing?.source_type === 'webcam' ? `Yes, open device ${testing?.source}` : 'Run test'}
        busy={testBusy}
        onCancel={() => setTesting(null)}
        onConfirm={runTest}
      >
        {testing?.source_type === 'webcam'
          ? `This briefly switches on webcam device ${testing?.source} on the server (its light may come on) to read one frame. Make sure this is the device intended for ${testing?.name}.`
          : 'The server opens the source, reads one frame and releases it. Nothing is recorded.'}
      </ConfirmDialog>

      <Dialog open={Boolean(deleting)} onClose={deleteBusy ? undefined : () => setDeleting(null)}>
        <DialogTitle>Delete camera?</DialogTitle>
        <DialogContent>
          {deleteError && <Alert severity="error" sx={{ mb: 2 }}>{deleteError}</Alert>}
          <DialogContentText>
            Delete {deleting?.name}? It will be hidden from the camera list and cannot be started again.
            Attendance recorded by this camera is kept. A camera that is processing must be stopped first.
            The app cannot undo this.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(null)} disabled={deleteBusy}>Cancel</Button>
          <Button color="error" variant="contained" onClick={confirmDelete} disabled={deleteBusy}>
            {deleteBusy ? 'Deleting...' : 'Delete camera'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CamerasPage;
