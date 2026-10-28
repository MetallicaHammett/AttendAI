import React, { useEffect, useState } from 'react';
import { Link as RouterLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar,
  Avatar,
  Box,
  Button,
  Chip,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import MenuIcon from '@mui/icons-material/Menu';
import SpaceDashboardOutlined from '@mui/icons-material/SpaceDashboardOutlined';
import VideocamOutlined from '@mui/icons-material/VideocamOutlined';
import PeopleAltOutlined from '@mui/icons-material/PeopleAltOutlined';
import FactCheckOutlined from '@mui/icons-material/FactCheckOutlined';
import AssessmentOutlined from '@mui/icons-material/AssessmentOutlined';
import SettingsInputAntennaOutlined from '@mui/icons-material/SettingsInputAntennaOutlined';
import EventNoteOutlined from '@mui/icons-material/EventNoteOutlined';
import AdminPanelSettingsOutlined from '@mui/icons-material/AdminPanelSettingsOutlined';
import EventAvailableOutlined from '@mui/icons-material/EventAvailableOutlined';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import GlobalSearch from './GlobalSearch';
import HistoryOutlined from '@mui/icons-material/HistoryOutlined';
import { ACCOUNT_LABELS, roleAtLeast, useAuth } from '../context/AuthContext';
import { api } from '../data';
import { tokens } from '../theme';
import { DemoBanner } from './ui';

// Four everyday pages always visible; everything set up once in a while sits under
// "Setup", folded away until opened (or while one of its pages is open). Each role sees
// only the pages it can use; the server still checks every request.
export const NAV_SECTIONS = [
  {
    label: 'Everyday',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: <SpaceDashboardOutlined fontSize="small" /> },
      { to: '/live-monitoring', label: 'Live Monitoring', icon: <VideocamOutlined fontSize="small" /> },
      { to: '/attendance', label: 'Attendance', icon: <FactCheckOutlined fontSize="small" /> },
      { to: '/reports', label: 'Reports', icon: <AssessmentOutlined fontSize="small" /> }
    ]
  },
  {
    label: 'Setup',
    collapsible: true,
    items: [
      { to: '/students', label: 'Students', icon: <PeopleAltOutlined fontSize="small" /> },
      { to: '/cameras', label: 'Cameras', icon: <SettingsInputAntennaOutlined fontSize="small" />, minRole: 'operator' },
      { to: '/timetable', label: 'Timetable', icon: <EventNoteOutlined fontSize="small" /> },
      // Administrators oversee every professor's registers
      { to: '/staff/timetable', label: 'Class Registers', icon: <EventAvailableOutlined fontSize="small" />, minRole: 'admin' },
      { to: '/users', label: 'Users', icon: <AdminPanelSettingsOutlined fontSize="small" />, minRole: 'admin' }
    ]
  }
];

const SETUP_OPEN_KEY = 'nav.setupOpen';
const readSetupOpen = () => {
  try { return localStorage.getItem(SETUP_OPEN_KEY) === '1'; } catch (error) { return false; }
};

// A student account sees only its own attendance
export const STUDENT_NAV_SECTIONS = [
  { label: 'My records', items: [{ to: '/my-attendance', label: 'Attendance Details', icon: <FactCheckOutlined fontSize="small" /> }] }
];

// A professor sees only the staff portal: their classes and registers
export const PROFESSOR_NAV_SECTIONS = [
  {
    label: 'Staff portal',
    items: [
      { to: '/staff/timetable', label: 'Weekly Timetable', icon: <EventNoteOutlined fontSize="small" /> },
      { to: '/staff/history', label: 'Attendance History', icon: <HistoryOutlined fontSize="small" /> }
    ]
  }
];

export const NAV_ITEMS = NAV_SECTIONS.flatMap((section) => section.items);
const ALL_NAV_ITEMS = [...NAV_ITEMS, ...STUDENT_NAV_SECTIONS.flatMap((section) => section.items),
  ...PROFESSOR_NAV_SECTIONS.flatMap((section) => section.items)];

const navSectionsFor = (role) => {
  if (role === 'student') return STUDENT_NAV_SECTIONS;
  if (role === 'professor') return PROFESSOR_NAV_SECTIONS;
  return NAV_SECTIONS;
};

const HEALTH_POLL_MS = 30000;

