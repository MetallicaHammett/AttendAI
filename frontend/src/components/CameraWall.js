import React, { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, Grid, Paper, Stack, Typography } from '@mui/material';
import { api, getErrorMessage } from '../data';

const STATE = {
  running: { label: 'Receiving frames', color: 'success' },
  reconnecting: { label: 'Reconnecting', color: 'warning' },
  disconnected: { label: 'Disconnected', color: 'error' },
  ended: { label: 'Video ended', color: 'info' },
  stopped: { label: 'Stopped', color: 'default' }
};

// One running camera: its classroom, connection state and newest processed frame.
// A frame older than the stale limit is shown greyed with a "no new frames" label, so a
// frozen picture is never mistaken for a live one.
export const CameraTile = ({ session, classroomName, frameInterval = 1000, onOpen }) => {
  const stats = session.service_stats || {};
  const [frameUrl, setFrameUrl] = useState(null);
  const [frameError, setFrameError] = useState('');
  const urlRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await api.get(`/video/${session.camera_id}/frame`, { responseType: 'blob', signal: controller.signal });
        const url = URL.createObjectURL(response.data);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setFrameUrl(url);
        setFrameError('');
      } catch (err) {
        if (!controller.signal.aborted && err?.response?.status !== 404) {
          setFrameError(getErrorMessage(err, 'Frame unavailable'));
        }
      } finally {
        inFlight = false;
      }
    };
    load();
    const interval = setInterval(load, frameInterval);
    return () => {
      clearInterval(interval);
      controller.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    };
  }, [session.camera_id, frameInterval]);

  const state = STATE[stats.state] || { label: stats.state || 'Unknown', color: 'default' };
  const stale = Boolean(stats.stale) || stats.state === 'disconnected' || stats.state === 'reconnecting';
  return (
    <Paper variant="outlined" sx={{ p: 1.5, height: '100%' }} component="section"
      aria-label={`${session.camera_name} live feed`}>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap" sx={{ mb: 1 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }} noWrap>{classroomName || 'No classroom linked'}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            {session.camera_name} · {session.camera_type === 'OUT' ? 'OUT (exits)' : 'IN (entries)'}
          </Typography>
        </Box>
        <Chip size="small" color={state.color} label={state.label} aria-label={`${session.camera_name} state`} />
        {stats.stale && stats.state === 'running' && <Chip size="small" color="warning" label="No new frames" />}
      </Stack>
      <Box sx={{ position: 'relative', bgcolor: '#101828', borderRadius: 1, aspectRatio: '16 / 9', overflow: 'hidden',
        display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {frameUrl ? (
          <Box component="img" src={frameUrl} alt={`Latest frame from ${session.camera_name}`}
            sx={{ maxWidth: '100%', maxHeight: '100%', opacity: stale ? 0.35 : 1 }} />
        ) : (
          <Typography variant="body2" sx={{ color: '#d0d5dd' }}>Waiting for the first frame…</Typography>
        )}
        {frameUrl && stale && (
          <Typography sx={{ position: 'absolute', color: '#fff', fontWeight: 700, textShadow: '0 1px 3px #000' }}>
            Not live: last picture shown
          </Typography>
        )}
      </Box>
      {(stats.last_error || frameError) && (
        <Alert severity={stats.state === 'reconnecting' ? 'warning' : 'error'} sx={{ mt: 1, py: 0 }}>
          {stats.last_error || frameError}
          {stats.state === 'reconnecting' && stats.reconnect_attempts ? ` (attempt ${stats.reconnect_attempts})` : ''}
        </Alert>
      )}
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 1 }}>
        <Typography variant="caption" color="text.secondary">
          {stats.frame_age_seconds != null ? `Newest frame ${Math.round(stats.frame_age_seconds)} s ago` : 'No frame yet'}
          {stats.total_reconnects ? ` · ${stats.total_reconnects} reconnect${stats.total_reconnects === 1 ? '' : 's'}` : ''}
        </Typography>
        {onOpen && <Button size="small" onClick={() => onOpen(session.camera_id)}>Details</Button>}
      </Stack>
    </Paper>
  );
};

// Every running camera side by side (two classrooms at once during the demonstration)
const CameraWall = ({ sessions, classroomNames, onOpen, frameInterval }) => {
  if (!sessions || sessions.length === 0) return null;
  return (
    <Box component="section" aria-label="All running cameras" sx={{ mb: 3 }}>
      <Typography variant="overline" color="text.secondary">All running cameras</Typography>
      <Grid container spacing={2}>
        {sessions.map((session) => (
          <Grid item xs={12} md={sessions.length > 1 ? 6 : 12} key={session.camera_id}>
            <CameraTile session={session} classroomName={classroomNames[session.classroom_id]}
              onOpen={onOpen} frameInterval={frameInterval} />
          </Grid>
        ))}
      </Grid>
    </Box>
  );
};

export default CameraWall;
