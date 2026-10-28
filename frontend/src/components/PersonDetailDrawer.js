import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { ConfirmDialog, LoadingState, formatLocalTime, formatMinutes } from './ui';
import { usePermission } from '../context/AuthContext';
import { api, getErrorMessage } from '../data';

const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const HISTORY_DAYS = 30;

// One person: enrolled face records (metadata only) and the last 30 days of entries/exits
const PersonDetailDrawer = ({ person, onClose, onEnroll, onChanged }) => {
  const canOperate = usePermission('operator');
  const [faces, setFaces] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [erasing, setErasing] = useState(null);
  const [eraseBusy, setEraseBusy] = useState(false);
  const [eraseError, setEraseError] = useState('');

  const load = useCallback(async () => {
    if (!person) return;
    setLoading(true);
    setError('');
    const end = new Date();
    const start = new Date(end.getTime() - (HISTORY_DAYS - 1) * 86400000);
    try {
      const [facesResponse, sessionsResponse] = await Promise.all([
        api.get(`/face-enrollment/${person.id}`),
        api.get('/attendance/sessions', { params: { student_id: person.id, start_date: isoDay(start), end_date: isoDay(end) } })
      ]);
      setFaces(facesResponse.data);
      setSessions(sessionsResponse.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load details'));
    } finally {
      setLoading(false);
    }
  }, [person]);

  useEffect(() => { load(); }, [load]);

  const erase = async () => {
    setEraseBusy(true);
    setEraseError('');
    try {
      await api.delete(`/face-enrollment/${person.id}/${erasing.id}`);
      setErasing(null);
      await load();
      onChanged?.();
    } catch (err) {
      setEraseError(getErrorMessage(err, 'Could not delete the face record'));
    } finally {
      setEraseBusy(false);
    }
  };

  return (
    <Drawer anchor="right" open={Boolean(person)} onClose={onClose} PaperProps={{ sx: { width: { xs: '100%', sm: 520 } } }}>
      {person && (
        <Box sx={{ p: 3 }} role="region" aria-label={`Details for ${person.name}`}>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Box>
              <Typography variant="h5" component="h2">{person.name}</Typography>
              <Typography variant="body2" color="text.secondary">
                {person.student_id} · {person.person_type === 'staff' ? 'Staff' : 'Student'}
                {person.class_name ? ` · ${person.class_name}` : ''}{person.department ? ` · ${person.department}` : ''}
              </Typography>
            </Box>
            <IconButton onClick={onClose} aria-label="Close details"><CloseIcon /></IconButton>
          </Stack>

          {error && <Alert severity="error" sx={{ mt: 2 }} action={<Button color="inherit" size="small" onClick={load}>Retry</Button>}>{error}</Alert>}
          {loading ? <LoadingState label="Loading details..." /> : (
            <>
              <Divider sx={{ my: 2 }} />
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="subtitle1" component="h3">Enrolled faces</Typography>
                {canOperate && person.is_active && (
                  <Button size="small" onClick={() => onEnroll(person)}>Add photo</Button>
                )}
              </Stack>
              {faces.length === 0 ? (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  Not enrolled: cameras cannot recognise {person.name} until a photo is added.
                </Alert>
              ) : (
                <List dense aria-label="Enrolled faces">
                  {faces.map((face, index) => (
                    <ListItem key={face.id} disableGutters divider
                      secondaryAction={canOperate && (
                        <Button size="small" color="error" onClick={() => { setEraseError(''); setErasing(face); }}>Delete</Button>
                      )}>
                      <ListItemText primary={`Face record ${index + 1}`}
                        secondary={`Added ${new Date(face.created_at).toLocaleString()}${face.is_active ? '' : ' · inactive'}`} />
                    </ListItem>
                  ))}
                </List>
              )}
              <Typography variant="caption" color="text.secondary">
                Only a 128-number face encoding is stored, never the photo. Deleting a record erases it permanently.
              </Typography>

              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle1" component="h3" sx={{ mb: 1 }}>Last {HISTORY_DAYS} days</Typography>
              {sessions.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No entries or exits recorded.</Typography>
              ) : (
                <Table size="small" aria-label="Attendance history">
                  <TableHead>
                    <TableRow>
                      <TableCell>Date</TableCell><TableCell>In</TableCell><TableCell>Out</TableCell><TableCell>Inside</TableCell><TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {sessions.map((s, i) => (
                      <TableRow key={`${s.date}-${s.entry_at || s.exit_at}-${i}`}>
                        <TableCell>{s.date}</TableCell>
                        <TableCell>{formatLocalTime(s.entry_at)}</TableCell>
                        <TableCell>{formatLocalTime(s.exit_at)}</TableCell>
                        <TableCell>{formatMinutes(s.duration_minutes)}</TableCell>
                        <TableCell>
                          {s.status !== 'complete' && <Chip size="small" variant="outlined" label={s.status.replace('_', ' ')} />}
                          {s.manual && <Chip size="small" variant="outlined" label="manual" sx={{ ml: 0.5 }} />}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </>
          )}

          <ConfirmDialog
            open={Boolean(erasing)}
            title="Delete face record?"
            confirmLabel="Delete permanently"
            danger
            busy={eraseBusy}
            error={eraseError}
            onCancel={() => setErasing(null)}
            onConfirm={erase}
          >
            The face encoding is erased permanently and running cameras stop using it. Attendance history is kept.
          </ConfirmDialog>
        </Box>
      )}
    </Drawer>
  );
};

export default PersonDetailDrawer;