// Backend status from GET /health: 200 = healthy, 503 = database problem, no response = offline
const useBackendHealth = () => {
  const [health, setHealth] = useState({ state: 'checking', label: 'Checking API…' });

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const response = await api.get('/health', { skipAuthRedirect: true });
        const model = response.data?.checks?.person_detection_model;
        if (!cancelled) {
          setHealth(model === 'missing'
            ? { state: 'warn', label: 'API online · model missing', hint: 'The person-detection model file is missing: cameras cannot start. See README, "Model files".' }
            : { state: 'ok', label: 'API online' });
        }
      } catch (error) {
        if (cancelled) return;
        if (error?.response?.status === 503) {
          const schema = error.response.data?.checks?.schema;
          setHealth(schema === 'outdated'
            ? { state: 'error', label: 'Database needs upgrade', hint: 'Run the database migration (README, "Upgrading the database").' }
            : { state: 'error', label: 'Database unavailable' });
        } else if (error?.response) {
          setHealth({ state: 'error', label: `API error (HTTP ${error.response.status})` });
        } else {
          setHealth({ state: 'error', label: 'API offline' });
        }
      }
    };
    check();
    const interval = setInterval(check, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return health;
};

// Status chip text on its white background (readable on the blue bar)
const HEALTH_TEXT = { ok: '#12805c', warn: '#b54708', error: '#c4320a' };

const isActive = (pathname, item) => pathname === item.to || pathname.startsWith(`${item.to}/`);

export const visibleNavItems = (role) => navSectionsFor(role)
  .flatMap((section) => section.items)
  .filter((item) => !item.minRole || roleAtLeast(role, item.minRole));

const NavItem = ({ item, pathname, onNavigate }) => {
  const active = isActive(pathname, item);
  return (
    <ListItemButton
      component={RouterLink}
      to={item.to}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      sx={{
        mb: 0.25, color: active ? tokens.color.brand : tokens.color.sidebarText,
        bgcolor: active ? tokens.color.sidebarActive : 'transparent',
        '&:hover': { bgcolor: active ? tokens.color.sidebarActive : 'rgba(16,24,40,0.05)' }
      }}
    >
      <ListItemIcon sx={{ minWidth: 34, color: 'inherit' }}>{item.icon}</ListItemIcon>
      <ListItemText primary={item.label} primaryTypographyProps={{ fontWeight: active ? 650 : 500, fontSize: 14 }} />
    </ListItemButton>
  );
};

const SUBHEADER_SX = { bgcolor: 'transparent', color: tokens.color.sidebarText, opacity: 0.6, fontSize: 11, fontWeight: 700,
  letterSpacing: '0.08em', textTransform: 'uppercase', lineHeight: '32px', mt: 1 };

const SidebarContent = ({ role, pathname, onNavigate }) => {
  const [setupOpen, setSetupOpen] = useState(readSetupOpen);
  const toggleSetup = () => setSetupOpen((open) => {
    try { localStorage.setItem(SETUP_OPEN_KEY, open ? '0' : '1'); } catch (error) { /* per-browser preference only */ }
    return !open;
  });
  return (
  <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: tokens.color.sidebar, color: tokens.color.sidebarText }}>
    <Stack alignItems="center" gap={0.75} sx={{ px: 2, pt: 2.5, pb: 2, textAlign: 'center' }}>
      <Box component="img" src={`${process.env.PUBLIC_URL}/srm-seal.png`} alt="SRM Institute of Science and Technology logo"
        sx={{ width: 84, height: 84 }} />
      <Typography sx={{ color: tokens.color.ink, fontWeight: 800, fontSize: 12.5, lineHeight: 1.25, letterSpacing: '0.04em' }}>
        SRM INSTITUTE OF SCIENCE AND TECHNOLOGY
      </Typography>
      <Typography sx={{ color: tokens.color.brand, fontWeight: 900, fontSize: 28, lineHeight: 1.1 }}>AttendAI</Typography>
      <Typography variant="caption" sx={{ color: tokens.color.sidebarText, opacity: 0.8 }}>Classroom attendance</Typography>
    </Stack>
    <Divider />
    <Box component="nav" aria-label="Main navigation" sx={{ flexGrow: 1, overflowY: 'auto', px: 1.5 }}>
      {navSectionsFor(role).map((section) => {
        const items = section.items.filter((item) => !item.minRole || roleAtLeast(role, item.minRole));
        if (items.length === 0) return null;
        if (section.collapsible) {
          // Always open while one of its pages is showing, so the current page stays visible
          const open = setupOpen || items.some((item) => isActive(pathname, item));
          return (
            <List key={section.label} dense disablePadding>
              <ListItemButton onClick={toggleSetup} aria-expanded={open} sx={{ mt: 1, mb: 0.25, color: tokens.color.sidebarText }}>
                <ListItemIcon sx={{ minWidth: 34, color: 'inherit' }}><SettingsOutlined fontSize="small" /></ListItemIcon>
                <ListItemText primary={section.label} secondary={open ? null : items.map((item) => item.label).join(', ')}
                  primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }}
                  secondaryTypographyProps={{ noWrap: true, fontSize: 11.5 }} />
                {open ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
              </ListItemButton>
              <Collapse in={open} timeout="auto">
                <Box sx={{ pl: 1 }}>
                  {items.map((item) => <NavItem key={item.to} item={item} pathname={pathname} onNavigate={onNavigate} />)}
                </Box>
              </Collapse>
            </List>
          );
        }
        return (
          <List key={section.label} dense disablePadding
            subheader={<ListSubheader disableSticky sx={SUBHEADER_SX}>{section.label}</ListSubheader>}>
            {items.map((item) => <NavItem key={item.to} item={item} pathname={pathname} onNavigate={onNavigate} />)}
          </List>
        );
      })}
    </Box>
    <Divider />
    <Typography variant="caption" sx={{ px: 2.5, py: 1.5, opacity: 0.7 }}>
      On-premise · no cloud services
    </Typography>
  </Box>
  );
};

