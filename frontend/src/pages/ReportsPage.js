import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
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
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography
} from '@mui/material';
import {
  AttendanceStatusChip, EmptyState, ErrorState, LoadingState, PageHeader, STATUS_LABELS, formatLocalTime, formatMinutes, todayIso
} from '../components/ui';
import { usePermission } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, downloadFile, getBlobErrorMessage, getErrorMessage } from '../data';

const weekdayOf = (iso) => (new Date(`${iso}T00:00:00`).getDay() + 6) % 7; // 0 = Monday
const EXCEPTION_LABELS = {
  LATE_ARRIVAL: 'Late arrival',
  ABSENT: 'Absent',
  EARLY_DEPARTURE: 'Left early',
  OUT_OF_CLASS: 'Out of class now',
  LEFT_DURING_PERIOD: 'Left and returned',
  DID_NOT_RETURN_AFTER_BREAK: 'Did not return after break'
};

const useExport = () => {
  const notify = useNotify();
  const [busy, setBusy] = useState('');
  const run = async (key, url, params, fallback) => {
    setBusy(key);
    try {
      notify(`Downloaded ${await downloadFile(url, params, fallback)}`);
    } catch (err) {
      notify(await getBlobErrorMessage(err, 'Export failed'), 'error');
    } finally {
      setBusy('');
    }
  };
  return [busy, run];
};

const CountChips = ({ counts }) => (
  <Stack direction="row" gap={1} flexWrap="wrap">
    {Object.entries(counts).filter(([, n]) => n > 0).map(([status, n]) => (
      <Chip key={status} size="small" variant="outlined" label={`${STATUS_LABELS[status] || status}: ${n}`} />
    ))}
  </Stack>
);

const OverrideDialog = ({ target, onClose, onSaved }) => {
  const [status, setStatus] = useState('EXCUSED');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (target) {
      setStatus(target.student.status === 'ABSENT' ? 'EXCUSED' : 'PRESENT');
      setReason(target.student.override_reason || '');
      setError('');
    }
  }, [target]);
  const save = async () => {
    if (reason.trim().length < 3) { setError('A reason is required (it is kept in the audit log).'); return; }
    setSaving(true);
    try {
      await api.put(`/timetable/periods/${target.periodId}/attendance/${target.student.student_db_id}`,
        { status, reason: reason.trim() }, { params: { date: target.date } });
      onSaved();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not save the override'));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog open={Boolean(target)} onClose={saving ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Override attendance</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {target?.student.name}: computed as {STATUS_LABELS[target?.student.computed_status] || target?.student.computed_status}.
          An override replaces this status in every report; the computed status stays visible.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} fullWidth margin="dense">
          {['PRESENT', 'LATE', 'ABSENT', 'EXCUSED'].map((s) => <MenuItem key={s} value={s}>{STATUS_LABELS[s]}</MenuItem>)}
        </TextField>
        <TextField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} fullWidth margin="dense"
          multiline minRows={2} inputProps={{ maxLength: 500 }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save override'}</Button>
      </DialogActions>
    </Dialog>
  );
};

// --- period attendance ---------------------------------------------------------------

