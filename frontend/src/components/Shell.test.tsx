import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Shell } from './Shell';
import { Router } from 'wouter';

describe('Shell', () => {
  it('renders navigation links and children', () => {
    render(
      <Router>
        <Shell>
          <div data-testid="child-content">Child Content</div>
        </Shell>
      </Router>
    );

    // Sidebar link (Overview is one of the routes)
    expect(screen.getByLabelText('Return Platform overview')).toBeInTheDocument();

    // Child content
    expect(screen.getByTestId('child-content')).toBeInTheDocument();
  });

  it('toggles mobile menu', () => {
    render(
      <Router>
        <Shell>
          <div>Content</div>
        </Shell>
      </Router>
    );

    const button = screen.getByRole('button', { name: /open menu/i });
    expect(button).toBeInTheDocument();

    // Initially not expanded
    expect(button).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(button);

    // Should toggle expanded state
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: /close menu/i })).toBeInTheDocument();
  });
});
