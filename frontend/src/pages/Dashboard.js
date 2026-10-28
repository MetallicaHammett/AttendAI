import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  Grid,
  LinearProgress,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography
} from '@mui/material';
import PeopleAltOutlined from '@mui/icons-material/PeopleAltOutlined';
import MeetingRoomOutlined from '@mui/icons-material/MeetingRoomOutlined';
import PersonSearchOutlined from '@mui/icons-material/PersonSearchOutlined';
import VideocamOutlined from '@mui/icons-material/VideocamOutlined';
import DailyAttendanceChart from '../components/DailyAttendanceChart';
import QuickActions from '../components/QuickActions';
import { EmptyState, ErrorState, KpiCard, LoadingState, PageHeader, SectionCard, formatTime } from '../components/ui';
import { api, getErrorMessage } from '../data';

export const DASHBOARD_REFRESH_MS = 30000;

const TREND_DISPLAY = {
  up: { symbol: '▲', color: 'success', label: 'Up' },
  down: { symbol: '▼', color: 'error', label: 'Down' },
  stable: { symbol: '●', color: 'default', label: 'Stable' }
};

const SEVERITY_COLOR = { error: 'error', warning: 'warning', info: 'info' };
const CATEGORY_LABEL = { attendance: 'Attendance', camera: 'Camera', occupancy: 'Occupancy' };

// Optional request: a failure is shown in its own panel, never blanks the dashboard
const optional = (promise) => promise.then((r) => ({ data: r.data }), (error) => ({ error }));

