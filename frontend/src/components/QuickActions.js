import React from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { Box, ButtonBase, Grid, Typography } from '@mui/material';
import VideocamOutlined from '@mui/icons-material/VideocamOutlined';
import FactCheckOutlined from '@mui/icons-material/FactCheckOutlined';
import PersonAddAlt1Outlined from '@mui/icons-material/PersonAddAlt1Outlined';
import AssessmentOutlined from '@mui/icons-material/AssessmentOutlined';
import ManageAccountsOutlined from '@mui/icons-material/ManageAccountsOutlined';
import EventAvailableOutlined from '@mui/icons-material/EventAvailableOutlined';
import { usePermission } from '../context/AuthContext';
import { tokens } from '../theme';

// One-click shortcuts for the everyday jobs, shown only to roles that can do them
// (the server checks every action again)
export const quickActionsFor = ({ operator, admin }) => [
  { to: '/live-monitoring', label: operator ? 'Start cameras' : 'Watch cameras', hint: 'Live view and camera controls', icon: <VideocamOutlined /> },
  { to: '/attendance', label: "Today's attendance", hint: 'Who came in and when', icon: <FactCheckOutlined /> },
  operator && { to: '/students?new=1', label: 'Add a student', hint: 'Then add their face photo', icon: <PersonAddAlt1Outlined /> },
  { to: '/reports', label: 'Reports', hint: 'Daily and monthly, CSV or PDF', icon: <AssessmentOutlined /> },
  admin && { to: '/users?new=1', label: 'Create account', hint: 'Staff, professor or student', icon: <ManageAccountsOutlined /> },
  admin && { to: '/staff/timetable', label: 'Class registers', hint: "Professors' saved attendance", icon: <EventAvailableOutlined /> }
].filter(Boolean);

const QuickActions = () => {
  const operator = usePermission('operator');
  const admin = usePermission('admin');
  const actions = quickActionsFor({ operator, admin });
  return (
    <Box component="nav" aria-label="Quick actions" sx={{ mb: 3 }}>
      <Grid container spacing={1.5}>
        {actions.map((action) => (
          <Grid item xs={6} sm={4} lg={{ 3: 4, 4: 3 }[actions.length] || 2} key={action.to}>
            <ButtonBase component={RouterLink} to={action.to} focusRipple
              sx={{
                width: '100%', height: '100%', p: 1.75, borderRadius: `${tokens.radius.md}px`, textAlign: 'left',
                justifyContent: 'flex-start', alignItems: 'flex-start', gap: 1.25, bgcolor: 'background.paper',
                border: `1px solid ${tokens.color.border}`, boxShadow: tokens.shadow.card,
                '&:hover': { borderColor: tokens.color.brand, bgcolor: tokens.color.sidebarActive }
              }}>
              <Box sx={{ color: tokens.color.brand, display: 'flex', pt: 0.25 }}>{action.icon}</Box>
              <Box>
                <Typography sx={{ fontWeight: 650, fontSize: 15, lineHeight: 1.3 }}>{action.label}</Typography>
                <Typography variant="caption" color="text.secondary">{action.hint}</Typography>
              </Box>
            </ButtonBase>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
};

export default QuickActions;