const Layout = () => {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const health = useBackendHealth();
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const [drawerOpen, setDrawerOpen] = useState(false);
  const role = user?.role || 'operator';
  const current = /^\/students\/\d+\/attendance/.test(location.pathname)
    ? { label: 'Student attendance' }
    : /^\/staff\/register\//.test(location.pathname)
      ? { label: 'Class register' }
      : [...navSectionsFor(role).flatMap((section) => section.items), ...ALL_NAV_ITEMS]
        .find((item) => location.pathname.startsWith(item.to));

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const sidebar = <SidebarContent role={role} pathname={location.pathname} onNavigate={() => setDrawerOpen(false)} />;

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default' }}>
      {compact ? (
        <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)}
          PaperProps={{ sx: { width: tokens.sidebarWidth, border: 0 } }}>{sidebar}</Drawer>
      ) : (
        <Box component="aside" sx={{ width: tokens.sidebarWidth, flexShrink: 0, position: 'sticky', top: 0, height: '100vh',
          borderRight: `1px solid ${tokens.color.border}` }}>
          {sidebar}
        </Box>
      )}

      <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {/* Brand-blue bar, like the card title bars; white text on it meets WCAG AA */}
        <AppBar position="sticky" elevation={0}
          sx={{ bgcolor: tokens.color.brand, color: '#ffffff', borderBottom: `1px solid ${tokens.color.brandDark}` }}>
          <Toolbar sx={{ gap: 1.5, minHeight: { xs: 56, sm: 60 } }}>
            {compact && (
              <IconButton edge="start" color="inherit" onClick={() => setDrawerOpen(true)} aria-label="Open navigation">
                <MenuIcon />
              </IconButton>
            )}
            <Typography variant="subtitle1" component="div" sx={{ flexGrow: 1, color: 'inherit', fontWeight: 700 }} noWrap>
              {current ? current.label : 'AttendAI'}
            </Typography>
            {roleAtLeast(role, 'viewer') && (
              <Box sx={{ display: { xs: 'none', sm: 'block' },
                '& .MuiOutlinedInput-root': { bgcolor: '#ffffff', borderRadius: `${tokens.radius.md}px` },
                '& .MuiOutlinedInput-notchedOutline': { borderColor: 'transparent' } }}>
                <GlobalSearch pages={visibleNavItems(role)} searchStudents />
              </Box>
            )}
            <Tooltip title={health.hint || 'Backend status (GET /health)'}>
              <Chip
                size="small"
                label={health.label}
                sx={{ bgcolor: '#ffffff', fontWeight: 600,
                  color: HEALTH_TEXT[health.state] || tokens.color.inkMuted }}
              />
            </Tooltip>
            {user && (
              <Stack direction="row" alignItems="center" gap={1} sx={{ display: { xs: 'none', sm: 'flex' } }}>
                <Avatar sx={{ width: 30, height: 30, fontSize: 14, bgcolor: '#ffffff', color: tokens.color.brand, fontWeight: 700 }}
                  aria-hidden>
                  {(user.username || '?').slice(0, 1).toUpperCase()}
                </Avatar>
                <Box sx={{ lineHeight: 1.1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{user.username}</Typography>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.85)' }}>
                    {ACCOUNT_LABELS[user.role] || 'Signed in'}
                  </Typography>
                </Box>
              </Stack>
            )}
            <Button variant="outlined" size="small" onClick={handleLogout}
              sx={{ color: '#ffffff', borderColor: 'rgba(255,255,255,0.7)',
                '&:hover': { borderColor: '#ffffff', bgcolor: 'rgba(255,255,255,0.12)' } }}>
              Logout
            </Button>
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ p: { xs: 2, sm: 3 }, maxWidth: 1440, width: '100%', mx: 'auto' }}>
          <DemoBanner />
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
};

export default Layout;
