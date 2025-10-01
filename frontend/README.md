# FreightWatch Frontend

React + TypeScript + Vite frontend for FreightWatch MVP v3.

## Tech Stack

- **React 18** with TypeScript
- **Vite** for fast development and building
- **React Router** for client-side routing
- **Tailwind CSS** for styling
- **Axios** for API communication

## Development

### Setup

```bash
# Install dependencies
npm install

# Start dev server
npm run dev
```

The frontend runs on `http://localhost:5173` by default.

### Backend Connection

Configure backend URL in `.env`:

```
VITE_API_URL=http://localhost:3000
```

## Pages

### 1. Projects Overview (`/projects`)
- Lists all projects with name, customer, phase, status
- Link to create new project

### 2. Upload Review (`/uploads/:uploadId/review`)
- Review LLM analysis of uploaded file
- View suggested column mappings
- Accept or reject parsing strategy

### 3. Report Viewer (`/projects/:projectId/reports`)
- Cost analysis report with statistics
- Carrier-level breakdown
- Top overpayment opportunities

## Build

```bash
npm run build
npm run preview
```

## Phase 7 Implementation

✅ 7.1: Project Overview
✅ 7.2: Upload Review UI
✅ 7.3: Report Viewer
