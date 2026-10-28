import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Typography
} from '@mui/material';
import { EmptyState, ErrorState, LoadingState, formatLocalTime, formatMinutes, todayIso } from './ui';
import { usePermission } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, downloadFile, getBlobErrorMessage, getErrorMessage } from '../data';

const daysAgoIso = (days) => {
  const d = new Date(Date.now() - days * 86400000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const STATUS_HELP = {
  complete: { label: 'Complete', color: 'default' },
  open: { label: 'Inside now', color: 'success' },
  no_exit: { label: 'No exit recorded', color: 'warning' },
  no_entry: { label: 'No entry recorded', color: 'warning' }
};

const nowLocalInput = () => {
  const d = new Date();
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

// Operator correction for an entry/exit the cameras missed (POST /attendance/manual, audited)
export const ManualEventDialog = ({ open, onClose, onSaved }) => {
  const [people, setPeople] = useState([]);
  const [form, setForm] = useState({ person: null, event_type: 'ENTRY', at: nowLocalInput(), reason: '' });
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setForm({ person: null, event_type: 'ENTRY', at: nowLocalInput(), reason: '' });
    setError('');
    const controller = new AbortController();
    api.get('/students/', { params: { limit: 5000 }, signal: controller.signal })
      .then((r) => setPeople(r.data.filter((p) => p.is_active)))
      .catch((err) => { if (!controller.signal.aborted) setError(getErrorMessage(err, 'Could not load people')); });
    return () => controller.abort();
  }, [open]);

  const save = async () => {
    if (!form.person) return setError('Choose the person.');
    if (form.reason.trim().length < 3) return setError('Give a reason (at least 3 characters); it is kept in the audit log.');
    setSaving(true);
    setError('');
    try {
      await api.post('/attendance/manual', {
        student_id: form.person.id, event_type: form.event_type, timestamp: `${form.at}:00`, reason: form.reason.trim()
      });
      onSaved(form);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not record the event'));
    } finally {
      setSaving(false);
    }
    return undefined;
  };

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Record a missed entry or exit</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Use this only when the cameras missed a crossing you observed. The event is marked as manual and your name and
          reason are kept in the audit log.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Autocomplete
          options={people}
          value={form.person}
          onChange={(_, value) => setForm({ ...form, person: value })}
          getOptionLabel={(p) => `${p.name} (${p.student_id})`}
          isOptionEqualToValue={(a, b) => a.id === b.id}
          renderInput={(params) => <TextField {...params} label="Person" margin="dense" />}
        />
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField select label="Event" value={form.event_type} margin="dense" fullWidth
            onChange={(e) => setForm({ ...form, event_type: e.target.value })}>
            <MenuItem value="ENTRY">Entry</MenuItem>
            <MenuItem value="EXIT">Exit</MenuItem>
          </TextField>
          <TextField label="When (local time)" type="datetime-local" value={form.at} margin="dense" fullWidth
            InputLabelProps={{ shrink: true }} onChange={(e) => setForm({ ...form, at: e.target.value })} />
        </Stack>
        <TextField label="Reason" value={form.reason} margin="dense" fullWidth multiline minRows={2}
          inputProps={{ maxLength: 500 }} onChange={(e) => setForm({ ...form, reason: e.target.value })}
          helperText="e.g. Camera was blocked by a group at 09:02" />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Record event'}</Button>
      </DialogActions>
    </Dialog>
  );
};

