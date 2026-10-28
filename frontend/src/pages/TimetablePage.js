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
  DialogTitle,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography
} from '@mui/material';
import { ConfirmDialog, EmptyState, ErrorState, LoadingState, PageHeader, SectionCard } from '../components/ui';
import { usePermission } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, getErrorMessage } from '../data';

export const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const EMPTY_PERIOD = { classroom_id: '', class_name: '', course_code: '', course_name: '', day_of_week: 0,
  start_time: '09:00', end_time: '10:00', grace_minutes: '', professor_user_id: '' };

const PeriodDialog = ({ open, period, classrooms, classNames, professors = [], onClose, onSaved }) => {
  const [form, setForm] = useState(EMPTY_PERIOD);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError('');
    setForm(period ? {
      classroom_id: String(period.classroom_id), class_name: period.class_name, course_code: period.course_code || '',
      course_name: period.course_name, day_of_week: period.day_of_week, start_time: period.start_time.slice(0, 5),
      end_time: period.end_time.slice(0, 5), grace_minutes: period.grace_minutes ?? '',
      professor_user_id: period.professor_user_id ? String(period.professor_user_id) : ''
    } : { ...EMPTY_PERIOD, classroom_id: classrooms[0] ? String(classrooms[0].id) : '' });
  }, [open, period, classrooms]);

  const save = async () => {
    if (!form.classroom_id || !form.class_name.trim() || !form.course_name.trim()) {
      setError('Classroom, class and course name are required.');
      return;
    }
    if (form.end_time <= form.start_time) {
      setError('The end time must be after the start time.');
      return;
    }
    setSaving(true);
    setError('');
    const body = {
      classroom_id: Number(form.classroom_id), class_name: form.class_name.trim(), course_code: form.course_code.trim() || null,
      course_name: form.course_name.trim(), day_of_week: Number(form.day_of_week), start_time: form.start_time,
      end_time: form.end_time, grace_minutes: form.grace_minutes === '' ? null : Number(form.grace_minutes), is_active: true,
      professor_user_id: form.professor_user_id ? Number(form.professor_user_id) : null
    };
    try {
      const response = period ? await api.put(`/timetable/periods/${period.id}`, body) : await api.post('/timetable/periods', body);
      onSaved(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not save the period'));
    } finally {
      setSaving(false);
    }
  };

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{period ? 'Edit period' : 'Add period'}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField select label="Classroom" value={form.classroom_id} onChange={set('classroom_id')} fullWidth margin="dense">
            {classrooms.map((room) => <MenuItem key={room.id} value={String(room.id)}>{room.name}</MenuItem>)}
          </TextField>
          <Autocomplete freeSolo options={classNames} value={form.class_name} fullWidth
            onInputChange={(_, value) => setForm((f) => ({ ...f, class_name: value }))}
            renderInput={(params) => <TextField {...params} label="Class group" margin="dense"
              helperText="Students whose class matches form the roster" />} />
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField label="Course code" value={form.course_code} onChange={set('course_code')} margin="dense" sx={{ minWidth: 140 }} />
          <TextField label="Course name" value={form.course_name} onChange={set('course_name')} margin="dense" fullWidth />
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField select label="Day" value={form.day_of_week} onChange={set('day_of_week')} margin="dense" fullWidth>
            {DAYS.map((day, index) => <MenuItem key={day} value={index}>{day}</MenuItem>)}
          </TextField>
          <TextField label="Start" type="time" value={form.start_time} onChange={set('start_time')} margin="dense" fullWidth InputLabelProps={{ shrink: true }} />
          <TextField label="End" type="time" value={form.end_time} onChange={set('end_time')} margin="dense" fullWidth InputLabelProps={{ shrink: true }} />
        </Stack>
        <TextField label="Grace minutes" value={form.grace_minutes} onChange={set('grace_minutes')} margin="dense"
          inputProps={{ inputMode: 'numeric' }} helperText="Arriving later than start + grace counts as late. Empty = server default." />
        <TextField select label="Professor" value={form.professor_user_id} onChange={set('professor_user_id')} margin="dense" fullWidth
          helperText={professors.length === 0 && !form.professor_user_id
            ? 'No professor accounts yet: create one on the Users page (role Professor).'
            : 'The professor sees this class in the staff portal and saves its attendance register.'}>
          <MenuItem value="">Not assigned</MenuItem>
          {professors.map((u) => <MenuItem key={u.id} value={String(u.id)}>{u.username}{u.is_active ? '' : ' (inactive)'}</MenuItem>)}
        </TextField>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save period'}</Button>
      </DialogActions>
    </Dialog>
  );
};

