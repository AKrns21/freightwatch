import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ProjectsPage } from './pages/Projects';
import { NewProjectPage } from './pages/NewProject';
import { UploadReviewPage } from './pages/UploadReview';
import { ReportViewerPage } from './pages/ReportViewer';
import { ProjectDetailPage } from './pages/ProjectDetail';
import { LoginPage } from './pages/Login';

/**
 * Simple auth guard — redirects to /login when no token is stored.
 */
const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const token = localStorage.getItem('auth_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

/**
 * Main App Component with React Router
 */
function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-100">
        <Routes>
          {/* Public */}
          <Route path="/login" element={<LoginPage />} />

          {/* Default route redirects to projects */}
          <Route path="/" element={<Navigate to="/projects" replace />} />

          {/* Protected routes */}
          <Route path="/projects" element={<RequireAuth><ProjectsPage /></RequireAuth>} />
          <Route path="/projects/new" element={<RequireAuth><NewProjectPage /></RequireAuth>} />
          <Route path="/projects/:projectId" element={<RequireAuth><ProjectDetailPage /></RequireAuth>} />

          {/* Upload Review */}
          <Route path="/uploads/:uploadId/review" element={<RequireAuth><UploadReviewPage /></RequireAuth>} />

          {/* Reports */}
          <Route
            path="/projects/:projectId/reports"
            element={<RequireAuth><ReportViewerPage /></RequireAuth>}
          />
          <Route
            path="/projects/:projectId/reports/:reportId"
            element={<RequireAuth><ReportViewerPage /></RequireAuth>}
          />

          {/* 404 Fallback */}
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

/**
 * Simple 404 Not Found page
 */
const NotFoundPage: React.FC = () => {
  return (
    <div className="container mx-auto p-6">
      <div className="bg-white rounded-lg shadow p-12 text-center">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">404</h1>
        <p className="text-xl text-gray-600 mb-6">Page not found</p>
        <a
          href="/projects"
          className="inline-block bg-blue-600 text-white px-6 py-3 rounded hover:bg-blue-700 transition"
        >
          Go to Projects
        </a>
      </div>
    </div>
  );
};

export default App;
