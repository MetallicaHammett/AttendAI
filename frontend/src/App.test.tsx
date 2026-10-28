import React from 'react';
import { render, screen } from '@testing-library/react';
import App from './App';

test('renders without crashing', () => {
  render(<App />);
});

test('sign-in page shows the institution and the application name', () => {
  render(<App />);
  expect(screen.getByRole('img', { name: 'SRM Institute of Science and Technology logo' })).toBeInTheDocument();
  expect(screen.getByText('SRM INSTITUTE OF SCIENCE AND TECHNOLOGY')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: 'AttendAI' })).toBeInTheDocument();
  expect(screen.getByText('Intelligent Attendance System')).toBeInTheDocument();
});
