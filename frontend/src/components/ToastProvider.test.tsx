import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { ToastProvider, useToast } from './ToastProvider';

const TestComponent = () => {
  const { toast } = useToast();
  return (
    <button onClick={() => { toast({ type: 'info', title: 'Test message' }); }}>
      Show Toast
    </button>
  );
};

describe('ToastProvider', () => {
  it('renders children', () => {
    render(
      <ToastProvider>
        <div>Child Content</div>
      </ToastProvider>
    );
    expect(screen.getByText('Child Content')).toBeInTheDocument();
  });

  it('shows and dismisses toast automatically', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>
    );

    // Initial state
    expect(screen.queryByText('Test message')).not.toBeInTheDocument();

    // Click button
    act(() => {
      screen.getByText('Show Toast').click();
    });
    expect(screen.getByText('Test message')).toBeInTheDocument();

    // Fast forward past duration
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.queryByText('Test message')).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
