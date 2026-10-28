import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  Button,
  Grid,
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
import BarChartOutlined from '@mui/icons-material/BarChartOutlined';
import DonutLargeOutlined from '@mui/icons-material/DonutLargeOutlined';
import TableChartOutlined from '@mui/icons-material/TableChartOutlined';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import { EmptyState, ErrorState, LoadingState, SectionCard } from './ui';
import { api, getErrorMessage } from '../data';
import { tokens } from '../theme';

// Below this total percentage a course is highlighted (a common minimum-attendance rule)
export const ATTENDANCE_THRESHOLD = 75;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "2026-07-20" -> "20/Jul/2026" (date only, no time-zone shift)
export const portalDate = (iso) => {
  const [y, m, d] = (iso || '').split('-').map(Number);
  return y && m && d ? `${String(d).padStart(2, '0')}/${MONTHS[m - 1]}/${y}` : '—';
};

const pct = (value) => (value === null || value === undefined ? '—' : value.toFixed(2));

const CourseChart = ({ courses }) => {
  const data = courses.filter((c) => c.total_pct !== null)
    .map((c) => ({ code: c.course_code || c.course_name, value: c.total_pct }));
  if (data.length === 0) return <EmptyState title="No completed classes yet" />;
  return (
    <div role="img" aria-label="Course-wise attendance percentage">
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="code" tick={{ fontSize: 12 }} />
          <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(v) => `${v}%`} width={44} />
          <Tooltip formatter={(v) => [`${v.toFixed(2)}%`, 'Attendance']} />
          <Bar dataKey="value" isAnimationActive={false} maxBarSize={36}>
            {data.map((item) => (
              <Cell key={item.code} fill={item.value < ATTENDANCE_THRESHOLD ? tokens.color.absent : tokens.color.chartBar} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <Typography variant="caption" color="text.secondary" component="p">
        {data.map((item) => `${item.code}: ${item.value.toFixed(2)}%`).join(' · ')}
      </Typography>
    </div>
  );
};

const HoursDonut = ({ totals }) => {
  const present = totals.attended + totals.od_ml;
  const data = [
    { name: 'Hours Present', value: present, color: tokens.color.present },
    { name: 'Hours Absent', value: totals.absent, color: tokens.color.absent }
  ];
  if (present + totals.absent === 0) return <EmptyState title="No completed classes yet" />;
  return (
    <div role="img" aria-label="Attendance hours">
      <ResponsiveContainer width="100%" height={280}>
        <PieChart>
          <Legend verticalAlign="top" iconType="rect" />
          <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="85%" startAngle={90} endAngle={-270}
            isAnimationActive={false} stroke="#fff">
            {data.map((item) => <Cell key={item.name} fill={item.color} />)}
          </Pie>
          <Tooltip />
        </PieChart>
      </ResponsiveContainer>
      <Typography variant="caption" color="text.secondary" component="p" align="center">
        Hours present: {present} · Hours absent: {totals.absent}
      </Typography>
    </div>
  );
};

const CourseTable = ({ courses, totals }) => (
  <TableContainer>
    <Table size="small" aria-label="Course-wise attendance">
      <TableHead>
        <TableRow>
          <TableCell>Code</TableCell><TableCell>Description</TableCell>
          <TableCell align="right">Max. hours</TableCell><TableCell align="right">Att. hours</TableCell>
          <TableCell align="right">Absent hours</TableCell><TableCell align="right">Average %</TableCell>
          <TableCell align="right">OD/ML Percentage</TableCell><TableCell align="right">Total Percentage</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {courses.map((c) => {
          const low = c.total_pct !== null && c.total_pct < ATTENDANCE_THRESHOLD;
          return (
            <TableRow key={c.course_code || c.course_name} hover>
              <TableCell>{c.course_code || '—'}</TableCell>
              <TableCell sx={{ textTransform: 'uppercase' }}>{c.course_name}</TableCell>
              <TableCell align="right">{c.max_hours}</TableCell>
              <TableCell align="right">{c.attended}</TableCell>
              <TableCell align="right">{c.absent}</TableCell>
              <TableCell align="right">{pct(c.average_pct)}</TableCell>
              <TableCell align="right">{pct(c.od_ml_pct)}</TableCell>
              <TableCell align="right" sx={low ? { color: 'error.main', fontWeight: 700 } : undefined}
                aria-label={low ? `${pct(c.total_pct)}, below ${ATTENDANCE_THRESHOLD}%` : undefined}>
                {pct(c.total_pct)}
              </TableCell>
            </TableRow>
          );
        })}
        <TableRow sx={{ '& td': { fontWeight: 700 } }}>
          <TableCell colSpan={2}>Total</TableCell>
          <TableCell align="right">{totals.max_hours}</TableCell>
          <TableCell align="right">{totals.attended}</TableCell>
          <TableCell align="right">{totals.absent}</TableCell>
          <TableCell colSpan={2} />
          <TableCell align="right">{pct(totals.total_pct)}</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  </TableContainer>
);

// Course-wise attendance of one student. `url` is /portal/me/attendance (the student's own
// page) or /portal/students/:id/attendance (staff). `header(student)` renders the page title.
const StudentAttendanceReport = ({ url, header }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState({ start_date: '', end_date: '' });

  const load = useCallback(async (params = {}) => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get(url, { params });
      setData(response.data);
      setRange({ start_date: response.data.start_date, end_date: response.data.end_date });
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load attendance'));
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => { load(); }, [load]);

  const apply = () => load({ start_date: range.start_date, end_date: range.end_date });

  return (
    <Box>
      {header(data?.student)}
      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={() => load()} /></Box>}
      {loading && !data ? <LoadingState label="Loading attendance..." /> : data && (
        <>
          <Stack direction={{ xs: 'column', sm: 'row' }} gap={1.5} alignItems={{ sm: 'center' }} sx={{ mb: 2 }}>
            <TextField label="From" type="date" size="small" value={range.start_date} InputLabelProps={{ shrink: true }}
              onChange={(e) => setRange({ ...range, start_date: e.target.value })} />
            <TextField label="To" type="date" size="small" value={range.end_date} InputLabelProps={{ shrink: true }}
              onChange={(e) => setRange({ ...range, end_date: e.target.value })} />
            <Button variant="contained" onClick={apply} disabled={loading || !range.start_date || !range.end_date}>
              Show
            </Button>
          </Stack>
          <Grid container spacing={2.5} sx={{ mb: 2.5 }}>
            <Grid item xs={12} md={8}>
              <SectionCard title="Course Wise Attendance (%)" icon={<BarChartOutlined fontSize="small" />}>
                <CourseChart courses={data.courses} />
              </SectionCard>
            </Grid>
            <Grid item xs={12} md={4}>
              <SectionCard title="Attendance Hours" icon={<DonutLargeOutlined fontSize="small" />}>
                <HoursDonut totals={data.totals} />
              </SectionCard>
            </Grid>
          </Grid>
          <SectionCard icon={<TableChartOutlined fontSize="small" />}
            title={`COURSE WISE ATTENDANCE - During the Period: ${portalDate(data.start_date)} To ${portalDate(data.end_date)}`}
            contentSx={{ px: 0, pt: 0 }}>
            {data.courses.length === 0
              ? <EmptyState title="No classes in this period" description="No timetable periods for this class fall in the chosen dates." />
              : <CourseTable courses={data.courses} totals={data.totals} />}
            <Typography variant="caption" color="text.secondary" component="p" sx={{ px: 2.25, pt: 1.5 }}>
              Each class period counts as one hour. OD/ML is a period excused by staff with a reason. Late arrivals count as
              attended ({data.totals.late} so far).
              {data.totals.unverified > 0 && ` ${data.totals.unverified} period(s) are not counted because no entry camera was running.`}
            </Typography>
          </SectionCard>
        </>
      )}
    </Box>
  );
};

export default StudentAttendanceReport;
