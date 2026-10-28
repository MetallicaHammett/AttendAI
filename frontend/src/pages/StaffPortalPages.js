import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, Navigate, Outlet, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  Grid,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography
} from '@mui/material';
import ArrowBack from '@mui/icons-material/ArrowBack';
import ChevronLeft from '@mui/icons-material/ChevronLeft';
import ChevronRight from '@mui/icons-material/ChevronRight';
import {
  AttendanceStatusChip,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  KpiCard,
  LoadingState,
  PageHeader,
  SectionCard,
  STATUS_LABELS,
  formatDateTime,
  formatLocalTime,
  formatMinutes,
  todayIso
} from '../components/ui';
import { useAuth } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, getErrorMessage } from '../data';

// Professors and administrators only; the backend enforces the same rule on every request
export const ProfessorOnly = () => {
  const { user } = useAuth();
  return user?.role === 'professor' || user?.role === 'admin' ? <Outlet /> : <Navigate replace to="/dashboard" />;
};

const shiftDate = (iso, days) => {
  const [y, m, d] = iso.split('-').map(Number);
  const next = new Date(y, m - 1, d + days);
  return `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-${String(next.getDate()).padStart(2, '0')}`;
};

const formatDay = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
};

const PERIOD_STATES = {
  submitted: { label: 'Submitted', color: 'success' },
  awaiting_review: { label: 'Needs review', color: 'warning' },
  in_progress: { label: 'In progress', color: 'info' },
  upcoming: { label: 'Upcoming', color: 'default' },
  holiday: { label: 'Holiday', color: 'info' },
  not_scheduled: { label: 'Not yet on timetable', color: 'default' }
};

export const registerPath = (periodId, date) => `/staff/register/${periodId}/${date}`;

// --- weekly timetable -----------------------------------------------------------------

