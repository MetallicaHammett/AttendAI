import React from 'react';
import { Typography } from '@mui/material';
import {
  Bar,
  BarChart as RechartsBarChart,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';

const DEFAULT_COLOR = '#90a4ae';

// Simple single-series bar chart. data: [{ name, value, color? }]
const BarChart = ({ data, height = 260, ariaLabel }) => (
  <div role="img" aria-label={ariaLabel}>
    <ResponsiveContainer width="100%" height={height}>
      <RechartsBarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="name" />
        <YAxis allowDecimals={false} />
        <Tooltip />
        <Bar dataKey="value" isAnimationActive={false}>
          {data.map((item) => (
            <Cell key={item.name} fill={item.color || DEFAULT_COLOR} />
          ))}
        </Bar>
      </RechartsBarChart>
    </ResponsiveContainer>
    {/* The plotted values as text, for screen readers and exact numbers */}
    <Typography variant="caption" color="text.secondary" component="p">
      {data.map((item) => `${item.name}: ${item.value}`).join(' · ')}
    </Typography>
  </div>
);

export default BarChart;