const TimetablePage = () => {
  const canEdit = usePermission('admin');
  const notify = useNotify();
  const [classrooms, setClassrooms] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [holidays, setHolidays] = useState([]);
  const [classNames, setClassNames] = useState([]);
  const [professors, setProfessors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [roomName, setRoomName] = useState('');
  const [holiday, setHoliday] = useState({ date: '', name: '' });
  const [formError, setFormError] = useState('');
  const [periodDialog, setPeriodDialog] = useState(null); // { period } | null
  const [removing, setRemoving] = useState(null); // { kind, item }
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [rooms, periodList, holidayList, classes, accounts] = await Promise.all([
        api.get('/timetable/classrooms'), api.get('/timetable/periods'), api.get('/timetable/holidays'),
        api.get('/students/classes').catch(() => ({ data: [] })),
        // Professor accounts an admin can assign; the account list is admin-only
        canEdit ? api.get('/users/').catch(() => ({ data: [] })) : Promise.resolve({ data: [] })
      ]);
      setClassrooms(rooms.data);
      setPeriods(periodList.data);
      setHolidays(holidayList.data);
      setClassNames(classes.data);
      setProfessors((accounts.data || []).filter((u) => u.role === 'professor'));
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load the timetable'));
    } finally {
      setLoading(false);
    }
  }, [canEdit]);

  useEffect(() => { load(); }, [load]);

  const byDay = useMemo(() => DAYS.map((day, index) => ({ day, periods: periods.filter((p) => p.day_of_week === index) }))
    .filter((d) => d.periods.length > 0), [periods]);
  const roomLabel = (id) => classrooms.find((r) => r.id === id)?.name || `Room ${id}`;

  const addRoom = async () => {
    if (!roomName.trim()) return;
    setFormError('');
    try {
      await api.post('/timetable/classrooms', { name: roomName.trim() });
      setRoomName('');
      notify('Classroom added');
      load();
    } catch (err) {
      setFormError(getErrorMessage(err, 'Could not add the classroom'));
    }
  };

  const addHoliday = async () => {
    if (!holiday.date || !holiday.name.trim()) return;
    setFormError('');
    try {
      await api.post('/timetable/holidays', { date: holiday.date, name: holiday.name.trim() });
      setHoliday({ date: '', name: '' });
      notify('Holiday added');
      load();
    } catch (err) {
      setFormError(getErrorMessage(err, 'Could not add the holiday'));
    }
  };

  const confirmRemove = async () => {
    const { kind, item } = removing;
    setRemoveBusy(true);
    setRemoveError('');
    try {
      const url = { room: `/timetable/classrooms/${item.id}`, period: `/timetable/periods/${item.id}`, holiday: `/timetable/holidays/${item.id}` }[kind];
      await api.delete(url);
      setRemoving(null);
      notify('Removed');
      load();
    } catch (err) {
      setRemoveError(getErrorMessage(err, 'Could not remove it'));
    } finally {
      setRemoveBusy(false);
    }
  };

  if (loading && classrooms.length === 0 && !error) return <LoadingState label="Loading timetable..." />;

  return (
    <Box>
      <PageHeader
        title="Timetable"
        subtitle="Classrooms, weekly class periods and holidays. Period attendance on the Reports page is computed from these."
        actions={canEdit && classrooms.length > 0 && (
          <Button variant="contained" onClick={() => setPeriodDialog({ period: null })}>Add period</Button>
        )}
      />
      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={load} /></Box>}
      {formError && <Alert severity="error" onClose={() => setFormError('')} sx={{ mb: 2 }}>{formError}</Alert>}

      <Grid container spacing={2}>
        <Grid item xs={12} lg={8}>
          <SectionCard title="Weekly periods" subtitle="Overlapping periods for the same class or room are rejected">
            {byDay.length === 0 ? (
              <EmptyState title="No periods scheduled"
                description={classrooms.length === 0 ? 'Add a classroom first, then add its periods.' : 'Add the class periods for each weekday.'} />
            ) : byDay.map(({ day, periods: dayPeriods }) => (
              <Box key={day} sx={{ mb: 2 }}>
                <Typography variant="overline" color="text.secondary">{day}</Typography>
                <TableContainer>
                  <Table size="small" aria-label={`${day} periods`}>
                    <TableHead>
                      <TableRow>
                        <TableCell>Time</TableCell><TableCell>Course</TableCell><TableCell>Class</TableCell>
                        <TableCell>Room</TableCell><TableCell>Professor</TableCell><TableCell>Grace</TableCell>
                        {canEdit && <TableCell align="right">Actions</TableCell>}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {dayPeriods.map((p) => (
                        <TableRow key={p.id} hover>
                          <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.start_time.slice(0, 5)}–{p.end_time.slice(0, 5)}</TableCell>
                          <TableCell>{p.course_name}{p.course_code && <Typography variant="caption" color="text.secondary" component="div">{p.course_code}</Typography>}</TableCell>
                          <TableCell><Chip size="small" variant="outlined" label={p.class_name} /></TableCell>
                          <TableCell>{roomLabel(p.classroom_id)}</TableCell>
                          <TableCell>{p.professor_username || <Typography variant="body2" color="text.secondary" component="span">Not assigned</Typography>}</TableCell>
                          <TableCell>{p.grace_minutes == null ? 'default' : `${p.grace_minutes} min`}</TableCell>
                          {canEdit && (
                            <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                              <Button size="small" onClick={() => setPeriodDialog({ period: p })} aria-label={`Edit ${p.course_name} on ${day}`}>Edit</Button>
                              <Button size="small" color="error" onClick={() => { setRemoveError(''); setRemoving({ kind: 'period', item: p }); }}
                                aria-label={`Remove ${p.course_name} on ${day}`}>Remove</Button>
                            </TableCell>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Box>
            ))}
          </SectionCard>
        </Grid>
        <Grid item xs={12} lg={4}>
          <Stack gap={2}>
            <SectionCard title="Classrooms">
              {classrooms.length === 0 && <Typography variant="body2" color="text.secondary">No classrooms yet.</Typography>}
              <List dense disablePadding aria-label="Classrooms">
                {classrooms.map((room) => (
                  <ListItem key={room.id} disableGutters divider
                    secondaryAction={canEdit && (
                      <Button size="small" color="error" onClick={() => { setRemoveError(''); setRemoving({ kind: 'room', item: room }); }}
                        aria-label={`Delete classroom ${room.name}`}>Delete</Button>
                    )}>
                    <ListItemText primary={room.name}
                      secondary={`${periods.filter((p) => p.classroom_id === room.id).length} weekly periods`} />
                  </ListItem>
                ))}
              </List>
              {canEdit && (
                <Stack direction="row" gap={1} sx={{ mt: 1.5 }}>
                  <TextField size="small" label="New classroom" value={roomName} onChange={(e) => setRoomName(e.target.value)} fullWidth />
                  <Button variant="outlined" onClick={addRoom} disabled={!roomName.trim()}>Add</Button>
                </Stack>
              )}
            </SectionCard>
            <SectionCard title="Holidays" subtitle="No attendance is computed on these dates">
              {holidays.length === 0 && <Typography variant="body2" color="text.secondary">No holidays defined.</Typography>}
              <List dense disablePadding aria-label="Holidays">
                {holidays.map((h) => (
                  <ListItem key={h.id} disableGutters divider
                    secondaryAction={canEdit && (
                      <Button size="small" color="error" onClick={() => { setRemoveError(''); setRemoving({ kind: 'holiday', item: h }); }}
                        aria-label={`Delete holiday ${h.name}`}>Delete</Button>
                    )}>
                    <ListItemText primary={h.name} secondary={new Date(`${h.date}T00:00:00`).toDateString()} />
                  </ListItem>
                ))}
              </List>
              {canEdit && (
                <Stack direction="row" gap={1} sx={{ mt: 1.5 }} flexWrap="wrap">
                  <TextField size="small" type="date" label="Date" InputLabelProps={{ shrink: true }} value={holiday.date}
                    onChange={(e) => setHoliday({ ...holiday, date: e.target.value })} />
                  <TextField size="small" label="Name" value={holiday.name} onChange={(e) => setHoliday({ ...holiday, name: e.target.value })} />
                  <Button variant="outlined" onClick={addHoliday} disabled={!holiday.date || !holiday.name.trim()}>Add</Button>
                </Stack>
              )}
            </SectionCard>
          </Stack>
        </Grid>
      </Grid>

      <PeriodDialog
        open={Boolean(periodDialog)}
        period={periodDialog?.period}
        classrooms={classrooms}
        classNames={classNames}
        professors={professors}
        onClose={() => setPeriodDialog(null)}
        onSaved={() => { setPeriodDialog(null); notify('Period saved'); load(); }}
      />
      <ConfirmDialog
        open={Boolean(removing)}
        title={removing?.kind === 'period' ? 'Remove this period?' : removing?.kind === 'room' ? 'Delete classroom?' : 'Delete holiday?'}
        confirmLabel="Remove"
        danger
        busy={removeBusy}
        error={removeError}
        onCancel={() => setRemoving(null)}
        onConfirm={confirmRemove}
      >
        {removing?.kind === 'period'
          ? 'The period is deactivated: it stops appearing in the timetable and in new reports. Past overrides are kept.'
          : removing?.kind === 'room'
            ? 'Only possible when no camera or active period uses the classroom.'
            : 'Attendance will be computed for this date again.'}
      </ConfirmDialog>
    </Box>
  );
};

export default TimetablePage;
