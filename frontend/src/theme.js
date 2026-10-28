import { alpha, createTheme } from '@mui/material/styles';

// Design tokens: a campus-portal look - white cards with solid blue title bars, a light
// grey sidebar and a white top bar. Status colours still carry meaning, not decoration.
export const tokens = {
  color: {
    ink: '#1d2939',
    inkMuted: '#475467',
    inkSubtle: '#667085',
    border: '#e4e7ec',
    borderStrong: '#d0d5dd',
    canvas: '#f3f4f6',
    surface: '#ffffff',
    surfaceMuted: '#f9fafb',
    brand: '#337ab7', // white text on it meets WCAG AA (4.5:1)
    brandDark: '#286090',
    accent: '#0f766e',
    sidebar: '#f2f3f5',
    sidebarText: '#2f3b4c',
    sidebarActive: '#e3ecf6',
    cardHeader: '#337ab7',
    chartBar: '#4f72dc',
    present: '#00b386',
    absent: '#f0a30a'
  },
  radius: { sm: 6, md: 8, lg: 12 },
  shadow: {
    card: '0 1px 2px rgba(16, 24, 40, 0.05)',
    raised: '0 4px 12px -2px rgba(16, 24, 40, 0.10), 0 2px 4px -2px rgba(16, 24, 40, 0.06)'
  },
  // System fonts only: the app must work without internet access (no web fonts)
  fontFamily: '"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif',
  sidebarWidth: 248
};

// Status colours used by chips and charts across the app
export const statusColors = {
  PRESENT: '#12805c',
  LATE: '#b54708',
  ABSENT: '#c4320a',
  EXCUSED: '#5925dc',
  UNVERIFIED: '#667085',
  NOT_ARRIVED: '#98a2b3',
  NOT_STARTED: '#98a2b3',
  HOLIDAY: '#175cd3'
};

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: tokens.color.brand, dark: tokens.color.brandDark, contrastText: '#ffffff' },
    secondary: { main: tokens.color.accent },
    success: { main: '#12805c' },
    warning: { main: '#b54708' },
    error: { main: '#c4320a' },
    info: { main: '#175cd3' },
    background: { default: tokens.color.canvas, paper: tokens.color.surface },
    text: { primary: tokens.color.ink, secondary: tokens.color.inkMuted },
    divider: tokens.color.border
  },
  shape: { borderRadius: tokens.radius.md },
  typography: {
    fontFamily: tokens.fontFamily,
    h4: { fontSize: '1.625rem', fontWeight: 650, letterSpacing: '-0.01em' },
    h5: { fontSize: '1.25rem', fontWeight: 650 },
    h6: { fontSize: '1rem', fontWeight: 650 },
    subtitle1: { fontWeight: 600 },
    subtitle2: { fontWeight: 600, color: tokens.color.inkMuted },
    overline: { fontWeight: 700, letterSpacing: '0.08em' },
    button: { textTransform: 'none', fontWeight: 600 }
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: tokens.color.canvas },
        '*:focus-visible': { outline: `2px solid ${tokens.color.brand}`, outlineOffset: 2 }
      }
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { borderRadius: tokens.radius.sm } }
    },
    MuiPaper: {
      styleOverrides: {
        outlined: { borderColor: tokens.color.border, boxShadow: tokens.shadow.card },
        rounded: { borderRadius: tokens.radius.md }
      }
    },
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: { root: { borderColor: tokens.color.border, boxShadow: tokens.shadow.card } }
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: 600,
          color: tokens.color.inkMuted,
          backgroundColor: tokens.color.surfaceMuted,
          fontSize: '0.8125rem'
        },
        root: { borderColor: tokens.color.border }
      }
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600, borderRadius: tokens.radius.sm } } },
    MuiTooltip: { defaultProps: { arrow: true } },
    MuiDialogTitle: { styleOverrides: { root: { fontWeight: 650 } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: tokens.radius.sm } } },
    MuiTab: { styleOverrides: { root: { textTransform: 'none', fontWeight: 600, minHeight: 44 } } },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: tokens.radius.sm,
          '&.Mui-selected': { backgroundColor: alpha(tokens.color.brand, 0.08) }
        }
      }
    }
  }
});

export default theme;