// GET /attendance/sessions: ENTRY/EXIT pairs per person per day over an inclusive date range
const SessionsView = () => {
  const [range, setRange] = useState({ start: daysAgoIso(6), end: todayIso() });
  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [manualOpen, setManualOpen] = useState(false);
  const [exporting, setExporting] = useState('');
  const canOperate = usePermission('operator');
  const notify = useNotify();

  const load = useCallback(async (signal) => {
    if (!range.start || !range.end) return;
    setLoading(true);
    setError('');
    try {
      const response = await api.get('/attendance/sessions', { params: { start_date: range.start, end_date: range.end }, signal });
      setSessions(response.data);
      setPage(0);
    } catch (err) {
      if (signal?.aborted) return;
      setError(getErrorMessage(err, 'Failed to load presence sessions'));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sessions.filter((s) => (type === 'all' || s.person_type === type)
      && (!q || [s.name, s.student_id, s.class_name].some((v) => (v || '').toLowerCase().includes(q))));
  }, [sessions, search, type]);
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
  const unpaired = filtered.filter((s) => s.status === 'no_exit' || s.status === 'no_entry').length;

  const exportAs = async (format) => {
    setExporting(format);
    try {
      const name = await downloadFile(`/reports/sessions.${format}`, { start_date: range.start, end_date: range.end },
        `attendance_${range.start}_${range.end}.${format}`);
      notify(`Downloaded ${name}`);
    } catch (err) {
      notify(await getBlobErrorMessage(err, 'Export failed'), 'error');
    } finally {
      setExporting('');
    }
  };

  return (
    <Box>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" gap={2} flexWrap="wrap" alignItems="center">
          <TextField label="From" type="date" size="small" value={range.start} InputLabelProps={{ shrink: true }}
            onChange={(e) => setRange({ ...range, start: e.target.value })} />
          <TextField label="To" type="date" size="small" value={range.end} InputLabelProps={{ shrink: true }}
            onChange={(e) => setRange({ ...range, end: e.target.value })} />
          <TextField label="Search people" size="small" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            placeholder="Name, ID or class" />
          <TextField select label="Type" size="small" value={type} onChange={(e) => { setType(e.target.value); setPage(0); }} sx={{ minWidth: 150 }}>
            <MenuItem value="all">Students and staff</MenuItem>
            <MenuItem value="student">Students</MenuItem>
            <MenuItem value="staff">Staff</MenuItem>
          </TextField>
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" variant="outlined" onClick={() => exportAs('csv')} disabled={Boolean(exporting)}>
            {exporting === 'csv' ? 'Preparing…' : 'Export CSV'}
          </Button>
          <Button size="small" variant="outlined" onClick={() => exportAs('pdf')} disabled={Boolean(exporting)}>
            {exporting === 'pdf' ? 'Preparing…' : 'Export PDF'}
          </Button>
          {canOperate && <Button size="small" variant="contained" onClick={() => setManualOpen(true)}>Record missed event</Button>}
        </Stack>
      </Paper>

      {loading && sessions.length === 0 ? <LoadingState label="Loading presence sessions..." />
        : error ? <ErrorState message={error} onRetry={() => load()} />
          : filtered.length === 0 ? (
            <EmptyState title="No entries or exits in this range"
              description="Sessions appear when cameras record entries and exits. Change the dates or filters." />
          ) : (
            <>
              {unpaired > 0 && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  {unpaired} session{unpaired === 1 ? ' has' : 's have'} a missing entry or exit (the camera did not see the
                  crossing). Time inside cannot be calculated for {unpaired === 1 ? 'it' : 'them'}; record the missed event if you
                  observed it.
                </Alert>
              )}
              <Paper variant="outlined">
                <TableContainer>
                  <Table size="small" aria-label="Presence sessions">
                    <TableHead>
                      <TableRow>
                        <TableCell>Date</TableCell>
                        <TableCell>Person</TableCell>
                        <TableCell>Class</TableCell>
                        <TableCell>Entry</TableCell>
                        <TableCell>Exit</TableCell>
                        <TableCell align="right">Time inside</TableCell>
                        <TableCell>Status</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {visible.map((s, i) => {
                        const status = STATUS_HELP[s.status] || { label: s.status, color: 'default' };
                        return (
                          <TableRow key={`${s.student_db_id}-${s.date}-${s.entry_at || s.exit_at}-${i}`} hover>
                            <TableCell>{s.date}</TableCell>
                            <TableCell>
                              {s.name}
                              <Typography variant="caption" color="text.secondary" component="div">
                                {s.student_id}{s.person_type === 'staff' ? ' · staff' : ''}
                              </Typography>
                            </TableCell>
                            <TableCell>{s.class_name || '—'}</TableCell>
                            <TableCell>
                              {formatLocalTime(s.entry_at)}
                              {s.entry_camera && <Typography variant="caption" color="text.secondary" component="div">{s.entry_camera}</Typography>}
                            </TableCell>
                            <TableCell>
                              {formatLocalTime(s.exit_at)}
                              {s.exit_camera && <Typography variant="caption" color="text.secondary" component="div">{s.exit_camera}</Typography>}
                            </TableCell>
                            <TableCell align="right">{formatMinutes(s.duration_minutes)}</TableCell>
                            <TableCell>
                              <Chip size="small" variant={s.status === 'complete' ? 'outlined' : 'filled'} color={status.color} label={status.label} />
                              {s.manual && <Chip size="small" variant="outlined" label="manual" sx={{ ml: 0.5 }} />}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>
                <TablePagination component="div" count={filtered.length} page={page} rowsPerPage={pageSize}
                  rowsPerPageOptions={[25, 50, 100]} onPageChange={(_, next) => setPage(next)}
                  onRowsPerPageChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }} />
              </Paper>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                Times are server-local. Dates are inclusive. Time inside for a person still inside is counted up to now.
              </Typography>
            </>
          )}

      <ManualEventDialog
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        onSaved={(form) => {
          setManualOpen(false);
          notify(`Recorded ${form.event_type.toLowerCase()} for ${form.person.name}.`);
          load();
        }}
      />
    </Box>
  );
};

export default SessionsView;