// GET /dashboard/ -> { stats, daily_attendance (7 days, oldest first), trend } plus
// GET /dashboard/alerts and GET /attendance/recent. Refreshes every DASHBOARD_REFRESH_MS.
const Dashboard = () => {
  const [data, setData] = useState(null);
  const [alerts, setAlerts] = useState({ items: [], error: '' });
  const [recent, setRecent] = useState({ items: [], error: '' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const controllerRef = useRef(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const { signal } = controller;
    try {
      const [dashboard, alertsResult, recentResult] = await Promise.all([
        api.get('/dashboard/', { signal }),
        optional(api.get('/dashboard/alerts', { signal })),
        optional(api.get('/attendance/recent', { params: { limit: 8 }, signal }))
      ]);
      if (signal.aborted) return;
      setData(dashboard.data);
      setAlerts(alertsResult.error
        ? { items: [], error: getErrorMessage(alertsResult.error, 'Alerts unavailable') }
        : { items: alertsResult.data.alerts || [], error: '' });
      setRecent(recentResult.error
        ? { items: [], error: getErrorMessage(recentResult.error, 'Recent events unavailable') }
        : { items: recentResult.data, error: '' });
      setError('');
      setLastUpdated(new Date());
    } catch (err) {
      if (signal.aborted) return;
      setError(getErrorMessage(err, 'Failed to load dashboard'));
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, DASHBOARD_REFRESH_MS);
    return () => {
      clearInterval(interval);
      controllerRef.current?.abort();
    };
  }, [load]);

  if (loading && !data) return <LoadingState label="Loading dashboard..." />;
  if (!data) {
    return <ErrorState message={error || 'Failed to load dashboard'} onRetry={() => { setLoading(true); load(); }} />;
  }

  const { stats, daily_attendance: daily, trend } = data;
  const students = stats.student_count ?? stats.active_students;
  const presentToday = stats.present_today ?? null;
  const knownInside = stats.known_inside_count ?? stats.estimated_present;
  const trendDisplay = TREND_DISPLAY[trend.trend] || TREND_DISPLAY.stable;
  const unhealthy = stats.cameras_unhealthy || 0;
  const processing = stats.cameras_processing || 0;
  const trackingSince = stats.unknown_tracking_since ? new Date(stats.unknown_tracking_since * 1000) : null;
  const presentPct = presentToday !== null && students ? Math.round((presentToday / students) * 100) : null;

  return (
    <Box>
      <PageHeader
        title="Dashboard"
        subtitle={`Live operations${lastUpdated ? ` · updated ${lastUpdated.toLocaleTimeString()}` : ''}`}
        actions={<Button variant="outlined" size="small" onClick={load}>Refresh</Button>}
      />

      <QuickActions />

      {error && (
        // Data is still shown from the last successful load
        <Alert severity="warning" sx={{ mb: 2 }}>Could not refresh: {error}</Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} lg={3}>
          <KpiCard
            label="Present today"
            value={presentToday ?? '—'}
            detail={presentPct === null ? `of ${students} students` : `${presentPct}% of ${students} students`}
            definition="Registered students (not staff) with at least one recorded entry today. The denominator is today's active student roster."
            icon={<PeopleAltOutlined fontSize="small" />}
          />
        </Grid>
        <Grid item xs={12} sm={6} lg={3}>
          <KpiCard
            label="Inside now"
            value={knownInside ?? '—'}
            detail={`${stats.students_inside ?? '—'} students · ${stats.staff_inside ?? '—'} staff`}
            definition="Registered people whose latest event today is an entry. Taken from stored attendance records, so it survives restarts."
            icon={<MeetingRoomOutlined fontSize="small" />}
          />
        </Grid>
        <Grid item xs={12} sm={6} lg={3}>
          <KpiCard
            label="Unregistered inside"
            value={stats.unknown_inside_count ?? 0}
            tone={stats.unknown_inside_count ? 'warn' : 'default'}
            detail={trackingSince ? `Estimate since ${trackingSince.toLocaleTimeString()} · ${stats.unknown_entries_today ?? 0} entered today`
              : 'Not tracking: no camera has run since the server started'}
            definition="Faces that matched no enrolled person at an entry camera, minus those matched at an exit camera. A face-matching estimate kept in memory; it resets when the server restarts."
            icon={<PersonSearchOutlined fontSize="small" />}
          />
        </Grid>
        <Grid item xs={12} sm={6} lg={3}>
          <KpiCard
            label="Cameras processing"
            value={processing}
            tone={unhealthy ? 'bad' : processing ? 'good' : 'default'}
            detail={unhealthy ? `${unhealthy} need attention` : `${stats.active_cameras} active · ${stats.in_cameras} IN / ${stats.out_cameras} OUT`}
            definition="Camera workers running on the server now. 'Need attention' = disconnected, stale or failing to process."
            icon={<VideocamOutlined fontSize="small" />}
          />
        </Grid>
      </Grid>

      <Grid container spacing={2} sx={{ mt: 0 }}>
        <Grid item xs={12} md={7}>
          <SectionCard title="Alerts" subtitle="Why each one was raised, and what it is based on"
            actions={<Chip size="small" label={alerts.items.length} aria-label="Alert count" />}>
            {alerts.error && <Alert severity="warning">{alerts.error}</Alert>}
            {!alerts.error && alerts.items.length === 0 && (
              <EmptyState title="No alerts" description="No attendance exceptions for today's periods, no camera problems and no unregistered visitors inside." />
            )}
            <List dense disablePadding aria-label="Alerts" sx={{ maxHeight: 320, overflowY: 'auto' }}>
              {alerts.items.map((alert, index) => (
                <ListItem key={`${alert.type}-${index}`} disableGutters divider
                  secondaryAction={<Chip size="small" variant="outlined" color={SEVERITY_COLOR[alert.severity] || 'default'}
                    label={CATEGORY_LABEL[alert.category] || alert.category} />}>
                  <ListItemText
                    sx={{ pr: 12 }}
                    primary={alert.title}
                    secondary={<>{alert.detail}<Typography component="span" variant="caption" display="block" color="text.disabled">Basis: {alert.basis}</Typography></>}
                  />
                </ListItem>
              ))}
            </List>
          </SectionCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <SectionCard title="Classroom occupancy" subtitle="Registered people inside, by the classroom of their entry camera">
            {(stats.classroom_occupancy || []).length === 0 ? (
              <EmptyState title="No classrooms yet" description="Create classrooms and assign cameras on the Timetable and Cameras pages."
                action={<Button size="small" component={RouterLink} to="/timetable">Set up classrooms</Button>} />
            ) : (
              <Stack gap={1.5} aria-label="Classroom occupancy">
                {stats.classroom_occupancy.map((room) => (
                  <Box key={room.classroom_id ?? 'none'}>
                    <Stack direction="row" justifyContent="space-between">
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{room.name}</Typography>
                      <Typography variant="body2" aria-label={`${room.name} inside`}>{room.known_inside}</Typography>
                    </Stack>
                    <LinearProgress variant="determinate"
                      value={knownInside ? Math.min(100, (room.known_inside / knownInside) * 100) : 0}
                      sx={{ height: 6, borderRadius: 3, mt: 0.5 }} />
                  </Box>
                ))}
                <Typography variant="caption" color="text.secondary">
                  Unregistered visitors are not attributed to classrooms.
                </Typography>
              </Stack>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={8}>
          <SectionCard
            title="Last 7 days"
            subtitle="Distinct students with an entry each day"
            actions={(
              <Chip
                aria-label="Attendance trend"
                color={trendDisplay.color}
                label={`${trendDisplay.symbol} ${trendDisplay.label} ${trend.change_percentage > 0 ? '+' : ''}${trend.change_percentage}%`}
                title={`Average daily attendance, last 7 completed days vs the ${trend.period_days / 2} days before`}
              />
            )}
          >
            <DailyAttendanceChart days={daily} height={240} showTable={false} />
            <Typography variant="caption" color="text.secondary">
              Absent is counted against today's active students. Late uses ATTENDANCE_LATE_AFTER when configured; per-period
              lateness is on the Reports page.
            </Typography>
          </SectionCard>
        </Grid>
        <Grid item xs={12} md={4}>
          <SectionCard title="Recent events"
            subtitle={`${stats.today_entries} ${stats.today_entries === 1 ? 'entry' : 'entries'} · ${stats.today_exits} ${stats.today_exits === 1 ? 'exit' : 'exits'} today`}
            actions={<Button size="small" component={RouterLink} to="/attendance">View all</Button>}>
            {recent.error && <Alert severity="warning">{recent.error}</Alert>}
            {!recent.error && recent.items.length === 0 && (
              <EmptyState title="No events yet" description="Entries and exits appear here as cameras record them." />
            )}
            <List dense disablePadding aria-label="Recent events">
              {recent.items.map((event) => (
                <ListItem key={event.id} disableGutters divider
                  secondaryAction={<Chip size="small" label={event.event_type} color={event.event_type === 'ENTRY' ? 'success' : 'default'}
                    variant={event.event_type === 'ENTRY' ? 'filled' : 'outlined'} />}>
                  <ListItemText primary={event.student_name}
                    secondary={`${formatTime(event.timestamp)} · ${event.camera_name || 'manual entry'}`} />
                </ListItem>
              ))}
            </List>
          </SectionCard>
        </Grid>
      </Grid>

      <Stack direction="row" gap={1.5} sx={{ mt: 3 }} flexWrap="wrap" aria-label="Quick actions">
        <Button variant="contained" component={RouterLink} to="/students">Manage Students</Button>
        <Button variant="outlined" component={RouterLink} to="/live-monitoring">Live Monitoring</Button>
        <Button variant="outlined" component={RouterLink} to="/cameras">Manage Cameras</Button>
        <Button variant="outlined" component={RouterLink} to="/attendance">Attendance Records</Button>
        <Button variant="outlined" component={RouterLink} to="/reports">Reports & exceptions</Button>
      </Stack>
    </Box>
  );
};

export default Dashboard;
