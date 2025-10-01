import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Project, ApiResponse } from '../types';

/**
 * ProjectsPage - Project Overview (Phase 7.1)
 *
 * Displays all projects for the current tenant with:
 * - Project cards showing name, customer, phase, status
 * - Link to create new project
 * - Navigation to project details
 */
export const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await api.get<ApiResponse<Project[]>>('/api/projects');
      setProjects(response.data.data);
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to load projects');
      console.error('Failed to load projects:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-xl text-gray-600">Loading projects...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h2 className="text-red-800 font-semibold mb-2">Error Loading Projects</h2>
          <p className="text-red-600">{error}</p>
          <button
            onClick={loadProjects}
            className="mt-4 bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold text-gray-900">Projects</h1>
        <Link
          to="/projects/new"
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 transition"
        >
          + New Project
        </Link>
      </div>

      {/* Empty State */}
      {projects.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <h2 className="text-xl font-semibold text-gray-700 mb-2">
            No projects yet
          </h2>
          <p className="text-gray-600 mb-6">
            Create your first project to start analyzing freight costs
          </p>
          <Link
            to="/projects/new"
            className="inline-block bg-blue-600 text-white px-6 py-3 rounded hover:bg-blue-700 transition"
          >
            Create First Project
          </Link>
        </div>
      ) : (
        /* Project Grid */
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((project) => (
            <Link
              key={project.id}
              to={`/projects/${project.id}`}
              className="border rounded-lg p-6 bg-white hover:shadow-lg transition-shadow duration-200"
            >
              <h3 className="text-xl font-semibold mb-2 text-gray-900">
                {project.name}
              </h3>
              <p className="text-gray-600 mb-4">{project.customer_name}</p>

              <div className="flex justify-between text-sm">
                <div>
                  <span className="text-gray-500">Phase:</span>{' '}
                  <span className={`font-medium ${
                    project.phase === 'quick_check' ? 'text-blue-600' :
                    project.phase === 'deep_dive' ? 'text-purple-600' :
                    'text-green-600'
                  }`}>
                    {project.phase}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Status:</span>{' '}
                  <span className={`font-medium ${
                    project.status === 'draft' ? 'text-gray-600' :
                    project.status === 'in_progress' ? 'text-yellow-600' :
                    project.status === 'review' ? 'text-orange-600' :
                    'text-green-600'
                  }`}>
                    {project.status}
                  </span>
                </div>
              </div>

              <div className="mt-4 text-xs text-gray-400">
                Created {new Date(project.created_at).toLocaleDateString()}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
};
