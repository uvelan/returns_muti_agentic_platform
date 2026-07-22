import {
  Component,
  type ErrorInfo,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  RotateCcw,
} from "lucide-react";


type ErrorBoundaryProps = {
  readonly children: ReactNode;
};


type ErrorBoundaryState = {
  readonly hasError: boolean;
};


const initialState: ErrorBoundaryState = {
  hasError: false,
};


export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  public state: ErrorBoundaryState = initialState;

  public static getDerivedStateFromError(): ErrorBoundaryState {
    return {
      hasError: true,
    };
  }

  public componentDidCatch(
    error: Error,
    errorInfo: ErrorInfo,
  ): void {
    console.error(
      "Unhandled frontend rendering error.",
      {
        errorName: error.name,
        errorMessage: error.message,
        componentStack: errorInfo.componentStack,
      },
    );
  }

  private readonly reloadApplication = (): void => {
    window.location.reload();
  };

  public render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <main
        className="
          flex min-h-screen items-center justify-center
          bg-slate-50 p-6
        "
      >
        <section
          className="
            w-full max-w-lg rounded-xl border border-slate-200
            bg-white p-8 text-center shadow-sm
          "
          aria-labelledby="application-error-heading"
          role="alert"
        >
          <div
            className="
              mx-auto flex size-12 items-center justify-center
              rounded-full bg-red-50 text-red-700
            "
            aria-hidden="true"
          >
            <AlertTriangle size={24} />
          </div>

          <h1
            id="application-error-heading"
            className="mt-5 text-2xl font-semibold text-slate-900"
          >
            Console unavailable
          </h1>

          <p className="mt-3 text-sm leading-6 text-slate-600">
            The Data Console encountered an unexpected rendering
            error. Reload the application to try again. Contact
            platform engineering if the problem continues.
          </p>

          <button
            type="button"
            onClick={this.reloadApplication}
            className="
              mt-6 inline-flex items-center justify-center gap-2
              rounded-md bg-slate-900 px-4 py-2
              text-sm font-medium text-white
              transition hover:bg-slate-700
              focus-visible:outline-none focus-visible:ring-2
              focus-visible:ring-slate-500 focus-visible:ring-offset-2
            "
          >
            <RotateCcw
              size={16}
              aria-hidden="true"
            />
            Reload console
          </button>
        </section>
      </main>
    );
  }
}