const PeriodTab = () => {
  const canOverride = usePermission('operator');
  const notify = useNotify();
  const [date, setDate] = useState(todayIso());
  const [periods, setPeriods] = useState([]);
  const [periodId, setPeriodId] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [overriding, setOverriding] = useState(null);
  const [busy, runExport] = useExport();

  useEffect(() => {
    if (!date) return undefined;
    const controller = new AbortController();
    setPeriods([]);
    setResult(null);
    api.get('/timetable/periods', { params: { day_of_week: weekdayOf(date) }, signal: controller.signal })
      .then((r) => {
        setPeriods(r.data);
        setPeriodId((current) => (r.data.some((p) => String(p.id) === current) ? current : (r.data[0] ? String(r.data[0].id) : '')));
      })
      .catch((err) => { if (!controller.signal.aborted) setError(getErrorMessage(err, 'Could not load periods')); });
    return () => controller.abort();
  }, [date]);

  const load = useCallback(async () => {
    if (!periodId || !date) { setResult(null); return; }
    setLoading(true);
    setError('');
    try {
      const response = await api.get(`/timetable/periods/${periodId}/attendance`, { params: { date } });
      setResult(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not compute attendance'));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [periodId, date]);

  useEffect(() => { load(); }, [load]);

  const clearOverride = async (student) => {
    try {
      await api.delete(`/timetable/periods/${periodId}/attendance/${student.student_db_id}`, { params: { date } });
      notify('Override removed');
      load();
    } catch (err) {
      notify(getErrorMessage(err, 'Could not remove the override'), 'error');
    }
  };

  return (
    <Box>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" gap={2} flexWrap="wrap" alignItems="center">
          <TextField label="Date" type="date" size="small" value={date} InputLabelProps={{ shrink: true }} onChange={(e) => setDate(e.target.value)} />
          <TextField select label="Period" size="small" value={periodId} onChange={(e) => setPeriodId(e.target.value)} sx={{ minWidth: 320 }}
            disabled={periods.length === 0}>
            {periods.map((p) => (
              <MenuItem key={p.id} value={String(p.id)}>{`${p.start_time.slice(0, 5)}–${p.end_time.slice(0, 5)} · ${p.course_name} · ${p.class_name}`}</MenuItem>
            ))}
          </TextField>
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" variant="outlined" disabled={!result || Boolean(busy)}
            onClick={() => runExport('period', '/reports/period.csv', { period_id: periodId, date }, `period_${periodId}_${date}.csv`)}>
            {busy === 'period' ? 'Preparing…' : 'Export CSV'}
          </Button>
        </Stack>
      </Paper>

      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={load} /></Box>}
      {periods.length === 0 && !error && (
        <EmptyState title="No periods on this day" description="Choose another date, or add periods on the Timetable page." />
      )}
      {loading && !result && <LoadingState label="Computing attendance..." />}
      {result && (
        <>
          <Stack direction={{ xs: 'column', md: 'row' }} gap={2} alignItems={{ md: 'center' }} sx={{ mb: 2 }}>
            <Box>
              <Typography variant="h6" component="h2">{result.course_name} · {result.class_name}</Typography>
              <Typography variant="body2" color="text.secondary">
                {result.start_time}–{result.end_time} · grace {result.grace_minutes} min · {result.roster_size} on roster ·{' '}
                {result.phase === 'completed' ? 'finished' : result.phase === 'in_progress' ? 'in progress' : 'not started'}
              </Typography>
            </Box>
            <Box sx={{ flexGrow: 1 }} />
            <Box sx={{ textAlign: { md: 'right' } }}>
              <Typography variant="h5" component="p" aria-label="Attendance rate">
                {result.attendance_rate === null ? '—' : `${result.attendance_rate}%`}
              </Typography>
              {result.decided_count !== undefined && (
                <Typography variant="caption" color="text.secondary">
                  of {result.decided_count} decided · {result.roster_size} on roster
                </Typography>
              )}
            </Box>
          </Stack>
          <Box sx={{ mb: 2 }}><CountChips counts={result.counts} /></Box>
          {result.holiday && <Alert severity="info" sx={{ mb: 2 }}>Holiday: {result.holiday}. No attendance is taken.</Alert>}
          {!result.camera_coverage && !result.holiday && result.phase !== 'upcoming' && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              No entry camera was running during this period, so students without a recorded entry are shown as
              Unverified, not Absent.
            </Alert>
          )}
          {result.students.length === 0 ? (
            <EmptyState title="Empty roster" description={`No active students have the class “${result.class_name}”.`} />
          ) : (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" aria-label="Period attendance">
                <TableHead>
                  <TableRow>
                    <TableCell>Student</TableCell><TableCell>Status</TableCell><TableCell>Arrived</TableCell>
                    <TableCell>Left</TableCell><TableCell align="right">Inside</TableCell><TableCell>Exceptions</TableCell>
                    {canOverride && <TableCell align="right">Actions</TableCell>}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.students.map((s) => (
                    <TableRow key={s.student_db_id} hover>
                      <TableCell>{s.name}<Typography variant="caption" color="text.secondary" component="div">{s.student_id}</Typography></TableCell>
                      <TableCell>
                        <Tooltip title={s.overridden ? `Computed: ${STATUS_LABELS[s.computed_status]}. Reason: ${s.override_reason}` : ''}>
                          <span><AttendanceStatusChip status={s.status} overridden={s.overridden} /></span>
                        </Tooltip>
                      </TableCell>
                      <TableCell>{s.inside_at_start && !s.arrived_at ? 'Before start' : formatLocalTime(s.arrived_at)}</TableCell>
                      <TableCell>{formatLocalTime(s.left_at)}</TableCell>
                      <TableCell align="right">{formatMinutes(s.minutes_inside)}</TableCell>
                      <TableCell>
                        {s.exceptions.map((e) => (
                          <Tooltip key={e.type} title={e.reason}>
                            <Chip size="small" color="warning" variant="outlined" label={EXCEPTION_LABELS[e.type] || e.type} sx={{ mr: 0.5, mb: 0.5 }} />
                          </Tooltip>
                        ))}
                      </TableCell>
                      {canOverride && (
                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                          <Button size="small" onClick={() => setOverriding({ periodId, date, student: s })}
                            aria-label={`Override ${s.name}`} disabled={Boolean(result.holiday)}>Override</Button>
                          {s.overridden && <Button size="small" onClick={() => clearOverride(s)} aria-label={`Clear override for ${s.name}`}>Clear</Button>}
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Rate = (present + late + excused) ÷ students whose status could be decided. Exceptions come from recorded entry and
            exit events; recognition errors can still affect them.
          </Typography>
        </>
      )}
      <OverrideDialog target={overriding} onClose={() => setOverriding(null)}
        onSaved={() => { setOverriding(null); notify('Override saved'); load(); }} />
    </Box>
  );
};

// --- daily summary ----------------------------------------------------------------------

const DailyTab = () => {
  const [date, setDate] = useState(todayIso());
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, runExport] = useExport();

  const load = useCallback(async () => {
    if (!date) return;
    setLoading(true);
    setError('');
    try {
      setSummary((await api.get('/timetable/daily', { params: { date } })).data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load the daily summary'));
    } finally {
      setLoading(false);
    }
  }, [date]);
  useEffect(() => { load(); }, [load]);

  return (
    <Box>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" gap={2} flexWrap="wrap" alignItems="center">
          <TextField label="Date" type="date" size="small" value={date} InputLabelProps={{ shrink: true }} onChange={(e) => setDate(e.target.value)} />
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" variant="outlined" disabled={Boolean(busy)} onClick={() => runExport('csv', '/reports/daily.csv', { date }, `daily_${date}.csv`)}>
            {busy === 'csv' ? 'Preparing…' : 'Export CSV'}
          </Button>
          <Button size="small" variant="outlined" disabled={Boolean(busy)} onClick={() => runExport('pdf', '/reports/daily.pdf', { date }, `daily_${date}.pdf`)}>
            {busy === 'pdf' ? 'Preparing…' : 'Export PDF'}
          </Button>
        </Stack>
      </Paper>
      {error && <ErrorState message={error} onRetry={load} />}
      {loading && !summary && <LoadingState label="Loading daily summary..." />}
      {summary && (summary.periods.length === 0 ? (
        <EmptyState title="No classes scheduled" description="There are no active periods on this weekday." />
      ) : (
        <>
          <TableContainer component={Paper} variant="outlined" sx={{ mb: 3 }}>
            <Table size="small" aria-label="Daily summary">
              <TableHead>
                <TableRow>
                  <TableCell>Time</TableCell><TableCell>Course</TableCell><TableCell>Class</TableCell>
                  <TableCell align="right">Roster</TableCell><TableCell align="right">Present</TableCell>
                  <TableCell align="right">Late</TableCell><TableCell align="right">Absent</TableCell>
                  <TableCell align="right">Rate</TableCell><TableCell>Coverage</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {summary.periods.map((p) => (
                  <TableRow key={p.period_id} hover>
                    <TableCell>{p.start_time}–{p.end_time}</TableCell>
                    <TableCell>{p.course_name}</TableCell>
                    <TableCell>{p.class_name}</TableCell>
                    <TableCell align="right">{p.roster_size}</TableCell>
                    <TableCell align="right">{p.counts.PRESENT}</TableCell>
                    <TableCell align="right">{p.counts.LATE}</TableCell>
                    <TableCell align="right">{p.counts.ABSENT}</TableCell>
                    <TableCell align="right">
                      {p.attendance_rate === null ? '—' : `${p.attendance_rate}%`}
                      {p.decided_count !== undefined && p.decided_count < p.roster_size && (
                        <Typography variant="caption" color="text.secondary" component="div">of {p.decided_count} decided</Typography>
                      )}
                    </TableCell>
                    <TableCell>{p.holiday ? <Chip size="small" label="Holiday" /> : p.camera_coverage
                      ? <Chip size="small" color="success" variant="outlined" label="Camera on" />
                      : <Chip size="small" color="warning" variant="outlined" label={p.phase === 'upcoming' ? 'Not started' : 'No camera'} />}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography variant="h6" component="h2" sx={{ mb: 1 }}>Exceptions ({summary.exceptions.length})</Typography>
          {summary.exceptions.length === 0 ? (
            <Typography variant="body2" color="text.secondary">No exceptions for this day.</Typography>
          ) : (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" aria-label="Exceptions">
                <TableHead>
                  <TableRow><TableCell>Period</TableCell><TableCell>Student</TableCell><TableCell>Exception</TableCell><TableCell>Why</TableCell></TableRow>
                </TableHead>
                <TableBody>
                  {summary.exceptions.map((e, i) => (
                    <TableRow key={`${e.period_id}-${e.student_db_id}-${e.type}-${i}`}>
                      <TableCell>{e.start_time} {e.course_name}</TableCell>
                      <TableCell>{e.name} ({e.student_id})</TableCell>
                      <TableCell><Chip size="small" variant="outlined" color="warning" label={EXCEPTION_LABELS[e.type] || e.type} /></TableCell>
                      <TableCell>{e.reason}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </>
      ))}
    </Box>
  );
};

// --- monthly summary ---------------------------------------------------------------------

const MonthlyTab = () => {
  const [month, setMonth] = useState(todayIso().slice(0, 7));
  const [className, setClassName] = useState('');
  const [classes, setClasses] = useState([]);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, runExport] = useExport();
  const params = useMemo(() => {
    const [year, m] = month.split('-').map(Number);
    return { year, month: m, ...(className ? { class_name: className } : {}) };
  }, [month, className]);

  useEffect(() => {
    api.get('/students/classes').then((r) => setClasses(r.data)).catch(() => setClasses([]));
  }, []);

  const load = useCallback(async () => {
    if (!params.year || !params.month) return;
    setLoading(true);
    setError('');
    try {
      setSummary((await api.get('/timetable/monthly', { params })).data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load the monthly summary'));
    } finally {
      setLoading(false);
    }
  }, [params]);
  useEffect(() => { load(); }, [load]);

  const stamp = `${params.year}-${String(params.month).padStart(2, '0')}`;
  return (
    <Box>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" gap={2} flexWrap="wrap" alignItems="center">
          <TextField label="Month" type="month" size="small" value={month} InputLabelProps={{ shrink: true }} onChange={(e) => setMonth(e.target.value)} />
          <TextField select label="Class" size="small" value={className} onChange={(e) => setClassName(e.target.value)} sx={{ minWidth: 180 }}>
            <MenuItem value="">All classes</MenuItem>
            {classes.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </TextField>
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" variant="outlined" disabled={Boolean(busy)} onClick={() => runExport('csv', '/reports/monthly.csv', params, `monthly_${stamp}.csv`)}>
            {busy === 'csv' ? 'Preparing…' : 'Export CSV'}
          </Button>
          <Button size="small" variant="outlined" disabled={Boolean(busy)} onClick={() => runExport('pdf', '/reports/monthly.pdf', params, `monthly_${stamp}.pdf`)}>
            {busy === 'pdf' ? 'Preparing…' : 'Export PDF'}
          </Button>
        </Stack>
      </Paper>
      {error && <ErrorState message={error} onRetry={load} />}
      {loading && !summary && <LoadingState label="Computing monthly summary..." />}
      {summary && (summary.students.length === 0 ? (
        <EmptyState title="No completed periods" description="Monthly figures appear once scheduled periods in this month have finished." />
      ) : (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Completed periods through {summary.through} · {summary.days_with_classes} day(s) with classes
          </Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small" aria-label="Monthly summary">
              <TableHead>
                <TableRow>
                  <TableCell>Student</TableCell><TableCell>Class</TableCell><TableCell align="right">Periods</TableCell>
                  <TableCell align="right">Present</TableCell><TableCell align="right">Late</TableCell>
                  <TableCell align="right">Absent</TableCell><TableCell align="right">Excused</TableCell>
                  <TableCell align="right">Unverified</TableCell><TableCell align="right">Left early</TableCell>
                  <TableCell align="right">Rate</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {summary.students.map((s) => (
                  <TableRow key={s.student_db_id} hover>
                    <TableCell>{s.name}<Typography variant="caption" color="text.secondary" component="div">{s.student_id}</Typography></TableCell>
                    <TableCell>{s.class_name}</TableCell>
                    <TableCell align="right">{s.scheduled}</TableCell>
                    <TableCell align="right">{s.present}</TableCell>
                    <TableCell align="right">{s.late}</TableCell>
                    <TableCell align="right">{s.absent}</TableCell>
                    <TableCell align="right">{s.excused}</TableCell>
                    <TableCell align="right">{s.unverified}</TableCell>
                    <TableCell align="right">{s.early_departures}</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 600, color: s.attendance_rate !== null && s.attendance_rate < 75 ? 'error.main' : undefined }}>
                      {s.attendance_rate === null ? '—' : `${s.attendance_rate}%`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Rate = (present + late + excused) ÷ (periods − unverified). Rates below 75% are highlighted.
          </Typography>
        </>
      ))}
    </Box>
  );
};

const TABS = { period: 'Period attendance', daily: 'Daily summary', monthly: 'Monthly summary' };

const ReportsPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = TABS[searchParams.get('tab')] ? searchParams.get('tab') : 'period';
  return (
    <Box>
      <PageHeader title="Reports" subtitle="Period attendance, exceptions and summaries computed from recorded events and the timetable." />
      <Tabs value={tab} onChange={(_, next) => setSearchParams({ tab: next }, { replace: true })}
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }} aria-label="Report type">
        {Object.entries(TABS).map(([value, label]) => <Tab key={value} value={value} label={label} />)}
      </Tabs>
      {tab === 'period' && <PeriodTab />}
      {tab === 'daily' && <DailyTab />}
      {tab === 'monthly' && <MonthlyTab />}
    </Box>
  );
};

export default ReportsPage;