export const WeeklyTimetablePage = () => {
  const { user } = useAuth();
  const [weekOf, setWeekOf] = useState(todayIso());
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const response = await api.get('/staff/timetable', { params: { week_of: weekOf } });
      setData(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load the timetable'));
    }
  }, [weekOf]);

  useEffect(() => { load(); }, [load]);

  const isAdmin = user?.role === 'admin';
  // Monday-Saturday always; Sunday only when something is scheduled
  const days = (data?.days || []).filter((day) => day.day_name !== 'Sunday' || day.periods.length > 0);

  return (
    <Box>
      <PageHeader
        title="Weekly timetable"
        subtitle={isAdmin ? 'Every assigned class (administrator view). Open a class to review its attendance register.'
          : 'Your classes this week. Open a class to review and save its attendance.'}
        actions={(
          <Stack direction="row" gap={1} alignItems="center">
            <Button size="small" variant="outlined" startIcon={<ChevronLeft />} onClick={() => setWeekOf(shiftDate(weekOf, -7))}>
              Previous week
            </Button>
            <Button size="small" variant="outlined" onClick={() => setWeekOf(todayIso())}>This week</Button>
            <Button size="small" variant="outlined" endIcon={<ChevronRight />} onClick={() => setWeekOf(shiftDate(weekOf, 7))}>
              Next week
            </Button>
          </Stack>
        )}
      />
      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={load} /></Box>}
      {!data && !error && <LoadingState label="Loading timetable…" />}
      {data && (
        <>
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2 }}>
            Week of {formatDay(data.week_start)} – {formatDay(data.week_end)}
          </Typography>
          {data.assigned_periods === 0 ? (
            <SectionCard>
              <EmptyState
                title={isAdmin ? 'No classes have a professor assigned' : 'No classes are assigned to you'}
                description={isAdmin ? 'Assign professors to periods on the Timetable page.'
                  : 'Ask an administrator to assign you to your timetable periods.'}
              />
            </SectionCard>
          ) : (
            <Stack gap={2}>
              {days.map((day) => (
                <SectionCard key={day.date} title={`${day.day_name}, ${formatDay(day.date)}`}
                  subtitle={day.holiday ? `Holiday: ${day.holiday}` : (day.date === data.today ? 'Today' : undefined)}>
                  {day.periods.length === 0 ? (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>No classes.</Typography>
                  ) : (
                    <TableContainer>
                      <Table size="small" aria-label={`${day.day_name} classes`}>
                        <TableHead>
                          <TableRow>
                            <TableCell>Time</TableCell><TableCell>Course</TableCell><TableCell>Class</TableCell>
                            <TableCell>Classroom</TableCell>{isAdmin && <TableCell>Professor</TableCell>}
                            <TableCell>Attendance</TableCell><TableCell align="right" />
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {day.periods.map((p) => {
                            const state = PERIOD_STATES[p.state] || { label: p.state, color: 'default' };
                            return (
                              <TableRow key={`${p.period_id}-${p.date}`} hover>
                                <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.start_time}–{p.end_time}</TableCell>
                                <TableCell>
                                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.course_name}</Typography>
                                  {p.course_code && <Typography variant="caption" color="text.secondary">{p.course_code}</Typography>}
                                </TableCell>
                                <TableCell>{p.class_name}</TableCell>
                                <TableCell>{p.classroom || '—'}</TableCell>
                                {isAdmin && <TableCell>{p.professor || '—'}</TableCell>}
                                <TableCell>
                                  <Tooltip title={p.submitted_at ? `Saved by ${p.submitted_by} at ${formatDateTime(p.submitted_at)}` : ''}>
                                    <Chip size="small" label={state.label} color={state.color} variant={p.state === 'submitted' ? 'filled' : 'outlined'} />
                                  </Tooltip>
                                </TableCell>
                                <TableCell align="right">
                                  {p.state !== 'not_scheduled' && (
                                    <Button size="small" component={RouterLink} to={registerPath(p.period_id, p.date)}
                                      aria-label={`Open ${p.course_name} register for ${day.day_name}`}>
                                      {p.state === 'awaiting_review' || p.state === 'in_progress' ? 'Take attendance' : 'Open register'}
                                    </Button>
                                  )}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  )}
                </SectionCard>
              ))}
            </Stack>
          )}
        </>
      )}
    </Box>
  );
};

// --- class register -------------------------------------------------------------------

const FINAL = ['PRESENT', 'LATE', 'ABSENT', 'EXCUSED'];
const FINAL_SHORT = { PRESENT: 'P', LATE: 'L', ABSENT: 'A', EXCUSED: 'E' };

const AI_LABELS = {
  PRESENT: 'Detected in class',
  LATE: 'Detected late',
  SEEN_ELSEWHERE: 'Seen elsewhere only',
  NOT_DETECTED: 'Not detected',
  NO_COVERAGE: 'No camera running',
  NOT_STARTED: 'Not started',
  HOLIDAY: 'Holiday'
};

const COVERAGE_TEXT = {
  full: ['success', 'The classroom camera was running for the whole period. Detected students are pre-filled; students it did not recognise are Unverified until you confirm.'],
  partial: ['warning', 'The classroom camera covered only part of the period. Check unverified students carefully.'],
  none: ['warning', 'No classroom camera was running during this period. There is no camera evidence: every student needs your confirmation.'],
  no_classroom_camera: ['warning', 'This classroom has no entry camera. Entries seen by other cameras only show a student was in the building, so every student needs your confirmation.'],
  not_applicable: ['info', 'The period has not started yet.']
};

// Same rule as the backend (staff_portal.save_register): only a new or changed decision
// needs a reason; an unchanged saved decision keeps the reason it already has
export const reasonRequired = (row, status) => {
  if (row.final && status === row.final.status) return false;
  return status === 'EXCUSED' || Boolean(row.final) || (row.evidence?.kind === 'classroom_camera' && status === 'ABSENT');
};

const initialDraft = (students) => Object.fromEntries(students.map((s) => [s.student_db_id, {
  status: s.final ? s.final.status : (FINAL.includes(s.suggested_status) ? s.suggested_status : 'UNVERIFIED'),
  reason: ''
}]));

