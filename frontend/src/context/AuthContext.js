import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { jwtDecode } from 'jwt-decode';
import { api, clearToken, getErrorMessage, getToken, setToken as storeToken, setUnauthorizedHandler } from '../data';

export const AuthContext = createContext();

// Mirrors backend app/permissions.py: viewer < operator < admin. "student" and "professor"
// are deliberately not ranked, so roleAtLeast() is false for them at every level.
export const ROLE_RANK = { viewer: 0, operator: 1, admin: 2 };
// Staff roles an admin can assign (the Users page role menu)
export const ROLE_LABELS = { viewer: 'Viewer', operator: 'Operator', admin: 'Administrator', professor: 'Professor' };
export const ACCOUNT_LABELS = { ...ROLE_LABELS, student: 'Student' };

export const roleAtLeast = (role, minimum) =>
  (ROLE_RANK[role] ?? -1) >= (ROLE_RANK[minimum] ?? Infinity);

const SESSION_EXPIRED = 'Your session has expired. Please sign in again.';

// True if the token decodes and its exp (seconds) is in the future
const isTokenUsable = (token) => {
  try {
    const { exp } = jwtDecode(token);
    return typeof exp === 'number' && exp * 1000 > Date.now();
  } catch (error) {
    return false;
  }
};

export const AuthContextProvider = ({ children }) => {
  const [token, setToken] = useState(getToken() || '');
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  // Shown on the login page: why the user was signed out, or why the session check failed
  const [authMessage, setAuthMessage] = useState('');

  const clearSession = useCallback((message = '') => {
    clearToken();
    setToken('');
    setUser(null);
    setIsAuthenticated(false);
    setAuthMessage(message);
  }, []);

  // Restore the session once on startup
  useEffect(() => {
    const initializeAuth = async () => {
      const stored = getToken();
      if (!stored) {
        setLoading(false);
        return;
      }
      if (!isTokenUsable(stored)) {
        clearSession(SESSION_EXPIRED);
        setLoading(false);
        return;
      }
      try {
        const response = await api.get('/auth/me', { skipAuthRedirect: true });
        setUser(response.data);
        setIsAuthenticated(true);
      } catch (error) {
        if (error?.response?.status === 401 || error?.response?.status === 403) {
          clearSession(SESSION_EXPIRED);
        } else {
          // Backend unreachable or failing: keep the token, but don't pretend to be signed in
          setAuthMessage(getErrorMessage(error, 'Could not verify your session'));
        }
      } finally {
        setLoading(false);
      }
    };

    initializeAuth();
  }, [clearSession]);

  // Any 401 from the shared API client ends the session; RequireAuth then shows /login
  useEffect(() => setUnauthorizedHandler(() => clearSession(SESSION_EXPIRED)), [clearSession]);

  const login = async (username, password) => {
    // OAuth2 password form: application/x-www-form-urlencoded
    const form = new URLSearchParams();
    form.append('username', username);
    form.append('password', password);
    const response = await api.post('/auth/login', form, { skipAuthRedirect: true });
    const { access_token } = response.data;

    storeToken(access_token);
    setToken(access_token);
    try {
      const userResponse = await api.get('/auth/me', { skipAuthRedirect: true });
      setUser(userResponse.data);
      setIsAuthenticated(true);
      setAuthMessage('');
    } catch (error) {
      clearSession();
      throw error;
    }
    return true;
  };

  const register = async (userData) => {
    const response = await api.post('/auth/register', userData, { skipAuthRedirect: true });
    return response.data;
  };

  const logout = () => {
    clearSession();
  };

  const value = {
    token,
    user,
    isAuthenticated,
    loading,
    authMessage,
    login,
    register,
    logout
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

// Whether the signed-in user may use controls that need `minimum`. This only hides
// controls; the server enforces every permission itself. Pages rendered without an
// AuthContextProvider (isolated component tests) show all controls.
export const usePermission = (minimum) => {
  const context = useContext(AuthContext);
  if (!context) return true;
  // Older backends do not send a role; treat the user as an operator-level account,
  // which matches what they could do before roles existed (the server still decides)
  return roleAtLeast(context.user?.role || 'operator', minimum);
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthContextProvider');
  }
  return context;
};
