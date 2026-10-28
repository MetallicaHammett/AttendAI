import React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import { Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import { format, parseISO } from 'date-fns';

export const DAILY_COLORS = { present: '#12805c', late: '#e08a2c', absent: '#d0d5dd' };

// "2026-10-19" (a server-local calendar day) -> "Mon 19 Oct"
export const formatDay = (isoDate) => format(parseISO(isoDate), 'EEE d MMM');

// Stacked present/late/absent per day from GET /dashboard/daily/{days}
// (oldest first), plus the same numbers as a table.
const DailyAttendanceChart = ({ days, height = 280, showTable = true }) => {
  const rows = days.map((day) => ({ ...day, label: formatDay(day.date) }));

  return (
    <>
      <div role="img" aria-label="Daily attendance chart">
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="present" name="Present" stackId="day" fill={DAILY_COLORS.present} isAnimationActive={false} />
            <Bar dataKey="late" name="Late" stackId="day" fill={DAILY_COLORS.late} isAnimationActive={false} />
            <Bar dataKey="absent" name="Absent" stackId="day" fill={DAILY_COLORS.absent} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {showTable && <TableContainer sx={{ mt: 2 }}>
        <Table size="small" aria-label="Daily attendance">
          <TableHead>
            <TableRow>
              <TableCell>Day</TableCell>
              <TableCell align="right">Present</TableCell>
              <TableCell align="right">Late</TableCell>
              <TableCell align="right">Absent</TableCell>
              <TableCell align="right">Total</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.date}>
                <TableCell component="th" scope="row">{row.label}</TableCell>
                <TableCell align="right">{row.present}</TableCell>
                <TableCell align="right">{row.late}</TableCell>
                <TableCell align="right">{row.absent}</TableCell>
                <TableCell align="right">{row.total}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>}
    </>
  );
};

export default DailyAttendanceChart;
