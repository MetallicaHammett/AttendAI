// Shared building blocks so every page looks and behaves the same.
import React from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Skeleton,
  Stack,
  Tooltip,
  Typography
} from '@mui/material';
import InfoOutlined from '@mui/icons-material/InfoOutlined';
import InboxOutlined from '@mui/icons-material/InboxOutlined';
import { statusColors, tokens } from '../theme';

// Set only when the separate demo instance is started (REACT_APP_DEMO_MODE=true); the real
// app never shows this. Demo data is fictional and its attendance is not from cameras.
export const isDemoMode = () => process.env.REACT_APP_DEMO_MODE === 'true';

export const DemoBanner = ({ sx }) => (isDemoMode() ? (
  <Alert severity="warning" variant="filled" role="note" sx={{ mb: 2, ...sx }}>
    DEMO instance — fictional people and synthetic attendance (not from cameras). Not the real AttendAI database.
  </Alert>
) : null);

export const PageHeader = ({ title, subtitle, actions }) => (
  <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }}
    gap={1.5} sx={{ mb: 3 }}>
    <Box>
      <Typography variant="h4" component="h1">{title}</Typography>
      {subtitle && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{subtitle}</Typography>}
    </Box>
    {actions && <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">{actions}</Stack>}
  </Stack>
);

// Labelled metric with an explicit definition (shown on hover / focus)
export const KpiCard = ({ label, value, detail, definition, tone = 'default', icon, loading }) => {
  const toneColor = { default: tokens.color.ink, good: '#12805c', warn: '#b54708', bad: '#c4320a', info: '#175cd3' }[tone];
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent sx={{ p: 2.25, '&:last-child': { pb: 2.25 } }}>
        <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 1 }}>
          {icon && <Box sx={{ color: 'text.secondary', display: 'flex' }}>{icon}</Box>}
          <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>{label}</Typography>
          {definition && (
            <Tooltip title={definition}>
              <InfoOutlined tabIndex={0} aria-label={`How "${label}" is calculated`} sx={{ fontSize: 16, color: 'text.disabled', ml: 'auto' }} />
            </Tooltip>
          )}
        </Stack>
        {loading ? <Skeleton width={64} height={40} /> : (
          <Typography variant="h4" component="p" aria-label={label} sx={{ color: toneColor, fontVariantNumeric: 'tabular-nums' }}>
            {value}
          </Typography>
        )}
        {detail && <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5 }}>{detail}</Typography>}
      </CardContent>
    </Card>
  );
};

// Card with a solid blue title bar (portal style). Actions sit in a strip under the bar,
// so buttons and inputs keep their normal colours.
export const SectionCard = ({ title, subtitle, actions, icon, children, sx, contentSx, ...rest }) => (
  <Card sx={{ height: '100%', overflow: 'hidden', ...sx }} {...rest}>
    {title && (
      <Box sx={{ bgcolor: tokens.color.cardHeader, color: '#fff', px: 2.25, py: 1.5 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          {icon && <Box aria-hidden sx={{ display: 'flex' }}>{icon}</Box>}
          <Typography variant="h6" component="h2" sx={{ color: 'inherit' }}>{title}</Typography>
        </Stack>
        {subtitle && <Typography variant="caption" component="p" sx={{ color: 'inherit', opacity: 0.9 }}>{subtitle}</Typography>}
      </Box>
    )}
    {actions && (
      <Stack direction="row" gap={1} alignItems="center" justifyContent="flex-end" flexWrap="wrap"
        sx={{ px: 2.25, py: 1, borderBottom: `1px solid ${tokens.color.border}` }}>
        {actions}
      </Stack>
    )}
    <Box sx={{ px: 2.25, pt: 2, pb: 2, ...contentSx }}>{children}</Box>
  </Card>
);

export const EmptyState = ({ title, description, action }) => (
  <Stack alignItems="center" gap={1} sx={{ py: 5, px: 2, textAlign: 'center', color: 'text.secondary' }}>
    <InboxOutlined sx={{ fontSize: 36, color: 'text.disabled' }} />
    <Typography variant="subtitle1" color="text.primary">{title}</Typography>
    {description && <Typography variant="body2" sx={{ maxWidth: 480 }}>{description}</Typography>}
    {action}
  </Stack>
);

export const ErrorState = ({ message, onRetry }) => (
  <Alert severity="error" action={onRetry && <Button color="inherit" size="small" onClick={onRetry}>Retry</Button>}>
    {message}
  </Alert>
);

export const LoadingState = ({ label = 'Loading…' }) => (
  <Stack alignItems="center" gap={2} sx={{ py: 5 }}>
    <CircularProgress size={28} />
    <Typography variant="body2" color="text.secondary">{label}</Typography>
  </Stack>
);

export const STATUS_LABELS = {
  PRESENT: 'Present',
  LATE: 'Late',
  ABSENT: 'Absent',
  EXCUSED: 'Excused',
  UNVERIFIED: 'Unverified',
  NOT_ARRIVED: 'Not arrived',
  NOT_STARTED: 'Not started',
  HOLIDAY: 'Holiday'
};

export const AttendanceStatusChip = ({ status, overridden }) => (
  <Chip
    size="small"
    label={`${STATUS_LABELS[status] || status}${overridden ? ' (override)' : ''}`}
    sx={{ bgcolor: `${statusColors[status] || '#667085'}1a`, color: statusColors[status] || '#667085' }}
  />
);

export const ConfirmDialog = ({ open, title, children, confirmLabel = 'Confirm', danger, busy, error, onCancel, onConfirm }) => (
  <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth>
    <DialogTitle>{title}</DialogTitle>
    <DialogContent>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <DialogContentText component="div">{children}</DialogContentText>
    </DialogContent>
    <DialogActions>
      <Button onClick={onCancel} disabled={busy}>Cancel</Button>
      <Button variant="contained" color={danger ? 'error' : 'primary'} onClick={onConfirm} disabled={busy}>
        {busy ? 'Working…' : confirmLabel}
      </Button>
    </DialogActions>
  </Dialog>
);

export const formatDateTime = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
export const formatTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—');
export const formatLocalTime = (localIso) => (localIso ? localIso.slice(11, 16) : '—');
export const formatMinutes = (minutes) => {
  if (minutes === null || minutes === undefined) return '—';
  const m = Math.round(minutes);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
};
export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
