import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import QuickActions, { quickActionsFor } from './QuickActions';
import StudentsPage from '../pages/StudentsPage';
import UsersPage from '../pages/UsersPage';
import { AuthContext } from '../context/AuthContext';
import { installMockApi } from '../testUtils/mockApi';

let mock;
afterEach(() => mock?.restore());

const LocationDisplay = () => {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
};

const asRole = (role, ui, path = '/') => render(
  <AuthContext.Provider value={{ user: { id: 1, username: role, role } }}>
    <MemoryRouter initialEntries={[path]}>{ui}<LocationDisplay /></MemoryRouter>
  </AuthContext.Provider>
);

test('each role gets only the shortcuts it can use', () => {
  const labels = (flags) => quickActionsFor(flags).map((a) => a.label);
  expect(labels({ operator: false, admin: false })).toEqual(['Watch cameras', "Today's attendance", 'Reports']);
  expect(labels({ operator: true, admin: false })).toEqual(['Start cameras', "Today's attendance", 'Add a student', 'Reports']);
  expect(labels({ operator: true, admin: true })).toEqual(
    ['Start cameras', "Today's attendance", 'Add a student', 'Reports', 'Create account', 'Class registers']);
});

test('shortcuts are links to the right places', () => {
  asRole('admin', <QuickActions />);
  const nav = screen.getByRole('navigation', { name: 'Quick actions' });
  expect(within(nav).getByRole('link', { name: /Add a student/ })).toHaveAttribute('href', '/students?new=1');
  expect(within(nav).getByRole('link', { name: /Create account/ })).toHaveAttribute('href', '/users?new=1');
  expect(within(nav).getByRole('link', { name: /Start cameras/ })).toHaveAttribute('href', '/live-monitoring');
});

test('"Add a student" opens the form straight away and clears the shortcut from the address', async () => {
  mock = installMockApi({ 'GET /students/': { data: [] } });
  asRole('operator', <Routes><Route path="/students" element={<StudentsPage />} /></Routes>, '/students?new=1');
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/students$/));
});

test('a viewer following the shortcut gets no form', async () => {
  mock = installMockApi({ 'GET /students/': { data: [
    { id: 1, student_id: 'RA001', name: 'Asha', class_name: 'CSE-A', is_active: true, enrolled_faces: 1 }] } });
  asRole('viewer', <Routes><Route path="/students" element={<StudentsPage />} /></Routes>, '/students?new=1');
  await screen.findByRole('table', { name: 'Students' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('search text from the top bar pre-fills the Students filter', async () => {
  mock = installMockApi({ 'GET /students/': { data: [
    { id: 1, student_id: 'RA001', name: 'Asha', class_name: 'CSE-A', is_active: true, enrolled_faces: 1 },
    { id: 2, student_id: 'RA002', name: 'Ravi', class_name: 'CSE-A', is_active: true, enrolled_faces: 1 }
  ] } });
  asRole('operator', <Routes><Route path="/students" element={<StudentsPage />} /></Routes>, '/students?q=Ravi');
  const table = await screen.findByRole('table', { name: 'Students' });
  expect(await within(table).findByText('Ravi')).toBeInTheDocument();
  expect(within(table).queryByText('Asha')).not.toBeInTheDocument();
});

test('"Create account" opens the account form straight away for an admin', async () => {
  mock = installMockApi({ 'GET /users/': { data: [] }, 'GET /users/audit-log': { data: [] }, 'GET /students/': { data: [] } });
  asRole('admin', <Routes><Route path="/users" element={<UsersPage />} /></Routes>, '/users?new=1');
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('button', { name: 'Create account' })).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Username')).toHaveValue('');
});
