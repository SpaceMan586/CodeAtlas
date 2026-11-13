import { Component, type ReactNode } from "react";

interface BoundaryProps {
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
}

export class GraphVisualizationBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-rose-400/20 bg-atlas-bg/70 p-6 text-center" role="alert">
          <p className="font-medium text-rose-300">Graph visualization could not be loaded.</p>
          <p className="mt-2 text-sm text-atlas-muted">Your analysis and graph data are still available.</p>
        </div>
      );
    }
    return this.props.children;
  }
}