// "Detected" only for a recognition by this classroom's camera; an operator's manual entry is labelled as such
const aiLabel = (row) => {
  if (row.evidence?.kind === 'manual_event') return row.ai_status === 'LATE' ? 'Recorded manually (late)' : 'Recorded manually';
  return AI_LABELS[row.ai_status] || row.ai_status;
};

const evidenceText = (row) => {
  if (!row.evidence) return null;
  const when = formatLocalTime(row.evidence.at);
  if (row.evidence.kind === 'manual_event') return `Manual entry at ${when}`;
  return `${row.evidence.camera || 'Camera'} at ${when}`;
};

export const ClassRegisterPage = () => {
  const { periodId, date } = useParams();
  const notify = useNotify();
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState('all');
  const [bulkAbsent, setBulkAbsent] = useState(false);

  const apply = (register) => {
    setData(register);
    setDraft(initialDraft(register.students));
  };

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const response = await api.get(`/staff/periods/${periodId}/register`, { params: { date } });
      setData(response.data);
      setDraft(initialDraft(response.data.students));
    } catch (err) {
      setLoadError(getErrorMessage(err, 'Could not load the register'));
    }
  }, [periodId, date]);

  useEffect(() => { load(); }, [load]);

  const students = useMemo(() => data?.students || [], [data]);
  const dirty = students.some((s) => {
    const d = draft[s.student_db_id];
    return d && (d.status !== (s.final ? s.final.status : (FINAL.includes(s.suggested_status) ? s.suggested_status : 'UNVERIFIED')) || d.reason);
  });

  // Unsaved decisions survive an accidental reload/close only with the browser's warning
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (loadError) {
    return (
      <Box>
        <Button component={RouterLink} to="/staff/timetable" startIcon={<ArrowBack />} sx={{ mb: 2 }}>Weekly timetable</Button>
        <ErrorState message={loadError} onRetry={load} />
      </Box>
    );
  }
  if (!data) return <LoadingState label="Loading class register…" />;

  const editable = data.can_save;
  const counts = { PRESENT: 0, LATE: 0, ABSENT: 0, EXCUSED: 0, UNVERIFIED: 0 };
  students.forEach((s) => { const status = draft[s.student_db_id]?.status; if (status in counts) counts[status] += 1; });
  const missingReasons = students.filter((s) => {
    const d = draft[s.student_db_id];
    return d && FINAL.includes(d.status) && reasonRequired(s, d.status) && (d.reason || '').trim().length < 3;
  });
  const needsReview = (s) => draft[s.student_db_id]?.status === 'UNVERIFIED' || s.review_flags.length > 0;
  const visible = students.filter((s) => {
    const status = draft[s.student_db_id]?.status;
    if (filter === 'review') return needsReview(s);
    if (filter === 'present') return status === 'PRESENT' || status === 'LATE';
    if (filter === 'absent') return status === 'ABSENT';
    return true;
  });

  const setStatus = (id, status) => {
    if (!status) return;
    setDraft((current) => ({ ...current, [id]: { ...current[id], status } }));
  };
  const setReason = (id, reason) => setDraft((current) => ({ ...current, [id]: { ...current[id], reason } }));
  const markUnverified = (status) => setDraft((current) => Object.fromEntries(Object.entries(current).map(
    ([id, d]) => [id, d.status === 'UNVERIFIED' ? { ...d, status } : d])));

  const save = async () => {
    setSaveError('');
    setConflict(false);
    setSaving(true);
    try {
      const body = {
        expected_version: data.register.version,
        decisions: students.map((s) => ({
          student_db_id: s.student_db_id,
          status: draft[s.student_db_id].status,
          reason: (draft[s.student_db_id].reason || '').trim() || null,
          base_status: s.final ? s.final.status : null
        }))
      };
      const response = await api.put(`/staff/periods/${periodId}/register`, body, { params: { date } });
      apply(response.data);
      notify(response.data.changed_count > 0
        ? `Attendance saved (${response.data.changed_count} decision${response.data.changed_count === 1 ? '' : 's'} recorded)`
        : 'Attendance saved; no decisions changed');
    } catch (err) {
      // The draft is kept: nothing the professor entered is lost on an error
      setSaveError(getErrorMessage(err, 'Could not save attendance'));
      setConflict(err?.response?.status === 409);
    } finally {
      setSaving(false);
    }
  };

  const [tone, coverageMessage] = COVERAGE_TEXT[data.coverage.state] || ['info', ''];
  const p = data.period;

  return (
    <Box>
      <Button component={RouterLink} to="/staff/timetable" startIcon={<ArrowBack />} sx={{ mb: 1 }}>Weekly timetable</Button>
      <PageHeader
        title={`${p.course_name}${p.course_code ? ` (${p.course_code})` : ''}`}
        subtitle={`${p.class_name} · ${p.classroom || 'No classroom'} · ${p.day_name} ${formatDay(data.date)}, ${p.start_time}–${p.end_time}`
          + (p.professor ? ` · ${p.professor}` : '')}
      />

      {data.register.submitted ? (
        <Alert severity="success" sx={{ mb: 2 }}>
          Submitted by {data.register.submitted_by} at {formatDateTime(data.register.submitted_at)}
          {data.register.version > 1 ? ` (version ${data.register.version}; first submitted by ${data.register.first_submitted_by} at ${formatDateTime(data.register.first_submitted_at)})` : ''}.
          {editable && ' You can correct it: saving again creates a new version, and changing a saved decision needs a reason.'}
        </Alert>
      ) : null}
      {!editable && data.save_blocked_reason && <Alert severity="info" sx={{ mb: 2 }}>{data.save_blocked_reason}. Attendance cannot be saved.</Alert>}
      {coverageMessage && data.phase !== 'upcoming' && !data.holiday && (
        <Alert severity={data.coverage.outage ? 'warning' : tone} sx={{ mb: 2 }}>
          {data.coverage.outage ? 'The classroom camera stopped reporting during this period. ' : ''}{coverageMessage}
          {data.coverage.covered_percent !== null && data.coverage.state !== 'full' ? ` Camera coverage: ${data.coverage.covered_percent}%.` : ''}
        </Alert>
      )}

      <Grid container spacing={2} sx={{ mb: 2 }}>
        {[['Present', counts.PRESENT + counts.LATE, counts.LATE ? `${counts.LATE} late` : 'incl. late', 'good'],
          ['Absent', counts.ABSENT, 'confirmed absences', 'bad'],
          ['Excused', counts.EXCUSED, 'OD / medical', 'default'],
          ['Unverified', counts.UNVERIFIED, 'need your decision', counts.UNVERIFIED ? 'warn' : 'default']].map(([label, value, detail, kpiTone]) => (
            <Grid item xs={6} md={3} key={label}><KpiCard label={label} value={value} detail={detail} tone={kpiTone} /></Grid>
        ))}
      </Grid>

      <SectionCard
        title={`Register · ${data.roster_size} student${data.roster_size === 1 ? '' : 's'}`}
        subtitle="AI detection is a suggestion from camera evidence. The final status is your decision: P Present · L Late · A Absent · E Excused (OD/medical)."
        actions={(
          <ToggleButtonGroup size="small" exclusive value={filter} onChange={(_, value) => value && setFilter(value)} aria-label="Filter students">
            <ToggleButton value="all">All</ToggleButton>
            <ToggleButton value="review">Needs review</ToggleButton>
            <ToggleButton value="present">Present</ToggleButton>
            <ToggleButton value="absent">Absent</ToggleButton>
          </ToggleButtonGroup>
        )}
      >
        {students.length === 0 ? (
          <EmptyState title="No students on this class's roster" description={`Students whose class is ${p.class_name} appear here.`} />
        ) : (
          <>
            {editable && counts.UNVERIFIED > 0 && (
              <Stack direction={{ xs: 'column', sm: 'row' }} gap={1} sx={{ mb: 1.5 }} alignItems={{ sm: 'center' }}>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {counts.UNVERIFIED} student{counts.UNVERIFIED === 1 ? ' is' : 's are'} unverified. Decide each one, or:
                </Typography>
                <Button size="small" variant="outlined" onClick={() => markUnverified('PRESENT')}>Mark unverified present</Button>
                <Button size="small" variant="outlined" color="error" onClick={() => setBulkAbsent(true)}>Mark unverified absent…</Button>
              </Stack>
            )}
            <TableContainer>
              <Table size="small" aria-label="Class register">
                <TableHead>
                  <TableRow>
                    <TableCell>Register no.</TableCell><TableCell>Name</TableCell><TableCell>AI detection</TableCell>
                    <TableCell>Final status</TableCell><TableCell>Evidence / review</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {visible.map((s) => {
                    const d = draft[s.student_db_id] || {};
                    const needReason = FINAL.includes(d.status) && reasonRequired(s, d.status);
                    const evidence = evidenceText(s);
                    return (
                      <TableRow key={s.student_db_id} hover sx={d.status === 'UNVERIFIED' ? { bgcolor: 'rgba(240,163,10,0.06)' } : undefined}>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{s.student_id}</TableCell>
                        <TableCell>{s.name}</TableCell>
                        <TableCell>
                          <Chip size="small" variant="outlined" label={aiLabel(s)}
                            color={s.evidence?.kind === 'classroom_camera' ? (s.ai_status === 'LATE' ? 'warning' : 'success') : 'default'} />
                          {evidence && <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>{evidence}</Typography>}
                          {s.minutes_inside > 0 && (
                            <Typography variant="caption" display="block" color="text.secondary">Inside {formatMinutes(s.minutes_inside)}</Typography>
                          )}
                        </TableCell>
                        <TableCell sx={{ minWidth: 220 }}>
                          {editable ? (
                            <ToggleButtonGroup size="small" exclusive value={d.status} onChange={(_, value) => setStatus(s.student_db_id, value)}
                              aria-label={`Final status of ${s.name}`}>
                              {FINAL.map((status) => (
                                <ToggleButton key={status} value={status} aria-label={`${STATUS_LABELS[status]} for ${s.name}`} sx={{ px: 1.25 }}>
                                  {FINAL_SHORT[status]}
                                </ToggleButton>
                              ))}
                            </ToggleButtonGroup>
                          ) : <AttendanceStatusChip status={s.status} />}
                          <Box sx={{ mt: 0.5 }}><AttendanceStatusChip status={d.status || s.status} /></Box>
                          {editable && needReason && (
                            <TextField size="small" fullWidth sx={{ mt: 1 }} value={d.reason || ''}
                              onChange={(e) => setReason(s.student_db_id, e.target.value)}
                              label="Reason (required)" inputProps={{ 'aria-label': `Reason for ${s.name}`, maxLength: 500 }}
                              error={(d.reason || '').trim().length < 3} />
                          )}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 360 }}>
                          {s.review_flags.map((flag) => (
                            <Typography key={flag.code} variant="caption" display="block" color={flag.code === 'LATE_ARRIVAL' ? 'text.secondary' : 'warning.dark'}>
                              {flag.message}
                            </Typography>
                          ))}
                          {s.final && (
                            <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>
                              Saved: {STATUS_LABELS[s.final.status]} by {s.final.by || 'unknown'} · {s.final.reason}
                            </Typography>
                          )}
                          {!s.review_flags.length && !s.final && (s.evidence ? <Typography variant="caption" color="success.main">Camera-confirmed</Typography> : '—')}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {visible.length === 0 && (
                    <TableRow><TableCell colSpan={5}><Typography variant="body2" color="text.secondary">No students match this filter.</Typography></TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </>
        )}
      </SectionCard>

      {editable && students.length > 0 && (
        <Box sx={{ position: 'sticky', bottom: 0, mt: 2, py: 1.5, px: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider', zIndex: 2 }}>
          {saveError && (
            <Alert severity="error" sx={{ mb: 1 }} action={conflict ? <Button color="inherit" size="small" onClick={load}>Reload</Button> : undefined}>
              {saveError}
            </Alert>
          )}
          <Stack direction={{ xs: 'column', sm: 'row' }} gap={1.5} alignItems={{ sm: 'center' }}>
            <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
              {counts.UNVERIFIED > 0 ? `Decide the ${counts.UNVERIFIED} unverified student${counts.UNVERIFIED === 1 ? '' : 's'} before saving.`
                : missingReasons.length > 0 ? `Give a reason for ${missingReasons.map((s) => s.name).join(', ')}.`
                  : (dirty ? 'Unsaved changes.' : (data.register.submitted ? 'No unsaved changes.' : 'Ready to save.'))}
            </Typography>
            <Button variant="contained" onClick={save} disabled={saving || counts.UNVERIFIED > 0 || missingReasons.length > 0}>
              {saving ? 'Saving…' : 'Save Attendance'}
            </Button>
          </Stack>
        </Box>
      )}

      <ConfirmDialog
        open={bulkAbsent}
        title="Mark unverified students absent?"
        confirmLabel="Mark absent"
        danger
        onCancel={() => setBulkAbsent(false)}
        onConfirm={() => { markUnverified('ABSENT'); setBulkAbsent(false); }}
      >
        {counts.UNVERIFIED} student{counts.UNVERIFIED === 1 ? ' was' : 's were'} not confirmed by the classroom camera.
        Mark them absent only if you know they were not in class. Nothing is saved until you click Save Attendance.
      </ConfirmDialog>
    </Box>
  );
};

// --- history --------------------------------------------------------------------------

export const AttendanceHistoryPage = () => {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const response = await api.get('/staff/history', { params: { limit: 100 } });
      setRows(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load the history'));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Box>
      <PageHeader title="Attendance history" subtitle="Registers you have submitted, newest first. Open one to review or correct it." />
      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={load} /></Box>}
      {!rows && !error && <LoadingState label="Loading history…" />}
      {rows && (
        <SectionCard>
          {rows.length === 0 ? <EmptyState title="No registers submitted yet" description="Saved class registers appear here." /> : (
            <TableContainer>
              <Table size="small" aria-label="Submitted registers">
                <TableHead>
                  <TableRow>
                    <TableCell>Date</TableCell><TableCell>Time</TableCell><TableCell>Course</TableCell><TableCell>Class</TableCell>
                    <TableCell align="right">Present</TableCell><TableCell align="right">Absent</TableCell>
                    <TableCell align="right">Excused</TableCell><TableCell>Saved</TableCell><TableCell align="right" />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={`${r.period_id}-${r.date}`} hover>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{r.day_name.slice(0, 3)} {formatDay(r.date)}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{r.start_time}–{r.end_time}</TableCell>
                      <TableCell>{r.course_name}{r.course_code ? ` (${r.course_code})` : ''}</TableCell>
                      <TableCell>{r.class_name}</TableCell>
                      <TableCell align="right">{r.counts.PRESENT + r.counts.LATE}</TableCell>
                      <TableCell align="right">{r.counts.ABSENT}</TableCell>
                      <TableCell align="right">{r.counts.EXCUSED}</TableCell>
                      <TableCell>
                        <Typography variant="caption" display="block">{r.submitted_by} · {formatDateTime(r.submitted_at)}</Typography>
                        {r.version > 1 && <Typography variant="caption" color="text.secondary">version {r.version}</Typography>}
                      </TableCell>
                      <TableCell align="right">
                        <Button size="small" component={RouterLink} to={registerPath(r.period_id, r.date)}>Open</Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </SectionCard>
      )}
    </Box>
  );
};
