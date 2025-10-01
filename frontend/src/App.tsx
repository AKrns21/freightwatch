import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ProjectsPage } from './pages/Projects';
import { UploadReviewPage } from './pages/UploadReview';
import { ReportViewerPage } from './pages/ReportViewer';

/**
 * Main App Component with React Router
 *
 * Routes:
 * - / → Projects list
 * - /projects → Projects list
 * - /projects/:projectId → Project details (TODO)
 * - /projects/:projectId/reports → Latest report
 * - /projects/:projectId/reports/:reportId → Specific report version
 * - /uploads/:uploadId/review → Upload review page
 */
function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-100">
        <Routes>
          {/* Default route redirects to projects */}
          <Route path="/" element={<Navigate to="/projects" replace />} />

          {/* Projects */}
          <Route path="/projects" element={<ProjectsPage />} />

          {/* Upload Review */}
          <Route path="/uploads/:uploadId/review" element={<UploadReviewPage />} />

          {/* Reports */}
          <Route
            path="/projects/:projectId/reports"
            element={<ReportViewerPage />}
          />
          <Route
            path="/projects/:projectId/reports/:reportId"
            element={<ReportViewerPage />}
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
