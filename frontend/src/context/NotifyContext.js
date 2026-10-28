import React, { createContext, useCallback, useContext, useState } from 'react';
import { Alert, Snackbar } from '@mui/material';

const NotifyContext = createContext(null);

// App-wide toast for the outcome of an action ("Saved", "Export ready", errors)
export const NotifyProvider = ({ children }) => {
  const [toast, setToast] = useState(null);
  const notify = useCallback((text, severity = 'success') => setToast({ text, severity, key: Date.now() }), []);
  return (
    <NotifyContext.Provider value={notify}>
      {children}
      <Snackbar
        key={toast?.key}
        open={Boolean(toast)}
        autoHideDuration={toast?.severity === 'error' ? 8000 : 4000}
        onClose={(_, reason) => { if (reason !== 'clickaway') setToast(null); }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        {toast ? <Alert severity={toast.severity} variant="filled" onClose={() => setToast(null)}>{toast.text}</Alert> : undefined}
      </Snackbar>
    </NotifyContext.Provider>
  );
};

// Falls back to a no-op so components also work outside the provider (unit tests)
export const useNotify = () => useContext(NotifyContext) || (() => {});
